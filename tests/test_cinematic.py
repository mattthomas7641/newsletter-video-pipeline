"""The cinematic look and the rich scene types: validator schemas, style
knobs (look, palette), the reference -> recipe mapping, the theme
resolution in the story payload, the writer prompt switch, and the proof
that the daily default is unchanged by all of it. No network, no media."""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from newsletter_video import look as looks
from newsletter_video.explainer_builder import resolve_theme, story_payload
from newsletter_video.scene_plan import (
    MAX_OBJECT3D,
    RICH_TYPES,
    SCENE_SCHEMA,
    SCENE_TYPES,
    VISUAL_TYPES,
    ScenePlanError,
    validate_plan,
)
from newsletter_video.styles import frames as fr
from newsletter_video.styles import vocabulary as V
from newsletter_video.styles.analysis import derive_look, derive_tags, finish_analysis
from newsletter_video.styles.planner import apply_recipe_policy
from newsletter_video.styles.recipes import (
    DEFAULT_GLOW_PALETTE,
    builtin_recipes,
    cinematic_fits,
    look_knobs,
    recipes_from_analysis,
)
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


def test_at_most_two_object3d_scenes_per_video():
    raw = rich_plan()
    raw.insert(3, copy.deepcopy(raw[1]))
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
    assert is_default(validate_style({"look": "default", "palette": []})[0])
    assert DEFAULT_STYLE["look"] == "default" and DEFAULT_STYLE["palette"] == []


def test_plan_keeps_a_cinematic_style_on_its_scenes():
    raw = rich_plan()
    raw[2]["style"] = {"look": "cinematic", "palette": ["#ff7a1a"], "transition": "fade"}
    raw[3]["style"] = {"look": "cinematic", "palette": ["nope"]}
    plan = validate_plan(raw, strict=True)
    assert plan.scenes[2].fields["style"]["look"] == "cinematic"
    assert "style" not in plan.scenes[3].fields and any("style dropped" in r for r in plan.repairs)


# --- the reference -> recipe mapping --------------------------------------------------

def _dark_analysis(three_d: bool = False, glow=None) -> dict:
    analysis = {
        "scenes": [{"index": 0, "start": 0.0, "end": 3.0, "duration": 3.0, "transition_in": "fade",
                    "motion": {"events": [{"t0": 0.1, "t1": 0.6, "kind": "appear", "easing": "ease-out", "sweep": None, "slide": None, "area": 0.2}],
                               "stagger": None, "drift": {"dx": 0, "dy": 0}, "loop_period": None, "continuous_share": 0.0},
                    "layout": {"text_like": False}, "palette": {"theme": "dark"}}],
        "cadence": {"pace": "steady", "transition_mix": {"dissolve": 1}},
        "palette": {"theme": "dark", "background": "#020407", "accent": "#0f182c", "colour_feel": "multi-hue", "contrast": 19.0},
        "audio": {"has_audio": True, "speech": True},
        "vision": {"overall": {"feel": ["3d", "neon"] if three_d else ["minimal"], "tags": []}, "scenes": {}} if three_d else None,
        "look": {"glow_palette": glow or []},
    }
    return analysis


def test_glow_palette_finds_neon_strokes_on_a_dark_ground():
    frame = np.zeros((120, 120, 3), dtype=np.uint8) + 4  # near-black
    frame[20:24, :, :] = [255, 120, 20]  # an orange stroke
    frame[60:62, :, :] = [40, 200, 255]  # a thinner cyan one
    palette = fr.glow_palette(np.stack([frame, frame]))
    assert len(palette) == 2 and palette[0].startswith("#f") and palette[1].endswith("f5")
    assert fr.glow_palette(np.full((40, 40, 3), 240, dtype=np.uint8)) == []  # paper: nothing glows


def test_derive_look_records_dark_ground_glow_and_3d():
    look = derive_look(_dark_analysis(three_d=True, glow=["#f58b45"]))
    assert look["ground"] == "dark" and look["glow"] and look["three_d"] and look["cinematic"]
    assert look["glow_palette"] == ["#f58b45"] and look["background"] == "#020407"
    light = derive_look({"palette": {"theme": "light", "background": "#f6f4ee"}, "cadence": {}})
    assert light["ground"] == "light" and not light["cinematic"] and light["glow_palette"] == []
    a = _dark_analysis(three_d=True, glow=["#f58b45"])
    finish_analysis(a)
    assert {"cinematic", "glow", "3d", "dark"} <= set(a["tags"]) and "glow colours #f58b45" in a["summary"]
    assert "cinematic" in derive_tags(a)


def test_cinematic_fits_and_look_knobs():
    assert cinematic_fits({"cinematic": False}) == []
    assert cinematic_fits({"cinematic": True, "three_d": False}) == ["kinetic", "glass", "depth"]
    assert cinematic_fits({"cinematic": True, "three_d": True})[0] == "object3d"
    assert look_knobs({"cinematic": False}, None) == {}
    knobs = look_knobs({"cinematic": True, "glow_palette": ["#f58b45", "#6f56f5"]}, {"accent": "#0f182c"})
    assert knobs == {"look": "cinematic", "accent": "#f58b45", "palette": ["#f58b45", "#6f56f5"]}
    assert look_knobs({"cinematic": True, "glow_palette": []}, None)["palette"] == DEFAULT_GLOW_PALETTE


def test_recipes_from_a_dark_3d_reference_carry_the_cinematic_look():
    recipes = recipes_from_analysis("ref", _dark_analysis(three_d=True, glow=["#f58b45", "#6f56f5"]))
    look = recipes[0]
    assert look.id == "ref-look" and look.cinematic and look.knobs["accent"] == "#f58b45"
    assert look.knobs["palette"] == ["#f58b45", "#6f56f5"] and look.fits[0] == "object3d"
    assert "cinematic" in look.demonstrates and "3d" in look.demonstrates and "renders in the cinematic look" in look.description
    assert all(r.cinematic for r in recipes)  # the per-scene recipes agree with the whole-reference look
    for r in recipes:
        style, errs = validate_style(dict(r.knobs))
        assert errs == [], (r.id, errs)
    # a paper-like reference maps to nothing new
    light = {"scenes": [], "palette": {"theme": "light", "background": "#ffffff"}, "cadence": {}}
    assert recipes_from_analysis("paper", light) == []
    assert builtin_recipes()["cinematic-neon"].cinematic and builtin_recipes()["cinematic-neon"].fits == list(RICH_TYPES)
    assert not builtin_recipes()["calm-fade"].cinematic


# --- look -> theme wiring ------------------------------------------------------------------

def test_recipe_policy_switches_the_payload_to_the_cinematic_theme():
    plan = validate_plan(rich_plan(), strict=True)
    assert resolve_theme(plan, "light") == ("light", [])
    styled = apply_recipe_policy(plan, "cinematic-neon")
    assert styled == len(plan.scenes) - 2  # the middle scenes
    assert plan.scenes[0].fields.get("style") is None  # title and outro keep the template's timing...
    theme, palette = resolve_theme(plan, "light")
    assert theme == "cinematic" and palette == DEFAULT_GLOW_PALETTE  # ...but the ground is the whole video's
    words = [[(w, 0.3 * k + i * 4.0) for k, w in enumerate(s.say.split())] for i, s in enumerate(plan.scenes)]
    payload = story_payload(plan, words, 30.0, "TLDR", 0.5, "light")
    assert payload["theme"] == "cinematic" and payload["palette"] == DEFAULT_GLOW_PALETTE
    assert all("cues" in s for s in payload["scenes"][1:-1])  # every rich scene reveals on the voice
    assert len(payload["scenes"][1]["cues"]) == 3 and len(payload["scenes"][5]["cues"]) == 2
    # the explicit theme is honoured on its own too
    assert resolve_theme(validate_plan(rich_plan(), strict=True), "cinematic")[0] == "cinematic"


def test_default_look_and_payload_are_unchanged(monkeypatch):
    """The daily job: no recipe applied, light theme -> exactly the old payload shape."""
    for key in ("VIDEO_STYLE", "VIDEO_THEME", "STYLE_RECIPE", "CHARACTER", "BACKGROUND_STYLE"):
        monkeypatch.delenv(key, raising=False)
    assert looks.look_from_env() == {"style": "explainer", "character": "", "theme": "light", "background": "gameplay", "recipe": "auto"}
    assert not looks.cinematic_requested()
    plan = validate_plan(rich_plan()[:1] + [
        {"type": "stat", "say": LINE, "value": "5.1x", "label": "Top cost multiplier", "trend": [1.8, 3.2, 5.1]},
        {"type": "statement", "say": LINE, "text": "It depends on the task", "emphasis": "depends", "icon": "scale"},
        {"type": "chart", "say": LINE, "kind": "dots", "title": "Share", "unit": "%",
         "series": [{"label": "Team", "value": 12}, {"label": "Tie", "value": 88}], "highlight": 0},
    ] + rich_plan()[-1:], strict=True)
    words = [[(w, 0.3 * k + i * 4.0) for k, w in enumerate(s.say.split())] for i, s in enumerate(plan.scenes)]
    payload = story_payload(plan, words, 20.0, "TLDR", 0.5)
    assert payload["theme"] == "light" and "palette" not in payload
    assert set(payload) == {"theme", "kicker", "duration", "scenes", "captions"}


def test_cinematic_requested_from_theme_or_recipe(monkeypatch):
    assert looks.validate_look({"theme": "cinematic"})["theme"] == "cinematic"
    assert looks.cinematic_requested({"style": "explainer", "theme": "cinematic", "recipe": "auto"})
    assert looks.cinematic_requested({"style": "explainer", "theme": "light", "recipe": "cinematic-neon"})
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
    monkeypatch.setenv("CHARACTER", "")
    plain = ew._system_prompt()
    assert "Look: cinematic" not in plain and "Use them only when" in plain
    monkeypatch.setenv("VIDEO_THEME", "cinematic")
    rich = ew._system_prompt()
    assert "Look: cinematic" in rich and "object3d" in rich and "glass" in rich and "At most 2 object3d" in rich


def test_vision_cost_estimate_is_in_the_right_ballpark():
    from newsletter_video.styles.vision import estimate_pass_cost

    est = estimate_pass_cost(25, (303, 538), {"model": "claude-sonnet-5-5", "max_scenes": 24, "scenes_per_call": 4, "frames_per_scene": 2},
                             sheet_size=(1024, 1024))
    assert est["scenes"] == 24 and est["calls"] == 7
    assert 0.10 <= est["usd"] <= 0.30  # the measured run cost $0.18
