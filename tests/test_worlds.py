"""3D worlds (world3d.py): what every scene of a 3D look shows, chosen from its content."""
from __future__ import annotations

from newsletter_video import look as looks
from newsletter_video.explainer_builder import story_payload
from newsletter_video.looks import get_preset, load_presets
from newsletter_video.scene_plan import validate_plan
from newsletter_video.world3d import annotate, choose_compositions, keywords_of, numbers_in, object_for, phrase_chunks, scene_content

LINE = "This spoken line carries the beat with enough words to reach the budget for the video."


def statements_only() -> list[dict]:
    """A story with no diagram, chart, comparison or timeline: nothing to convert, everything still 3D."""
    return [
        {"type": "title", "say": LINE, "kicker": "AI CHIPS", "headline": "TSMC eyes AI memory chips", "icon": "chip"},
        {"type": "statement", "say": "Tesla now calls its AI Super Intelligence, and the label is meant to stick.", "label": "The rebrand", "text": "Tesla's new label: SI for Super Intelligence", "emphasis": "Super Intelligence", "icon": "brain"},
        {"type": "stat", "say": "The deal could be worth 21.5 billion dollars to the maker over three years.", "value": "$21.5B", "label": "deal value", "context": "over three years", "source": "", "trend": []},
        {"type": "quote", "say": LINE, "text": "Compute is the contest everyone is in", "attribution": "an analyst"},
        {"type": "list", "say": LINE, "title": "Who is in", "items": ["Nvidia wants supply", "TSMC has the fabs", "Huawei builds its own"]},
        {"type": "statement", "say": "Whoever controls the chips controls the pace of the whole AI industry from here.", "label": "The race", "text": "Whoever controls chips sets the pace", "emphasis": "chips", "icon": ""},
        {"type": "outro", "say": LINE, "text": "Who wins the compute race?"},
    ]


def per_line_of(plan):
    return [[(w, 0.3 * k + i * 4.0) for k, w in enumerate(s.say.split())] for i, s in enumerate(plan.scenes)]


def test_the_3d_presets_have_distinct_worlds_and_paper_has_none():
    worlds = {p.id: p.world for p in load_presets() if p.world}
    assert worlds == {"cinematic-glass": "hero", "depth-cards": "corridor", "data-focus": "metrics"}
    assert get_preset("paper").threed is False and get_preset("glow-type").world == ""


def test_world_of_follows_the_preset():
    assert looks.world_of({"style": "explainer", "preset": "data-focus"}) == "metrics"
    assert looks.world_of({"style": "explainer", "recipe": "look-depth-cards"}) == "corridor"
    assert looks.world_of({"style": "explainer", "preset": "cinematic-glass", "recipe": "look-data-focus"}) == "metrics"
    assert looks.world_of({"style": "explainer", "preset": "paper"}) == ""
    assert looks.world_of({"style": "avatar", "preset": "data-focus"}) == ""
    assert looks.threed_requested({"style": "explainer", "preset": "data-focus"})


def test_every_scene_of_any_story_gets_a_world_block_and_a_composition():
    plan = validate_plan(statements_only())
    flat = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0)
    assert "world" not in flat and all("w3" not in s for s in flat["scenes"])  # a flat look's payload is untouched
    for world in ("hero", "corridor", "metrics"):
        payload = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world=world)
        assert payload["world"] == world and all(s["w3"]["kind"] == s["type"] and s["w3"]["comp"] for s in payload["scenes"])
        assert not any("_words" in s for s in payload["scenes"])
    blocks = [s["w3"] for s in payload["scenes"]]
    assert blocks[0]["title"] == "TSMC eyes AI memory chips" and blocks[0]["icon"] == "chip"
    assert blocks[2]["value"] == "$21.5B" and blocks[1]["emph"] == "Super Intelligence"
    assert [i["text"] for i in blocks[4]["items"]][0] == "Nvidia wants supply"


def test_no_two_consecutive_scenes_share_a_composition_and_the_video_varies():
    plan = validate_plan(statements_only())
    for world in ("hero", "metrics"):
        payload = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world=world)
        comps = [s["w3"]["comp"] for s in payload["scenes"]]
        assert all(a != b for a, b in zip(comps, comps[1:])), comps
        assert len(set(comps)) >= 5, comps


def test_objects_come_from_what_the_scene_says():
    plan = validate_plan(statements_only())
    hero = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world="hero")
    comps = {s["type"] + str(i): s["w3"]["comp"] for i, s in enumerate(hero["scenes"])}
    assert comps["title0"] == "object:chip"  # the chip icon: a chip slab
    assert comps["statement1"] == "object:network"  # the brain icon: a node network
    assert comps["stat2"] == "monolith"  # one figure: a monolith, never a made-up chart
    metrics = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world="metrics")
    assert not any(s["w3"]["comp"].startswith("object:") for s in metrics["scenes"])  # figures and typography only
    assert object_for({"icon": "lock"}, "x") == "shield" and object_for({"icon": ""}, "Zipline raised a $21.5B valuation") == "coins"
    assert object_for({"icon": ""}, "A plain sentence about nothing in particular") == ""


def test_bars_only_with_two_or_more_figures_in_the_scene():
    plan = validate_plan(statements_only())
    for world in ("hero", "metrics"):
        payload = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world=world)
        for s in payload["scenes"]:
            if s["w3"]["comp"] in ("bars", "monolith", "gauge"):
                assert len(s["w3"]["numbers"]) >= (2 if s["w3"]["comp"] == "bars" else 1)
    two = {"kind": "statement", "title": "x", "items": [], "numbers": [{"label": "a", "value": 1, "shown": "1"}, {"label": "b", "value": 2, "shown": "2"}], "icon": "", "label": "", "lines": [], "say": "a b"}
    assert choose_compositions([two], "metrics") == ["bars"]


def test_numbers_in_the_words_become_figures_and_nothing_is_invented():
    nums = numbers_in("Zipline hit $21.5B while Shield reached $28.8B, up 30% on the year")
    assert [n["shown"] for n in nums][:2] == ["$21.5B", "$28.8B"] and nums[0]["value"] == 21.5e9
    plan = validate_plan(statements_only())
    assert scene_content(plan.scenes[2])["numbers"][0]["value"] == 21.5e9
    assert scene_content(plan.scenes[3])["numbers"] == []


def test_typography_helpers_only_use_words_of_the_line():
    assert phrase_chunks("one two three four five six", 3) == ["one two", "three four", "five six"]
    words = keywords_of("Whoever controls chips sets the pace of the industry", 3)
    assert set(words) <= set("Whoever controls chips sets the pace of the industry".split()) and "chips" in words or "controls" in words


def test_an_explicit_preset_on_the_command_line_beats_the_saved_look():
    from newsletter_video.look import resolve_look
    from newsletter_video.preview import _saved_look

    saved = {"style": "explainer", "theme": "cinematic", "recipe": "look-cinematic-glass", "preset": "cinematic-glass"}
    over = {"preset": "depth-cards"}
    look = resolve_look(_saved_look(saved, over), over)
    assert look["preset"] == "depth-cards" and look["recipe"] == "look-depth-cards" and look["theme"] == "dark"
    assert resolve_look(_saved_look(saved, None), None)["preset"] == "cinematic-glass"
    both = resolve_look(_saved_look(saved, {"preset": "depth-cards", "theme": "light"}), {"preset": "depth-cards", "theme": "light"})
    assert both["theme"] == "light" and both["recipe"] == "look-depth-cards"  # an explicit theme still wins over the preset


def test_paper_and_flat_presets_leave_the_plan_unchanged():
    from newsletter_video.styles.planner import apply_recipe_policy

    before = [s["type"] for s in validate_plan(statements_only()).to_list()]
    for policy in ("auto", "none", "look-quiet-editorial", "look-glow-type", "look-depth-cards", "look-data-focus", "look-cinematic-glass"):
        plan = validate_plan(statements_only())
        apply_recipe_policy(plan, policy)
        assert [s["type"] for s in plan.to_list()] == before, policy  # the worlds draw scenes; they never rewrite the plan
