"""Keeps the pipeline's on-disk state in a Cloud Storage bucket, so a fresh container can carry on where the
last one stopped.

The pipeline itself only ever reads and writes plain files under output/ (and lineup.json). Rather than
teach every module about buckets, a cloud run copies the state down before it starts and back up while it
runs and when it ends (cloud_run.py). With no STATE_BUCKET set nothing here does anything, so the Mac's
daily run is unchanged.

What travels:
  output/<day>/...   sidecars, uploads.json, status.json, plan.json (always: they hold the covered-story
                     history and the duplicate-upload guard) plus the videos and thumbnails of recent days
                     (a failed upload is retried the next day from those)
  config/lineup.json -> lineup.json at the project root (what to make each day)
What does not: scratch work folders, caches, audio and drafts.

    python -m newsletter_video.cloud_state seed     # first time: upload this Mac's history
    python -m newsletter_video.cloud_state config   # upload lineup.json after editing it
    python -m newsletter_video.cloud_state pull     # look at what the cloud has
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
import threading
from datetime import date
from pathlib import Path

log = logging.getLogger("newsletter_video")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LINEUP_FILE = PROJECT_ROOT / "lineup.json"
OUTPUT_PREFIX = "output/"
CONFIG_LINEUP = "config/lineup.json"

SKIP_DIRS = {"_work", "_cache", "_audio", "_drafts", "_style_eval", ".partial", "__pycache__"}
SKIP_NAMES = {".DS_Store", ".pipeline.lock"}
SMALL_SUFFIXES = {".json", ".md", ".txt", ".log"}  # state files: always synced, whatever the day
DEFAULT_MEDIA_DAYS = 3  # videos/thumbnails are synced for today and this many days before it
PERIODIC_PUSH_SECONDS = 300


def _md5_b64(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def _day_of(rel_parts: tuple[str, ...]) -> date | None:
    try:
        return date.fromisoformat(rel_parts[0])
    except (ValueError, IndexError):
        return None


class CloudState:
    def __init__(self, bucket, output_dir: Path = OUTPUT_DIR, lineup_file: Path = LINEUP_FILE,
                 media_days: int = DEFAULT_MEDIA_DAYS, today: date | None = None):
        self.bucket = bucket  # anything with list_blobs(prefix=), blob(name) (google.cloud.storage.Bucket)
        self.output_dir = Path(output_dir)
        self.lineup_file = Path(lineup_file)
        self.media_days = media_days
        self.today = today or date.today()
        self._push_lock = threading.Lock()

    # -- what counts ----------------------------------------------------------

    def _wanted(self, rel: Path) -> bool:
        """Should this file (path relative to output/) travel?"""
        if rel.name in SKIP_NAMES or any(part in SKIP_DIRS for part in rel.parts):
            return False
        if rel.suffix.lower() in SMALL_SUFFIXES:
            return True
        day = _day_of(rel.parts)
        if day is None:
            return False  # big files outside a day folder (renders in _drafts, ...) stay put
        return 0 <= (self.today - day).days <= self.media_days

    # -- pull -----------------------------------------------------------------

    def pull(self) -> dict:
        """Downloads what the bucket has and this disk lacks or has differently. Returns counts."""
        downloaded = skipped = 0
        for blob in self.bucket.list_blobs(prefix=OUTPUT_PREFIX):
            rel = Path(blob.name[len(OUTPUT_PREFIX):])
            if not blob.name[len(OUTPUT_PREFIX):] or not self._wanted(rel):
                continue
            dest = self.output_dir / rel
            if dest.exists() and dest.stat().st_size == blob.size and _md5_b64(dest) == blob.md5_hash:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            blob.download_to_filename(str(tmp))
            os.replace(tmp, dest)
            downloaded += 1
        lineup = self.bucket.blob(CONFIG_LINEUP)
        if lineup.exists():
            lineup.download_to_filename(str(self.lineup_file))
        log.info("Cloud state pulled: %d files downloaded, %d already current", downloaded, skipped)
        return {"downloaded": downloaded, "skipped": skipped}

    # -- push -----------------------------------------------------------------

    def push(self) -> dict:
        """Uploads every wanted file the bucket lacks or has differently. Never raises: a failed copy is
        logged and counted, and the next push tries again."""
        with self._push_lock:
            remote = {b.name: b.md5_hash for b in self.bucket.list_blobs(prefix=OUTPUT_PREFIX)}
            uploaded = failed = 0
            if not self.output_dir.is_dir():
                return {"uploaded": 0, "failed": 0}
            for path in sorted(self.output_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.output_dir)
                if not self._wanted(rel):
                    continue
                name = OUTPUT_PREFIX + rel.as_posix()
                try:
                    if remote.get(name) == _md5_b64(path):
                        continue
                    self.bucket.blob(name).upload_from_filename(str(path))
                    uploaded += 1
                except Exception as exc:  # network blip, file replaced mid-read
                    failed += 1
                    log.warning("Cloud state: could not upload %s (%s)", name, exc)
            if uploaded or failed:
                log.info("Cloud state pushed: %d uploaded, %d failed", uploaded, failed)
            return {"uploaded": uploaded, "failed": failed}

    def push_config(self) -> bool:
        if not self.lineup_file.exists():
            return False
        self.bucket.blob(CONFIG_LINEUP).upload_from_filename(str(self.lineup_file))
        return True

    def start_periodic_push(self, seconds: int = PERIODIC_PUSH_SECONDS) -> threading.Event:
        """Pushes in the background every few minutes, so a container killed without warning (out of memory)
        loses minutes of state rather than the whole run. Set the returned event to stop it."""
        stop = threading.Event()

        def loop():
            while not stop.wait(seconds):
                try:
                    self.push()
                except Exception:
                    log.exception("Periodic cloud-state push failed")

        threading.Thread(target=loop, name="cloud-state-push", daemon=True).start()
        return stop


def from_env() -> CloudState | None:
    """The configured state store, or None when STATE_BUCKET is not set (the Mac)."""
    name = os.environ.get("STATE_BUCKET", "").strip()
    if not name:
        return None
    from google.cloud import storage  # only needed in the cloud image

    return CloudState(storage.Client().bucket(name))


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "pull"
    state = from_env()
    if state is None:
        print("Set STATE_BUCKET to the bucket name first.")
        return 2
    if cmd == "seed":
        print("pushed:", state.push(), "| lineup:", state.push_config())
    elif cmd == "config":
        print("lineup uploaded" if state.push_config() else "no lineup.json to upload")
    elif cmd == "pull":
        print(state.pull())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
