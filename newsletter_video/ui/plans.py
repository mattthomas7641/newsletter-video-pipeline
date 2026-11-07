"""Plans and previews for the panel: listing plan manifests, editing a
planned video's look, and starting the plan / preview / rewrite / final
render jobs (all worker processes through the job manager).

A plan reference (`ref`) is a date (output/<date>/plan.json) or a test
plan's id (output/_previews/<id>/plan.json). A video reference is a plan
entry id (v01) or, for a video the pipeline already rendered, its sidecar
stem (10_some-title), so an existing video can be previewed with a new look
too. Both are validated by regex before they touch a path.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from newsletter_video import look as looks
from newsletter_video import plan_manifest as pm

from . import security
from .config import UIPaths
from .envstore import EnvStore, SettingsError
from .jobs import JobManager, JobView
from .runs import RunBlocked, RunService

REF = re.compile(r"^(\d{4}-\d{2}-\d{2}|[a-z0-9]{6,40})$")
VID = re.compile(r"^(v\d{2,3}|\d{2,}_[A-Za-z0-9._-]{0,150})$")
PREVIEW_FILE = re.compile(r"^(storyboard\.jpg|draft\.mp4|frames/\d{2}_\d{3}\.\d{1}s\.png)$")
TIERS = ("stills", "draft")
MAX_PLAN_VIDEOS = 20


class PlanError(ValueError):
    """Shown to the owner as is."""


class PlanService:
    def __init__(self, paths: UIPaths, jobs: JobManager, store: EnvStore, runs: RunService, characters=None):
        self.paths = paths
        self.jobs = jobs
        self.store = store
        self.runs = runs
        self.characters = characters

    # --- references ---

    def folder(self, ref: str) -> Path:
        if not REF.match(ref or ""):
            raise KeyError(ref)
        try:
            day = date.fromisoformat(ref)
            return self.paths.output_dir / day.isoformat()
        except ValueError:
            return self.paths.output_dir / "_previews" / ref

    @staticmethod
    def is_test(ref: str) -> bool:
        try:
            date.fromisoformat(ref)
            return False
        except ValueError:
            return True

    def _env(self, extra: dict[str, str] | None = None) -> dict:
        env = dict(os.environ)
        env["BYTESIZE_ENV_FILE"] = str(self.paths.env_path)
        env.update(extra or {})
        return env

    # --- listing ---

    def list_plans(self, today: date) -> list[dict]:
        out = []
        for offset in range(0, 14):
            day = today.fromordinal(today.toordinal() - offset)
            data = pm.read_manifest(self.paths.output_dir / day.isoformat())
            if data:
                out.append(self._plan_card(day.isoformat(), data))
        root = self.paths.output_dir / "_previews"
        tests = []
        if root.is_dir():
            for folder in root.iterdir():
                if folder.is_dir() and security.JOB_ID.match(folder.name):
                    data = pm.read_manifest(folder)
                    if data:
                        tests.append((folder.stat().st_mtime, self._plan_card(folder.name, data)))
        tests.sort(key=lambda t: -t[0])
        return out + [card for _, card in tests[:6]]

    def _plan_card(self, ref: str, data: dict) -> dict:
        videos = data.get("videos", [])
        return {"ref": ref, "kind": data.get("kind", "day"), "date": data.get("date"), "created": data.get("created"),
                "count": len(videos), "rendered": sum(1 for v in videos if v.get("status") == "rendered"),
                "titles": [str(v.get("title", ""))[:120] for v in videos]}

    def describe(self, ref: str) -> dict:
        """The plan with every video's resolved look and preview state."""
        folder = self.folder(ref)
        data = pm.read_manifest(folder)
        if data is None:
            raise KeyError(ref)
        defaults = self._default_look()
        videos = []
        for entry in data["videos"]:
            overrides = entry.get("look") or {}
            try:
                resolved = looks.resolve_look(overrides, environ=self._environ())
            except looks.LookError:
                resolved = dict(defaults)
            plan = entry.get("scene_plan") or {}
            videos.append({
                "id": entry["id"], "index": entry.get("index"), "title": entry.get("title", ""),
                "badge": entry.get("badge", ""), "stories": [str((s or {}).get("title", s))[:160] for s in entry.get("stories", [])],
                "words": sum(len(str(s.get("say", "")).split()) for s in plan.get("scenes", [])) if plan
                else sum(len(str(b.get("say", "")).split()) for b in (entry.get("script") or {}).get("beats", []))
                + len(str((entry.get("script") or {}).get("hook", "")).split()) + len(str((entry.get("script") or {}).get("outro", "")).split()),
                "scenes": [s.get("type") for s in plan.get("scenes", [])] if plan else None,
                "writer": entry.get("writer", ""), "status": entry.get("status", "planned"), "error": entry.get("error"),
                "rendered": entry.get("rendered"), "look": {k: v for k, v in overrides.items() if k != "version"},
                "resolved": resolved, "preview": self.preview_state(ref, entry["id"]),
            })
        return {"ref": ref, "kind": data.get("kind", "day"), "date": data.get("date"), "created": data.get("created"),
                "updated": data.get("updated"), "writer": data.get("writer", ""), "default_look": defaults,
                "videos": videos, "is_test": self.is_test(ref)}

    def _environ(self) -> dict:
        env = dict(os.environ)
        env.update(self.store.read()["values"])
        return env

    def _default_look(self) -> dict:
        try:
            return looks.look_from_env(self._environ())
        except looks.LookError:
            return dict(looks.DEFAULTS)

    def preview_state(self, ref: str, vid: str) -> dict:
        folder = self.folder(ref)
        if not VID.match(vid or ""):
            raise KeyError(vid)
        out_dir = folder / "_preview" / vid
        try:
            data = json.loads((out_dir / "preview.json").read_text())
        except (OSError, ValueError):
            return {}
        state = {}
        for tier in TIERS:
            info = data.get(tier)
            if isinstance(info, dict) and (out_dir / str(info.get("file", ""))).is_file():
                state[tier] = {
                    "url": f"/media/plan/{ref}/{vid}/{info['file']}", "timing": info.get("timing"),
                    "seconds": info.get("seconds"), "at": info.get("at"), "duration": info.get("duration"),
                    "frames": [{"label": f.get("label"), "at": f.get("at"), "detail": f.get("detail"),
                                "url": f"/media/plan/{ref}/{vid}/frames/{f.get('file')}"} for f in info.get("frames", [])]
                    if tier == "stills" else None,
                    "look": data.get("look"),
                }
        return state

    def preview_file(self, ref: str, vid: str, rel: str) -> Path | None:
        if not VID.match(vid or "") or not PREVIEW_FILE.match(rel or ""):
            return None
        target = self.folder(ref) / "_preview" / vid / rel
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            return None
        base = self.paths.output_dir.resolve()
        return resolved if base in resolved.parents and resolved.is_file() else None

    # --- editing ---

    def set_look(self, ref: str, vid: str, raw: dict) -> dict:
        folder = self.folder(ref)
        if not re.fullmatch(r"v\d{2,3}", vid or ""):
            raise KeyError(vid)
        try:
            clean = looks.validate_look(raw or {})
        except looks.LookError as exc:
            raise PlanError("; ".join(exc.errors)) from None
        if clean.get("character") and self.characters is not None:
            if clean["character"] not in {c["slug"] for c in self.characters.list()}:
                raise PlanError("That character doesn't exist.")
        try:
            pm.update_entry(folder, vid, look=clean)
        except pm.ManifestError as exc:
            raise KeyError(str(exc)) from None
        return self.describe(ref)

    # --- jobs ---

    def start_plan(self, ref: str | None, videos: int | None, overrides: dict) -> JobView:
        clean = looks.validate_look(overrides or {})
        self.runs.guard()
        job_id = self.jobs.new_id()
        argv = [sys.executable, "-m", "newsletter_video.plan_day"]
        if ref is None:
            ref = job_id
            argv += ["--test", "--out", str(self.paths.output_dir / "_previews" / job_id)]
            n = videos or 1
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", ref):
                raise PlanError("A day plan needs a date.")
            argv.append(ref)
            n = videos or int(self.store.read()["values"]["VIDEOS_PER_DAY"])
        if not 1 <= int(n) <= MAX_PLAN_VIDEOS:
            raise PlanError(f"Plan between 1 and {MAX_PLAN_VIDEOS} videos.")
        argv += ["--videos", str(int(n)), *_look_args(clean)]
        env = self._env({"NOTIFY_WEBHOOK_URL": ""})
        return self.jobs.create("plan", argv, env, {"ref": ref, "videos": int(n), "test": self.is_test(ref)}, job_id=job_id)

    def start_rewrite(self, ref: str, vid: str) -> JobView:
        folder = self.folder(ref)
        if not re.fullmatch(r"v\d{2,3}", vid or ""):
            raise KeyError(vid)
        if pm.read_manifest(folder) is None:
            raise KeyError(ref)
        self.runs.guard()
        argv = [sys.executable, "-m", "newsletter_video.plan_day", "--out", str(folder), "--rewrite", vid]
        if not self.is_test(ref):
            argv.insert(3, ref)
        return self.jobs.create("rewrite", argv, self._env({"NOTIFY_WEBHOOK_URL": ""}), {"ref": ref, "video": vid})

    def start_preview(self, ref: str, vid: str, tier: str, voice: bool = False, overrides: dict | None = None) -> JobView:
        folder = self.folder(ref)
        if not VID.match(vid or ""):
            raise KeyError(vid)
        if tier not in TIERS:
            raise PlanError("Choose a storyboard (stills) or a draft clip.")
        clean = looks.validate_look(overrides or {})
        if vid.startswith("v"):
            if pm.read_manifest(folder) is None:
                raise KeyError(ref)
            source = [str(folder), "--video", vid]
        else:
            sidecar = folder / f"{vid}.json"
            if not sidecar.is_file():
                raise KeyError(vid)
            source = [str(sidecar)]
        self.runs.guard()
        argv = [sys.executable, "-m", "newsletter_video.preview", *source, "--preview", tier, *_look_args(clean)]
        if voice:
            argv.append("--voice")
        return self.jobs.create("preview", argv, self._env(), {"ref": ref, "video": vid, "tier": tier, "voice": bool(voice)})

    def start_render(self, ref: str, only: list[str] | None, confirm: bool) -> JobView:
        folder = self.folder(ref)
        data = pm.read_manifest(folder)
        if data is None:
            raise KeyError(ref)
        if confirm is not True:
            raise PlanError("A final render needs confirmation.")
        ids = {e["id"] for e in data["videos"]}
        if only is not None:
            if not isinstance(only, list) or not only or not all(isinstance(v, str) and v in ids for v in only):
                raise PlanError("Choose which planned videos to render.")
        self.runs.guard()
        argv = [sys.executable, "-m", "newsletter_video.render_plan", ref if not self.is_test(ref) else str(folder), "--final"]
        if only:
            argv += ["--only", ",".join(only)]
        env = self._env()
        if self.is_test(ref):
            argv.append("--test")
            env.update({"YOUTUBE_UPLOAD": "0", "NOTIFY_WEBHOOK_URL": ""})
        saved = self.store.read()["values"]
        meta = {"ref": ref, "only": only, "videos": len(only) if only else len(ids), "test": self.is_test(ref),
                "uploads": saved["YOUTUBE_UPLOAD"] == "1" and not self.is_test(ref), "privacy": saved["YOUTUBE_PRIVACY"]}
        return self.jobs.create("render", argv, env, meta)


def _look_args(clean: dict) -> list[str]:
    args: list[str] = []
    for key in looks.FIELDS:
        if clean.get(key) is not None:
            args += [f"--{key}", clean[key]]
    return args


__all__ = ["PlanService", "PlanError", "RunBlocked", "SettingsError"]
