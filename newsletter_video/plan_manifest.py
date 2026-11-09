"""The plan manifest: `plan.json` in an output folder, holding a day's
planned videos (story, script, scene plan and per-video look) before any of
them is voiced or rendered.

    output/<date>/plan.json              a real day, planned from the panel
    output/_previews/<id>/plan.json      a throwaway test plan

Previews (newsletter_video.preview) and the final render
(newsletter_video.render_plan) both read scripts back from here, so a plan
is written once (one paid writer call) and every preview, look change and
re-render after it is free. `plan.json` is never a sidecar: runstate,
run_pipeline and the panel's history all ignore it (NON_SIDECAR_JSON), so
planning never counts as covering a story and never changes what the
unattended daily run does.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from . import look as looks
from .runstate import now_iso, read_json, write_json_atomic
from .scraper import Story
from .script_writer import Beat, Script

MANIFEST_NAME = "plan.json"
MANIFEST_VERSION = 1
STATUSES = ("planned", "rendered", "failed")
_VID = re.compile(r"^v\d{2,3}$")


class ManifestError(ValueError):
    pass


def manifest_path(folder: Path) -> Path:
    return Path(folder) / MANIFEST_NAME


@contextmanager
def locked(folder: Path):
    """An exclusive flock on plan.json.lock around a read-modify-write, so
    the panel saving a look and the renderer marking a video rendered can
    never lose each other's update."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (MANIFEST_NAME + ".lock")).open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fingerprint(script: dict | None, scene_plan: dict | None, look: dict | None) -> str:
    """What a preview depends on: the script, the scene plan and the look.
    A preview whose fingerprint no longer matches its video is stale."""
    payload = {"script": script or {}, "scene_plan": scene_plan or {},
               "look": {k: v for k, v in (look or {}).items() if k != "version" and v is not None}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def entry_fingerprint(entry: dict) -> str:
    return fingerprint(entry.get("script"), entry.get("scene_plan"), entry.get("look"))


def sidecar_fingerprint(meta: dict) -> str:
    return fingerprint(meta.get("script"), meta.get("scene_plan"), _overrides_from_sidecar_look(meta.get("look")))


def preview_dir(folder: Path, vid: str) -> Path:
    return Path(folder) / "_preview" / vid


def drop_previews(folder: Path, vid: str | None = None) -> None:
    """Previews of one video (or of every video) are deleted when what
    they showed no longer exists: a rewritten script, a new plan."""
    target = preview_dir(folder, vid) if vid else Path(folder) / "_preview"
    shutil.rmtree(target, ignore_errors=True)


def story_dict(story: Story) -> dict:
    return {"title": story.title, "url": story.url, "edition": story.edition, "section": story.section,
            "summary": story.summary}


def story_from_dict(d: Any) -> Story:
    if isinstance(d, str):
        return Story(edition="", section="", title=d, summary="", url="")
    d = d or {}
    return Story(edition=str(d.get("edition", "")), section=str(d.get("section", "")), title=str(d.get("title", "")),
                 summary=str(d.get("summary", "")), url=str(d.get("url", "")))


def entry_from_script(script: Script, index: int, look_overrides: dict | None = None) -> dict:
    """A manifest entry for one written video."""
    entry = {
        "id": f"v{index:02d}", "index": index, "title": script.title, "badge": script.badge,
        "stories": [story_dict(s) for s in script.stories],
        "script": {"title": script.title, "badge": script.badge, "hook": script.hook,
                   "beats": [asdict(b) for b in script.beats], "outro": script.outro},
        "post_caption": script.post_caption, "hashtags": list(script.hashtags), "writer": script.writer or "",
        "scene_plan": None, "look": looks.compact(looks.validate_look(look_overrides or {})),
        "status": "planned", "rendered": None, "error": None, "updated": now_iso(),
    }
    plan = getattr(script, "plan", None)
    if plan is not None:
        from .explainer_writer import plan_dict

        entry["scene_plan"] = plan_dict(plan)
    return entry


def script_from_entry(entry: dict) -> Script:
    """The Script (or ExplainerScript) an entry describes, ready to render.
    The scene plan is validated again on the way out, so a hand-edited
    manifest can never reach the composition broken."""
    stories = [story_from_dict(s) for s in entry.get("stories", [])]
    raw = entry.get("script") or {}
    plan_raw = entry.get("scene_plan")
    if plan_raw and plan_raw.get("scenes"):
        from .explainer_writer import script_from_plan
        from .scene_plan import validate_plan

        plan = validate_plan(plan_raw["scenes"])
        script = script_from_plan(plan, str(entry.get("title") or raw.get("title") or "Untitled"),
                                  str(entry.get("badge") or raw.get("badge") or "NEWS"), stories,
                                  str(entry.get("post_caption", "")), list(entry.get("hashtags", [])),
                                  writer=str(entry.get("writer") or "claude"))
    else:
        script = Script(title=str(entry.get("title") or raw.get("title") or "Untitled"),
                        badge=str(entry.get("badge") or raw.get("badge") or "NEWS"), hook=str(raw.get("hook", "")),
                        beats=[Beat(**{k: b.get(k) for k in ("say", "card", "highlight", "kind", "stat") if k in b})
                               for b in raw.get("beats", [])],
                        outro=str(raw.get("outro", "")), stories=stories,
                        post_caption=str(entry.get("post_caption", "")), hashtags=list(entry.get("hashtags", [])),
                        writer=str(entry.get("writer") or ""))
    script.look = looks.validate_look(entry.get("look") or {})
    return script


def script_from_sidecar(meta: dict) -> Script:
    """A rendered video's sidecar (<video>.json) as a Script, for previews
    and re-renders of videos the pipeline already made."""
    entry = {
        "title": (meta.get("script") or {}).get("title") or meta.get("title", ""),
        "badge": (meta.get("script") or {}).get("badge", "NEWS"),
        "stories": meta.get("stories", []), "script": meta.get("script") or {},
        "post_caption": meta.get("caption", ""), "hashtags": [], "writer": meta.get("writer", ""),
        "scene_plan": meta.get("scene_plan"), "look": _overrides_from_sidecar_look(meta.get("look")),
    }
    return script_from_entry(entry)


def _overrides_from_sidecar_look(stored: Any) -> dict:
    """A sidecar stores the *resolved* look it was rendered with; replaying
    it means every field is an override."""
    if not isinstance(stored, dict):
        return {}
    try:
        clean = looks.validate_look(stored, partial=True)
    except looks.LookError:
        return {}
    return {k: v for k, v in clean.items() if v is not None}


def new_manifest(scripts: list[Script], day: date, kind: str = "day", default_look: dict | None = None,
                 look_overrides: dict | None = None) -> dict:
    return {
        "version": MANIFEST_VERSION, "kind": kind, "date": day.isoformat(), "created": now_iso(), "updated": now_iso(),
        "default_look": dict(default_look or looks.look_from_env()),
        "writer": scripts[0].writer if scripts else "",
        "videos": [entry_from_script(s, i + 1, look_overrides) for i, s in enumerate(scripts)],
    }


def read_manifest(folder: Path) -> dict | None:
    data = read_json(manifest_path(folder))
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION or not isinstance(data.get("videos"), list):
        return None
    return data


def write_manifest(folder: Path, data: dict) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data["updated"] = now_iso()
    write_json_atomic(manifest_path(folder), data)
    return manifest_path(folder)


def get_entry(data: dict, vid: str) -> dict:
    if not _VID.match(vid or ""):
        raise ManifestError(f"{vid!r} is not a video id")
    for entry in data["videos"]:
        if entry.get("id") == vid:
            return entry
    raise ManifestError(f"no video {vid} in this plan")


def update_entry(folder: Path, vid: str, **fields) -> dict:
    """Atomically updates one entry (re-reading the manifest first, so two
    writers cannot clobber each other's fields)."""
    if "look" in fields:
        fields["look"] = looks.compact(looks.validate_look(fields["look"] or {}))
    with locked(folder):
        data = read_manifest(folder)
        if data is None:
            raise ManifestError(f"no plan in {folder}")
        entry = get_entry(data, vid)
        entry.update(fields)
        entry["updated"] = now_iso()
        write_manifest(folder, data)
    return entry


def replace_script(folder: Path, vid: str, script: Script) -> dict:
    """Swap in a rewritten script, keeping the entry's id, index and look.
    Its previews showed the old script, so they are deleted."""
    with locked(folder):
        data = read_manifest(folder)
        if data is None:
            raise ManifestError(f"no plan in {folder}")
        entry = get_entry(data, vid)
        fresh = entry_from_script(script, int(entry["index"]))
        fresh["look"] = entry.get("look") or looks.compact({})
        fresh["status"], fresh["rendered"], fresh["error"] = "planned", None, None
        data["videos"][data["videos"].index(entry)] = fresh
        write_manifest(folder, data)
    drop_previews(folder, vid)
    return fresh


def resolved_look(entry: dict, run_overrides: dict | None = None, environ=None) -> dict:
    """default (environment) < run overrides < this video's look."""
    return looks.resolve_look(run_overrides, entry.get("look"), environ=environ, index=_position(entry))


def _position(entry: dict) -> int | None:
    """The video's 0-based place in its plan (its `index` is 1-based), for the accent rotation."""
    try:
        return max(0, int(entry.get("index")) - 1)
    except (TypeError, ValueError):
        return None
