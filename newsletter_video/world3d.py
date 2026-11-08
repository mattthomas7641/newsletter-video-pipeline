"""3D worlds: what every scene of a 3D look shows, extracted from the plan.

The 3D looks are not a few special scenes on top of flat ones: for ANY story
every scene (title, statement, stat, quote, list, outro...) is drawn in 3D by
the composition's world renderer. This module turns a scene of the plan into
one small, normalised content block (`w3`) the renderer reads, so the
template needs no per-type logic for the three worlds:

    hero      cinematic-glass: a floating glass/neon object (cube, prism, orb,
              ring, shards, an icon on a glass plate) next to the sentence set
              as glowing type on a plate, over a grid floor with reflections;
              a different layout and camera move for each scene
    corridor  depth-cards: every scene is a plane in ONE continuous 3D space;
              scene changes are camera flights from plane to plane, never cuts
    metrics   data-focus: columns / rings for every beat; the numbers found in
              the scene's own words become the bars, with no number in the text
              the scene gets abstract (decorative, no figures) columns

Pure data in, no model and no clock: the same plan always gives the same blocks.
"""
from __future__ import annotations

import re
from typing import Any

WORLDS = ("hero", "corridor", "metrics")

_NUM = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?)\s*(%|x|k|m|b|t|bn|million|billion|trillion|thousand)?(?![\w])", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}


def _clip(text: Any, cap: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def numbers_in(text: str) -> list[dict]:
    """Numbers spoken or shown in `text`: [{label, value, shown}] in order.
    `value` is comparable within one unit class (percent, dollars, plain);
    `label` is the word that follows (or precedes) the number."""
    out = []
    words = text.split()
    for m in _NUM.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        try:
            value = float(raw.replace("$", "").replace(",", ""))
        except ValueError:
            continue
        if unit in _MULT:
            value *= _MULT[unit]
        klass = "pct" if unit == "%" else "usd" if raw.startswith("$") else "x" if unit == "x" else "num"
        after = re.findall(r"[A-Za-z][A-Za-z'-]*", text[m.end():])
        before = re.findall(r"[A-Za-z][A-Za-z'-]*", text[: m.start()])
        label = (after[0] if after else (before[-1] if before else ""))
        out.append({"label": _clip(label, 11), "value": value, "shown": _clip(m.group(0).strip(), 9), "class": klass})
    return out


def _series_numbers(series: list[dict], unit: str) -> list[dict]:
    return [{"label": _clip(p["label"], 11), "value": float(p["value"]), "shown": f"{p['value']:g}{unit}", "class": "series"} for p in series]


def scene_content(scene) -> dict:
    """The `w3` block of one scene (type, say and fields as in scene_plan)."""
    f, t = scene.fields, scene.type
    c: dict[str, Any] = {"kind": t, "label": "", "title": "", "emph": "", "value": "", "lines": [], "items": [], "numbers": [], "icon": ""}
    if t == "title":
        c.update(label=f.get("kicker", ""), title=f.get("headline", ""), icon=f.get("icon", ""))
    elif t == "statement":
        c.update(label=f.get("label", ""), title=f.get("text", ""), emph=f.get("emphasis", ""), icon=f.get("icon", ""))
    elif t == "stat":
        c.update(label=f.get("label", ""), value=f.get("value", ""), lines=[x for x in (f.get("context", ""),) if x])
        if f.get("trend"):
            c["numbers"] = [{"label": "", "value": float(v), "shown": f"{v:g}", "class": "trend"} for v in f["trend"][:5]]
    elif t == "quote":
        c.update(label=f.get("attribution", ""), title=f.get("text", ""))
    elif t == "list":
        c.update(title=f.get("title", ""), items=[{"label": "", "text": x} for x in f.get("items", [])], icon=(f.get("icons") or [""])[0])
    elif t == "timeline":
        c.update(title=f.get("title", ""), items=[{"label": s["label"], "text": s["text"]} for s in f.get("steps", [])])
    elif t == "comparison":
        sides = [f.get("left") or {}, f.get("right") or {}]
        c.update(title=f.get("title", ""), items=[{"label": s.get("label", ""), "text": " ".join(x for x in (s.get("value"), s.get("note")) if x)} for s in sides])
        if all((s.get("number") or 0) > 0 for s in sides):
            c["numbers"] = [{"label": _clip(s["label"], 11), "value": float(s["number"]), "shown": s.get("value", ""), "class": "series"} for s in sides]
    elif t == "chart":
        c.update(title=f.get("title", ""), items=[{"label": p["label"], "text": f"{p['value']:g}{f.get('unit', '')}"} for p in f.get("series", [])])
        c["numbers"] = _series_numbers(f.get("series", []), f.get("unit", ""))
    elif t == "diagram":
        c.update(title=f.get("title", ""), label=f.get("center", ""), sub=f.get("kind", ""), items=[{"label": n["label"], "text": n.get("text", "")} for n in f.get("nodes", [])])
    elif t in ("object3d", "depth", "glass"):
        c.update(title=f.get("title", ""), label=f.get("label", "") or f.get("center", ""), icon=f.get("icon", ""))
        if f.get("kind") == "bars":
            c["items"] = [{"label": p["label"], "text": f"{p['value']:g}{f.get('unit', '')}"} for p in f.get("series", [])]
            c["numbers"] = _series_numbers(f.get("series", []), f.get("unit", ""))
        else:
            c["items"] = [{"label": n["label"], "text": n.get("text", "")} for n in f.get("nodes", [])]
    elif t == "device":
        c.update(title=f.get("title", ""), label=f.get("label", ""), items=[{"label": "", "text": x} for x in f.get("lines", [])])
    elif t == "kinetic":
        c.update(label=f.get("label", ""), value=f.get("value", ""), title=" ".join(f.get("lines", [])))
    elif t == "outro":
        c.update(title=f.get("text", ""), icon=f.get("icon", "comment"))
    if not c["numbers"]:
        nums = numbers_in(" ".join([c["title"], c["value"]] + c["lines"]))
        # bars need two or more figures of one kind; one lone figure is a "hero number"
        klass = max({n["class"] for n in nums}, key=lambda k: sum(n["class"] == k for n in nums), default="") if nums else ""
        same = [n for n in nums if n["class"] == klass][:5]
        c["numbers"] = same if len(same) >= 2 else nums[:1]
    c["numbers"] = [{"label": n["label"], "value": n["value"], "shown": n["shown"]} for n in c["numbers"] if n["value"] >= 0][:5]
    c["label"] = _clip(c["label"], 40)
    c["title"] = _clip(c["title"], 110)
    c["value"] = _clip(c["value"], 12)
    c["lines"] = [_clip(x, 60) for x in c["lines"]][:3]
    c["items"] = [{"label": _clip(i["label"], 24), "text": _clip(i["text"], 52)} for i in c["items"]][:5]
    c["say"] = _clip(scene.say, 200)
    return c


# --- compositions: what each scene is, chosen from its content ----------------------------
# A 3D composition may appear in a scene ONLY if it depicts something that scene's own words are
# about; every other scene stays a clean 2D scene ("flat", the ordinary explainer scene of its
# kind in the preset's colours), so a video is a mix of 2D and 3D shots and nothing is decoration.
# The gate is the scene's text (narration, title, label, items), never an icon alone: an icon the
# writer picked loosely cannot summon a globe, and a generic list is never a stack of plates.

OBJECT_PATTERNS = [
    ("chip", r"\b(chips?|gpus?|cpus?|semiconductors?|silicon|processors?|hbm|wafers?|fabs?|asics?)\b"),
    ("cubegrid", r"\b(data ?cent(?:er|re)s?|servers?|server racks?|clusters?|superclusters?|gigawatts?|hyperscal\w*)\b"),
    ("shield", r"\b(security|encrypt\w*|privacy|breach\w*|hack\w*|exploit\w*|vulnerab\w*|malware|cyberattacks?)\b"),
    ("streaks", r"\b(faster|fastest|speed(?:s|ing)?|instantly|latency|real-time|lightning|quicker|milliseconds?)\b"),
    ("globe", r"\b(worldwide|global\w*|countries|earth|planet|satellites?|orbit\w*)\b|(?<!real-)(?<!real )\bthe world\b"),
    ("network", r"\b(neural|llms?|ai models?|machine learning|ai agents?)\b"),
    ("cube", r"\b(packages?|parcels?|shipments?|shipping)\b"),
]
MONEY = re.compile(r"[$\u20ac\u00a3]\s?\d|\b\d[\d.,]*\s?(?:billion|million|trillion|bn)\b", re.I)
LAYERS = re.compile(r"\b(layers?|stack|tiers?|levels?|osi|pipeline|stages?|abstraction)\b", re.I)
LOOP = re.compile(r"\b(loop|cycle|circle|flywheel)\b", re.I)
PLANE_KINDS = ("title", "statement", "quote", "outro")  # what the corridor sets as planes in depth
_STOP = set("the a an and or but to of in on at for with than as by from into that its is are was were not no per this these those their his her our your it be been have has had will would could may might can just more most very over under about after before while when where which who whom what how why".split())


def scene_text(c: dict) -> str:
    """Everything the scene says or shows, lower-cased: the gate for every 3D composition."""
    parts = [c.get("say", ""), c.get("title", ""), c.get("label", ""), c.get("value", "")] + list(c.get("lines", []))
    parts += [(i["label"] + " " + i["text"]) for i in c.get("items", [])]
    return " ".join(parts).lower()


def phrase_chunks(text: str, n: int = 3) -> list[str]:
    words = text.split()
    n = max(1, min(n, len(words)))
    if len(words) <= 3:
        n = min(n, 2) if len(words) > 1 else 1
    size = -(-len(words) // n)
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def keywords_of(text: str, n: int = 4) -> list[str]:
    """The longest distinctive words of a line, in reading order (words that appear in it, never invented)."""
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z0-9$%][A-Za-z0-9$%'.-]*", text):
        key = w.lower().strip(".,'")
        if key in _STOP or len(key) < 4 or key in seen:
            continue
        seen.add(key)
        out.append(w.strip(".,"))
    top = sorted(out, key=len, reverse=True)[:n]
    return [w for w in out if w in top][:n]


def object_for(c: dict, say: str = "") -> str:
    """The object that is the thing the scene talks about, or '': its own text must name it (or a
    direct synonym); money needs an amount."""
    text = scene_text(c)
    if MONEY.search(text):
        return "coins"
    for obj, pattern in OBJECT_PATTERNS:
        if re.search(pattern, text):
            return obj
    return ""


def _figures_on_screen(c: dict) -> list[dict]:
    """Figures that are what the scene is about: its stat value, series or comparison, or two or more in its title."""
    return c["numbers"] if c["kind"] in ("stat", "chart", "comparison", "object3d") or len(c["numbers"]) >= 2 else []


def compose(c: dict, world: str) -> str:
    """The 3D composition this scene's content justifies in this world, or 'flat' (the 2D scene)."""
    kind, items, nums = c["kind"], c["items"], _figures_on_screen(c)
    text = scene_text(c)
    pct = len(nums) == 1 and nums[0]["shown"].endswith("%") and 0 < nums[0]["value"] <= 100
    if world == "corridor":
        return "plane" if kind in PLANE_KINDS else "flat"
    if len(nums) >= 2 and kind in ("stat", "chart", "comparison", "object3d", "statement"):
        return "bars"
    if kind == "comparison" and len(items) == 2 and (world == "hero" or nums):
        return "split"
    if len(nums) == 1 and kind in ("stat",) and (pct or nums[0]["shown"][:1] in "$\u20ac\u00a3" or nums[0]["value"] >= 100):
        return "gauge" if pct else "monolith"  # a small count ("3 cities") is not worth a monolith: it stays a 2D stat
    if len(items) >= 2 and kind in ("diagram", "timeline", "list", "depth", "object3d") and LAYERS.search(text):
        return "stack"
    if world == "hero":
        if kind in ("timeline", "diagram") and c.get("sub") != "hub" and len(items) >= 3:
            return "path"  # a real sequence of steps: planes the camera walks through as each is spoken
        if kind == "diagram" and c.get("sub") == "hub" and len(items) >= 3:
            return "hub"
        if kind == "outro":
            return "object:ring" if LOOP.search(text) else "flat"
        obj = object_for(c)
        if obj and kind in ("title", "statement", "quote", "stat"):
            return f"object:{obj}"
    return "flat"


def choose_compositions(contents: list[dict], world: str) -> list[str]:
    """One composition per scene, from its content alone. A relevant 3D shot is never swapped for
    another to vary the video, but two identical 3D shots in a row are not allowed: the second one
    falls back to its 2D scene, which keeps the video a mix of 2D and 3D."""
    chosen: list[str] = []
    for c in contents:
        pick = compose(c, world)
        if chosen and pick != "flat" and pick == chosen[-1] and world != "corridor":
            pick = "flat"
        chosen.append(pick)
    return chosen


def annotate(contents: list[dict], world: str) -> None:
    """Adds `comp` (a composition or 'flat'), `shape` and `words` (the emphasised keyword) to each block, in place."""
    for c, comp in zip(contents, choose_compositions(contents, world)):
        c["comp"] = comp
        c["shape"] = comp.split(":", 1)[1] if comp.startswith("object:") else ""
        c["chunks"] = [(i["label"] + " " + i["text"]).strip() for i in c["items"]][:4] if comp == "path" else []
        c["words"] = []
