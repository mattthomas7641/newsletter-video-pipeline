"""Starting pipeline runs from the panel, and noticing runs it didn't start.

Two kinds of run:
- "test":  one video, never uploaded, written to output/_previews/<id>/ (a
           folder the real pipeline's story-history scan, upload step and
           cleanup all ignore because it isn't a dated folder).
- "batch": today's real run, exactly as the 7:00 AM job would do it.

Only one run at a time. Besides panel-started jobs we also refuse when
something else is already running the pipeline: a live process (the
scheduled job, a terminal) or a held lockfile (see `LOCKFILE_CANDIDATES`).
"""
from __future__ import annotations

import fcntl
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import UIPaths
from .envstore import EnvStore
from .jobs import JobManager, JobView, pid_alive

# The failure-handling update adds a lockfile; its exact name isn't known to
# this branch, so a few likely names are checked. Add the real one here (or set
# PIPELINE_LOCKFILE) once it lands. The process check below catches a run either way.
LOCKFILE_CANDIDATES = (
    "output/.pipeline.lock", "output/pipeline.lock", "output/.run.lock",
    ".pipeline.lock", "pipeline.lock",
)
PIPELINE_MARKERS = ("newsletter_video.run_pipeline", "newsletter_video.ui.preview_runner")

STAGES = [  # (log fragment, label, step)
    ("Scraping editions", "Reading today's newsletters", 1),
    ("Fetched", "Reading today's newsletters", 1),
    ("Selecting top stories", "Choosing stories and writing scripts", 2),
    ("Script '", "Choosing stories and writing scripts", 2),
    ("Rendering", "Voicing and rendering the video", 3),
    ("Done:", "Rendering finished", 4),
    ("YouTube", "Uploading to YouTube", 4),
]
TOTAL_STEPS = 4


class RunBlocked(Exception):
    """A run can't start right now; the message is shown to the owner as is."""


@dataclass
class Activity:
    source: str  # panel | process | lockfile
    label: str
    pid: int | None = None

    def to_json(self) -> dict:
        return {"source": self.source, "label": self.label, "pid": self.pid}


def list_processes() -> list[tuple[int, int, str]]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True,
                             timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


def lock_held(path: Path) -> bool:
    """True when `path` is a lock that someone currently holds: it names a live
    process, or another process has it flock()ed. A leftover file isn't a lock."""
    try:
        if not path.is_file():
            return False
        head = path.read_text(errors="replace")[:200]
    except OSError:
        return False
    for number in re.findall(r"\d+", head)[:1]:
        pid = int(number)
        if pid != os.getpid() and pid_alive(pid):
            return True
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def parse_stage(lines: list[str]) -> dict:
    """Friendly progress from the tail of a run's log."""
    stage, step = None, 0
    for line in reversed(lines):
        for fragment, label, number in STAGES:
            if fragment in line:
                stage, step = label, number
                break
        if stage:
            break
    last = ""
    for line in reversed(lines):
        text = re.sub(r"^\d{4}-\d\d-\d\d[ T][\d:,.]+\s*", "", line).strip()
        if text:
            last = text[:240]
            break
    return {"stage": stage, "step": step, "total_steps": TOTAL_STEPS, "last_line": last}


class RunService:
    def __init__(self, paths: UIPaths, jobs: JobManager, store: EnvStore,
                 lister: Callable[[], list[tuple[int, int, str]]] = list_processes,
                 environ: dict | None = None):
        self.paths = paths
        self.jobs = jobs
        self.store = store
        self.lister = lister
        self.environ = environ if environ is not None else os.environ

    # --- what's running ---

    def activity(self) -> list[Activity]:
        found: list[Activity] = []
        for job in self.jobs.active(kinds=("test", "batch")):
            found.append(Activity("panel", "A test video" if job.kind == "test" else "Today's batch", None))
        ours = self.jobs.wrapper_pids() | {os.getpid()}
        for pid, ppid, command in self.lister():
            if pid in ours or ppid in ours or "newsletter_video.ui.jobwrap" in command:
                continue
            tokens = command.split()
            if any(marker in tokens for marker in PIPELINE_MARKERS) or any(
                    Path(t).name == "run_daily.sh" for t in tokens):
                found.append(Activity("process", "A pipeline run started outside the panel "
                                                 "(the scheduled job or a terminal)", pid))
        candidates = [self.paths.root / rel for rel in LOCKFILE_CANDIDATES]
        override = self.environ.get("PIPELINE_LOCKFILE")
        if override:
            candidates.append(Path(override))
        for path in candidates:
            if lock_held(path):
                found.append(Activity("lockfile", "The pipeline's lockfile is held, so a run is in progress", None))
                break
        return found

    def _guard(self) -> None:
        busy = self.activity()
        if busy:
            first = busy[0]
            if first.source == "panel":
                raise RunBlocked(f"{first.label} is already being made. Wait for it to finish, or stop it first.")
            raise RunBlocked(f"{first.label}. Wait for it to finish before starting another.")

    # --- starting ---

    def _env(self, extra: dict[str, str]) -> dict:
        env = dict(os.environ)
        env.update(extra)
        return env

    def start_test(self, overrides: dict | None = None) -> JobView:
        clean = EnvStore.validate(overrides or {})
        self._guard()
        job_id = self.jobs.new_id()
        preview = self.paths.output_dir / "_previews" / job_id
        env = self._env({
            **clean,
            "VIDEOS_PER_DAY": "1",
            "YOUTUBE_UPLOAD": "0",
            "FALLBACK_UPLOAD": "0",
            "NOTIFY_WEBHOOK_URL": "",  # a failed test shouldn't page anyone
            "BYTESIZE_TEST_RUN": "1",
        })
        argv = [sys.executable, "-m", "newsletter_video.ui.preview_runner", "--out", str(preview)]
        return self.jobs.create("test", argv, env, {"preview_id": job_id, "used_unsaved": sorted(clean)},
                                job_id=job_id)

    def start_batch(self) -> JobView:
        self._guard()
        saved = self.store.read()["values"]
        argv = [sys.executable, "-m", "newsletter_video.run_pipeline"]
        meta = {
            "videos": int(saved["VIDEOS_PER_DAY"]),
            "uploads": saved["YOUTUBE_UPLOAD"] == "1",
            "privacy": saved["YOUTUBE_PRIVACY"],
        }
        return self.jobs.create("batch", argv, self._env({}), meta)

    # --- reporting ---

    def describe(self, job: JobView | None) -> dict | None:
        if job is None:
            return None
        data = job.to_json()
        data.update(parse_stage(self.jobs.tail(job.id)))
        if job.kind == "test":
            data["videos"] = self.preview_videos(job.meta.get("preview_id", ""))
        if job.state in ("failed", "interrupted"):
            data["error_tail"] = self.jobs.tail(job.id, 8)
        return data

    def preview_videos(self, preview_id: str) -> list[dict]:
        if not re.fullmatch(r"[a-z0-9]{6,40}", preview_id or ""):
            return []
        folder = self.paths.output_dir / "_previews" / preview_id
        videos = []
        for mp4 in sorted(folder.glob("*.mp4")) if folder.is_dir() else []:
            videos.append({
                "name": mp4.name, "title": _title_from(mp4.with_suffix(".json"), mp4.stem),
                "url": f"/media/preview/{preview_id}/{mp4.name}", "path": str(mp4),
                "size": mp4.stat().st_size, "modified": mp4.stat().st_mtime,
            })
        return videos

    def latest(self) -> JobView | None:
        jobs = self.jobs.list(limit=15)
        for job in jobs:
            if job.kind in ("test", "batch"):
                return job
        return None


def _title_from(meta_path: Path, fallback: str) -> str:
    import json

    try:
        meta = json.loads(meta_path.read_text())
        title = (meta.get("script") or {}).get("title") or meta.get("title")
    except (OSError, ValueError, AttributeError):
        title = None
    return str(title)[:140] if title else fallback.replace("_", " ").replace("-", " ").strip()
