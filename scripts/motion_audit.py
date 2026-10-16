#!/usr/bin/env python
"""Motion audit: fails a rendered video that holds a visually static frame.

A Short that stops moving loses viewers (docs/explainer-research.md); the
explainer style promises something meaningful changes every ~1-2 s. This
script measures it instead of trusting the composition: frames are sampled
at --fps, the caption band (which always moves) is masked out, and any
stretch where consecutive frames differ by less than --threshold for longer
than --max-static seconds is a violation. It also prints the motion-per-
second curve so a reviewer can see where the quiet stretches are.

    ./venv/bin/python scripts/motion_audit.py video.mp4
    ./venv/bin/python scripts/motion_audit.py video.mp4 --fps 8 --max-static 1.2 --json

Exit code 1 on violations, 2 when the video cannot be read. Needs ffmpeg
(the bundled imageio-ffmpeg one is used) and numpy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_W, SAMPLE_H = 216, 384  # 1080x1920 scaled by 0.2: enough to see motion, cheap to diff
CAPTION_BAND = (1376 / 1920, 1512 / 1920)  # the lower-third caption, as fractions of the height
DEFAULT_FPS = 8
DEFAULT_MAX_STATIC = 1.2
DEFAULT_THRESHOLD = 0.06  # percent of pixels that must change (by more than PIXEL_DELTA) for a frame to count as "moved"
PIXEL_DELTA = 20  # per-pixel change (0-255) that counts as a change, above compression noise


@dataclass
class StaticStretch:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def frame_diffs(frames: np.ndarray, mask_rows: tuple[int, int] | None = None) -> np.ndarray:
    """Percentage of pixels that changed (by more than PIXEL_DELTA) between
    consecutive frames (grayscale), ignoring the rows in `mask_rows`. A
    percentage, not a mean difference, so a small element moving visibly
    counts and a slow full-frame drift under the eye's notice does not.
    frames: (n, h, w) uint8."""
    if len(frames) < 2:
        return np.zeros(0)
    f = frames.astype(np.int16)
    if mask_rows is not None:
        f = np.concatenate([f[:, : mask_rows[0]], f[:, mask_rows[1]:]], axis=1)
    changed = np.abs(f[1:] - f[:-1]) > PIXEL_DELTA
    return changed.mean(axis=(1, 2)) * 100


def static_stretches(diffs: np.ndarray, fps: float, threshold: float, max_static: float) -> list[StaticStretch]:
    """Runs of consecutive sub-threshold differences longer than max_static.
    A run of k sub-threshold diffs means k/fps seconds without change."""
    stretches: list[StaticStretch] = []
    run_start: int | None = None
    for i, d in enumerate(list(diffs) + [np.inf]):
        if d < threshold:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            length = (i - run_start) / fps
            if length > max_static:
                stretches.append(StaticStretch(round(run_start / fps, 2), round(i / fps, 2)))
            run_start = None
    return stretches


def motion_curve(diffs: np.ndarray, fps: float) -> list[float]:
    """Mean difference per second of video, for the report."""
    per_sec = int(round(fps))
    return [round(float(diffs[i:i + per_sec].mean()), 2) for i in range(0, len(diffs), per_sec)]


def audit_frames(frames: np.ndarray, fps: float, threshold: float = DEFAULT_THRESHOLD,
                 max_static: float = DEFAULT_MAX_STATIC, mask_caption: bool = True) -> dict:
    h = frames.shape[1]
    mask = (int(CAPTION_BAND[0] * h), int(CAPTION_BAND[1] * h)) if mask_caption else None
    diffs = frame_diffs(frames, mask)
    stretches = static_stretches(diffs, fps, threshold, max_static)
    return {
        "frames": int(len(frames)),
        "seconds": round(len(frames) / fps, 2),
        "static_stretches": [{"start": s.start, "end": s.end, "length": round(s.length, 2)} for s in stretches],
        "longest_static": round(max((s.length for s in stretches), default=0.0), 2),
        "mean_motion": round(float(diffs.mean()), 3) if len(diffs) else 0.0,  # % of pixels changing per sample
        "curve": motion_curve(diffs, fps),
        "ok": not stretches,
    }


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def read_frames(video: Path, fps: float) -> np.ndarray:
    """Grayscale frames at `fps`, scaled to SAMPLE_W x SAMPLE_H, as (n, h, w) uint8."""
    cmd = [_ffmpeg(), "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={SAMPLE_W}:{SAMPLE_H}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(out) // (SAMPLE_W * SAMPLE_H)
    return np.frombuffer(out[: n * SAMPLE_W * SAMPLE_H], dtype=np.uint8).reshape(n, SAMPLE_H, SAMPLE_W)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--max-static", type=float, default=DEFAULT_MAX_STATIC, help="seconds (default 1.2)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--no-mask", action="store_true", help="also count the caption band as motion")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        frames = read_frames(args.video, args.fps)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"cannot read {args.video}: {exc}", file=sys.stderr)
        return 2
    report = audit_frames(frames, args.fps, args.threshold, args.max_static, mask_caption=not args.no_mask)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{args.video.name}: {report['seconds']} s, mean motion {report['mean_motion']}, "
              f"longest static {report['longest_static']} s")
        bars = "".join("▁▂▃▄▅▆▇█"[min(7, int(v * 2))] for v in report["curve"])
        print(f"motion/s: {bars}")
        for s in report["static_stretches"]:
            print(f"  STATIC {s['start']:6.2f}-{s['end']:6.2f} s ({s['length']} s)")
        print("OK" if report["ok"] else f"FAIL: {len(report['static_stretches'])} static stretch(es) over {args.max_static} s")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
