"""Recipes: reusable, parameterised style presets the explainer composition
can render, each with the metadata that makes it findable and plannable
(what it demonstrates, when to use it, which scene types it suits, which
HyperFrames registry items and GSAP techniques it relates to, and how
faithfully it reproduced its reference, when it came from one).

There are two sources: the built-in catalogue below (hand-written presets
of the template's style knobs) and recipes derived from references in the
style library (`recipes_from_analysis`), which the planner weighs by their
measured fidelity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import vocabulary as V


@dataclass
class Recipe:
    id: str
    name: str
    description: str
    knobs: dict[str, Any]  # style knob values (see style_plan.DEFAULT_STYLE)
    demonstrates: list[str] = field(default_factory=list)  # vocabulary tags
    when_to_use: str = ""
    scene_types: list[str] = field(default_factory=lambda: ["*"])
    registry_refs: list[str] = field(default_factory=list)  # HyperFrames registry item names
    gsap: str = ""
    source: str = "builtin"  # "builtin" or the library reference name
    fidelity: float | None = None  # measured recreation score (0-1) for library recipes
    params: dict[str, Any] = field(default_factory=dict)  # per-knob allowed values / ranges for the planner

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        return cls(**{k: d.get(k, v) for k, v in asdict(cls("x", "x", "", {})).items()} | {"id": d["id"], "name": d.get("name", d["id"]),
                     "description": d.get("description", ""), "knobs": dict(d.get("knobs", {}))})

    def applies_to(self, scene_type: str) -> bool:
        return "*" in self.scene_types or scene_type in self.scene_types

    def search_text(self) -> str:
        return " ".join([self.id, self.name, self.description, self.when_to_use, " ".join(self.demonstrates),
                         " ".join(self.scene_types), " ".join(f"{k}:{v}" for k, v in self.knobs.items()), self.source]).lower()


KNOB_PARAMS = {
    "transition": list(V.TRANSITIONS), "entrance": list(V.ENTRANCES), "hold": list(V.HOLDS),
    "type_treatment": list(V.TYPE_TREATMENTS), "background": list(V.BACKGROUNDS), "ease": list(V.EASES),
    "speed": "0.5-2.0", "stagger": "0.02-0.4 s", "accent": "#rrggbb",
}


def _r(id: str, name: str, description: str, knobs: dict, demonstrates: list[str], when: str, types: list[str],
       refs: list[str], gsap: str) -> Recipe:
    return Recipe(id=id, name=name, description=description, knobs=knobs, demonstrates=demonstrates, when_to_use=when,
                  scene_types=types, registry_refs=refs, gsap=gsap, params=KNOB_PARAMS)


BUILTIN_RECIPES: list[Recipe] = [
    _r("editorial-wipe", "Editorial wipe",
       "The template's own look pushed to its wipe side: a top-down wipe behind an accent edge, words waving in.",
       {"transition": "wipe-down", "entrance": "word-wave", "ease": "out"},
       ["kinetic-type", "editorial", "mask-reveal"],
       "headlines and statements when the video should feel considered and paper-like",
       ["title", "statement", "quote", "outro"], ["staggered-fade-up", "per-word-crossfade"],
       "clip-path inset tween power3.inOut 0.5 s; words y 44→0 power4.out, 55 ms stagger"),
    _r("kinetic-slam", "Kinetic slam",
       "Hard cuts, oversized heavy sans in caps and a scale-pop: every scene lands like a punch.",
       {"transition": "cut", "entrance": "scale-pop", "type_treatment": "caps", "ease": "spring", "speed": 1.3, "hold": "pulse"},
       ["kinetic-type", "bold", "fast-cuts", "scale-pop", "caps", "oversized"],
       "hooks, single-sentence statements and stats when the video needs energy; not for quotes",
       ["title", "statement", "stat"], ["caption-kinetic-slam", "top-down-letters"],
       "scale 0.6→1 back.out(1.4) 0.45 s with a hard cut (no exit tween)"),
    _r("push-stack", "Push stack",
       "Scenes push up from below and rows slide in from the left one after another.",
       {"transition": "push-up", "entrance": "line-slide", "ease": "out", "stagger": 0.12},
       ["list", "card-stack", "push-up", "line-slide"],
       "lists, timelines and diagrams with several items that should arrive in order",
       ["list", "timeline", "diagram", "device"], ["line-by-line-slide", "staggered-fade-up"],
       "x -60→0, opacity 0→1 power3.out 0.5 s, stagger 0.12 s; scene y 72→0"),
    _r("zoom-through", "Zoom through",
       "The outgoing scene scales up and fades while the next settles from slightly larger; heavy sans at the largest size.",
       {"transition": "zoom-through", "entrance": "fade-up", "type_treatment": "sans-heavy", "ease": "inOut", "hold": "slow-push"},
       ["zoom", "camera-move", "bold", "slow-push"],
       "a turn in the story, a reveal, the single most important number",
       ["stat", "statement", "comparison", "chart"], ["shared-axis-z", "focus-blur-resolve"],
       "outgoing scale 1→1.12 opacity→0 power2.in 0.35 s; incoming scale 1.08→1 power3.out 0.6 s"),
    _r("accent-field", "Accent field",
       "The scene sits on a full-bleed accent field with inverted ink: a colour hit between paper scenes.",
       {"transition": "cut", "background": "accent-field", "type_treatment": "caps", "entrance": "word-wave"},
       ["full-bleed-word", "high-contrast", "bold", "mono-accent"],
       "one statement or stat per video that deserves a colour change; never two in a row",
       ["statement", "stat", "title"], ["mk-emphasis-type"],
       "ground colour swap with a hard cut; ink inverted via CSS variables; word wave as usual"),
    _r("typewriter-mono", "Typewriter",
       "Text types on character by character in the mono face with a blinking caret.",
       {"transition": "fade", "entrance": "typewriter", "ease": "linear", "hold": "none"},
       ["typewriter", "terminal", "code", "techy"],
       "developer tools, commands, anything that reads like a terminal or a log",
       ["device", "statement", "quote"], ["typewriter"],
       "discrete textContent sets per character (seek-safe), 45 ms per char, caret opacity toggles every 0.5 s"),
    _r("mask-reveal-headline", "Mask reveal",
       "Headlines are revealed from behind a moving mask left to right, scenes wipe from the right.",
       {"transition": "wipe-left", "entrance": "mask-reveal", "ease": "inOut"},
       ["mask-reveal", "kinetic-type", "editorial"],
       "titles and quotes when the reference uses masked text reveals",
       ["title", "statement", "quote", "outro"], ["tracking-in", "kinetic-center-build"],
       "clip-path inset(0 100% 0 0)→inset(0 0 0 0) power3.inOut 0.6 s per line"),
    _r("spring-list", "Spring list",
       "Rows and nodes pop in with a small overshoot, staggered quickly.",
       {"entrance": "scale-pop", "ease": "spring", "stagger": 0.08, "speed": 1.2},
       ["playful", "scale-pop", "spring", "list"],
       "lists, diagrams and device lines when the tone is playful or product-y",
       ["list", "diagram", "device", "chart", "comparison"], ["staggered-fade-up"],
       "scale 0.7→1 back.out(1.6) 0.4 s, stagger 0.08 s"),
    _r("calm-fade", "Calm fade",
       "Everything cross-fades and drifts up slowly; no wipes, no pushes.",
       {"transition": "fade", "entrance": "fade-up", "hold": "float", "ease": "inOut", "speed": 0.8},
       ["minimal", "slow-build", "calm", "fade-up", "float"],
       "quotes, sombre stories, anything that should feel quiet",
       ["quote", "statement", "stat", "outro"], ["per-word-crossfade", "blur-out-up"],
       "opacity 0→1 y 24→0 power2.inOut 0.8 s; hold: y ±6 px sine.inOut loop 2.4 s"),
    _r("flash-cut", "Flash cut",
       "A one-frame accent flash between scenes, letters stepping in; fast and loud.",
       {"transition": "flash", "entrance": "letter-stagger", "ease": "out", "speed": 1.4, "stagger": 0.04},
       ["fast-cuts", "bold", "beat-synced", "letter-stagger"],
       "music-driven or very fast references; use on at most half the scenes",
       ["title", "statement", "stat", "list"], ["top-down-letters", "rgb-glitch-text"],
       "flash div opacity 1→0 over 3 frames at the cut; letters y -40→0 power3.out 0.3 s stagger 0.04 s"),
    _r("blur-focus", "Blur focus",
       "Headlines resolve from heavy blur to crisp; scenes dissolve.",
       {"transition": "fade", "entrance": "blur-in", "ease": "out", "hold": "slow-push"},
       ["minimal", "zoom", "slow-push", "editorial"],
       "a reveal that should feel cinematic rather than cut",
       ["title", "statement", "quote", "stat"], ["focus-blur-resolve"],
       "filter blur(24px)→0 opacity 0→1 power2.out 0.7 s"),
]


def builtin_recipes() -> dict[str, Recipe]:
    return {r.id: r for r in BUILTIN_RECIPES}


# --- recipes derived from an analysed reference -----------------------------------------

_SLIDE_TO_ENTRANCE = {"left": "line-slide", "right": "line-slide", "up": "fade-up", "down": "fade-up"}


def knobs_from_scene(scene: dict, vision: dict | None, palette: dict | None) -> dict[str, Any]:
    """Style knobs for one analysed reference scene: the vision description
    wins where present, the measured evidence fills the rest."""
    k: dict[str, Any] = {}
    vis = vision or {}
    transition = scene.get("transition_in", "cut")
    if vis.get("transition_in", "default") != "default":
        k["transition"] = vis["transition_in"]
    else:
        base = transition.split("-")[0]
        if base == "cut":
            k["transition"] = "cut"
        elif base == "fade" or base == "dissolve":
            k["transition"] = "fade"
        elif transition in ("push-up", "push-left"):
            k["transition"] = transition
        elif transition in ("push-down", "push-right"):
            k["transition"] = "push-up" if transition == "push-down" else "push-left"
        elif transition.startswith("wipe"):
            k["transition"] = "wipe-left" if transition in ("wipe-left", "wipe-right") else "wipe-down"
    motion = scene.get("motion", {})
    events = motion.get("events", [])
    if vis.get("entrance", "default") != "default":
        k["entrance"] = vis["entrance"]
    elif events:
        first = events[0]
        if first.get("sweep") in ("right", "left"):
            k["entrance"] = "mask-reveal"
        elif first.get("slide"):
            k["entrance"] = _SLIDE_TO_ENTRANCE[first["slide"]]
        elif first.get("easing") == "instant" and len(events) >= 4:
            k["entrance"] = "typewriter"
        else:
            k["entrance"] = "fade-up"
    if vis.get("hold", "default") != "default":
        k["hold"] = vis["hold"]
    elif motion.get("loop_period"):
        k["hold"] = "pulse" if motion["loop_period"] < 1.2 else "float"
    elif abs(motion.get("drift", {}).get("dy", 0)) + abs(motion.get("drift", {}).get("dx", 0)) > 0.5:
        k["hold"] = "slow-push"
    elif motion.get("continuous_share", 0) > 0.15:
        k["hold"] = "parallax"
    if vis.get("type_treatment", "default") != "default":
        k["type_treatment"] = vis["type_treatment"]
    elif vis.get("case") == "upper":
        k["type_treatment"] = "caps"
    elif vis.get("scale") == "oversized":
        k["type_treatment"] = "oversized"
    if vis.get("background", "default") != "default":
        k["background"] = vis["background"]
    if vis.get("ease", "default") != "default":
        k["ease"] = vis["ease"]
    elif events:
        shapes = [e.get("easing") for e in events if e.get("easing") not in (None, "instant")]
        if shapes:
            top = max(set(shapes), key=shapes.count)
            k["ease"] = {"ease-out": "out", "ease-in-out": "inOut", "linear": "linear", "ease-in": "out"}.get(top, "out")
    stagger = motion.get("stagger")
    if stagger and 0.02 <= stagger.get("interval", 0) <= 0.4:
        k["stagger"] = stagger["interval"]
    # speed: the reference's typical entrance length against the template's 0.6 s
    durations = [e["t1"] - e["t0"] for e in events if e.get("kind") != "disappear" and e["t1"] > e["t0"]]
    if durations:
        typical = sorted(durations)[len(durations) // 2]
        k["speed"] = round(min(2.0, max(0.5, 0.6 / max(typical, 0.15))), 2)
    if palette and palette.get("accent"):
        k["accent"] = palette["accent"]
    return k


def recipes_from_analysis(name: str, analysis: dict) -> list[Recipe]:
    """One whole-reference recipe (the dominant knobs across scenes, for
    'make it look like X') plus one recipe per distinctive scene."""
    scenes = analysis.get("scenes", [])
    vision = analysis.get("vision", {}) or {}
    vis_scenes = {int(k): v for k, v in (vision.get("scenes") or {}).items()} if vision else {}
    palette = analysis.get("palette")
    overall = (vision.get("overall") or {}) if vision else {}
    fidelity = (analysis.get("recreation") or {}).get("score")
    per_scene: list[tuple[dict, dict]] = []
    for s in scenes:
        per_scene.append((s, knobs_from_scene(s, vis_scenes.get(s["index"]), palette)))
    recipes: list[Recipe] = []
    if per_scene:
        # dominant knob values across the reference
        dominant: dict[str, Any] = {}
        for knob in ("transition", "entrance", "hold", "type_treatment", "background", "ease"):
            values = [k[knob] for _, k in per_scene if k.get(knob)]
            if values:
                dominant[knob] = max(set(values), key=values.count)
        speeds = [k["speed"] for _, k in per_scene if k.get("speed")]
        if speeds:
            dominant["speed"] = round(sorted(speeds)[len(speeds) // 2], 2)
        staggers = [k["stagger"] for _, k in per_scene if k.get("stagger")]
        if staggers:
            dominant["stagger"] = round(sorted(staggers)[len(staggers) // 2], 2)
        if palette and palette.get("accent"):
            dominant["accent"] = palette["accent"]
        feel = list(overall.get("feel", [])) + list(overall.get("tags", []))
        cad = analysis.get("cadence", {})
        tags = sorted(set(feel + [v for v in dominant.values() if isinstance(v, str) and v in V.all_tags()]
                          + ([cad.get("pace", "") + "-cuts"] if cad.get("pace") == "fast" else [])
                          + ([palette.get("theme", "")] if palette else [])))
        recipes.append(Recipe(
            id=f"{name}-look", name=f"{name}: overall look",
            description=overall.get("summary") or f"The dominant style of reference '{name}': " + ", ".join(f"{k} {v}" for k, v in dominant.items()),
            knobs=dominant, demonstrates=[t for t in tags if t], when_to_use=overall.get("reuse", "") or "when a video should feel like this reference",
            scene_types=["*"], registry_refs=_registry_refs(vis_scenes), gsap=overall.get("motion", ""),
            source=name, fidelity=fidelity, params=KNOB_PARAMS,
        ))
    seen: set[tuple] = set()
    for s, k in per_scene:
        vis = vis_scenes.get(s["index"], {})
        key = tuple(sorted((kk, str(vv)) for kk, vv in k.items() if kk not in ("speed", "stagger", "accent")))
        if not k or key in seen:
            continue
        seen.add(key)
        stype = vis.get("scene_type", "statement")
        demonstrates = sorted(set([v for v in k.values() if isinstance(v, str) and v in V.all_tags()] + list(vis.get("effects", []))))
        recipes.append(Recipe(
            id=f"{name}-s{s['index'] + 1}", name=f"{name}: scene {s['index'] + 1} ({stype})",
            description=vis.get("what") or f"Scene {s['index'] + 1} of '{name}', {s['duration']:.1f} s, arrives by {s.get('transition_in')}",
            knobs=k, demonstrates=demonstrates,
            when_to_use=f"a {stype} scene that should move like scene {s['index'] + 1} of '{name}'",
            scene_types=[stype] if stype in V.SCENE_TYPES else ["*"],
            registry_refs=list(vis.get("registry_keywords", [])), gsap=vis.get("gsap", ""),
            source=name, fidelity=fidelity, params=KNOB_PARAMS,
        ))
    return recipes[:12]


def _registry_refs(vis_scenes: dict) -> list[str]:
    seen: list[str] = []
    for v in vis_scenes.values():
        for kw in v.get("registry_keywords", []):
            if kw not in seen:
                seen.append(kw)
    return seen[:8]
