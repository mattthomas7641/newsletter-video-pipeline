"""Voicebox failure modes against a fake local server: wedged (heartbeats
forever), a silent stream, bad/empty audio, the app not starting."""
from __future__ import annotations

import subprocess
import time

import pytest

from newsletter_video import tts
from newsletter_video.supervisor import FatalPipelineError
from tests.fake_voicebox import FakeVoicebox


@pytest.fixture
def voicebox(monkeypatch):
    server = FakeVoicebox().start()
    monkeypatch.setattr(tts, "VOICEBOX_BASE_URL", server.url)
    monkeypatch.setenv("TTS_PROVIDER", "voicebox")
    monkeypatch.setenv("VOICEBOX_PROFILE_ID", "test-profile")
    monkeypatch.setenv("VOICEBOX_GENERATION_TIMEOUT", "1")
    yield server
    server.stop()


def test_happy_path(voicebox, tmp_path):
    out = tts.synthesize("Hello there, this is a short test line.", tmp_path / "a.wav")
    assert out.exists() and out.stat().st_size > 1000
    assert voicebox.generations == 1


def test_wedged_generation_hits_wall_clock_deadline(voicebox, tmp_path, monkeypatch):
    """Heartbeats keep the socket alive, so only a wall-clock deadline ends
    the wait; before the fix this blocked the run forever."""
    voicebox.mode = "wedged"
    monkeypatch.setattr(tts, "_restart_voicebox", lambda: None)  # no retry rescue here
    started = time.monotonic()
    with pytest.raises(tts.VoiceboxWedged):
        tts._voicebox_generate("Some words to say here.", tmp_path / "w.wav")
    assert time.monotonic() - started < 5


def test_wedge_triggers_restart_and_retry_once(voicebox, tmp_path, monkeypatch):
    voicebox.mode = "wedged"
    restarts = []

    def fake_restart():
        restarts.append(1)
        voicebox.mode = "normal"  # the relaunch fixed it

    monkeypatch.setattr(tts, "_restart_voicebox", fake_restart)
    out = tts.synthesize("Hello there, this is a short test line.", tmp_path / "r.wav")
    assert out.exists() and restarts == [1]
    assert voicebox.generations == 2


def test_wedge_persisting_after_restart_fails_this_video(voicebox, tmp_path, monkeypatch):
    voicebox.mode = "wedged"
    restarts = []
    monkeypatch.setattr(tts, "_restart_voicebox", lambda: restarts.append(1))
    with pytest.raises(tts.VoiceboxWedged):
        tts.synthesize("Hello there.", tmp_path / "r.wav")
    assert restarts == [1]  # retried exactly once, then gave up


def test_silent_stream_hits_read_timeout(voicebox, tmp_path, monkeypatch):
    voicebox.mode = "silent_stream"
    monkeypatch.setattr(tts, "VOICEBOX_TIMEOUT_SECONDS", 1)
    monkeypatch.setenv("VOICEBOX_GENERATION_TIMEOUT", "30")
    monkeypatch.setattr(tts, "_restart_voicebox", lambda: None)
    started = time.monotonic()
    with pytest.raises(tts.VoiceboxWedged):
        tts._voicebox_generate("Some words.", tmp_path / "s.wav")
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("mode", ["bad_audio", "empty_audio"])
def test_broken_clip_is_rejected(voicebox, tmp_path, monkeypatch, mode):
    voicebox.mode = mode
    monkeypatch.setattr(tts, "_restart_voicebox", lambda: None)
    with pytest.raises(tts.VoiceboxWedged):
        tts._voicebox_generate("Some words to say.", tmp_path / "b.wav")


def test_app_not_installed_is_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "VOICEBOX_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr(tts, "VOICEBOX_STARTUP_TIMEOUT_SECONDS", 1)

    def failing_open(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(tts.subprocess, "run", failing_open)
    with pytest.raises(FatalPipelineError, match="could not be launched"):
        tts._ensure_voicebox_running()


def test_app_never_comes_up_is_fatal(monkeypatch):
    monkeypatch.setattr(tts, "VOICEBOX_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(tts, "VOICEBOX_STARTUP_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(tts.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(FatalPipelineError, match="didn't come up"):
        tts._ensure_voicebox_running()


def test_restart_quits_relaunches_and_waits(monkeypatch):
    calls = []
    # up before quit; down after it (twice checked); still down when relaunching; then back up
    health = iter([True, False, False, False])

    monkeypatch.setattr(tts.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(tts, "_voicebox_is_up", lambda: next(health, True))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    tts._restart_voicebox()
    assert calls[0][:2] == ["osascript", "-e"] and "quit" in calls[0][2]
    assert ["open", "-a", "Voicebox"] in calls


def test_check_audio_rejects_truncated_clip(tmp_path):
    from tests.conftest import make_wav

    make_wav(tmp_path / "short.wav", seconds=1.5)
    with pytest.raises(RuntimeError, match="truncated"):
        tts.check_audio(tmp_path / "short.wav", " ".join(["word"] * 100))
    make_wav(tmp_path / "ok.wav", seconds=20)
    tts.check_audio(tmp_path / "ok.wav", " ".join(["word"] * 100))
