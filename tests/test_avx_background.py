"""The avatar explainer's background switch: plain colour grounds (the default) or gameplay footage behind the same
animations (AVX_BACKGROUND=gameplay)."""
from __future__ import annotations

from pathlib import Path

import pytest

from newsletter_video import avx_builder as avx
from newsletter_video import video_builder as vb


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("AVX_BACKGROUND", raising=False)


def test_plain_colour_is_the_default_and_gameplay_needs_both_the_setting_and_a_clip(monkeypatch):
    monkeypatch.setattr(avx, "background_clips", lambda: [Path("a.mp4")])
    assert avx.gameplay_background() is False  # unset: the colour backgrounds, whatever clips exist
    monkeypatch.setenv("AVX_BACKGROUND", "plain")
    assert avx.gameplay_background() is False
    monkeypatch.setenv("AVX_BACKGROUND", "Gameplay")
    assert avx.gameplay_background() is True
    monkeypatch.setattr(avx, "background_clips", lambda: [])
    assert avx.gameplay_background() is False  # no footage: plain, never a half-and-half video


def test_the_background_file_always_exists_and_the_flag_says_whether_footage_is_behind(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(avx, "background_clips", lambda: [Path("a.mp4")])
    monkeypatch.setattr(vb, "_cut_background", lambda clips, dst, dur, seed: calls.append(("cut", dur, seed)) or True)
    monkeypatch.setattr(vb, "_blank_background", lambda dst, dur: calls.append(("blank", dur)))
    assert avx._place_background(tmp_path, 31.5, "2026-11-11-3", gameplay=True) is True
    assert avx._place_background(tmp_path, 31.5, "2026-11-11-3", gameplay=False) is False
    assert calls == [("cut", 31.5, "2026-11-11-3"), ("blank", 31.5)]


def test_gameplay_that_cannot_be_cut_falls_back_to_a_blank_file(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(avx, "background_clips", lambda: [Path("a.mp4")])
    monkeypatch.setattr(vb, "_cut_background", lambda *a: False)  # a clip shorter than the video, say
    monkeypatch.setattr(vb, "_blank_background", lambda dst, dur: calls.append("blank"))
    assert avx._place_background(tmp_path, 31.5, "s", gameplay=True) is False and calls == ["blank"]


def test_gameplay_scenes_share_one_dark_ground_so_there_are_no_colour_wipes_and_the_type_is_light():
    from newsletter_video import avx_writer as w
    from tests.avx_fixtures import STORY, VIDEO
    import copy

    scenes = w.check_video(copy.deepcopy(VIDEO), [STORY])[0]
    looked = avx.apply_look(scenes, avx.GAMEPLAY_GROUND, "", seed="2026-11-11-1")
    assert {s["bg"] for s in looked} == {avx.GAMEPLAY_GROUND}
    assert all("transition" not in s for s in looked)  # one ground: nothing to wipe between
    pal = avx.palette(avx.GAMEPLAY_GROUND)
    assert pal["light"] is False  # a dark ground: light ink


def test_the_template_shows_footage_and_a_shade_only_in_gameplay_mode_and_drops_the_colour_ground():
    html = (avx.TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    assert '<video id="gp" src="assets/bg.mp4"' in html and 'id="shade"' in html
    assert "story.gameplay" in html and ".gp .scene-bg { background: transparent; }" in html
    assert ".gp #ground { display: none; }" in html
    assert html.index('id="gp"') < html.index('id="ground"') < html.index('id="scenes"')  # footage under everything
