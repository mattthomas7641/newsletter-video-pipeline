"""The shared vocabulary of the style library: the closed sets of effect,
transition, typography, layout and colour tags that the analyzer emits, the
vision model is asked to map what it sees onto, recipes are described with,
search understands, and the explainer template can render.

Everything downstream (analysis.json, recipes, style plans, the planner
prompt) speaks in these words, so a reference video, a recipe and a new
story's scene plan can be compared like for like.
"""
from __future__ import annotations

# --- transitions between scenes -------------------------------------------------
# What the template can render for a scene's `style.transition`.
TRANSITIONS: dict[str, str] = {
    "default": "the template's own alternation (push on even scenes, top-down wipe on odd ones)",
    "cut": "a hard cut: the new scene is simply there on its first frame",
    "push-up": "the new scene rises in from below while the old one lifts out",
    "push-left": "the new scene slides in from the right while the old one exits left",
    "wipe-down": "the new scene is revealed top to bottom behind a travelling accent edge",
    "wipe-left": "the new scene is revealed right to left behind a travelling accent edge",
    "zoom-through": "the old scene scales up and fades while the new one settles from slightly larger",
    "mask-circle": "the new scene opens from a circle at the centre",
    "fade": "a plain cross-fade",
    "flash": "a brief accent flash frame between the scenes",
}

# --- how the hero of a scene enters ---------------------------------------------
ENTRANCES: dict[str, str] = {
    "default": "the template's own entrance for the scene type (word wave, rows, draws, counts)",
    "word-wave": "words rise one after another from below",
    "line-slide": "whole lines slide in from the left, staggered",
    "typewriter": "text appears character by character",
    "scale-pop": "the element scales up from small to full size",
    "fade-up": "a soft fade with a short upward drift",
    "mask-reveal": "text is revealed from behind a moving mask, left to right",
    "letter-stagger": "letters arrive one after another in a staircase",
    "blur-in": "the element resolves from heavy blur to crisp",
}

# --- what keeps moving while the scene holds --------------------------------------
HOLDS: dict[str, str] = {
    "default": "the template's own hold motion (slow push-in, head parallax, retraces, sweeps)",
    "slow-push": "a slow continuous push-in on the hero",
    "parallax": "the hero and head drift at different rates",
    "float": "a gentle vertical float loop on the hero",
    "pulse": "the hero breathes in scale on a steady period",
    "none": "no hold motion beyond the element animations",
}

# --- typography treatments ---------------------------------------------------------
TYPE_TREATMENTS: dict[str, str] = {
    "default": "the template's type system (serif display, sans body, mono labels)",
    "caps": "headline set in uppercase with wide tracking",
    "oversized": "the headline is pushed to the largest size that fits, edge to edge",
    "highlight-band": "the emphasised phrase sits on an accent band instead of changing colour",
    "outline": "the headline is drawn as an outlined stroke, filling in on emphasis",
    "sans-heavy": "the headline is set in the heavy sans instead of the serif",
}

# --- background treatments ----------------------------------------------------------
BACKGROUNDS: dict[str, str] = {
    "default": "the paper ground with the dot grid",
    "solid": "a flat ground with no dot grid",
    "accent-field": "the scene sits on a full-bleed accent field with inverted ink",
    "dark-panel": "a dark panel behind the hero on the light ground (or vice versa)",
    "gradient-wash": "a soft two-stop wash in the accent hue",
    "mesh-gradient": "the registry's mesh-gradient shader drifting behind the scene (cinematic look; WebGPU)",
}

# --- look families: the whole-video ground a recipe asks for --------------------------
# A recipe learned from a dark, glowing, 3D-looking reference carries
# `look: cinematic`; selecting it switches the video to the cinematic theme
# (near-black ground, perspective grid floor, glow accents from the
# reference's palette, vignette and grain) and unlocks the rich scene types
# (object3d, depth, kinetic, glass). "default" is the paper look.
LOOKS: dict[str, str] = {
    "default": "the template's own paper look (light or dark theme as configured)",
    "cinematic": "a dark cinematic ground with glow accents, a perspective grid, vignette and grain; 3D and glass scenes fit",
}
MAX_PALETTE = 4  # glow colours a recipe may carry

# --- easing families --------------------------------------------------------------
EASES: dict[str, str] = {
    "default": "the template's own eases (power3.out entrances, power2.in exits)",
    "out": "decelerating: fast start, soft landing (power3.out)",
    "inOut": "symmetric: slow start and end (power3.inOut)",
    "spring": "a small overshoot before settling (back.out(1.4))",
    "linear": "constant speed (none)",
}

GSAP_EASE = {"out": "power3.out", "inOut": "power3.inOut", "spring": "back.out(1.4)", "linear": "none"}

# --- free-form descriptive tags the analyzer and vision model may emit ------------
# (searchable; not everything here is renderable, and that is fine: a tag
# describes a reference, a recipe says what we can do about it)
FEEL_TAGS: tuple[str, ...] = (
    "kinetic-type", "editorial", "minimal", "bold", "playful", "corporate", "techy", "retro",
    "neon", "pastel", "monochrome", "mono-accent", "duotone", "multi-hue", "high-contrast", "dark", "light", "grainy",
    "flat", "3d", "isometric", "hand-drawn", "glitch", "data-viz", "chart-heavy", "icon-led",
    "photo-led", "footage-led", "caption-heavy", "fast-cuts", "slow-build", "calm", "energetic", "beat-synced",
    "voice-synced", "looping", "count-up", "line-draw", "mask-reveal", "parallax", "camera-move",
    "zoom", "split-screen", "grid", "card-stack", "full-bleed-word", "lower-third", "sticker",
    "emoji", "device-mockup", "terminal", "code", "map", "timeline", "list", "comparison",
    "quote", "stat", "diagram", "music-only", "narrated", "glow", "cinematic", "glass", "wireframe",
)

FONT_CLASSES: tuple[str, ...] = ("serif", "sans-grotesk", "sans-geometric", "sans-humanist", "mono", "display", "script", "mixed")
CASES: tuple[str, ...] = ("mixed", "upper", "lower", "title")
LAYOUTS: tuple[str, ...] = ("centered-stack", "left-column", "split-halves", "grid", "full-bleed-word", "card", "lower-third", "scattered")

SCENE_TYPES: tuple[str, ...] = ("title", "statement", "stat", "comparison", "list", "timeline", "quote", "chart",
                                "diagram", "device", "object3d", "depth", "kinetic", "glass", "outro")
# The rich scene types the cinematic look unlocks, and what a reference must
# show for each to be a good fit (see recipes.cinematic_fits).
RICH_SCENE_TYPES: tuple[str, ...] = ("object3d", "depth", "kinetic", "glass")

# Search synonyms: a query word on the left matches any tag on the right.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "wipe": ("wipe-down", "wipe-left", "mask-reveal"),
    "wipes": ("wipe-down", "wipe-left", "mask-reveal"),
    "push": ("push-up", "push-left"),
    "slide": ("push-up", "push-left", "line-slide"),
    "zoom": ("zoom-through", "zoom", "camera-move", "slow-push"),
    "type": ("kinetic-type", "typewriter", "word-wave", "letter-stagger", "caps", "oversized"),
    "typography": ("kinetic-type", "word-wave", "letter-stagger", "caps", "oversized"),
    "kinetic": ("kinetic-type", "word-wave", "letter-stagger", "line-slide"),
    "text": ("kinetic-type", "word-wave", "typewriter", "line-slide"),
    "words": ("word-wave", "kinetic-type"),
    "numbers": ("count-up", "stat"),
    "number": ("count-up", "stat"),
    "counter": ("count-up",),
    "chart": ("chart-heavy", "data-viz", "chart"),
    "charts": ("chart-heavy", "data-viz", "chart"),
    "graph": ("chart-heavy", "data-viz"),
    "fast": ("fast-cuts",),
    "quick": ("fast-cuts",),
    "punchy": ("fast-cuts", "bold", "scale-pop"),
    "calm": ("slow-build", "minimal", "fade-up"),
    "slow": ("slow-build",),
    "music": ("beat-synced", "music-only"),
    "beat": ("beat-synced",),
    "voice": ("narrated", "voice-synced"),
    "dark": ("dark",),
    "light": ("light",),
    "mask": ("mask-reveal", "mask-circle"),
    "circle": ("mask-circle",),
    "pop": ("scale-pop",),
    "bounce": ("spring",),
    "spring": ("spring",),
    "draw": ("line-draw",),
    "drawn": ("line-draw", "hand-drawn"),
    "loop": ("looping", "float", "pulse"),
    "float": ("float",),
    "caps": ("caps",),
    "uppercase": ("caps",),
    "big": ("oversized", "full-bleed-word"),
    "huge": ("oversized", "full-bleed-word"),
    "highlight": ("highlight-band",),
    "gradient": ("gradient-wash",),
    "flat": ("flat", "solid"),
    "grain": ("grainy",),
    "glitch": ("glitch",),
    "phone": ("device-mockup",),
    "device": ("device-mockup",),
    "editorial": ("editorial", "serif"),
    "serif": ("serif",),
    "cinematic": ("cinematic", "dark", "glow", "3d"),
    "neon": ("neon", "glow", "cinematic"),
    "3d": ("3d", "isometric", "cinematic"),
    "glow": ("glow", "neon"),
    "glass": ("glass", "cinematic"),
}


def all_tags() -> frozenset[str]:
    return frozenset(
        set(TRANSITIONS) | set(ENTRANCES) | set(HOLDS) | set(TYPE_TREATMENTS) | set(BACKGROUNDS)
        | set(EASES) | set(LOOKS) | set(FEEL_TAGS) | set(FONT_CLASSES) | set(LAYOUTS) | set(SCENE_TYPES)
    ) - {"default"}


def expand_query(text: str) -> set[str]:
    """Tokens of a search query plus their synonyms, lower-cased."""
    import re

    words = [w for w in re.split(r"[^a-z0-9-]+", text.lower()) if w]
    out: set[str] = set()
    for w in words:
        out.add(w)
        out.update(SYNONYMS.get(w, ()))
        if w.endswith("s") and w[:-1] in SYNONYMS:
            out.update(SYNONYMS[w[:-1]])
    return out
