"""Orchestrates the full daily pipeline: scrape -> pick top stories ->
one script + TTS + HyperFrames render per story.

Usage:
    python -m newsletter_video.run_pipeline               # today
    python -m newsletter_video.run_pipeline 2026-09-22    # a specific date
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .notify import notify
from .runstate import NON_SIDECAR_JSON, load_state, now_iso, read_json, sidecars, update_run
from .scraper import DEFAULT_EDITIONS, fetch_editions
from .script_writer import build_scripts
from .supervisor import (
    DEFAULT_RUN_DEADLINE_MINUTES, EXIT_CONFIG, EXIT_LOCKED, AlreadyRunning, ConfigError, RunLock, Watchdog,
    validate_config,
)
from .video_builder import build_shorts
from .youtube_upload import (
    TOKEN, YouTubeAuthError, days_with_pending_uploads, pending_videos, upload_recent, write_metadata,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_PATH = PROJECT_ROOT / "pipeline.log"
LOCK_PATH = PROJECT_ROOT / ".pipeline.lock"

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
        # gethostbyname has no timeout argument, so the process-wide default
        # is set for this probe only and restored right after: left in
        # place it was inherited by every later socket, including the
        # YouTube client, whose 30 MB uploads then died on any 5 s stall.
        previous = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(5)
            socket.gethostbyname(host)
            return True
        except OSError:
            pass
        finally:
            socket.setdefaulttimeout(previous)
        time.sleep(NETWORK_WAIT_INTERVAL_SECONDS)
    return False


def _covered_titles() -> set[str]:
    """Story titles covered by any earlier video, from every day's sidecar
    metadata. A sidecar that cannot be parsed (half-written when a run was
    killed) is skipped with a warning rather than failing every future run."""
    covered: set[str] = set()
    for folder in OUTPUT_DIR.iterdir() if OUTPUT_DIR.is_dir() else []:
        if not folder.is_dir():
            continue
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        for meta in folder.glob("*.json"):
            if meta.name in NON_SIDECAR_JSON or meta.name.startswith("."):
                continue
            data = read_json(meta)
            if not isinstance(data, dict):
                log.warning("Ignoring unreadable sidecar %s", meta)
                continue
            covered.update(data.get("stories", []))
    return covered


def _fallback_policy(scripts, uploading: bool) -> tuple[list, str | None]:
    """Rule-based fallback scripts (the writer without Claude) are poor:
    raw headlines read aloud. When uploads are on, FALLBACK_UPLOAD decides
    what happens to them: skip (default) makes none, private renders and
    uploads them private for review, public behaves as before. Returns the
    scripts to render and the per-video privacy override."""
    if not uploading or not any(s.writer == "heuristic" for s in scripts):
        return scripts, None
    policy = os.environ.get("FALLBACK_UPLOAD", "skip").strip().lower()
    heuristic = sum(1 for s in scripts if s.writer == "heuristic")
    if policy == "public":
        log.warning("%d rule-based fallback script(s) will be uploaded public (FALLBACK_UPLOAD=public)", heuristic)
        return scripts, None
    if policy == "private":
        log.warning("%d rule-based fallback script(s) will be uploaded private (FALLBACK_UPLOAD=private)", heuristic)
        return scripts, "private"
    raise RuntimeError(
        f"Claude did not write today's scripts and {heuristic} rule-based fallback script(s) are not "
        "published (FALLBACK_UPLOAD=skip). Fix the Claude API problem and rerun; nothing was rendered."
    )


def run(target_date: date | None = None) -> list[Path]:
    load_dotenv(PROJECT_ROOT / ".env")
    target_date = target_date or date.today()
    validate_config(PROJECT_ROOT, OUTPUT_DIR, TOKEN)
    out_dir = OUTPUT_DIR / target_date.isoformat()
    uploading = os.environ.get("YOUTUBE_UPLOAD") == "1"
    update_run(out_dir, started=now_iso(), finished=None, error=None)

    if not _wait_for_network():
        log.warning(
            "Network still unreachable after %ss wait — attempting scrape anyway",
            NETWORK_WAIT_TIMEOUT_SECONDS,
        )

    # Resume rather than redo: a re-run on the same day (after a crash, a
    # watchdog kill or a manual retry) only makes the videos still missing.
    n_videos = int(os.environ.get("VIDEOS_PER_DAY", 3))
    already = len(sidecars(out_dir))
    wanted = max(0, n_videos - already)
    paths: list[Path] = []
    if already:
        log.info("%d video(s) already made for %s; making %d more", already, target_date, wanted)
    if wanted:
        log.info("Scraping editions: %s for %s", DEFAULT_EDITIONS, target_date)
        editions = fetch_editions(target_date=target_date)
        total_stories = sum(len(e.stories) for e in editions)
        log.info("Fetched %d editions, %d stories total", len(editions), total_stories)
        if not editions or total_stories == 0:
            raise RuntimeError("No stories scraped — aborting before wasting TTS/video work")

        # Scan every day's output, not just today's — build_scripts does a fuzzy
        # similarity match against this set, so a story covered weeks ago (even
        # reworded) still gets excluded, not just an exact title repeat today.
        # Rendered .json metadata is kept indefinitely (clean_old_outputs only
        # deletes the mp4/audio after KEEP_DAYS), so this reaches the full history.
        covered = _covered_titles()
        if covered:
            log.info("Excluding %d previously-covered story title(s) (all-time, similarity-matched)", len(covered))

        log.info("Selecting top stories and building scripts")
        scripts = build_scripts(editions, n_videos=wanted, exclude_titles=covered)
        for script in scripts:
            log.info("Script %r (%s): %d beats, %d words, stories %s", script.title, script.writer or "?",
                     len(script.beats), script.word_count, [s.title for s in script.stories])
        if not scripts:
            raise RuntimeError("No stories selected — nothing to render")
        scripts, privacy = _fallback_policy(scripts, uploading)
        update_run(out_dir, writer=scripts[0].writer, requested=wanted)

        log.info("Rendering %d shorts to %s", len(scripts), out_dir)
        # The sidecar is written the moment each video is done (not after the
        # whole batch), so a crash at video 7 leaves videos 1-6 uploadable
        # and their stories marked as covered.
        paths = build_shorts(scripts, out_dir, OUTPUT_DIR / "_audio" / target_date.isoformat(), target_date,
                             on_rendered=lambda s, p: write_metadata(s, p, privacy=privacy))
        log.info("Done: %s", [p.name for p in paths])

    if uploading:
        try:
            upload_recent(target_date)
        except YouTubeAuthError as exc:
            notify("YouTube sign-in needs attention", f"{exc}. Today's videos are saved and will upload once fixed.")
        except Exception:
            log.exception("YouTube upload step failed; videos are saved, retry with youtube_upload")
        still_pending = sum(len(pending_videos(OUTPUT_DIR / d.isoformat()))
                            for d in days_with_pending_uploads(target_date))
        update_run(out_dir, pending_uploads=still_pending)

    clean_old_outputs(target_date)
    return paths


def run_summary(out_dir: Path, minutes: float) -> str:
    """One line: what the day produced, what went up, and what failed."""
    state = load_state(out_dir)
    videos = state["videos"].values()
    rendered = sum(1 for v in videos if v.get("status") in ("rendered", "uploaded", "upload_failed"))
    uploaded = sum(1 for v in videos if v.get("status") == "uploaded")
    failed = [name for name, v in state["videos"].items() if v.get("status") == "failed"]
    parts = [f"{rendered} video(s) made", f"{uploaded} uploaded", f"{minutes:.0f} min"]
    if state["run"].get("writer") == "heuristic":
        parts.append("rule-based scripts (Claude unavailable)")
    if failed:
        parts.append(f"{len(failed)} failed: {', '.join(failed)}")
    pending = state["run"].get("pending_uploads")
    if pending:
        parts.append(f"{pending} awaiting upload")
    return "; ".join(parts)


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
        uploaded = read_json(folder / "uploads.json", default={}) or {}
        for video in folder.glob("*.mp4"):
            if (uploaded.get(video.name) or {}).get("id"):  # a recorded id, not just an attempt
                freed += video.stat().st_size
                video.unlink()
        audio = OUTPUT_DIR / "_audio" / folder.name
        if audio.is_dir():
            freed += sum(f.stat().st_size for f in audio.rglob("*") if f.is_file())
            shutil.rmtree(audio)
    if freed:
        log.info("Cleanup: freed %.0f MB from days before %s", freed / 1e6, cutoff)


def main(argv: list[str] | None = None) -> int:
    """The unattended entry point: one run at a time (lockfile), a hard
    wall-clock deadline (watchdog), and a notification whichever way it
    ends. Returns the process exit code."""
    argv = sys.argv[1:] if argv is None else argv
    d = date.fromisoformat(argv[0]) if argv else date.today()
    load_dotenv(PROJECT_ROOT / ".env")
    out_dir = OUTPUT_DIR / d.isoformat()
    started = time.monotonic()

    def minutes() -> float:
        return (time.monotonic() - started) / 60

    try:
        lock = RunLock(LOCK_PATH).acquire()
    except AlreadyRunning as exc:
        log.error("Not starting: %s", exc)
        notify("Pipeline skipped", f"A previous run is still going ({exc.holder}). If it is stuck, kill it; "
                                   "the lock frees itself when the process ends.")
        return EXIT_LOCKED

    def terminated(signum, frame):  # launchctl stop / kill: fail visibly instead of vanishing
        raise RuntimeError("terminated by SIGTERM")

    signal.signal(signal.SIGTERM, terminated)
    deadline_min = int(os.environ.get("RUN_DEADLINE_MINUTES", DEFAULT_RUN_DEADLINE_MINUTES) or DEFAULT_RUN_DEADLINE_MINUTES)
    watchdog = Watchdog(
        deadline_min * 60,
        on_expire=lambda: (
            update_run(out_dir, finished=None, error=f"killed by watchdog after {deadline_min} min"),
            notify("Pipeline killed (watchdog)",
                   f"Run exceeded {deadline_min} min and was stopped. So far: {run_summary(out_dir, minutes())}. "
                   "Rerun to resume; finished videos are kept."),
        ),
    ).start()
    try:
        run(d)
    except ConfigError as exc:
        log.error("%s", exc)
        notify("Pipeline not started", str(exc))
        return EXIT_CONFIG
    except Exception as exc:
        log.exception("Pipeline failed")
        update_run(out_dir, finished=now_iso(), error=f"{type(exc).__name__}: {exc}"[:500])
        notify("Pipeline failed", f"{type(exc).__name__}: {str(exc)[:400]}\nSo far: {run_summary(out_dir, minutes())}")
        return 1
    else:
        summary = run_summary(out_dir, minutes())
        update_run(out_dir, finished=now_iso(), error=None)
        failed = any(v.get("status") in ("failed", "upload_failed") for v in load_state(out_dir)["videos"].values())
        notify("Pipeline finished with problems" if failed else "Pipeline finished", summary, ok=not failed)
        return 0
    finally:
        watchdog.cancel()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
