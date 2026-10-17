"""Writer failure modes with the Anthropic client stubbed (never called for real)."""
from __future__ import annotations

import json
import types

import pytest

from newsletter_video import script_writer
from newsletter_video.scraper import Edition
from tests.conftest import make_story

STORIES = [make_story(f"Story number {i} about tech") for i in range(6)]
EDITIONS = [Edition("tech", "2026-10-14", "TLDR", STORIES)]


def _video(i, words=100):
    say = " ".join(["word"] * (words // 4))
    return {"title": f"Video {i}", "badge": "AI", "story_ids": [i], "hook": "A hook.",
            "beats": [{"say": say, "card": "Card", "highlight": "Card", "kind": "fact", "stat": ""} for _ in range(4)],
            "outro": "Comment below.", "post_caption": "cap", "hashtags": ["ai"]}


@pytest.fixture
def fake_claude(monkeypatch):
    """Installs a stand-in `anthropic` module whose stream returns `ctl['response']`
    or raises `ctl['error']`."""
    ctl = {"response": None, "error": None, "calls": 0}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("VIDEO_STYLE", "avatar")  # the classic writer; explainer tests cover the other path

    class APIError(Exception):
        pass

    class Stream:
        def __enter__(self):
            ctl["calls"] += 1
            if ctl["error"]:
                raise ctl["error"]
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return ctl["response"]

    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: Stream())))
    fake = types.SimpleNamespace(APIError=APIError, Anthropic=lambda: client)
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    ctl["APIError"] = APIError
    return ctl


def _response(text, stop_reason="end_turn"):
    return types.SimpleNamespace(stop_reason=stop_reason, content=[types.SimpleNamespace(type="text", text=text)],
                                 usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))


def test_claude_scripts_are_tagged(fake_claude):
    fake_claude["response"] = _response(json.dumps({"videos": [_video(0), _video(1)]}))
    scripts = script_writer.build_scripts(EDITIONS, n_videos=2)
    assert [s.writer for s in scripts] == ["claude", "claude"]


@pytest.mark.parametrize("failure", ["api_error", "refusal", "max_tokens", "bad_json", "wrong_shape"])
def test_writer_failures_fall_back_to_tagged_heuristic(fake_claude, failure):
    if failure == "api_error":
        fake_claude["error"] = fake_claude["APIError"]("overloaded")
    elif failure in ("refusal", "max_tokens"):
        fake_claude["response"] = _response("", stop_reason=failure)
    elif failure == "bad_json":
        fake_claude["response"] = _response("{not json")
    else:
        fake_claude["response"] = _response(json.dumps({"videos": "nope"}))
    scripts = script_writer.build_scripts(EDITIONS, n_videos=2)
    assert scripts and all(s.writer == "heuristic" for s in scripts)


def test_malformed_video_is_skipped_not_fatal(fake_claude):
    broken = _video(1)
    del broken["beats"][0]["card"]  # schema violation
    fake_claude["response"] = _response(json.dumps({"videos": [_video(0), broken]}))
    scripts = script_writer.build_scripts(EDITIONS, n_videos=2)
    assert [s.title for s in scripts] == ["Video 0"]


def test_overlong_or_empty_scripts_are_dropped(fake_claude):
    too_long = _video(1, words=400)
    empty_hook = _video(2)
    empty_hook["hook"] = "  "
    fake_claude["response"] = _response(json.dumps({"videos": [_video(0), too_long, empty_hook]}))
    scripts = script_writer.build_scripts(EDITIONS, n_videos=3)
    assert [s.title for s in scripts] == ["Video 0"]


def test_all_videos_invalid_falls_back(fake_claude):
    fake_claude["response"] = _response(json.dumps({"videos": [_video(0, words=400)]}))
    scripts = script_writer.build_scripts(EDITIONS, n_videos=1)
    assert scripts and scripts[0].writer == "heuristic"


def test_no_key_uses_heuristic_without_calling_anything(fake_claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    scripts = script_writer.build_scripts(EDITIONS, n_videos=1)
    assert fake_claude["calls"] == 0 and scripts[0].writer == "heuristic"
