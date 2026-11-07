# MODIFIED from rea-video (https://github.com/JuneYaooo/rea-video)
# Copyright 2026 JuneYaooo. Licensed under the Apache License, Version 2.0.
#
# Changes (see README.md in this directory): every function was rewritten
# against numpy only (upstream uses OpenCV), signatures were generalised to
# plain arrays, and the shot-boundary scoring was reduced to the parts the
# style library needs (hard cuts, adaptive threshold). Thresholds are kept
# as upstream tuned them.
"""Signal primitives for shot splitting, motion-event description and tempo
estimation, adapted from rea-video. All functions are pure numpy."""
from __future__ import annotations

import numpy as np


def local_median(x: np.ndarray, radius: int) -> np.ndarray:
    """Median of the window around each sample, excluding the sample itself
    (so an isolated spike does not raise its own baseline)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    out = np.zeros(n)
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        window = np.concatenate([x[lo:i], x[i + 1:hi]]) if hi - lo > 1 else x[lo:hi]
        out[i] = np.median(window) if len(window) else 0.0
    return out


def colour_histogram(rgb: np.ndarray, bins: tuple[int, int, int] = (8, 4, 4)) -> np.ndarray:
    """An L1-normalised 3-D histogram of an RGB thumbnail (upstream uses HSV
    with 16x4x4 bins; RGB with fewer bins is enough on 96 px thumbs and needs
    no colour-space conversion)."""
    flat = rgb.reshape(-1, 3).astype(np.int32)
    idx = ((flat[:, 0] * bins[0]) // 256) * bins[1] * bins[2] + ((flat[:, 1] * bins[1]) // 256) * bins[2] + (flat[:, 2] * bins[2]) // 256
    h = np.bincount(idx, minlength=bins[0] * bins[1] * bins[2]).astype(np.float64)
    return h / max(1.0, h.sum())


def bhattacharyya(h1: np.ndarray, h2: np.ndarray) -> float:
    """Bhattacharyya distance between two normalised histograms (0 = same)."""
    bc = float(np.sqrt(h1 * h2).sum())
    return float(np.sqrt(max(0.0, 1.0 - bc)))


def boundary_score(hist_dist: np.ndarray, pix_diff: np.ndarray, changed: np.ndarray, fps: float,
                   cut_threshold: float = 0.30) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame cut evidence and the hard-cut decision, as upstream combines
    them: 0.6 * histogram distance + 0.4 * clipped pixel difference, judged
    against the local activity level so flicker and fast motion do not fire.
    Returns (score, is_cut) with index i meaning "frame i starts a new shot"."""
    score = 0.6 * np.asarray(hist_dist) + 0.4 * np.clip(np.asarray(pix_diff) * 4.0, 0, 1)
    score = score.astype(np.float64)
    if len(score):
        score[0] = 0.0
    radius = max(4, int(round(fps * 0.5)))
    base = local_median(score, radius)
    chg = np.asarray(changed, dtype=np.float64)
    chg_base = local_median(chg, radius)
    strong = (score > cut_threshold) & (score > base * 2.5 + 0.06) & (chg > 0.25)
    relative = (chg > 0.45) & (chg > chg_base * 3 + 0.2) & (score > base * 4 + 0.03)
    is_cut = strong | relative
    if len(is_cut):
        is_cut[0] = False
    return score, is_cut


def easing_shape(activity: np.ndarray) -> str:
    """Where in its life an event does its changing: most of it early is an
    ease-out, late an ease-in, evenly a linear move, in the middle an
    ease-in-out. Thresholds as upstream."""
    act = np.asarray(activity, dtype=np.float64)
    if len(act) < 4 or act.sum() <= 0:
        return "instant"
    c = np.cumsum(act) / act.sum()
    half = c[len(c) // 2 - (1 if len(c) % 2 == 0 else 0)]
    if half > 0.68:
        return "ease-out"
    if half < 0.32:
        return "ease-in"
    cv = float(act.std() / (act.mean() + 1e-6))
    return "linear" if cv < 0.45 else "ease-in-out"


def sweep_direction(onset: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> str | None:
    """If the start time of change sweeps across a region (a wipe, a growing
    bar, a drawn line), the direction it travels; None when it does not."""
    on = np.asarray(onset, dtype=np.float64)
    if len(on) < 12 or on.std() < 1e-6:
        return None
    best, axis = 0.0, None
    for coord, pos, neg in ((np.asarray(xs, dtype=np.float64), "right", "left"),
                            (np.asarray(ys, dtype=np.float64), "down", "up")):
        if coord.std() < 1e-6:
            continue
        r = float(np.corrcoef(coord, on)[0, 1])
        if abs(r) > abs(best):
            best, axis = r, (pos if r > 0 else neg)
    return axis if abs(best) > 0.6 else None


def tempo_from_onsets(onset_env: np.ndarray, hop_s: float, lo_bpm: float = 60.0, hi_bpm: float = 190.0) -> dict:
    """Tempo and a beat grid from a percussive onset envelope: the strongest
    autocorrelation lag between lo and hi BPM (preferring the 75-180 octave),
    then the comb phase that collects the most onset energy, snapped to the
    nearest local peak. Returns {} when there is no periodicity."""
    env = np.asarray(onset_env, dtype=np.float64)
    if len(env) < 8 or env.std() < 1e-9:
        return {}
    env = env - env.mean()
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    if ac[0] <= 0:
        return {}
    ac = ac / ac[0]
    lo, hi = int(60 / hi_bpm / hop_s), int(60 / lo_bpm / hop_s)
    if hi >= len(ac) or hi <= lo:
        return {}
    lag = lo + int(np.argmax(ac[lo:hi]))
    strength = float(ac[lag])
    tempo = 60 / (lag * hop_s)
    if tempo < 75:
        tempo *= 2
        lag = max(1, lag // 2)
    raw = np.asarray(onset_env, dtype=np.float64)
    phase = int(np.argmax([raw[p::lag].sum() for p in range(lag)]))
    grid = np.arange(phase, len(raw), lag)
    snapped = []
    for g in grid:
        a, b = max(0, g - 3), min(len(raw), g + 4)
        snapped.append(a + int(np.argmax(raw[a:b])))
    beats = np.array(snapped) * hop_s
    return {"tempo_bpm": round(float(tempo), 1), "beat_strength": round(strength, 3),
            "beats": [round(float(b), 3) for b in beats]}


def on_grid_ratio(events: list[float], grid: list[float], tolerance: float = 0.08) -> dict:
    """How many event times fall within `tolerance` of a grid time, and the
    share that chance alone would put there (so a ratio only counts when it
    clearly beats chance)."""
    if not events or len(grid) < 2:
        return {"ratio": 0.0, "chance": 1.0, "locked": False}
    ev = np.asarray(events, dtype=np.float64)
    gr = np.sort(np.asarray(grid, dtype=np.float64))
    d = np.min(np.abs(ev[:, None] - gr[None, :]), axis=1)
    spacing = float(np.median(np.diff(gr)))
    chance = min(1.0, 2 * tolerance / max(spacing, 1e-3))
    ratio = float((d < tolerance).mean())
    return {"ratio": round(ratio, 3), "chance": round(chance, 3),
            "locked": bool(ratio > max(0.45, 1.5 * chance))}
