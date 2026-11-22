"""The lineup: validation, batch -> slot expansion, one-off and fallback, and the daily run reading it."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from newsletter_video import lineup as lu
from newsletter_video import run_pipeline
from newsletter_video.runstate import record_video
from newsletter_video.scraper import Edition
from newsletter_video.youtube_upload import write_metadata
from tests.conftest import make_script, make_story

DAY = date(2026, 11, 10)
PAL = ["#ff7a1a", "#ff3d8a", "#22d3ee", "#8b5cff", "#2ee6a6"]


def doc(**over):
    base = {"order": "interleave", "batches": [
        {"count": 3, "style": "explainer", "preset": "paper", "accent": "#0e8a7d", "name": "Paper teal"},
        {"count": 4, "style": "explainer", "preset": "cinematic-glass", "accent_mode": "alternate", "palette": PAL[:3], "name": "Glass"},
        {"count": 3, "style": "avatar", "character": ""}]}
    base.update(over)
    return base


def test_validation_accepts_the_owners_example_and_normalises():
    out = lu.validate(doc())
    assert out["total"] == 10 and out["version"] == 1 and out["batches"][0]["accent"] == "#0e8a7d" and out["batches"][2]["style"] == "avatar"
    assert lu.validate({"batches": [{"count": 2, "accent": "#ABC"}]})["batches"][0]["accent"] == "#aabbcc"


@pytest.mark.parametrize("bad, fragment", [
    ({"batches": []}, "between 1 and"),
    ({"batches": [{"count": 0}]}, "count"),
    ({"batches": [{"count": 21}]}, "count"),
    ({"batches": [{"count": 2, "preset": "sepia"}]}, "preset"),
    ({"batches": [{"count": 2, "accent": "teal"}]}, "#rrggbb"),
    ({"batches": [{"count": 2, "bg": "#12"}]}, "#rrggbb"),
    ({"batches": [{"count": 2, "accent_mode": "alternate", "palette": ["#ff7a1a"]}]}, "palette"),
    ({"batches": [{"count": 2, "accent_mode": "random", "palette": ["#ff7a1a"] * 9}]}, "palette"),
    ({"batches": [{"count": 2}], "total": 5}, "must equal the sum"),
    ({"batches": [{"count": 11}, {"count": 11}]}, "between 1 and 20"),
    ({"batches": [{"count": 2}], "order": "random"}, "order"),
    ({"batches": [{"count": 2}], "slots": {"9": {"accent": "#ff7a1a"}}}, "outside"),
    ({"batches": [{"count": 2}], "slots": {"1": {"colour": "red"}}}, "unknown field"),
    ({"batches": [{"count": 2}], "version": 2}, "version"),
])
def test_validation_rejects_bad_input(bad, fragment):
    with pytest.raises(lu.LineupError) as exc:
        lu.validate(bad)
    assert fragment in str(exc.value)


def test_avatar_batches_must_name_an_existing_avatar_when_checked():
    ok = {"batches": [{"count": 2, "style": "avatar", "character": "gizmo"}]}
    assert lu.validate(ok, characters={"", "gizmo"})["batches"][0]["character"] == "gizmo"
    with pytest.raises(lu.LineupError, match="doesn't exist"):
        lu.validate(ok, characters={""})
    assert lu.validate(ok)  # the unattended run does not check: the renderer reports a missing avatar as today


def test_expansion_counts_interleaves_and_assigns_colours():
    slots = lu.expand(lu.validate(doc()), DAY)
    assert [s["slot"] for s in slots] == list(range(1, 11)) and len(slots) == 10
    assert [s["family"] for s in slots] == ["paper", "glass", "avatar", "paper", "glass", "avatar", "paper", "glass", "avatar", "glass"]
    glass = [s["look"]["accent"] for s in slots if s["family"] == "glass"]
    assert glass == [PAL[0], PAL[1], PAL[2], PAL[0]]  # alternates within its own batch
    assert {s["look"]["accent"] for s in slots if s["family"] == "paper"} == {"#0e8a7d"}
    assert all(s["look"]["style"] == "avatar" and "accent" not in s["look"] for s in slots if s["family"] == "avatar")
    seq = lu.expand(lu.validate(doc(order="sequential")), DAY)
    assert [s["family"] for s in seq] == ["paper"] * 3 + ["glass"] * 4 + ["avatar"] * 3


def test_random_colours_are_seeded_by_the_day_and_never_repeat_in_a_row():
    d = lu.validate({"batches": [{"count": 12, "preset": "cinematic-glass", "accent_mode": "random", "palette": PAL}]})
    a = [s["look"]["accent"] for s in lu.expand(d, DAY)]
    assert a == [s["look"]["accent"] for s in lu.expand(d, DAY)]  # a re-run or re-render keeps its colours
    assert a != [s["look"]["accent"] for s in lu.expand(d, DAY + timedelta(days=1))]
    assert all(x != y for x, y in zip(a, a[1:])) and set(a) <= set(PAL) and len(set(a)) > 2


def test_slot_override_replaces_just_that_slot():
    d = lu.validate(doc(slots={"2": {"preset": "paper", "accent": "#c0243c"}, "3": {"style": "explainer", "preset": "cinematic-glass"}}))
    slots = {s["slot"]: s for s in lu.expand(d, DAY)}
    assert slots[2]["family"] == "paper" and slots[2]["look"]["accent"] == "#c0243c" and slots[2]["overridden"]
    assert slots[3]["family"] == "glass" and "character" not in slots[3]["look"]
    assert not slots[1]["overridden"]


def test_a_one_off_applies_to_its_date_only_and_then_the_standing_lineup_is_back(tmp_path):
    path = tmp_path / "lineup.json"
    standing = doc()
    once = {"date": DAY.isoformat(), "order": "sequential", "batches": [{"count": 2, "preset": "cinematic-glass"}]}
    lu.save({**standing, "once": once}, path)
    core, which = lu.active(DAY, path)
    assert which == "once" and core["total"] == 2
    core, which = lu.active(DAY + timedelta(days=1), path)  # the next day: nothing to clean up
    assert which == "default" and core["total"] == 10
    assert lu.active(DAY - timedelta(days=1), path)[1] == "default"


def test_no_file_or_a_broken_file_means_no_lineup(tmp_path):
    assert lu.active(DAY, tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert lu.active(DAY, tmp_path / "bad.json") is None
    (tmp_path / "invalid.json").write_text(json.dumps({"batches": []}))
    assert lu.active(DAY, tmp_path / "invalid.json") is None
    d = lu.default_from_environ({"VIDEOS_PER_DAY": "4", "VIDEO_STYLE": "explainer", "LOOK_PRESET": "cinematic-glass", "LOOK_ACCENT_ROTATE": ",".join(PAL[:2])})
    assert d["total"] == 4 and d["batches"][0]["accent_mode"] == "alternate" and d["batches"][0]["palette"] == PAL[:2]


def test_scripts_are_written_once_per_format_and_carry_their_slot(monkeypatch):
    slots = lu.expand(lu.validate(doc()), DAY)
    calls = []

    def fake_build(editions, n_videos=None, exclude_titles=frozenset()):
        import os
        calls.append((os.environ.get("VIDEO_STYLE"), os.environ.get("LOOK_PRESET"), n_videos, set(exclude_titles)))
        return [make_script(f"S{len(calls)}-{i}") for i in range(n_videos)]

    scripts = lu.write_scripts(slots, DAY, 10, [], {"old"}, fake_build)
    assert [(c[0], c[1], c[2]) for c in calls] == [("explainer", "paper", 3), ("explainer", "cinematic-glass", 4), ("avatar", None, 3)]
    assert "old" in calls[1][3] and len(calls[2][3]) > len(calls[0][3])  # later groups skip the stories already picked
    assert [s.lineup["slot"] for s in scripts] == list(range(1, 11))
    assert scripts[0].look["preset"] == "paper" and scripts[1].look["preset"] == "cinematic-glass" and scripts[2].look["style"] == "avatar"
    assert scripts[1].lineup["format"] == "Dark glass" and scripts[1].lineup["accent"] == PAL[0] and scripts[1].lineup["batch"] == 2


def test_the_sidecar_and_upload_record_say_which_format_a_video_was(tmp_path):
    slots = lu.expand(lu.validate(doc()), DAY)
    script = make_script("Tracked")
    script.look = dict(slots[1]["look"])
    script.lineup = lu.lineup_info(slots[1], DAY, 10)
    mp4 = tmp_path / "02_tracked.mp4"
    mp4.write_bytes(b"x")
    meta = json.loads(write_metadata(script, mp4).read_text())
    assert meta["lineup"] == {"date": "2026-11-10", "slot": 2, "of": 10, "batch": 2, "batch_name": "Glass", "accent_mode": "alternate",
                              "format": "Dark glass", "style": "explainer", "preset": "cinematic-glass", "bg": "", "accent": PAL[0],
                              "character": "", "background": "", "overridden": False}
    assert meta["look"]["preset"] == "cinematic-glass" and meta["look"]["accent"] == PAL[0]
    from newsletter_video.youtube_upload import _format_record

    rec = _format_record(mp4)
    assert rec["format"] == "Dark glass" and rec["slot"] == 2 and rec["accent"] == PAL[0] and rec["batch"] == 2 and rec["date"] == "2026-11-10"
    assert lu.format_label(meta["lineup"]) == "Dark glass" and lu.format_label(None, {"style": "avatar", "character": "gizmo"}) == "Avatar: gizmo"


def test_the_daily_run_reads_the_lineup(monkeypatch, tmp_path, isolated_env):
    """The unattended run makes the lineup's slots in the lineup's formats; a re-run only fills the gaps."""
    path = tmp_path / "lineup.json"
    lu.save({"order": "interleave", "batches": [{"count": 2, "preset": "paper", "accent": "#0e8a7d"},
                                                {"count": 2, "preset": "cinematic-glass", "accent_mode": "alternate", "palette": PAL[:2]}]}, path)
    monkeypatch.setenv("BYTESIZE_LINEUP_FILE", str(path))
    monkeypatch.setenv("VIDEOS_PER_DAY", "3")  # the lineup's total (4) wins
    monkeypatch.setattr(run_pipeline, "_wait_for_network", lambda: True)
    monkeypatch.setattr(run_pipeline, "validate_config", lambda *a: None)
    monkeypatch.setattr(run_pipeline, "get_source", lambda: type("S", (), {"describe": lambda s: "fake", "fetch": lambda s, d: [Edition("tech", DAY.isoformat(), "TLDR", [make_story("A")])]})())
    seen = []

    def fake_build(editions, n_videos=None, exclude_titles=frozenset()):
        seen.append(n_videos)
        return [make_script(f"Story {len(seen)}-{i}") for i in range(n_videos)]

    monkeypatch.setattr(run_pipeline, "build_scripts", fake_build)
    built = []

    def fake_shorts(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, s in enumerate(scripts, start=1):
            p = out_dir / f"{s.lineup['slot']:02d}_{i}.mp4"
            p.write_bytes(b"x")
            on_rendered(s, p)
            record_video(out_dir, p.stem, "rendered")
            built.append((s.lineup["slot"], s.lineup["format"], s.look.get("accent")))
        return []

    monkeypatch.setattr(run_pipeline, "build_shorts", fake_shorts)
    assert run_pipeline.main([DAY.isoformat()]) == 0
    assert sorted(seen) == [2, 2]  # one writer call per format group
    assert built == [(1, "Clean paper", "#0e8a7d"), (2, "Dark glass", PAL[0]), (3, "Clean paper", "#0e8a7d"), (4, "Dark glass", PAL[1])]
    folder = isolated_env / DAY.isoformat()
    # a second run: nothing is missing, so nothing is made again
    seen.clear(); built.clear()
    run_pipeline.main([DAY.isoformat()])
    assert seen == [] and built == []
    # delete slot 2's sidecar: only that slot is made again, in its own format and colour
    next(folder.glob("02_*.json")).unlink()
    run_pipeline.main([DAY.isoformat()])
    assert built == [(2, "Dark glass", PAL[0])]



# ---- avatar background: Minecraft or regular ------------------------------------------------

def test_avatar_batches_choose_a_minecraft_or_regular_background():
    doc = lu.validate({"version": 1, "total": 5, "batches": [
        {"count": 2, "style": "avatar", "character": "puck", "background": "none"},
        {"count": 3, "style": "avatar", "character": "peter-griffin"}]})
    assert [b["background"] for b in doc["batches"]] == ["none", "gameplay"]  # unset means Minecraft, as before
    slots = lu.expand({k: doc[k] for k in ("total", "order", "batches", "slots")}, date(2026, 11, 10))
    assert [s["look"].get("background") for s in slots] == ["none", "gameplay", "none", "gameplay", "gameplay"]
    assert slots[0]["look"]["character"] == "puck"


def test_a_bad_background_is_refused_and_explainers_carry_none():
    with pytest.raises(lu.LineupError):
        lu.validate({"version": 1, "total": 1, "batches": [{"count": 1, "style": "avatar", "background": "space"}]})
    doc = lu.validate({"version": 1, "total": 1, "batches": [{"count": 1, "style": "explainer", "preset": "paper"}]})
    slot = lu.expand({k: doc[k] for k in ("total", "order", "batches", "slots")}, date(2026, 11, 10))[0]
    assert "background" not in slot["look"]


def test_a_slot_can_switch_its_avatar_background_and_the_label_says_so():
    doc = lu.validate({"version": 1, "total": 2, "batches": [{"count": 2, "style": "avatar", "character": "puck"}],
                           "slots": {"2": {"background": "none"}}})
    slots = lu.expand({k: doc[k] for k in ("total", "order", "batches", "slots")}, date(2026, 11, 10))
    assert [s["look"]["background"] for s in slots] == ["gameplay", "none"]
    info = lu.lineup_info(slots[1], date(2026, 11, 10), 2)
    assert info["background"] == "none"
    assert lu.format_label(info) == "Avatar: puck · Regular"
