"""Motion audit on synthetic frames (no ffmpeg, no video)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from motion_audit import audit_frames, frame_diffs, static_stretches  # noqa: E402

FPS = 8
H, W = 384, 216


def frames_with(moving_until: float, seconds: float, caption_motion: bool = False) -> np.ndarray:
    """A square that moves one pixel per frame until `moving_until`, then
    holds; optionally the caption band keeps changing the whole time."""
    n = int(seconds * FPS)
    frames = np.zeros((n, H, W), dtype=np.uint8)
    for i in range(n):
        x = min(i, int(moving_until * FPS)) * 2
        frames[i, 100:160, 20 + x:80 + x] = 255
        if caption_motion:
            frames[i, 280:300, 10 + 4 * (i % 20):130 + 4 * (i % 20)] = 200
    return frames


def test_moving_video_passes():
    report = audit_frames(frames_with(10, 10), FPS)
    assert report["ok"] and report["static_stretches"] == []
    assert len(report["curve"]) == 10 and report["mean_motion"] > 0


def test_static_hold_is_reported_with_its_timing():
    report = audit_frames(frames_with(3, 8), FPS)
    assert not report["ok"]
    (stretch,) = report["static_stretches"]
    assert abs(stretch["start"] - 3.0) < 0.3 and stretch["end"] >= 7.8
    assert report["longest_static"] > 4


def test_caption_motion_does_not_hide_a_static_scene():
    masked = audit_frames(frames_with(3, 8, caption_motion=True), FPS)
    assert not masked["ok"]
    unmasked = audit_frames(frames_with(3, 8, caption_motion=True), FPS, mask_caption=False)
    assert unmasked["ok"]


def test_short_holds_under_the_limit_are_fine():
    frames = frames_with(10, 10)
    frames[40:48] = frames[40]  # a 1.0 s hold at 8 fps
    assert audit_frames(frames, FPS)["ok"]


def test_static_stretch_helper_counts_runs():
    diffs = np.array([5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 0, 0, 5], dtype=float)
    stretches = static_stretches(diffs, fps=8, threshold=0.06, max_static=1.0)
    assert len(stretches) == 1 and abs(stretches[0].length - 10 / 8) < 0.02
    assert frame_diffs(np.zeros((1, 4, 4), dtype=np.uint8)).size == 0
