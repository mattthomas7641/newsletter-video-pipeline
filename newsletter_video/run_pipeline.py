"""Orchestrates the full daily pipeline: scrape -> script -> tts -> video.

Usage:
    python -m newsletter_video.run_pipeline               # today
    python -m newsletter_video.run_pipeline 2026-09-22    # a specific date
"""
from __future__ import annotations

import logging
import socket
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .scraper import DEFAULT_EDITIONS, fetch_editions
from .script_writer import build_script
from .video_builder import build_video

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


def run(target_date: date | None = None) -> Path:
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

    log.info("Building narration script")
    beats = build_script(editions)
    log.info("Built %d narration beats", len(beats))

    headline = editions[0].headline if editions else "TLDR Daily"
    out_path = OUTPUT_DIR / f"{target_date.isoformat()}.mp4"
    log.info("Rendering video to %s", out_path)
    build_video(beats, out_path, headline=headline)
    log.info("Done: %s", out_path)
    return out_path


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    try:
        run(d)
    except Exception:
        log.exception("Pipeline failed")
        raise
