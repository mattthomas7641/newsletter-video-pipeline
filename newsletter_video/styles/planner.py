"""The planner: given a new story's scene plan and the style library,
choose per scene which recipe (and knob overrides) to apply, with reasons,
as a validated style plan the explainer builder consumes.

Retrieval first narrows the library to a short candidate list (recipes
that apply to the plan's scene types, library recipes ranked by measured
fidelity, the built-in presets as a floor, and anything the story's words
pull in through search); then one structured-output Claude call picks per
scene. Without a key, a deterministic planner applies the best-measured
reference's look. Whatever the source, the result goes through
`validate_style_plan`: an invalid answer never renders, a scene with an
invalid style keeps the default look.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable

from . import vision
from . import vocabulary as V
from .library import Library, open_library
from .recipes import Recipe, builtin_recipes
from .style_plan import STYLE_PLAN_SCHEMA, StylePlan, StylePlanError, is_default, validate_style, validate_style_plan

log = logging.getLogger("newsletter_video")

MAX_CANDIDATES = 24
DEFAULT_PLANNER_MODEL = "claude-sonnet-5-5"


def planner_model() -> str:
    return os.environ.get("STYLE_PLANNER_MODEL", "").strip() or os.environ.get("STYLE_VISION_MODEL", "").strip() or DEFAULT_PLANNER_MODEL


def candidates_for(scenes: list[dict], lib: Library, query: str = "", limit: int = MAX_CANDIDATES) -> list[Recipe]:
    """A short, diverse list of recipes worth offering for this plan."""
    recipes = lib.all_recipes()
    types = {s.get("type", "") for s in scenes}
    library_recipes = [r for r in recipes.values() if r.source != "builtin"]
    builtin = [r for r in recipes.values() if r.source == "builtin"]

    def fidelity(r: Recipe) -> float:
        return r.fidelity if r.fidelity is not None else 0.0

    chosen: list[Recipe] = []
    seen: set[str] = set()

    def take(r: Recipe) -> None:
        if r.id not in seen and len(chosen) < limit:
            chosen.append(r)
            seen.add(r.id)

    # whole-reference looks first, best measured first
    for r in sorted((r for r in library_recipes if r.id.endswith("-look")), key=lambda r: -fidelity(r)):
        take(r)
    # search hits from the story's words (title, kicker, labels)
    if query:
        for _score, _entry, hits in lib.search(query, limit=5):
            for r in hits:
                take(r)
    # per-scene recipes that apply to the plan's scene types, best first
    for r in sorted((r for r in library_recipes if not r.id.endswith("-look") and any(r.applies_to(t) for t in types)),
                    key=lambda r: -fidelity(r)):
        take(r)
    # built-in presets that apply, as the floor
    for r in builtin:
        if any(r.applies_to(t) for t in types):
            take(r)
    return chosen


def heuristic_plan(scenes: list[dict], candidates: list[Recipe], lib: Library) -> StylePlan:
    """No model: apply the best-measured reference's overall look to every
    scene it suits, with the template's default where it does not."""
    looks = [r for r in candidates if r.id.endswith("-look")]
    if not looks:
        return StylePlan(scenes=[None] * len(scenes), reasons=[""] * len(scenes), global_notes={"feel": "no reference looks available"})
    look = looks[0]
    raw = {"global": {"accent": look.knobs.get("accent", ""), "feel": f"the look of '{look.source}' (heuristic planner, no model)"},
           "scenes": []}
    for i, s in enumerate(scenes):
        if s.get("type") in ("title", "outro"):
            # keep the opening and closing scenes on the template's proven timing; only colour follows the reference
            raw["scenes"].append({"index": i, "recipe": "", "transition": "default", "entrance": "default", "hold": "default",
                                  "type_treatment": "default", "background": "default", "ease": "default", "speed": 1.0,
                                  "reason": "title and outro keep the default timing"})
            continue
        raw["scenes"].append({"index": i, "recipe": look.id, "transition": "default", "entrance": "default", "hold": "default",
                              "type_treatment": "default", "background": "default", "ease": "default", "speed": 1.0,
                              "reason": f"{look.name} (fidelity {look.fidelity if look.fidelity is not None else 'n/a'})"})
    return validate_style_plan(raw, len(scenes), {r.id: r.knobs for r in candidates})


PLANNER_SYSTEM = """\
You are the motion designer for a daily tech-news channel. Each video is a 40 s vertical explainer rendered \
by a data-driven composition: a title, 3-7 scenes (statement, stat, comparison, list, timeline, quote, chart, \
diagram, device) and an outro, each timed to the narration. Every scene can take a style: a recipe id from \
the candidate list (a preset of knobs learned from reference videos the channel owner likes, or a built-in \
preset) plus optional knob overrides. Knobs and their values:
- transition: {transitions}
- entrance: {entrances}
- hold: {holds}
- type_treatment: {types}
- background: {backgrounds}
- ease: {eases}
- speed: 0.5-2.0 (1.0 = the recipe's own)
'default' means the template's own, proven behaviour, which is always acceptable.

Rules
- Pick styles that fit each scene's content and type (a stat wants a count-up and a strong entrance; a quote \
wants calm; a list wants a stagger), and that read as one video: draw mainly on one reference's look, use at \
most three distinct recipes, and never 'accent-field' or 'flash' on two scenes in a row.
- Prefer library recipes with higher measured fidelity over built-ins when they fit; prefer '' (default) over \
a poor fit. The title keeps its first second clean (no flash, no accent-field); the outro stays 'default' or calm.
- Give a short reason per scene. Return every scene index exactly once."""


def _system() -> str:
    return PLANNER_SYSTEM.format(
        transitions=", ".join(V.TRANSITIONS), entrances=", ".join(V.ENTRANCES), holds=", ".join(V.HOLDS),
        types=", ".join(V.TYPE_TREATMENTS), backgrounds=", ".join(V.BACKGROUNDS), eases=", ".join(V.EASES))


def _scene_brief(i: int, s: dict) -> dict:
    keys = {"title": ("kicker", "headline"), "statement": ("label", "text", "emphasis"), "stat": ("value", "label"),
            "comparison": ("title",), "list": ("title", "items"), "timeline": ("title",), "quote": ("attribution",),
            "chart": ("title", "kind"), "diagram": ("title", "kind"), "device": ("title", "kind"), "outro": ("text",)}
    brief = {"index": i, "type": s.get("type"), "say": s.get("say", "")}
    for k in keys.get(s.get("type", ""), ()):
        if s.get(k):
            brief[k] = s[k]
    return brief


def _recipe_brief(r: Recipe) -> dict:
    return {"id": r.id, "name": r.name, "source": r.source, "fidelity": r.fidelity, "scene_types": r.scene_types,
            "knobs": r.knobs, "demonstrates": r.demonstrates[:8], "when": r.when_to_use[:160]}


def plan_styles(scenes: list[dict], lib: Library | None = None, call: Callable | None = None, title: str = "",
                use_model: bool | None = None) -> StylePlan | None:
    """The style plan for a scene plan (list of scene dicts, as
    ScenePlan.to_list() gives). None when the library is off or empty, so
    callers keep the current look untouched."""
    lib = lib if lib is not None else open_library()
    if lib is None or lib.is_empty():
        return None
    query = " ".join([title] + [str(s.get(k, "")) for s in scenes for k in ("kicker", "title", "label")])
    candidates = candidates_for(scenes, lib, query)
    if not candidates:
        return None
    recipes = {r.id: r.knobs for r in candidates}
    want_model = vision.vision_available() if use_model is None else use_model
    if call is None and want_model:
        call = vision._default_call(planner_model(), os.environ.get("STYLE_PLANNER_EFFORT", "low"))
    if call is None:
        plan = heuristic_plan(scenes, candidates, lib)
        plan.global_notes["planner"] = "heuristic"
        return plan
    content = [
        {"type": "text", "text": f"Video title: {title}\nScenes: " + json.dumps([_scene_brief(i, s) for i, s in enumerate(scenes)])},
        {"type": "text", "text": "Candidate recipes: " + json.dumps([_recipe_brief(r) for r in candidates])},
        {"type": "text", "text": "Choose the style for each scene."},
    ]
    try:
        data, usage = call(_system(), content, STYLE_PLAN_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - a failed call means the default look, never a failed video
        log.warning("Style planner call failed (%s: %s); keeping the default look", type(exc).__name__, exc)
        return None
    try:
        plan = validate_style_plan(data, len(scenes), recipes)
    except StylePlanError as exc:
        log.warning("Style plan rejected (%s); keeping the default look", exc)
        return None
    for note in plan.repairs:
        log.info("Style plan repair: %s", note)
    plan.global_notes["planner"] = "model"
    plan.global_notes["usage"] = usage
    plan.global_notes["cost_usd"] = vision.estimate_cost(planner_model(), usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    log.info("Style plan: %d of %d scenes styled with %s (%d in / %d out tokens)", plan.styled_count, len(scenes),
             ", ".join(plan.recipes_used) or "no recipes", usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return plan


def apply_style_plan(plan, style_plan: StylePlan | None) -> int:
    """Attach the validated styles to a ScenePlan's scenes in place; returns
    how many scenes were styled."""
    if style_plan is None:
        return 0
    count = 0
    for scene, style in zip(plan.scenes, style_plan.scenes):
        if style and not is_default(style):
            checked, errs = validate_style(style)
            if checked and not errs:
                scene.fields["style"] = checked
                count += 1
    return count


def apply_recipe_policy(plan, policy: str, lib: Library | None = None) -> int:
    """Re-style a validated ScenePlan in place according to a look's recipe
    policy, with no model call: "auto" keeps whatever styles the plan
    already carries, "none" strips them, and a recipe id applies that
    recipe's knobs to every middle scene it suits (title and outro keep the
    template's timing). Returns the number of styled scenes afterwards.
    An unknown recipe id strips styles and logs, never raises: a preview or
    render must still happen."""
    policy = (policy or "auto").strip().lower()
    if policy == "auto":
        return sum(1 for s in plan.scenes if s.fields.get("style"))
    for scene in plan.scenes:
        scene.fields.pop("style", None)
    if policy == "none":
        return 0
    lib = lib if lib is not None else open_library()
    recipes = lib.all_recipes() if lib is not None else builtin_recipes()
    recipe = recipes.get(policy)
    if recipe is None:
        log.warning("Style recipe %r is not in the library; rendering the default look", policy)
        return 0
    count = 0
    n = len(plan.scenes)
    for i, scene in enumerate(plan.scenes):
        if i == 0 or i == n - 1 or not recipe.applies_to(scene.type):
            continue
        candidate = {k: v for k, v in recipe.knobs.items() if k in DEFAULT_STYLE_KEYS}
        candidate["recipe"] = recipe.id
        style, errs = validate_style(candidate)
        if style and not errs and not is_default(style):
            scene.fields["style"] = style
            count += 1
    return count


DEFAULT_STYLE_KEYS = ("transition", "entrance", "hold", "type_treatment", "background", "ease", "accent", "speed", "stagger")


def style_plan_from_file(path, n_scenes: int, lib: Library | None = None) -> StylePlan:
    """A saved style plan (from `styles plan --out`) validated against the
    library's recipes."""
    lib = lib if lib is not None else open_library()
    recipes = {r.id: r.knobs for r in (lib.all_recipes().values() if lib else [])}
    raw = json.loads(open(path).read())
    return validate_style_plan(raw, n_scenes, recipes)
