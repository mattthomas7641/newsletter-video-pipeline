"""A second channel made from the first channel's finished videos (mirror_channel.py): no Claude calls."""
from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from newsletter_video import avx_writer as w
from newsletter_video import mirror_channel as mc
from newsletter_video import plan_manifest as pm
from tests.avx_fixtures import STORY, VIDEO

DAY = date(2026, 11, 11)


def sidecar(title: str, story: str | None = None) -> dict:
    scenes = w.check_video(copy.deepcopy(VIDEO), [STORY])[0]
    return {"title": title, "caption": "A caption?", "stories": [story or title], "writer": "claude",
            "script": {"title": title, "badge": "NEWS", "hook": scenes[0]["say"],
                       "beats": [{"say": s["say"], "card": "", "highlight": ""} for s in scenes[1:-1]],
                       "outro": scenes[-1]["say"]},
            "scene_plan": w.plan_dict(scenes), "look": {"style": "avx", "character": "puck"}}


def write_day(root, metas):
    folder = root / DAY.isoformat()
    folder.mkdir(parents=True)
    for i, m in enumerate(metas, 1):
        (folder / f"{i:02d}_x.json").write_text(json.dumps(m))
    for name in ("status.json", "uploads.json", "plan.json"):  # never mistaken for videos
        (folder / name).write_text("{}")
    return root


def test_the_source_folder_yields_only_finished_avatar_videos(tmp_path):
    other = sidecar("Dark glass video")
    other["scene_plan"] = {"style": "explainer", "scenes": []}  # a different format: no avx scene list
    root = write_day(tmp_path / "src", [sidecar("A"), other, sidecar("B")])
    metas = [m for m in mc.fetch_sidecars(DAY, str(root), tmp_path / "t") if mc.usable(m)]
    assert [m["title"] for m in metas] == ["A", "B"]


def test_the_best_scored_videos_are_chosen_best_first_and_a_tie_keeps_source_order():
    metas = [sidecar(t) for t in ("A", "B", "C", "D")]
    chosen = mc.choose(metas, 2, scorer=lambda ms: [1.0, 2.5, 2.5, 0.2])
    assert [(m["title"], s) for m, s in chosen] == [("B", 2.5), ("C", 2.5)]


def test_without_jev_the_sources_own_order_is_kept():
    metas = [sidecar(t) for t in ("A", "B", "C")]
    assert [(m["title"], s) for m, s in mc.choose(metas, 2, scorer=lambda ms: None)] == [("A", None), ("B", None)]


def test_fewer_than_the_count_is_fine():
    assert len(mc.choose([sidecar("A")], 5, scorer=lambda ms: [1.0])) == 1


def test_stories_this_channel_already_made_are_skipped_before_choosing():
    metas = [sidecar("Meta buys Conductor"), sidecar("Nvidia opens an investigation into its CEO")]
    chosen = mc.choose(metas, 5, covered_titles=["Nvidia opens an investigation into its CEO"], scorer=lambda ms: [1.0] * len(ms))
    assert [m["title"] for m, _ in chosen] == ["Meta buys Conductor"]


def test_the_plan_carries_the_other_narrators_look_and_the_stored_scenes_so_no_writer_call_is_needed(tmp_path):
    chosen = [(sidecar("A"), 1.0), (sidecar("B"), 0.5)]
    path = mc.build_plan(DAY, chosen, {"style": "avx", "character": "peter-griffin"}, tmp_path / "out")
    data = json.loads(path.read_text())
    assert [v["title"] for v in data["videos"]] == ["A", "B"]
    for v in data["videos"]:
        assert v["look"]["character"] == "peter-griffin" and v["look"]["style"] == "avx"  # not the source's puck
        assert v["scene_plan"]["style"] == "avx" and len(v["scene_plan"]["scenes"]) >= 5
        assert v["status"] == "planned"
    scripts = [pm.script_from_entry(v) for v in data["videos"]]
    assert all(getattr(s, "avx_scenes", None) for s in scripts)  # renderable as they are


def test_channel_look_needs_a_character(monkeypatch):
    monkeypatch.delenv("CHARACTER", raising=False)
    with pytest.raises(SystemExit):
        mc.channel_look()
    monkeypatch.setenv("CHARACTER", "peter-griffin")
    assert mc.channel_look() == {"style": "avx", "character": "peter-griffin"}


def test_jev_scores_are_none_without_a_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert mc.jev_scores([sidecar("A")]) is None
