"""Shared fixtures. Every test runs offline: no real API, no YouTube, no
renders — output goes to pytest's tmp_path, never the real output/."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newsletter_video import notify as notify_mod  # noqa: E402
from newsletter_video import run_pipeline, status, youtube_upload  # noqa: E402
from newsletter_video.scraper import Story  # noqa: E402
from newsletter_video.script_writer import Beat, Script  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """No keys, no uploads, no desktop notifications, output under tmp_path."""
    for key in ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "NOTIFY_WEBHOOK_URL", "YOUTUBE_SCHEDULE",
                "VOICEBOX_PROFILE_ID", "ELEVENLABS_API_KEY", "VOICE_ID", "RUN_DEADLINE_MINUTES",
                "FALLBACK_UPLOAD", "RENDER_TIMEOUT_SECONDS", "VOICEBOX_GENERATION_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOTIFY_MACOS", "0")
    monkeypatch.setenv("YOUTUBE_UPLOAD", "0")
    monkeypatch.setenv("YOUTUBE_PRIVACY", "public")
    monkeypatch.setenv("TTS_PROVIDER", "say")
    monkeypatch.setenv("VIDEOS_PER_DAY", "3")
    monkeypatch.setenv("BACKGROUND_STYLE", "none")
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(run_pipeline, "OUTPUT_DIR", out)
    monkeypatch.setattr(run_pipeline, "LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)  # so .env is never read
    monkeypatch.setattr(youtube_upload, "OUTPUT_DIR", out)
    monkeypatch.setattr(youtube_upload, "TOKEN", tmp_path / "secrets" / "youtube_token.json")
    monkeypatch.setattr(status, "OUTPUT_DIR", out)
    yield out


@pytest.fixture
def notifications(monkeypatch):
    """Captures every notification as (title, message, ok)."""
    sent: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(notify_mod, "notify", lambda title, message, ok=False: sent.append((title, message, ok)))
    monkeypatch.setattr(run_pipeline, "notify", notify_mod.notify)
    return sent


def make_wav(path: Path, seconds: float = 2.0, silent: bool = False, rate: int = 16000) -> Path:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    samples = np.zeros_like(t) if silent else 0.5 * np.sin(2 * np.pi * 220 * t)
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(path), samples.astype("float32"), rate)
    return path


def make_story(title: str, edition: str = "tech", section: str = "Big Tech & Startups") -> Story:
    return Story(edition=edition, section=section, title=title,
                 summary=f"{title}. It matters because reasons. Also more detail.", url=f"https://example.com/{len(title)}")


def make_script(title: str, writer: str = "claude", words: int = 100) -> Script:
    say = " ".join(["word"] * (words // 5))
    return Script(title=title, badge="TECH", hook="Here is a hook for you.",
                  beats=[Beat(say=say, card="Card text", highlight="Card") for _ in range(4)],
                  outro="Comment below.", stories=[make_story(title)], post_caption="caption", hashtags=["ai"],
                  writer=writer)
