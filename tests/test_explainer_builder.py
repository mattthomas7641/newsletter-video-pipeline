"""Explainer timing tests: scene starts, reveal cues and captions from word
times (pytest, no network, no media)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newsletter_video.explainer_builder import caption_chunks, cue_times, story_payload, video_style  # noqa: E402
from newsletter_video.scene_plan import validate_plan  # noqa: E402

LINE = [("Which", 10.0), ("model", 10.3), ("you", 10.5), ("run,", 10.7), ("how", 11.0), ("hard", 11.2),
        ("the", 11.3), ("task", 11.5), ("is,", 11.7), ("and", 12.0), ("budget.", 12.4)]


def test_cues_land_on_spoken_words_in_order():
    cues = cue_times(["Which model you run", "How hard the task is", "How much budget you have"], LINE, 9.8, 14.0)
    assert cues == [10.15, 11.2, 12.4]  # "model" is said at 10.3 but the floor is start + 0.35


def test_unnamed_items_are_spread_evenly_and_never_go_backwards():
    cues = cue_times(["alpha", "beta", "gamma"], LINE, 9.8, 14.0)
    assert cues[0] == pytest.approx(10.15)
    assert cues == sorted(cues)
    assert cues[-1] <= 14.0 - 0.3
    mixed = cue_times(["alpha", "budget", "zeta"], LINE, 9.8, 14.0)
    # "budget" is said at 12.4 but a 4.2 s scene caps the second cue earlier (no late holes)
    assert mixed[1] <= 12.4 and mixed[0] < mixed[1] <= mixed[2]


def test_cues_match_inflected_words():
    line = [("Senior", 1.0), ("jobs", 1.3), ("supplied", 1.6), ("most", 1.9), ("of", 2.0), ("it.", 2.2)]
    assert cue_times(["Seniors"], line, 0.0, 4.0) == [1.0]
    assert cue_times(["Juniors"], line, 0.0, 4.0) == [0.35]  # not said: lands at the floor


def test_caption_chunks_break_on_phrases_not_dangling_words():
    chunks = caption_chunks(LINE)
    assert [" ".join(w for w, _ in c) for c in chunks] == ["Which model you run,", "how hard the task is,", "and budget."]
    text = "Teams cost between one point eight and five point one times as much. Yet there was no gain."
    chunks = caption_chunks([(w, i * 0.3) for i, w in enumerate(text.split())])
    joined = [" ".join(w for w, _ in c) for c in chunks]
    assert "one point eight and five point one times" in joined  # a spoken number is never split
    assert not any(c[0][0].lower() in {"and", "to", "of", "the"} for c in chunks[1:] if c[0][0].lower() != "and")
    assert all(len(c) >= 2 for c in chunks)


def test_story_payload_times_scenes_from_word_times():
    plan = validate_plan([
        {"type": "title", "say": "Agent teams cost up to five times more. Do they actually win?",
         "kicker": "AI AGENTS", "headline": "Agent teams cost 5x more. Worth it?"},
        {"type": "stat", "say": "Teams cost up to five point one times as much as a single agent.",
         "value": "5.1x", "label": "Top cost multiplier for teams"},
        {"type": "statement", "say": "Whether a team pays off depends on the model, the task, and the budget.",
         "text": "It depends on model, task and budget", "emphasis": "depends"},
        {"type": "list", "say": "Three things decide it: the model, the task, and the budget you have.",
         "items": ["The model", "The task", "The budget"]},
        {"type": "outro", "say": "Do you use multiple agents? Tell us if it's worth it.", "text": "Worth the bill?"},
    ])

    def line(text, start, step=0.3):
        return [(w, round(start + i * step, 3)) for i, w in enumerate(text.split())]

    per_line = [line(s.say, t) for s, t in zip(plan.scenes, [0.1, 4.0, 9.0, 14.0, 19.0])]
    payload = story_payload(plan, per_line, speech_duration=23.0, kicker="TLDR · OCT 13", tail=0.6, theme="light")

    assert payload["duration"] == 23.6 and payload["kicker"] == "TLDR · OCT 13" and payload["theme"] == "light"
    scenes = payload["scenes"]
    assert [s["type"] for s in scenes] == ["title", "stat", "statement", "list", "outro"]
    assert scenes[0]["start"] == 0.0
    assert scenes[1]["start"] == pytest.approx(3.8)  # 0.2 s lead before the first word
    assert all(a["end"] == b["start"] for a, b in zip(scenes, scenes[1:]))
    assert scenes[-1]["end"] == 23.6
    assert scenes[1]["countAt"] == pytest.approx(4.8)  # "five" is said at 5.2 but the count never waits past start + 1 s
    assert scenes[2]["emphasisAt"] == pytest.approx(10.5)  # "depends" is the 6th word of its line
    assert scenes[3]["cues"] == sorted(scenes[3]["cues"]) and len(scenes[3]["cues"]) == 3
    assert scenes[3]["cues"][0] >= scenes[3]["start"] + 0.35
    captions = payload["captions"]
    assert captions[0]["start"] == 0.1 and captions[-1]["end"] == 23.0
    assert all(c["end"] >= c["start"] for c in captions)


def test_video_style_is_validated(monkeypatch):
    monkeypatch.delenv("VIDEO_STYLE", raising=False)
    assert video_style() == "explainer"
    monkeypatch.setenv("VIDEO_STYLE", " Explainer ")
    assert video_style() == "explainer"
    monkeypatch.setenv("VIDEO_STYLE", "cartoon")
    with pytest.raises(ValueError):
        video_style()
