"""Scene plans: the typed script format behind the "explainer" video style
(VIDEO_STYLE=explainer, see README "Video styles").

A scene plan is an ordered list of scenes. Each scene names one layout from
the fixed scene library in hf_template_explainer/ (title, statement, stat,
comparison, list, timeline, quote, chart, outro), carries the typed on-screen
fields that layout needs, and the line the voice speaks while it is up.

Every text field has a hard character cap (CAPS below). The composition is
designed around those caps, so validated text always fits its box: nothing
is ever truncated or clipped at render time. `validate_plan` is strict — a
plan that breaks a cap, references an emphasis phrase that isn't in its
text, or has the wrong shape is rejected with a list of precise errors, and
the pipeline skips that video rather than rendering a broken one. The few
repairs it does make (whitespace, dropping an unusable emphasis, clamping a
chart highlight) never change what the viewer reads.

`plan_from_script` derives a structural plan from a classic hook/beats/outro
Script, so rule-based scripts and the saved sidecars of earlier days can be
rendered in the explainer style too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCENE_TYPES = ("title", "statement", "stat", "comparison", "list", "timeline", "quote", "chart",
               "diagram", "device", "outro")
# Scenes whose hero is a graphic (a number, chart, diagram or wireframe) vs
# scenes the viewer mostly reads. The strict rules below keep a video from
# turning into a slideshow of text.
VISUAL_TYPES = frozenset({"stat", "comparison", "chart", "timeline", "diagram", "device"})
TEXT_TYPES = frozenset({"statement", "quote", "list"})
CHART_KINDS = ("bars", "columns", "line", "dots")
DIAGRAM_KINDS = ("flow", "stack", "hub", "funnel")
DEVICE_KINDS = ("phone", "laptop", "terminal", "chat")
# The pictogram set in hf_template_explainer/icons.js (one stroke style).
ICONS = frozenset({
    "chip", "cloud", "code", "rocket", "lock", "dollar", "trend-up", "trend-down", "users", "user",
    "robot", "brain", "phone", "laptop", "globe", "bolt", "clock", "warning", "check", "cross", "box",
    "building", "briefcase", "gavel", "eye", "search", "mail", "play", "shield", "bug", "scale", "car",
    "star", "database", "key", "gear", "camera", "mic", "video", "document", "chart", "layers", "link",
    "wallet", "megaphone",
})

# Hard character caps per on-screen field. These are the numbers the
# composition's type scale was designed against (see DESIGN.md): the largest
# allowed string still fits its box at the smallest permitted size.
CAPS: dict[str, dict[str, int]] = {
    "title": {"kicker": 18, "headline": 56},
    "statement": {"label": 24, "text": 72, "emphasis": 28},
    "stat": {"label": 36, "value": 9, "context": 64, "source": 32},
    "comparison": {"title": 40, "label": 16, "value": 12, "note": 36},
    "list": {"title": 40, "item": 38},
    "timeline": {"title": 40, "step_label": 12, "step_text": 40},
    "quote": {"text": 110, "attribution": 40},
    "chart": {"title": 40, "series_label": 14, "unit": 8},
    "diagram": {"title": 40, "node_label": 16, "node_text": 30, "center": 16},
    "device": {"title": 40, "label": 18, "line": 36},
    "outro": {"text": 56},
}
LIST_ITEMS = (2, 4)
TIMELINE_STEPS = (2, 4)
CHART_SERIES = (2, 5)
TREND_POINTS = (2, 8)
DIAGRAM_NODES = {"flow": (2, 4), "stack": (2, 4), "hub": (3, 5), "funnel": (3, 4)}
DEVICE_LINES = (2, 4)
SAY_WORDS = (4, 40)  # per scene
PLAN_WORDS = (60, 120)  # whole video; the writer aims for 100-110 (40-45 s), pace absorbs the rest
STRICT_MIN_WORDS = 95  # strict (writer) plans: the pacing logic can't stretch a short script to 40 s
STRICT_MIN_VISUAL = 2  # strict plans: at least this many graphic scenes
MIDDLE_SCENES = (3, 7)  # between the title and the outro
MAX_SAME_TYPE_RUN = 2  # no three identical layouts in a row (statements excepted: they are the plain card)


class ScenePlanError(ValueError):
    """A plan that must not be rendered. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class Scene:
    type: str
    say: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "say": self.say, **self.fields}


@dataclass
class ScenePlan:
    scenes: list[Scene]
    repairs: list[str] = field(default_factory=list)  # what validate_plan quietly fixed

    @property
    def lines(self) -> list[str]:
        return [s.say for s in self.scenes]

    @property
    def word_count(self) -> int:
        return sum(len(s.say.split()) for s in self.scenes)

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.scenes]


# --- validation ---------------------------------------------------------------

def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


class _Check:
    def __init__(self, index: int, kind: str):
        self.where = f"scene {index + 1} ({kind})"
        self.errors: list[str] = []
        self.repairs: list[str] = []

    def text(self, raw: dict, key: str, cap: int, required: bool = True, label: str | None = None) -> str:
        value = _clean(raw.get(key))
        name = label or key
        if not value:
            if required:
                self.errors.append(f"{self.where}: '{name}' is missing")
            return ""
        if len(value) > cap:
            self.errors.append(f"{self.where}: '{name}' is {len(value)} chars, cap is {cap}: {value!r}")
        return value

    def items(self, raw: dict, key: str, bounds: tuple[int, int]) -> list:
        value = raw.get(key)
        if not isinstance(value, list):
            self.errors.append(f"{self.where}: '{key}' must be a list")
            return []
        if not bounds[0] <= len(value) <= bounds[1]:
            self.errors.append(f"{self.where}: '{key}' has {len(value)} entries, allowed {bounds[0]}-{bounds[1]}")
        return value

    def choice(self, raw: dict, key: str, options: tuple, default: str) -> str:
        value = _clean(raw.get(key)).lower()
        if not value:
            return default
        if value not in options:
            self.errors.append(f"{self.where}: '{key}' must be one of {', '.join(options)}, not {value!r}")
            return default
        return value

    def icon(self, raw: dict, key: str = "icon") -> str:
        """An unknown pictogram name is dropped, not fatal: the scene still
        works without it."""
        value = _clean(raw.get(key)).lower()
        if value and value not in ICONS:
            self.repairs.append(f"{self.where}: unknown icon {value!r} dropped")
            return ""
        return value


def _validate_scene(index: int, raw: Any) -> tuple[Scene | None, list[str], list[str]]:
    if not isinstance(raw, dict):
        return None, [f"scene {index + 1}: not an object"], []
    kind = _clean(raw.get("type")).lower()
    if kind not in SCENE_TYPES:
        return None, [f"scene {index + 1}: unknown type {kind!r} (allowed: {', '.join(SCENE_TYPES)})"], []
    c = _Check(index, kind)
    caps = CAPS[kind]
    say = _clean(raw.get("say"))
    words = len(say.split())
    if not SAY_WORDS[0] <= words <= SAY_WORDS[1]:
        c.errors.append(f"{c.where}: 'say' has {words} words, allowed {SAY_WORDS[0]}-{SAY_WORDS[1]}")
    f: dict[str, Any] = {}

    if kind == "title":
        f["kicker"] = c.text(raw, "kicker", caps["kicker"])
        f["headline"] = c.text(raw, "headline", caps["headline"])
        f["icon"] = c.icon(raw)
    elif kind == "statement":
        f["label"] = c.text(raw, "label", caps["label"], required=False)
        f["text"] = c.text(raw, "text", caps["text"])
        emphasis = c.text(raw, "emphasis", caps["emphasis"], required=False)
        if emphasis and emphasis not in f["text"]:
            c.repairs.append(f"{c.where}: emphasis {emphasis!r} is not in the text; dropped")
            emphasis = ""
        f["emphasis"] = emphasis
        f["icon"] = c.icon(raw)
    elif kind == "stat":
        f["value"] = c.text(raw, "value", caps["value"])
        f["label"] = c.text(raw, "label", caps["label"])
        f["context"] = c.text(raw, "context", caps["context"], required=False)
        f["source"] = c.text(raw, "source", caps["source"], required=False)
        trend = raw.get("trend")
        points = []
        if isinstance(trend, list) and trend:
            points = [_number(v) for v in trend]
            if any(p is None or p < 0 for p in points):
                c.errors.append(f"{c.where}: 'trend' must be non-negative numbers")
                points = []
            elif not TREND_POINTS[0] <= len(points) <= TREND_POINTS[1]:
                c.errors.append(f"{c.where}: 'trend' has {len(points)} points, allowed {TREND_POINTS[0]}-{TREND_POINTS[1]}")
                points = []
        f["trend"] = points
    elif kind == "comparison":
        f["title"] = c.text(raw, "title", caps["title"], required=False)
        for side in ("left", "right"):
            raw_side = raw.get(side)
            if not isinstance(raw_side, dict):
                c.errors.append(f"{c.where}: '{side}' must be an object")
                continue
            f[side] = {
                "label": c.text(raw_side, "label", caps["label"], label=f"{side}.label"),
                "value": c.text(raw_side, "value", caps["value"], label=f"{side}.value"),
                "note": c.text(raw_side, "note", caps["note"], required=False, label=f"{side}.note"),
                "number": _number(raw_side.get("number")),
                "icon": c.icon(raw_side),
            }
        if "left" in f and "right" in f:
            nums = [f["left"]["number"], f["right"]["number"]]
            # Bars need two comparable positive quantities; anything else (one
            # side missing, 0 on both sides) means "no bars", not an error.
            if not (all(n is not None and n > 0 for n in nums)):
                if any(n is not None and n > 0 for n in nums):
                    c.repairs.append(f"{c.where}: only one side has a positive number; bars dropped")
                f["left"]["number"] = f["right"]["number"] = None
    elif kind == "list":
        f["title"] = c.text(raw, "title", caps["title"], required=False)
        items = [_clean(i) for i in c.items(raw, "items", LIST_ITEMS)]
        for i, item in enumerate(items):
            if not item:
                c.errors.append(f"{c.where}: item {i + 1} is empty")
            elif len(item) > caps["item"]:
                c.errors.append(f"{c.where}: item {i + 1} is {len(item)} chars, cap is {caps['item']}: {item!r}")
        f["items"] = items
        icons = raw.get("icons")
        icons = [c.icon({"icon": v}) for v in icons] if isinstance(icons, list) else []
        if icons and len(icons) != len(items):
            c.repairs.append(f"{c.where}: {len(icons)} icons for {len(items)} items; icons dropped")
            icons = []
        f["icons"] = icons if any(icons) else []
    elif kind == "timeline":
        f["title"] = c.text(raw, "title", caps["title"], required=False)
        steps = []
        for i, step in enumerate(c.items(raw, "steps", TIMELINE_STEPS)):
            if not isinstance(step, dict):
                c.errors.append(f"{c.where}: step {i + 1} must be an object")
                continue
            steps.append({
                "label": c.text(step, "label", caps["step_label"], label=f"step {i + 1}.label"),
                "text": c.text(step, "text", caps["step_text"], label=f"step {i + 1}.text"),
            })
        f["steps"] = steps
    elif kind == "quote":
        f["text"] = c.text(raw, "text", caps["text"])
        f["attribution"] = c.text(raw, "attribution", caps["attribution"])
    elif kind == "chart":
        f["title"] = c.text(raw, "title", caps["title"])
        f["unit"] = c.text(raw, "unit", caps["unit"], required=False)
        f["kind"] = c.choice(raw, "kind", CHART_KINDS, "bars")
        series = []
        for i, point in enumerate(c.items(raw, "series", CHART_SERIES)):
            if not isinstance(point, dict):
                c.errors.append(f"{c.where}: series {i + 1} must be an object")
                continue
            number = _number(point.get("value"))
            if number is None or number < 0:
                c.errors.append(f"{c.where}: series {i + 1} needs a non-negative numeric value")
                number = 0.0
            series.append({"label": c.text(point, "label", caps["series_label"], label=f"series {i + 1}.label"),
                           "value": number})
        f["series"] = series
        if f["kind"] == "dots" and sum(p["value"] for p in series) > 100.5:
            c.errors.append(f"{c.where}: a dots chart shows shares of 100; the values sum to "
                            f"{sum(p['value'] for p in series):g}")
        highlight = raw.get("highlight")
        if isinstance(highlight, bool) or not isinstance(highlight, int) or not 0 <= highlight < len(series):
            if highlight not in (None, -1):
                c.repairs.append(f"{c.where}: highlight {highlight!r} out of range; using the largest value")
            highlight = max(range(len(series)), key=lambda i: series[i]["value"]) if series else 0
        f["highlight"] = highlight
    elif kind == "diagram":
        f["title"] = c.text(raw, "title", caps["title"], required=False)
        f["kind"] = c.choice(raw, "kind", DIAGRAM_KINDS, "flow")
        f["center"] = c.text(raw, "center", caps["center"], required=f["kind"] == "hub")
        nodes = []
        for i, node in enumerate(c.items(raw, "nodes", DIAGRAM_NODES[f["kind"]])):
            if not isinstance(node, dict):
                c.errors.append(f"{c.where}: node {i + 1} must be an object")
                continue
            nodes.append({
                "label": c.text(node, "label", caps["node_label"], label=f"node {i + 1}.label"),
                "text": c.text(node, "text", caps["node_text"], required=False, label=f"node {i + 1}.text"),
            })
        f["nodes"] = nodes
    elif kind == "device":
        f["title"] = c.text(raw, "title", caps["title"], required=False)
        f["kind"] = c.choice(raw, "kind", DEVICE_KINDS, "phone")
        f["label"] = c.text(raw, "label", caps["label"], required=False)
        lines = [_clean(i) for i in c.items(raw, "lines", DEVICE_LINES)]
        for i, line in enumerate(lines):
            if not line:
                c.errors.append(f"{c.where}: line {i + 1} is empty")
            elif len(line) > caps["line"]:
                c.errors.append(f"{c.where}: line {i + 1} is {len(line)} chars, cap is {caps['line']}: {line!r}")
        f["lines"] = lines
    elif kind == "outro":
        f["text"] = c.text(raw, "text", caps["text"])

    return Scene(type=kind, say=say, fields=f), c.errors, c.repairs


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_plan(raw_scenes: Any, strict: bool = False) -> ScenePlan:
    """Returns a normalized ScenePlan or raises ScenePlanError listing every
    problem (so the writer's output can be fixed in one pass).

    `strict` adds the engagement rules the writer is held to: at least
    STRICT_MIN_WORDS spoken words (so the video reaches ~40 s), at least
    STRICT_MIN_VISUAL graphic scenes, and never two text-only scenes in a
    row. Structural plans derived from classic scripts are not held to
    them (they are the loudly-logged fallback)."""
    errors: list[str] = []
    repairs: list[str] = []
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ScenePlanError(["plan must be a non-empty list of scenes"])
    scenes: list[Scene] = []
    for i, raw in enumerate(raw_scenes):
        scene, errs, reps = _validate_scene(i, raw)
        errors += errs
        repairs += reps
        if scene is not None:
            scenes.append(scene)
    if scenes:
        if scenes[0].type != "title":
            errors.append("the first scene must be a 'title'")
        if scenes[-1].type != "outro":
            errors.append("the last scene must be an 'outro'")
        for i, scene in enumerate(scenes[1:-1], start=2):
            if scene.type in ("title", "outro"):
                errors.append(f"scene {i} ({scene.type}): only the first scene may be a title and only the last an outro")
        middle = len(scenes) - 2
        if not MIDDLE_SCENES[0] <= middle <= MIDDLE_SCENES[1]:
            errors.append(f"{middle} scenes between title and outro, allowed {MIDDLE_SCENES[0]}-{MIDDLE_SCENES[1]}")
        run = 1
        for prev, cur in zip(scenes, scenes[1:]):
            run = run + 1 if cur.type == prev.type else 1
            if run > MAX_SAME_TYPE_RUN and cur.type != "statement":
                errors.append(f"more than {MAX_SAME_TYPE_RUN} '{cur.type}' scenes in a row")
                break
        total = sum(len(s.say.split()) for s in scenes)
        low = STRICT_MIN_WORDS if strict else PLAN_WORDS[0]
        if not low <= total <= PLAN_WORDS[1]:
            errors.append(f"spoken text is {total} words, allowed {low}-{PLAN_WORDS[1]}")
        if strict:
            visual = sum(s.type in VISUAL_TYPES for s in scenes)
            if visual < STRICT_MIN_VISUAL:
                errors.append(f"only {visual} graphic scene(s) (stat, chart, comparison, timeline, diagram, device); "
                              f"at least {STRICT_MIN_VISUAL} required")
            for i, (prev, cur) in enumerate(zip(scenes, scenes[1:]), start=2):
                if prev.type in TEXT_TYPES and cur.type in TEXT_TYPES:
                    errors.append(f"scenes {i - 1} and {i} are both text-only ({prev.type}, {cur.type}); "
                                  "put a graphic scene between them")
    if errors:
        raise ScenePlanError(errors)
    return ScenePlan(scenes=scenes, repairs=repairs)


# --- deriving a plan from a classic script ------------------------------------

_STAT_VALUE_RE = re.compile(r"^[$€£]?\d[\d,.]*\s?(?:[KMBT%x×]|[kmbt]|bn|tn|pts?)?$")


def _kicker_from_badge(badge: str) -> str:
    return (badge or "NEWS").strip().upper()[: CAPS["title"]["kicker"]]


def _shorten(text: str, cap: int) -> str:
    """Trims at a word boundary, never mid-word; adds no ellipsis (the
    on-screen text is a headline, not a quote)."""
    text = _clean(text).rstrip(".")
    if len(text) <= cap:
        return text
    cut = text[:cap].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return cut if len(cut) >= cap // 2 else text[:cap].rstrip()


def plan_from_script(script: Any) -> ScenePlan:
    """A structural plan from a hook/beats/outro Script (script_writer.Script
    or the `script` dict of a saved sidecar): the hook becomes the title
    scene, each beat a statement (or a stat scene when the beat carries a
    number), and the outro the closing scene. Validated like any plan."""
    get = (lambda o, k, d="": o.get(k, d)) if isinstance(script, dict) else (lambda o, k, d="": getattr(o, k, d))
    beats = get(script, "beats", [])
    raw: list[dict[str, Any]] = [{
        "type": "title",
        "say": get(script, "hook"),
        "kicker": _kicker_from_badge(get(script, "badge")),
        "headline": _shorten(get(script, "title"), CAPS["title"]["headline"]),
    }]
    for beat in beats:
        b = (lambda k, d="": beat.get(k, d)) if isinstance(beat, dict) else (lambda k, d="": getattr(beat, k, d))
        card, highlight, stat = _clean(b("card")), _clean(b("highlight") or ""), _clean(b("stat") or "")
        if b("kind") == "stat" and stat and _STAT_VALUE_RE.match(stat) and len(stat) <= CAPS["stat"]["value"]:
            raw.append({"type": "stat", "say": b("say"), "value": stat,
                        "label": _shorten(card, CAPS["stat"]["label"])})
        else:
            text = _shorten(card, CAPS["statement"]["text"])
            raw.append({"type": "statement", "say": b("say"), "text": text,
                        "emphasis": highlight if highlight and highlight in text else ""})
    raw.append({"type": "outro", "say": get(script, "outro"),
                "text": _shorten(get(script, "outro"), CAPS["outro"]["text"])})
    return validate_plan(raw)


# --- JSON schema for the writer's structured output ---------------------------

def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


def _s(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


# One flat object for every scene type (a per-type anyOf compiles to a
# grammar the API rejects as too large). Fields a type doesn't use are
# sent empty; validate_plan reads only the fields each type needs.
_SIDE = _obj({
    "label": _s("under 16 characters"),
    "value": _s("the figure shown, under 12 characters"),
    "note": _s("under 36 characters, or ''"),
    "number": {"type": "number", "description": "size for a proportional disc; 0 on both sides when not comparable"},
    "icon": _s("pictogram name when there are no numbers (see the icon list), else ''"),
})
SCENE_SCHEMA: dict[str, Any] = _obj({
    "type": {"type": "string", "enum": list(SCENE_TYPES)},
    "say": _s("What the voice says while this scene is on screen (title: the hook, outro: the sign-off, each at most 14 words)"),
    "kicker": _s("title only: topic label, 1-2 words, under 18 characters, e.g. 'AI AGENTS'; else ''"),
    "headline": _s("title only: the story in one line, under 56 characters; else ''"),
    "icon": _s("title and statement: a pictogram name from the icon list that stands for the subject; else ''"),
    "label": _s("statement: optional small label under 24 characters; stat: what the number is, under 36 characters, not repeating it; device: app or window name under 18 characters; else ''"),
    "text": _s("statement: one sentence under 72 characters; quote: the quotation under 110 characters, no quote marks; outro: the closing line under 56 characters; else ''"),
    "emphasis": _s("statement only: 1-3 words copied exactly from text; else ''"),
    "value": _s("stat only: the number written short, under 9 characters: '5.1x', '$7B', '22%'; else ''"),
    "context": _s("stat only: optional one-line context under 64 characters; else ''"),
    "source": _s("stat only: optional source name under 32 characters; else ''"),
    "trend": {"type": "array", "items": {"type": "number"},
              "description": "stat only: 2-8 numbers from the story drawn as a mini column chart under the number, oldest first; else []"},
    "attribution": _s("quote only: who said it, under 40 characters; else ''"),
    "title": _s("comparison, list, timeline, diagram, device (optional) and chart (required): under 40 characters; else ''"),
    "kind": _s("chart: bars | columns | line | dots; diagram: flow | stack | hub | funnel; device: phone | laptop | terminal | chat; else ''"),
    "items": {"type": "array", "items": _s("under 38 characters"), "description": "list only: 2-4 items; else []"},
    "icons": {"type": "array", "items": _s("pictogram name"), "description": "list only: one icon per item, or []"},
    "steps": {"type": "array", "description": "timeline only: 2-4 steps in order; else []",
              "items": _obj({"label": _s("when: under 12 characters, e.g. '2024', 'Now', 'Next'"),
                             "text": _s("what happened, under 40 characters")})},
    "series": {"type": "array", "description": "chart only: 2-5 points; else []",
               "items": _obj({"label": _s("under 14 characters"), "value": {"type": "number"}})},
    "unit": _s("chart only: unit after each value, under 8 characters, e.g. '%', '$B'; else ''"),
    "highlight": {"type": "integer", "description": "chart only: index of the point that matters; else 0"},
    "nodes": {"type": "array", "description": "diagram only: flow/stack 2-4, hub 3-5, funnel 3-4 nodes; else []",
              "items": _obj({"label": _s("under 16 characters"), "text": _s("under 30 characters, or ''")})},
    "center": _s("diagram hub only: the thing in the middle, under 16 characters; else ''"),
    "lines": {"type": "array", "items": _s("under 36 characters"),
              "description": "device only: 2-4 lines shown on the screen (terminal: commands and output; chat: alternating messages; phone: notifications; laptop: rows); else []"},
    "left": _SIDE,
    "right": _SIDE,
})
