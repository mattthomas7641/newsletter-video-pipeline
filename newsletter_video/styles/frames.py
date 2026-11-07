"""Still-frame evidence: colour palette, contrast and theme; the layout of a
keyframe as a coarse occupancy grid with alignment and density; and the
distances between palettes and layouts used by the fidelity checks."""
from __future__ import annotations

import numpy as np

GRID = 16  # occupancy grid is GRID x GRID in normalised coordinates (aspect-agnostic)


# --- colour ----------------------------------------------------------------------------

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def relative_luminance(rgb) -> float:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def contrast_ratio(a, b) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def palette(frames: np.ndarray, k: int = 6) -> dict:
    """Dominant colours of a set of RGB frames (n, h, w, 3) by quantisation
    (5 bits per channel) and merging of close bins; the background from the
    frame borders; the accent as the most saturated non-neutral colour."""
    px = frames.reshape(-1, 3)
    if len(px) > 60000:
        px = px[np.random.default_rng(0).choice(len(px), 60000, replace=False)]
    q = (px >> 3).astype(np.int32)
    codes = (q[:, 0] << 10) | (q[:, 1] << 5) | q[:, 2]
    counts = np.bincount(codes, minlength=32768)
    order = np.argsort(counts)[::-1]
    picked: list[tuple[np.ndarray, int]] = []
    for code in order[:200]:
        c = int(counts[code])
        if c == 0:
            break
        rgb = np.array([(code >> 10) & 31, (code >> 5) & 31, code & 31], dtype=np.float64) * 8 + 4
        merged = False
        for j, (prgb, pc) in enumerate(picked):
            if np.abs(prgb - rgb).max() <= 24:
                picked[j] = ((prgb * pc + rgb * c) / (pc + c), pc + c)
                merged = True
                break
        if not merged:
            picked.append((rgb, c))
    picked.sort(key=lambda p: -p[1])
    total = float(sum(c for _, c in picked)) or 1.0
    colours = [{"hex": _hex(rgb), "share": round(c / total, 3)} for rgb, c in picked[:k]]
    # background: the median of the border pixels of the frames
    border = np.concatenate([frames[:, 0].reshape(-1, 3), frames[:, -1].reshape(-1, 3),
                             frames[:, :, 0].reshape(-1, 3), frames[:, :, -1].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    bg_lum = relative_luminance(bg)
    theme = "dark" if bg_lum < 0.25 else ("light" if bg_lum > 0.6 else "mid")
    # accent: most saturated dominant colour that is not the background and not neutral
    def sat(rgb):
        rgb = np.asarray(rgb, dtype=np.float64)
        mx, mn = rgb.max(), rgb.min()
        return 0.0 if mx == 0 else (mx - mn) / mx
    accent = None
    ink = None
    ink_gap = 0.3
    for rgb, c in picked[:12]:
        if np.abs(rgb - bg).max() < 30:
            continue
        if sat(rgb) > 0.35 and c / total > 0.003 and accent is None:
            accent = _hex(rgb)
        gap = abs(relative_luminance(rgb) - bg_lum)
        if sat(rgb) < 0.35 and gap > ink_gap and c / total > 0.003:
            ink, ink_gap = _hex(rgb), gap
    fg_candidates = [rgb for rgb, _ in picked[:6] if np.abs(rgb - bg).max() >= 30]
    contrast = max((contrast_ratio(rgb, bg) for rgb in fg_candidates), default=1.0)
    hues = set()
    for rgb, c in picked[:8]:
        if sat(rgb) > 0.3 and c / total > 0.01:
            r, g, b = rgb / 255.0
            mx, mn = max(r, g, b), min(r, g, b)
            if mx == mn:
                continue
            if mx == r:
                h = (60 * ((g - b) / (mx - mn)) + 360) % 360
            elif mx == g:
                h = 60 * ((b - r) / (mx - mn)) + 120
            else:
                h = 60 * ((r - g) / (mx - mn)) + 240
            hues.add(int(h // 30))
    return {"colours": colours, "background": _hex(bg), "theme": theme, "accent": accent, "ink": ink,
            "contrast": round(float(contrast), 2), "hue_count": len(hues),
            "colour_feel": "monochrome" if len(hues) == 0 else ("mono-accent" if len(hues) == 1 else ("duotone" if len(hues) == 2 else "multi-hue"))}


def palette_similarity(a: list[str], b: list[str]) -> float:
    """1 - normalised mean nearest-colour distance in Lab between two palettes
    (symmetric). 1.0 is the same palette; ~0.5 is unrelated."""
    if not a or not b:
        return 0.0
    la = _srgb_to_lab(np.array([hex_to_rgb(h) for h in a]))
    lb = _srgb_to_lab(np.array([hex_to_rgb(h) for h in b]))
    d = np.linalg.norm(la[:, None, :] - lb[None, :, :], axis=2)
    mean = (d.min(axis=1).mean() + d.min(axis=0).mean()) / 2
    return round(float(max(0.0, 1.0 - mean / 100.0)), 3)


# --- layout --------------------------------------------------------------------------

def ink_mask(rgb: np.ndarray, bg_hex: str | None = None, thr: float = 18.0) -> np.ndarray:
    """Pixels far enough from the background colour to be content."""
    if bg_hex is None:
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
    else:
        bg = np.array(hex_to_rgb(bg_hex), dtype=np.float64)
    lab = _srgb_to_lab(rgb)
    lab_bg = _srgb_to_lab(bg)
    return np.linalg.norm(lab - lab_bg, axis=2) > thr


def occupancy_grid(mask: np.ndarray, grid: int = GRID) -> np.ndarray:
    h, w = mask.shape
    out = np.zeros((grid, grid))
    for gy in range(grid):
        for gx in range(grid):
            y0, y1 = int(gy * h / grid), max(int((gy + 1) * h / grid), int(gy * h / grid) + 1)
            x0, x1 = int(gx * w / grid), max(int((gx + 1) * w / grid), int(gx * w / grid) + 1)
            out[gy, gx] = mask[y0:y1, x0:x1].mean()
    return out


def layout(rgb: np.ndarray, bg_hex: str | None = None) -> dict:
    """Where the content sits in a keyframe: occupancy grid, vertical thirds,
    horizontal alignment, blob count and a text-likeness guess."""
    from scipy import ndimage

    mask = ink_mask(rgb, bg_hex)
    grid = occupancy_grid(mask)
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return {"grid": grid.round(3).tolist(), "fill": 0.0, "thirds": [0.0, 0.0, 0.0], "align": "center",
                "blobs": 0, "text_like": False, "bbox": None, "kind": "empty"}
    fill = float(mask.mean())
    thirds = [round(float(mask[: h // 3].mean()), 3), round(float(mask[h // 3: 2 * h // 3].mean()), 3),
              round(float(mask[2 * h // 3:].mean()), 3)]
    cx = float(xs.mean()) / w
    left_w, right_w = float(mask[:, : w // 2].mean()), float(mask[:, w // 2:].mean())
    if abs(left_w - right_w) < 0.12 * max(left_w, right_w, 1e-6) and 0.4 < cx < 0.6:
        align = "center"
    elif cx < 0.45:
        align = "left"
    elif cx > 0.55:
        align = "right"
    else:
        align = "center"
    labelled, blobs = ndimage.label(ndimage.binary_dilation(mask, iterations=1))
    sizes = ndimage.sum(mask, labelled, range(1, blobs + 1)) if blobs else []
    small = [s for s in sizes if s < 0.02 * h * w]
    bbox = [round(float(xs.min()) / w, 3), round(float(ys.min()) / h, 3),
            round(float(xs.max() - xs.min() + 1) / w, 3), round(float(ys.max() - ys.min() + 1) / h, 3)]
    # text-likeness: many small blobs whose rows line up
    text_like = blobs >= 6 and len(small) >= 0.7 * blobs
    if fill > 0.55:
        kind = "full-bleed"
    elif bbox[2] > 0.8 and bbox[3] < 0.4:
        kind = "band"
    elif bbox[3] > 0.6 and bbox[2] < 0.5:
        kind = "column"
    elif len(sizes) and max(sizes) > 0.35 * mask.sum() and blobs > 1:
        kind = "hero-plus-text"
    else:
        kind = "stack"
    return {"grid": grid.round(3).tolist(), "fill": round(fill, 3), "thirds": thirds, "align": align,
            "blobs": int(blobs), "text_like": bool(text_like), "bbox": bbox, "kind": kind}


def layout_similarity(grid_a, grid_b) -> float:
    """Correlation of two occupancy grids mapped to 0..1 (0.5 = unrelated)."""
    a = np.asarray(grid_a, dtype=np.float64).ravel()
    b = np.asarray(grid_b, dtype=np.float64).ravel()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 1.0 if abs(a.mean() - b.mean()) < 0.05 else 0.0
    r = float(np.corrcoef(a, b)[0, 1])
    return round((r + 1) / 2, 3)
