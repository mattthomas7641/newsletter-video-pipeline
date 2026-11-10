"""The vision pass: Claude looks at a few downscaled frames per scene plus
the numeric evidence and maps what it sees onto our vocabulary (scene
types, layouts, type treatments, entrances, transitions, holds, effect
tags, registry keywords). Cost is bounded by design: frames are 512 px on
the long side and JPEG-compressed, several scenes share one call, the
number of scenes looked at is capped, and the model, effort and budgets are
all environment settings (see .env.example, STYLE_VISION_*).

The call itself is injectable (`call=`), so the analyzer is testable
offline and the real model is only used in explicit, budgeted runs.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Callable

from . import vocabulary as V

log = logging.getLogger("newsletter_video")

DEFAULT_VISION_MODEL = "claude-sonnet-5-5"  # the same model the explainer writer uses; a look costs cents
DEFAULT_EFFORT = "low"
DEFAULT_FRAMES_PER_SCENE = 2
DEFAULT_SCENES_PER_CALL = 4
DEFAULT_MAX_SCENES = 24
FRAME_MAX_SIDE = 512
FRAME_JPEG_QUALITY = 70
# $ per million tokens (input, output), for the cost estimate in analysis.json
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.1, 0.5),
          "claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0)}


def settings() -> dict:
    return {
        "model": os.environ.get("STYLE_VISION_MODEL", DEFAULT_VISION_MODEL).strip() or DEFAULT_VISION_MODEL,
        "effort": os.environ.get("STYLE_VISION_EFFORT", DEFAULT_EFFORT).strip() or DEFAULT_EFFORT,
        "frames_per_scene": max(1, int(os.environ.get("STYLE_VISION_FRAMES_PER_SCENE", DEFAULT_FRAMES_PER_SCENE))),
        "scenes_per_call": max(1, int(os.environ.get("STYLE_VISION_SCENES_PER_CALL", DEFAULT_SCENES_PER_CALL))),
        "max_scenes": max(1, int(os.environ.get("STYLE_VISION_MAX_SCENES", DEFAULT_MAX_SCENES))),
    }


def vision_available() -> bool:
    if os.environ.get("STYLE_VISION", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (4.0, 20.0))
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 4)


def image_tokens(width: int, height: int) -> int:
    """Claude's approximate image token cost: pixels / 750."""
    return int(width * height / 750)


# --- schemas -------------------------------------------------------------------------

def _enum(values, description: str) -> dict:
    return {"type": "string", "enum": list(values), "description": description}


SCENE_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "the scene index given in the prompt"},
                    "what": {"type": "string", "description": "one sentence: what is on screen and what it does"},
                    "scene_type": _enum(V.SCENE_TYPES, "the closest scene type in our library"),
                    "layout": _enum(V.LAYOUTS, "where the content sits"),
                    "font_class": _enum(V.FONT_CLASSES, "rough class of the main typeface"),
                    "weight": _enum(("light", "regular", "medium", "bold", "black", "mixed"), "weight of the main text"),
                    "case": _enum(V.CASES, "letter case of the main text"),
                    "scale": _enum(("small", "medium", "large", "oversized"), "size of the main text relative to the frame"),
                    "hierarchy_levels": {"type": "integer", "description": "how many distinct type sizes are visible (1-4)"},
                    "background": _enum(V.BACKGROUNDS, "closest background treatment"),
                    "entrance": _enum(V.ENTRANCES, "how the main element most likely entered (from the first and middle frames and the motion evidence)"),
                    "transition_in": _enum(V.TRANSITIONS, "how the scene most likely arrived; 'default' if unclear"),
                    "hold": _enum(V.HOLDS, "what keeps moving while the scene holds; 'default' if unclear"),
                    "type_treatment": _enum(V.TYPE_TREATMENTS, "closest typographic treatment"),
                    "ease": _enum(V.EASES, "easing family of the motion, from the evidence"),
                    "icon_style": _enum(("none", "line", "filled", "3d", "emoji", "photo", "illustration"), "style of icons or illustration"),
                    "captions": _enum(("none", "lower-third", "centre", "top", "word-by-word"), "caption presence and placement"),
                    "effects": {"type": "array", "items": _enum(V.FEEL_TAGS, "an effect or feel tag"),
                                "description": "every tag from the list that applies"},
                    "registry_keywords": {"type": "array", "items": {"type": "string"},
                                          "description": "2-5 short search phrases for the HyperFrames registry that would reproduce this scene's look"},
                    "gsap": {"type": "string", "description": "one line: how you would build the key motion in GSAP (properties, ease, stagger)"},
                },
                "required": ["index", "what", "scene_type", "layout", "font_class", "weight", "case", "scale",
                             "hierarchy_levels", "background", "entrance", "transition_in", "hold", "type_treatment",
                             "ease", "icon_style", "captions", "effects", "registry_keywords", "gsap"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenes"],
    "additionalProperties": False,
}

OVERALL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "two sentences: what kind of video this is and what makes its look"},
        "feel": {"type": "array", "items": _enum(V.FEEL_TAGS, "a feel tag"), "description": "3-8 tags that describe the whole video"},
        "typography": {"type": "string", "description": "one line on the type system: families, weights, case, scale, hierarchy"},
        "colour": {"type": "string", "description": "one line on the palette and contrast"},
        "motion": {"type": "string", "description": "one line on the motion vocabulary: entrances, transitions, holds, easing, stagger"},
        "pacing": {"type": "string", "description": "one line on cut cadence and how it relates to the audio"},
        "reuse": {"type": "string", "description": "two sentences: which of these effects would translate well to a 40 s vertical tech-news explainer, and which would not"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "5-12 free-form searchable tags (lowercase, hyphenated)"},
    },
    "required": ["summary", "feel", "typography", "colour", "motion", "pacing", "reuse", "tags"],
    "additionalProperties": False,
}

SYSTEM = """\
You are a motion designer reverse-engineering motion-graphic and kinetic-typography videos so their \
techniques can be recreated in a data-driven HTML/GSAP composition (HyperFrames). You are shown a few \
frames per scene (first, middle, sometimes last) plus measured evidence (scene timing, transition guess, \
motion bursts with easing and direction, stagger interval, palette). Describe each scene strictly in the \
given vocabulary: every enum field must use one of its allowed values, and 'default' means you could not \
tell. Describe technique, never content: do not transcribe text, brands, names or logos. Base the motion \
fields on the evidence when the frames alone cannot show motion. Keep every string short."""


def _scene_vocab_notes() -> str:
    return (
        "Vocabulary.\nTransitions: " + "; ".join(f"{k}: {v}" for k, v in V.TRANSITIONS.items())
        + "\nEntrances: " + "; ".join(f"{k}: {v}" for k, v in V.ENTRANCES.items())
        + "\nHolds: " + "; ".join(f"{k}: {v}" for k, v in V.HOLDS.items())
        + "\nType treatments: " + "; ".join(f"{k}: {v}" for k, v in V.TYPE_TREATMENTS.items())
        + "\nBackgrounds: " + "; ".join(f"{k}: {v}" for k, v in V.BACKGROUNDS.items())
        + "\nScene types: title (opening headline), statement (one sentence), stat (one big number), comparison "
        "(two things side by side), list (2-4 rows), timeline (steps on a rail), quote (quotation + attribution), "
        "chart (bars/columns/line/dots), diagram (flow/stack/hub/funnel), device (phone/laptop/terminal/chat "
        "mockup), outro (closing call to action).\n"
    )


# --- the calls -------------------------------------------------------------------------

def _default_call(model: str, effort: str):
    """A structured-output Claude call: (system, content blocks, schema) -> dict."""
    import anthropic

    from ..script_writer import ANTHROPIC_TIMEOUT_SECONDS, _stream_with_retries

    client = anthropic.Anthropic(timeout=ANTHROPIC_TIMEOUT_SECONDS, max_retries=2)

    def call(system: str, content: list[dict], schema: dict) -> tuple[dict, dict]:
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        response = _stream_with_retries(
            client, anthropic,
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"vision call stopped with {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return json.loads(text), usage

    return call


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def describe_scenes(scenes: list[dict], frames_for: Callable[[int], list[tuple[str, bytes, tuple[int, int]]]],
                    evidence_for: Callable[[int], dict], overview: dict, sheet: tuple[bytes, tuple[int, int]] | None,
                    call: Callable | None = None, config: dict | None = None) -> dict:
    """Run the vision pass. `frames_for(i)` returns [(label, jpeg bytes, (w, h))]
    for scene i; `evidence_for(i)` the numeric evidence to show; `overview`
    the whole-video evidence; `sheet` a contact sheet for the overall call.
    Returns {"scenes": {index: description}, "overall": {...}, "usage": {...},
    "cost_usd": float, "model": str, "calls": int}."""
    cfg = {**settings(), **(config or {})}
    model, effort = cfg["model"], cfg["effort"]
    call = call or _default_call(model, effort)
    chosen = scenes[: cfg["max_scenes"]]
    if len(scenes) > cfg["max_scenes"]:
        # spread the budget over the whole video instead of the first N scenes
        step = len(scenes) / cfg["max_scenes"]
        chosen = [scenes[int(k * step)] for k in range(cfg["max_scenes"])]
    out: dict = {"scenes": {}, "overall": None, "usage": {"input_tokens": 0, "output_tokens": 0}, "model": model,
                 "calls": 0, "errors": []}
    est_in = 0
    vocab = _scene_vocab_notes()
    for k in range(0, len(chosen), cfg["scenes_per_call"]):
        batch = chosen[k:k + cfg["scenes_per_call"]]
        content: list[dict] = [{"type": "text", "text": vocab}]
        for s in batch:
            ev = evidence_for(s["index"])
            content.append({"type": "text", "text": f"Scene {s['index']}: {s['start']:.2f}-{s['end']:.2f} s "
                                                    f"(transition in: {s.get('transition_in')}). Evidence: {json.dumps(ev)}"})
            for label, jpeg, (w, h) in frames_for(s["index"])[: cfg["frames_per_scene"]]:
                content.append({"type": "text", "text": f"Scene {s['index']} frame: {label}"})
                content.append(_image_block(jpeg))
                est_in += image_tokens(w, h)
        content.append({"type": "text", "text": "Describe each of these scenes in the vocabulary. Return one entry per scene index."})
        try:
            data, usage = call(SYSTEM, content, SCENE_DESCRIPTION_SCHEMA)
        except Exception as exc:  # noqa: BLE001 - the analysis survives a failed look
            log.warning("Vision call failed for scenes %s: %s: %s", [s["index"] for s in batch], type(exc).__name__, exc)
            out["errors"].append(f"scenes {[s['index'] for s in batch]}: {type(exc).__name__}")
            continue
        out["calls"] += 1
        out["usage"]["input_tokens"] += usage.get("input_tokens", 0)
        out["usage"]["output_tokens"] += usage.get("output_tokens", 0)
        for d in data.get("scenes", []):
            if isinstance(d, dict) and isinstance(d.get("index"), int):
                out["scenes"][d["index"]] = _normalize_scene(d)
    # the whole video, from the contact sheet and the measured overview
    content = [{"type": "text", "text": "Whole-video evidence: " + json.dumps(overview)}]
    if sheet is not None:
        content.append({"type": "text", "text": "Contact sheet (one frame per scene, in order):"})
        content.append(_image_block(sheet[0]))
        est_in += image_tokens(*sheet[1])
    content.append({"type": "text", "text": "Per-scene descriptions already made: " + json.dumps(
        {i: {k: v for k, v in d.items() if k in ("scene_type", "entrance", "transition_in", "effects")} for i, d in out["scenes"].items()})})
    content.append({"type": "text", "text": "Summarise this video's style for a searchable library entry."})
    try:
        data, usage = call(SYSTEM, content, OVERALL_SCHEMA)
        out["calls"] += 1
        out["usage"]["input_tokens"] += usage.get("input_tokens", 0)
        out["usage"]["output_tokens"] += usage.get("output_tokens", 0)
        out["overall"] = _normalize_overall(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vision overall call failed: %s: %s", type(exc).__name__, exc)
        out["errors"].append(f"overall: {type(exc).__name__}")
    out["cost_usd"] = estimate_cost(model, out["usage"]["input_tokens"], out["usage"]["output_tokens"])
    out["estimated_image_tokens"] = est_in
    return out


def _pick(value, allowed, default="default"):
    return value if value in allowed else default


def _normalize_scene(d: dict) -> dict:
    return {
        "what": str(d.get("what", ""))[:200],
        "scene_type": _pick(d.get("scene_type"), V.SCENE_TYPES, "statement"),
        "layout": _pick(d.get("layout"), V.LAYOUTS, "centered-stack"),
        "font_class": _pick(d.get("font_class"), V.FONT_CLASSES, "sans-grotesk"),
        "weight": _pick(d.get("weight"), ("light", "regular", "medium", "bold", "black", "mixed"), "bold"),
        "case": _pick(d.get("case"), V.CASES, "mixed"),
        "scale": _pick(d.get("scale"), ("small", "medium", "large", "oversized"), "large"),
        "hierarchy_levels": int(d.get("hierarchy_levels", 2)) if isinstance(d.get("hierarchy_levels"), int) else 2,
        "background": _pick(d.get("background"), V.BACKGROUNDS),
        "entrance": _pick(d.get("entrance"), V.ENTRANCES),
        "transition_in": _pick(d.get("transition_in"), V.TRANSITIONS),
        "hold": _pick(d.get("hold"), V.HOLDS),
        "type_treatment": _pick(d.get("type_treatment"), V.TYPE_TREATMENTS),
        "ease": _pick(d.get("ease"), V.EASES),
        "icon_style": _pick(d.get("icon_style"), ("none", "line", "filled", "3d", "emoji", "photo", "illustration"), "none"),
        "captions": _pick(d.get("captions"), ("none", "lower-third", "centre", "top", "word-by-word"), "none"),
        "effects": [e for e in d.get("effects", []) if e in V.FEEL_TAGS][:12],
        "registry_keywords": [str(k)[:60] for k in d.get("registry_keywords", [])][:5],
        "gsap": str(d.get("gsap", ""))[:300],
    }


def _normalize_overall(d: dict) -> dict:
    return {
        "summary": str(d.get("summary", ""))[:600],
        "feel": [e for e in d.get("feel", []) if e in V.FEEL_TAGS][:8],
        "typography": str(d.get("typography", ""))[:300],
        "colour": str(d.get("colour", ""))[:300],
        "motion": str(d.get("motion", ""))[:300],
        "pacing": str(d.get("pacing", ""))[:300],
        "reuse": str(d.get("reuse", ""))[:600],
        "tags": [str(t).lower().strip().replace(" ", "-")[:40] for t in d.get("tags", [])][:12],
    }
