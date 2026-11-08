"""3D worlds (world3d.py): what every scene of a 3D look shows, chosen from its content."""
from __future__ import annotations

from newsletter_video import look as looks
from newsletter_video.explainer_builder import story_payload
from newsletter_video.looks import get_preset, load_presets
from newsletter_video.scene_plan import validate_plan
from newsletter_video.world3d import choose_compositions, compose, keywords_of, numbers_in, object_for, phrase_chunks, scene_content

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


def block(kind="statement", say="A plain sentence about nothing in particular.", **kw):
    c = {"kind": kind, "label": "", "title": "", "emph": "", "value": "", "lines": [], "items": [], "numbers": [], "icon": "", "say": say}
    c.update(kw)
    return c


def test_every_scene_of_any_story_gets_a_world_block_and_a_composition():
    plan = validate_plan(statements_only())
    flat = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0)
    assert "world" not in flat and all("w3" not in s for s in flat["scenes"])  # a flat look's payload is untouched
    for world in ("hero", "corridor", "metrics"):
        payload = story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world=world)
        assert payload["world"] == world and all(s["w3"]["kind"] == s["type"] and s["w3"]["comp"] for s in payload["scenes"])
        assert not any("_words" in s for s in payload["scenes"])
    blocks = [s["w3"] for s in story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world="hero")["scenes"]]
    assert blocks[0]["title"] == "TSMC eyes AI memory chips" and blocks[0]["icon"] == "chip"
    assert blocks[2]["value"] == "$21.5B" and blocks[1]["emph"] == "Super Intelligence"


def test_a_scene_gets_3d_only_when_its_own_words_justify_it():
    plan = validate_plan(statements_only())
    hero = [s["w3"]["comp"] for s in story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world="hero")["scenes"]]
    # title and the last statement name chips; the stat is a money figure; everything else is a 2D scene
    assert hero == ["object:chip", "flat", "monolith", "flat", "flat", "object:chip", "flat"], hero
    metrics = [s["w3"]["comp"] for s in story_payload(plan, per_line_of(plan), 30.0, "TLDR", 1.0, "dark", world="metrics")["scenes"]]
    assert metrics == ["flat", "flat", "monolith", "flat", "flat", "flat", "flat"], metrics  # figures only: no decorative objects


def test_the_relevance_gate_per_composition():
    # a generic list is never a stack; layers are
    generic = block("list", items=[{"label": "", "text": "Nvidia wants supply"}, {"label": "", "text": "TSMC has the fabs"}])
    assert compose(generic, "hero") == "flat" and compose(generic, "metrics") == "flat"
    layered = block("list", say="The network has seven layers.", items=[{"label": "Layer 1", "text": "cables"}, {"label": "Layer 2", "text": "frames"}])
    assert compose(layered, "hero") == "stack"
    # an icon alone never summons an object; the scene's own words do
    assert compose(block(icon="globe", say="A firm said it was doing well."), "hero") == "flat"
    assert compose(block(icon="globe", say="The firm is going worldwide this year."), "hero") == "object:globe"
    assert compose(block(icon="globe", say="It trains on real-world data."), "hero") == "flat"
    assert compose(block(icon="chip", say="Nobody knows yet."), "hero") == "flat"
    assert compose(block(say="TSMC may build memory chips for Nvidia."), "hero") == "object:chip"
    assert compose(block(say="Zipline raised $21.5B from investors."), "hero") == "object:coins"
    assert compose(block(say="Servers fill the data center."), "hero") == "object:cubegrid"
    # the outro is calm 2D unless it is about a loop
    assert compose(block("outro", say="Comment below."), "hero") == "flat"
    assert compose(block("outro", say="That is the whole loop."), "hero") == "object:ring"
    # the metrics and corridor worlds never use decorative objects
    assert not compose(block(say="TSMC may build memory chips."), "metrics").startswith("object:")
    assert compose(block("statement"), "corridor") == "plane" and compose(block("stat"), "corridor") == "flat"
    assert compose(block("comparison", items=[{"label": "a", "text": "x"}, {"label": "b", "text": "y"}]), "hero") == "split"
    seq = block("timeline", say="First capture, then train, then ship.", items=[{"label": "a", "text": "x"}, {"label": "b", "text": "y"}, {"label": "c", "text": "z"}])
    assert compose(seq, "hero") == "path" and compose(seq, "metrics") == "flat"


def test_no_two_consecutive_3d_shots_are_the_same():
    chip = block(say="Chips again.")
    assert choose_compositions([chip, chip, chip], "hero") == ["object:chip", "flat", "object:chip"]


def test_bars_only_with_two_or_more_figures_in_the_scene():
    two = block("stat", numbers=[{"label": "a", "value": 1, "shown": "1"}, {"label": "b", "value": 2, "shown": "2"}])
    assert compose(two, "metrics") == "bars"
    lone_small = block("stat", numbers=[{"label": "cities", "value": 3, "shown": "3"}])
    assert compose(lone_small, "metrics") == "flat"  # a small count is not worth a monolith
    assert compose(block("statement", numbers=[{"label": "a", "value": 1, "shown": "1"}]), "metrics") == "flat"


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
