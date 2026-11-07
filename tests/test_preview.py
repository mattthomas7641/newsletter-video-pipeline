"""Preview planning (preview.py): timing estimates, which moments the
storyboard captures, the story injection into the composition, and the
rule that previews never voice or render in full on their own."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from newsletter_video import plan_manifest as pm
from newsletter_video import preview
from newsletter_video.explainer_writer import script_from_plan
from newsletter_video.scene_plan import validate_plan
from tests.conftest import make_script, make_story
from tests.test_plan_manifest import PLAN

DAY = date(2026, 11, 7)


def test_estimated_timing_is_monotonic_and_about_the_voice_pace():
    lines = ["One two three four five six seven eight nine ten.", "Eleven twelve thirteen fourteen fifteen."]
    per_line, speech = preview.estimate_timing(lines)
    times = [t for line in per_line for _, t in line]
    assert times == sorted(times) and times[0] >= 0.1 and times[-1] < speech
    assert 15 / 2.5 + 0.3 <= speech <= 15 / 2.5 + 1.0


def test_explainer_stills_land_after_each_scene_is_built():
    script = script_from_plan(validate_plan(PLAN), "T", "AI", [make_story("s")])
    source = preview.Source(script, {"style": "explainer", "theme": "light", "character": "", "background": "none",
                                     "recipe": "auto"}, Path("/tmp"), "v01", DAY, "plan")
    per_line, speech = preview.estimate_timing(source.lines)
    payload = preview.build_payload(source, per_line, speech)
    stills = preview.still_times(payload, "explainer")
    scenes = payload["scenes"]
    assert stills[0].label.startswith("hook") and len(stills) == len(scenes) + 1
    for still, scene in zip(stills[1:], scenes):
        assert scene["start"] + 0.5 <= still.at <= scene["end"] - 0.3, (still, scene)
        for cue in scene.get("cues", []):
            assert still.at >= min(cue + 0.5, scene["end"] - 0.4)  # after the last reveal, or as late as the scene allows
    assert stills[2].scene == 1 and stills[2].label == "2 · stat" and stills[2].at >= scenes[1]["countAt"] + 0.5


def test_avatar_stills_cover_hook_cards_and_outro():
    script = make_script("Classic")
    source = preview.Source(script, {"style": "avatar", "theme": "light", "character": "", "background": "none",
                                     "recipe": "auto"}, Path("/tmp"), "v01", DAY, "plan")
    per_line, speech = preview.estimate_timing(source.lines)
    payload = preview.build_payload(source, per_line, speech)
    stills = preview.still_times(payload, "avatar")
    assert [s.label for s in stills][0] == "hook" and stills[-1].label == "outro" and len(stills) == len(script.beats) + 2
    assert all(a.at < b.at for a, b in zip(stills, stills[1:]))


def test_style_detail_lists_non_default_knobs():
    assert preview._style_detail({"style": {"recipe": "kinetic-slam", "transition": "cut", "entrance": "default",
                                            "type_treatment": "caps"}}) == "kinetic-slam, transition cut, type treatment caps"
    assert preview._style_detail({}) == ""


def test_inject_story_replaces_the_declared_default(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html data-composition-variables='[\n {\"id\":\"story\",\"default\":\"{\\\"a\\\":1}\"}\n  ]'>\n<body>x's</body></html>")
    preview.inject_story(html, {"kicker": "TLDR · NOV 7", "title": "it's & more"})
    text = html.read_text()
    assert "&#39;" in text and "&amp;" in text and "<body>x's</body>" in text
    import re

    attr = re.search(r"data-composition-variables='([^']*)'", text).group(1)
    declared = json.loads(attr.replace("&#39;", "'").replace("&amp;", "&"))
    assert json.loads(declared[0]["default"])["title"] == "it's & more"
    (tmp_path / "none.html").write_text("<html><body>no declaration</body></html>")
    with pytest.raises(RuntimeError):
        preview.inject_story(tmp_path / "none.html", {})


def test_sources_from_plan_and_sidecar_carry_resolved_looks(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    monkeypatch.setenv("VIDEO_THEME", "light")
    folder = tmp_path / "2026-11-07"
    pm.write_manifest(folder, pm.new_manifest([make_script("A")], DAY, look_overrides={"theme": "dark"}))
    src = preview.source_from_plan(folder, "v01", {"style": "avatar"})
    assert src.look["style"] == "avatar" and src.look["theme"] == "dark" and src.key == "v01" and src.day == DAY
    assert src.out_dir == folder / "_preview" / "v01"
    sidecar = folder / "03_thing.json"
    sidecar.write_text(json.dumps({"title": "Thing", "stories": ["s"], "script": {
        "title": "Thing", "badge": "AI", "hook": "Hook here.", "beats": [{"say": "a b c", "card": "c"}], "outro": "Bye."},
        "look": {"version": 1, "style": "avatar", "theme": "dark"}}))
    src2 = preview.source_from_sidecar(sidecar, {"theme": "light"})
    assert src2.look["style"] == "avatar" and src2.look["theme"] == "light" and src2.key == "03_thing"
    assert src2.audio_hint is None  # no narration saved for this day


def test_stills_and_draft_never_synthesise_without_voice(tmp_path, monkeypatch):
    """Without --voice the preview uses cached or estimated timing; TTS is
    never called. With --voice it is (once), and the result is cached."""
    folder = tmp_path / "2026-11-07"
    pm.write_manifest(folder, pm.new_manifest([make_script("A")], DAY))
    source = preview.source_from_plan(folder, "v01", {"style": "avatar", "background": "none"})
    calls = []
    monkeypatch.setattr(preview.eb, "_voice_paced", lambda lines, wav, cache: calls.append(1) or (
        [[(w, 0.1 * i) for i, w in enumerate(l.split())] for l in lines], 20.0))
    audio, per_line, speech, timing = preview._timing(source, voice=False)
    assert audio is None and timing == "estimated timing" and calls == []
    monkeypatch.setattr(preview.vb, "_speech_span", lambda p: (0.0, 20.0, 20.0))
    audio, per_line, speech, timing = preview._timing(source, voice=True)
    assert timing == "voiced" and calls == [1] and audio.name.endswith(".wav")
    (source.folder / preview.AUDIO_CACHE_DIRNAME / audio.name).write_bytes(b"RIFF")  # pretend the file exists
    audio2, _, _, timing2 = preview._timing(source, voice=False)
    assert timing2 == "cached voice" and audio2 == audio and calls == [1]


def test_cli_defaults_to_stills_and_needs_final_for_a_full_render(tmp_path, monkeypatch):
    folder = tmp_path / "2026-11-07"
    pm.write_manifest(folder, pm.new_manifest([make_script("A")], DAY))
    seen = {}

    def stub(tier, file):
        def run(source, *a, **k):
            seen["tier"] = tier
            return {"tier": tier, "file": file, "seconds": 1, "timing": "estimated timing"}
        return run

    monkeypatch.setattr(preview, "preview_stills", stub("stills", "storyboard.jpg"))
    monkeypatch.setattr(preview, "preview_draft", stub("draft", "draft.mp4"))
    monkeypatch.setattr(preview, "render_final", lambda source, out: seen.__setitem__("tier", "final") or out)
    monkeypatch.setattr(preview.eb, "PROJECT_ROOT", tmp_path)
    assert preview.main([str(folder), "--video", "v01"]) == 0 and seen["tier"] == "stills"
    seen.clear()
    assert preview.main([str(folder), "--video", "v01", "--preview", "draft"]) == 0 and seen["tier"] == "draft"
    seen.clear()
    assert preview.main([str(folder), "--video", "v01", "--final", "--out", str(tmp_path / "f.mp4")]) == 0
    assert seen["tier"] == "final"
