"""Orchestrates the full daily pipeline: scrape -> pick top stories ->
one script + TTS + HyperFrames render per story.

Usage:
    python -m newsletter_video.run_pipeline               # today
    python -m newsletter_video.run_pipeline 2026-09-22    # a specific date
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .scraper import DEFAULT_EDITIONS, fetch_editions
from .script_writer import build_scripts
from .video_builder import build_shorts
from .youtube_upload import upload_recent, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_PATH = PROJECT_ROOT / "pipeline.log"

# launchd fires this at a fixed wall-clock time, which can land while the
# Mac is still asleep or mid-wake (Wi-Fi not reassociated, DNS not up yet —
# this caused several silent failures before this preflight was added).
NETWORK_WAIT_TIMEOUT_SECONDS = 300
NETWORK_WAIT_INTERVAL_SECONDS = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("newsletter_video")


def _wait_for_network(host: str = "tldr.tech") -> bool:
    deadline = time.monotonic() + NETWORK_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            socket.setdefaulttimeout(5)
            socket.gethostbyname(host)
            return True
        except OSError:
            time.sleep(NETWORK_WAIT_INTERVAL_SECONDS)
    return False


def run(target_date: date | None = None) -> list[Path]:
    load_dotenv(PROJECT_ROOT / ".env")
    target_date = target_date or date.today()

    if not _wait_for_network():
        log.warning(
            "Network still unreachable after %ss wait — attempting scrape anyway",
            NETWORK_WAIT_TIMEOUT_SECONDS,
        )

    log.info("Scraping editions: %s for %s", DEFAULT_EDITIONS, target_date)
    editions = fetch_editions(target_date=target_date)
    total_stories = sum(len(e.stories) for e in editions)
    log.info("Fetched %d editions, %d stories total", len(editions), total_stories)
    if not editions or total_stories == 0:
        raise RuntimeError("No stories scraped — aborting before wasting TTS/video work")

    out_dir = OUTPUT_DIR / target_date.isoformat()
    covered = set()
    for meta in out_dir.glob("*.json"):
        if meta.name != "uploads.json":
            covered.update(json.loads(meta.read_text()).get("stories", []))
    if covered:
        log.info("Skipping stories already covered today: %s", sorted(covered))

    log.info("Selecting top stories and building scripts")
    scripts = build_scripts(editions, exclude_titles=covered)
    for script in scripts:
        log.info("Script %r: %d beats, %d words, stories %s", script.title, len(script.beats),
                 script.word_count, [s.title for s in script.stories])
    if not scripts:
        raise RuntimeError("No stories selected — nothing to render")

    log.info("Rendering %d shorts to %s", len(scripts), out_dir)
    paths = build_shorts(scripts, out_dir, OUTPUT_DIR / "_audio" / target_date.isoformat(), target_date)
    for script, path in zip(scripts, paths):
        write_metadata(script, path)
    log.info("Done: %s", [p.name for p in paths])

    if os.environ.get("YOUTUBE_UPLOAD") == "1":
        try:
            upload_recent(target_date)
        except Exception:
            log.exception("YouTube upload step failed; videos are saved, retry with youtube_upload")

    clean_old_outputs(target_date)
    return paths


KEEP_DAYS = 7


def clean_old_outputs(today: date, keep_days: int = KEEP_DAYS) -> None:
    """Frees disk space: for days older than `keep_days`, deletes video files
    that are confirmed uploaded (their .json metadata is kept) and that day's
    voice/work files. Videos never uploaded, and anything outside dated
    folders, are left alone."""
    cutoff = today - timedelta(days=keep_days)
    freed = 0
    for folder in OUTPUT_DIR.iterdir():
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if day >= cutoff or not folder.is_dir():
            continue
        record_path = folder / "uploads.json"
        uploaded = json.loads(record_path.read_text()) if record_path.exists() else {}
        for video in folder.glob("*.mp4"):
            if video.name in uploaded:
                freed += video.stat().st_size
                video.unlink()
        audio = OUTPUT_DIR / "_audio" / folder.name
        if audio.is_dir():
            freed += sum(f.stat().st_size for f in audio.rglob("*") if f.is_file())
            shutil.rmtree(audio)
    if freed:
        log.info("Cleanup: freed %.0f MB from days before %s", freed / 1e6, cutoff)


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    try:
        run(d)
    except Exception:
        log.exception("Pipeline failed")
        raise
