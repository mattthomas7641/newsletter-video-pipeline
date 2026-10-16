"""Guards for the unattended run: a lockfile against overlapping runs, a
wall-clock watchdog so nothing can hang forever, a subprocess runner with
a deadline that also kills the child's own children (headless Chrome under
the HyperFrames renderer), and fail-fast config validation.

Nothing here knows about videos; run_pipeline wires it in.
"""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("newsletter_video")

EXIT_LOCKED = 75  # another run holds the lock (EX_TEMPFAIL)
EXIT_CONFIG = 78  # bad configuration (EX_CONFIG)
EXIT_WATCHDOG = 70  # the run-level deadline fired (EX_SOFTWARE)

DEFAULT_RUN_DEADLINE_MINUTES = 240  # a normal 10-video day takes ~100 min
MIN_FREE_DISK_GB = 3.0  # a day of renders plus voice work is ~0.5 GB; Chrome needs headroom too


class ConfigError(RuntimeError):
    """One readable message listing everything wrong with the configuration."""


class FatalPipelineError(RuntimeError):
    """A failure no later video can get past either (the TTS app cannot
    start, npx is missing): the day stops now instead of failing each video
    in turn, which with a 90 s startup wait per video would burn 15 minutes."""


class AlreadyRunning(RuntimeError):
    def __init__(self, holder: str):
        super().__init__(f"another pipeline run is still active ({holder})")
        self.holder = holder


# --- lockfile ------------------------------------------------------------------

class RunLock:
    """`fcntl.flock` on a lockfile. The kernel releases the lock the moment
    the holding process dies (crash, kill -9, reboot), so a lock that is
    held always means a live process: there is no stale-lock cleanup to do,
    and a leftover file from a dead run is harmless. The file's contents
    (pid, start time) only serve the "who is holding it" message."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            holder = fh.read().strip() or "unknown pid"
            fh.close()
            raise AlreadyRunning(holder)
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid {os.getpid()}, started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
        finally:
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


# --- process deadlines -----------------------------------------------------------

def _descendants(pid: int) -> list[int]:
    """All transitive children of `pid` (via pgrep -P, available on macOS and Linux)."""
    found: list[int] = []
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        try:
            out = subprocess.run(["pgrep", "-P", str(parent)], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        kids = [int(p) for p in out.split() if p.isdigit()]
        found.extend(kids)
        frontier.extend(kids)
    return found


def kill_process_tree(pid: int, sig: int = signal.SIGKILL) -> None:
    """Kills `pid`'s descendants (deepest first) and then `pid` itself."""
    for child in reversed(_descendants(pid)):
        try:
            os.kill(child, sig)
        except OSError:
            pass
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def run_subprocess(cmd: list[str], timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run(check=True)` with a wall-clock deadline. On timeout
    the whole process tree is killed (a bare `subprocess.run(timeout=)`
    only kills the direct child, leaving e.g. the renderer's headless
    Chrome alive) and TimeoutExpired is raised."""
    kwargs.setdefault("check", True)
    kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **{k: v for k, v in kwargs.items() if k != "check"})
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # anything that re-parented itself
        except OSError:
            pass
        proc.wait()
        raise
    if kwargs["check"] and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


class Watchdog:
    """A daemon thread that ends the process once `deadline_seconds` of
    wall-clock time have passed. `on_expire` (e.g. a notification) runs
    first; then every child process is killed and the interpreter exits
    with EXIT_WATCHDOG. Per-video state is written to disk as the run
    goes, so nothing useful is lost by exiting hard — and a hung run no
    longer blocks tomorrow's.

    Uses time.monotonic(), which on macOS does not advance while the
    machine sleeps: a laptop closed mid-run resumes with the same budget
    left, instead of being killed the moment it wakes."""

    def __init__(self, deadline_seconds: float, on_expire=None, exit_fn=os._exit):
        self.deadline_seconds = deadline_seconds
        self.on_expire = on_expire
        self.exit_fn = exit_fn
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="run-watchdog", daemon=True)
        self.fired = False

    def start(self) -> "Watchdog":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        if self._stop.wait(self.deadline_seconds):
            return
        self.fired = True
        log.critical("Watchdog: run exceeded %.0f minutes — aborting", self.deadline_seconds / 60)
        try:
            if self.on_expire:
                self.on_expire()
        except Exception:
            log.exception("Watchdog on_expire failed")
        for child in reversed(_descendants(os.getpid())):
            try:
                os.kill(child, signal.SIGKILL)
            except OSError:
                pass
        logging.shutdown()
        self.exit_fn(EXIT_WATCHDOG)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.cancel()


# --- configuration -----------------------------------------------------------

TTS_PROVIDERS = {"kokoro", "voicebox", "elevenlabs", "say"}
PRIVACY_VALUES = {"private", "unlisted", "public"}
FALLBACK_UPLOAD_VALUES = {"skip", "private", "public"}


def _positive_int(name: str, default: int, problems: list[str]) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{name}={raw!r} is not a whole number")
        return default
    if value <= 0:
        problems.append(f"{name}={value} must be at least 1")
    return value


def validate_config(project_root: Path, output_dir: Path, token_path: Path) -> None:
    """Checks the configuration a run depends on, up front, and raises one
    ConfigError listing every problem — so a bad .env fails in the first
    second with a readable message, not 40 minutes in with a KeyError.
    Only the presence of secrets is checked; their values are never read
    out or logged."""
    problems: list[str] = []
    _positive_int("VIDEOS_PER_DAY", 3, problems)
    _positive_int("RUN_DEADLINE_MINUTES", DEFAULT_RUN_DEADLINE_MINUTES, problems)

    provider = os.environ.get("TTS_PROVIDER", "kokoro").strip()
    if provider not in TTS_PROVIDERS:
        problems.append(f"TTS_PROVIDER={provider!r} must be one of {sorted(TTS_PROVIDERS)}")
    if provider == "voicebox" and not os.environ.get("VOICEBOX_PROFILE_ID", "").strip():
        problems.append("TTS_PROVIDER=voicebox needs VOICEBOX_PROFILE_ID")
    if provider == "elevenlabs":
        for key in ("ELEVENLABS_API_KEY", "VOICE_ID"):
            if not os.environ.get(key, "").strip():
                problems.append(f"TTS_PROVIDER=elevenlabs needs {key}")

    upload = os.environ.get("YOUTUBE_UPLOAD", "0").strip() == "1"
    privacy = os.environ.get("YOUTUBE_PRIVACY", "private").strip()
    if privacy not in PRIVACY_VALUES:
        problems.append(f"YOUTUBE_PRIVACY={privacy!r} must be one of {sorted(PRIVACY_VALUES)}")
    fallback = os.environ.get("FALLBACK_UPLOAD", "skip").strip().lower()
    if fallback not in FALLBACK_UPLOAD_VALUES:
        problems.append(f"FALLBACK_UPLOAD={fallback!r} must be one of {sorted(FALLBACK_UPLOAD_VALUES)}")
    if upload and not token_path.exists():
        problems.append(f"YOUTUBE_UPLOAD=1 but {token_path.name} is missing — run `python -m "
                        f"newsletter_video.youtube_upload --auth` (or set YOUTUBE_UPLOAD=0)")

    try:
        from .tts import npx_path
        npx_path()
    except FileNotFoundError as exc:
        problems.append(str(exc))
    if shutil.which("ffmpeg") is None and not Path(sys.executable).with_name("ffmpeg").exists():
        log.warning("No `ffmpeg` on PATH: word timing will fall back to estimates (mlx-whisper needs it)")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(output_dir).free / 1e9
        if free_gb < MIN_FREE_DISK_GB:
            problems.append(f"only {free_gb:.1f} GB free on the output disk (need {MIN_FREE_DISK_GB:.0f} GB)")
    except OSError as exc:
        problems.append(f"cannot use output folder {output_dir}: {exc}")

    if problems:
        raise ConfigError("Configuration problems:\n  - " + "\n  - ".join(problems))
