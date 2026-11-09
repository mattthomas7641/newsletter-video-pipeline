"""The explicit final render of planned videos (plan_manifest.py).

    python -m newsletter_video.render_plan 2026-11-07 --final              # every planned video
    python -m newsletter_video.render_plan 2026-11-07 --final --only v01,v03
    python -m newsletter_video.render_plan output/_previews/<id> --final   # a test plan: never uploaded

`--final` is required: this is the only command in the project that turns
a plan into full-quality videos, and nothing does it implicitly. It runs
under the same hardening as the daily job (run_pipeline.supervised: the
lockfile, the watchdog, status.json, a notification), writes each video's
sidecar the moment it is rendered, and for a real day folder uploads
afterwards exactly as the daily run would (YOUTUBE_UPLOAD, YOUTUBE_PRIVACY,
FALLBACK_UPLOAD all apply). Each video renders with its own look from the
plan layered over the .env defaults.
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
from .look import LookError
from .run_pipeline import OUTPUT_DIR, PROJECT_ROOT, _fallback_policy, clean_old_outputs, supervised
from .runstate import update_run, now_iso
from .supervisor import validate_config
from .video_builder import build_shorts
from .youtube_upload import TOKEN, YouTubeAuthError, days_with_pending_uploads, pending_videos, upload_recent, write_metadata

log = logging.getLogger("newsletter_video")


class NotFinal(RuntimeError):
    """A full render was requested without --final."""


def resolve_folder(target: str) -> tuple[Path, bool]:
    """(folder, is_test). A date means output/<date>; anything else is a
    folder path that must hold a plan.json."""
    try:
        day = date.fromisoformat(target)
        return OUTPUT_DIR / day.isoformat(), False
    except ValueError:
        folder = Path(target).expanduser()
        if not folder.is_absolute():
            folder = PROJECT_ROOT / folder
        return folder, True


def render(folder: Path, only: list[str] | None = None, is_test: bool = False, final: bool = False,
           run_overrides: dict | None = None) -> list[Path]:
    if not final:
        raise NotFinal("A full render needs --final; previews are `python -m newsletter_video.preview`")
    data = pm.read_manifest(folder)
    if data is None:
        raise RuntimeError(f"No plan in {folder} (plan first: python -m newsletter_video.plan_day)")
    try:
        day = date.fromisoformat(data.get("date", ""))
    except ValueError:
        day = date.today()
    wanted = [e for e in data["videos"] if (only is None or e["id"] in only)]
    if only:
        unknown = sorted(set(only) - {e["id"] for e in wanted})
        if unknown:
            raise RuntimeError(f"No such planned video(s): {', '.join(unknown)}")
    wanted = [e for e in wanted if e.get("status") != "rendered"]
    if not is_test:
        # The plan may be hours old: the scheduled job (or an earlier render) may
        # have covered a story since. Those videos are skipped, never duplicated.
        from .run_pipeline import _covered_titles
        from .script_writer import _is_covered, _normalize_title

        covered = [_normalize_title(t) for t in _covered_titles()]
        still = []
        for entry in wanted:
            titles = [str((s or {}).get("title", s)) for s in entry.get("stories", [])]
            hit = next((t for t in titles if _is_covered(t, covered)), None)
            if hit:
                log.warning("Skipping %s (%r): its story %r is already covered by a rendered video", entry["id"],
                            entry.get("title"), hit)
                pm.update_entry(folder, entry["id"], status="failed", error=f"Skipped: story already covered ({hit[:80]})")
                continue
            still.append(entry)
        wanted = still
    if not wanted:
        log.info("Nothing to render: every selected video is already rendered or covered")
        return []
    uploading = os.environ.get("YOUTUBE_UPLOAD") == "1" and not is_test
    validate_config(PROJECT_ROOT, OUTPUT_DIR, TOKEN)
    # mode="render" replaces an approval-mode run's "plan", so the summary counts videos again
    update_run(folder, started=now_iso(), finished=None, error=None, mode="render")

    scripts = []
    for entry in wanted:
        try:
            script = pm.script_from_entry(entry)
        except (LookError, ValueError) as exc:  # a bad stored look or plan fails this video only
            log.error("Skipping %s: %s", entry["id"], exc)
            pm.update_entry(folder, entry["id"], status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            continue
        # The entry's own look (partial) rides on the script; the builder
        # layers it over the defaults at render time. A run-level override
        # sits between the two.
        if run_overrides:
            merged = dict(run_overrides)
            merged.update({k: v for k, v in (script.look or {}).items() if v is not None})
            script.look = merged
        pos = pm._position(entry)
        if pos is not None:  # LOOK_ACCENT_ROTATE: this video's place in the plan picks its accent (unless it chose one)
            from .look import with_rotation

            script.look = with_rotation(script.look, pos)
        if script.look and script.look.get("recipe"):
            plan = getattr(script, "plan", None)
            if plan is not None:
                from .styles.planner import apply_recipe_policy

                apply_recipe_policy(plan, script.look["recipe"])
        scripts.append((entry, script))
    if not scripts:
        raise RuntimeError("No planned video could be rendered; see the plan's errors")
    privacy = None
    if not is_test:
        _, privacy = _fallback_policy([s for _, s in scripts], uploading)
    update_run(folder, writer=scripts[0][1].writer, requested=len(scripts))
    by_title = {id(s): e for e, s in scripts}

    def on_rendered(script, path):
        write_metadata(script, path, privacy=privacy)
        entry = by_title[id(script)]
        pm.update_entry(folder, entry["id"], status="rendered", error=None,
                        rendered={"file": path.name, "at": now_iso()})

    log.info("Rendering %d planned video(s) to %s", len(scripts), folder)
    audio_dir = (folder / "_work" / "audio") if is_test else (OUTPUT_DIR / "_audio" / day.isoformat())
    paths = build_shorts([s for _, s in scripts], folder, audio_dir, day, on_rendered=on_rendered)
    done = {p.name for p in paths}
    for entry, script in scripts:
        fresh = pm.get_entry(pm.read_manifest(folder), entry["id"])
        if fresh.get("status") != "rendered":
            pm.update_entry(folder, entry["id"], status="failed",
                            error=f"{script.title!r} did not render; see status.json")
    log.info("Done: %s", sorted(done))

    if uploading:
        try:
            upload_recent(day)
        except YouTubeAuthError as exc:
            from .notify import notify

            notify("YouTube sign-in needs attention", f"{exc}. The videos are saved and will upload once fixed.")
        except Exception:
            log.exception("YouTube upload step failed; videos are saved, retry with youtube_upload")
        still_pending = sum(len(pending_videos(OUTPUT_DIR / d.isoformat())) for d in days_with_pending_uploads(day))
        update_run(folder, pending_uploads=still_pending)
    if not is_test:
        clean_old_outputs(day)
    return paths


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.environ.get("BYTESIZE_ENV_FILE") or PROJECT_ROOT / ".env")  # the panel may point at another .env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.render_plan", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="a date (output/<date>/plan.json) or a folder holding a plan.json")
    parser.add_argument("--final", action="store_true", help="required: render full-quality videos")
    parser.add_argument("--only", help="comma-separated video ids to render, e.g. v01,v03 (default: all)")
    parser.add_argument("--test", action="store_true", help="treat the folder as a test plan: never upload")
    from .plan_day import add_look_arguments, _overrides_from_args

    add_look_arguments(parser)
    args = parser.parse_args(argv)
    folder, is_test = resolve_folder(args.target)
    is_test = is_test or args.test
    only = [v.strip() for v in args.only.split(",") if v.strip()] if args.only else None
    if not args.final:
        log.error("Refusing to render without --final. For a quick look use: python -m newsletter_video.preview %s", args.target)
        return 2
    try:
        overrides = _overrides_from_args(args)
    except LookError as exc:
        log.error("%s", exc)
        return 2
    return supervised(folder, lambda: render(folder, only, is_test, final=True, run_overrides=overrides),
                      label="Final render")


if __name__ == "__main__":
    sys.exit(main())
