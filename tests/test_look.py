"""Look layering and validation (look.py), and the proof that a script
without a per-video look renders with the environment untouched."""
from __future__ import annotations

import os

import pytest

from newsletter_video import look as looks
from newsletter_video import plan_manifest as pm
from newsletter_video import video_builder
from tests.conftest import make_script


def test_default_look_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "avatar")
    monkeypatch.setenv("CHARACTER", "gizmo")
    monkeypatch.setenv("VIDEO_THEME", "dark")
    monkeypatch.delenv("BACKGROUND_STYLE", raising=False)
    monkeypatch.delenv("STYLE_RECIPE", raising=False)
    monkeypatch.delenv("LOOK_PRESET", raising=False)
    assert looks.look_from_env() == {"style": "avatar", "character": "gizmo", "theme": "dark",
                                     "background": "gameplay", "recipe": "auto", "preset": "", "bg": "", "accent": ""}


def test_layers_fill_only_unset_fields(monkeypatch):
    env = {"VIDEO_STYLE": "explainer", "CHARACTER": "", "VIDEO_THEME": "light"}
    run = {"theme": "dark"}
    video = {"style": "avatar", "character": "gizmo"}
    resolved = looks.resolve_look(run, video, environ=env)
    assert resolved == {"style": "avatar", "character": "gizmo", "theme": "dark", "background": "gameplay", "recipe": "auto",
                        "preset": "", "bg": "", "accent": ""}
    # a later layer wins over an earlier one; an unset later field keeps the earlier value
    assert looks.resolve_look({"theme": "dark"}, {"theme": "light"}, environ=env)["theme"] == "light"
    assert looks.resolve_look({"theme": "dark"}, {"theme": None}, environ=env)["theme"] == "dark"
    assert looks.resolve_look({"theme": "dark"}, {}, environ=env)["theme"] == "dark"


@pytest.mark.parametrize("bad", [
    {"style": "cartoon"}, {"theme": "sepia"}, {"background": "beach"}, {"character": "../x"},
    {"recipe": "not a slug!"}, {"voice": "deep"}, {"version": 99}, {"style": 3}, "avatar", {"preset": "no-such-preset"},
])
def test_invalid_looks_are_rejected_with_every_problem_listed(bad):
    with pytest.raises(looks.LookError) as info:
        looks.validate_look(bad)
    assert info.value.errors


def test_validation_normalises_case_and_treats_blank_as_unset():
    clean = looks.validate_look({"style": " Avatar ", "theme": "", "recipe": "Kinetic-Slam", "character": ""})
    assert clean["style"] == "avatar" and clean["theme"] is None and clean["recipe"] == "kinetic-slam"
    assert clean["character"] == ""  # blank character is a real choice: the default narrator
    assert looks.compact(clean) == {"version": 1, "style": "avatar", "character": "", "recipe": "kinetic-slam"}
    assert looks.is_empty({}) and looks.is_empty(None) and not looks.is_empty(clean)


def test_applied_sets_and_restores_the_environment(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    monkeypatch.delenv("VIDEO_THEME", raising=False)
    resolved = looks.resolve_look({"style": "avatar", "theme": "dark"})
    with looks.applied(resolved):
        assert os.environ["VIDEO_STYLE"] == "avatar" and os.environ["VIDEO_THEME"] == "dark"
    assert os.environ["VIDEO_STYLE"] == "explainer" and "VIDEO_THEME" not in os.environ
    with looks.applied(None):
        pass  # a no-op, never raises


def test_build_short_without_a_look_leaves_the_environment_exactly_as_the_daily_job_sees_it(monkeypatch, tmp_path):
    """The unattended run never sets a per-video look, so the builder must
    see the plain .env values, untouched, for a script whose look is None."""
    seen = {}

    def fake_inner(script, out_path, audio_path, work_dir, kicker, seed):
        seen["env"] = {k: os.environ.get(k) for k in ("VIDEO_STYLE", "CHARACTER", "VIDEO_THEME", "BACKGROUND_STYLE", "STYLE_RECIPE", "LOOK_PRESET")}
        return out_path

    monkeypatch.setattr(video_builder, "_build_short", fake_inner)
    monkeypatch.setenv("VIDEO_STYLE", "avatar")
    monkeypatch.setenv("CHARACTER", "")
    monkeypatch.delenv("VIDEO_THEME", raising=False)
    before = {k: os.environ.get(k) for k in ("VIDEO_STYLE", "CHARACTER", "VIDEO_THEME", "BACKGROUND_STYLE", "STYLE_RECIPE", "LOOK_PRESET")}
    script = make_script("Plain")
    assert script.look is None
    video_builder.build_short(script, tmp_path / "x.mp4", tmp_path / "x.wav", tmp_path / "w", "K", "seed")
    assert seen["env"] == before  # nothing added, nothing changed


def test_build_short_with_a_look_layers_it_over_the_environment(monkeypatch, tmp_path):
    seen = {}

    def fake_inner(script, out_path, audio_path, work_dir, kicker, seed):
        seen["env"] = {k: os.environ.get(k) for k in ("VIDEO_STYLE", "VIDEO_THEME", "CHARACTER")}
        return out_path

    monkeypatch.setattr(video_builder, "_build_short", fake_inner)
    monkeypatch.setenv("VIDEO_STYLE", "avatar")
    monkeypatch.setenv("VIDEO_THEME", "light")
    monkeypatch.setenv("CHARACTER", "")
    script = make_script("Styled")
    script.look = {"style": "explainer", "theme": "dark"}
    video_builder.build_short(script, tmp_path / "x.mp4", tmp_path / "x.wav", tmp_path / "w", "K", "seed")
    assert seen["env"] == {"VIDEO_STYLE": "explainer", "VIDEO_THEME": "dark", "CHARACTER": ""}
    assert os.environ["VIDEO_STYLE"] == "avatar" and os.environ["VIDEO_THEME"] == "light"  # restored


def test_describe_reads_naturally():
    assert looks.describe({"style": "explainer", "character": "", "theme": "dark", "recipe": "none"}) == \
        "explainer, default narrator, dark theme, no recipes"
    assert "gameplay background" in looks.describe({"style": "avatar", "character": "gizmo", "background": "gameplay"})


def test_colours_are_validated_layered_and_blankable():
    env = {"VIDEO_STYLE": "explainer", "LOOK_BG": "#E3ECDF", "LOOK_ACCENT": "0e8a7d"}
    default = looks.look_from_env(env)
    assert default["bg"] == "#e3ecdf" and default["accent"] == "#0e8a7d"  # normalised from the env
    assert looks.resolve_look({"accent": "#ff00aa"}, environ=env)["accent"] == "#ff00aa" and looks.resolve_look({"accent": "#ff00aa"}, environ=env)["bg"] == "#e3ecdf"
    assert looks.resolve_look({"bg": ""}, environ=env)["bg"] == ""  # a video can go back to the look's own colour
    assert looks.compact({"bg": "", "accent": "#aabbcc"}) == {"version": 1, "bg": "", "accent": "#aabbcc"}  # "" is kept: "the look's own colour"
    for bad in ("red", "#12", "#gggggg", "javascript:1"):
        with pytest.raises(looks.LookError):
            looks.validate_look({"accent": bad})
    assert looks.validate_look({"bg": "#FFF"})["bg"] == "#ffffff"
    assert looks.look_env({"bg": "#ffffff", "accent": ""}) == {"LOOK_BG": "#ffffff", "LOOK_ACCENT": ""}


def test_colour_guard_keeps_text_readable():
    from newsletter_video.colors import contrast, fit_colors, luminance, palette_from_accent

    ok = fit_colors("light", "#e3ecdf", "#0e8a7d")
    assert ok == {"bg": "#e3ecdf", "accent": "#0e8a7d", "notes": []}
    pale = fit_colors("light", "#e3ecdf", "#e8f0e5")  # an accent that vanishes on the ground
    assert contrast(pale["accent"], pale["bg"]) >= 3.0 and "darkened" in pale["notes"][0]
    dark_paper = fit_colors("light", "#202020", None)  # paper needs a light ground
    assert luminance(dark_paper["bg"]) >= 0.55 and "lightened" in dark_paper["notes"][0]
    bright_glass = fit_colors("cinematic", "#88aaff", "#ff3d8a")  # glass needs a dark ground
    assert luminance(bright_glass["bg"]) <= 0.06 and "darkened" in bright_glass["notes"][0]
    dim = fit_colors("cinematic", "#04140d", "#1a1a40")
    assert contrast(dim["accent"], dim["bg"]) >= 3.0
    pal = palette_from_accent("#ff3d8a")
    assert len(pal) == 4 and pal[0] == "#ff3d8a" and len(set(pal)) == 4


def test_colours_reach_the_story_the_composition_reads(monkeypatch):
    from newsletter_video.explainer_builder import story_payload
    from tests.test_worlds import per_line_of, statements_only
    from newsletter_video.scene_plan import validate_plan

    plan = validate_plan(statements_only())
    for key in ("LOOK_BG", "LOOK_ACCENT"):
        monkeypatch.delenv(key, raising=False)
    assert "colors" not in story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0)  # the shipped look is untouched
    with looks.applied({"bg": "#e3ecdf", "accent": "#0e8a7d", "theme": "light"}):
        paper = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "light")
    assert paper["colors"] == {"bg": "#e3ecdf", "accent": "#0e8a7d"} and "palette" not in paper
    with looks.applied({"bg": "#04140d", "accent": "#ff3d8a"}):
        glass = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "cinematic")
    assert glass["colors"] == {"bg": "#04140d", "accent": "#ff3d8a"} and glass["palette"][0] == "#ff3d8a" and len(glass["palette"]) == 4
    assert "LOOK_BG" not in __import__("os").environ  # applied() restores the environment


def test_another_preset_starts_from_its_own_colours_not_the_defaults():
    env = {"VIDEO_STYLE": "explainer", "LOOK_PRESET": "cinematic-glass", "LOOK_BG": "#04140d", "LOOK_ACCENT": "#ff3d8a"}
    assert looks.resolve_look({}, environ=env)["accent"] == "#ff3d8a"
    paper = looks.resolve_look({"preset": "paper"}, environ=env)
    assert paper["preset"] == "paper" and paper["bg"] == "" and paper["accent"] == "" and paper["theme"] == "light"
    mixed = looks.resolve_look({"preset": "paper", "accent": "#0e8a7d"}, environ=env)
    assert mixed["accent"] == "#0e8a7d" and mixed["bg"] == ""
    assert looks.resolve_look({"preset": "cinematic-glass"}, environ=env)["bg"] == "#04140d"  # the same look keeps its colours


ROT = "#ff7a1a,#ff3d8a,#22d3ee"
GLASS_ENV = {"VIDEO_STYLE": "explainer", "LOOK_PRESET": "cinematic-glass", "LOOK_ACCENT_ROTATE": ROT}


def test_accent_rotation_alternates_wraps_and_is_stable():
    accents = [looks.resolve_look({}, environ=GLASS_ENV, index=i)["accent"] for i in range(7)]
    assert accents == ["#ff7a1a", "#ff3d8a", "#22d3ee", "#ff7a1a", "#ff3d8a", "#22d3ee", "#ff7a1a"]
    assert all(a != b for a, b in zip(accents, accents[1:]))  # consecutive videos always differ
    assert looks.resolve_look({}, environ=GLASS_ENV, index=1)["accent"] == accents[1]  # a re-render keeps its colour
    assert looks.resolve_look({}, environ=GLASS_ENV)["accent"] == ""  # no position, no rotation


def test_rotation_is_off_by_default_and_only_for_dark_glass_explainers():
    env = {k: v for k, v in GLASS_ENV.items() if k != "LOOK_ACCENT_ROTATE"}
    assert looks.resolve_look({}, environ={**env, "LOOK_ACCENT": "#abcdef"}, index=3)["accent"] == "#abcdef"  # the single colour
    assert looks.resolve_look({}, environ={**env}, index=3)["accent"] == ""
    paper = {**GLASS_ENV, "LOOK_PRESET": "paper"}
    assert looks.resolve_look({}, environ=paper, index=1)["accent"] == ""
    assert looks.resolve_look({"preset": "paper"}, environ=GLASS_ENV, index=1)["accent"] == ""  # a video that chose paper
    assert looks.resolve_look({"style": "avatar"}, environ=GLASS_ENV, index=1)["accent"] == ""
    assert looks.resolve_look({}, environ={**GLASS_ENV, "LOOK_ACCENT": "#abcdef"}, index=1)["accent"] == "#ff3d8a"  # rotation beats the single colour


def test_a_custom_style_accent_wins_over_the_rotation():
    assert looks.resolve_look({"accent": "#0e8a7d"}, environ=GLASS_ENV, index=1)["accent"] == "#0e8a7d"
    assert looks.resolve_look({"accent": ""}, environ=GLASS_ENV, index=1)["accent"] == ""  # "the look's own colour" is a choice too
    assert looks.resolve_look({"preset": "cinematic-glass"}, {"accent": "#0e8a7d"}, environ=GLASS_ENV, index=2)["accent"] == "#0e8a7d"


def test_with_rotation_feeds_the_daily_and_plan_render_paths(monkeypatch):
    monkeypatch.setenv("LOOK_PRESET", "cinematic-glass")
    monkeypatch.setenv("LOOK_ACCENT_ROTATE", ROT)
    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    monkeypatch.delenv("LOOK_ACCENT", raising=False)
    assert [looks.with_rotation(None, i)["accent"] for i in range(4)] == ["#ff7a1a", "#ff3d8a", "#22d3ee", "#ff7a1a"]
    assert looks.with_rotation({"accent": "#0e8a7d"}, 1) == {"accent": "#0e8a7d"}  # its own colour stays
    assert looks.with_rotation({"theme": "dark"}, 1) == {"theme": "dark"} or looks.with_rotation({"theme": "dark"}, 1)["theme"] == "dark"
    once = looks.with_rotation(None, 2)  # the sidecar records the resolved accent, so a re-render replays it
    assert looks.resolve_look(once, index=0)["accent"] == "#22d3ee"
    entry = {"index": 2, "look": {}}
    assert pm.resolved_look(entry)["accent"] == "#ff3d8a"  # plan videos are 1-based: video 2 is the second colour
    monkeypatch.delenv("LOOK_ACCENT_ROTATE")
    assert looks.with_rotation(None, 2) is None  # off: the unattended run is untouched


def test_rotation_colours_are_validated():
    from newsletter_video.colors import DEFAULT_ROTATION, ROTATION_MAX, parse_rotation

    assert parse_rotation(" #FF7A1A , 22d3ee,") == ["#ff7a1a", "#22d3ee"]
    assert parse_rotation("#ff7a1a,blue,#22d3ee") == ["#ff7a1a", "#22d3ee"]  # reading skips a bad entry
    with pytest.raises(ValueError):
        parse_rotation("#ff7a1a,blue", strict=True)
    with pytest.raises(ValueError):
        parse_rotation(",".join(["#ff7a1a"] * (ROTATION_MAX + 1)), strict=True)
    assert len(parse_rotation(",".join(["#ff7a1a"] * 12))) == ROTATION_MAX
    assert len(DEFAULT_ROTATION) == 5 and parse_rotation(",".join(DEFAULT_ROTATION)) == list(DEFAULT_ROTATION)
