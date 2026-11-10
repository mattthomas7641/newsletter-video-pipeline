"""Fidelity of a recreation against its reference, from two analyses of
the same shape (analysis.analyze_video on each): cut-time alignment,
motion-curve correlation, transition kinds, palette distance and layout
similarity on matched keyframes, combined into one score, plus a
side-by-side contact sheet for a human look."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .frames import layout_similarity, palette_similarity

CUT_TOLERANCE_S = 0.35
WEIGHTS = {"cuts": 0.3, "motion": 0.25, "transitions": 0.1, "palette": 0.15, "layout": 0.2}


def cut_alignment(ref_cuts: list[float], rec_cuts: list[float], tolerance: float = CUT_TOLERANCE_S) -> dict:
    """Precision / recall / F1 of recreated cut times against the reference's,
    with the mean offset of the matched pairs."""
    if not ref_cuts and not rec_cuts:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "mean_offset": 0.0, "matched": 0}
    if not ref_cuts or not rec_cuts:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "mean_offset": None, "matched": 0}
    used: set[int] = set()
    offsets = []
    for r in ref_cuts:
        best, best_d = None, tolerance
        for j, c in enumerate(rec_cuts):
            if j in used:
                continue
            d = abs(c - r)
            if d <= best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
            offsets.append(best_d)
    matched = len(offsets)
    precision = matched / len(rec_cuts)
    recall = matched / len(ref_cuts)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "mean_offset": round(float(np.mean(offsets)), 3) if offsets else None, "matched": matched}


def resample(curve: list[float], n: int) -> np.ndarray:
    c = np.asarray(curve, dtype=np.float64)
    if len(c) == 0:
        return np.zeros(n)
    if len(c) == 1:
        return np.full(n, c[0])
    x = np.linspace(0, 1, len(c))
    return np.interp(np.linspace(0, 1, n), x, c)


def motion_correlation(ref_curve: list[float], rec_curve: list[float], n: int = 240) -> float:
    """Pearson correlation of the two activity curves on a normalised time
    axis (both smoothed a little), mapped to 0..1."""
    a, b = resample(ref_curve, n), resample(rec_curve, n)
    kernel = np.ones(5) / 5
    a, b = np.convolve(a, kernel, "same"), np.convolve(b, kernel, "same")
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 1.0 if abs(a.mean() - b.mean()) < 1e-6 else 0.0
    r = float(np.corrcoef(a, b)[0, 1])
    return round((r + 1) / 2, 3)


def _base(kind: str) -> str:
    return (kind or "cut").split("-")[0]


def transition_agreement(ref_scenes: list[dict], rec_scenes: list[dict], tolerance: float = CUT_TOLERANCE_S) -> dict:
    """Of the reference transitions that have a recreated boundary near
    them, the share with the same base kind (cut / fade / dissolve / push / wipe)."""
    pairs = []
    for r in ref_scenes[1:]:
        near = [s for s in rec_scenes[1:] if abs(s["start"] - r["start"]) <= tolerance]
        if near:
            pairs.append((_base(r["transition_in"]), _base(near[0]["transition_in"])))
    if not pairs:
        return {"agreement": 0.0, "pairs": 0}
    same = sum(1 for a, b in pairs if a == b or {a, b} <= {"fade", "dissolve"})
    return {"agreement": round(same / len(pairs), 3), "pairs": len(pairs)}


def matched_layouts(ref: dict, rec: dict) -> list[float]:
    """Layout similarity of each reference scene's mid keyframe against the
    recreation's keyframe at the same moment."""
    sims = []
    rec_scenes = rec.get("scenes", [])
    for r in ref.get("scenes", []):
        mid = (r["start"] + r["end"]) / 2
        target = next((s for s in rec_scenes if s["start"] <= mid < s["end"]), None)
        if target is None or not r.get("layout") or not target.get("layout"):
            continue
        sims.append(layout_similarity(r["layout"]["grid"], target["layout"]["grid"]))
    return sims


def compare_analyses(ref: dict, rec: dict) -> dict:
    ref_scenes, rec_scenes = ref.get("scenes", []), rec.get("scenes", [])
    cuts = cut_alignment([s["start"] for s in ref_scenes[1:]], [s["start"] for s in rec_scenes[1:]])
    motion = motion_correlation(ref.get("activity", []), rec.get("activity", []))
    trans = transition_agreement(ref_scenes, rec_scenes)
    pal = palette_similarity([c["hex"] for c in ref.get("palette", {}).get("colours", [])],
                             [c["hex"] for c in rec.get("palette", {}).get("colours", [])])
    lay = matched_layouts(ref, rec)
    layout = round(float(np.mean(lay)), 3) if lay else 0.0
    score = (WEIGHTS["cuts"] * cuts["f1"] + WEIGHTS["motion"] * motion + WEIGHTS["transitions"] * trans["agreement"]
             + WEIGHTS["palette"] * pal + WEIGHTS["layout"] * layout)
    return {
        "score": round(float(score), 3),
        "cuts": cuts,
        "motion_correlation": motion,
        "transitions": trans,
        "palette_similarity": pal,
        "layout_similarity": layout,
        "layout_per_scene": [round(v, 3) for v in lay],
        "ref_scene_count": len(ref_scenes),
        "rec_scene_count": len(rec_scenes),
        "duration_delta": round(float(rec.get("info", {}).get("duration", 0)) - float(ref.get("info", {}).get("duration", 0)), 2),
    }


def side_by_side(ref_frames: list[Path], rec_frames: list[Path], out: Path, labels: list[str] | None = None,
                 cell_w: int = 240) -> Path:
    """Rows of reference keyframe | recreation keyframe for a human check."""
    from PIL import Image, ImageDraw

    rows = max(len(ref_frames), len(rec_frames))
    cells = []
    for i in range(rows):
        pair = []
        for frames in (ref_frames, rec_frames):
            if i < len(frames) and frames[i].exists():
                im = Image.open(frames[i]).convert("RGB")
                im = im.resize((cell_w, max(1, int(im.height * cell_w / im.width))))
            else:
                im = Image.new("RGB", (cell_w, int(cell_w * 16 / 9)), (40, 40, 40))
            pair.append(im)
        cells.append(pair)
    row_h = max(max(a.height, b.height) for a, b in cells) if cells else 10
    sheet = Image.new("RGB", (cell_w * 2 + 12, (row_h + 22) * max(rows, 1)), (245, 245, 243))
    d = ImageDraw.Draw(sheet)
    for i, (a, b) in enumerate(cells):
        y = i * (row_h + 22)
        sheet.paste(a, (0, y + 20))
        sheet.paste(b, (cell_w + 12, y + 20))
        label = labels[i] if labels and i < len(labels) else f"scene {i + 1}"
        d.text((4, y + 4), f"ref {label}", fill=(30, 30, 30))
        d.text((cell_w + 16, y + 4), f"recreation {label}", fill=(30, 30, 30))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out
