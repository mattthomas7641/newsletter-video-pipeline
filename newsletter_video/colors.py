"""The two colours a look can change: the ground (`bg`) and the accent the
animations, highlights, bars and neon use. Hex only, and an automatic guard
so an unreadable pair never renders: the text colour is chosen by the look
(light ink on the dark glass look, dark ink on paper), so the guard keeps the
ground on the right side of that and the accent readable against it.

The same arithmetic lives in the panel (static/js/colors.js) for the live
preview; this module is what a render actually uses.
"""
from __future__ import annotations

import colorsys
import re

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
PAPER_BG_MIN_LUM = 0.55  # a light ground: dark ink stays readable
GLASS_BG_MAX_LUM = 0.06  # a dark ground: light ink and neon stay readable
ACCENT_MIN_CONTRAST = 3.0  # large text, bars and strokes against the ground (WCAG large-text bar)


ROTATION_MAX = 8
# The accents the automatic videos of a day alternate through when rotation is on (Dark glass): orange,
# magenta, cyan, violet, mint. Consecutive videos always differ.
DEFAULT_ROTATION = ("#ff7a1a", "#ff3d8a", "#22d3ee", "#8b5cff", "#2ee6a6")


def parse_rotation(value: str | None, strict: bool = False) -> list[str]:
    """A comma-separated list of #rrggbb colours, normalised and capped at ROTATION_MAX. Bad entries are
    skipped when reading; `strict` (the settings validator) raises ValueError naming the first one."""
    out: list[str] = []
    for raw in (value or "").split(","):
        if not raw.strip():
            continue
        hex_ = normalise_hex(raw)
        if hex_ is None:
            if strict:
                raise ValueError(f"{raw.strip()!r} is not a colour like #1f49d6")
            continue
        out.append(hex_)
    if strict and len(out) > ROTATION_MAX:
        raise ValueError(f"at most {ROTATION_MAX} colours")
    return out[:ROTATION_MAX]


def normalise_hex(value: str) -> str | None:
    """'#RRGGBB' lower-cased, from '#rgb', 'rgb', '#rrggbb' or 'rrggbb'; None when not a colour."""
    v = (value or "").strip().lower().lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3}", v):
        v = "".join(c * 2 for c in v)
    return f"#{v}" if re.fullmatch(r"[0-9a-f]{6}", v) else None


def rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_hex(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(x))):02x}" for x in c)


def luminance(hex_: str) -> float:
    def f(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(hex_)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(hex_: str, target: tuple[int, int, int], t: float) -> str:
    c = rgb(hex_)
    return to_hex(tuple(c[i] + (target[i] - c[i]) * t for i in range(3)))


def _push(hex_: str, toward: tuple[int, int, int], ok) -> str:
    """Mix `hex_` toward `toward` in small steps until `ok(colour)` holds."""
    out = hex_
    for i in range(1, 41):
        if ok(out):
            return out
        out = _mix(hex_, toward, i / 40)
    return out


def fit_colors(theme: str, bg: str | None, accent: str | None) -> dict:
    """Make a ground and accent safe for the look's theme.

    `theme` is "cinematic" (dark glass) or anything else (paper: light).
    Returns {"bg", "accent", "notes"}: values are None where unset, and
    `notes` lists in plain words anything adjusted ("Background darkened ...").
    A colour that was already fine comes back untouched."""
    notes: list[str] = []
    dark = theme == "cinematic"
    base_bg = "#050509" if dark else "#f6f4ee"
    out_bg = normalise_hex(bg) if bg else None
    out_accent = normalise_hex(accent) if accent else None
    if out_bg:
        if dark and luminance(out_bg) > GLASS_BG_MAX_LUM:
            fixed = _push(out_bg, (0, 0, 0), lambda c: luminance(c) <= GLASS_BG_MAX_LUM)
            notes.append(f"Background darkened from {out_bg} to {fixed}: the glass look needs a dark ground to keep the light text readable.")
            out_bg = fixed
        elif not dark and luminance(out_bg) < PAPER_BG_MIN_LUM:
            fixed = _push(out_bg, (255, 255, 255), lambda c: luminance(c) >= PAPER_BG_MIN_LUM)
            notes.append(f"Background lightened from {out_bg} to {fixed}: the paper look needs a light ground to keep the dark text readable.")
            out_bg = fixed
    if out_accent:
        ground = out_bg or base_bg
        if contrast(out_accent, ground) < ACCENT_MIN_CONTRAST:
            toward = (255, 255, 255) if dark else (0, 0, 0)
            fixed = _push(out_accent, toward, lambda c: contrast(c, ground) >= ACCENT_MIN_CONTRAST)
            notes.append(f"Animation colour {'lightened' if dark else 'darkened'} from {out_accent} to {fixed} so it stands out against the background.")
            out_accent = fixed
    return {"bg": out_bg, "accent": out_accent, "notes": notes}


def palette_from_accent(accent: str) -> list[str]:
    """The glass look's four glow colours from one accent: the accent itself,
    then three neighbours spread round the hue wheel (the shipped orange /
    magenta / violet / cyan set is the same shape), kept bright and saturated."""
    r, g, b = (x / 255 for x in rgb(accent))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s = max(s, 0.65)
    l = min(max(l, 0.5), 0.62)
    out = [accent]
    for off in (-0.14, -0.34, 0.45):
        rr, gg, bb = colorsys.hls_to_rgb((h + off) % 1.0, l, s)
        out.append(to_hex((rr * 255, gg * 255, bb * 255)))
    return out
