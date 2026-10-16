"""Prints a day's run state from its per-video records.

    python -m newsletter_video.status               # today
    python -m newsletter_video.status 2026-10-14    # a specific day
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from .runstate import load_state, read_json, sidecars

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


def report(day: date) -> str:
    folder = OUTPUT_DIR / day.isoformat()
    if not folder.is_dir():
        return f"{day}: no output folder (the run never started, or started before status tracking)"
    state = load_state(folder)
    run = state["run"]
    uploads = read_json(folder / "uploads.json", default={}) or {}
    lines = [f"{day}: started {run.get('started', '?')}, finished {run.get('finished') or 'no'}"]
    if run.get("writer"):
        lines.append(f"  writer: {run['writer']}" + (" (rule-based fallback)" if run["writer"] == "heuristic" else ""))
    if run.get("error"):
        lines.append(f"  error: {run['error']}")
    names = sorted(set(state["videos"]) | {p.stem for p in sidecars(folder)})
    for name in names:
        info = state["videos"].get(name, {})
        status = info.get("status", "rendered")
        upload = uploads.get(f"{name}.mp4") or {}
        if upload.get("id"):
            status = f"uploaded https://youtube.com/shorts/{upload['id']} ({upload.get('privacy', '?')})"
        elif name in uploads or f"{name}.mp4" in uploads:
            status += " (upload attempted, result unknown — verified on next run)"
        line = f"  {name}: {status}"
        if info.get("error"):
            line += f" — {info['error'][:160]}"
        lines.append(line)
    if not names:
        lines.append("  no videos recorded")
    if run.get("pending_uploads"):
        lines.append(f"  {run['pending_uploads']} video(s) across all days still awaiting upload")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()))
