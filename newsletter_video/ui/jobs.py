"""Background jobs (test render, daily batch, voice sample, voice cloning).

Each job lives in its own directory under <state_dir>/jobs/<id>/:
    job.json     what it is and which supervisor process owns it
    log.txt      everything the job printed
    child.json   the real process, once started
    result.json  written by the supervisor when the process ends

Because the state is on disk and the supervisor (`jobwrap`) runs in its own
session, a job survives the control panel being closed or restarted: the next
panel simply reads the same directory and carries on.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# spawner(argv, env, job_dir, cwd) -> supervisor pid
Spawner = Callable[[list[str], dict, Path, Path], int]

LOG_CHUNK_BYTES = 64 * 1024
KEEP_JOBS = 40


def default_spawner(argv: list[str], env: dict, job_dir: Path, cwd: Path) -> int:
    with (job_dir / "log.txt").open("ab") as log:  # supervisor start-up errors end up in the job log
        process = subprocess.Popen(
            [sys.executable, "-m", "newsletter_video.ui.jobwrap", str(job_dir), "--", *argv],
            env=env, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
            stderr=log, start_new_session=True, close_fds=True,
        )
    return process.pid


def pid_alive(pid: int | None) -> bool:
    if not pid or pid < 2:
        return False
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)  # reaps a finished child so it doesn't look alive
        if reaped == pid:
            return False
        return True
    except ChildProcessError:
        pass  # not our child (e.g. after a panel restart): fall through to a signal-0 probe
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class JobView:
    id: str
    kind: str
    state: str  # running | done | failed | cancelled | interrupted
    created_at: float
    finished_at: float | None
    exit_code: int | None
    meta: dict

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state, "created_at": self.created_at,
                "finished_at": self.finished_at, "exit_code": self.exit_code, "meta": self.meta}


class JobManager:
    def __init__(self, state_dir: Path, cwd: Path, spawner: Spawner = default_spawner,
                 redactor: Callable[[], Iterable[str]] | None = None):
        self.jobs_dir = Path(state_dir) / "jobs"
        self.cwd = cwd
        self.spawner = spawner
        self.redactor = redactor
        self._lock = threading.RLock()

    # --- reading ---

    def _read(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def get(self, job_id: str) -> JobView | None:
        directory = self.jobs_dir / job_id
        info = self._read(directory / "job.json")
        if not info:
            return None
        result = self._read(directory / "result.json")
        if result:
            if result.get("cancelled"):
                state = "cancelled"
            else:
                state = "done" if result.get("exit_code") == 0 else "failed"
            return JobView(job_id, info.get("kind", "?"), state, info.get("created_at", 0),
                           result.get("finished_at"), result.get("exit_code"), info.get("meta", {}))
        alive = pid_alive(info.get("wrapper_pid"))
        if not alive and (directory / "cancel").exists():
            state = "cancelled"
        else:
            state = "running" if alive else "interrupted"
        return JobView(job_id, info.get("kind", "?"), state, info.get("created_at", 0), None, None,
                       info.get("meta", {}))

    def list(self, kind: str | None = None, limit: int = 20) -> list[JobView]:
        if not self.jobs_dir.is_dir():
            return []
        names = sorted((p.name for p in self.jobs_dir.iterdir() if p.is_dir()), reverse=True)
        views = []
        for name in names:
            view = self.get(name)
            if view and (kind is None or view.kind == kind):
                views.append(view)
                if len(views) >= limit:
                    break
        return views

    def active(self, kinds: Iterable[str] | None = None) -> list[JobView]:
        wanted = set(kinds) if kinds is not None else None
        return [j for j in self.list(limit=KEEP_JOBS) if j.state == "running"
                and (wanted is None or j.kind in wanted)]

    def wrapper_pids(self) -> set[int]:
        pids = set()
        for view in self.active():
            info = self._read(self.jobs_dir / view.id / "job.json") or {}
            if info.get("wrapper_pid"):
                pids.add(int(info["wrapper_pid"]))
        return pids

    def log(self, job_id: str, since: int = 0) -> dict:
        """A chunk of the job's output from byte offset `since`, with secrets scrubbed."""
        path = self.jobs_dir / job_id / "log.txt"
        try:
            size = path.stat().st_size
        except OSError:
            return {"text": "", "next": 0}
        since = max(0, min(since, size))
        if size - since > LOG_CHUNK_BYTES:
            since = size - LOG_CHUNK_BYTES  # a runaway log: show the latest part
        with path.open("rb") as handle:
            handle.seek(since)
            raw = handle.read(LOG_CHUNK_BYTES)
        text = raw.decode("utf-8", "replace")
        return {"text": self.scrub(text), "next": since + len(raw)}

    def scrub(self, text: str) -> str:
        for secret in sorted(self.redactor() if self.redactor else (), key=len, reverse=True):
            if secret and secret in text:
                text = text.replace(secret, "[hidden]")
        text = re.sub(r"sk-ant-[A-Za-z0-9_\-]{8,}", "[hidden]", text)
        text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}", r"\1[hidden]", text)
        return text

    def tail(self, job_id: str, lines: int = 12) -> list[str]:
        try:
            size = (self.jobs_dir / job_id / "log.txt").stat().st_size
        except OSError:
            return []
        chunk = self.log(job_id, max(0, size - 16384))["text"].splitlines()
        return [line for line in chunk if line.strip()][-lines:]

    # --- starting and stopping ---

    @staticmethod
    def new_id() -> str:
        return f"{int(time.time()):x}{secrets.token_hex(3)}"

    def create(self, kind: str, argv: list[str], env: dict, meta: dict, job_id: str | None = None) -> JobView:
        with self._lock:
            job_id = job_id or self.new_id()
            directory = self.jobs_dir / job_id
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "log.txt").touch()
            created = time.time()
            self._write(directory / "job.json", {"id": job_id, "kind": kind, "created_at": created,
                                                 "meta": meta, "wrapper_pid": None})
            env = {**env, "PYTHONUNBUFFERED": "1"}
            pid = self.spawner(argv, env, directory, self.cwd)
            self._write(directory / "job.json", {"id": job_id, "kind": kind, "created_at": created,
                                                 "meta": meta, "wrapper_pid": pid})
            self.prune()
            return self.get(job_id)

    def cancel(self, job_id: str) -> bool:
        directory = self.jobs_dir / job_id
        info = self._read(directory / "job.json")
        view = self.get(job_id)
        if not info or not view or view.state != "running":
            return False
        pid = int(info.get("wrapper_pid") or 0)
        if pid < 2 or pid == os.getpid():
            return False
        (directory / "cancel").write_text("1")
        try:
            os.killpg(pid, signal.SIGTERM)  # the supervisor leads its own process group
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def prune(self) -> None:
        if not self.jobs_dir.is_dir():
            return
        names = sorted((p.name for p in self.jobs_dir.iterdir() if p.is_dir()), reverse=True)
        for name in names[KEEP_JOBS:]:
            view = self.get(name)
            if view and view.state != "running":
                for child in (self.jobs_dir / name).iterdir():
                    child.unlink(missing_ok=True)
                (self.jobs_dir / name).rmdir()

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)

    def write_meta(self, job_id: str, **updates) -> None:
        path = self.jobs_dir / job_id / "job.json"
        info = self._read(path) or {}
        info.setdefault("meta", {}).update(updates)
        self._write(path, info)
