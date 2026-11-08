"""3D-ification: a deterministic plan transform for the looks that want depth.

The 3D looks (cinematic-glass, depth-cards, data-focus) should show several
Three.js scenes in an ordinary story, whether or not the writer chose them.
`threeify_plan` rewrites suitable middle scenes of a validated ScenePlan, in
place and with no model call, into `object3d` or `depth` scenes carrying the
same words and numbers:

    diagram stack            -> object3d stack   (glowing layers over the grid floor)
    diagram hub              -> object3d orbit   (satellites circling a core)
    chart (bars/columns/...) -> object3d bars    (3D columns on the grid floor)
    comparison with numbers  -> object3d bars    (two columns)
    comparison, timeline,
    diagram flow / funnel    -> depth            (the camera dollies through glass planes)

Each rewrite is re-validated by scene_plan (caps, bounds, run limits); a
scene that would not validate, or would make a run of three identical
layouts, is left as it was. The title and the outro are never touched, the
spoken line (`say`) is never changed, and at most `limit` scenes are
rewritten, spread over the video so the 3D scenes are not all adjacent.
Scenes that already are 3D count toward the limit.
"""
from __future__ import annotations

import logging

from . import scene_plan as sp

log = logging.getLogger("newsletter_video")

DEFAULT_LIMIT = 4  # object3d is capped at MAX_OBJECT3D; depth is cheap but the video should not become one effect
THREE_D_TYPES = ("object3d", "depth")


def _short(text: str, cap: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= cap else sp._shorten(text, cap)


def _nodes(items, label_cap: int = 16, text_cap: int = 30):
    return [{"label": _short(i.get("label", ""), label_cap), "text": _short(i.get("text", ""), text_cap)} for i in items]


def _rewrite(scene: sp.Scene) -> dict | None:
    """The raw dict of the 3D scene `scene` becomes, or None if it does not suit."""
    f, base = scene.fields, {"say": scene.say}
    if "style" in f:
        base["style"] = f["style"]
    if scene.type == "diagram":
        nodes = _nodes(f.get("nodes", []))
        kind = f.get("kind")
        if kind == "stack":
            return {**base, "type": "object3d", "kind": "stack", "title": f.get("title", ""), "nodes": nodes}
        if kind == "hub":
            return {**base, "type": "object3d", "kind": "orbit", "title": f.get("title", ""),
                    "center": _short(f.get("center", ""), 16), "nodes": nodes}
        return {**base, "type": "depth", "title": f.get("title", ""), "label": "", "nodes": nodes}
    if scene.type == "chart" and f.get("series"):
        return {**base, "type": "object3d", "kind": "bars", "title": f.get("title", ""), "unit": f.get("unit", ""),
                "series": [dict(p) for p in f["series"]], "highlight": f.get("highlight", 0)}
    if scene.type == "comparison":
        left, right = f.get("left"), f.get("right")
        if not left or not right:
            return None
        if left.get("number") and right.get("number"):
            return {**base, "type": "object3d", "kind": "bars", "title": f.get("title", ""), "unit": "",
                    "series": [{"label": _short(left["label"], 11), "value": left["number"]},
                               {"label": _short(right["label"], 11), "value": right["number"]}],
                    "highlight": 0 if left["number"] >= right["number"] else 1}
        return {**base, "type": "depth", "title": f.get("title", ""), "label": "",
                "nodes": [{"label": _short(s["label"], 16), "text": _short(" ".join(x for x in (s.get("value"), s.get("note")) if x), 30)}
                          for s in (left, right)]}
    if scene.type == "timeline" and f.get("steps"):
        return {**base, "type": "depth", "title": f.get("title", ""), "label": "", "nodes": _nodes(f["steps"], 16, 30)}
    return None


_PRIORITY = {"diagram": 0, "chart": 1, "comparison": 2, "timeline": 3}


def threeify_plan(plan: sp.ScenePlan, limit: int = DEFAULT_LIMIT) -> int:
    """Rewrites suitable scenes of `plan` into 3D scenes; returns how many it
    changed. Never raises: a scene that cannot be rewritten is kept."""
    scenes = plan.scenes
    n = len(scenes)
    have = sum(s.type in THREE_D_TYPES for s in scenes)
    room = max(0, limit - have)
    object3d_room = max(0, sp.MAX_OBJECT3D - sum(s.type == "object3d" for s in scenes))
    changed = 0
    order = sorted((i for i in range(1, n - 1) if scenes[i].type in _PRIORITY), key=lambda i: (_PRIORITY[scenes[i].type], i))
    for i in order:
        if changed >= room:
            break
        raw = _rewrite(scenes[i])
        if raw is None:
            continue
        if raw["type"] == "object3d" and object3d_room <= 0:
            continue
        new, errors, repairs = sp._validate_scene(i, raw)
        if new is None or errors:
            log.info("3D look: scene %d (%s) not converted: %s", i + 1, scenes[i].type, "; ".join(errors)[:160])
            continue
        trial = scenes[:i] + [new] + scenes[i + 1:]
        if _run_too_long(trial, i):
            continue
        scenes[i] = new
        changed += 1
        if new.type == "object3d":
            object3d_room -= 1
        plan.repairs.extend(repairs)
        plan.repairs.append(f"3D look: scene {i + 1} became {new.type}" + (f" {new.fields.get('kind')}" if new.fields.get("kind") else ""))
    return changed


def _run_too_long(scenes: list[sp.Scene], at: int) -> bool:
    kind = scenes[at].type
    if kind == "statement":
        return False
    run = 1
    j = at - 1
    while j >= 0 and scenes[j].type == kind:
        run += 1
        j -= 1
    j = at + 1
    while j < len(scenes) and scenes[j].type == kind:
        run += 1
        j += 1
    return run > sp.MAX_SAME_TYPE_RUN
