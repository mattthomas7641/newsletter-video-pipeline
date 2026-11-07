"""The *look* of a video: which style renders it, who narrates it, and the
theme, background and style-recipe policy, as one small validated object.

Three layers, each only filling fields the layer above left unset:

    default look   <-  the environment (.env): VIDEO_STYLE, CHARACTER,
                       VIDEO_THEME, BACKGROUND_STYLE, STYLE_RECIPE
    run overrides  <-  what a panel action or CLI flag asked for this run
    video look     <-  the `look` stored with one planned video

`resolve_look()` folds the layers into a complete look. `look_env()` turns a
resolved look back into the environment variables the builders already read,
so with nothing overridden the 7:00 AM job behaves exactly as before (see
tests/test_look.py::test_default_look_leaves_the_environment_unchanged).

A stored look is versioned (`LOOK_VERSION`) and validated on the way in:
an unknown field, style, theme or a malformed character slug is an error,
never silently ignored, so a typo cannot quietly render the wrong video.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

LOOK_VERSION = 1
STYLES = ("avatar", "explainer")
THEMES = ("light", "dark")
BACKGROUNDS = ("gameplay", "none")
# The style-recipe policy for the explainer style's style library:
#   auto  - the writer's planner picks recipes (one small model call per video)
#   none  - the template's own look, no recipes
#   <id>  - one specific recipe applied to the middle scenes, deterministic
RECIPE_AUTO = "auto"
RECIPE_NONE = "none"
FIELDS = ("style", "character", "theme", "background", "recipe")
ENV_KEYS = {"style": "VIDEO_STYLE", "character": "CHARACTER", "theme": "VIDEO_THEME",
            "background": "BACKGROUND_STYLE", "recipe": "STYLE_RECIPE"}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RECIPE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DEFAULTS = {"style": "explainer", "character": "", "theme": "light", "background": "gameplay", "recipe": RECIPE_AUTO}


class LookError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_field(key: str, value: Any) -> tuple[str | None, str | None]:
    """(normalised value, error). None value means 'unset: inherit'."""
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"look.{key} must be a string"
    v = value.strip()
    if key == "style":
        v = v.lower()
        return (v, None) if v in STYLES else (None, f"look.style must be one of {', '.join(STYLES)}, not {v!r}")
    if key == "theme":
        v = v.lower()
        return (v, None) if v in THEMES else (None, f"look.theme must be one of {', '.join(THEMES)}, not {v!r}")
    if key == "background":
        v = v.lower()
        return (v, None) if v in BACKGROUNDS else (None, f"look.background must be one of {', '.join(BACKGROUNDS)}, not {v!r}")
    if key == "character":
        return (v, None) if v == "" or _SLUG.match(v) else (None, f"look.character {v!r} is not a character folder name")
    if key == "recipe":
        v = v.lower()
        if v in (RECIPE_AUTO, RECIPE_NONE) or _RECIPE.match(v):
            return v, None
        return None, f"look.recipe must be 'auto', 'none' or a recipe id, not {v!r}"
    return None, f"look has an unknown field {key!r}"


def validate_look(raw: Any, partial: bool = True) -> dict[str, str | None]:
    """Normalise a look. `partial=True` keeps unset fields as None (an
    override layer); `partial=False` requires every field (a resolved look).
    Raises LookError listing every problem."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LookError(["look must be an object"])
    errors: list[str] = []
    out: dict[str, str | None] = {}
    version = raw.get("version", LOOK_VERSION)
    if version != LOOK_VERSION:
        errors.append(f"look.version {version!r} is not supported (expected {LOOK_VERSION})")
    for key, value in raw.items():
        if key == "version":
            continue
        if key not in FIELDS:
            errors.append(f"look has an unknown field {key!r}")
            continue
        if value == "" and key != "character":
            value = None  # an empty choice in a form means "inherit"
        normalised, problem = _check_field(key, value)
        if problem:
            errors.append(problem)
        else:
            out[key] = normalised
    for key in FIELDS:
        out.setdefault(key, None)
        if not partial and out[key] is None:
            errors.append(f"look.{key} is required")
    if errors:
        raise LookError(errors)
    return out


def is_empty(look: Mapping[str, Any] | None) -> bool:
    return not look or all(look.get(k) is None for k in FIELDS)


def compact(look: Mapping[str, Any] | None) -> dict[str, Any]:
    """Only the set fields, with the version, for storing in a sidecar."""
    out: dict[str, Any] = {"version": LOOK_VERSION}
    for key in FIELDS:
        if look and look.get(key) is not None:
            out[key] = look[key]
    return out


def look_from_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The default look: what the environment says, with the pipeline's own
    defaults for anything unset. Invalid values raise, exactly as the
    builders' own readers (`video_style()`) would."""
    environ = os.environ if environ is None else environ
    raw = {}
    for key, env_key in ENV_KEYS.items():
        value = environ.get(env_key)
        raw[key] = value if value is not None and (value.strip() or key == "character") else DEFAULTS[key]
    return validate_look(raw, partial=False)  # type: ignore[return-value]


def resolve_look(*layers: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Default (environment) < each layer in order; a layer's unset fields
    inherit. Every layer is validated."""
    resolved: dict[str, Any] = dict(look_from_env(environ))
    for layer in layers:
        clean = validate_look(layer, partial=True)
        for key in FIELDS:
            if clean[key] is not None:
                resolved[key] = clean[key]
    return resolved


def look_env(look: Mapping[str, Any]) -> dict[str, str]:
    """The environment variables that make the builders render this look."""
    return {ENV_KEYS[key]: str(look[key]) for key in FIELDS if look.get(key) is not None}


@contextmanager
def applied(look: Mapping[str, Any] | None) -> Iterator[dict[str, str]]:
    """Temporarily puts a resolved look into os.environ for one build, so
    every existing reader (video_style, video_theme, characters.active,
    background_clips, the style library policy) sees it; restored after.
    With an empty/None look nothing is touched."""
    if is_empty(look):
        yield {}
        return
    env = look_env(look)
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield env
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def describe(look: Mapping[str, Any]) -> str:
    style = look.get("style", "?")
    bits = [style]
    bits.append(f"narrator {look['character']}" if look.get("character") else "default narrator")
    if style == "avatar":
        bits.append(f"{look.get('background', 'gameplay')} background")
    else:
        bits.append(f"{look.get('theme', 'light')} theme")
        recipe = look.get("recipe", RECIPE_AUTO)
        bits.append("planner recipes" if recipe == RECIPE_AUTO else "no recipes" if recipe == RECIPE_NONE else f"recipe {recipe}")
    return ", ".join(bits)
