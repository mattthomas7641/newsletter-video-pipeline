"""The look preset library: named, data-driven looks for the explainer
style (`presets.json` next to this file), built from the composition's
look families and HyperFrames registry items.

A preset is a small record: the look fields it implies (`look`: the
theme, and the recipe `look-<id>` derived from its knobs), the style
knobs applied to a video's middle scenes (`knobs`, the closed sets of
styles/vocabulary.py), the scene types the writer is asked to prefer
(`emphasis`), the registry packages the look is built with (`registry`)
and a thumbnail path under `newsletter_video/ui/static`.

`load_presets()` is the read-only loader the panel backend can call; the
wiring is in look.py (`look.preset` resolves into theme + recipe) and in
styles/recipes.py (`preset_recipes()` turns each preset's knobs into a
built-in recipe so the existing recipe policy renders it). Nothing here
changes a video unless a look names a preset.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PRESETS_PATH = Path(__file__).resolve().parent / "presets.json"
RECIPE_PREFIX = "look-"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class PresetError(ValueError):
    pass


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    look: dict[str, Any]  # look fields: theme (recipe is derived)
    knobs: dict[str, Any]  # style knobs for the middle scenes
    emphasis: list[str] = field(default_factory=list)
    registry: list[str] = field(default_factory=list)
    thumbnail: str = ""
    default: bool = False
    good_for: str = ""  # one line for the panel's library: the stories a preset suits
    world: str = ""  # a 3D look: "hero" | "metrics" (world3d.py); every scene is drawn in 3D

    @property
    def threed(self) -> bool:
        return bool(self.world)

    @property
    def recipe_id(self) -> str:
        """The built-in recipe carrying this preset's knobs ('' when it has none)."""
        return f"{RECIPE_PREFIX}{self.id}" if self.knobs else ""

    @property
    def cinematic(self) -> bool:
        return self.look.get("theme") == "cinematic" or self.knobs.get("look") == "cinematic"

    def look_fields(self) -> dict[str, str]:
        """What the preset sets on a look: its theme and its recipe."""
        out = {k: v for k, v in self.look.items() if k in ("theme",) and v}
        if self.recipe_id:
            out["recipe"] = self.recipe_id
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "description": self.description, "default": self.default,
                "look": dict(self.look), "recipe": self.recipe_id, "knobs": dict(self.knobs), "emphasis": list(self.emphasis),
                "registry": list(self.registry), "thumbnail": self.thumbnail, "cinematic": self.cinematic,
                "good_for": self.good_for, "threed": self.threed, "world": self.world}


def _validate(raw: Any) -> Preset:
    if not isinstance(raw, dict) or not _ID.match(str(raw.get("id", ""))):
        raise PresetError(f"preset without a usable id: {raw!r}"[:200])
    pid = raw["id"]
    look = raw.get("look") or {}
    knobs = raw.get("knobs") or {}
    if not isinstance(look, dict) or not isinstance(knobs, dict):
        raise PresetError(f"preset {pid!r}: look and knobs must be objects")
    from .. import look as looks
    from ..styles.style_plan import validate_style

    if look.get("theme") and look["theme"] not in looks.THEMES:
        raise PresetError(f"preset {pid!r}: theme must be one of {', '.join(looks.THEMES)}")
    style, errs = validate_style(dict(knobs))
    if errs:
        raise PresetError(f"preset {pid!r}: " + "; ".join(errs))
    from ..world3d import WORLDS

    if raw.get("world") and raw["world"] not in WORLDS:
        raise PresetError(f"preset {pid!r}: world must be one of {', '.join(WORLDS)}")
    return Preset(
        id=pid, name=str(raw.get("name") or pid), description=str(raw.get("description") or ""),
        look={k: v for k, v in look.items() if k == "theme"}, knobs={k: v for k, v in (style or {}).items()
                                                                     if k != "recipe" and v not in (None, "", [], "default", 1.0)},
        emphasis=[str(t) for t in raw.get("emphasis") or []], registry=[str(r) for r in raw.get("registry") or []],
        thumbnail=str(raw.get("thumbnail") or ""), default=bool(raw.get("default")), world=str(raw.get("world") or ""),
        good_for=str(raw.get("good_for") or "")[:160],
    )


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[Preset, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    presets = [_validate(p) for p in data.get("presets", [])]
    ids = [p.id for p in presets]
    if len(ids) != len(set(ids)):
        raise PresetError("duplicate preset ids in " + path)
    return tuple(presets)


def load_presets(path: Path | str | None = None) -> list[Preset]:
    """Every preset, in file order (the default first in the file)."""
    return list(_load(str(path or PRESETS_PATH)))


def preset_ids(path: Path | str | None = None) -> list[str]:
    return [p.id for p in load_presets(path)]


def get_preset(preset_id: str, path: Path | str | None = None) -> Preset | None:
    pid = (preset_id or "").strip().lower()
    return next((p for p in load_presets(path) if p.id == pid), None)


def default_preset(path: Path | str | None = None) -> Preset | None:
    return next((p for p in load_presets(path) if p.default), None)


def presets_json(path: Path | str | None = None) -> list[dict[str, Any]]:
    """The list the panel backend can hand to its API, as plain dicts."""
    return [p.to_dict() for p in load_presets(path)]


def preset_recipes():
    """One built-in Recipe per preset with knobs (id `look-<preset>`)."""
    from ..styles.recipes import KNOB_PARAMS, Recipe

    out = []
    for p in load_presets():
        if not p.knobs:
            continue
        out.append(Recipe(
            id=p.recipe_id, name=f"Look: {p.name}", description=p.description, knobs=dict(p.knobs),
            demonstrates=[t for t in ("cinematic", "glow", "3d") if p.cinematic] + list(p.emphasis),
            when_to_use=f"the '{p.name}' look preset", scene_types=["*"], registry_refs=list(p.registry),
            gsap="", source="preset", fidelity=None, params=KNOB_PARAMS,
            fits=[t for t in p.emphasis if t in ("object3d", "depth", "kinetic", "glass")],
        ))
    return out


__all__ = ["Preset", "PresetError", "PRESETS_PATH", "RECIPE_PREFIX", "load_presets", "preset_ids", "get_preset",
           "default_preset", "presets_json", "preset_recipes"]
