"""Lockfile, run-level watchdog, subprocess deadline, config validation."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from newsletter_video import supervisor
from newsletter_video.supervisor import (
    EXIT_WATCHDOG, AlreadyRunning, ConfigError, RunLock, run_subprocess, validate_config,
)

ROOT = Path(__file__).resolve().parent.parent


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid: int, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        # a killed child we never waited for shows up as a zombie; reap if ours
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return True
        except ChildProcessError:
            pass
        time.sleep(0.05)
    return not _alive(pid)


# --- lockfile ----------------------------------------------------------------

def test_second_run_is_refused_while_first_holds_lock(tmp_path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        with pytest.raises(AlreadyRunning) as info:
            RunLock(path).acquire()
        assert f"pid {os.getpid()}" in info.value.holder
    RunLock(path).acquire().release()  # free again after release


def test_lock_held_by_another_process_is_seen_and_freed_on_its_death(tmp_path):
    path = tmp_path / "run.lock"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time; sys.path.insert(0, {str(ROOT)!r})
        from newsletter_video.supervisor import RunLock
        lock = RunLock({str(path)!r}).acquire(); print("locked", flush=True); time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            RunLock(path).acquire()
        holder.kill()
        holder.wait()
        RunLock(path).acquire().release()  # a dead holder leaves nothing stale
    finally:
        if holder.poll() is None:
            holder.kill()


# --- watchdog ----------------------------------------------------------------

def test_watchdog_kills_hung_run_and_its_children(tmp_path):
    marker = tmp_path / "expired"
    pidfile = tmp_path / "child.pid"
    script = textwrap.dedent(f"""
        import subprocess, sys, time; sys.path.insert(0, {str(ROOT)!r})
        from newsletter_video.supervisor import Watchdog
        child = subprocess.Popen(["sleep", "60"])
        open({str(pidfile)!r}, "w").write(str(child.pid))
        Watchdog(0.5, on_expire=lambda: open({str(marker)!r}, "w").write("x")).start()
        time.sleep(60)  # the "hang"
    """)
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script], timeout=20)
    assert proc.returncode == EXIT_WATCHDOG
    assert time.monotonic() - started < 10
    assert marker.exists()
    assert _wait_dead(int(pidfile.read_text()))


def test_watchdog_cancel_prevents_firing():
    fired = []
    wd = supervisor.Watchdog(0.2, on_expire=lambda: fired.append(1), exit_fn=lambda code: fired.append(code)).start()
    wd.cancel()
    time.sleep(0.4)
    assert fired == [] and not wd.fired


# --- subprocess deadline ------------------------------------------------------

def test_run_subprocess_timeout_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    cmd = ["sh", "-c", f"sleep 60 & echo $! > '{pidfile}'; wait"]
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess(cmd, timeout=1)
    assert _wait_dead(int(pidfile.read_text()))


def test_run_subprocess_raises_on_nonzero_exit():
    with pytest.raises(subprocess.CalledProcessError):
        run_subprocess(["sh", "-c", "exit 3"], timeout=5)
    assert run_subprocess(["true"], timeout=5).returncode == 0


# --- config validation ----------------------------------------------------------

def test_validate_config_collects_every_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOS_PER_DAY", "zero")
    monkeypatch.setenv("TTS_PROVIDER", "voicebox")  # without VOICEBOX_PROFILE_ID
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")  # without a token file
    monkeypatch.setenv("YOUTUBE_PRIVACY", "everyone")
    monkeypatch.setenv("FALLBACK_UPLOAD", "yolo")
    with pytest.raises(ConfigError) as info:
        validate_config(tmp_path, tmp_path / "out", tmp_path / "secrets" / "youtube_token.json")
    message = str(info.value)
    for fragment in ("VIDEOS_PER_DAY", "VOICEBOX_PROFILE_ID", "youtube_token.json", "YOUTUBE_PRIVACY", "FALLBACK_UPLOAD"):
        assert fragment in message, message
    assert message.count("\n  - ") == 5


def test_validate_config_passes_for_sane_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "MIN_FREE_DISK_GB", 0.0)
    validate_config(tmp_path, tmp_path / "out", tmp_path / "token.json")


def test_validate_config_fails_on_low_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "MIN_FREE_DISK_GB", 10 ** 9)
    with pytest.raises(ConfigError, match="free on the output disk"):
        validate_config(tmp_path, tmp_path / "out", tmp_path / "token.json")
