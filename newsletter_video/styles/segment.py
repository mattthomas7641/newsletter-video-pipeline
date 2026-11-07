"""Scene segmentation and motion description of a motion-graphic video,
from frames alone (numpy; no OpenCV).

A reference is split into scenes at hard cuts and at soft transitions
(fades, dissolves, wipes, pushes), each transition is named, and every
scene gets a motion profile: how much changes when (the activity curve),
the bursts of change that are element entrances (with an easing shape, a
direction and a stagger interval when several arrive in a row), the share
of the frame that keeps moving through the hold (drifts, loops, pushes),
and a global drift estimate from phase correlation. Cut cadence statistics
summarise the whole video.

The per-frame cut evidence and the easing / sweep heuristics are adapted
from rea-video (third_party/rea_video/primitives.py, Apache-2.0).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from third_party.rea_video.primitives import (
    bhattacharyya,
    boundary_score,
    colour_histogram,
    easing_shape,
    local_median,
    sweep_direction,
)

TRACK_FPS = 12.0  # analysis frame rate: enough to see a 0.3 s transition, cheap to diff
TRACK_W = 96  # thumbnail width for cut detection
MOTION_W = 160  # width for motion events (cells of CELL px)
CELL = 4
PIXEL_DELTA = 18  # per-pixel change that counts (0-255), above compression noise
MIN_SCENE_S = 0.35
CUT_THRESHOLD = 0.30
SOFT_FAR = 0.42  # histogram distance across a 0.5 s window that reads as a change of scene
TURNOVER = 0.6  # share of the (non-chrome) ink replaced across a 0.8 s window that reads as a scene change
MAX_SCENES = 60


@dataclass
class Scene:
    index: int
    start: float
    end: float
    transition_in: str = "cut"  # cut | fade | dissolve | wipe-<dir> | push-<dir> | none (first scene)
    boundary_score: float = 0.0
    transition_duration: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"index": self.index, "start": round(self.start, 3), "end": round(self.end, 3),
                "duration": round(self.duration, 3), "transition_in": self.transition_in,
                "transition_duration": round(self.transition_duration, 2), "boundary_score": round(self.boundary_score, 3)}


@dataclass
class Track:
    fps: float
    gray: np.ndarray  # (n, h, w) uint8 thumbs
    hist_dist: np.ndarray
    pix_diff: np.ndarray
    changed: np.ndarray
    luma: np.ndarray
    hists: list[np.ndarray] = field(default_factory=list, repr=False)

    @property
    def n(self) -> int:
        return len(self.luma)

    def t(self, i: int) -> float:
        return i / self.fps


def build_track(frames: np.ndarray, fps: float) -> Track:
    """Per-frame cut evidence from RGB thumbnails (n, h, w, 3)."""
    n = len(frames)
    gray = (0.299 * frames[..., 0] + 0.587 * frames[..., 1] + 0.114 * frames[..., 2]).astype(np.uint8) if frames.ndim == 4 else frames
    hists = [colour_histogram(f) for f in frames] if frames.ndim == 4 else [np.ones(1)] * n
    hist_d = np.zeros(n)
    pix_d = np.zeros(n)
    chg = np.zeros(n)
    g = gray.astype(np.int16)
    for i in range(1, n):
        d = np.abs(g[i] - g[i - 1])
        hist_d[i] = bhattacharyya(hists[i - 1], hists[i]) if frames.ndim == 4 else 0.0
        pix_d[i] = float(d.mean() / 255.0)
        thr = max(6.0, 0.35 * float(min(gray[i].std(), gray[i - 1].std())))
        chg[i] = float((d > thr).mean())
    return Track(fps=fps, gray=gray, hist_dist=hist_d, pix_diff=pix_d, changed=chg,
                 luma=gray.reshape(n, -1).mean(axis=1) if n else np.zeros(0), hists=hists)


# --- transitions -------------------------------------------------------------------

def phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Global translation (dx, dy) in pixels from a to b by phase correlation,
    and the peak response (0-1, how well a pure shift explains the change)."""
    fa = np.fft.fft2(a.astype(np.float32) - a.mean())
    fb = np.fft.fft2(b.astype(np.float32) - b.mean())
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-6
    corr = np.real(np.fft.ifft2(cross))
    h, w = corr.shape
    idx = int(np.argmax(corr))
    y, x = divmod(idx, w)
    if y > h // 2:
        y -= h
    if x > w // 2:
        x -= w
    peak = float(corr.max()) / max(1e-6, float(np.abs(corr).sum()) / (h * w)) / (h * w) * 100
    return -float(x), -float(y), min(1.0, peak)


def _blend_residual(a: np.ndarray, m: np.ndarray, b: np.ndarray) -> float:
    """How well the middle frame is a linear blend of the outer two (a
    dissolve): the residual of the best alpha, relative to the change."""
    A, M, B = (x.astype(np.float32) for x in (a, m, b))
    D = B - A
    denom = float((D * D).sum())
    if denom <= 0:
        return 1.0
    alpha = float(((M - A) * D).sum() / denom)
    if not 0.15 <= alpha <= 0.85:
        return 1.0
    return float(np.abs(M - (A + alpha * D)).mean() / (np.abs(D).mean() + 1e-6))


def _is_blank(gray: np.ndarray) -> bool:
    """A fade frame: near-uniform and at a luma extreme."""
    return float(gray.std()) < 4.0 and (float(gray.mean()) < 20 or float(gray.mean()) > 250)


def classify_transition(track: Track, i: int, span: int) -> tuple[str, float]:
    """Name the transition whose change is centred on frame i and spread over
    about `span` frames: fade (through a flat frame), dissolve (frames are
    blends of the shots), push (the picture translates), wipe (a change
    front sweeps across a still picture) or cut."""
    n = track.n
    if span <= 1:
        return "cut", 1.0 / track.fps
    lo, hi = max(0, i - span), min(n - 1, i + span)
    window = range(lo, hi + 1)
    # fade: a near-uniform black or white frame inside the window (an empty
    # paper frame mid-wipe is not a fade)
    if any(_is_blank(track.gray[k]) for k in window):
        return "fade", span * 2 / track.fps
    # dissolve: the middle frame is a blend of the ends
    if _blend_residual(track.gray[lo], track.gray[i], track.gray[hi]) < 0.25:
        return "dissolve", span * 2 / track.fps
    # wipe: the onset of change sweeps across the frame in one direction
    # (checked before push: in a push every cell starts changing at once)
    g = track.gray[lo:hi + 1].astype(np.int16)
    d = np.abs(np.diff(g, axis=0)) > PIXEL_DELTA
    if len(d):
        ever = d.any(axis=0)
        if ever.mean() > 0.04:
            onset = np.where(ever, d.argmax(axis=0), -1)
            ys, xs = np.nonzero(ever)
            direction = sweep_direction(onset[ever], ys, xs)
            if direction:
                return "wipe-" + direction, (hi - lo) / track.fps
    # push: consistent global translation across the active frames (on a
    # 96 px thumb a 0.5 s push moves 1-2 px per frame, so the test is on the
    # sign agreement and the summed travel, not on one large shift)
    shifts = []
    for k in range(max(lo + 1, 1), hi + 1):
        if track.changed[k] < 0.02:
            continue
        dx, dy, peak = phase_shift(track.gray[k - 1], track.gray[k])
        if peak > 0.08 and max(abs(dx), abs(dy)) >= 1:
            shifts.append((dx, dy))
    if len(shifts) >= 3:
        sx = np.array([s[0] for s in shifts])
        sy = np.array([s[1] for s in shifts])
        if np.abs(sy).sum() >= np.abs(sx).sum() and abs(np.sign(sy).sum()) == len(sy) and np.abs(sy).sum() >= 3:
            return ("push-up" if sy[0] < 0 else "push-down"), (hi - lo) / track.fps
        if np.abs(sx).sum() > np.abs(sy).sum() and abs(np.sign(sx).sum()) == len(sx) and np.abs(sx).sum() >= 3:
            return ("push-left" if sx[0] < 0 else "push-right"), (hi - lo) / track.fps
    return "dissolve", (hi - lo) / track.fps


def ink_masks(gray: np.ndarray, delta: int = 24) -> np.ndarray:
    """Per-frame content masks: pixels that differ from the frame's border
    colour (the ground). Motion graphics change their ink, not their
    ground, so scene changes show as the ink layout being replaced."""
    n = len(gray)
    if n == 0:
        return np.zeros((0, 1, 1), bool)
    border = np.concatenate([gray[:, 0], gray[:, -1], gray[:, :, 0], gray[:, :, -1]], axis=1)  # (n, k)
    bg = np.median(border, axis=1)[:, None, None]
    return np.abs(gray.astype(np.int16) - bg) > delta


def layout_turnover(masks: np.ndarray, k: int) -> np.ndarray:
    """How much of the ink on screen k frames before each frame is gone k
    frames after it, and how much of the ink after is new: the smaller of
    the two. Near 1 when the layout was replaced (a scene change); low when
    elements were added to what was there (an entrance keeps the old ink)
    or when only something left. Persistent chrome (a kicker, a progress
    rule) is a small share of the ink, so it barely lowers the value."""
    n = len(masks)
    out = np.zeros(n)
    for i in range(k, n - k):
        a, b = masks[i - k], masks[i + k]
        na, nb = a.sum(), b.sum()
        # nothing before: an entrance onto an empty ground, not a scene change
        if na < 0.004 * a.size:
            out[i] = 0.0
            continue
        both = (a & b).sum()
        gone = 1.0 - both / na
        new = 1.0 - both / nb if nb >= 0.004 * b.size else gone  # left to an empty ground: counts as gone
        out[i] = min(gone, new)
    return out


def _spread(track: Track, i: int, peak: float) -> int:
    """How many frames on either side of i the change keeps going (a 0.5 s
    push reads as a run of elevated change; a hard cut is one frame)."""
    thr = max(0.025, 0.3 * peak)
    span = back = 0
    for k in range(1, int(track.fps * 0.7) + 1):
        if i + k < track.n and track.changed[i + k] > thr:
            span = k
        else:
            break
    for k in range(1, int(track.fps * 0.7) + 1):
        if i - k > 0 and track.changed[i - k] > thr:
            back = k
        else:
            break
    return max(span, back)


def detect_scenes(track: Track, min_scene_s: float = MIN_SCENE_S, duration: float | None = None) -> list[Scene]:
    """Scene boundaries from two kinds of evidence: rea-video's hard-cut rule
    (large colour / pixel jumps, for footage-like references) and a
    motion-graphics rule (the ink layout turns over across a ~0.8 s window
    while change in that window clearly exceeds the local baseline)."""
    n = track.n
    fps = track.fps
    if n == 0:
        return []
    total = duration if duration is not None else n / fps
    score, is_cut = boundary_score(track.hist_dist, track.pix_diff, track.changed, fps, CUT_THRESHOLD)
    boundaries: dict[int, tuple[str, float, float]] = {}  # frame -> (transition, score, duration)
    min_gap = max(1, int(round(min_scene_s * fps)))
    blank = [_is_blank(g) for g in track.gray]
    for i in np.nonzero(is_cut)[0]:
        i = int(i)
        if boundaries and i - max(boundaries) < min_gap:
            continue
        if blank[i]:
            continue  # the cut into a black / white frame; the scene starts where the fade comes back
        if i > 0 and blank[i - 1]:
            boundaries[i] = ("fade", float(score[i]), sum(1 for k in range(max(0, i - int(fps * 2.5)), i) if blank[k]) / fps)
            continue
        spread = _spread(track, i, float(track.changed[i]))
        kind, dur = ("cut", 1 / fps) if spread < 2 else classify_transition(track, i, spread)
        boundaries[i] = (kind, float(score[i]), dur)
    # motion-graphics boundaries: the ink layout is replaced
    K = max(2, int(round(0.4 * fps)))
    masks = ink_masks(track.gray)
    # chrome that stays through the whole video (a kicker, a progress rule,
    # a caption band's frame) is not part of any scene's layout
    persistent = masks.mean(axis=0) > 0.5
    masks = masks & ~persistent[None]
    turnover = layout_turnover(masks, K)
    base = local_median(track.changed, int(fps * 1.5))
    far = np.zeros(n)
    for i in range(K, n - K):
        far[i] = bhattacharyya(track.hists[i - K], track.hists[i + K]) if len(track.hists[i]) > 1 else 0.0
    evidence = np.zeros(n)
    ink_share = masks.reshape(n, -1).mean(axis=1)
    for i in range(K, n - K):
        window = track.changed[i - K + 1:i + K + 1]
        peak = float(window.max())
        if peak < 0.035 or peak < base[i] * 3 + 0.02:
            continue
        if ink_share[i - K] < 0.004:  # an entrance onto an empty ground, not a scene change
            continue
        if turnover[i] > TURNOVER or far[i] > SOFT_FAR:
            evidence[i] = max(turnover[i], far[i])
    # local maxima of the evidence, one per min_gap window, placed where the
    # change inside the window begins (the incoming scene's first frame)
    cand = [i for i in range(K, n - K) if evidence[i] > 0 and evidence[i] == evidence[max(0, i - K):i + K + 1].max()]
    cand.sort(key=lambda i: -evidence[i])
    for i in cand:
        lo, hi = max(1, i - K + 1), min(n, i + K + 1)
        peak_i = lo + int(np.argmax(track.changed[lo:hi]))
        peak = float(track.changed[peak_i])
        while peak_i - 1 > lo and track.changed[peak_i - 1] > max(0.02, 0.25 * peak):
            peak_i -= 1
        if any(abs(peak_i - b) < max(min_gap, 2 * K) for b in boundaries):
            continue
        spread = _spread(track, peak_i, float(track.changed[peak_i]))
        # a hard cut shows as a single frame of change with nothing either side;
        # anything softer is classified over the turnover window
        if spread < 2 and track.changed[peak_i] > 4 * max(track.changed[peak_i - 1], track.changed[min(n - 1, peak_i + 1)], 0.01):
            kind, dur = "cut", 1 / fps
        else:
            kind, dur = classify_transition(track, peak_i, max(spread, K))
        boundaries[peak_i] = (kind, float(evidence[i]), dur)
    starts = sorted(boundaries)
    scenes: list[Scene] = []
    prev = 0
    prev_kind, prev_score, prev_dur = "none", 0.0, 0.0
    for b in starts + [n]:
        if b - prev < min_gap and scenes:
            continue
        start = track.t(prev)
        end = track.t(b) if b < n else total
        if end - start <= 0:
            continue
        scenes.append(Scene(index=len(scenes), start=start, end=end, transition_in=prev_kind,
                            boundary_score=prev_score, transition_duration=prev_dur))
        if b < n:
            prev_kind, prev_score, prev_dur = boundaries[b]
        prev = b
    if len(scenes) > MAX_SCENES:
        # keep the strongest boundaries only
        keep = sorted(sorted(scenes[1:], key=lambda s: -s.boundary_score)[:MAX_SCENES - 1], key=lambda s: s.start)
        merged = [Scene(0, scenes[0].start, keep[0].start if keep else total, "none")]
        for j, s in enumerate(keep):
            merged.append(Scene(j + 1, s.start, keep[j + 1].start if j + 1 < len(keep) else total, s.transition_in,
                                s.boundary_score, s.transition_duration))
        scenes = merged
    return scenes


# --- motion inside a scene -----------------------------------------------------------

def activity_curve(gray: np.ndarray) -> np.ndarray:
    """Fraction of pixels changing by more than PIXEL_DELTA per frame step."""
    if len(gray) < 2:
        return np.zeros(0)
    g = gray.astype(np.int16)
    return (np.abs(g[1:] - g[:-1]) > PIXEL_DELTA).mean(axis=(1, 2))


def _bursts(act: np.ndarray, fps: float) -> list[tuple[int, int]]:
    """Runs of elevated activity (element entrances / exits) as (start, end)
    frame-step indices; a run ends after two quiet steps."""
    if not len(act):
        return []
    thr = max(0.004, float(np.percentile(act, 40)) * 1.5)
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(act)
    while i < n:
        if act[i] > thr:
            j = i
            quiet = 0
            while j < n and quiet < 2:
                quiet = quiet + 1 if act[j] <= thr else 0
                j += 1
            runs.append((i, max(i + 1, j - quiet)))
            i = j
        else:
            i += 1
    return runs


def motion_profile(frames: np.ndarray, fps: float, t0: float = 0.0) -> dict:
    """Describe what moves in one scene (RGB frames (n, h, w, 3) at `fps`,
    MOTION_W wide): the activity curve, entrance bursts with easing and
    direction, stagger, the continuously moving share and the drift."""
    n = len(frames)
    if n < 3:
        return {"activity": [], "events": [], "stagger": None, "continuous_share": 0.0, "drift": {"dx": 0.0, "dy": 0.0},
                "loop_period": None, "mean_activity": 0.0, "still_share": 1.0}
    gray = (0.299 * frames[..., 0] + 0.587 * frames[..., 1] + 0.114 * frames[..., 2]).astype(np.uint8)
    act = activity_curve(gray)
    h, w = gray.shape[1:]
    gh, gw = h // CELL, w // CELL
    g = gray[:, : gh * CELL, : gw * CELL].astype(np.int16)
    d = np.abs(np.diff(g, axis=0)) > PIXEL_DELTA  # (n-1, H, W)
    cells = d.reshape(n - 1, gh, CELL, gw, CELL).mean(axis=(2, 4)) > 0.12  # (n-1, gh, gw)
    count = cells.sum(axis=0)
    cont = count > 0.7 * (n - 1)
    continuous_share = float(cont.mean())
    events = []
    for a, b in _bursts(act, fps):
        region = cells[a:b].any(axis=0) & ~cont
        area = float(region.mean())
        if area < 0.002:
            continue
        ys, xs = np.nonzero(region)
        onset = cells[a:b].argmax(axis=0)[region]
        prof = cells[a:b][:, region].sum(axis=1).astype(float)
        before = gray[a][ys.min() * CELL:(ys.max() + 1) * CELL, xs.min() * CELL:(xs.max() + 1) * CELL]
        after = gray[min(n - 1, b + 1)][ys.min() * CELL:(ys.max() + 1) * CELL, xs.min() * CELL:(xs.max() + 1) * CELL]
        # ink (pixels away from the frame's ground) before and after the burst
        ground = float(np.median(np.concatenate([gray[a][0], gray[a][-1], gray[a][:, 0], gray[a][:, -1]])))
        ink_b = float((np.abs(before.astype(np.int16) - ground) > PIXEL_DELTA).mean())
        ink_a = float((np.abs(after.astype(np.int16) - ground) > PIXEL_DELTA).mean())
        if ink_a > ink_b + 0.1 and ink_a > 1.6 * ink_b:
            kind = "appear"
        elif ink_b > ink_a + 0.1 and ink_b > 1.6 * ink_a:
            kind = "disappear"
        else:
            kind = "change"
        sweep = sweep_direction(onset.astype(float), ys.astype(float), xs.astype(float))
        # slide: the centre of the active cells travels during the burst
        slide = None
        centres = [(float(np.nonzero(c)[0].mean()) / gh, float(np.nonzero(c)[1].mean()) / gw) for c in cells[a:b] if c.any()]
        if not sweep and len(centres) >= 3:
            dy, dx = centres[-1][0] - centres[0][0], centres[-1][1] - centres[0][1]
            if max(abs(dy), abs(dx)) > 0.03:
                slide = ("down" if dy > 0 else "up") if abs(dy) > abs(dx) else ("right" if dx > 0 else "left")
        events.append({
            "t0": round(t0 + (a + 1) / fps, 2), "t1": round(t0 + (b + 1) / fps, 2),
            "box": [round(float(xs.min()) / gw, 3), round(float(ys.min()) / gh, 3), round(float(xs.max() + 1 - xs.min()) / gw, 3),
                    round(float(ys.max() + 1 - ys.min()) / gh, 3)],
            "area": round(area, 3), "kind": kind, "easing": easing_shape(prof), "sweep": sweep, "slide": slide,
        })
    events = events[:24]
    # stagger: three or more entrances at a steady interval
    stagger = None
    appears = [e["t0"] for e in events if e["kind"] != "disappear"]
    if len(appears) >= 3:
        gaps = np.diff(appears)
        if gaps.min() > 0 and gaps.std() < 0.45 * gaps.mean():
            stagger = {"n": len(appears), "interval": round(float(gaps.mean()), 2)}
    # global drift (slow push / pan): median phase shift over the quiet frames
    shifts = []
    step = max(1, int(fps))
    for k in range(step, n, step):
        dx, dy, peak = phase_shift(gray[k - step], gray[k])
        if peak > 0.05:
            shifts.append((dx, dy))
    drift = {"dx": round(float(np.median([s[0] for s in shifts])), 2) if shifts else 0.0,
             "dy": round(float(np.median([s[1] for s in shifts])), 2) if shifts else 0.0}
    # loop: periodic activity through the hold
    loop_period = None
    if len(act) > 3 * fps:
        z = act - act.mean()
        ac = np.correlate(z, z, "full")[len(z) - 1:]
        if ac[0] > 0:
            ac = ac / ac[0]
            lo, hi = int(0.4 * fps), min(len(ac) - 1, int(3.0 * fps))
            if hi > lo:
                lag = lo + int(np.argmax(ac[lo:hi]))
                if ac[lag] > 0.5:
                    loop_period = round(lag / fps, 2)
    quiet = float((act < 0.003).mean()) if len(act) else 1.0
    return {
        "activity": [round(float(v), 4) for v in act],
        "events": events,
        "stagger": stagger,
        "continuous_share": round(continuous_share, 3),
        "drift": drift,
        "loop_period": loop_period,
        "mean_activity": round(float(act.mean()), 4) if len(act) else 0.0,
        "still_share": round(quiet, 3),
    }


def cadence_stats(scenes: list[Scene], duration: float) -> dict:
    lengths = [s.duration for s in scenes]
    if not lengths:
        return {"scene_count": 0}
    cuts = [s.start for s in scenes[1:]]
    window = 5.0
    per_window = []
    t = 0.0
    while t < duration:
        per_window.append(sum(1 for c in cuts if t <= c < t + window))
        t += window
    kinds: dict[str, int] = {}
    for s in scenes[1:]:
        base = s.transition_in.split("-")[0]
        kinds[base] = kinds.get(base, 0) + 1
    return {
        "scene_count": len(scenes),
        "mean_scene_s": round(float(np.mean(lengths)), 2),
        "median_scene_s": round(float(np.median(lengths)), 2),
        "min_scene_s": round(float(np.min(lengths)), 2),
        "max_scene_s": round(float(np.max(lengths)), 2),
        "cuts_per_5s": per_window,
        "transition_mix": kinds,
        "pace": "fast" if np.median(lengths) < 1.6 else ("steady" if np.median(lengths) < 3.5 else "slow"),
    }
