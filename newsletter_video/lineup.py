"""The lineup: a standing description of the day's videos that the automatic
daily run (and the control panel) read, instead of one look for every video.

    10 slots a day, split into batches:  3 x Clean paper (one colour)
                                         4 x Dark glass (alternating colours)
                                         3 x Avatar

It lives in one validated JSON file next to .env (`lineup.json`, gitignored,
written atomically, schema-versioned). No file means exactly the behaviour
before lineups existed: VIDEOS_PER_DAY and the LOOK_* settings in .env. A
file takes precedence over those for the look of each video; the panel keeps
VIDEOS_PER_DAY in sync with its total.

A one-off (`once`) lineup applies only on its date and then the standing one
is back, with nothing to clean up: the date check does it.

Everything here is pure data in, data out: the same lineup and day always
expand to the same slots, including the "random" colour mode (seeded by the
day), so a re-run or re-render of a slot keeps its colour.
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from .colors import HEX, ROTATION_MAX, normalise_hex, DEFAULT_ROTATION

LINEUP_VERSION = 1
MAX_TOTAL = 20
MAX_BATCHES = 10
DEFAULT_TOTAL = 10
STYLES = ("explainer", "avatar", "avx")  # avx: the avatar explainer (avx_builder.py)
ACCENT_MODES = ("one", "alternate", "random")
ORDERS = ("interleave", "sequential")
FILE_NAME = "lineup.json"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLOT_FIELDS = ("style", "preset", "bg", "accent", "character", "background")
BACKGROUNDS = ("gameplay", "none")  # avatar videos: Minecraft parkour footage, or the plain graph-paper background
BACKGROUND_NAMES = {"gameplay": "Minecraft", "none": "Regular"}


class LineupError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def lineup_path(environ: Mapping[str, str] | None = None) -> Path:
    """BYTESIZE_LINEUP_FILE (tests, a second checkout) or `lineup.json` in the project root."""
    environ = os.environ if environ is None else environ
    given = environ.get("BYTESIZE_LINEUP_FILE", "").strip()
    if given:
        return Path(given)
    from .run_pipeline import PROJECT_ROOT

    return PROJECT_ROOT / FILE_NAME


# ---- validation ------------------------------------------------------------------------

def _preset_ids() -> list[str]:
    from .looks import preset_ids

    return preset_ids()


def _hex_or_blank(value: Any, label: str, errors: list[str]) -> str:
    if value in (None, ""):
        return ""
    hex_ = normalise_hex(str(value))
    if hex_ is None:
        errors.append(f"{label} must be a #rrggbb colour, not {str(value)[:20]!r}")
        return ""
    return hex_


def validate_batch(raw: Any, i: int, characters: set[str] | None, errors: list[str]) -> dict:
    where = f"batch {i + 1}"
    if not isinstance(raw, dict):
        errors.append(f"{where} must be an object")
        return {}
    out: dict[str, Any] = {}
    count = raw.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_TOTAL:
        errors.append(f"{where}: count must be a whole number from 1 to {MAX_TOTAL}")
        count = 1
    out["count"] = count
    style = str(raw.get("style") or "explainer").lower()
    if style not in STYLES:
        errors.append(f"{where}: style must be one of {', '.join(STYLES)}")
        style = "explainer"
    out["style"] = style
    out["name"] = str(raw.get("name") or "").strip()[:40]
    if style in ("explainer", "avx"):
        if style == "explainer":
            preset = str(raw.get("preset") or "paper").lower()
            if preset not in _preset_ids():
                errors.append(f"{where}: preset must be one of {', '.join(_preset_ids())}")
                preset = "paper"
            out["preset"] = preset
        else:
            out["preset"] = ""
        # avx: ONE plain ground for every scene of the video; blank = the house rhythm (avx_builder.HOUSE_RHYTHM)
        out["bg"] = _hex_or_blank(raw.get("bg"), f"{where}: bg", errors)
        mode = str(raw.get("accent_mode") or "one").lower()
        if mode not in ACCENT_MODES:
            errors.append(f"{where}: accent_mode must be one of {', '.join(ACCENT_MODES)}")
            mode = "one"
        out["accent_mode"] = mode
        out["accent"] = _hex_or_blank(raw.get("accent"), f"{where}: accent", errors)
        pal_raw = raw.get("palette") or []
        if not isinstance(pal_raw, list) or len(pal_raw) > ROTATION_MAX:
            errors.append(f"{where}: palette must be a list of at most {ROTATION_MAX} colours")
            pal_raw = []
        palette = [h for h in (_hex_or_blank(c, f"{where}: palette colour", errors) for c in pal_raw) if h]
        if mode != "one" and len(palette) < 2:
            errors.append(f"{where}: {mode} colours need a palette of at least 2 colours")
        out["palette"] = palette
        out["character"] = _character(raw, where, characters, errors) if style == "avx" else ""
        out["background"] = ""
    else:
        out["character"] = _character(raw, where, characters, errors)
        background = str(raw.get("background") or "gameplay").lower()
        if background not in BACKGROUNDS:
            errors.append(f"{where}: background must be one of {', '.join(BACKGROUNDS)}")
            background = "gameplay"
        out["background"] = background
        out.update(preset="", bg="", accent="", accent_mode="one", palette=[])
    return out


def _character(raw: Mapping[str, Any], where: str, characters: set[str] | None, errors: list[str]) -> str:
    """An avatar batch's (or avatar explainer batch's) narrator: a character folder name, "" for the default."""
    character = str(raw.get("character") or "")
    if character and not _SLUG.match(character):
        errors.append(f"{where}: character {character[:30]!r} is not an avatar name")
        return ""
    if characters is not None and character not in characters:
        errors.append(f"{where}: the avatar {character or 'default'!r} doesn't exist")
    return character


def _validate_core(raw: Any, characters: set[str] | None, errors: list[str], where: str = "") -> dict:
    if not isinstance(raw, dict):
        errors.append(f"{where}lineup must be an object")
        return {}
    batches_raw = raw.get("batches")
    if not isinstance(batches_raw, list) or not 1 <= len(batches_raw) <= MAX_BATCHES:
        errors.append(f"{where}needs between 1 and {MAX_BATCHES} batches")
        batches_raw = []
    batches = [validate_batch(b, i, characters, errors) for i, b in enumerate(batches_raw)]
    total = sum(b.get("count", 0) for b in batches)
    if "total" in raw and raw["total"] != total:
        errors.append(f"{where}total ({raw['total']}) must equal the sum of the batches ({total})")
    if not 1 <= total <= MAX_TOTAL:
        errors.append(f"{where}the batches must add up to between 1 and {MAX_TOTAL} videos, not {total}")
    order = str(raw.get("order") or "interleave").lower()
    if order not in ORDERS:
        errors.append(f"{where}order must be one of {', '.join(ORDERS)}")
        order = "interleave"
    slots: dict[str, dict] = {}
    slots_raw = raw.get("slots") or {}
    if not isinstance(slots_raw, dict):
        errors.append(f"{where}slots must be an object")
        slots_raw = {}
    for key, over in slots_raw.items():
        if not str(key).isdigit() or not 1 <= int(key) <= total:
            errors.append(f"{where}slot {str(key)[:10]!r} is outside 1..{total}")
            continue
        slots[str(int(key))] = _validate_override(over, f"{where}slot {key}", characters, errors)
    return {"total": total, "order": order, "batches": batches, "slots": slots}


def _validate_override(raw: Any, where: str, characters: set[str] | None, errors: list[str]) -> dict:
    if not isinstance(raw, dict):
        errors.append(f"{where}: an override must be an object")
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in SLOT_FIELDS:
            errors.append(f"{where}: unknown field {str(key)[:20]!r}")
        elif key == "style":
            if value not in STYLES:
                errors.append(f"{where}: style must be one of {', '.join(STYLES)}")
            else:
                out["style"] = value
        elif key == "preset":
            if value not in _preset_ids():
                errors.append(f"{where}: preset must be one of {', '.join(_preset_ids())}")
            else:
                out["preset"] = value
        elif key in ("bg", "accent"):
            out[key] = _hex_or_blank(value, f"{where}: {key}", errors)
        elif key == "background":
            if value not in BACKGROUNDS:
                errors.append(f"{where}: background must be one of {', '.join(BACKGROUNDS)}")
            else:
                out["background"] = value
        elif key == "character":
            value = str(value or "")
            if value and not _SLUG.match(value):
                errors.append(f"{where}: character is not an avatar name")
            elif characters is not None and value not in characters:
                errors.append(f"{where}: the avatar {value or 'default'!r} doesn't exist")
            else:
                out["character"] = value
    return out


def validate(raw: Any, characters: set[str] | None = None) -> dict:
    """A normalised lineup document, or LineupError listing every problem.
    `characters` (avatar names that exist, "" for the default) turns on the avatar existence check."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise LineupError(["lineup must be an object"])
    if raw.get("version", LINEUP_VERSION) != LINEUP_VERSION:
        errors.append(f"lineup version {raw.get('version')!r} is not supported (expected {LINEUP_VERSION})")
    out: dict[str, Any] = {"version": LINEUP_VERSION, **_validate_core(raw, characters, errors)}
    once = raw.get("once")
    if once:
        if not isinstance(once, dict) or not _DATE.match(str(once.get("date", ""))):
            errors.append("once needs a date like 2026-11-10")
        else:
            out["once"] = {"date": once["date"], **_validate_core(once, characters, errors, "once: ")}
    if errors:
        raise LineupError(errors)
    return out


# ---- the file --------------------------------------------------------------------------

def load(path: Path | None = None) -> dict | None:
    """The saved lineup, or None when there is none (or it is unreadable: the daily run then behaves as before)."""
    path = path or lineup_path()
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save(lineup: Mapping[str, Any], path: Path | None = None, characters: set[str] | None = None) -> dict:
    from .runstate import write_json_atomic

    clean = validate(dict(lineup), characters)
    write_json_atomic(path or lineup_path(), clean)
    return clean


def active(day: date, path: Path | None = None) -> tuple[dict, str] | None:
    """(core lineup for `day`, "once" | "default"), or None without a lineup file. A one-off for another date is ignored."""
    doc = load(path)
    if doc is None:
        return None
    once = doc.get("once")
    if once and once["date"] == day.isoformat():
        return {k: once[k] for k in ("total", "order", "batches", "slots")}, "once"
    return {k: doc[k] for k in ("total", "order", "batches", "slots")}, "default"


def default_from_environ(environ: Mapping[str, str] | None = None) -> dict:
    """What a lineup with no file amounts to: VIDEOS_PER_DAY videos in the .env look (the panel's starting point)."""
    environ = os.environ if environ is None else environ
    try:
        total = max(1, min(MAX_TOTAL, int(environ.get("VIDEOS_PER_DAY", DEFAULT_TOTAL))))
    except ValueError:
        total = DEFAULT_TOTAL
    style = (environ.get("VIDEO_STYLE") or "explainer").lower()
    batch: dict[str, Any] = {"count": total, "style": style if style in STYLES else "explainer"}
    if batch["style"] == "avx":
        batch.update(character=environ.get("CHARACTER", ""), bg=environ.get("LOOK_BG", ""), accent=environ.get("LOOK_ACCENT", ""))
    elif batch["style"] == "avatar":
        batch["character"] = environ.get("CHARACTER", "")
        batch["background"] = "none" if (environ.get("BACKGROUND_STYLE") or "").strip().lower() == "none" else "gameplay"
    else:
        preset = (environ.get("LOOK_PRESET") or "paper").lower()
        batch["preset"] = preset if preset in _preset_ids() else "paper"
        batch["bg"] = environ.get("LOOK_BG", "")
        rot = [c for c in (normalise_hex(x) for x in environ.get("LOOK_ACCENT_ROTATE", "").split(",")) if c][:ROTATION_MAX]
        if len(rot) >= 2:
            batch.update(accent_mode="alternate", palette=rot)
        else:
            batch["accent"] = environ.get("LOOK_ACCENT", "")
    return {"version": LINEUP_VERSION, "order": "interleave", "batches": [validate_batch(batch, 0, None, [])], "slots": {},
            "total": total}


# ---- expansion -------------------------------------------------------------------------

def _random_accent(day: date, batch: int, k: int, palette: list[str]) -> str:
    """The k-th random colour of a batch: seeded by day and batch, never the same twice in a row."""
    rng = random.Random(f"{day.isoformat()}:{batch}")
    previous = None
    pick = palette[0]
    for _ in range(k + 1):
        pick = rng.choice([c for c in palette if c != previous] or palette)
        previous = pick
    return pick


def family_of(look: Mapping[str, Any]) -> str:
    if look.get("style") == "avatar":
        return "avatar"
    if look.get("style") == "avx":
        return "avx"
    return "glass" if look.get("preset") == "cinematic-glass" else "paper"


FAMILY_NAMES = {"paper": "Clean paper", "glass": "Dark glass", "avatar": "Avatar", "avx": "Avatar explainer"}


def _avx_look(look: Mapping[str, Any]) -> dict[str, str]:
    """An avatar explainer slot's look: its narrator (left out for the default, so the run's CHARACTER
    narrates), one background colour ("" = the house rhythm) and the animation colour ("" = from the ground)."""
    out = {"style": "avx", "bg": look.get("bg") or "", "accent": look.get("accent") or ""}
    if look.get("character"):
        out["character"] = look["character"]
    return out


def expand(lineup: Mapping[str, Any], day: date) -> list[dict]:
    """The total number of slots in publish order, each with the partial look it renders with:
    {slot, batch, batch_name, accent_mode, family, label, look: {style, preset, bg, accent, character}}."""
    batches = list(lineup["batches"])
    queues = [list(range(b["count"])) for b in batches]
    order: list[tuple[int, int]] = []  # (batch, position within the batch)
    if lineup.get("order", "interleave") == "sequential":
        order = [(bi, k) for bi, q in enumerate(queues) for k in q]
    else:
        remaining = [list(q) for q in queues]
        while any(remaining):
            for bi, q in enumerate(remaining):
                if q:
                    order.append((bi, q.pop(0)))
    slots = []
    for n, (bi, k) in enumerate(order, start=1):
        b = batches[bi]
        if b["style"] == "avatar":
            look: dict[str, str] = {"style": "avatar", "character": b.get("character", ""),
                                    "background": b.get("background") or "gameplay"}
        else:
            mode, palette = b.get("accent_mode", "one"), list(b.get("palette") or [])
            if mode == "alternate" and len(palette) >= 2:
                accent = palette[k % len(palette)]
            elif mode == "random" and len(palette) >= 2:
                accent = _random_accent(day, bi, k, palette)
            else:
                accent = b.get("accent", "")
            if b["style"] == "avx":
                look = _avx_look({**b, "accent": accent})
            else:
                look = {"style": "explainer", "preset": b.get("preset") or "paper", "bg": b.get("bg", ""), "accent": accent}
        over = (lineup.get("slots") or {}).get(str(n)) or {}
        look.update(over)
        if look.get("style") == "avatar":  # an override can switch a slot's format
            look = {"style": "avatar", "character": look.get("character", ""),
                    "background": look.get("background") or "gameplay"}
        elif look.get("style") == "avx":
            look = _avx_look(look)
        else:
            look.setdefault("preset", "paper")
            look.pop("character", None)
            look.pop("background", None)
        family = family_of(look)
        label = FAMILY_NAMES[family]
        slots.append({"slot": n, "batch": bi + 1, "batch_name": b.get("name") or "", "accent_mode": b.get("accent_mode", "one"),
                      "overridden": bool(over), "family": family, "label": label, "look": look})
    return slots


def lineup_info(slot: Mapping[str, Any], day: date, total: int) -> dict:
    """What a produced video records about where it came from (sidecar, plan entry, upload record)."""
    look = slot["look"]
    return {"date": day.isoformat(), "slot": slot["slot"], "of": total, "batch": slot["batch"], "batch_name": slot["batch_name"],
            "accent_mode": slot["accent_mode"], "format": slot["label"], "style": look.get("style", ""),
            "preset": look.get("preset", ""), "bg": look.get("bg", ""), "accent": look.get("accent", ""),
            "character": look.get("character", ""), "background": look.get("background", ""),
            "overridden": bool(slot.get("overridden"))}


def format_label(info: Mapping[str, Any] | None, look: Mapping[str, Any] | None = None) -> str:
    """A video's format for the results table: from its lineup record, else from the look it rendered with."""
    src = dict(info or {})
    if not src.get("format") and look:
        src = {"format": FAMILY_NAMES[family_of(look)], "character": look.get("character", ""), "style": look.get("style", ""),
               "background": look.get("background", "")}
    name = str(src.get("format") or "")
    if name in ("Avatar", FAMILY_NAMES["avx"]) and src.get("character"):
        name = f"{name}: {src['character']}"
    if name.startswith("Avatar") and src.get("style") != "avx" and src.get("background") in BACKGROUND_NAMES:
        name = f"{name} · {BACKGROUND_NAMES[src['background']]}"
    return name or "Unknown format"


# ---- writing the day's scripts for a lineup --------------------------------------------

def write_scripts(slots: list[dict], day: date, total: int, editions: list, covered: set[str],
                  build: Callable[..., list]) -> list:
    """One writer call per format group (avatar cards, explainer scene plans, avatar explainer scene lists,
    and a 3D look's own notes differ per format), each asked for as many stories as that group has slots and told to skip the stories
    the groups before it picked. Every script gets its slot's look and lineup record; returned in slot order."""
    from .look import applied

    groups: dict[tuple, list[dict]] = {}
    for s in slots:
        look = s["look"]
        if look.get("style") in ("avatar", "avx"):  # avx: one call writes every avatar explainer slot's scene list
            key: tuple = (look["style"],)
        else:
            key = ("explainer", look.get("preset", "paper"))
        groups.setdefault(key, []).append(s)
    taken = set(covered)
    produced: list[tuple[int, Any]] = []
    for key, group in groups.items():
        wanted = {"style": key[0], **({"preset": key[1]} if len(key) > 1 else {})}
        with applied(wanted):
            scripts = build(editions, n_videos=len(group), exclude_titles=taken)
        extra = 0
        for k, script in enumerate(scripts):
            if k < len(group):
                slot = group[k]
            else:  # a must-cover video written past the lineup's count (MUST_COVER_EXTRA): the group's last look
                extra += 1
                slot = {**group[-1], "slot": total + extra}
            script.look = dict(slot["look"])
            script.lineup = lineup_info(slot, day, total)
            produced.append((slot["slot"], script))
            taken.update(st.title for st in script.stories)
    return [s for _, s in sorted(produced, key=lambda t: t[0])]


__all__ = ["LineupError", "validate", "load", "save", "active", "expand", "write_scripts", "lineup_path", "format_label",
           "lineup_info", "default_from_environ", "DEFAULT_ROTATION"]
