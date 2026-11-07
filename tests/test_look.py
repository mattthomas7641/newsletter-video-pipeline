"""Look layering and validation (look.py), and the proof that a script
without a per-video look renders with the environment untouched."""
from __future__ import annotations

import os

import pytest

from newsletter_video import look as looks
from newsletter_video import video_builder
from tests.conftest import make_script


def test_default_look_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "avatar")
    monkeypatch.setenv("CHARACTER", "gizmo")
    monkeypatch.setenv("VIDEO_THEME", "dark")
    monkeypatch.delenv("BACKGROUND_STYLE", raising=False)
    monkeypatch.delenv("STYLE_RECIPE", raising=False)
    assert looks.look_from_env() == {"style": "avatar", "character": "gizmo", "theme": "dark",
                                     "background": "gameplay", "recipe": "auto"}


def test_layers_fill_only_unset_fields(monkeypatch):
    env = {"VIDEO_STYLE": "explainer", "CHARACTER": "", "VIDEO_THEME": "light"}
    run = {"theme": "dark"}
    video = {"style": "avatar", "character": "gizmo"}
    resolved = looks.resolve_look(run, video, environ=env)
    assert resolved == {"style": "avatar", "character": "gizmo", "theme": "dark", "background": "gameplay", "recipe": "auto"}
    # a later layer wins over an earlier one; an unset later field keeps the earlier value
    assert looks.resolve_look({"theme": "dark"}, {"theme": "light"}, environ=env)["theme"] == "light"
    assert looks.resolve_look({"theme": "dark"}, {"theme": None}, environ=env)["theme"] == "dark"
    assert looks.resolve_look({"theme": "dark"}, {}, environ=env)["theme"] == "dark"


@pytest.mark.parametrize("bad", [
    {"style": "cartoon"}, {"theme": "sepia"}, {"background": "beach"}, {"character": "../x"},
    {"recipe": "not a slug!"}, {"voice": "deep"}, {"version": 99}, {"style": 3}, "avatar",
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
        seen["env"] = {k: os.environ.get(k) for k in ("VIDEO_STYLE", "CHARACTER", "VIDEO_THEME", "BACKGROUND_STYLE", "STYLE_RECIPE")}
        return out_path

    monkeypatch.setattr(video_builder, "_build_short", fake_inner)
    monkeypatch.setenv("VIDEO_STYLE", "avatar")
    monkeypatch.setenv("CHARACTER", "")
    monkeypatch.delenv("VIDEO_THEME", raising=False)
    before = {k: os.environ.get(k) for k in ("VIDEO_STYLE", "CHARACTER", "VIDEO_THEME", "BACKGROUND_STYLE", "STYLE_RECIPE")}
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
