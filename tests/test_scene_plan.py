"""Scene-plan validator tests (pytest, no network, no media).

    ./venv/bin/python -m pytest tests/
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newsletter_video.scene_plan import (  # noqa: E402
    CAPS,
    PLAN_WORDS,
    SCENE_SCHEMA,
    ScenePlanError,
    plan_from_script,
    validate_plan,
)

SAY = "This line is spoken while the scene is on screen, six words or more."


def good_plan() -> list[dict]:
    return [
        {"type": "title", "say": "Agent teams cost up to five times more. Do they actually win?",
         "kicker": "AI AGENTS", "headline": "Agent teams cost 5x more. Worth it?"},
        {"type": "statement", "say": SAY, "label": "The test",
         "text": "Single agents vs. agent teams, head to head", "emphasis": "agent teams"},
        {"type": "stat", "say": "Teams cost between one point eight and five point one times as much.",
         "value": "5.1x", "label": "Top cost multiplier for teams", "context": "Low end: 1.8x", "source": "Vals AI"},
        {"type": "comparison", "say": SAY, "title": "Cost vs. payoff",
         "left": {"label": "Single", "value": "1x", "note": "baseline", "number": 1},
         "right": {"label": "Team", "value": "5.1x", "note": "no score gain", "number": 5.1}},
        {"type": "list", "say": "Whether a team pays off depends on the model, the task, and the budget.",
         "title": "It depends on", "items": ["The model", "The task", "The budget"]},
        {"type": "timeline", "say": SAY, "title": "Claude Code Projects",
         "steps": [{"label": "Sept", "text": "Waitlist opens"}, {"label": "Now", "text": "Every Pro and Max user is in"}]},
        {"type": "chart", "say": SAY, "title": "Cost multiplier by model", "unit": "x",
         "series": [{"label": "Sol low", "value": 1.8}, {"label": "Opus 5.5", "value": 5.1}], "highlight": 1},
        {"type": "quote", "say": SAY, "text": "Whether a team pays off depends on the model, task, and budget.",
         "attribution": "Vals AI case study"},
        {"type": "outro", "say": "Do you use multiple agents? Tell us if it's worth it.",
         "text": "Are agent teams worth the bill?"},
    ]


def errors_of(raw) -> list[str]:
    with pytest.raises(ScenePlanError) as info:
        validate_plan(raw)
    return info.value.errors


def test_good_plan_validates_and_normalizes():
    plan = validate_plan(good_plan())
    assert [s.type for s in plan.scenes] == ["title", "statement", "stat", "comparison", "list",
                                             "timeline", "chart", "quote", "outro"]
    assert plan.repairs == []
    assert plan.lines[0].startswith("Agent teams")
    assert PLAN_WORDS[0] <= plan.word_count <= PLAN_WORDS[1]
    chart = plan.scenes[6].fields
    assert chart["series"][1]["value"] == 5.1 and chart["highlight"] == 1
    assert plan.to_list()[0]["headline"] == "Agent teams cost 5x more. Worth it?"


def test_whitespace_is_normalized_not_rejected():
    raw = good_plan()
    raw[1]["text"] = "  Single agents   vs. agent teams,\nhead to head "
    plan = validate_plan(raw)
    assert plan.scenes[1].fields["text"] == "Single agents vs. agent teams, head to head"


@pytest.mark.parametrize("index,key", [(0, "headline"), (0, "kicker"), (1, "text"), (2, "value"),
                                       (2, "label"), (7, "text"), (8, "text")])
def test_text_over_cap_is_rejected(index, key):
    raw = good_plan()
    kind = raw[index]["type"]
    raw[index][key] = "x" * (CAPS[kind][key] + 1)
    errs = errors_of(raw)
    assert any(f"'{key}'" in e and "cap is" in e for e in errs), errs


def test_missing_required_field_is_rejected():
    raw = good_plan()
    del raw[2]["value"]
    assert any("'value' is missing" in e for e in errors_of(raw))


def test_unknown_type_is_rejected():
    raw = good_plan()
    raw[3]["type"] = "hero"
    assert any("unknown type 'hero'" in e for e in errors_of(raw))


def test_first_and_last_scene_shape():
    raw = good_plan()
    raw[0], raw[1] = raw[1], raw[0]
    assert any("first scene must be a 'title'" in e for e in errors_of(raw))
    raw = good_plan()
    raw.append({"type": "statement", "say": SAY, "text": "After the outro"})
    assert any("last scene must be an 'outro'" in e for e in errors_of(raw))
    raw = good_plan()
    raw.insert(4, {"type": "title", "say": SAY, "kicker": "AGAIN", "headline": "A second title"})
    assert any("only the first scene may be a title" in e for e in errors_of(raw))


def test_scene_count_bounds():
    raw = good_plan()[:1] + good_plan()[-1:]  # title + outro only
    assert any("between title and outro" in e for e in errors_of(raw))


def test_word_budget_bounds():
    raw = good_plan()
    raw[1]["say"] = " ".join(["word"] * 41)  # per-scene cap
    assert any("'say' has 41 words" in e for e in errors_of(raw))
    raw = good_plan()
    for scene in raw[1:-1]:
        scene["say"] = " ".join(["word"] * 20)
    assert any("spoken text is" in e and "allowed" in e for e in errors_of(raw))


def test_emphasis_not_in_text_is_dropped_as_repair():
    raw = good_plan()
    raw[1]["emphasis"] = "nowhere"
    plan = validate_plan(raw)
    assert plan.scenes[1].fields["emphasis"] == ""
    assert any("emphasis" in r for r in plan.repairs)


def test_list_and_timeline_bounds():
    raw = good_plan()
    raw[4]["items"] = ["only one"]
    assert any("'items' has 1 entries" in e for e in errors_of(raw))
    raw = good_plan()
    raw[4]["items"] = ["a", "b", "c", "d", "e"]
    assert any("'items' has 5 entries" in e for e in errors_of(raw))
    raw = good_plan()
    raw[4]["items"] = ["ok", "x" * (CAPS["list"]["item"] + 1)]
    assert any("item 2 is" in e for e in errors_of(raw))
    raw = good_plan()
    raw[5]["steps"] = [{"label": "Now", "text": "one step only"}]
    assert any("'steps' has 1 entries" in e for e in errors_of(raw))


def test_chart_series_rules():
    raw = good_plan()
    raw[6]["series"] = [{"label": "Only", "value": 1}]
    assert any("'series' has 1 entries" in e for e in errors_of(raw))
    raw = good_plan()
    raw[6]["series"][0]["value"] = "lots"
    assert any("non-negative numeric value" in e for e in errors_of(raw))
    raw = good_plan()
    raw[6]["highlight"] = 7  # out of range: repaired to the largest bar
    plan = validate_plan(raw)
    assert plan.scenes[6].fields["highlight"] == 1
    assert any("highlight" in r for r in plan.repairs)


def test_comparison_bars_need_two_positive_numbers():
    raw = good_plan()
    raw[3]["left"]["number"] = 0
    raw[3]["right"]["number"] = 0
    plan = validate_plan(raw)
    assert plan.scenes[3].fields["left"]["number"] is None  # no bars, no complaint
    assert plan.repairs == []
    raw = good_plan()
    raw[3]["right"]["number"] = None
    plan = validate_plan(raw)
    assert plan.scenes[3].fields["left"]["number"] is None
    assert any("bars dropped" in r for r in plan.repairs)
    raw = good_plan()
    del raw[3]["right"]
    assert any("'right' must be an object" in e for e in errors_of(raw))


def test_no_three_identical_data_scenes_in_a_row_but_statements_are_fine():
    raw = good_plan()
    stat = raw[2]
    raw[3:5] = [copy.deepcopy(stat), copy.deepcopy(stat)]
    assert any("more than 2 'stat' scenes in a row" in e for e in errors_of(raw))
    raw = good_plan()
    statement = dict(raw[1], say="A short spoken line for this card.")
    raw[2:5] = [copy.deepcopy(statement), copy.deepcopy(statement), copy.deepcopy(statement)]
    validate_plan(raw)  # statements are the plain card; a run of them is allowed


def test_all_errors_are_reported_together():
    raw = good_plan()
    raw[0]["headline"] = "x" * 100
    raw[2]["value"] = ""
    raw[6]["type"] = "pie"
    errs = errors_of(raw)
    assert len(errs) >= 3


def test_rejects_non_list_and_empty():
    assert errors_of({}) and errors_of([]) and errors_of(None)
    assert any("not an object" in e for e in errors_of([{"type": "title"}, "nope", {"type": "outro"}]))


def test_plan_from_classic_script_dict():
    script = {
        "title": "AI Agent Teams Cost 5x More. Are They Worth It?",
        "badge": "AI AGENTS",
        "hook": "Agent teams cost up to five times more. Do they win?",
        "beats": [
            {"say": "Researchers tested two models on a coding benchmark, as single agents and as teams.",
             "card": "Single agents vs teams", "highlight": "teams", "kind": "fact", "stat": ""},
            {"say": "Teams cost up to five point one times as much.",
             "card": "the max cost multiplier for teams", "highlight": "cost multiplier", "kind": "stat", "stat": "5.1x"},
            {"say": "Yet there was no statistically significant score difference on the benchmark.",
             "card": "Almost no real score gain", "highlight": "no real score gain", "kind": "fact", "stat": ""},
            {"say": "Whether a team pays off depends on the model, the task, and the budget you have.",
             "card": "It depends on model, task, budget", "highlight": "It depends", "kind": "fact", "stat": ""},
        ],
        "outro": "Do you use multiple agents? Tell us if it's worth it.",
    }
    plan = plan_from_script(script)
    assert [s.type for s in plan.scenes] == ["title", "statement", "stat", "statement", "statement", "outro"]
    assert plan.scenes[0].fields["kicker"] == "AI AGENTS"
    assert plan.scenes[2].fields == {"value": "5.1x", "label": "the max cost multiplier for teams", "context": "", "source": ""}
    assert plan.scenes[1].fields["emphasis"] == "teams"
    assert plan.lines == [script["hook"]] + [b["say"] for b in script["beats"]] + [script["outro"]]


def test_plan_from_script_shortens_long_titles_at_word_boundaries():
    script = {
        "title": "A very long headline that would never fit on the title card of a vertical video at all",
        "badge": "big tech & startups and more",
        "hook": "Here is a hook that is long enough to count as a line.",
        "beats": [{"say": "Beat one is spoken here with enough words to pass the floor of the budget.",
                   "card": "Card", "highlight": None, "kind": "fact", "stat": ""}] * 3,
        "outro": "Follow for tomorrow's tech news in under a minute.",
    }
    plan = plan_from_script(script)
    headline = plan.scenes[0].fields["headline"]
    assert len(headline) <= CAPS["title"]["headline"] and not headline.endswith(" ")
    assert len(plan.scenes[0].fields["kicker"]) <= CAPS["title"]["kicker"]


def test_schema_is_flat_and_strict():
    """The writer's structured-output schema: one object for every type
    (no anyOf), every property required, no extras."""
    assert SCENE_SCHEMA["type"] == "object"
    assert SCENE_SCHEMA["additionalProperties"] is False
    assert set(SCENE_SCHEMA["required"]) == set(SCENE_SCHEMA["properties"])
    assert "anyOf" not in json.dumps(SCENE_SCHEMA)
    assert SCENE_SCHEMA["properties"]["type"]["enum"][0] == "title"
