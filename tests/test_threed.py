"""3D-ification (threed.py): the plan transform behind the 3D look presets."""
from __future__ import annotations

import copy

from newsletter_video import look as looks
from newsletter_video import threed
from newsletter_video.looks import get_preset, load_presets
from newsletter_video.scene_plan import MAX_OBJECT3D, validate_plan
from newsletter_video.styles.planner import apply_recipe_policy

LINE = "This spoken line carries the beat with enough words to reach the budget for the video."


def ordinary() -> list[dict]:
    return [
        {"type": "title", "say": LINE, "kicker": "NETWORKS", "headline": "How a photo crosses the internet", "icon": "globe"},
        {"type": "diagram", "say": LINE, "kind": "stack", "title": "The OSI stack",
         "nodes": [{"label": "Physical", "text": "cables and radio"}, {"label": "Network", "text": "IP addresses"},
                   {"label": "Application", "text": "the chat app"}]},
        {"type": "chart", "say": LINE, "kind": "bars", "title": "Bytes per piece", "unit": "B",
         "series": [{"label": "Header", "value": 40}, {"label": "Payload", "value": 1460}], "highlight": 1},
        {"type": "timeline", "say": LINE, "title": "Behind the app",
         "steps": [{"label": "The app", "text": "taps send"}, {"label": "The OS", "text": "opens a socket, then a long sentence"}]},
        {"type": "comparison", "say": LINE, "title": "Wi-Fi against fibre",
         "left": {"label": "Wi-Fi", "value": "500 Mb/s", "note": "at home", "number": 500},
         "right": {"label": "Fibre", "value": "10 Gb/s", "note": "under the sea", "number": 10000}},
        {"type": "outro", "say": LINE, "text": "Which layer surprised you?"},
    ]


def test_threeify_turns_suitable_scenes_into_3d_and_keeps_the_words():
    plan = validate_plan(ordinary())
    say = [s.say for s in plan.scenes]
    n = threed.threeify_plan(plan)
    types = [s.type + ":" + s.fields.get("kind", "") for s in plan.scenes]
    assert n == 4 and types[0].startswith("title") and types[-1].startswith("outro")
    assert types[1] == "object3d:stack" and types[2] == "object3d:bars" and types[3] == "depth:" and types[4] == "object3d:bars"
    assert [s.say for s in plan.scenes] == say  # the narration is never touched
    bars = plan.scenes[4].fields["series"]
    assert [p["value"] for p in bars] == [500, 10000] and plan.scenes[4].fields["highlight"] == 1
    # the long step text was shortened to the depth cap, not dropped
    assert all(len(n_["text"]) <= 30 for n_ in plan.scenes[3].fields["nodes"])
    validate_plan(plan.to_list())  # the result is a valid plan


def test_hub_diagram_becomes_an_orbit_and_flow_a_depth():
    raw = ordinary()
    raw[1] = {"type": "diagram", "say": LINE, "kind": "hub", "title": "Who talks", "center": "The photo",
              "nodes": [{"label": "Router", "text": ""}, {"label": "Server", "text": ""}, {"label": "Phone", "text": ""}]}
    plan = validate_plan(raw)
    threed.threeify_plan(plan)
    assert plan.scenes[1].type == "object3d" and plan.scenes[1].fields["kind"] == "orbit"
    assert plan.scenes[1].fields["center"] == "The photo"


def test_threeify_respects_limit_and_object3d_cap():
    plan = validate_plan(ordinary())
    assert threed.threeify_plan(plan, limit=2) == 2
    assert sum(s.type in ("object3d", "depth") for s in plan.scenes) == 2
    again = validate_plan(ordinary())
    threed.threeify_plan(again, limit=99)
    assert sum(s.type == "object3d" for s in again.scenes) <= MAX_OBJECT3D


def test_threeify_never_makes_three_identical_layouts_in_a_row():
    tl = ordinary()[3]
    flow = {"type": "diagram", "say": LINE, "kind": "flow", "title": "Path",
            "nodes": [{"label": "A", "text": "one"}, {"label": "B", "text": "two"}]}
    raw = [ordinary()[0], copy.deepcopy(tl), copy.deepcopy(tl), flow, ordinary()[-1]]
    plan = validate_plan(raw)
    threed.threeify_plan(plan, limit=9)
    types = [s.type for s in plan.scenes]
    assert types[1:4] == ["depth", "timeline", "depth"]  # converting the middle one would make a run of three
    validate_plan(plan.to_list())


def test_the_3d_presets_are_flagged_and_paper_is_not():
    flagged = {p.id for p in load_presets() if p.threed}
    assert flagged == {"cinematic-glass", "depth-cards", "data-focus"}
    assert get_preset("paper").threed is False and get_preset("glow-type").threed is False


def test_a_3d_preset_recipe_threeifies_through_the_recipe_policy():
    for preset in ("cinematic-glass", "depth-cards", "data-focus"):
        plan = validate_plan(ordinary())
        apply_recipe_policy(plan, f"look-{preset}")
        assert sum(s.type in ("object3d", "depth") for s in plan.scenes) >= 3, preset
        assert all(s.fields.get("style") for s in plan.scenes[1:-1] if s.type in ("object3d", "depth"))


def test_paper_and_flat_presets_leave_the_plan_unchanged():
    before = validate_plan(ordinary()).to_list()
    for policy in ("auto", "none", "look-quiet-editorial", "look-glow-type"):
        plan = validate_plan(ordinary())
        apply_recipe_policy(plan, policy)
        types = [s["type"] for s in plan.to_list()]
        assert types == [s["type"] for s in before], policy


def test_threed_requested_follows_the_preset():
    assert looks.threed_requested({"style": "explainer", "preset": "data-focus"})
    assert looks.threed_requested({"style": "explainer", "recipe": "look-depth-cards"})
    assert not looks.threed_requested({"style": "explainer", "preset": "paper"})
    assert not looks.threed_requested({"style": "avatar", "preset": "data-focus"})
