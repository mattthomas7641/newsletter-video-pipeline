"""Per-run, per-video state for a day's output folder: `status.json`.

Written incrementally as the run goes (one record per video the moment it
is rendered, failed or uploaded), so a crash or a watchdog kill at video 7
leaves an accurate account of videos 1-6, and `python -m
newsletter_video.status` can show what happened. All writes are atomic
(temp file + rename) so a kill mid-write never leaves half a JSON file.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

STATUS_FILE = "status.json"
# Files in a day folder that are not a video's sidecar metadata.
NON_SIDECAR_JSON = {"uploads.json", STATUS_FILE}
_INDEX_RE = re.compile(r"^(\d{2,})_")


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    """A sidecar or record file that was only half-written when the process
    died is treated as absent rather than crashing every later run."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state(out_dir: Path) -> dict:
    state = read_json(out_dir / STATUS_FILE, default=None)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("run", {})
    state.setdefault("videos", {})
    return state


def update_run(out_dir: Path, **fields) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(out_dir)
    state["run"].update(fields)
    write_json_atomic(out_dir / STATUS_FILE, state)
    return state


def record_video(out_dir: Path, name: str, status: str, **info) -> dict:
    """`name` is the video's file stem (e.g. "03_some-title"); `status` is
    one of rendered / failed / uploaded / upload_failed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(out_dir)
    record = state["videos"].setdefault(name, {})
    record.update(info)
    record["status"] = status
    record["updated"] = now_iso()
    write_json_atomic(out_dir / STATUS_FILE, state)
    return state


def sidecars(out_dir: Path) -> list[Path]:
    """The metadata sidecars of videos that finished rendering."""
    if not out_dir.is_dir():
        return []
    return sorted(p for p in out_dir.glob("*.json") if p.name not in NON_SIDECAR_JSON and not p.name.startswith("."))


def next_index(out_dir: Path) -> int:
    """One past the highest NN_ prefix already used in `out_dir` by any
    file (video, sidecar, leftover partial), so a re-run can never collide
    with or overwrite an earlier video — even if some earlier attempts
    failed before producing an mp4."""
    highest = 0
    if out_dir.is_dir():
        for p in list(out_dir.iterdir()) + list((out_dir / ".partial").glob("*") if (out_dir / ".partial").is_dir() else []):
            m = _INDEX_RE.match(p.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1
