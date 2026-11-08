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
        c.update(title=f.get("title", ""), label=f.get("center", ""), items=[{"label": n["label"], "text": n.get("text", "")} for n in f.get("nodes", [])])
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
        nums = numbers_in(" ".join([scene.say, c["title"], c["value"]]))
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
# The renderer has a library of 3D compositions. Which one a scene gets comes from what it
# says (an icon or a keyword picks an object that IS the thing discussed; figures pick bars,
# a monolith or a gauge; a list, a process or a comparison picks a stack, a hub or a split;
# anything without a visual becomes typography in depth) and from rotation, so no two
# consecutive scenes share a composition and a 7-scene video shows at least 5 different ones.

OBJECT_BY_ICON = {
    "chip": "chip", "brain": "network", "robot": "network", "cloud": "cubegrid", "database": "cubegrid",
    "energy": "cubegrid", "globe": "globe", "users": "network",
    "dollar": "coins", "money": "coins", "wallet": "coins", "lock": "shield", "shield": "shield", "key": "shield",
    "bug": "shield", "box": "cube", "layers": "stack3", "building": "cubegrid",
}
OBJECT_BY_KEYWORD = [
    ("chip", r"\b(chips?|gpus?|semiconductors?|silicon|processors?|memory|hbm|wafers?|fabs?)\b"),
    ("network", r"\b(ai models?|neural|llms?|agents?|robots?|intelligence|machine learning)\b"),
    ("coins", r"\b(valuation|funding|revenue|profit|loss|price|pricing|billion|million|ipo|raised|payments?)\b"),
    ("streaks", r"\b(faster|fastest|speed|instantly|latency|real-time|lightning|quicker)\b"),
    ("shield", r"\b(security|encrypt\w*|privacy|breach|hack\w*|exploit\w*|vulnerab\w*|safety)\b"),
    ("cubegrid", r"\b(data ?cent(?:er|re)s?|servers?|clusters?|gigawatts?|compute)\b"),
]
TYPE_COMPS = ("type-stack", "type-mono", "type-orbit", "type-plate")
_STOP = set("the a an and or but to of in on at for with than as by from into that its is are was were not no per this these those their his her our your it be been have has had will would could may might can just more most very over under about after before while when where which who whom what how why".split())


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


def object_for(c: dict, say: str) -> str:
    """The object that is the thing the scene talks about, or ''."""
    if c.get("icon") in OBJECT_BY_ICON:
        return OBJECT_BY_ICON[c["icon"]]
    text = " ".join([say, c.get("title", ""), c.get("label", "")]).lower()
    for obj, pattern in OBJECT_BY_KEYWORD:
        if re.search(pattern, text):
            return obj
    return ""


def _candidates(c: dict, world: str, index: int) -> tuple[list[str], list[str]]:
    """(data-bearing compositions, typographic ones), best first. A data-bearing one shows what the
    scene's items or figures are (a stack of its steps, bars of its numbers, the object it names); the
    typographic ones set its words in depth and suit any scene."""
    kind, items, nums = c["kind"], c["items"], c["numbers"]
    pct = len(nums) == 1 and nums[0]["shown"].endswith("%") and 0 < nums[0]["value"] <= 100
    rot = TYPE_COMPS[(index + 1) % len(TYPE_COMPS):] + TYPE_COMPS[:(index + 1) % len(TYPE_COMPS)]
    typo = list(rot)
    obj = object_for(c, c.get("say", "")) if world == "hero" else ""
    if kind == "outro":
        return (["object:ring"] if world == "hero" else []), ["type-mono", "type-plate"]
    if kind == "title":
        return ([f"object:{obj}"] if obj else []), ["type-mono", "type-orbit"]
    data: list[str] = []
    if len(nums) >= 2 and kind in ("stat", "chart", "comparison", "object3d", "statement", "quote"):
        data.append("bars")
    if len(nums) == 1:
        data.append("gauge" if pct else "monolith")
    if kind == "comparison" and len(items) == 2:
        data.insert(0, "split") if (world == "hero" or not nums) else data.append("split")
    if len(items) >= 2 and kind != "comparison":
        flow = kind in ("timeline", "diagram", "depth", "object3d", "glass", "list", "device")
        data.append("stack" if flow else "type-stack")
        if kind == "diagram" and world == "hero":
            data.insert(0, "hub")
        if len(items) >= 3 and "type-stack" not in data:
            data.append("type-stack")
    if obj and world == "hero":
        data.append(f"object:{obj}")
    return data, typo


def choose_compositions(contents: list[dict], world: str) -> list[str]:
    """One composition per scene. Content decides first: the first data-bearing candidate that differs
    from the previous scene's wins (never swapped for typography just to vary); a scene without one
    gets typography in depth, preferring a form not used yet. No two consecutive scenes match."""
    chosen: list[str] = []
    used: set[str] = set()
    for i, c in enumerate(contents):
        data, typo = _candidates(c, world, i)
        prev = chosen[-1] if chosen else ""
        pick = next((x for x in data if x != prev), None)
        if pick is None:
            pick = next((x for x in typo if x != prev and x not in used), None) or next((x for x in typo if x != prev), typo[0])
        chosen.append(pick)
        used.add(pick)
    return chosen


def annotate(contents: list[dict], world: str) -> None:
    """Adds `comp`, `shape`, `chunks` and `words` (keyword labels) to each block, in place."""
    for c, comp in zip(contents, choose_compositions(contents, world)):
        c["comp"] = comp
        c["shape"] = comp.split(":", 1)[1] if comp.startswith("object:") else ""
        text = c["title"] or " ".join(c["lines"])
        if comp == "type-stack":
            # the sentence in chunks, or (a scene with items but no sentence) its items, each set in depth
            c["chunks"] = phrase_chunks(text, 3) if text else [(i["label"] + " " + i["text"]).strip() for i in c["items"]][:3]
        else:
            c["chunks"] = []
        c["words"] = keywords_of(c["title"] + " " + " ".join(c["lines"]) + " " + c.get("say", ""), 5) if comp in ("type-orbit", "type-mono") else []
