"""Plan a day's videos without rendering any of them: fetch the
newsletters, select stories and write the scripts (one paid writer call),
then write the plan manifest (plan_manifest.py) for review, previews and an
explicit final render later.

    python -m newsletter_video.plan_day                  # today, VIDEOS_PER_DAY videos
    python -m newsletter_video.plan_day 2026-11-07 --videos 2
    python -m newsletter_video.plan_day --test --out output/_previews/<id>   # a throwaway test plan
    python -m newsletter_video.plan_day 2026-11-07 --rewrite v02            # rewrite one video's script (paid)

Nothing here voices or renders; the writer's cost (~$0.03 per call) is the
only cost. The unattended daily job does not read plans: it keeps writing and
rendering with the defaults, exactly as before.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from . import plan_manifest as pm
from .look import LookError, applied, is_empty, look_from_env, resolve_look
from .run_pipeline import OUTPUT_DIR, PROJECT_ROOT, _covered_titles, _wait_for_network
from .script_writer import build_scripts
from .sources import get_source

log = logging.getLogger("newsletter_video")


def plan(target_date: date, out_dir: Path, n_videos: int, kind: str = "day",
         run_overrides: dict | None = None, lineup_slots: list[dict] | None = None, lineup_total: int = 0) -> dict:
    """Fetch, select, write, save. `run_overrides` (a partial look) decides
    which writer path runs (an avatar look writes cards, an explainer look
    writes scene plans) and is recorded as the default of every video."""
    from .explainer_builder import video_style

    resolved = resolve_look(run_overrides)
    with applied(None if is_empty(run_overrides) else resolved):
        log.info("Planning %d video(s) for %s in the %s style", n_videos, target_date, video_style())
        source = get_source()
        if not _wait_for_network():
            log.warning("Network still unreachable; attempting the fetch anyway")
        log.info("Fetching stories from %s for %s", source.describe(), target_date)
        editions = source.fetch(target_date)
        total = sum(len(e.stories) for e in editions)
        log.info("Fetched %d editions, %d stories total", len(editions), total)
        if not editions or total == 0:
            raise RuntimeError("No stories scraped; nothing to plan")
        covered = _covered_titles()
        from . import factcheck

        mark = factcheck.drop_mark()
        from . import must_cover

        must_cover.clear()
        log.info("Selecting top stories and building scripts")
        if lineup_slots is not None:  # the standing lineup (lineup.py): formats per slot, one writer call per format
            from .lineup import write_scripts

            scripts = write_scripts(lineup_slots, target_date, lineup_total, editions, covered, build_scripts)
        else:
            scripts = build_scripts(editions, n_videos=n_videos, exclude_titles=covered)
        if not scripts:
            raise RuntimeError("No stories selected; nothing to plan")
        for script in scripts:
            log.info("Script %r (%s): %d words, stories %s", script.title, script.writer or "?", script.word_count,
                     [s.title for s in script.stories])
    data = pm.new_manifest(scripts, target_date, kind=kind, default_look=resolved, look_overrides=run_overrides)
    drops = factcheck.drops_since(mark)
    if drops:  # the videos the checks refused (validator, editor, fact check), with their reasons
        data["dropped"] = drops
    from . import must_cover

    must_rows = must_cover.report(scripts)
    if must_rows:  # which of the day's must-cover stories got a video
        data["must_cover"] = must_rows
    if lineup_slots is not None:  # each planned video keeps its own slot's look and record
        for entry, script in zip(data["videos"], scripts):
            entry["look"] = pm.looks.compact(pm.looks.validate_look(script.look or {}))
    with pm.locked(out_dir):
        pm.drop_previews(out_dir)  # previews of the previous plan showed other videos
        path = pm.write_manifest(out_dir, data)
    log.info("Planned %d video(s): %s", len(scripts), path)
    return data


def rewrite(out_dir: Path, vid: str, run_overrides: dict | None = None) -> dict:
    """Rewrite one planned video's script from its own stories: a separate,
    explicit, paid action (one writer call), never part of a preview."""
    data = pm.read_manifest(out_dir)
    if data is None:
        raise RuntimeError(f"No plan in {out_dir}")
    entry = pm.get_entry(data, vid)
    stories = [pm.story_from_dict(s) for s in entry.get("stories", [])]
    if not any(s.summary for s in stories):
        raise RuntimeError("This video's stories have no saved summaries, so the script can't be rewritten from them")
    from .scraper import Edition

    resolved = pm.resolved_look(entry, run_overrides)
    with applied(resolved):
        log.info("Rewriting the script of %s (%r)", vid, entry.get("title"))
        scripts = build_scripts([Edition(stories[0].edition or "tech", data.get("date", ""), "TLDR", stories)],
                                n_videos=1, exclude_titles=set())
    if not scripts:
        raise RuntimeError("The writer returned no usable script")
    fresh = pm.replace_script(out_dir, vid, scripts[0])
    log.info("Rewrote %s: %r (%d words)", vid, scripts[0].title, scripts[0].word_count)
    return fresh


def _overrides_from_args(args) -> dict:
    raw = {"style": args.style, "character": args.character, "theme": args.theme, "background": args.background,
           "recipe": args.recipe, "preset": getattr(args, "preset", None),
           "bg": getattr(args, "bg", None), "accent": getattr(args, "accent", None)}
    return {k: v for k, v in raw.items() if v is not None}


def add_look_arguments(parser: argparse.ArgumentParser) -> None:
    from .look import THEMES

    parser.add_argument("--style", choices=("avatar", "explainer", "avx"), help="override VIDEO_STYLE for this run")
    parser.add_argument("--character", help="override CHARACTER for this run ('' for the default narrator)")
    parser.add_argument("--theme", choices=THEMES, help="override VIDEO_THEME for this run")
    parser.add_argument("--background", choices=("gameplay", "none"), help="override BACKGROUND_STYLE for this run")
    parser.add_argument("--recipe", help="style recipe policy for this run: auto, none or a recipe id")
    parser.add_argument("--preset", "--look-preset", dest="preset",
                        help="a look preset id from newsletter_video/looks/presets.json (theme + recipe; '' for none)")
    parser.add_argument("--bg", help="the look's background colour for this run (#rrggbb; '' for the look's own)")
    parser.add_argument("--accent", help="the look's animation/accent colour for this run (#rrggbb; '' for the look's own)")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.environ.get("BYTESIZE_ENV_FILE") or PROJECT_ROOT / ".env")  # the panel may point at another .env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.plan_day", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date", nargs="?", type=date.fromisoformat, default=None)
    parser.add_argument("--videos", type=int, default=None, help="how many to plan (default VIDEOS_PER_DAY)")
    parser.add_argument("--out", type=Path, default=None, help="folder for plan.json (default output/<date>)")
    parser.add_argument("--test", action="store_true", help="a throwaway test plan (kind=test, never uploaded)")
    parser.add_argument("--rewrite", metavar="VID", help="rewrite one planned video's script instead of planning")
    add_look_arguments(parser)
    args = parser.parse_args(argv)
    day = args.date or date.today()
    out_dir = args.out or (OUTPUT_DIR / day.isoformat())
    try:
        overrides = _overrides_from_args(args)
        look_from_env()  # fail now on a misspelled VIDEO_STYLE etc.
        if args.rewrite:
            rewrite(out_dir, args.rewrite, overrides)
        else:
            n = args.videos or (1 if args.test else int(os.environ.get("VIDEOS_PER_DAY", 3)))
            plan(day, out_dir, n, kind="test" if args.test else "day", run_overrides=overrides)
    except (RuntimeError, LookError, ValueError, pm.ManifestError) as exc:
        log.error("%s", exc)
        return 1
    print(f"Plan ready: {pm.manifest_path(out_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
