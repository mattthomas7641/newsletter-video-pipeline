"""Style plans: the optional `style` a scene can carry, and the whole-video
plan the planner produces for the explainer builder.

A scene style is a small set of knobs the explainer composition knows how
to render (transition, entrance, hold, type treatment, background, accent,
ease, speed, stagger) plus the id of the recipe it came from. Every knob is
a closed set from `vocabulary.py`; "default" means "do what the template
does today", so a scene with no style, or an all-default style, renders
exactly as before. `validate_style` is strict about shape and values and
returns None (with the reasons) rather than something half-valid: an
invalid style never reaches the renderer, the scene simply keeps the
current look.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import vocabulary as V

KNOBS = ("transition", "entrance", "hold", "type_treatment", "background", "ease", "look")
KNOB_VALUES: dict[str, tuple[str, ...]] = {
    "transition": tuple(V.TRANSITIONS),
    "entrance": tuple(V.ENTRANCES),
    "hold": tuple(V.HOLDS),
    "type_treatment": tuple(V.TYPE_TREATMENTS),
    "background": tuple(V.BACKGROUNDS),
    "ease": tuple(V.EASES),
    "look": tuple(V.LOOKS),
}
SPEED_RANGE = (0.5, 2.0)
STAGGER_RANGE = (0.02, 0.4)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

DEFAULT_STYLE: dict[str, Any] = {
    "recipe": "", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
    "background": "default", "ease": "default", "look": "default", "accent": "", "palette": [], "speed": 1.0,
    "stagger": None,
}


class StylePlanError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def is_default(style: dict | None) -> bool:
    if not style:
        return True
    for k in KNOBS:
        if style.get(k, "default") != "default":
            return False
    return (not style.get("accent") and not style.get("palette") and float(style.get("speed", 1.0) or 1.0) == 1.0
            and not style.get("stagger"))


def is_cinematic(style: dict | None) -> bool:
    return bool(style) and style.get("look") == "cinematic"


def validate_style(raw: Any, known_recipes: set[str] | None = None) -> tuple[dict | None, list[str]]:
    """Normalise one scene style. Returns (style, errors); the style is None
    whenever there is any error. Unknown keys are errors too, so a typo in
    a knob name can't silently mean 'default'."""
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["style must be an object"]
    errors: list[str] = []
    out: dict[str, Any] = dict(DEFAULT_STYLE)
    for key, value in raw.items():
        if key == "recipe":
            value = str(value or "").strip()
            if value and not _SLUG.match(value):
                errors.append(f"style.recipe {value!r} is not a slug")
            elif value and known_recipes is not None and value not in known_recipes:
                errors.append(f"style.recipe {value!r} is not in the library")
            out["recipe"] = value
        elif key in KNOB_VALUES:
            value = str(value or "default").strip().lower() or "default"
            canonical = {v.lower(): v for v in KNOB_VALUES[key]}
            if value not in canonical:
                errors.append(f"style.{key} must be one of {', '.join(KNOB_VALUES[key])}, not {value!r}")
            out[key] = canonical.get(value, value)
        elif key == "accent":
            value = str(value or "").strip()
            if value and not _HEX.match(value):
                errors.append(f"style.accent {value!r} is not a #rrggbb colour")
            out["accent"] = value.lower()
        elif key == "palette":
            # The glow colours of a cinematic look: up to MAX_PALETTE #rrggbb values.
            if value in (None, ""):
                out["palette"] = []
                continue
            if not isinstance(value, (list, tuple)):
                errors.append("style.palette must be a list of #rrggbb colours")
                continue
            colours = [str(v or "").strip().lower() for v in value]
            bad = [c for c in colours if not _HEX.match(c)]
            if bad:
                errors.append(f"style.palette has non-colour entries: {', '.join(repr(b) for b in bad)}")
            if len(colours) > V.MAX_PALETTE:
                errors.append(f"style.palette has {len(colours)} colours, at most {V.MAX_PALETTE}")
            out["palette"] = colours
        elif key == "speed":
            try:
                speed = float(value) if value not in (None, "") else 1.0
            except (TypeError, ValueError):
                errors.append(f"style.speed {value!r} is not a number")
                continue
            if not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
                errors.append(f"style.speed {speed} outside {SPEED_RANGE[0]}-{SPEED_RANGE[1]}")
            out["speed"] = round(speed, 3)
        elif key == "stagger":
            if value in (None, "", 0):
                out["stagger"] = None
                continue
            try:
                stagger = float(value)
            except (TypeError, ValueError):
                errors.append(f"style.stagger {value!r} is not a number")
                continue
            if not STAGGER_RANGE[0] <= stagger <= STAGGER_RANGE[1]:
                errors.append(f"style.stagger {stagger} outside {STAGGER_RANGE[0]}-{STAGGER_RANGE[1]}")
            out["stagger"] = round(stagger, 3)
        else:
            errors.append(f"style has an unknown key {key!r}")
    if errors:
        return None, errors
    return out, []


def style_from_recipe(recipe_knobs: dict, overrides: dict | None = None) -> dict:
    """A style dict from a recipe's knobs with optional per-scene overrides
    (not yet validated)."""
    out = {k: (list(v) if k == "palette" else v) for k, v in recipe_knobs.items() if k in DEFAULT_STYLE}
    for k, v in (overrides or {}).items():
        if k in DEFAULT_STYLE and v not in (None, ""):
            out[k] = v
    return out


# --- the whole-video plan ------------------------------------------------------------

@dataclass
class StylePlan:
    scenes: list[dict | None]  # one validated style (or None) per scene of the scene plan, in order
    reasons: list[str] = field(default_factory=list)
    global_notes: dict = field(default_factory=dict)
    recipes_used: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)  # scenes whose style was dropped and why

    def to_dict(self) -> dict:
        return {"scenes": [{"index": i, "style": s, "reason": r} for i, (s, r) in enumerate(zip(self.scenes, self.reasons))],
                "global": self.global_notes, "recipes_used": self.recipes_used, "repairs": self.repairs}

    @property
    def styled_count(self) -> int:
        return sum(1 for s in self.scenes if s and not is_default(s))


STYLE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "global": {
            "type": "object",
            "properties": {
                "accent": {"type": "string", "description": "optional #rrggbb accent for the whole video, or ''"},
                "feel": {"type": "string", "description": "one line on the look you are going for and which references it draws on"},
            },
            "required": ["accent", "feel"],
            "additionalProperties": False,
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "0-based scene index in the scene plan"},
                    "recipe": {"type": "string", "description": "a recipe id from the candidates, or '' for the template's default look"},
                    "transition": {"type": "string", "description": "override, or 'default'"},
                    "entrance": {"type": "string", "description": "override, or 'default'"},
                    "hold": {"type": "string", "description": "override, or 'default'"},
                    "type_treatment": {"type": "string", "description": "override, or 'default'"},
                    "background": {"type": "string", "description": "override, or 'default'"},
                    "ease": {"type": "string", "description": "override, or 'default'"},
                    "speed": {"type": "number", "description": "0.5-2.0 motion speed multiplier, 1.0 for the recipe's own"},
                    "reason": {"type": "string", "description": "one short clause: why this recipe fits this scene"},
                },
                "required": ["index", "recipe", "transition", "entrance", "hold", "type_treatment", "background",
                             "ease", "speed", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["global", "scenes"],
    "additionalProperties": False,
}


def validate_style_plan(raw: Any, n_scenes: int, recipes: dict[str, dict]) -> StylePlan:
    """Turn the planner's answer into per-scene validated styles. A scene
    whose style is invalid is dropped to the default look with a note
    (never the whole plan), unless the answer has the wrong shape, which
    raises StylePlanError. `recipes` maps recipe id -> knob dict."""
    if not isinstance(raw, dict) or not isinstance(raw.get("scenes"), list):
        raise StylePlanError(["style plan must be an object with a 'scenes' list"])
    styles: list[dict | None] = [None] * n_scenes
    reasons = [""] * n_scenes
    repairs: list[str] = []
    used: list[str] = []
    glob = raw.get("global") if isinstance(raw.get("global"), dict) else {}
    accent = str(glob.get("accent", "") or "").strip().lower()
    if accent and not _HEX.match(accent):
        repairs.append(f"global accent {accent!r} ignored: not a #rrggbb colour")
        accent = ""
    for entry in raw["scenes"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
            repairs.append("a scene entry without an integer index was ignored")
            continue
        i = entry["index"]
        if not 0 <= i < n_scenes:
            repairs.append(f"scene index {i} is outside the plan (0-{n_scenes - 1}); ignored")
            continue
        recipe_id = str(entry.get("recipe", "") or "").strip()
        knobs = {}
        if recipe_id:
            if recipe_id not in recipes:
                repairs.append(f"scene {i + 1}: unknown recipe {recipe_id!r}; default look kept")
                continue
            knobs = recipes[recipe_id]
        overrides = {k: entry.get(k) for k in KNOBS if entry.get(k) not in (None, "", "default")}
        if entry.get("speed") not in (None, "", 1, 1.0):
            overrides["speed"] = entry.get("speed")
        candidate = style_from_recipe(knobs, overrides)
        candidate["recipe"] = recipe_id
        if accent and not candidate.get("accent"):
            candidate["accent"] = accent
        style, errors = validate_style(candidate, set(recipes))
        if style is None:
            repairs.append(f"scene {i + 1}: style rejected ({'; '.join(errors)}); default look kept")
            continue
        styles[i] = style
        reasons[i] = str(entry.get("reason", ""))[:200]
        if recipe_id and recipe_id not in used:
            used.append(recipe_id)
    return StylePlan(scenes=styles, reasons=reasons, global_notes={"accent": accent, "feel": str(glob.get("feel", ""))[:300]},
                     recipes_used=used, repairs=repairs)
