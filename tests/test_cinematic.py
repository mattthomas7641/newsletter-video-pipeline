"""The cinematic look, the rich scene types and the look preset library:
validator schemas, style knobs (look, palette), preset loading and look
resolution, the theme resolution in the story payload, the writer prompt
switch, and the proof that the daily default is unchanged by all of it.
No network, no media."""
from __future__ import annotations

import copy
import json

import pytest

from newsletter_video import look as looks
from newsletter_video.explainer_builder import resolve_theme, story_payload
from newsletter_video.looks import PRESETS_PATH, get_preset, load_presets, preset_recipes, presets_json
from newsletter_video.scene_plan import (
    MAX_OBJECT3D,
    RICH_TYPES,
    SCENE_SCHEMA,
    SCENE_TYPES,
    VISUAL_TYPES,
    ScenePlanError,
    validate_plan,
)
from newsletter_video.styles import vocabulary as V
from newsletter_video.styles.planner import apply_recipe_policy
from newsletter_video.styles.recipes import DEFAULT_GLOW_PALETTE, builtin_recipes
from newsletter_video.styles.style_plan import DEFAULT_STYLE, is_cinematic, is_default, validate_style

LINE = "This spoken line carries the beat with enough words to reach the budget for the video."


def rich_plan() -> list[dict]:
    return [
        {"type": "title", "say": LINE, "kicker": "NETWORKS", "headline": "How a photo crosses the internet", "icon": "globe"},
        {"type": "object3d", "say": LINE, "kind": "stack", "title": "The stack",
         "nodes": [{"label": "Physical", "text": "cables"}, {"label": "Network", "text": "addresses"}, {"label": "App", "text": "the chat"}]},
        {"type": "kinetic", "say": LINE, "label": "Layer 4", "value": "4", "lines": ["Transport", "cut into pieces"]},
        {"type": "glass", "say": LINE, "label": "Packet header", "icon": "box",
         "nodes": [{"label": "To", "text": "203.0.113.7"}, {"label": "Hops", "text": "64"}]},
        {"type": "depth", "say": LINE, "label": "Behind the app",
         "nodes": [{"label": "The app", "text": "taps send"}, {"label": "The cable", "text": "glass"}]},
        {"type": "object3d", "say": LINE, "kind": "bars", "title": "Bytes per piece", "unit": "",
         "series": [{"label": "Header", "value": 40}, {"label": "Payload", "value": 1460}], "highlight": 1},
        {"type": "outro", "say": LINE, "text": "Which layer surprised you?"},
    ]


def errors_of(raw, strict=True) -> list[str]:
    with pytest.raises(ScenePlanError) as info:
        validate_plan(raw, strict=strict)
    return info.value.errors


def timed(plan):
    return [[(w, 0.3 * k + i * 4.0) for k, w in enumerate(s.say.split())] for i, s in enumerate(plan.scenes)]


# --- validator -------------------------------------------------------------------------

def test_rich_scene_types_validate_and_normalise():
    plan = validate_plan(rich_plan(), strict=True)
    assert [s.type for s in plan.scenes] == ["title", "object3d", "kinetic", "glass", "depth", "object3d", "outro"]
    assert plan.repairs == []
    stack = plan.scenes[1].fields
    assert stack["kind"] == "stack" and len(stack["nodes"]) == 3 and stack["series"] == []
    bars = plan.scenes[5].fields
    assert bars["kind"] == "bars" and bars["highlight"] == 1 and bars["nodes"] == [] and bars["series"][1]["value"] == 1460
    assert plan.scenes[2].fields == {"label": "Layer 4", "value": "4", "lines": ["Transport", "cut into pieces"]}
    assert plan.scenes[3].fields["nodes"][0] == {"label": "To", "text": "203.0.113.7"} and plan.scenes[3].fields["icon"] == "box"
    assert set(RICH_TYPES) <= set(SCENE_TYPES) and {"object3d", "depth", "glass"} <= VISUAL_TYPES
    assert set(RICH_TYPES) <= set(V.SCENE_TYPES) and set(RICH_TYPES) == set(V.RICH_SCENE_TYPES)


def test_rich_scene_caps_and_bounds():
    raw = rich_plan()
    raw[2]["lines"] = ["x" * 25]
    assert any("line 1 is 25 chars, cap is 24" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[2]["value"] = "12345"
    assert any("'value' is 5 chars, cap is 4" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[2]["lines"] = []
    assert any("'lines' has 0 entries, allowed 1-3" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[3]["nodes"] = [{"label": "To", "text": ""}, {"label": "From", "text": "x"}]
    assert any("'node 1.text' is missing" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[4]["nodes"] = [{"label": "Only one", "text": ""}]
    assert any("'nodes' has 1 entries, allowed 2-4" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[1]["kind"] = "orbit"  # needs a centre and 3-5 nodes
    assert any("'center' is missing" in e for e in errors_of(raw))
    raw[1]["center"] = "The platform"
    raw[1]["nodes"] = raw[1]["nodes"][:2]
    assert any("'nodes' has 2 entries, allowed 3-5" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[1]["kind"] = "sphere"
    assert any("'kind' must be one of stack, bars, orbit, cards" in e for e in errors_of(raw))
    raw = rich_plan()
    raw[5]["series"] = [{"label": "A", "value": -1}, {"label": "B", "value": 2}]
    assert any("non-negative numeric value" in e for e in errors_of(raw))


def test_at_most_four_object3d_scenes_per_video():
    raw = rich_plan()
    for at in (3, 5, 7):
        raw.insert(at, copy.deepcopy(raw[1]))  # four more object3d: five in all
    errs = errors_of(raw, strict=False)
    assert any(f"at most {MAX_OBJECT3D}" in e for e in errs)


def test_schema_stays_flat_and_mentions_the_rich_types():
    assert set(RICH_TYPES) <= set(SCENE_SCHEMA["properties"]["type"]["enum"])
    assert "anyOf" not in json.dumps(SCENE_SCHEMA)
    assert "object3d" in SCENE_SCHEMA["properties"]["kind"]["description"]
    assert "kinetic" in SCENE_SCHEMA["properties"]["lines"]["description"]


# --- style knobs ------------------------------------------------------------------------

def test_look_and_palette_knobs_validate():
    style, errs = validate_style({"look": "cinematic", "palette": ["#FF7A1A", "#22d3ee"], "accent": "#ff7a1a"})
    assert errs == [] and style["look"] == "cinematic" and style["palette"] == ["#ff7a1a", "#22d3ee"]
    assert is_cinematic(style) and not is_default(style)
    assert validate_style({"look": "noir"})[0] is None
    assert validate_style({"palette": "#ff7a1a"})[0] is None
    assert validate_style({"palette": ["#ff7a1a", "red"]})[0] is None
    assert validate_style({"palette": ["#111111"] * (V.MAX_PALETTE + 1)})[0] is None
    assert validate_style({"background": "mesh-gradient"})[0]["background"] == "mesh-gradient"
    assert is_default(validate_style({"look": "default", "palette": []})[0])
    assert DEFAULT_STYLE["look"] == "default" and DEFAULT_STYLE["palette"] == []


def test_plan_keeps_a_cinematic_style_on_its_scenes():
    raw = rich_plan()
    raw[2]["style"] = {"look": "cinematic", "palette": ["#ff7a1a"], "transition": "fade"}
    raw[3]["style"] = {"look": "cinematic", "palette": ["nope"]}
    plan = validate_plan(raw, strict=True)
    assert plan.scenes[2].fields["style"]["look"] == "cinematic"
    assert "style" not in plan.scenes[3].fields and any("style dropped" in r for r in plan.repairs)


# --- look -> theme wiring ------------------------------------------------------------------

def test_recipe_policy_switches_the_payload_to_the_cinematic_theme():
    plan = validate_plan(rich_plan(), strict=True)
    assert resolve_theme(plan, "light") == ("light", [])
    styled = apply_recipe_policy(plan, "cinematic-neon")
    assert styled == len(plan.scenes) - 2  # the middle scenes
    assert plan.scenes[0].fields.get("style") is None  # title and outro keep the template's timing...
    theme, palette = resolve_theme(plan, "light")
    assert theme == "cinematic" and palette == DEFAULT_GLOW_PALETTE  # ...but the ground is the whole video's
    payload = story_payload(plan, timed(plan), 30.0, "TLDR", 0.5, "light")
    assert payload["theme"] == "cinematic" and payload["palette"] == DEFAULT_GLOW_PALETTE
    assert all("cues" in s for s in payload["scenes"][1:-1])  # every rich scene reveals on the voice
    assert len(payload["scenes"][1]["cues"]) == 3 and len(payload["scenes"][5]["cues"]) == 2
    # the explicit theme is honoured on its own too
    assert resolve_theme(validate_plan(rich_plan(), strict=True), "cinematic")[0] == "cinematic"


def test_default_look_and_payload_are_unchanged(monkeypatch):
    """The daily job: no preset, no recipe applied, light theme -> exactly the old payload shape."""
    for key in ("VIDEO_STYLE", "VIDEO_THEME", "STYLE_RECIPE", "CHARACTER", "BACKGROUND_STYLE", "LOOK_PRESET"):
        monkeypatch.delenv(key, raising=False)
    assert looks.look_from_env() == {"style": "explainer", "character": "", "theme": "light", "background": "gameplay",
                                     "recipe": "auto", "preset": ""}
    assert not looks.cinematic_requested()
    plan = validate_plan(rich_plan()[:1] + [
        {"type": "stat", "say": LINE, "value": "5.1x", "label": "Top cost multiplier", "trend": [1.8, 3.2, 5.1]},
        {"type": "statement", "say": LINE, "text": "It depends on the task", "emphasis": "depends", "icon": "scale"},
        {"type": "chart", "say": LINE, "kind": "dots", "title": "Share", "unit": "%",
         "series": [{"label": "Team", "value": 12}, {"label": "Tie", "value": 88}], "highlight": 0},
        {"type": "list", "say": LINE, "title": "Three things", "items": ["A", "B", "C"]},
        {"type": "device", "say": LINE, "kind": "terminal", "label": "sh", "lines": ["$ run", "ok"]},
    ] + rich_plan()[-1:], strict=True)
    payload = story_payload(plan, timed(plan), 30.0, "TLDR", 0.5)
    assert payload["theme"] == "light" and "palette" not in payload
    assert set(payload) == {"theme", "kicker", "duration", "scenes", "captions"}
    assert all("style" not in s for s in payload["scenes"])


def test_cinematic_requested_from_theme_or_recipe(monkeypatch):
    assert looks.validate_look({"theme": "cinematic"})["theme"] == "cinematic"
    assert looks.cinematic_requested({"style": "explainer", "theme": "cinematic", "recipe": "auto"})
    assert looks.cinematic_requested({"style": "explainer", "theme": "light", "recipe": "cinematic-neon"})
    assert looks.cinematic_requested({"style": "explainer", "theme": "light", "recipe": "look-cinematic-glass"})
    assert not looks.cinematic_requested({"style": "explainer", "theme": "light", "recipe": "calm-fade"})
    assert not looks.cinematic_requested({"style": "explainer", "theme": "light", "recipe": "no-such-recipe"})
    assert not looks.cinematic_requested({"style": "avatar", "theme": "cinematic", "recipe": "cinematic-neon"})
    monkeypatch.setenv("STYLE_RECIPE", "cinematic-neon")
    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    assert looks.cinematic_requested()
    assert "cinematic" in looks.describe({"style": "explainer", "character": "", "theme": "cinematic", "recipe": "none"})


def test_writer_prompt_offers_rich_scenes_only_for_the_cinematic_look(monkeypatch):
    from newsletter_video import explainer_writer as ew

    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    monkeypatch.setenv("STYLE_RECIPE", "none")
    monkeypatch.delenv("VIDEO_THEME", raising=False)
    monkeypatch.delenv("LOOK_PRESET", raising=False)
    monkeypatch.setenv("CHARACTER", "")
    plain = ew._system_prompt()
    assert "renders in the cinematic look" not in plain and "Use them only when" in plain and "Look preset:" not in plain
    monkeypatch.setenv("VIDEO_THEME", "cinematic")
    rich = ew._system_prompt()
    assert "renders in the cinematic look" in rich and "object3d" in rich and "glass" in rich and "At most 4 object3d" in rich
    monkeypatch.delenv("VIDEO_THEME", raising=False)
    monkeypatch.setenv("LOOK_PRESET", "data-focus")
    data = ew._system_prompt()
    # data-focus is a 3D preset: it gets the rich scenes and the "prefer 3D" note
    assert "Look preset: Data focus" in data and "chart, stat, comparison, object3d" in data and "This look is a 3D look" in data
    monkeypatch.setenv("LOOK_PRESET", "quiet-editorial")
    quiet = ew._system_prompt()
    assert "Look preset: Quiet editorial" in quiet and "renders in the cinematic look" not in quiet and "3D look" not in quiet
    monkeypatch.setenv("LOOK_PRESET", "glow-type")
    assert "renders in the cinematic look" in ew._system_prompt()  # a cinematic preset switches the scenes on too


# --- the preset library ----------------------------------------------------------------

def test_presets_load_validate_and_map_to_recipes():
    presets = load_presets()
    ids = [p.id for p in presets]
    assert len(presets) >= 5 and len(set(ids)) == len(ids) and PRESETS_PATH.is_file()
    paper = get_preset("paper")
    assert paper.default and paper.look_fields() == {} and paper.recipe_id == "" and paper.registry == []
    glass = get_preset("cinematic-glass")
    assert glass.cinematic and glass.look_fields() == {"theme": "cinematic", "recipe": "look-cinematic-glass"}
    assert "mesh-gradient" in glass.registry and "object3d" in glass.emphasis
    recipes = builtin_recipes()
    for p in presets:
        if p.knobs:
            assert p.recipe_id in recipes and recipes[p.recipe_id].knobs == p.knobs and recipes[p.recipe_id].source == "preset"
    assert {r.id for r in preset_recipes()} == {p.recipe_id for p in presets if p.knobs}
    assert all(set(d) >= {"id", "name", "description", "look", "recipe", "knobs", "emphasis", "registry", "thumbnail"} for d in presets_json())
    assert get_preset("no-such") is None


def test_preset_resolves_into_theme_and_recipe_but_explicit_fields_win():
    env = {"VIDEO_STYLE": "explainer", "VIDEO_THEME": "light", "STYLE_RECIPE": "auto"}
    resolved = looks.resolve_look({"preset": "cinematic-glass"}, environ=env)
    assert resolved["theme"] == "cinematic" and resolved["recipe"] == "look-cinematic-glass" and resolved["preset"] == "cinematic-glass"
    resolved = looks.resolve_look({"preset": "cinematic-glass", "theme": "dark"}, environ=env)
    assert resolved["theme"] == "dark" and resolved["recipe"] == "look-cinematic-glass"  # the hand-set theme wins
    resolved = looks.resolve_look({"preset": "data-focus"}, {"recipe": "none"}, environ=env)
    assert resolved["theme"] == "dark" and resolved["recipe"] == "none"  # a later layer overrides the preset's recipe
    assert looks.look_from_env({**env, "LOOK_PRESET": "glow-type"})["recipe"] == "look-glow-type"
    assert looks.resolve_look({"preset": ""}, environ=env)["theme"] == "light"
    assert looks.resolve_look({"preset": "paper"}, environ=env) == {**looks.look_from_env(env), "preset": "paper"}
    with pytest.raises(looks.LookError):
        looks.validate_look({"preset": "sepia-dream"})
    assert looks.compact({"preset": "", "style": "explainer"}) == {"version": 1, "style": "explainer"}
    assert looks.compact({"preset": "glow-type"}) == {"version": 1, "preset": "glow-type"}
    assert "preset glow-type" in looks.describe(looks.resolve_look({"preset": "glow-type"}, environ=env))


def test_every_preset_recipe_switches_the_plan_and_the_default_does_not():
    for p in load_presets():
        plan = validate_plan(rich_plan(), strict=True)
        n = apply_recipe_policy(plan, p.recipe_id or "none")
        theme, palette = resolve_theme(plan, p.look.get("theme", "light"))
        if p.id == "paper":
            assert n == 0 and theme == "light" and palette == []
            continue
        assert n == len(plan.scenes) - 2
        assert theme == ("cinematic" if p.cinematic else p.look["theme"])
        if p.cinematic:
            assert palette == p.knobs["palette"]
