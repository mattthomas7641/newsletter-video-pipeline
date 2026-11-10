"""Recreating a reference with our own composition and placeholder content.

The analysis gives scene timing, transitions, motion evidence and (with a
model) a description of each scene in our vocabulary. From that we build a
placeholder scene plan (generic text, never the reference's words, brands
or footage), give every scene a style (recipe knobs derived from the
analysis), render it with the explainer composition, measure fidelity
against the reference (compare.py) and, for a bounded number of rounds,
let the model see the side-by-side sheet and the numbers and adjust the
styles. The best round is kept and the per-scene styles are what the
recipes are made from.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

from ..scene_plan import CAPS
from . import vision
from .analysis import light_analysis
from .compare import compare_analyses, side_by_side
from .recipes import knobs_from_scene
from .style_plan import STYLE_PLAN_SCHEMA, is_default, validate_style, validate_style_plan

log = logging.getLogger("newsletter_video")

DEFAULT_ROUNDS = 2  # renders per reference: the first plus this many refinements at most
MAX_ROUNDS = 3
GOOD_ENOUGH = 0.85
MIN_SCENE_S = 0.6  # shorter reference scenes are merged into their neighbour (the template can't land anything faster)
MAX_SCENES = 40

# Placeholder content: generic by design. Nothing here comes from a reference.
PLACEHOLDER = {
    "title": {"kicker": "REFERENCE", "headline": "A headline lands here first", "icon": "rocket"},
    "statement": {"label": "The point", "text": "One sentence carries the whole scene", "emphasis": "whole scene", "icon": "bolt"},
    "stat": {"value": "72%", "label": "of the frame is the number", "context": "a placeholder figure, not a fact", "source": "Style library"},
    "comparison": {"title": "Before and after", "left": {"label": "Before", "value": "1x", "note": "the old way", "number": 1},
                   "right": {"label": "After", "value": "3x", "note": "the new way", "number": 3}},
    "list": {"title": "Three things to know", "items": ["The first thing", "The second thing", "The third thing"],
             "icons": ["chip", "cloud", "code"]},
    "timeline": {"title": "How it unfolded", "steps": [{"label": "Then", "text": "The first step"}, {"label": "Now", "text": "The second step"},
                                                      {"label": "Next", "text": "The third step"}]},
    "quote": {"text": "A placeholder quotation sits here, long enough to wrap onto a second line.", "attribution": "Someone, somewhere"},
    "chart": {"title": "A small chart", "unit": "", "kind": "bars", "series": [{"label": "One", "value": 3}, {"label": "Two", "value": 5},
                                                                               {"label": "Three", "value": 8}], "highlight": 2},
    "diagram": {"title": "How it fits", "kind": "flow", "nodes": [{"label": "Input", "text": "comes in"}, {"label": "Model", "text": "does the work"},
                                                                  {"label": "Output", "text": "goes out"}]},
    "device": {"title": "On the screen", "kind": "phone", "label": "App", "lines": ["A notification arrives", "Another one follows", "And a third"]},
    "outro": {"text": "Which look should we use next?", "icon": "comment"},
}
TEXT_TYPES = ("title", "statement", "quote", "outro")


def _rounds() -> int:
    try:
        return max(0, min(MAX_ROUNDS, int(os.environ.get("STYLE_RECREATE_ROUNDS", DEFAULT_ROUNDS))))
    except ValueError:
        return DEFAULT_ROUNDS


def guess_scene_type(scene: dict, vis: dict | None, position: tuple[int, int]) -> str:
    """Our scene type for a reference scene: the model's call when there is
    one, else a guess from the layout and motion evidence."""
    i, n = position
    if vis and vis.get("scene_type"):
        t = vis["scene_type"]
        if t == "title" and i > 0:
            t = "statement"
        if t == "outro" and i < n - 1:
            t = "statement"
        return t
    if i == 0:
        return "title"
    if i == n - 1 and n >= 3:
        return "outro"
    lay = scene.get("layout") or {}
    motion = scene.get("motion") or {}
    if motion.get("stagger") and motion["stagger"].get("n", 0) >= 3:
        return "list"
    if lay.get("kind") == "hero-plus-text" and not lay.get("text_like"):
        return "stat"
    if lay.get("kind") == "column":
        return "timeline"
    return "statement"


def placeholder_scenes(analysis: dict) -> list[dict]:
    """A scene list (type, placeholder fields, start/end from the reference,
    style knobs from the analysis) ready for a payload."""
    scenes = analysis.get("scenes", [])
    vis_scenes = {int(k): v for k, v in ((analysis.get("vision") or {}).get("scenes") or {}).items()}
    palette = analysis.get("palette") or {}
    merged: list[dict] = []
    for s in scenes:
        if merged and s["duration"] < MIN_SCENE_S:
            merged[-1]["end"] = s["end"]
            continue
        merged.append(dict(s))
    merged = merged[:MAX_SCENES]
    if merged:
        merged[-1]["end"] = analysis["info"]["duration"]
    out = []
    n = len(merged)
    for i, s in enumerate(merged):
        vis = vis_scenes.get(s["index"])
        stype = guess_scene_type(s, vis, (i, n))
        fields = json.loads(json.dumps(PLACEHOLDER[stype]))
        knobs = knobs_from_scene(s, vis, palette)
        if i == 0:
            knobs.pop("transition", None)  # the first scene has no transition in
        style, errs = validate_style(knobs)
        if errs:
            log.info("Recreation: scene %d knobs %s rejected (%s); using defaults", i + 1, knobs, "; ".join(errs))
            style = None
        entry = {"type": stype, "start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3), **fields}
        if style and not is_default(style):
            entry["style"] = style
        out.append(entry)
    return out


def payload_from_scenes(scenes: list[dict], duration: float, theme: str, kicker: str = "STYLE LIBRARY",
                        captions: bool = False) -> dict:
    """The composition's `story` variable with synthetic cues: no narration,
    so items are spread through their scene and numbers count on arrival."""
    from ..explainer_builder import cue_times

    out_scenes = []
    for s in scenes:
        entry = dict(s)
        start, end = entry["start"], entry["end"]
        t = entry["type"]
        if t == "stat":
            entry["countAt"] = round(start + 0.5, 3)
        elif t == "statement" and entry.get("emphasis"):
            entry["emphasisAt"] = round(start + min(1.2, (end - start) * 0.5), 3)
        elif t == "list":
            entry["cues"] = cue_times(entry["items"], [], start, end)
        elif t == "timeline":
            entry["cues"] = cue_times([x["label"] for x in entry["steps"]], [], start, end)
        elif t == "chart":
            entry["cues"] = cue_times([p["label"] for p in entry["series"]], [], start, end)
        elif t == "comparison":
            entry["cues"] = cue_times(["a", "b"], [], start, end)
        elif t == "diagram":
            entry["cues"] = cue_times([x["label"] for x in entry["nodes"]], [], start, end)
        elif t == "device":
            entry["cues"] = cue_times(entry["lines"], [], start, end)
        out_scenes.append(entry)
    caps = []
    if captions:
        words = ["placeholder", "caption", "words", "follow", "the", "voice", "here"]
        t = 0.4
        k = 0
        while t < duration - 0.8:
            chunk = [words[(k + j) % len(words)] for j in range(3)]
            caps.append({"words": chunk, "at": [round(t + 0.3 * j, 3) for j in range(3)], "start": round(t, 3), "end": round(min(t + 1.4, duration), 3)})
            t += 1.5
            k += 3
    return {"theme": theme, "kicker": kicker, "duration": round(duration, 3), "scenes": out_scenes, "captions": caps}


def render_recreation(payload: dict, out_path: Path, work_root: Path) -> Path:
    """Render a payload with the explainer composition and a silent voice
    track of the right length."""
    from ..explainer_builder import _prepare_workdir, render_story
    from .media import write_silence

    work_dir = work_root / "_hf_work_recreate"
    _prepare_workdir(work_dir)
    write_silence(work_dir / "assets" / "voice.wav", payload["duration"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return render_story(payload, out_path, work_dir)


REFINE_SYSTEM = """\
You are refining a recreation of a reference motion-graphic video. The recreation is rendered by a \
data-driven composition whose scenes each take a small set of style knobs (transition, entrance, hold, \
type_treatment, background, ease, speed 0.5-2.0, stagger). You see a side-by-side sheet (reference \
keyframe on the left, recreation on the right, one row per reference scene), the measured fidelity \
(cut alignment, motion-curve correlation, transition agreement, palette, layout), the per-scene evidence \
from the reference, and the knobs currently used. Change only what would bring the recreation closer: \
keep the timing (it already follows the reference), prefer the reference's transitions and entrances, \
and leave a knob at 'default' when the template's own behaviour is the better match. Return one entry \
per scene index (0-based) with the full knob set; use '' for recipe."""


def refine_once(analysis: dict, scenes: list[dict], comparison: dict, sheet_path: Path, call: Callable) -> list[dict] | None:
    """Ask the model for adjusted knobs; None when the answer is unusable."""
    from .media import jpeg_bytes
    from PIL import Image
    import numpy as np

    im = np.asarray(Image.open(sheet_path).convert("RGB"))
    content = [
        {"type": "text", "text": "Side-by-side keyframes:"},
        vision._image_block(jpeg_bytes(im, 70, 1024)),
        {"type": "text", "text": "Fidelity: " + json.dumps({k: v for k, v in comparison.items() if k != "layout_per_scene"})},
        {"type": "text", "text": "Layout similarity per scene: " + json.dumps(comparison.get("layout_per_scene", []))},
        {"type": "text", "text": "Reference scenes (evidence): " + json.dumps([
            {"index": i, "transition_in": s.get("transition_in"), "duration": s.get("duration"),
             "events": [{k: e.get(k) for k in ("kind", "easing", "sweep", "slide")} for e in (s.get("motion") or {}).get("events", [])[:4]],
             "stagger": (s.get("motion") or {}).get("stagger")} for i, s in enumerate(analysis.get("scenes", []))])},
        {"type": "text", "text": "Current scenes and knobs: " + json.dumps([{"index": i, "type": s["type"], "style": s.get("style")} for i, s in enumerate(scenes)])},
        {"type": "text", "text": "Return the adjusted knobs for every scene."},
    ]
    try:
        data, _usage = call(REFINE_SYSTEM, content, STYLE_PLAN_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        log.warning("Refinement call failed: %s: %s", type(exc).__name__, exc)
        return None
    try:
        plan = validate_style_plan(data, len(scenes), {})
    except Exception as exc:  # noqa: BLE001
        log.warning("Refinement answer rejected: %s", exc)
        return None
    out = []
    for s, style in zip(scenes, plan.scenes):
        entry = {k: v for k, v in s.items() if k != "style"}
        if style and not is_default(style):
            entry["style"] = style
        out.append(entry)
    return out


def recreate_reference(lib, name: str, analysis: dict, call: Callable | None = None, rounds: int | None = None,
                       render: Callable | None = None) -> dict:
    """Render, compare, refine (bounded), keep the best. Writes everything
    under <entry>/recreation/. `render(payload, out_path, work_root)` can be
    injected for tests; `call` is the model (None: real model if available,
    else no refinement)."""
    entry_dir = lib.entry_dir(name)
    rec_dir = entry_dir / "recreation"
    if rec_dir.exists():
        shutil.rmtree(rec_dir)
    rec_dir.mkdir(parents=True)
    render = render or render_recreation
    rounds = _rounds() if rounds is None else max(0, min(MAX_ROUNDS, rounds))
    if call is None and vision.vision_available():
        cfg = vision.settings()
        call = vision._default_call(cfg["model"], cfg["effort"])
    theme = "dark" if (analysis.get("palette") or {}).get("theme") == "dark" else "light"
    has_captions = any(v.get("captions", "none") != "none" for v in ((analysis.get("vision") or {}).get("scenes") or {}).values())
    scenes = placeholder_scenes(analysis)
    ref_keyframes = [entry_dir / s["keyframes"][1] for s in analysis.get("scenes", []) if len(s.get("keyframes", [])) > 1]
    history = []
    best: dict | None = None
    cost = 0.0
    for r in range(rounds + 1):
        payload = payload_from_scenes(scenes, analysis["info"]["duration"], theme, captions=has_captions)
        video = rec_dir / f"round{r}.mp4"
        render(payload, video, rec_dir)
        rec_analysis = light_analysis(video, rec_dir / f"round{r}_analysis")
        comparison = compare_analyses(analysis, rec_analysis)
        comparison["round"] = r
        rec_keyframes = []
        for s in analysis.get("scenes", []):
            mid = (s["start"] + s["end"]) / 2
            target = next((t for t in rec_analysis["scenes"] if t["start"] <= mid < t["end"]), None)
            rec_keyframes.append(rec_dir / f"round{r}_analysis" / target["keyframes"][1] if target and len(target.get("keyframes", [])) > 1 else Path("/nonexistent"))
        sheet = side_by_side(ref_keyframes, rec_keyframes, rec_dir / f"round{r}_side_by_side.jpg")
        history.append({"round": r, "score": comparison["score"], "scenes": scenes, "comparison": comparison})
        log.info("Recreation %s round %d: fidelity %.3f (cuts %.2f, motion %.2f, transitions %.2f, palette %.2f, layout %.2f)",
                 name, r, comparison["score"], comparison["cuts"]["f1"], comparison["motion_correlation"],
                 comparison["transitions"]["agreement"], comparison["palette_similarity"], comparison["layout_similarity"])
        if best is None or comparison["score"] > best["score"]:
            best = {"score": comparison["score"], "round": r, "scenes": scenes, "comparison": comparison, "video": video, "sheet": sheet,
                    "payload": payload}
        if comparison["score"] >= GOOD_ENOUGH or r == rounds or call is None:
            break
        adjusted = refine_once(analysis, scenes, comparison, sheet, call)
        cost += 0.02  # one refinement look, rough estimate (a 1024 px sheet + a short plan)
        if adjusted is None or adjusted == scenes:
            break
        scenes = adjusted
    assert best is not None
    # keep the best round under stable names
    shutil.copy2(best["video"], rec_dir / "recreation.mp4")
    shutil.copy2(best["sheet"], rec_dir / "side_by_side.jpg")
    (rec_dir / "plan.json").write_text(json.dumps({"theme": theme, "scenes": best["scenes"]}, indent=1, ensure_ascii=False))
    (rec_dir / "story.json").write_text(json.dumps(best["payload"], indent=1))
    (rec_dir / "rounds.json").write_text(json.dumps([{k: v for k, v in h.items() if k != "scenes"} for h in history], indent=1))
    result = dict(best["comparison"])
    result.update({"rounds": len(history), "best_round": best["round"], "cost_usd": round(cost, 4), "video": "recreation/recreation.mp4",
                   "side_by_side": "recreation/side_by_side.jpg", "scene_styles": [s.get("style") for s in best["scenes"]]})
    (rec_dir / "compare.json").write_text(json.dumps(result, indent=1))
    for r in range(len(history)):  # the per-round renders are large; keep only the analysis folders
        p = rec_dir / f"round{r}.mp4"
        if p.exists() and r != best["round"]:
            p.unlink()
    return result
