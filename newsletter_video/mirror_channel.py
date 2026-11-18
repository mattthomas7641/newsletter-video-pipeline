"""A second channel's day, made from the first channel's finished videos: no scraping, no writer, no editor, no fact
check, so no Claude calls. The second channel reads the same scripts with its own narrator and voice.

    python -m newsletter_video.mirror_channel [YYYY-MM-DD] [--count 5] [--list] [--render]

1. Reads the day's finished videos from the source: a bucket (`MIRROR_SOURCE=gs://<bucket>/output`, needs `gsutil`)
   or a folder (`MIRROR_SOURCE=/path/to/output`), the other channel's saved scene plans.
2. Picks the COUNT most promising with Jev (the same questions the story screen asks: how newsworthy, would a
   well-informed follower of AI and tech want to know it; both are close to free). Without Jev, the source's own
   order is kept (the writer put its strongest video first). Stories this channel already made are skipped.
3. Writes output/<day>/plan.json with this channel's look (`CHARACTER`, the avatar-explainer style) and, with
   --render, renders and uploads it exactly as `python -m newsletter_video.render_plan <day> --final` would.

Honest limit: on this channel's own history, Jev's scores did not predict views, so "most likely to do well" is a
proxy (news value), not a forecast.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("newsletter_video")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
NOT_SIDECARS = {"status.json", "uploads.json", "plan.json", "plan.json.lock"}
DEFAULT_COUNT = 5


# --- the source: another channel's finished videos -----------------------------------------------------------

def _gsutil() -> str:
    return os.environ.get("GSUTIL") or shutil.which("gsutil") or str(Path.home() / "google-cloud-sdk" / "bin" / "gsutil")


def fetch_sidecars(day: date, source: str, into: Path) -> list[dict]:
    """The day's sidecars (the <video>.json files) as dicts, oldest number first. `source` is a gs:// prefix
    ending in /output, or a local output folder."""
    into.mkdir(parents=True, exist_ok=True)
    if source.startswith("gs://"):
        subprocess.run([_gsutil(), "-q", "-m", "cp", f"{source.rstrip('/')}/{day.isoformat()}/*.json", str(into)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        files = sorted(into.glob("*.json"))
    else:
        files = sorted((Path(source) / day.isoformat()).glob("*.json"))
    out = []
    for f in files:
        if f.name in NOT_SIDECARS or f.name.startswith("."):
            continue
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Ignoring unreadable sidecar %s", f.name)
            continue
        if isinstance(meta, dict) and meta.get("scene_plan"):
            meta["_file"] = f.name
            out.append(meta)
    return out


def usable(meta: dict) -> bool:
    """An avatar-explainer video with a stored scene list (what a re-render needs)."""
    from .avx_writer import is_avx_plan

    return is_avx_plan(meta.get("scene_plan"))


# --- choosing ------------------------------------------------------------------------------------------------

def _blurb(meta: dict) -> str:
    plan = meta.get("scene_plan") or {}
    says = [str(s.get("say", "")) for s in (plan.get("scenes") or [])[:3] if isinstance(s, dict)]
    return f"{meta.get('title', '')}\n\n" + " ".join(says)


def jev_scores(metas: list[dict]) -> list[float] | None:
    """newsworthiness + importance per video (the story screen's own questions), or None when Jev is not
    configured or fails (the caller then keeps the source's order)."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None
    try:
        import typesafe_sdk as ts

        from . import script_writer as sw

        questions = {
            "important": ts.Noul(instructions=sw._JEV_IMPORTANT_INSTRUCTIONS),
            "newsworthiness": ts.Score(instructions=sw._JEV_NEWSWORTHINESS_INSTRUCTIONS,
                                       criteria=sw._JEV_NEWSWORTHINESS_LEVELS),
        }
        client = ts.TypeSafeClient()
        scores = []
        for meta in metas:
            a = client.system_one(state=_blurb(meta), questions=questions).answers
            scores.append(float(a["newsworthiness"].score) + float(a["important"].noul))
        return scores
    except Exception as exc:  # noqa: BLE001 - Jev is a convenience here, never a reason to make no videos
        log.warning("Jev unavailable (%s: %s); keeping the source's order", type(exc).__name__, exc)
        return None


def choose(metas: list[dict], count: int, covered_titles: list[str] = (), scorer=jev_scores) -> list[tuple[dict, float | None]]:
    """The `count` best videos, best first, as (sidecar, score). Videos whose story this channel already covered
    are dropped first; fewer than `count` are returned when fewer remain."""
    from .script_writer import _is_covered, _normalize_title

    covered = [_normalize_title(t) for t in covered_titles]
    fresh = []
    for meta in metas:
        titles = [str(s.get("title", s)) if isinstance(s, dict) else str(s) for s in meta.get("stories", [])] or [meta.get("title", "")]
        if any(_is_covered(t, covered) for t in titles):
            log.info("Skipping %r: this channel already covered it", meta.get("title"))
            continue
        fresh.append(meta)
    scores = scorer(fresh) if fresh else None
    if scores is None:
        return [(m, None) for m in fresh[:count]]
    ranked = sorted(zip(fresh, scores), key=lambda pair: -pair[1])  # a stable sort: ties keep the source's order
    return ranked[:count]


# --- the plan ------------------------------------------------------------------------------------------------

def build_plan(day: date, chosen: list[tuple[dict, float | None]], look: dict, folder: Path) -> Path:
    """Writes folder/plan.json from the chosen sidecars, every video with `look` (this channel's narrator)."""
    from . import plan_manifest as pm

    scripts = [pm.script_from_sidecar(meta) for meta, _ in chosen]
    manifest = pm.new_manifest(scripts, day, look_overrides=look)  # defaults come from this channel's own .env
    folder.mkdir(parents=True, exist_ok=True)
    return pm.write_manifest(folder, manifest)


def channel_look() -> dict:
    character = os.environ.get("CHARACTER", "").strip()
    if not character:
        raise SystemExit("Set CHARACTER in .env: the narrator this channel uses (a folder under assets/characters/).")
    return {"style": "avx", "character": character}


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.environ.get("BYTESIZE_ENV_FILE") or PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.mirror_channel", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("day", nargs="?", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--count", type=int, default=int(os.environ.get("MIRROR_COUNT", DEFAULT_COUNT)))
    parser.add_argument("--source", default=os.environ.get("MIRROR_SOURCE", ""),
                        help="gs://<bucket>/output or a local output folder (default: MIRROR_SOURCE)")
    parser.add_argument("--list", action="store_true", help="only show the ranking; write nothing")
    parser.add_argument("--render", action="store_true", help="render and upload the plan after writing it")
    args = parser.parse_args(argv)
    if not args.source:
        log.error("No source: set MIRROR_SOURCE (gs://<bucket>/output) in .env or pass --source")
        return 2
    day = date.fromisoformat(args.day)
    from .run_pipeline import _covered_titles

    with tempfile.TemporaryDirectory(prefix="mirror-") as tmp:
        metas = [m for m in fetch_sidecars(day, args.source, Path(tmp)) if usable(m)]
    if not metas:
        log.error("No finished avatar-explainer videos for %s in %s", day, args.source)
        return 1
    chosen = choose(metas, args.count, sorted(_covered_titles()))
    log.info("%d finished videos on %s; %d chosen:", len(metas), day, len(chosen))
    for n, (meta, score) in enumerate(chosen, 1):
        log.info("  %d. %s%s", n, meta.get("title", "?"), f"  (Jev {score:.2f})" if score is not None else "")
    if args.list or not chosen:
        return 0
    from . import plan_manifest as pm

    folder = OUTPUT_DIR / day.isoformat()
    if pm.read_manifest(folder) is not None:  # a re-run the same day: keep the plan (and what it already rendered)
        log.info("A plan for %s already exists; keeping it and rendering what is left", day)
        path = pm.manifest_path(folder)
    else:
        path = build_plan(day, chosen, channel_look(), folder)
        log.info("Plan written: %s", path)
    if not args.render:
        log.info("Render it with: python -m newsletter_video.render_plan %s --final", day)
        return 0
    from .render_plan import main as render_main

    return render_main([day.isoformat(), "--final"])


if __name__ == "__main__":
    sys.exit(main())
