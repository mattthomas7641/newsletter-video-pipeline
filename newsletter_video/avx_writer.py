"""The writer path for the avatar explainer (VIDEO_STYLE=avx, or a lineup's
"Avatar explainer" batch): Claude writes each video as a list of AVX scenes
(avx_builder.STYLES), one spoken line plus one purpose-built graphic each.

It mirrors explainer_writer: one structured-output call writes the whole
day, every scene list is validated in code (validate_scenes, plus no two
videos of a day with the same middle sequence of styles), the rejected
ones go back to Claude once with the validator's exact errors, and a video
that still fails is skipped with its reasons logged, never rendered broken.
Then an editor pass (critique: one more call for the whole day) reads every
video like a first-time viewer against its story; a flagged video gets one
rewrite with the editor's reasons and is dropped if it is still flagged.
Stories whose summary is too thin for 5-8 scenes are not offered at all
(select_stories).
Without Claude, heuristic_avx_scripts turns the rule-based scripts into
plain scene lists (kinetic hook, list, outro) flagged writer="heuristic", so
run_pipeline's FALLBACK_UPLOAD policy applies exactly as for the other
formats.

The facts rule is enforced, not just asked for: every numeral on screen or
in a spoken line, and every stat, chart value and timeline year, must occur
in the text of the stories the video uses. A story with no numbers therefore
cannot get a stat or chart scene, and there is no "illustrative" data.

The result is a script_writer.Script with `avx_scenes` set (the builder's
scene format, without colours: the look adds those at render time) and its
hook/beats/outro holding the same spoken lines, so narration, word count,
metadata, uploads and covered-story history work unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from . import factcheck
from .avx_builder import STYLES
from .scraper import Story
from .script_writer import (
    ANTHROPIC_TIMEOUT_SECONDS,
    DEFAULT_WRITER_EFFORT,
    WRITER_MODEL,
    Beat,
    Script,
    WriterNetworkError,
    _stream_with_retries,
    _trim,
)

log = logging.getLogger("newsletter_video")

MIDDLE_STYLES = ("stat", "timeline", "versus", "chart", "relation", "list")
MIN_SCENES, MAX_SCENES = 5, 8
MIN_WORDS, MAX_WORDS = 80, 115  # the absolute floor and ceiling of spoken words
# Measured on the ten rendered avx drafts of 2026-11-09: 2.44-2.96 spoken words per second, 2.74 on average,
# pauses between scenes included. A video must still run at least MIN_SECONDS at that pace (83+ words), so a
# short one is sent back to add one more sourced scene, never padding (90 was an arbitrary floor that dropped
# good videos at 83-88 words, the Nvidia CEO story among them).
WORDS_PER_SECOND = 2.74
MIN_SECONDS = 30.0
SCENE_MIN_WORDS, SCENE_MAX_WORDS = 4, 32
MIN_STYLES = 3
# The narrator's poses, by ROLE (characters.py: each picture of a character has one). The writer names a
# role per scene; the builder resolves it against the active character's pictures. The shrug is reserved
# for the LAST scene (the closing question): anywhere else it is repaired away here and in the builder.
POSE_ROLES = {"neutral": "standing, relaxed (a good opener)", "point": "pointing up at the graphic (numbers, charts)",
              "think": "thinking, hand on chin (a twist, a timeline, an open question)",
              "present": "presenting, one hand on hip, the other palm open (comparisons, lists, who-did-what)",
              "shrug": "shrugging, both hands open: ONLY for the last scene, the closing question"}
SHRUG = "shrug"
MAX_POSES = 8  # a stored plan may name a picture by index (older plans): 0..7
POSES = POSE_ROLES  # the old name
ICONS = ("wave", "spark", "chip")
# Character caps, measured against the template (hf_template_avx/index.html) at its type sizes; the
# template also shrinks a too-wide line to fit, so these keep the type big rather than merely on screen.
CAPS = {
    "eyebrow": 24, "kinetic_line": 16, "outro_line": 16, "cta": 16,
    "stat_label": 28, "stat_source": 48, "prefix": 2, "suffix": 4, "stat_shown": 7,
    "tl_year": 10, "tl_title": 16, "tl_tail": 30,
    "vs_title": 10, "vs_tag": 12, "vs_item": 16, "vs_verdict": 18,
    "chart_name": 8, "x_label": 6, "y_label": 24, "callout": 24, "gap": 10,
    "rel_name": 18, "rel_role": 22, "initials": 3, "verb": 14, "phrase": 14,
    "list_title": 28, "list_item": 22,
}
COUNTS = {"kinetic_lines": (2, 4), "outro_lines": (1, 3), "tl_nodes": (2, 3), "vs_items": (2, 3), "series": (1, 2),
          "points": (2, 6), "list_items": (2, 3)}


class AvxPlanError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# --- facts: which numbers a video may show ---------------------------------------------

_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?|(?<=[A-Za-z$€£-])\d[\d,]*(?:\.\d+)?")
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "dozen": 12, "half": 0.5,
    "twice": 2, "double": 2, "doubled": 2, "triple": 3, "tripled": 3, "quadrupled": 4,
}


def numbers_in(text: str) -> list[float]:
    """Every numeral in `text` ("2,350" -> 2350, "$7.5B" -> 7.5, "GPT-5" -> 5, "40%" -> 40)."""
    out = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0).replace(",", "").rstrip(".")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def story_numbers(text: str) -> set[float]:
    """The numbers a story gives: its numerals plus the plain number words ("two", "doubled")."""
    found = set(numbers_in(text))
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if word in _NUMBER_WORDS:
            found.add(float(_NUMBER_WORDS[word]))
    return found


def number_ok(x: float, allowed: set[float]) -> bool:
    """`x` is one of the story's numbers: exactly, rounded to 0-2 decimals, or the same quantity in other
    units of a thousand ("2.35 billion" shown as 2,350 million or 2.4B)."""
    for a in allowed:
        for scale in (1, 1e3, 1e6, 1e9, 1e12, 1e-3, 1e-6, 1e-9, 1e-12, 100, 0.01):  # 100: "$0.71" said "71 cents"
            v = a * scale
            if abs(v - x) <= 1e-9 * max(1.0, abs(x)):
                return True
            # rounded for the screen (2.35 -> 2.4), but never by more than 5% (1.6 is not "2")
            if v and abs(v - x) <= 0.05 * abs(v) and any(abs(round(v, d) - x) <= 1e-9 * max(1.0, abs(x)) for d in (0, 1, 2)):
                return True
    return False


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _tokens(text: str) -> list[str]:
    return [t for t in (_norm(w) for w in (text or "").split()) if t]


# --- scenes: from the writer's flat schema to the builder's format ------------------------

def _cue(word: Any) -> dict:
    word = str(word or "").strip()
    return {"cue": word} if word else {}


def _side(raw: dict) -> dict:
    return {"name": str(raw.get("name", "")).strip(), "tag": str(raw.get("tag", "")).strip(),
            "items": [str(i).strip() for i in raw.get("items") or [] if str(i).strip()],
            "initials": str(raw.get("initials", "")).strip(), "cue": str(raw.get("cue", "")).strip()}


def scene_from_writer(raw: dict) -> dict:
    """One scene of Claude's flat schema as avx_builder takes it (`data` per style, cues as {"cue": word})."""
    style = str(raw.get("style", "")).strip().lower()
    scene: dict[str, Any] = {"style": style, "pose": raw.get("pose", 0), "say": str(raw.get("say", "")).strip(),
                             "eyebrow": str(raw.get("eyebrow", "")).strip()}
    text, text2 = str(raw.get("text", "")).strip(), str(raw.get("text2", "")).strip()
    lines = [{"text": str(ln.get("text", "")).strip(), **({"em": True} if ln.get("em") else {}), **_cue(ln.get("cue"))}
             for ln in raw.get("lines") or [] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    a, b = _side(raw.get("a") or {}), _side(raw.get("b") or {})
    if style == "kinetic":
        data: dict[str, Any] = {"lines": lines}
        if raw.get("cue") and any(ln.get("em") for ln in lines):
            data["underline"] = _cue(raw.get("cue"))
        scene["captions"] = False
    elif style == "outro":
        data = {"lines": lines}
        if text:
            data["cta"] = {"text": text, **_cue(raw.get("text_cue"))}
        scene["captions"] = False
    elif style == "stat":
        value = raw.get("value", 0)
        data = {"value": value, "decimals": int(raw.get("decimals") or 0), "prefix": str(raw.get("prefix", "")).strip(),
                "suffix": str(raw.get("suffix", "")).strip(), "count": _cue(raw.get("cue")),
                "label": str(raw.get("label", "")).strip(), "dots": 0}
        if str(raw.get("source", "")).strip():
            data["source"] = str(raw.get("source", "")).strip()
        _percent_dots(data)
    elif style == "timeline":
        data = {"nodes": [{"year": str(n.get("year", "")).strip(), "title": str(n.get("title", "")).strip(),
                           "icon": str(n.get("icon", "")).strip() or "spark", **_cue(n.get("cue"))}
                          for n in raw.get("nodes") or [] if isinstance(n, dict)]}
        if text:
            data["tail"] = {"text": text, **_cue(raw.get("text_cue"))}
    elif style == "versus":
        # a = the side named first (the right-hand, highlighted card), b = the other side (it gets the verdict stamp)
        data = {"right": {"title": a["name"], "tag": a["tag"], "items": a["items"], **_cue(a["cue"])},
                "left": {"title": b["name"], "tag": b["tag"], "items": b["items"], **_cue(b["cue"])}}
        if text:
            data["verdict"] = {"text": text, **_cue(raw.get("text_cue"))}
    elif style == "chart":
        data = {"y_label": str(raw.get("label", "")).strip(), "x_labels": [str(x).strip() for x in raw.get("x_labels") or []],
                "series": [{"name": str(sr.get("name", "")).strip(), "values": list(sr.get("values") or []), **_cue(sr.get("cue"))}
                           for sr in raw.get("series") or [] if isinstance(sr, dict)]}
        if text:
            data["callout"] = {"text": text, **_cue(raw.get("text_cue"))}
        if text2 and len(data["series"]) == 2:
            data["gap"] = {"text": text2, **_cue(raw.get("text2_cue"))}
    elif style == "relation":
        data = {"from": {"name": a["name"], "role": a["tag"], "initials": a["initials"] or _initials(a["name"]), **_cue(a["cue"])},
                "to": {"name": b["name"], "role": b["tag"], "initials": b["initials"] or _initials(b["name"]), **_cue(b["cue"])},
                "verb": {"text": text2, **_cue(raw.get("text2_cue"))}, "phrase": {"text": text, **_cue(raw.get("text_cue"))}}
    elif style == "list":
        data = {"title": str(raw.get("label", "")).strip(),
                "items": [{"text": str(i.get("text", "")).strip(), **_cue(i.get("cue"))}
                          for i in raw.get("items") or [] if isinstance(i, dict) and str(i.get("text", "")).strip()]}
    else:
        data = {}
    scene["data"] = data
    return scene


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2] if w[:1].isalnum()).upper()[:3]


def _percent_dots(data: dict) -> None:
    """A percentage up to 100 gets the unit chart (20 dots, each 5%, filled to the value); any other number
    gets none: dots for an arbitrary quantity would be a made-up scale."""
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return
    if data.get("suffix") == "%" and 0 < value <= 100:
        data.update(dots=20, fill=max(1, round(value / 5)), dot_label="1 dot = 5%")
    else:
        data.update(dots=0)
        data.pop("fill", None)
        data.pop("dot_label", None)


# --- validation ------------------------------------------------------------------------------

def _texts_on_screen(scene: dict) -> list[tuple[str, str]]:
    """(where, text) for every string the scene shows."""
    d, out = scene.get("data") or {}, [("eyebrow", scene.get("eyebrow", ""))]
    for i, ln in enumerate(d.get("lines") or []):
        out.append((f"line {i + 1}", ln.get("text", "")))
    for key in ("label", "title", "y_label", "dot_label"):
        if isinstance(d.get(key), str):
            out.append((key, d[key]))
    for key in ("cta", "tail", "verdict", "callout", "gap", "verb", "phrase"):
        if isinstance(d.get(key), dict):
            out.append((key, d[key].get("text", "")))
    for i, it in enumerate(d.get("items") or []):
        out.append((f"item {i + 1}", it.get("text", "") if isinstance(it, dict) else str(it)))
    for i, n in enumerate(d.get("nodes") or []):
        out += [(f"node {i + 1} title", n.get("title", ""))]
    for side in ("left", "right"):
        if isinstance(d.get(side), dict):
            out += [(f"{side} title", d[side].get("title", "")), (f"{side} tag", d[side].get("tag", ""))]
            out += [(f"{side} item", str(x)) for x in d[side].get("items") or []]
    for side in ("from", "to"):
        if isinstance(d.get(side), dict):
            out += [(f"{side} name", d[side].get("name", "")), (f"{side} role", d[side].get("role", ""))]
    for i, sr in enumerate(d.get("series") or []):
        out.append((f"series {i + 1} name", sr.get("name", "")))
    return out


def _cap(errors: list[str], where: str, text: str, cap: int) -> None:
    if len(text) > cap:
        errors.append(f"{where} {text!r} is {len(text)} characters (at most {cap})")


def _need(errors: list[str], where: str, text: Any) -> None:
    if not isinstance(text, str) or not text.strip():
        errors.append(f"{where} is required")


def _count(errors: list[str], where: str, items: Any, key: str) -> list:
    lo, hi = COUNTS[key]
    items = items if isinstance(items, list) else []
    if not lo <= len(items) <= hi:
        errors.append(f"{where} needs {lo}-{hi} entries, not {len(items)}")
    return items


def _check_style_data(scene: dict, errors: list[str], strict: bool) -> None:
    style, d = scene["style"], scene.get("data")
    if not isinstance(d, dict):
        errors.append("data must be an object")
        return
    caps = strict  # caps are what Claude is held to; a stored plan or fallback is only checked for shape
    if style in ("kinetic", "outro"):
        lines = _count(errors, "lines", d.get("lines"), "kinetic_lines" if style == "kinetic" else "outro_lines") if strict \
            else (d.get("lines") or [])
        if not lines:
            errors.append("lines are required")
        for i, ln in enumerate(lines):
            _need(errors, f"line {i + 1}", ln.get("text") if isinstance(ln, dict) else None)
            if caps and isinstance(ln, dict):
                _cap(errors, f"line {i + 1}", str(ln.get("text", "")), CAPS["kinetic_line" if style == "kinetic" else "outro_line"])
        if style == "outro" and isinstance(d.get("cta"), dict) and caps:
            _cap(errors, "cta", str(d["cta"].get("text", "")), CAPS["cta"])
    elif style == "stat":
        value = d.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"value must be a number, not {value!r}")
            return
        if value < 0:
            errors.append("value must not be negative")
        if not isinstance(d.get("decimals", 0), int) or not 0 <= d.get("decimals", 0) <= 2:
            errors.append("decimals must be 0, 1 or 2")
        _need(errors, "label", d.get("label"))
        if caps:
            _cap(errors, "label", str(d.get("label", "")), CAPS["stat_label"])
            _cap(errors, "prefix", str(d.get("prefix", "")), CAPS["prefix"])
            _cap(errors, "suffix", str(d.get("suffix", "")), CAPS["suffix"])
            shown = f"{d.get('prefix', '')}{float(value):.{max(0, min(2, int(d.get('decimals') or 0)))}f}{d.get('suffix', '')}"
            _cap(errors, "the number as shown", shown, CAPS["stat_shown"])
            if d.get("source"):
                _cap(errors, "source", str(d["source"]), CAPS["stat_source"])
    elif style == "timeline":
        nodes = _count(errors, "nodes", d.get("nodes"), "tl_nodes")
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                errors.append(f"node {i + 1} must be an object")
                continue
            _need(errors, f"node {i + 1} year", n.get("year"))
            _need(errors, f"node {i + 1} title", n.get("title"))
            if n.get("icon") not in ICONS:
                n["icon"] = "spark"
            if caps:
                _cap(errors, f"node {i + 1} year", str(n.get("year", "")), CAPS["tl_year"])
                _cap(errors, f"node {i + 1} title", str(n.get("title", "")), CAPS["tl_title"])
        if isinstance(d.get("tail"), dict) and caps:
            _cap(errors, "tail", str(d["tail"].get("text", "")), CAPS["tl_tail"])
    elif style == "versus":
        for side in ("right", "left"):
            s = d.get(side)
            if not isinstance(s, dict):
                errors.append(f"{side} card is required")
                continue
            _need(errors, f"{side} title", s.get("title"))
            items = _count(errors, f"{side} items", s.get("items"), "vs_items")
            if caps:
                _cap(errors, f"{side} title", str(s.get("title", "")), CAPS["vs_title"])
                _cap(errors, f"{side} tag", str(s.get("tag", "")), CAPS["vs_tag"])
                for x in items:
                    _cap(errors, f"{side} item", str(x), CAPS["vs_item"])
        if isinstance(d.get("verdict"), dict) and caps:
            _cap(errors, "verdict", str(d["verdict"].get("text", "")), CAPS["vs_verdict"])
    elif style == "chart":
        series = _count(errors, "series", d.get("series"), "series")
        lengths = set()
        for i, sr in enumerate(series):
            if not isinstance(sr, dict):
                errors.append(f"series {i + 1} must be an object")
                continue
            values = _count(errors, f"series {i + 1} values", sr.get("values"), "points")
            lengths.add(len(values))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                errors.append(f"series {i + 1} values must all be numbers")
            elif any(v < 0 for v in values):
                errors.append(f"series {i + 1} values must not be negative")
            elif values and max(values) <= 0:
                errors.append(f"series {i + 1} needs a value above 0")
            _need(errors, f"series {i + 1} name", sr.get("name"))
            if caps:
                _cap(errors, f"series {i + 1} name", str(sr.get("name", "")), CAPS["chart_name"])
        if len(lengths) > 1:
            errors.append("every series needs the same number of values")
        x_labels = d.get("x_labels") or []
        if lengths and x_labels and len(x_labels) != next(iter(lengths)):
            errors.append("x_labels needs one label per value")
        if caps:
            for x in x_labels:
                _cap(errors, "x label", str(x), CAPS["x_label"])
            _cap(errors, "y label", str(d.get("y_label", "")), CAPS["y_label"])
            if isinstance(d.get("callout"), dict):
                _cap(errors, "callout", str(d["callout"].get("text", "")), CAPS["callout"])
            if isinstance(d.get("gap"), dict):
                _cap(errors, "gap label", str(d["gap"].get("text", "")), CAPS["gap"])
    elif style == "relation":
        for side in ("from", "to"):
            p = d.get(side)
            if not isinstance(p, dict):
                errors.append(f"{side} is required")
                continue
            _need(errors, f"{side} name", p.get("name"))
            if not p.get("initials"):
                p["initials"] = _initials(str(p.get("name", ""))) or "?"
            if caps:
                _cap(errors, f"{side} name", str(p.get("name", "")), CAPS["rel_name"])
                _cap(errors, f"{side} role", str(p.get("role", "")), CAPS["rel_role"])
                _cap(errors, f"{side} initials", str(p.get("initials", "")), CAPS["initials"])
        for key in ("verb", "phrase"):
            if not isinstance(d.get(key), dict):
                errors.append(f"{key} is required")
                continue
            _need(errors, key, d[key].get("text"))
            if caps:
                _cap(errors, key, str(d[key].get("text", "")), CAPS[key])
    elif style == "list":
        _need(errors, "title", d.get("title"))
        items = _count(errors, "items", d.get("items"), "list_items") if strict else (d.get("items") or [])
        if not items:
            errors.append("items are required")
        for i, it in enumerate(items):
            _need(errors, f"item {i + 1}", it.get("text") if isinstance(it, dict) else None)
            if caps and isinstance(it, dict):
                _cap(errors, f"item {i + 1}", str(it.get("text", "")), CAPS["list_item"])
        if caps:
            _cap(errors, "title", str(d.get("title", "")), CAPS["list_title"])


def _check_facts(scene: dict, allowed: set[float], source_text: str, errors: list[str], repairs: list[str]) -> None:
    """Numbers and names on screen (and numerals in the spoken line) must come from the stories."""
    d = scene.get("data") or {}
    style = scene["style"]
    shown: list[tuple[str, float]] = []
    if style == "stat" and isinstance(d.get("value"), (int, float)):
        shown.append(("the stat value", float(d["value"])))
    if style == "chart":
        for sr in d.get("series") or []:
            shown += [(f"chart value of {sr.get('name', '?')!r}", float(v)) for v in sr.get("values") or []
                      if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for where, text in _texts_on_screen(scene) + [("x label", str(x)) for x in d.get("x_labels") or []] \
            + [("year", str(n.get("year", ""))) for n in d.get("nodes") or [] if isinstance(n, dict)] \
            + [("spoken line", scene.get("say", ""))]:
        if where == "dot_label":
            continue  # "1 dot = 5%" is the chart's own key, not a claim
        shown += [(f"{where} ({text[:30]!r})", x) for x in numbers_in(text)]
    for where, x in shown:
        if not number_ok(x, allowed):
            errors.append(f"{where}: the number {x:g} is not in the story text (use only numbers the story gives)")
    if style == "relation":
        names = proper_nouns(source_text)
        for side in ("from", "to"):
            name = str((d.get(side) or {}).get("name", ""))
            if name and not any(t in names for t in _tokens(name) if len(t) >= 2):
                errors.append(f"{side} name {name!r} is not a person, company or product the story names "
                              "(relation scenes are about who does what to whom, not ideas)")
    if style == "stat" and d.get("source"):
        words = set(_tokens(source_text))
        if not any(t in words for t in _tokens(str(d["source"])) if len(t) >= 3):
            repairs.append(f"dropped the source {d['source']!r}: it is not named in the story")
            d.pop("source")


_COMMON_STARTERS = {"the", "a", "an", "this", "that", "these", "those", "some", "it", "its", "they", "we", "you", "he",
                    "she", "but", "and", "so", "as", "in", "on", "at", "for", "with", "after", "while", "how", "why",
                    "what", "when", "where", "who", "new", "more", "most", "many", "one", "two", "three", "here", "there"}


def proper_nouns(text: str) -> set[str]:
    """Normalised names (people, companies, products) in `text`: a capitalised word in the middle of a
    sentence, a word with inner capitals or digits (OpenAI, GPT-5), or a capitalised sentence opener that
    never appears in lower case and is not a common opening word ("Anthropic claims...")."""
    lower = {_norm(w) for w in re.findall(r"\b[a-z][\w&-]*", text or "")}
    out = set()
    for line in (text or "").splitlines():
        titled = sum(w[:1].isupper() for w in line.split()) > 0.6 * max(1, len(line.split()))  # a headline
        for m in re.finditer(r"\b([A-Z][\w&-]*)", line):
            word, tok = m.group(1), _norm(m.group(1))
            before = line[: m.start()].rstrip()
            opener = not before or before[-1] in ".!?:\"'" or titled
            inner = bool(re.search(r"[A-Z0-9]", word[1:]))
            if inner or not opener or (tok not in lower and tok not in _COMMON_STARTERS):
                out.add(tok)
    return {t for t in out if t}


def _typed_words_spoken(scene: dict, errors: list[str]) -> None:
    """Kinetic and outro scenes have no captions: their type IS the spoken line, so the words on screen
    must be (mostly) the words said. Numerals pass: "$11 billion" is said "eleven billion"."""
    spoken = set(_tokens(scene.get("say", "")))
    for i, ln in enumerate((scene.get("data") or {}).get("lines") or []):
        toks = [t for t in _tokens(ln.get("text", "")) if not t.isdigit()]
        if toks and sum(t in spoken for t in toks) / len(toks) < 0.6:
            errors.append(f"line {i + 1} {ln.get('text')!r} is not what the narrator says in this scene "
                          "(the type is the spoken words: copy them from `say`)")


def _repair_cues(scene: dict, repairs: list[str], where: str) -> None:
    """A cue must be a word of this scene's spoken line (case and punctuation ignored); one that is not
    is dropped, and the graphic then lands on the template's own timing instead."""
    spoken = set(_tokens(scene.get("say", "")))

    def walk(x):
        if isinstance(x, dict):
            if "cue" in x:
                first = _norm(str(x["cue"]).split()[0]) if str(x["cue"]).split() else ""
                if not first or first not in spoken:
                    repairs.append(f"{where}: dropped the cue {x['cue']!r} (not a word of the spoken line)")
                    x.pop("cue")
                    x.pop("at", None)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(scene.get("data"))
    d = scene.get("data") or {}
    if scene.get("style") in ("kinetic", "outro"):
        # The type is the spoken line: each line lands on its own first word when that word is said (a line
        # cued on the last word of the scene would appear just as the scene leaves).
        toks = _tokens(scene.get("say", ""))
        for ln in d.get("lines") or []:
            first = _norm(str(ln.get("text", "")).split()[0]) if str(ln.get("text", "")).split() else ""
            if first and first in spoken:
                ln["cue"] = ln["text"].split()[0]
            elif "cue" in ln and _norm(str(ln["cue"]).split()[0]) in toks[-2:]:
                ln.pop("cue")
    if isinstance(d.get("count"), dict) and not d["count"]:
        d.pop("count")


def validate_scenes(raw: Any, source_text: str | None = None, strict: bool = True) -> tuple[list[dict], list[str]]:
    """(clean scenes, repair notes) or AvxPlanError listing every problem.

    `strict` holds a scene list to everything Claude is asked for: 5-8 scenes, a kinetic hook first and an
    outro last, the middle from MIDDLE_STYLES, at least three styles, 90-115 spoken words and the text caps.
    Not strict (a stored plan being reloaded, the rule-based fallback) checks the shape only. With
    `source_text` (the stories' titles and summaries) every number is checked against it."""
    if not isinstance(raw, list) or not raw:
        raise AvxPlanError(["scenes must be a non-empty list"])
    errors: list[str] = []
    repairs: list[str] = []
    scenes: list[dict] = []
    allowed = story_numbers(source_text) if source_text is not None else None
    for i, item in enumerate(raw):
        where = f"scene {i + 1}"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object")
            continue
        scene = json.loads(json.dumps(item))  # a deep copy that is plain JSON
        for key in ("bg", "accent", "transition"):  # colours belong to the look, applied at render time
            scene.pop(key, None)
        style = scene.get("style")
        if style not in STYLES:
            errors.append(f"{where}: style must be one of {', '.join(STYLES)}, not {style!r}")
            continue
        where = f"scene {i + 1} ({style})"
        scene_errors: list[str] = []
        say = scene.get("say")
        if not isinstance(say, str) or not say.strip():
            scene_errors.append("say is required")
            say = ""
        n_words = len(say.split())
        if strict and not SCENE_MIN_WORDS <= n_words <= SCENE_MAX_WORDS:
            scene_errors.append(f"say is {n_words} words (each scene {SCENE_MIN_WORDS}-{SCENE_MAX_WORDS})")
        if strict and say and not re.search(r"[.!?][\"'”’)]?$", say.strip()):
            scene_errors.append(f"say {say[-40:]!r} does not end as a complete sentence (end it with . ? or !)")
        pose = scene.get("pose", 0)
        last = i == len(raw) - 1
        if isinstance(pose, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,23}", pose.strip().lower()):
            scene["pose"] = pose = pose.strip().lower()
            if pose == SHRUG and not last:
                repairs.append(f"{where}: the shrug is kept for the last scene; the builder picks another pose here")
                scene.pop("pose")
        elif isinstance(pose, bool) or not isinstance(pose, int) or not 0 <= pose < MAX_POSES:
            repairs.append(f"{where}: pose {pose!r} is not a pose role; using the main pose")
            scene["pose"] = 0
        eyebrow = scene.get("eyebrow", "")
        if not isinstance(eyebrow, str):
            scene["eyebrow"] = ""
        elif len(eyebrow) > CAPS["eyebrow"]:
            scene["eyebrow"] = eyebrow[: CAPS["eyebrow"]].rsplit(" ", 1)[0].rstrip(" ·,-")
            repairs.append(f"{where}: shortened the eyebrow to {scene['eyebrow']!r}")
        _check_style_data(scene, scene_errors, strict)
        if strict and style in ("kinetic", "outro") and not scene_errors:
            _typed_words_spoken(scene, scene_errors)
        if not scene_errors:
            _repair_cues(scene, repairs, where)
            if allowed is not None:
                _check_facts(scene, allowed, source_text or "", scene_errors, repairs)
        errors += [f"{where}: {e}" for e in scene_errors]
        scenes.append(scene)
    if strict and not errors:
        styles = [s["style"] for s in scenes]
        if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
            errors.append(f"a video has {MIN_SCENES}-{MAX_SCENES} scenes, not {len(scenes)}")
        if styles[0] != "kinetic":
            errors.append("the first scene must be the kinetic hook")
        if styles[-1] != "outro":
            errors.append("the last scene must be the outro")
        bad = [f"{i + 2} ({s})" for i, s in enumerate(styles[1:-1]) if s not in MIDDLE_STYLES]
        if bad:
            errors.append(f"middle scenes must be one of {', '.join(MIDDLE_STYLES)}; not scene {', '.join(bad)}")
        if len(set(styles)) < MIN_STYLES or len(set(styles[1:-1])) < 2:
            errors.append(f"use at least {MIN_STYLES} different scene styles, with at least 2 different middle styles "
                          f"(this video's middle uses {', '.join(sorted(set(styles[1:-1]))) or 'nothing'})")
        words = sum(len(s["say"].split()) for s in scenes)
        if not MIN_WORDS <= words <= MAX_WORDS:
            errors.append(f"the spoken lines total {words} words (allowed {MIN_WORDS}-{MAX_WORDS})")
        elif words / WORDS_PER_SECOND < MIN_SECONDS:
            errors.append(f"the spoken lines total {words} words, about {words / WORDS_PER_SECOND:.0f} seconds voiced "
                          f"(at least {MIN_SECONDS:.0f}): add one more scene with another fact the story gives (quoted "
                          "in the evidence), never filler")
    if errors:
        raise AvxPlanError(errors)
    return scenes, repairs


# --- scripts -----------------------------------------------------------------------------------

def _card(scene: dict) -> str:
    """A short on-screen summary of a scene for the classic script fields (sidecar, plan review)."""
    d = scene.get("data") or {}
    style = scene["style"]
    if style == "stat":
        return f"{d.get('prefix', '')}{d.get('value')}{d.get('suffix', '')} {d.get('label', '')}".strip()
    if style == "timeline":
        return " → ".join(f"{n.get('year', '')} {n.get('title', '')}".strip() for n in d.get("nodes") or [])
    if style == "versus":
        return f"{(d.get('right') or {}).get('title', '')} vs {(d.get('left') or {}).get('title', '')}"
    if style == "chart":
        return str((d.get("callout") or {}).get("text") or d.get("y_label") or "Chart")
    if style == "relation":
        return f"{(d.get('from') or {}).get('name', '')} {(d.get('verb') or {}).get('text', '')} {(d.get('to') or {}).get('name', '')}".strip()
    if style == "list":
        return str(d.get("title") or " · ".join(i.get("text", "") for i in d.get("items") or []))
    return " ".join(ln.get("text", "") for ln in d.get("lines") or [])


def script_from_scenes(scenes: list[dict], title: str, badge: str, stories: list[Story], post_caption: str = "",
                       hashtags: list[str] | None = None, writer: str = "claude") -> Script:
    """`writer` is "claude" for Claude's scene lists and "heuristic" for the rule-based fallback, so
    run_pipeline's FALLBACK_UPLOAD policy applies to this format too."""
    return Script(
        title=title, badge=badge.upper()[:14], hook=scenes[0]["say"],
        beats=[Beat(say=s["say"], card=_card(s), highlight=None, kind="stat" if s["style"] == "stat" else "fact",
                    stat=_card(s).split(" ")[0] if s["style"] == "stat" else "") for s in scenes[1:-1]],
        outro=scenes[-1]["say"] if len(scenes) > 1 else "", stories=stories, post_caption=post_caption.strip(),
        hashtags=[re.sub(r"[^\w]", "", h).lower() for h in (hashtags or []) if h.strip()][:5], writer=writer,
        avx_scenes=scenes,
    )


def plan_dict(scenes: list[dict]) -> dict:
    """What a sidecar or plan entry stores (under `scene_plan`) so the video can be re-rendered without
    asking Claude again."""
    return {"style": "avx", "scenes": scenes}


def is_avx_plan(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("style") == "avx" and isinstance(raw.get("scenes"), list)


def scenes_for(script: Script) -> list[dict]:
    """The scenes to render for a script: its own, or for a script written in another format (a planned
    video switched to this format, the Lineup's preview on a sample story) a plain structural list."""
    if getattr(script, "avx_scenes", None):
        return script.avx_scenes
    log.warning("Avatar explainer: %r has no scene list; rendering a structural one from its script",
                getattr(script, "title", "?"))
    return structural_scenes(script)


# --- the rule-based fallback --------------------------------------------------------------------

def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    """Greedy word wrap; whatever does not fit in `max_lines` joins the last line (the template shrinks it)."""
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return lines


def _short(text: str, cap: int) -> str:
    text = re.sub(r"\s+", " ", text.strip().rstrip(".!?…:;,"))
    if len(text) <= cap:
        return text
    cut = text[:cap].rsplit(" ", 1)[0] if " " in text[:cap] else text[:cap]
    return cut.rstrip(" ,;:-—–")


def _eyebrow(script: Script) -> str:
    return (script.badge or "News").title()[: CAPS["eyebrow"]]


def structural_scenes(script: Script) -> list[dict]:
    """A plain scene list from a hook/beats/outro script: the hook as kinetic type, the beats as checklist
    scenes (up to three beats each, every item cued on its first spoken word), the outro with a CTA card.
    Only words of the script: nothing invented."""
    eyebrow = _eyebrow(script)
    hook_lines = _wrap(script.hook.strip(), CAPS["kinetic_line"], 4)
    lines = [{"text": ln, "cue": ln.split()[0]} for ln in hook_lines]
    if len(lines) >= 2:
        lines[-1]["em"] = True
    hook: dict[str, Any] = {"style": "kinetic", "pose": "neutral", "eyebrow": eyebrow, "captions": False, "say": script.hook.strip(),
                            "data": {"lines": lines}}
    if len(lines) >= 2:
        hook["data"]["underline"] = {"cue": lines[-1]["text"].split()[0]}
    scenes = [hook]
    beats = [b for b in script.beats if b.say.strip()]
    for k in range(0, len(beats), 3):
        group = beats[k:k + 3]
        items = []
        for b in group:
            text = _short(b.card or b.say, CAPS["list_item"])
            items.append({"text": text, "cue": text.split()[0] if text else ""})
        scenes.append({"style": "list", "pose": "present" if k == 0 else "point", "eyebrow": eyebrow,
                       "say": " ".join(b.say.strip() for b in group),
                       "data": {"title": "The short version" if k == 0 else "And there's more", "items": items}})
    outro_say = script.outro.strip() or "Follow for more tech news."
    cta = "Tell me below" if re.search(r"comment|below|tell me|let me know", outro_say, re.IGNORECASE) else "Follow for more"
    scenes.append({"style": "outro", "pose": SHRUG, "eyebrow": "Your call", "captions": False, "say": outro_say,
                   "data": {"lines": [{"text": ln, "cue": ln.split()[0]} for ln in _wrap(outro_say, CAPS["outro_line"], 3)],
                            "cta": {"text": cta, "cue": cta.split()[0]}}})
    clean, _ = validate_scenes(scenes, strict=False)  # repairs drop cues the voice does not say
    return clean


def heuristic_avx_scripts(scripts: list[Script]) -> list[Script]:
    """Rule-based scripts (no Claude) as avatar explainer scripts: structural scene lists, writer="heuristic",
    logged loudly because they are plainer than Claude's."""
    out = []
    for script in scripts:
        try:
            scenes = structural_scenes(script)
        except AvxPlanError as exc:
            log.error("Avatar explainer: skipping %r, no valid structural scene list:\n  %s", script.title,
                      "\n  ".join(exc.errors))
            continue
        log.warning("Avatar explainer: %r uses a structural scene list from the rule-based script (no Claude plan)",
                    script.title)
        out.append(script_from_scenes(scenes, script.title, script.badge, script.stories, script.post_caption,
                                      script.hashtags, writer="heuristic"))
    return out


# --- the prompt -----------------------------------------------------------------------------------

AVX_WRITER_SYSTEM = f"""\
You are the head writer for a daily tech-news channel on TikTok and YouTube Shorts. Each video is an \
avatar explainer: a friendly cartoon narrator stands at the bottom left of a plain coloured background \
and talks; above and to the right of him, one purpose-built motion graphic per beat of the story plays \
in time with his words (kinetic type, a number counting up, a timeline drawing itself, two cards facing \
off, a line chart, who-did-what-to-whom, a checklist). Your viewers are curious, tech-savvy people aged \
18-35 who scroll fast and leave the moment a video gets boring or generic.

Your job: read today's newsletter stories and write the day's videos as scene lists.

Choosing what to cover
- The channel covers technology: AI and AI models, software and developer tools, big tech companies and \
startups, chips and hardware, gadgets, apps and platforms, cybersecurity, and the money and policy fights \
around them.
- Pick the most interesting of those stories: surprising, consequential, weird, or genuinely useful to \
know. Big launches, real numbers, power moves between companies, failures, and things that change how \
people work or live.
- Do not cover pure science (physics, space, biology, medicine, climate) unless the story is really about \
a tech product or company.
@SCOPE@- Skip sponsored items, job posts, generic marketing or growth advice, and minor \
product updates.
- Each video tells ONE story (or 2 tightly linked ones). Never cover the same story in two videos; \
newsletters often repeat a story under different titles, treat those as one.
- Only pick a story whose summary gives enough substance for 5-8 distinct beats (a concrete claim plus \
evidence, a consequence and a catch). Skip thin ones rather than padding them.

The scenes (`style` exactly; one spoken line each, one graphic each)
- kinetic: ALWAYS the first scene, the hook. `lines`: 2-4 lines of at most {CAPS['kinetic_line']} characters \
each, the KEY WORDS of the hook copied verbatim from its `say` in the same order (a shortened version \
made only of words the narrator says, never synonyms: say "OpenAI raised eleven billion dollars and still \
missed its target." -> lines "OpenAI raised" / "$11 billion" / "and still missed" / "its target."; numbers \
may be written as digits); mark the one punch line `em` (it is set big in serif italic in the accent \
colour); `cue` = the word to underline (a word of the em line). Keep the hook's `say` short (8-16 words) \
so the lines can carry it. Lead with the news itself; never a greeting.
- stat: one number from the story as the hero, counting up. `value` (a number), `decimals` (0-2), \
`prefix` (at most {CAPS['prefix']} characters, e.g. "$"), `suffix` (at most {CAPS['suffix']}, e.g. "B", "%", "x", "M+"); \
prefix + number + suffix at most {CAPS['stat_shown']} characters; `label` what it counts, at most \
{CAPS['stat_label']} characters, not repeating the number; `cue` = the spoken word where the number is \
said; `source` = optional, who reported it, only if the story names them. A percentage is drawn as dots.
- timeline: 2-3 dated steps, each `nodes` entry a `year` (at most {CAPS['tl_year']} characters: "2011", \
"March 2025", "Now", "Next"), a `title` (at most {CAPS['tl_title']} characters), an `icon` (wave = voice or \
sound, spark = AI or a launch, chip = hardware or software) and a `cue`; optional `text` (at most \
{CAPS['tl_tail']} characters) under the last step, with `text_cue`.
- versus: two sides facing off. `a` = the side you name FIRST (highlighted card), `b` = the other side; \
each has a `name` (at most {CAPS['vs_title']} characters), a `tag` (at most {CAPS['vs_tag']}), 2-3 `items` \
(at most {CAPS['vs_item']} characters each) and a `cue`; optional `text` = a verdict stamped on side b (at \
most {CAPS['vs_verdict']} characters) with `text_cue`.
- chart: a line chart of real figures from the story. `series`: 1-2 series (`name` at most \
{CAPS['chart_name']} characters, 2-6 `values`, all numbers from the story, oldest first, none negative, a \
`cue`); `x_labels` one per value (at most {CAPS['x_label']} characters, e.g. years from the story); \
`label` = the y axis (at most {CAPS['y_label']} characters); `text` = a callout card (at most \
{CAPS['callout']} characters) with `text_cue`; with two series `text2` = a short gap label (at most \
{CAPS['gap']} characters) with `text2_cue`. Use it only when the story itself gives the series.
- relation: who does what to whom, between two real people, companies or products the story names \
(never an idea, a trend or a group like "investors"; if the story has no such pair, use another style). \
`a` = the actor, `b` = the one acted on: `name` (at most \
{CAPS['rel_name']} characters), `tag` = their role (at most {CAPS['rel_role']}), `initials` (at most \
{CAPS['initials']}), a `cue`; `text2` = the verb (at most {CAPS['verb']} characters, "pushes", "sues", \
"buys") with `text2_cue`; `text` = the key line in huge serif type (at most {CAPS['phrase']} characters) \
with `text_cue`.
- list: a checklist. `label` = its title (at most {CAPS['list_title']} characters; its second word is set \
in the accent serif), 2-3 `items` (at most {CAPS['list_item']} characters, each with a `cue`). Say the \
items in order so each ticks in as it is spoken.
- outro: ALWAYS the last scene. `lines`: 1-3 lines (at most {CAPS['outro_line']} characters) with the \
closing question exactly as `say` asks it (no captions here: the type is the spoken words), one `em`; `text` = the call-to-action card (at most {CAPS['cta']} characters, e.g. "Tell \
me below") with `text_cue`. Its `say` is the spoken sign-off: a reason to comment or follow, tied to \
the topic.
Every scene also has an `eyebrow` (a 1-3 word chapter label above the graphic, at most \
{CAPS['eyebrow']} characters, e.g. "The stakes", "How we got here") and a `pose` for the narrator, by \
name: @POSES@. Open on neutral, point at numbers and charts, think at a twist or a timeline, present at a \
comparison or a list. The shrug is ONLY for the last scene (the closing question), never earlier. Never \
the same pose in two scenes in a row.
Fields a style does not use are sent empty: "" for text, [] for lists, 0 for numbers, false for em.

Writing each video: a clear mini-story a stranger can follow
- The viewer has NOT read the article. Every `say` is one or two complete, natural spoken sentences that \
make sense on their own when heard once: a subject, a verb, plain words. Never a fragment, never a \
headline squeezed into half a sentence, never a pun or a phrase that only makes sense if you already know \
the story. Read each line aloud in your head: if a friend would say "wait, what?", rewrite it.
- The hook (scene 1) states the actual news or claim in ONE clear, complete sentence, in plain words \
(who did what, or what is surprising), e.g. "OpenAI raised eleven billion dollars and still missed its \
target." Never paraphrase or compress the story's title or opening line into a riddle. A short second \
sentence may add the stakes. Its on-screen `lines` are those same words.
- Each scene advances exactly ONE idea, and the scenes build in order: the claim (hook) -> the evidence \
or how it works -> why it matters or what changes -> the catch, the twist or the takeaway -> the closing \
question. No scene repeats an earlier one in other words, and no scene is filler: never a recap scene \
that lists again what was already said ("so three things: the round, the price, the doubts").
- A stat or chart scene shows a number that directly proves the point its own line is making (the \
growth, the price, the size of the problem), and the line says what the number means. A number that is \
only background (how many companies an investor backs, how many tips an article lists) is not a stat \
scene: mention it in words or leave it out.
- The graphic matches its line: a versus compares the two things the line contrasts, a list holds the \
items the line names in the same order, a timeline the dates the line walks through.
- Structure: 5-8 scenes: the kinetic hook, then 3-6 middle scenes chosen from stat, timeline, versus, \
chart, relation and list to FIT each beat of THAT story (a number gets a stat, a sequence a timeline, a \
rivalry or before/after a versus, a power move a relation, a set of takeaways a list), then the outro. \
Use at least two different middle styles and avoid the same style twice in a row. A story with less to \
say gets fewer scenes, never stretched ones.
- Vary the shape across the day: the videos must not share one middle sequence of styles. Change which \
styles you use, their order, where the number lands (early proof or late payoff) and how many scenes \
there are (some 5, some 6, some 7 or 8).
- Length: 85-115 spoken words in total across all `say` lines (never under 83: the video must run at least \
30 seconds; if a story runs short, add one more scene with another fact the story gives, never filler). Each `say` is 6-25 words, conversational, \
as if explaining to a smart friend.
- Cues: every `cue`, `text_cue` and `text2_cue` is ONE word copied exactly from that scene's own `say`, \
the moment that element should appear. A list item, a timeline step or a versus side is cued on the word \
that names it. Write each `say` so the elements are named early enough to be seen: never cue the last \
word of the line.
- On-screen text is short and punchy and respects every character cap above; text over a cap is rejected \
and the video is dropped.
- `title`: a scroll-stopping post title under 70 characters. `badge`: a 1-2 word topic label, under 14 \
characters. `post_caption`: 1-3 short sentences: what happened and why it matters, plus a question that \
invites comments. `hashtags`: 3-5 relevant hashtags without the # sign; no "shorts".

Accuracy (checked automatically: a video that breaks these is rejected)
- Use only facts present in the provided stories. Never invent facts, numbers, dates, names, quotes or \
outcomes; say "reportedly" for anything the source frames as a report or rumor.
- Every number shown on screen or written as digits in `say` (a stat value, chart values, timeline years, \
numbers inside labels and items) must appear in the text of the stories this video uses. If the story has \
no number, do not use a stat or a chart scene. Never make up "illustrative" chart data: a chart needs \
real values from the story for every point. Use timeline years only when the story gives them.
- The people and companies in a relation scene must be named in the story.

Tone and spoken text
- Energetic but credible: no hype words such as "game-changer", "revolutionary" or "insane", no emojis, \
no hashtags in spoken or on-screen text.
- `say` is read by a text-to-speech voice: write it exactly as it should be spoken, with no URLs, \
parentheses, bullet characters or markdown. Write money and units in words ("ten cents per million \
tokens", "1.8 billion dollars"); symbols like "$" belong only on screen.
"""

AVX_WRITER_SYSTEM = AVX_WRITER_SYSTEM.replace("@SCOPE@", factcheck.SCOPE_NOTE)

_STR = {"type": "string"}
_SIDE_SCHEMA = {"type": "object", "properties": {
    "name": _STR, "tag": _STR, "items": {"type": "array", "items": _STR}, "initials": _STR, "cue": _STR},
    "required": ["name", "tag", "items", "initials", "cue"], "additionalProperties": False}
SCENE_SCHEMA = {"type": "object", "properties": {
    "style": {"type": "string", "enum": list(STYLES)},
    "pose": {"type": "string", "description": "the narrator's pose by role (neutral, point, think, present...); "
             "shrug only in the last scene"},
    "eyebrow": {"type": "string", "description": f"1-3 word chapter label, at most {CAPS['eyebrow']} characters"},
    "say": {"type": "string", "description": "the spoken line for this scene"},
    "lines": {"type": "array", "description": "kinetic and outro only, else []", "items": {
        "type": "object", "properties": {"text": _STR, "em": {"type": "boolean"}, "cue": _STR},
        "required": ["text", "em", "cue"], "additionalProperties": False}},
    "items": {"type": "array", "description": "list only, else []", "items": {
        "type": "object", "properties": {"text": _STR, "cue": _STR}, "required": ["text", "cue"], "additionalProperties": False}},
    "nodes": {"type": "array", "description": "timeline only, else []", "items": {
        "type": "object", "properties": {"year": _STR, "title": _STR, "icon": {"type": "string", "enum": list(ICONS)}, "cue": _STR},
        "required": ["year", "title", "icon", "cue"], "additionalProperties": False}},
    "a": {**_SIDE_SCHEMA, "description": "versus: the side named first; relation: the actor; else empty"},
    "b": {**_SIDE_SCHEMA, "description": "versus: the other side; relation: the one acted on; else empty"},
    "value": {"type": "number", "description": "stat only, else 0"},
    "decimals": {"type": "integer", "description": "stat only: 0-2, else 0"},
    "prefix": {"type": "string", "description": "stat only, else ''"},
    "suffix": {"type": "string", "description": "stat only, else ''"},
    "label": {"type": "string", "description": "stat: what the number counts; chart: the y axis; list: the title; else ''"},
    "source": {"type": "string", "description": "stat only: who reported it, if the story names them; else ''"},
    "series": {"type": "array", "description": "chart only, else []", "items": {
        "type": "object", "properties": {"name": _STR, "values": {"type": "array", "items": {"type": "number"}}, "cue": _STR},
        "required": ["name", "values", "cue"], "additionalProperties": False}},
    "x_labels": {"type": "array", "items": _STR, "description": "chart only, else []"},
    "text": {"type": "string", "description": "timeline tail, versus verdict, chart callout, relation key line, outro CTA; else ''"},
    "text_cue": _STR,
    "text2": {"type": "string", "description": "relation verb, chart gap label; else ''"},
    "text2_cue": _STR,
    "cue": {"type": "string", "description": "kinetic: the word to underline; stat: the word where the number is said; else ''"},
}, "required": ["style", "pose", "eyebrow", "say", "lines", "items", "nodes", "a", "b", "value", "decimals", "prefix",
                "suffix", "label", "source", "series", "x_labels", "text", "text_cue", "text2", "text2_cue", "cue"],
    "additionalProperties": False}

_AVX_SCHEMA = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "badge": {"type": "string", "description": "1-2 word topic label, under 14 characters"},
                    "story_ids": {"type": "array", "items": {"type": "integer"}},
                    "scenes": {"type": "array", "items": SCENE_SCHEMA},
                    "post_caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "evidence": factcheck.EVIDENCE_SCHEMA,
                },
                "required": ["title", "badge", "story_ids", "scenes", "post_caption", "hashtags", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["videos"],
    "additionalProperties": False,
}


def pose_roles() -> list[str]:
    """The pose roles the active character has (its pictures' roles), else the house set."""
    try:
        from .characters import active

        character = active()
    except Exception:  # noqa: BLE001 - a missing CHARACTER folder is reported elsewhere
        character = None
    if character and character.avatar and character.pose_roles:
        return list(character.pose_roles)
    return ["neutral", "point", "think", "shrug"]


def _poses_text() -> str:
    roles = pose_roles()
    order = [r for r in POSE_ROLES if r in roles and r != SHRUG] + [r for r in roles if r not in POSE_ROLES]
    if SHRUG in roles:
        order.append(SHRUG)
    return "; ".join(f'"{r}" = {POSE_ROLES.get(r, "another pose of the narrator")}' for r in order)


def _system_prompt() -> str:
    """The prompt plus the narrator's persona (tone only), like the other writers."""
    prompt = AVX_WRITER_SYSTEM.replace("@POSES@", _poses_text()) + factcheck.GROUNDING_RULES
    try:
        from .characters import active

        character = active()
    except Exception:  # a missing CHARACTER folder is reported by run_pipeline, not here
        character = None
    if character and character.persona:
        prompt += (
            f"\nNarrator character\n- The narrator is {character.name}: {character.persona.rstrip('. ')}. "
            "This shapes the tone and word choice of the spoken lines only. Every fact, number and claim must "
            "still come from the stories, and the word budget, scene rules and caps above still apply.\n"
        )
    return prompt


def _claude_json(system: str, user: str, schema: dict, what: str, key: str) -> dict | None:
    """One structured-output call (same model, effort and retries as the other writers); None when Claude is
    unavailable or the answer is unusable. `key` is the list the answer must hold."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        effort = os.environ.get("WRITER_EFFORT", DEFAULT_WRITER_EFFORT)
        if effort:
            output_config["effort"] = effort
        client = anthropic.Anthropic(timeout=ANTHROPIC_TIMEOUT_SECONDS, max_retries=2)
        response = _stream_with_retries(
            client, anthropic,
            model=WRITER_MODEL,
            max_tokens=64000,
            # The prompt is the same for the first call and the repair call: cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
    except (anthropic.APIError, WriterNetworkError) as exc:
        log.warning("Claude avatar explainer %s failed (%s: %s)", what, type(exc).__name__, getattr(exc, "message", exc))
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude avatar explainer %s stopped with %s", what, response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Claude avatar explainer %s returned unparseable JSON", what)
        return None
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        log.warning("Claude avatar explainer %s returned no %s list", what, key)
        return None
    log.info("Claude avatar explainer %s: %d %s (%d in / %d out tokens)", what, len(data[key]), key,
             response.usage.input_tokens, response.usage.output_tokens)
    return data


def _call_writer(user: str) -> dict | None:
    return _claude_json(_system_prompt(), user, _AVX_SCHEMA, "writer", "videos")


def _source_text(video: dict, candidates: list[Story]) -> str:
    """The text the video's facts may come from: its stories' titles and summaries, plus the linked article
    excerpts once the fact check has fetched them (FACTCHECK_FETCH_ARTICLES)."""
    return "\n".join(factcheck.story_text(s) for s in _video_stories(video, candidates))


def _video_stories(video: Any, candidates: list[Story]) -> list[Story]:
    ids = [i for i in (video.get("story_ids") or [] if isinstance(video, dict) else [])
           if isinstance(i, int) and 0 <= i < len(candidates)]
    return [candidates[i] for i in dict.fromkeys(ids)]


def check_video(video: Any, candidates: list[Story]) -> tuple[list[dict], list[str]]:
    """One video of Claude's answer, converted and validated against its own stories (strict)."""
    if not isinstance(video, dict):
        raise AvxPlanError(["a video must be an object"])
    errors = []
    if not str(video.get("title", "")).strip():
        errors.append("title is required")
    source = _source_text(video, candidates)
    if not source:
        errors.append("story_ids must name at least one of the stories by its [id]")
    raw = video.get("scenes")
    if not isinstance(raw, list) or not raw:
        raise AvxPlanError(errors + ["scenes must be a non-empty list"])
    try:
        scenes, repairs = validate_scenes([scene_from_writer(s) if isinstance(s, dict) else s for s in raw], source, strict=True)
    except AvxPlanError as exc:
        raise AvxPlanError(errors + exc.errors) from None
    if source:  # grounded generation: every spoken line's quotes must really be in the stories
        ev_errors, ev_warnings, _ = factcheck.check_evidence(video.get("evidence"), [sc["say"] for sc in scenes], source)
        if factcheck.evidence_strict("avx"):
            errors += ev_errors
        else:
            repairs += [f"evidence (not enforced): {e}" for e in ev_errors]
        repairs += [f"evidence: {w}" for w in ev_warnings]
    if errors:
        raise AvxPlanError(errors)
    return scenes, repairs


def _candidate(video: dict, scenes: list[dict], candidates: list[Story]) -> factcheck.Candidate:
    """A validated video as the fact check sees it: its stories, every spoken and on-screen string, and the
    writer's evidence (for the audit record)."""
    stories = _video_stories(video, candidates)
    _, warnings, mapping = factcheck.check_evidence(video.get("evidence"), [sc["say"] for sc in scenes],
                                                    _source_text(video, candidates))
    title = str(video.get("title", "")).strip() or "Untitled"
    return factcheck.Candidate(title=title, stories=stories,
                               items=factcheck.items_avx(scenes, title, str(video.get("post_caption", ""))),
                               key=(video, scenes), evidence=mapping, evidence_warnings=warnings)


# --- variety across the day: no two videos with the same shape -------------------------------------

def middle_sequence(scenes: list[dict]) -> tuple[str, ...]:
    """The styles between the hook and the outro, in order: a video's "shape"."""
    return tuple(s.get("style", "") for s in scenes[1:-1])


def sequence_clashes(shapes: list[tuple[str, ...] | None]) -> dict[int, str]:
    """{index: reason} for every video whose middle sequence repeats an EARLIER video's (the first keeps it)."""
    seen: dict[tuple[str, ...], int] = {}
    out = {}
    for i, shape in enumerate(shapes):
        if shape is None:
            continue
        if shape in seen:
            out[i] = (f"its middle scenes ({' -> '.join(shape)}) are the same sequence as video {seen[shape] + 1}'s; "
                      "every video of the day needs its own shape: change the styles, their order or the scene count")
        else:
            seen[shape] = i
    return out


def _validated_videos(user: str, n_videos: int, candidates: list[Story],
                      avoid_shapes: tuple = (), first: dict | None = None) -> list[tuple[dict, list[dict]]] | None:
    """Asks for the videos (or takes `first`, an answer already written), validates every scene list (and that no
    two share a middle sequence), gives Claude one chance to rewrite the rejected ones with the exact errors, and
    returns the accepted (video, scenes) pairs; None when Claude was unavailable."""
    data = first if first is not None else _call_writer(user)
    if data is None:
        return None
    videos = data.get("videos", [])[:n_videos]
    results: list[tuple[dict, list[dict] | None, list[str]]] = []
    for video in videos:
        try:
            scenes, repairs = check_video(video, candidates)
            results.append((video, scenes, repairs))
        except AvxPlanError as exc:
            results.append((video, None, exc.errors))
    for i, why in _clashes(results, avoid_shapes).items():
        results[i] = (results[i][0], None, [why])
    rejected = [(i, v, errs) for i, (v, s, errs) in enumerate(results) if s is None]
    if rejected:
        notes = "\n".join(f"- video {i + 1} ({str(v.get('title', '') if isinstance(v, dict) else '')[:60]!r}):\n  "
                          + "\n  ".join(errs) for i, v, errs in rejected)
        log.info("Avatar explainer: %d of %d scene lists rejected, asking Claude to fix them once:\n%s",
                 len(rejected), len(results), notes)
        # Only the rejected videos are rewritten: returning a whole day of scene lists again would run past the
        # output limit (one avx video is ~5k output tokens).
        keep = [middle_sequence(sc) for _, sc, _ in results if sc] + [tuple(x) for x in avoid_shapes]
        repair = (user + "\n\nThe automatic checks rejected these videos you wrote:\n" + notes
                  + f"\n\nRewrite ONLY these {len(rejected)} videos so they pass, in the order listed. Do not reuse "
                  "these middle scene sequences, which other videos already use: "
                  + ("; ".join(" -> ".join(x) for x in keep) or "none")
                  + ".\n\nThe rejected videos as you wrote them:\n" + json.dumps([v for _, v, _ in rejected]))
        data2 = _call_writer(repair)
        if data2 is not None:
            videos2 = list(data2.get("videos") or [])
            for n, (i, _, _) in enumerate(rejected):
                if n >= len(videos2):
                    continue
                try:
                    scenes, repairs = check_video(videos2[n], candidates)
                    results[i] = (videos2[n], scenes, repairs)
                except AvxPlanError as exc:
                    results[i] = (videos2[n], None, exc.errors)
        for i, why in _clashes(results, avoid_shapes).items():
            results[i] = (results[i][0], None, [why])
    accepted = []
    for video, scenes, notes in results:
        title = str(video.get("title", "") if isinstance(video, dict) else "").strip() or "Untitled"
        if scenes is None:
            factcheck.note_drop("avx", "validator", title, _video_stories(video, candidates), notes)
            continue
        for note in notes:
            log.info("Avatar explainer: %r repair: %s", title, note)
        accepted.append((video, scenes))
    return accepted


def _clashes(results: list, avoid_shapes: tuple) -> dict[int, str]:
    """sequence_clashes over this answer, counting shapes already used today (a refill round) as earlier videos."""
    avoid = [tuple(x) for x in avoid_shapes]
    shapes = avoid + [middle_sequence(s) if s else None for _, s, _ in results]
    return {i - len(avoid): why for i, why in sequence_clashes(shapes).items() if i >= len(avoid)}


# --- the editor: one cheap Claude pass that reads every video like a viewer would ---------------------

CRITIC_KINDS = ("unclear", "unsupported", "weak_hook", "graphic_mismatch", "repetitive", "incoherent")
CRITIC_SYSTEM = """\
You are the strict final editor of a tech-news Shorts channel. Each video is an avatar explainer: a \
narrator speaks one line per scene while a graphic plays. Viewers have NOT read the article, hear each line \
once, and swipe away the moment something does not make sense.

For every video you get the source story (the only facts allowed) and its scenes: each scene's style, the \
spoken line, and what the graphic shows. Flag a video ("fix") when ANY of these is true, naming the scene:
- unclear: a spoken line that is garbled, a fragment, a half-sentence, a paraphrased headline that does \
not read as a normal sentence, or that a first-time viewer would not understand on its own.
- unsupported: a claim, number, name or outcome the story does not support (overstated, invented, or \
twisted in meaning).
- weak_hook: the first line does not plainly state the actual news or claim (who did what, or what is \
surprising) in one clear sentence.
- graphic_mismatch: the graphic does not show what its line says, or a number/chart that does not \
support the point its own line makes (a background number dressed up as the key stat).
- repetitive: a scene that restates an earlier one instead of adding something new, or filler stretched \
from a thin story.
- incoherent: the scenes do not build one story in order (claim, evidence, why it matters, takeaway, \
question).
Be concrete in `detail`: quote the problem and say what a fix would be. Mark each problem's `severity`: \
"blocker" when it would confuse or mislead a first-time viewer (a garbled or fragment line, a hook that \
hides the news, a wrong or unsupported claim, a graphic that contradicts or does not support its line, a \
scene that only repeats another), "minor" for wording or emphasis you would merely polish. Only blockers \
send a video back, so do not inflate minor points. A video with no blocker gets "ok".
"""
_CRITIC_SCHEMA = {
    "type": "object",
    "properties": {"reviews": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "video": {"type": "integer", "description": "the video's number as given"},
            "verdict": {"type": "string", "enum": ["ok", "fix"]},
            "problems": {"type": "array", "items": {
                "type": "object",
                "properties": {"scene": {"type": "integer", "description": "1-based scene number, 0 = whole video"},
                               "kind": {"type": "string", "enum": list(CRITIC_KINDS)},
                               "severity": {"type": "string", "enum": ["blocker", "minor"]},
                               "detail": {"type": "string"}},
                "required": ["scene", "kind", "severity", "detail"], "additionalProperties": False}},
        },
        "required": ["video", "verdict", "problems"], "additionalProperties": False}}},
    "required": ["reviews"], "additionalProperties": False,
}


def _call_critic(user: str) -> dict | None:
    return _claude_json(CRITIC_SYSTEM, user, _CRITIC_SCHEMA, "editor", "reviews")


def _describe(scenes: list[dict]) -> str:
    out = []
    for k, sc in enumerate(scenes, start=1):
        role = "hook" if k == 1 else "closing question" if k == len(scenes) else sc["style"]
        out.append(f"  {k}. [{role}] says: {sc.get('say', '')!r}\n     graphic ({sc['style']}): {_card(sc)!r}")
    return "\n".join(out)


def critique(videos: list[tuple[str, list[dict], str]]) -> dict[int, list[str]] | None:
    """One Claude call for the whole batch: {video index: [reasons]} for every flagged video ({} = all clean),
    None when the editor could not run (the videos then ship as validated, with a warning)."""
    if not videos:
        return {}
    blocks = [f"VIDEO {i + 1}: {title}\nStory:\n{source}\nScenes:\n{_describe(scenes)}"
              for i, (title, scenes, source) in enumerate(videos)]
    data = _call_critic("Review these videos.\n\n" + "\n\n".join(blocks))
    if data is None:
        return None
    flagged: dict[int, list[str]] = {}
    for review in data.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        i = review.get("video")
        if not isinstance(i, int) or not 1 <= i <= len(videos):
            continue
        problems = [p for p in review.get("problems") or [] if isinstance(p, dict)]
        blockers = [p for p in problems if p.get("severity", "blocker") != "minor"]
        if blockers:  # minor notes never send a video back
            flagged[i - 1] = [f"scene {p.get('scene', 0)} ({p.get('kind', '?')}): {p.get('detail', '')}".strip()
                              for p in blockers]
    return flagged


def _critic_pass(user: str, accepted: list[tuple[dict, list[dict]]], candidates: list[Story]) -> list[tuple[dict, list[dict]]]:
    """The editor reads every accepted video; a flagged one gets ONE rewrite with the editor's reasons, is
    validated and reviewed again, and is dropped if it is still flagged (never shipped)."""
    reviews = critique([(str(v.get("title", "")), s, _source_text(v, candidates)) for v, s in accepted])
    if reviews is None:
        log.warning("Avatar explainer: the editor pass could not run; shipping the %d validated videos unreviewed",
                    len(accepted))
        return accepted
    if not reviews:
        log.info("Avatar explainer: the editor passed all %d videos", len(accepted))
        return accepted
    flagged = sorted(reviews)
    notes = "\n".join(f"- video {n + 1} ({str(accepted[i][0].get('title', ''))[:60]!r}):\n  " + "\n  ".join(reviews[i])
                      for n, i in enumerate(flagged))
    log.info("Avatar explainer: the editor flagged %d of %d videos, asking for one rewrite:\n%s", len(flagged),
             len(accepted), notes)
    keep_shapes = [middle_sequence(s) for i, (_, s) in enumerate(accepted) if i not in reviews]
    repair = (user + "\n\nAn editor reviewed some of the videos you wrote and flagged these problems:\n" + notes
              + "\n\nRewrite ONLY these flagged videos so a first-time viewer follows every line, fixing every "
              "problem listed (you may pick a different, richer story from the list if the story was too thin), "
              "and keep every rule of the brief (the hook's on-screen lines are its own spoken words, the caps, "
              "only the story's numbers). "
              f"Return exactly {len(flagged)} videos, in the order listed above. Do not reuse these middle scene "
              "sequences, which other videos already use: " + ("; ".join(" -> ".join(x) for x in keep_shapes) or "none")
              + ".\n\nThe flagged videos as you wrote them:\n" + json.dumps([accepted[i][0] for i in flagged]))
    data = _call_writer(repair)
    fixed: dict[int, tuple[dict, list[dict]]] = {}
    invalid: dict[int, list[str]] = {}
    rewrites = factcheck.align_rewrites([accepted[i][0] for i in flagged], list((data or {}).get("videos") or []))
    for n, i in enumerate(flagged):
        if rewrites[n] is None:
            continue
        try:
            fixed[i] = (rewrites[n], check_video(rewrites[n], candidates)[0])
        except AvxPlanError as exc:
            invalid[i] = exc.errors
    if invalid:  # the rewrite broke a hard rule (a cap, a number): one more try with the validator's errors
        bad = [i for i in flagged if i in invalid]
        notes2 = "\n".join(f"- video {n + 1}:\n  " + "\n  ".join(invalid[i]) for n, i in enumerate(bad))
        log.info("Avatar explainer: %d rewrites failed validation, asking once more:\n%s", len(bad), notes2)
        again = _call_writer(repair + "\n\nYour rewrites of these videos failed the automatic checks:\n" + notes2
                             + f"\n\nReturn exactly {len(bad)} videos, these ones only, in this order, fixed.\n\n"
                             "Your rewrites:\n" + json.dumps([rewrites[flagged.index(i)] for i in bad]))
        for n, i in enumerate(bad):
            video = ((again or {}).get("videos") or [None] * len(bad))[n] if again and n < len(again.get("videos") or []) else None
            if video is None:
                continue
            try:
                fixed[i] = (video, check_video(video, candidates)[0])
                invalid.pop(i)
            except AvxPlanError as exc:
                invalid[i] = exc.errors
    for i, errs in invalid.items():
        log.error("Avatar explainer: the rewrite of %r failed validation:\n  %s",
                  str(accepted[i][0].get("title", "")), "\n  ".join(errs))
    order = list(fixed)  # the second review numbers the rewritten videos in this order
    second = critique([(str(v.get("title", "")), s, _source_text(v, candidates)) for v, s in fixed.values()]) if fixed else {}
    if second is None:  # the editor could not run again: a rewrite nobody re-read is not shipped
        second = {k: ["the editor could not review the rewrite"] for k in range(len(order))}
    still = {order[k]: reasons for k, reasons in second.items()}
    out = []
    for i, pair in enumerate(accepted):
        title = str(pair[0].get("title", ""))
        if i not in reviews:
            out.append(pair)
        elif i in fixed and i not in still:
            log.info("Avatar explainer: %r rewritten after the editor's notes and passed", title)
            out.append(fixed[i])
        else:
            why = still.get(i) or reviews[i] or invalid.get(i) or []
            factcheck.note_drop("avx", "editor", title, _video_stories(pair[0], candidates),
                                ["still flagged by the editor:"] + list(why))
    clashes = sequence_clashes([middle_sequence(s) for _, s in out])
    for k in sorted(clashes, reverse=True):
        factcheck.note_drop("avx", "editor", str(out[k][0].get("title", "")), _video_stories(out[k][0], candidates),
                            [f"after the rewrite: {clashes[k]}"])
        out.pop(k)
    return out


def _fact_check_pass(user: str, accepted: list[tuple[dict, list[dict]]], candidates: list[Story],
                     avoid_shapes: tuple = ()) -> list[tuple[dict, list[dict], dict]]:
    """The independent verifier (factcheck.gate) over the day's videos: a video with an unsupported or
    contradicted claim gets one repair with the verifier's reasons and quotes, is validated and verified again,
    and is dropped if it still fails. Returns (video, scenes, fact_check record)."""

    def rewrite(pairs):
        keep = [middle_sequence(c.key[1]) for c in cands if all(c is not p for p, _ in pairs)] + list(avoid_shapes)
        note = (f"Return exactly {len(pairs)} videos, in the order listed, each with the same story_ids. Do not "
                "reuse these middle scene sequences, which other videos already use: "
                + ("; ".join(" -> ".join(x) for x in keep) or "none") + ".")
        prompt = (user + "\n\n" + factcheck.repair_prompt(note, pairs)
                  + "\n\nThe videos as you wrote them:\n" + json.dumps([c.key[0] for c, _ in pairs]))
        data = _call_writer(prompt)

        def check(v):
            try:
                return _candidate(v, check_video(v, candidates)[0], candidates), []
            except AvxPlanError as exc:
                return None, exc.errors

        return factcheck.checked_rewrites([c.key[0] for c, _ in pairs], list((data or {}).get("videos") or []), check,
                                          lambda tail: list((_call_writer(prompt + tail) or {}).get("videos") or []))

    cands = [_candidate(v, s, candidates) for v, s in accepted]
    result = factcheck.gate("avx", cands, rewrite)
    out = [(c.key[0], c.key[1], rec) for c, rec in result.accepted]
    clashes = _clashes([(v, s, None) for v, s, _ in out], avoid_shapes)
    for k in sorted(clashes, reverse=True):
        factcheck.note_drop("avx", "fact check", str(out[k][0].get("title", "")), _video_stories(out[k][0], candidates),
                            [f"after the repair: {clashes[k]}"])
        out.pop(k)
    return out


# --- which stories the writer sees ----------------------------------------------------------------------

AVX_SUMMARY_CHARS = 900  # the writer sees the whole summary: trimmed to 400, "7 Ways to Handle Your AI Gross
# Margin Problem" lost the half that made it a story, and the video was stretched from what was left
MIN_SUMMARY_WORDS = 40  # below this a summary rarely holds 5-8 distinct beats (claim, evidence, why, catch)
THIN_FLOOR_WORDS = 20  # a top-up story is never thinner than this
POOL_PER_VIDEO = 2  # stories offered per requested video, so the writer can still choose


def _words(text: str) -> int:
    return len((text or "").split())


def select_stories(candidates: list[Story], n_videos: int) -> list[Story]:
    """The stories rich enough for an avatar explainer, in their original order: every story whose summary has
    at least MIN_SUMMARY_WORDS words. When that leaves fewer than POOL_PER_VIDEO per requested video, the
    fullest of the thinner ones (down to THIN_FLOOR_WORDS) are added back so the day still gets its count."""
    rich = [s for s in candidates if _words(s.summary) >= MIN_SUMMARY_WORDS]
    want = POOL_PER_VIDEO * n_videos
    chosen = {id(s) for s in rich}
    if len(rich) < want:
        thin = sorted((s for s in candidates if id(s) not in chosen and _words(s.summary) >= THIN_FLOOR_WORDS),
                      key=lambda s: -_words(s.summary))
        for s in thin[: want - len(rich)]:
            chosen.add(id(s))
        log.info("Avatar explainer: only %d stories have %d+ summary words; topped up with %d thinner ones",
                 len(rich), MIN_SUMMARY_WORDS, len(chosen) - len(rich))
    out = [s for s in candidates if id(s) in chosen]
    skipped = [s for s in candidates if id(s) not in chosen]
    if skipped:
        log.info("Avatar explainer: skipped %d stories too thin for 5-8 scenes (under %d summary words): %s",
                 len(skipped), MIN_SUMMARY_WORDS, "; ".join(f"{s.title[:50]!r} ({_words(s.summary)})" for s in skipped[:12]))
    return out or list(candidates)


AVX_VIDEOS_PER_CALL = 4  # one avx video is ~5k output tokens: ten in one call ran into the 64k output limit


def _story_lines(candidates: list[Story], ids: list[int]) -> str:
    return "\n\n".join(f"[{i}] ({candidates[i].edition} / {candidates[i].section}) {candidates[i].title}{_site(candidates[i])}"
                       f"\n{_trim(candidates[i].summary, AVX_SUMMARY_CHARS)}" for i in ids)


def _first_drafts(candidates: list[Story], n_videos: int, avoid: str) -> dict | None:
    """A day's first drafts written in parallel calls of at most AVX_VIDEOS_PER_CALL videos, each from its own
    share of the stories (dealt round-robin, so every share gets strong ones; ids stay global). None only when
    every call failed; a failed call just means fewer drafts (the refill rounds make up the count)."""
    from concurrent.futures import ThreadPoolExecutor

    k = -(-n_videos // AVX_VIDEOS_PER_CALL)
    counts = [n_videos // k + (1 if g < n_videos % k else 0) for g in range(k)]
    shares = [[i for i in range(len(candidates)) if i % k == g] for g in range(k)]

    def write(g: int) -> dict | None:
        return _call_writer(
            f"Write exactly {counts[g]} videos from the stories below, each as an avatar explainer scene list. Order "
            f"them with the strongest video first. Reference stories by their [id]. Give each video its own shape: no "
            f"two videos may use the same sequence of middle scene styles, and vary the scene count (other writers "
            f"cover today's other stories in parallel, so prefer less common shapes).{avoid}\n\n"
            + _story_lines(candidates, shares[g]))

    with ThreadPoolExecutor(max_workers=min(3, k)) as pool:
        answers = list(pool.map(write, range(k)))
    if all(a is None for a in answers):
        return None
    videos = [v for a, n in zip(answers, counts) for v in list((a or {}).get("videos") or [])[:n]]
    log.info("Avatar explainer: %d first drafts from %d parallel writer calls (%d failed)", len(videos), k,
             sum(a is None for a in answers))
    return {"videos": videos}


def llm_avx_scripts(candidates: list[Story], n_videos: int, avoid_shapes: tuple = ()) -> list[Script] | None:
    """The avatar explainer counterpart of script_writer._llm_scripts: one call for the day's videos, the
    validator (with one repair), then the editor (with one rewrite). Videos that fail either are skipped with
    a logged reason (an empty list means every one was rejected: no video rather than a broken one); None
    means Claude could not be used at all."""
    candidates = select_stories(candidates, n_videos)
    stories = "\n\n".join(
        f"[{i}] ({s.edition} / {s.section}) {s.title}{_site(s)}\n{_trim(s.summary, AVX_SUMMARY_CHARS)}"
        for i, s in enumerate(candidates)
    )
    avoid = (" These middle scene sequences are already used by today's other videos, do not reuse them: "
             + "; ".join(" -> ".join(x) for x in avoid_shapes) + ".") if avoid_shapes else ""
    user = (f"Write exactly {n_videos} videos from today's stories below, each as an avatar explainer scene list. "
            f"Order them with the strongest video first. Reference stories by their [id]. Give each video its own "
            f"shape: no two videos may use the same sequence of middle scene styles, and vary the scene count.{avoid}"
            f"\n\n{stories}")
    first = _first_drafts(candidates, n_videos, avoid) if n_videos > AVX_VIDEOS_PER_CALL else None
    if n_videos > AVX_VIDEOS_PER_CALL and first is None:
        return None
    accepted = _validated_videos(user, n_videos, candidates, avoid_shapes, first=first)
    if accepted is None:
        return None
    if accepted and os.environ.get("AVX_EDITOR", "1").strip().lower() not in ("0", "off", "false", "no"):
        accepted = _critic_pass(user, accepted, candidates)
    checked = _fact_check_pass(user, accepted, candidates, avoid_shapes) if accepted else []
    scripts = []
    for video, scenes, rec in checked:
        script = script_from_scenes(scenes, str(video.get("title", "")).strip() or "Untitled",
                                    str(video.get("badge") or "NEWS"), _video_stories(video, candidates),
                                    str(video.get("post_caption", "")), list(video.get("hashtags") or []))
        script.fact_check = rec
        scripts.append(script)
    if len(scripts) < n_videos:
        log.warning("Avatar explainer: %d of %d videos dropped (invalid scene lists or editor flags)",
                    n_videos - len(scripts), n_videos)
    return scripts


__all__ = ["AvxPlanError", "critique", "select_stories", "sequence_clashes", "validate_scenes", "scene_from_writer", "script_from_scenes", "structural_scenes",
           "heuristic_avx_scripts", "llm_avx_scripts", "scenes_for", "plan_dict", "is_avx_plan", "check_video"]


def _site(story) -> str:
    """" [link: nytimes.com]": who published the linked article, so an allegation can be attributed to it."""
    from .factcheck import publisher

    site = publisher(getattr(story, "url", ""))
    return f" [link: {site}]" if site else ""
