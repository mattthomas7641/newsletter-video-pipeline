"""Read-only views of what the pipeline has produced and the state around it.

Everything here tolerates missing, empty or half-written files: the pipeline
may be writing while the panel reads, and old days predate some files.
"""
from __future__ import annotations

import json
import plistlib
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

from . import security
from .config import LAUNCHD_LABEL, UIPaths
from .envstore import EnvStore

GOOD = ("ok", "done", "success", "succeeded", "complete", "completed", "uploaded", "rendered", "published")
BAD = ("fail", "error", "abort", "crash")
ACTIVE = ("run", "render", "voic", "writ", "script", "upload", "progress", "start", "work")


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _text(value, limit: int = 300) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and str(value).strip() else ""


def _tone(state: str) -> str:
    low = state.lower()
    if any(k in low for k in BAD):
        return "bad"
    if any(k in low for k in GOOD):
        return "good"
    if any(k in low for k in ACTIVE):
        return "active"
    return "neutral"


def normalize_status(raw) -> dict | None:
    """Pulls a state/stage/error out of whatever a status.json holds."""
    if not isinstance(raw, dict):
        return None
    state = ""
    for key in ("state", "status", "stage", "phase"):
        state = _text(raw.get(key), 60)
        if state:
            break
    error = ""
    for key in ("error", "error_message", "failure", "reason", "message"):
        error = _text(raw.get(key))
        if error and (key != "message" or _tone(state) == "bad"):
            break
        error = ""
    if not state and not error:
        return None
    return {"state": state or ("failed" if error else ""), "error": error, "tone": _tone(state) if state else "bad"}


def _status_for(folder: Path, mp4: Path, whole) -> dict | None:
    status = normalize_status(_json(mp4.with_suffix(".status.json")))
    if status or not isinstance(whole, dict):
        return status
    for container in (whole.get("videos"), whole.get("items"), whole):
        if isinstance(container, dict):
            for key in (mp4.name, mp4.stem):
                if isinstance(container.get(key), dict):
                    return normalize_status(container[key])
        elif isinstance(container, list):
            for row in container:
                if isinstance(row, dict) and {row.get("file"), row.get("name")} & {mp4.name, mp4.stem}:
                    return normalize_status(row)
    return None


def day_summary(paths: UIPaths, day: date) -> dict:
    folder = paths.output_dir / day.isoformat()
    uploads = _json(folder / "uploads.json") or {}
    whole = _json(folder / "status.json")
    videos, seen = [], set()
    stems = {p.stem for p in folder.glob("*.mp4")} | {
        p.stem for p in folder.glob("*.json") if p.name not in ("uploads.json", "status.json")
        and not p.name.endswith(".status.json")} if folder.is_dir() else set()
    for stem in sorted(stems):
        mp4 = folder / f"{stem}.mp4"
        meta = _json(folder / f"{stem}.json") or {}
        title = _text((meta.get("script") or {}).get("title") if isinstance(meta, dict) else "", 140) or \
            _text(meta.get("title") if isinstance(meta, dict) else "", 140) or stem.replace("_", " ").replace("-", " ")
        upload = uploads.get(mp4.name) if isinstance(uploads, dict) else None
        link = None
        if isinstance(upload, dict) and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(upload.get("id", ""))):
            link = {"id": upload["id"], "url": f"https://www.youtube.com/shorts/{upload['id']}",
                    "privacy": _text(upload.get("privacy"), 20)}
        has_file = mp4.is_file()
        status = _status_for(folder, mp4, whole)
        if status is None:
            if link:
                status = {"state": "Uploaded", "error": "", "tone": "good"}
            elif has_file:
                status = {"state": "Ready", "error": "", "tone": "neutral"}
            else:
                status = {"state": "Video file removed", "error": "", "tone": "neutral"}
        videos.append({
            "name": mp4.name, "title": title, "has_file": has_file,
            "url": f"/media/video/{day.isoformat()}/{mp4.name}" if has_file else None,
            "size": mp4.stat().st_size if has_file else 0,
            "youtube": link, "status": status,
        })
        seen.add(mp4.name)
    overall = normalize_status(whole) if isinstance(whole, dict) else None
    return {"date": day.isoformat(), "videos": videos, "status": overall}


def recent_days(paths: UIPaths, today: date, days: int = 14) -> list[dict]:
    out = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        if (paths.output_dir / day.isoformat()).is_dir():
            out.append(day_summary(paths, day))
    return out


def previews(paths: UIPaths, limit: int = 6) -> list[dict]:
    root = paths.output_dir / "_previews"
    if not root.is_dir():
        return []
    items = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        if not security.JOB_ID.match(folder.name):
            continue
        for mp4 in sorted(folder.glob("*.mp4")):
            meta = _json(mp4.with_suffix(".json")) or {}
            title = _text((meta.get("script") or {}).get("title"), 140) if isinstance(meta, dict) else ""
            items.append({"id": folder.name, "name": mp4.name, "title": title or mp4.stem,
                          "url": f"/media/preview/{folder.name}/{mp4.name}", "modified": mp4.stat().st_mtime,
                          "path": str(mp4)})
        if len(items) >= limit:
            break
    return items[:limit]


# --- scheduler & setup -------------------------------------------------------

def schedule(paths: UIPaths) -> dict:
    """When the daily job runs. Reads only the hour/minute out of the launchd plist."""
    info = {"configured": False, "time": None, "loaded": None, "label": LAUNCHD_LABEL}
    plist = paths.launchd_plist
    try:
        data = plistlib.loads(plist.read_bytes()) if plist and plist.is_file() else None
    except (OSError, plistlib.InvalidFileException, ValueError):
        data = None
    if isinstance(data, dict):
        interval = data.get("StartCalendarInterval")
        if isinstance(interval, list) and interval:
            interval = interval[0]
        if isinstance(interval, dict) and "Hour" in interval:
            info["configured"] = True
            info["time"] = f"{int(interval['Hour']):02d}:{int(interval.get('Minute', 0)):02d}"
        elif plist:
            info["configured"] = True
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=4, check=False).stdout
        info["loaded"] = any(line.split("\t")[-1:] == [LAUNCHD_LABEL] for line in out.splitlines())
    except (OSError, subprocess.SubprocessError):
        info["loaded"] = None
    return info


def setup_check(paths: UIPaths, store: EnvStore, voicebox_running: bool) -> list[dict]:
    keys = store.secret_presence()
    uses_voicebox = store.read_only().get("TTS_PROVIDER") == "voicebox" or \
        store.read()["values"]["CHARACTER"] != "" or store.has_value("VOICEBOX_PROFILE_ID")
    token = (paths.secrets_dir / "youtube_token.json").is_file()
    client = (paths.secrets_dir / "youtube_client_secret.json").is_file()
    clips = len(list(paths.background_dir.glob("*.mp4"))) if paths.background_dir.is_dir() else 0
    return [
        {"id": "writer", "label": "AI writer", "ok": keys["ANTHROPIC_API_KEY"],
         "detail": "API key found." if keys["ANTHROPIC_API_KEY"]
         else "No API key. Scripts come from the simpler rule-based writer."},
        {"id": "voicebox", "label": "Voicebox", "ok": voicebox_running,
         "optional": not uses_voicebox,
         "detail": "Running." if voicebox_running
         else ("Not running. It starts automatically when a voice is needed." if uses_voicebox
               else "Not running. Only needed for cloned voices.")},
        {"id": "youtube", "label": "YouTube", "ok": token,
         "detail": "Signed in." if token
         else ("Not signed in. Run: python -m newsletter_video.youtube_upload --auth" if client
               else "Not set up. See Uploading to YouTube Shorts in the README.")},
        {"id": "gameplay", "label": "Gameplay clips", "ok": clips > 0, "optional": True,
         "detail": f"{clips} clip{'s' if clips != 1 else ''} in assets/background." if clips
         else "None found. The gameplay background falls back to graph paper."},
        {"id": "notify", "label": "Failure alerts", "ok": keys["NOTIFY_WEBHOOK_URL"], "optional": True,
         "detail": "A notification address is set." if keys["NOTIFY_WEBHOOK_URL"] else "Not set up."},
    ]
