"""The avatar explainer (avx) as a pipeline format: look and lineup plumbing, the writer and its validator,
the rule-based fallback, plan/sidecar round trips, the render dispatch and the panel's lineup API.
No TTS, no render, no network: the builder and Claude are stubbed."""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from newsletter_video import avx_builder as avx
from newsletter_video import avx_writer as w
from newsletter_video import lineup as lu
from newsletter_video import look as looks
from newsletter_video import plan_manifest as pm
from newsletter_video.scraper import Edition
from newsletter_video.youtube_upload import write_metadata
from tests.avx_fixtures import SCENES, STORY, VIDEO, video
from tests.conftest import make_script, make_story

DAY = date(2026, 11, 10)


# ---- look ----------------------------------------------------------------------------------

def test_avx_is_a_look_style_with_its_own_description():
    clean = looks.validate_look({"style": "AVX", "character": "puck", "bg": "#F3EFE6", "accent": ""})
    assert clean["style"] == "avx" and clean["bg"] == "#f3efe6" and clean["accent"] == ""
    resolved = looks.resolve_look({"style": "avx", "bg": "", "accent": "#ff4d1f"}, environ={"CHARACTER": "puck"})
    assert resolved["style"] == "avx" and resolved["character"] == "puck" and resolved["bg"] == ""
    assert "avatar explainer" in looks.describe(resolved) and "house rhythm" in looks.describe(resolved)
    assert not looks.cinematic_requested(resolved) and looks.world_of(resolved) == ""
    with pytest.raises(looks.LookError):
        looks.validate_look({"style": "avx2"})


def test_the_default_look_and_rotation_ignore_avx():
    env = {"LOOK_ACCENT_ROTATE": "#ff7a1a,#22d3ee", "LOOK_PRESET": "cinematic-glass"}
    assert looks.with_rotation({"style": "avx", "bg": "", "accent": ""}, 1, environ=env) == {"style": "avx", "bg": "", "accent": ""}
    assert looks.look_from_env({})["style"] == "explainer"  # nothing set: the daily default is unchanged


# ---- lineup --------------------------------------------------------------------------------

def avx_doc(**over):
    base = {"order": "interleave", "batches": [
        {"count": 2, "style": "avx", "character": "puck", "bg": "#F3EFE6", "accent": "#ff4d1f", "name": "AVX paper"},
        {"count": 3, "style": "avx", "character": "", "bg": "", "accent_mode": "alternate", "palette": ["#ff4d1f", "#5ef2b0"]},
        {"count": 1, "style": "explainer", "preset": "paper"}]}
    base.update(over)
    return base


def test_avx_batches_validate_and_keep_their_fields():
    out = lu.validate(avx_doc(), characters={"", "puck"})
    b0, b1 = out["batches"][:2]
    assert b0["style"] == "avx" and b0["bg"] == "#f3efe6" and b0["accent"] == "#ff4d1f" and b0["character"] == "puck"
    assert b0["preset"] == "" and b0["background"] == ""
    assert b1["accent_mode"] == "alternate" and b1["palette"] == ["#ff4d1f", "#5ef2b0"] and b1["bg"] == ""


@pytest.mark.parametrize("batch, fragment", [
    ({"count": 2, "style": "avx", "bg": "blue"}, "#rrggbb"),
    ({"count": 2, "style": "avx", "accent_mode": "random", "palette": ["#ff4d1f"]}, "palette"),
    ({"count": 2, "style": "avx", "character": "Not A Slug"}, "avatar name"),
    ({"count": 2, "style": "avx", "character": "ghost"}, "doesn't exist"),
])
def test_bad_avx_batches_are_refused(batch, fragment):
    with pytest.raises(lu.LineupError, match=fragment):
        lu.validate({"batches": [batch]}, characters={"", "puck"})


def test_avx_expansion_one_ground_house_rhythm_and_rotation():
    slots = lu.expand(lu.validate(avx_doc()), DAY)
    assert [s["family"] for s in slots] == ["avx", "avx", "paper", "avx", "avx", "avx"]
    assert slots[0]["look"] == {"style": "avx", "bg": "#f3efe6", "accent": "#ff4d1f", "character": "puck"}
    assert slots[0]["label"] == "Avatar explainer"
    # blank character: left out, so the run's CHARACTER narrates; blank bg = the house rhythm; alternating accents
    assert slots[1]["look"] == {"style": "avx", "bg": "", "accent": "#ff4d1f"}
    assert [s["look"]["accent"] for s in slots if s["batch"] == 2] == ["#ff4d1f", "#5ef2b0", "#ff4d1f"]
    info = lu.lineup_info(slots[0], DAY, 6)
    assert info["format"] == "Avatar explainer" and info["style"] == "avx" and info["bg"] == "#f3efe6" and info["character"] == "puck"
    assert lu.format_label(info) == "Avatar explainer: puck"
    assert lu.format_label(None, {"style": "avx", "character": "", "background": "gameplay"}) == "Avatar explainer"


def test_slots_can_switch_to_and_from_avx():
    doc = avx_doc(slots={"3": {"style": "avx", "bg": "#0d0f14", "character": "puck"},
                         "1": {"style": "explainer", "preset": "paper", "accent": "#0e8a7d"}})
    slots = lu.expand(lu.validate(doc), DAY)
    assert slots[2]["look"] == {"style": "avx", "bg": "#0d0f14", "accent": "", "character": "puck"} and slots[2]["overridden"]
    assert slots[0]["look"]["style"] == "explainer" and "character" not in slots[0]["look"] and slots[0]["family"] == "paper"
    with pytest.raises(lu.LineupError):
        lu.validate(avx_doc(slots={"1": {"style": "avx", "bg": "nope"}}))


def test_video_style_avx_in_the_environment_gives_an_avx_default_lineup():
    d = lu.default_from_environ({"VIDEOS_PER_DAY": "4", "VIDEO_STYLE": "avx", "CHARACTER": "puck", "LOOK_BG": "#2f4fe0"})
    b = d["batches"][0]
    assert d["total"] == 4 and b["style"] == "avx" and b["character"] == "puck" and b["bg"] == "#2f4fe0"


def test_write_scripts_makes_one_avx_writer_call_for_every_avx_slot():
    slots = lu.expand(lu.validate(avx_doc()), DAY)
    calls = []

    def fake_build(editions, n_videos=None, exclude_titles=frozenset()):
        import os
        calls.append((os.environ.get("VIDEO_STYLE"), n_videos))
        return [make_script(f"S{len(calls)}-{i}") for i in range(n_videos)]

    scripts = lu.write_scripts(slots, DAY, 6, [], set(), fake_build)
    assert calls == [("avx", 5), ("explainer", 1)]
    assert [s.lineup["slot"] for s in scripts] == [1, 2, 3, 4, 5, 6]
    assert scripts[0].look["style"] == "avx" and scripts[0].lineup["format"] == "Avatar explainer"


# ---- the writer's validator ---------------------------------------------------------------------

def test_a_good_scene_list_is_accepted_and_converted():
    scenes, repairs = w.check_video(VIDEO, [STORY])
    assert repairs == []
    assert [s["style"] for s in scenes] == ["kinetic", "stat", "relation", "versus", "list", "outro"]
    stat = scenes[1]["data"]
    assert stat["value"] == 62 and stat["dots"] == 20 and stat["fill"] == 12 and stat["count"] == {"cue": "sixty"}
    assert scenes[0]["captions"] is False and scenes[0]["data"]["underline"] == {"cue": "billion"}
    assert scenes[3]["data"]["right"]["title"] == "Nvidia" and scenes[3]["data"]["verdict"]["text"] == "Racing to catch up"
    assert scenes[2]["data"]["from"]["name"] == "Jensen Huang" and scenes[2]["data"]["verb"] == {"text": "hypes", "cue": "says"}


def _with(i, **fields):
    v = video()
    v["scenes"][i].update(fields)
    return v


@pytest.mark.parametrize("bad, fragment", [
    (_with(1, value=75), "75 is not in the story"),  # an invented statistic
    (_with(1, label="up on 9 measures"), "9 is not in the story"),  # an invented number in a label
    (_with(4, style="chart", series=[{"name": "Sales", "values": [10, 20, 30], "cue": "Demand"}], x_labels=["Q1", "Q2", "Q3"]),
     "is not in the story"),  # illustrative chart data
    (_with(0, lines=[{"text": "Nvidia just made a fortune", "em": False, "cue": "Nvidia"}, {"text": "$57B", "em": True, "cue": ""}]),
     "at most 16"),  # overflow
    (_with(4, items=[{"text": "Demand is very very real today", "cue": "Demand"}, {"text": "x", "cue": ""}]), "at most 22"),
    (_with(2, style="sparkle"), "style must be one of"),
    (_with(2, a={"name": "Lisa Su", "tag": "AMD CEO", "items": [], "initials": "LS", "cue": "Jensen"}), "not a person, company"),
    (_with(2, b={"name": "Demand", "tag": "The market", "items": [], "initials": "D", "cue": "demand"}), "not a person, company"),
    (_with(5, lines=[{"text": "Is Nvidia", "em": False, "cue": ""}, {"text": "unstoppable?", "em": True, "cue": ""}]),
     "is not what the narrator says"),
    (_with(1, value="lots"), "must be a number"),
    (video(scenes=[s for i, s in enumerate(copy.deepcopy(VIDEO["scenes"])) if i not in (2, 3)]), "spoken lines total"),
    (video(scenes=list(reversed(copy.deepcopy(VIDEO["scenes"])))), "first scene must be the kinetic hook"),
    (video(story_ids=[7]), "story_ids"),
])
def test_bad_scene_lists_are_rejected(bad, fragment):
    with pytest.raises(w.AvxPlanError) as exc:
        w.check_video(bad, [STORY])
    assert fragment in str(exc.value)


def test_too_few_styles_are_rejected():
    v = video()
    for i in (1, 2, 3, 4):  # every middle scene a list
        v["scenes"][i] = copy.deepcopy(VIDEO["scenes"][4])
        v["scenes"][i]["say"] = VIDEO["scenes"][i]["say"].replace("Demand", "demand") + " Demand is real."
    with pytest.raises(w.AvxPlanError, match="at least 2 different middle styles"):
        w.check_video(v, [STORY])


def test_bad_cues_poses_and_long_eyebrows_are_repaired_not_rejected():
    v = _with(3, pose=9, eyebrow="A very long chapter label indeed", text_cue="zebra")
    scenes, repairs = w.check_video(v, [STORY])
    assert scenes[3]["pose"] == 0 and len(scenes[3]["eyebrow"]) <= w.CAPS["eyebrow"]
    assert "cue" not in scenes[3]["data"]["verdict"] and any("zebra" in r for r in repairs)
    # cues match case- and punctuation-insensitively
    scenes, _ = w.check_video(_with(5, text_cue="TELL,"), [STORY])
    assert scenes[5]["data"]["cta"]["cue"] == "TELL,"


def test_a_source_the_story_does_not_name_is_dropped():
    scenes, repairs = w.check_video(_with(1, source="Bloomberg"), [STORY])
    assert "source" not in scenes[1]["data"] and any("Bloomberg" in r for r in repairs)
    scenes, _ = w.check_video(_with(1, source="Nvidia earnings"), [STORY])
    assert scenes[1]["data"]["source"] == "Nvidia earnings"


def test_numbers_match_with_rounding_and_units_but_not_loosely():
    allowed = w.story_numbers(STORY.title + " " + STORY.summary)
    assert w.number_ok(57, allowed) and w.number_ok(51.2, allowed) and w.number_ok(51, allowed)
    assert w.number_ok(57000, allowed)  # $57 billion as 57,000 million
    assert not w.number_ok(60, allowed) and not w.number_ok(2025, allowed)
    assert w.numbers_in("GPT-5 costs $7.5 for 2,350 users in Q3") == [5.0, 7.5, 2350.0, 3.0]


# ---- the Claude call: retry once with the validator's errors ---------------------------------

def test_rejected_videos_go_back_to_claude_once(monkeypatch):
    bad = _with(1, value=75)
    answers = [{"videos": [bad]}, {"videos": [VIDEO]}]
    prompts = []

    def fake_call(user):
        prompts.append(user)
        return answers.pop(0)

    monkeypatch.setattr(w, "_call_writer", fake_call)
    scripts = w.llm_avx_scripts([STORY], 1)
    assert len(scripts) == 1 and len(prompts) == 2 and "75 is not in the story" in prompts[1]
    s = scripts[0]
    assert s.writer == "claude" and s.avx_scenes and s.stories == [STORY] and s.hashtags == ["nvidia", "ai", "technews"]
    assert s.narration == " ".join(sc["say"] for sc in SCENES) and s.word_count == 99
    assert s.hook == SCENES[0]["say"] and s.outro == SCENES[-1]["say"] and len(s.beats) == 4


def test_a_video_that_fails_twice_is_dropped(monkeypatch):
    monkeypatch.setattr(w, "_call_writer", lambda user: {"videos": [_with(1, value=75)]})
    assert w.llm_avx_scripts([STORY], 1) == []
    monkeypatch.setattr(w, "_call_writer", lambda user: None)
    assert w.llm_avx_scripts([STORY], 1) is None


def test_the_schema_and_prompt_carry_the_rules():
    props = w.SCENE_SCHEMA["properties"]
    assert set(w.SCENE_SCHEMA["required"]) == set(props) and props["style"]["enum"] == list(avx.STYLES)
    assert "illustrative" in w.AVX_WRITER_SYSTEM and "no number" in w.AVX_WRITER_SYSTEM.lower()


# ---- the rule-based fallback ---------------------------------------------------------------------

def test_without_claude_build_scripts_writes_heuristic_avx_scripts(monkeypatch, isolated_env):
    from newsletter_video.script_writer import build_scripts

    monkeypatch.setenv("VIDEO_STYLE", "avx")
    stories = [make_story("Nvidia posts record quarter"), make_story("Apple ships a new chip")]
    scripts = build_scripts([Edition("tech", DAY.isoformat(), "TLDR", stories)], n_videos=1)
    assert len(scripts) == 1
    s = scripts[0]
    assert s.writer == "heuristic" and s.avx_scenes[0]["style"] == "kinetic" and s.avx_scenes[-1]["style"] == "outro"
    assert {sc["style"] for sc in s.avx_scenes[1:-1]} == {"list"}
    assert s.narration.split() == " ".join(sc["say"] for sc in s.avx_scenes).split()
    for sc in s.avx_scenes:  # every cue that survived is a spoken word of its scene
        spoken = {w._norm(x) for x in sc["say"].split()}
        for cue in json.dumps(sc["data"]).split('"cue": "')[1:]:
            assert w._norm(cue.split('"')[0].split()[0]) in spoken


def test_structural_scenes_wrap_and_cap_text():
    s = make_script("A title")
    s.hook = "Everyone's about to be talking about Claude Haiku 5.5 and what it costs."
    scenes = w.structural_scenes(s)
    assert len(scenes[0]["data"]["lines"]) <= 4
    for sc in scenes[1:-1]:
        assert all(len(it["text"]) <= w.CAPS["list_item"] for it in sc["data"]["items"])


# ---- plans and sidecars ----------------------------------------------------------------------------

def _claude_script():
    scenes, _ = w.check_video(VIDEO, [STORY])
    s = w.script_from_scenes(scenes, VIDEO["title"], VIDEO["badge"], [STORY], VIDEO["post_caption"], VIDEO["hashtags"])
    s.look = {"style": "avx", "bg": "", "accent": "#ff4d1f", "character": "puck"}
    return s


def test_plan_entries_round_trip_the_scene_list():
    s = _claude_script()
    entry = pm.entry_from_script(s, 1, s.look)
    assert entry["scene_plan"]["style"] == "avx" and len(entry["scene_plan"]["scenes"]) == 6
    entry = json.loads(json.dumps(entry))  # through the JSON file
    back = pm.script_from_entry(entry)
    assert back.avx_scenes == s.avx_scenes and back.narration == s.narration and back.writer == "claude"
    assert back.look["style"] == "avx" and back.look["accent"] == "#ff4d1f" and back.stories[0].title == STORY.title


def test_the_sidecar_stores_the_scenes_for_a_rerender(tmp_path):
    s = _claude_script()
    s.lineup = lu.lineup_info(lu.expand(lu.validate(avx_doc()), DAY)[0], DAY, 6)
    mp4 = tmp_path / "01_nvidia.mp4"
    mp4.write_bytes(b"x")
    meta = json.loads(write_metadata(s, mp4).read_text())
    assert meta["scene_plan"]["style"] == "avx" and meta["stories"] == [STORY.title] and meta["look"]["style"] == "avx"
    assert meta["lineup"]["format"] == "Avatar explainer" and meta["writer"] == "claude"
    back = pm.script_from_sidecar(meta)
    assert back.avx_scenes == s.avx_scenes and back.narration == s.narration


def test_a_scripts_without_scenes_still_render_as_avx():
    plain = make_script("Switched format")  # e.g. a planned explainer video switched to this format
    scenes = w.scenes_for(plain)
    assert scenes[0]["style"] == "kinetic" and scenes[-1]["style"] == "outro"


# ---- colours and the render ---------------------------------------------------------------------------

def _per_line(scenes):
    t, out = 0.2, []
    for s in scenes:
        line = []
        for word in s["say"].split():
            line.append((word, round(t, 3)))
            t += 0.3
        out.append(line)
        t += 0.4
    return out


def test_one_ground_for_every_scene_cuts_between_them():
    scenes = avx.apply_look(_claude_script().avx_scenes, "#F3EFE6", "#2f4fe0")
    assert {s["bg"] for s in scenes} == {"#f3efe6"} and {s["accent"] for s in scenes} == {"#2f4fe0"}
    p = avx.build_payload(scenes, _per_line(scenes), 40.0)
    assert {s["transition"] for s in p["scenes"]} == {"cut"}  # the graphics' own directional exits, no wipes


def test_the_house_rhythm_matches_the_draft():
    assert avx.house_rhythm(8) == [s["bg"] for s in avx.DRAFT_SCENES]
    scenes = avx.apply_look(_claude_script().avx_scenes, "", "")
    assert [s["bg"] for s in scenes] == avx.house_rhythm(6) and "accent" not in scenes[0]
    p = avx.build_payload(scenes, _per_line(scenes), 40.0)
    assert p["scenes"][-1]["transition"] == "shutter" and p["scenes"][2]["transition"] == "iris"
    assert len({s["pal"]["accent"] for s in p["scenes"]}) == 3  # each ground its own accent


def test_the_builder_dispatches_avx_videos_with_their_look(monkeypatch, tmp_path, isolated_env):
    from newsletter_video import video_builder as vb

    seen = {}

    def fake_build_avx(scenes, out_path, work_dir, **kw):
        import os
        seen.update(scenes=scenes, out=out_path, work=work_dir, character=os.environ.get("CHARACTER"), **kw)
        out_path.write_bytes(b"mp4")
        return {}

    monkeypatch.setattr(avx, "build_avx", fake_build_avx)
    s = _claude_script()
    s.look = {"style": "avx", "bg": "#0d0f14", "accent": "", "character": "puck"}
    out_dir, audio_dir = tmp_path / "out", tmp_path / "_audio" / "day"
    rendered = []
    paths = vb.build_shorts([s], out_dir, audio_dir, DAY, on_rendered=lambda sc, p: rendered.append(p))
    assert paths == rendered and paths[0].read_bytes() == b"mp4"
    assert seen["character"] == "puck" and {x["bg"] for x in seen["scenes"]} == {"#0d0f14"}
    assert seen["work"].name == "_hf_work_avx" and seen["audio_out"] == audio_dir / "01.wav" and seen["crf"] == vb.RENDER_CRF
    status = json.loads((out_dir / "status.json").read_text())
    assert any(v["status"] == "rendered" for v in status["videos"].values())


def test_avx_render_failures_retry_once_and_clean_up(monkeypatch, tmp_path):
    from newsletter_video import supervisor

    calls = []

    def failing(cmd, timeout, **kw):
        calls.append(timeout)
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"half")
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(supervisor, "run_subprocess", failing)
    monkeypatch.setenv("RENDER_TIMEOUT_SECONDS", "77")
    out = tmp_path / "v.mp4"
    with pytest.raises(subprocess.TimeoutExpired):
        avx.render(tmp_path, tmp_path / "story.json", out, tmp_path / "r.log")
    assert calls == [77.0, 77.0] and not out.exists() and not (tmp_path / ".partial" / "v.mp4").exists()

    def ok(cmd, timeout, **kw):
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"video")

    monkeypatch.setattr(supervisor, "run_subprocess", ok)
    assert avx.render(tmp_path, tmp_path / "story.json", out, tmp_path / "r.log").read_bytes() == b"video"


def test_avatar_poses_fall_back_when_a_character_has_fewer(monkeypatch, tmp_path):
    from PIL import Image

    from newsletter_video import video_builder as vb

    img = tmp_path / "a.png"
    Image.new("RGBA", (40, 80)).save(img)
    monkeypatch.setattr(vb, "character_image", lambda: img)
    monkeypatch.setattr(vb, "character_poses", lambda: [])
    av = avx._avatar_assets(tmp_path, "s", [0.0, 2.0, 4.0], [3, 1, 0])
    assert av["poses"] == ["pose-1.png"] and av["first"] == 0 and av["changes"] == []
    monkeypatch.setattr(vb, "character_image", lambda: None)
    assert avx._avatar_assets(tmp_path, "s", [0.0], [0])["poses"] == ["pose-1.png"]


# ---- previews ----------------------------------------------------------------------------------------

def test_previews_build_an_avx_payload_and_storyboard_times(tmp_path):
    from newsletter_video import preview

    s = _claude_script()
    look = looks.resolve_look(s.look)
    src = preview.Source(s, look, tmp_path, "v01", DAY, "plan")
    assert src.lines == [sc["say"] for sc in SCENES]
    per_line, speech = preview.estimate_timing(src.lines)
    payload = preview.build_payload(src, per_line, speech)
    stills = preview.still_times(payload, "avx")
    assert len(stills) == 6
    for st, sc in zip(stills, payload["scenes"]):
        assert sc["start"] < st.at < sc["end"]


def test_kinetic_lines_without_cues_land_on_their_first_spoken_word():
    v = _with(0, lines=[{"text": "Nvidia just made", "em": False, "cue": ""}, {"text": "$57 billion", "em": True, "cue": ""}])
    scenes, _ = w.check_video(v, [STORY])
    assert scenes[0]["data"]["lines"][0]["cue"] == "Nvidia" and "cue" not in scenes[0]["data"]["lines"][1]


def test_names_are_the_stories_proper_nouns():
    names = w.proper_nouns(STORY.title + "\n" + STORY.summary)
    assert {"nvidia", "jensen", "huang", "blackwell", "amd", "mi450"} <= names
    assert not {"data", "demand", "chips", "sales"} & names  # ordinary words in the middle of a sentence


def test_a_scene_whose_element_lands_on_its_last_words_gets_a_longer_breath():
    late = {"style": "list", "say": "Watch the final close", "data": {"title": "T", "items": [{"text": "Close", "cue": "close"}]}}
    early = {"style": "list", "say": "Close watch on it all", "data": {"title": "T", "items": [{"text": "Close", "cue": "close"}]}}
    out = avx.apply_look([late, early], "#ffffff")
    assert out[0]["pause_after"] == avx.LATE_CUE_PAUSE and "pause_after" not in out[1]


def test_kinetic_lines_are_cued_on_their_own_first_word():
    v = _with(0, lines=[{"text": "Nvidia just made", "em": False, "cue": "months"}, {"text": "$57 billion", "em": True, "cue": "demand."}])
    scenes, _ = w.check_video(v, [STORY])
    assert scenes[0]["data"]["lines"][0]["cue"] == "Nvidia" and "cue" not in scenes[0]["data"]["lines"][1]
