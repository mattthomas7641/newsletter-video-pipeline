"""Cuts an avatar out of a flat-coloured background (the kind image generators produce:
one plain colour, usually with a soft ground shadow under the feet).

    cut = cutout(Image.open("pose.png"))            # RGBA, same size as the input
    images = cut_poses([Path("a.png"), Path("b.png")])  # all cropped to one shared box

Poses of one character are drawn on the same canvas, so they are cropped to the *union* box
rather than each to its own: the feet, body and head then line up exactly when the video
swaps one pose for another.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

BG_TOLERANCE = 24  # colour distance that still counts as the background
POCKET_TOLERANCE = 30  # looser, for the small gaps the figure encloses (between arm and body)
MIN_BG_AREA = 150  # pixels; smaller background-coloured specks are details of the drawing
MIN_POCKET_AREA = 60
SHADOW_ZONE = 0.12  # the bottom share of the picture where a ground shadow can sit


def _near(rgb: np.ndarray, colour, tol: float) -> np.ndarray:
    return np.sqrt(((rgb - np.asarray(colour, dtype=float)) ** 2).sum(axis=2)) < tol


def _border_colour(rgb: np.ndarray) -> np.ndarray:
    edge = np.concatenate([rgb[:6].reshape(-1, 3), rgb[-6:].reshape(-1, 3),
                           rgb[:, :6].reshape(-1, 3), rgb[:, -6:].reshape(-1, 3)])
    return np.median(edge, axis=0)


def cutout(image: Image.Image) -> Image.Image:
    """The picture with its background (and ground shadow) made transparent, at the input's size."""
    from scipy import ndimage as ndi

    src = image.convert("RGB")
    rgb = np.asarray(src).astype(float)
    height = rgb.shape[0]
    bg = _border_colour(rgb)

    # Background: every sizeable region of the background colour, wherever it is.
    near_bg = _near(rgb, bg, BG_TOLERANCE)
    labels, count = ndi.label(near_bg)
    sizes = ndi.sum(near_bg, labels, range(1, count + 1)) if count else []
    remove = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= MIN_BG_AREA])

    # Pockets the figure encloses (an arm held away from the body): background-coloured on average,
    # but not the white of a collar or the silver of a watch.
    loose = _near(rgb, bg, POCKET_TOLERANCE) & ~remove
    plabels, pcount = ndi.label(loose)
    for i in range(1, pcount + 1):
        part = plabels == i
        if part.sum() >= MIN_POCKET_AREA and np.sqrt(((rgb[part].mean(axis=0) - bg) ** 2).sum()) < 9:
            remove |= part

    # Ground shadow: a darker patch of the background hue near the bottom, i.e. pixels close to the line
    # bg * k (k between 0.4 and 0.9). Shoes and outlines are far from that line, so they stay.
    zone = np.zeros(rgb.shape[:2], bool)
    zone[int(height * (1 - SHADOW_ZONE)):] = True
    k = (rgb @ bg) / (bg @ bg)
    off_line = np.sqrt(((rgb - k[..., None] * bg) ** 2).sum(axis=2))
    shadow = zone & (k > 0.4) & (k < 0.9) & (off_line < 22)
    slabels, scount = ndi.label(shadow)
    if scount:
        ssizes = ndi.sum(shadow, slabels, range(1, scount + 1))
        remove |= np.isin(slabels, [i + 1 for i, s in enumerate(ssizes) if s > 150])

    keep = ndi.binary_opening(~remove, iterations=1)
    klabels, kcount = ndi.label(keep)
    if kcount:  # the biggest piece is the figure; stray specks go
        ksizes = ndi.sum(keep, klabels, range(1, kcount + 1))
        keep = klabels == (1 + int(np.argmax(ksizes)))
    keep = ndi.binary_erosion(keep, iterations=1)  # no fringe of background colour
    alpha = Image.fromarray((keep * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.9))
    out = src.convert("RGBA")
    out.putalpha(alpha)
    return out


def _box(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()


def cut_poses(sources: list[Path], max_side: int = 2000) -> list[Image.Image]:
    """Every source cut out and cropped to one shared box. They must be the same size."""
    cuts = [cutout(Image.open(p)) for p in sources]
    sizes = {c.size for c in cuts}
    if len(sizes) > 1:
        raise ValueError("The poses are different sizes (" + ", ".join(f"{w}x{h}" for w, h in sorted(sizes))
                         + "); draw them on the same canvas so they line up.")
    boxes = [b for b in (_box(c) for c in cuts) if b]
    if not boxes:
        raise ValueError("Nothing was left after removing the background; is the background one plain colour?")
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    out = []
    for c in cuts:
        c = c.crop(union)
        c.thumbnail((max_side, max_side))
        out.append(c)
    return out
