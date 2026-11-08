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
# "cinematic" is the explainer's dark look family (near-black ground, glow
# accents, grid floor, vignette, grain); a recipe with `look: cinematic`
# selects it on its own, so the theme field only needs it for an explicit choice.
THEMES = ("light", "dark", "cinematic")
BACKGROUNDS = ("gameplay", "none")
# The style-recipe policy for the explainer style's style library:
#   auto  - the writer's planner picks recipes (one small model call per video)
#   none  - the template's own look, no recipes
#   <id>  - one specific recipe applied to the middle scenes, deterministic
RECIPE_AUTO = "auto"
RECIPE_NONE = "none"
# `preset` names a look preset (newsletter_video/looks/presets.json): it
# resolves into a theme and a recipe (`look-<preset>`) that sit between the
# environment defaults and a layer's explicit fields, so a per-video look can
# still override any field. "" means no preset: nothing added.
# Presets that were removed from presets.json: a saved plan, sidecar or LOOK_PRESET that still names one
# resolves to the default look instead of failing.
RETIRED_PRESETS = frozenset({"depth-cards", "data-focus", "glow-type", "quiet-editorial"})
RETIRED_RECIPES = frozenset({"look-" + p for p in RETIRED_PRESETS})
# `bg` and `accent` are the two colours a look can change (colors.py): the
# ground and the colour of the animations / highlights / neon. "" is "the
# look's own colour"; anything else is a #rrggbb hex, checked on the way in.
FIELDS = ("style", "character", "theme", "background", "recipe", "preset", "bg", "accent")
BLANKABLE = ("character", "preset", "bg", "accent")
ENV_KEYS = {"style": "VIDEO_STYLE", "character": "CHARACTER", "theme": "VIDEO_THEME",
            "background": "BACKGROUND_STYLE", "recipe": "STYLE_RECIPE", "preset": "LOOK_PRESET",
            "bg": "LOOK_BG", "accent": "LOOK_ACCENT"}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RECIPE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DEFAULTS = {"style": "explainer", "character": "", "theme": "light", "background": "gameplay", "recipe": RECIPE_AUTO,
            "preset": "", "bg": "", "accent": ""}


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
        if v in RETIRED_RECIPES:
            return RECIPE_AUTO, None  # a saved look that named a retired preset's recipe: the default policy
        if v in (RECIPE_AUTO, RECIPE_NONE) or _RECIPE.match(v):
            return v, None
        return None, f"look.recipe must be 'auto', 'none' or a recipe id, not {v!r}"
    if key in ("bg", "accent"):
        from .colors import normalise_hex

        if v == "":
            return "", None
        hex_ = normalise_hex(v)
        return (hex_, None) if hex_ else (None, f"look.{key} must be a #rrggbb colour, not {v!r}")
    if key == "preset":
        v = v.lower()
        if v == "" or v in RETIRED_PRESETS:
            return "", None  # no preset: a saved look or LOOK_PRESET naming a retired one falls back to the default look
        from .looks import preset_ids

        ids = preset_ids()
        return (v, None) if v in ids else (None, f"look.preset must be one of {', '.join(ids)}, not {v!r}")
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
        if value == "" and key not in BLANKABLE:
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
        if look and look.get(key) is not None and not (key == "preset" and look[key] == ""):
            out[key] = look[key]
    return out


def _base_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    raw = {}
    for key, env_key in ENV_KEYS.items():
        value = environ.get(env_key)
        raw[key] = value if value is not None and (value.strip() or key in BLANKABLE) else DEFAULTS[key]
    return validate_look(raw, partial=False)  # type: ignore[return-value]


def look_from_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The default look: what the environment says, with the pipeline's own
    defaults for anything unset. Invalid values raise, exactly as the
    builders' own readers (`video_style()`) would."""
    return apply_preset(_base_from_env(environ))  # type: ignore[arg-type]


def apply_preset(look: dict[str, Any], explicit: set[str] | None = None) -> dict[str, Any]:
    """Fold `look.preset` into the look: the preset's theme and recipe fill
    every field not in `explicit` (the fields a layer set by hand). With no
    preset the look is returned untouched."""
    pid = look.get("preset") or ""
    if not pid:
        return look
    from .looks import get_preset

    preset = get_preset(pid)
    if preset is None:
        return look
    for key, value in preset.look_fields().items():
        if key not in (explicit or set()):
            look[key] = value
    return look


def _effective_preset(pid: str) -> str:
    """"" and the library's default preset render the same look."""
    if pid:
        return pid
    from .looks import default_preset

    d = default_preset()
    return d.id if d else ""


def resolve_look(*layers: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Default (environment) < preset < each layer in order; a layer's unset
    fields inherit. A preset named by the environment or any layer supplies
    its theme and recipe wherever no layer set them by hand, so a per-video
    look can still override any field. Every layer is validated."""
    base = _base_from_env(environ)
    resolved: dict[str, Any] = dict(apply_preset(dict(base)))
    explicit: set[str] = set()
    for layer in layers:
        clean = validate_look(layer, partial=True)
        if clean["preset"] is not None and _effective_preset(clean["preset"]) != _effective_preset(resolved.get("preset", "")):
            for key in ("bg", "accent"):  # the colours belong to a preset: another preset starts from its own
                if clean[key] is None:
                    resolved[key] = ""
            for key in ("theme", "recipe"):  # so do the theme and recipe the previous preset supplied
                if key not in explicit:
                    resolved[key] = base[key]
        for key in FIELDS:
            if clean[key] is not None:
                resolved[key] = clean[key]
                if key != "preset":
                    explicit.add(key)
    return apply_preset(resolved, explicit)


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


def cinematic_requested(look: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the look (or, with none given, the environment) asks for the
    cinematic look: the theme is 'cinematic', or the recipe policy names a
    recipe whose knobs carry `look: cinematic`. The writer uses this to
    offer the rich scene types; a missing library or recipe means False."""
    try:
        resolved = dict(look) if look else look_from_env(environ)
    except LookError:
        return False
    if resolved.get("style") == "avatar":
        return False
    if resolved.get("theme") == "cinematic":
        return True
    recipe = str(resolved.get("recipe") or RECIPE_AUTO)
    if recipe in (RECIPE_AUTO, RECIPE_NONE):
        return False
    try:
        from .styles.library import open_library
        from .styles.recipes import builtin_recipes

        lib = open_library()
        recipes = lib.all_recipes() if lib is not None else builtin_recipes()
    except Exception:  # noqa: BLE001 - a broken library never blocks writing
        return False
    r = recipes.get(recipe)
    return bool(r and r.cinematic)


def describe(look: Mapping[str, Any]) -> str:
    style = look.get("style", "?")
    bits = [style]
    if look.get("preset"):
        bits.append(f"preset {look['preset']}")
    if look.get("bg"):
        bits.append(f"background {look['bg']}")
    if look.get("accent"):
        bits.append(f"accent {look['accent']}")
    bits.append(f"narrator {look['character']}" if look.get("character") else "default narrator")
    if style == "avatar":
        bits.append(f"{look.get('background', 'gameplay')} background")
    else:
        bits.append(f"{look.get('theme', 'light')} theme")
        recipe = look.get("recipe", RECIPE_AUTO)
        bits.append("planner recipes" if recipe == RECIPE_AUTO else "no recipes" if recipe == RECIPE_NONE else f"recipe {recipe}")
    return ", ".join(bits)


def world_of(look: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> str:
    """The 3D world ("hero") of the look's preset, or
    "" for a flat look: with a world, every scene of the video is drawn in 3D
    (world3d.py). The preset comes from the look's `preset` or its
    `look-<preset>` recipe; the avatar style never has one."""
    try:
        resolved = dict(look) if look else look_from_env(environ)
    except LookError:
        return ""
    if resolved.get("style") == "avatar":
        return ""
    try:
        from .looks import RECIPE_PREFIX, get_preset

        recipe = str(resolved.get("recipe") or "")
        pid = recipe[len(RECIPE_PREFIX):] if recipe.startswith(RECIPE_PREFIX) else str(resolved.get("preset") or "")
        preset = get_preset(pid) if pid else None
    except Exception:  # noqa: BLE001
        return ""
    return preset.world if preset else ""


def threed_requested(look: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the look has a 3D world: the writer is then told to prefer 3D scenes."""
    return bool(world_of(look, environ))


def colors_for(theme: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The look's ground and accent as the composition should draw them, from
    LOOK_BG / LOOK_ACCENT (set by `applied()` for one video, or the daily
    default from .env), fitted to the theme by colors.fit_colors. Empty when
    neither is set, so the shipped look is untouched."""
    from .colors import fit_colors, normalise_hex

    environ = os.environ if environ is None else environ
    bg, accent = (normalise_hex(environ.get(k, "")) for k in ("LOOK_BG", "LOOK_ACCENT"))
    if not bg and not accent:
        return {}
    fitted = fit_colors(theme, bg, accent)
    return {k: fitted[k] for k in ("bg", "accent") if fitted[k]}
