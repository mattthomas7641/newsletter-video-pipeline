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

import hashlib
import json
import os
import re
import shutil
import socket
import sys
import time
from datetime import date
from pathlib import Path

from newsletter_video import look as looks
from newsletter_video import plan_manifest as pm
from newsletter_video.runstate import write_json_atomic

from . import security
from .config import UIPaths
from .envstore import EnvStore, SettingsError
from .jobs import JobManager, JobView
from .runs import RunBlocked, RunService

REF = re.compile(r"^(\d{4}-\d{2}-\d{2}|[a-z0-9]{6,40})$")
VID = re.compile(r"^(v\d{2,3}|\d{2,}_[A-Za-z0-9._-]{0,150})$")
_FRAME = r"frames/\d{2}_\d{3}\.\d{1}s\.png"
PREVIEW_FILE = re.compile(rf"^(storyboard\.jpg|draft\.mp4|{_FRAME}|looks/[a-z0-9-]{{1,60}}/(storyboard\.jpg|draft\.mp4|{_FRAME}))$")
TIERS = ("stills", "draft")
MAX_PLAN_VIDEOS = 20
# Storyboards of earlier looks on the same video, kept so two looks can be
# compared side by side: _preview/<vid>/looks/<key>/ plus a looks.json index.
LOOKS_DIRNAME = "looks"
MAX_KEPT_LOOKS = 8
STUDIO_KIND = "studio"  # one live HyperFrames Studio at a time; not a heavy run (previews may run beside it)
STUDIO_HOST = "127.0.0.1"
STUDIO_DIRNAME = "_studio"  # = preview.STUDIO_DIRNAME (not imported: the panel stays free of the render stack)
STUDIO_WAIT_SECONDS = 25.0  # how long a start request waits for the server to answer before returning anyway


class PlanError(ValueError):
    """Shown to the owner as is."""


class PlanService:
    def __init__(self, paths: UIPaths, jobs: JobManager, store: EnvStore, runs: RunService, characters=None):
        self.paths = paths
        self.jobs = jobs
        self.store = store
        self.runs = runs
        self.characters = characters
        self.studio_wait = STUDIO_WAIT_SECONDS

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
                pos = pm._position(entry)
                resolved = looks.resolve_look(overrides, environ=self._environ(), index=pos)
                video_default = looks.resolve_look({}, environ=self._environ(), index=pos)
            except looks.LookError:
                resolved = video_default = dict(defaults)
            plan = entry.get("scene_plan") or {}
            videos.append({
                "id": entry["id"], "index": entry.get("index"), "title": entry.get("title", ""),
                "badge": entry.get("badge", ""), "stories": [str((s or {}).get("title", s))[:160] for s in entry.get("stories", [])],
                "words": sum(len(str(s.get("say", "")).split()) for s in plan.get("scenes", [])) if plan
                else sum(len(str(b.get("say", "")).split()) for b in (entry.get("script") or {}).get("beats", []))
                + len(str((entry.get("script") or {}).get("hook", "")).split()) + len(str((entry.get("script") or {}).get("outro", "")).split()),
                "scenes": [s.get("style" if plan.get("style") == "avx" else "type") for s in plan.get("scenes", [])] if plan else None,
                "writer": entry.get("writer", ""), "status": entry.get("status", "planned"), "error": entry.get("error"),
                "rendered": entry.get("rendered"), "look": {k: v for k, v in overrides.items() if k != "version"},
                "resolved": resolved, "default_look": video_default, "color_notes": color_notes(resolved), "look_key": look_key(resolved), "preview": self.preview_state(ref, entry["id"]),
            })
        return {"ref": ref, "kind": data.get("kind", "day"), "date": data.get("date"), "created": data.get("created"),
                "updated": data.get("updated"), "writer": data.get("writer", ""), "default_look": defaults,
                "default_color_notes": color_notes(defaults),
                "videos": videos, "is_test": self.is_test(ref), "studio": self.studio_state()}

    def _environ(self) -> dict:
        """The environment a look resolves in: the process environment, the
        settings the panel manages and the read-only ones it only reflects
        (the daily job's style-library policy)."""
        env = dict(os.environ)
        env.update({k: v for k, v in self.store.read_only().items() if v})
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
        current = self._fingerprint(folder, vid)
        for tier in TIERS:
            info = data.get(tier)
            trial = info.get("trial_look") if isinstance(info, dict) else None
            if isinstance(info, dict) and info.get("fingerprint") != current and not (
                    isinstance(trial, dict) and info.get("fingerprint") == self._trial_fingerprint(folder, vid, trial)):
                continue  # made from an earlier script or look: stale, hidden
            if isinstance(info, dict) and (out_dir / str(info.get("file", ""))).is_file():
                state[tier] = {
                    "url": f"/media/plan/{ref}/{vid}/{info['file']}", "timing": info.get("timing"),
                    "seconds": info.get("seconds"), "at": info.get("at"), "duration": info.get("duration"),
                    "fps": info.get("fps"), "quality": info.get("quality"), "size": info.get("size"),
                    "frames": [{"label": f.get("label"), "at": f.get("at"), "detail": f.get("detail"),
                                "url": f"/media/plan/{ref}/{vid}/frames/{f.get('file')}"} for f in info.get("frames", [])]
                    if tier == "stills" else None,
                    "look": data.get("look"),
                    "trial": bool(trial) and info.get("fingerprint") != current,
                }
        kept = self._kept_looks(out_dir)
        if kept:
            base = f"/media/plan/{ref}/{vid}/{LOOKS_DIRNAME}"
            state["looks"] = [{
                "key": e["key"], "look": e.get("look"), "at": e.get("at"), "duration": e.get("duration"),
                "timing": e.get("timing"),
                "url": f"{base}/{e['key']}/storyboard.jpg" if (out_dir / LOOKS_DIRNAME / e["key"] / "storyboard.jpg").is_file() else None,
                "draft": {**e["draft"], "url": f"{base}/{e['key']}/draft.mp4"} if isinstance(e.get("draft"), dict)
                and (out_dir / LOOKS_DIRNAME / e["key"] / "draft.mp4").is_file() else None,
                "frames": [{"label": f.get("label"), "at": f.get("at"), "url": f"{base}/{e['key']}/frames/{f.get('file')}"}
                           for f in e.get("frames", [])],
            } for e in kept]
        return state

    # --- storyboards of earlier looks, kept for side-by-side comparison ---

    def _kept_looks(self, out_dir: Path) -> list[dict]:
        try:
            data = json.loads((out_dir / "looks.json").read_text())
        except (OSError, ValueError):
            return []
        entries = data.get("entries") if isinstance(data, dict) else None
        return [e for e in entries or [] if isinstance(e, dict) and re.fullmatch(r"[a-z0-9-]{1,60}", str(e.get("key", "")))
                and any((out_dir / LOOKS_DIRNAME / e["key"] / name).is_file() for name in ("storyboard.jpg", "draft.mp4"))]

    def keep_stills(self, ref: str, vid: str) -> dict | None:
        """Copies the video's current, fresh storyboard (frames included) and
        draft clip under `_preview/<vid>/looks/<key>/`, keyed by the look they
        show, so the next look can be compared against them. Nothing is
        rendered; a stale or missing preview keeps nothing. Returns the index
        entry, or None."""
        folder = self.folder(ref)
        if not VID.match(vid or ""):
            raise KeyError(vid)
        out_dir = folder / "_preview" / vid
        try:
            data = json.loads((out_dir / "preview.json").read_text())
        except (OSError, ValueError):
            return None
        fp = self._fingerprint(folder, vid)
        info, dinfo = data.get("stills"), data.get("draft")
        sheet = out_dir / str((info or {}).get("file") or "storyboard.jpg") if isinstance(info, dict) and info.get("fingerprint") == fp else None
        clip = out_dir / str(dinfo.get("file") or "draft.mp4") if isinstance(dinfo, dict) and dinfo.get("fingerprint") == fp else None
        sheet = sheet if sheet is not None and sheet.is_file() else None
        clip = clip if clip is not None and clip.is_file() else None
        if sheet is None and clip is None:
            return None
        try:
            resolved = looks.resolve_look(self._stored_look(folder, vid), environ=self._environ(), index=self._position(folder, vid))
        except looks.LookError:
            return None
        key = look_key(resolved)
        target = out_dir / LOOKS_DIRNAME / key
        entry = next((e for e in self._kept_looks(out_dir) if e["key"] == key), {"key": key})
        entry["look"] = resolved
        target.mkdir(parents=True, exist_ok=True)
        if sheet is not None:
            shutil.rmtree(target / "frames", ignore_errors=True)
            (target / "frames").mkdir(parents=True)
            shutil.copy2(sheet, target / "storyboard.jpg")
            frames = []
            for f in info.get("frames", []):
                name = str((f or {}).get("file") or "")
                src = out_dir / "frames" / name
                if re.fullmatch(_FRAME[len("frames/"):], name) and src.is_file():
                    shutil.copy2(src, target / "frames" / name)
                    frames.append({"label": f.get("label"), "at": f.get("at"), "detail": f.get("detail"), "file": name})
            entry.update({"at": info.get("at"), "duration": info.get("duration"), "timing": info.get("timing"), "frames": frames})
        if clip is not None:
            shutil.copy2(clip, target / "draft.mp4")
            entry["draft"] = {"at": dinfo.get("at"), "duration": dinfo.get("duration"), "timing": dinfo.get("timing"),
                              "fps": dinfo.get("fps"), "size": dinfo.get("size")}
            entry.setdefault("at", dinfo.get("at"))
        kept = [e for e in self._kept_looks(out_dir) if e["key"] != key]
        kept.insert(0, entry)
        for old in kept[MAX_KEPT_LOOKS:]:
            shutil.rmtree(out_dir / LOOKS_DIRNAME / old["key"], ignore_errors=True)
        write_json_atomic(out_dir / "looks.json", {"version": 1, "entries": kept[:MAX_KEPT_LOOKS]})
        return entry

    def _position(self, folder: Path, vid: str) -> int | None:
        if not vid.startswith("v"):
            return None
        data = pm.read_manifest(folder)
        try:
            return pm._position(pm.get_entry(data, vid)) if data else None
        except pm.ManifestError:
            return None

    def _stored_look(self, folder: Path, vid: str) -> dict:
        if vid.startswith("v"):
            data = pm.read_manifest(folder)
            return (pm.get_entry(data, vid).get("look") or {}) if data else {}
        try:
            return pm._overrides_from_sidecar_look(json.loads((folder / f"{vid}.json").read_text()).get("look"))
        except (OSError, ValueError):
            return {}

    def _trial_fingerprint(self, folder: Path, vid: str, trial_look: dict) -> str | None:
        """The fingerprint the video would have if `trial_look` were its saved look (a trial preview's)."""
        if not vid.startswith("v"):
            return None
        data = pm.read_manifest(folder)
        if not data:
            return None
        try:
            entry = pm.get_entry(data, vid)
        except KeyError:
            return None
        return pm.fingerprint(entry.get("script"), entry.get("scene_plan"), trial_look)

    def _fingerprint(self, folder: Path, vid: str) -> str | None:
        if vid.startswith("v"):
            data = pm.read_manifest(folder)
            try:
                return pm.entry_fingerprint(pm.get_entry(data, vid)) if data else None
            except pm.ManifestError:
                return None
        try:
            return pm.sidecar_fingerprint(json.loads((folder / f"{vid}.json").read_text()))
        except (OSError, ValueError):
            return None

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
        self.keep_stills(ref, vid)  # the storyboard of the look being replaced stays comparable
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

    def start_preview(self, ref: str, vid: str, tier: str, voice: bool = False, overrides: dict | None = None, trial: bool = False) -> JobView:
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
        self.keep_stills(ref, vid)  # a re-capture overwrites the frames and clip; the current look stays comparable
        argv = [sys.executable, "-m", "newsletter_video.preview", *source, "--preview", tier, *_look_args(clean)]
        if trial and vid.startswith("v"):
            argv.append("--trial")
        if voice:
            argv.append("--voice")
        return self.jobs.create("preview", argv, self._env(), {"ref": ref, "video": vid, "tier": tier, "voice": bool(voice)})

    # --- HyperFrames Studio (live, scrubbable composition) ---

    def studio_state(self) -> dict | None:
        """The running Studio job, if any: which video it shows and its URL."""
        for job in self.jobs.active(kinds=(STUDIO_KIND,)):
            return {"id": job.id, "ref": job.meta.get("ref"), "video": job.meta.get("video"),
                    "url": job.meta.get("url"), "port": job.meta.get("port"), "started_at": job.created_at}
        return None

    def stop_studio(self) -> bool:
        """Stops the running Studio server (there is at most one)."""
        stopped = False
        for job in self.jobs.active(kinds=(STUDIO_KIND,)):
            stopped = self.jobs.cancel(job.id) or stopped
        return stopped

    def start_studio(self, ref: str, vid: str, overrides: dict | None = None) -> dict:
        """Prepares the video's composition (resolved look, cached timing, no
        voice) and serves it with HyperFrames Studio on a free 127.0.0.1 port.
        Starting another Studio stops the previous one. Returns the job and
        the local URL; `ready` says whether the server answered in time."""
        folder = self.folder(ref)
        if not VID.match(vid or ""):
            raise KeyError(vid)
        clean = looks.validate_look(overrides or {})
        if vid.startswith("v"):
            data = pm.read_manifest(folder)
            if data is None:
                raise KeyError(ref)
            try:
                pm.get_entry(data, vid)
            except pm.ManifestError:
                raise KeyError(vid) from None
            source = [str(folder), "--video", vid]
        else:
            sidecar = folder / f"{vid}.json"
            if not sidecar.is_file():
                raise KeyError(vid)
            source = [str(sidecar)]
        self.runs.guard()  # not while a render is using the machine; Studio itself never blocks a preview
        self.stop_studio()
        port = _free_port()
        url = f"http://{STUDIO_HOST}:{port}/#project/{STUDIO_DIRNAME}"  # the URL Studio itself prints
        argv = [sys.executable, "-m", "newsletter_video.preview", *source, "--studio", "--port", str(port), *_look_args(clean)]
        meta = {"ref": ref, "video": vid, "port": port, "url": url, "test": self.is_test(ref)}
        job = self.jobs.create(STUDIO_KIND, argv, self._env(), meta)
        ready = self._wait_for_studio(job.id, port, self.studio_wait)
        return {"job": self.jobs.get(job.id).to_json(), "url": url, "ready": ready}

    def _wait_for_studio(self, job_id: str, port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if _port_answers(port):
                return True
            view = self.jobs.get(job_id)
            if view is None or view.state != "running" or time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((STUDIO_HOST, 0))
        return int(sock.getsockname()[1])


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection((STUDIO_HOST, port), timeout=0.3):
            return True
    except OSError:
        return False


def _look_args(clean: dict) -> list[str]:
    args: list[str] = []
    for key in looks.FIELDS:
        if clean.get(key) is not None:
            args += [f"--{key}", clean[key]]
    return args


def color_notes(resolved: dict) -> list[str]:
    """Plain-words notes on any colour the guard (colors.fit_colors) would adjust for this look."""
    from ..colors import fit_colors

    if resolved.get("style") in ("avatar", "avx") or not (resolved.get("bg") or resolved.get("accent")):  # avx: palette() guards
        return []
    return fit_colors(resolved.get("theme") or "light", resolved.get("bg") or None, resolved.get("accent") or None)["notes"]


def look_key(resolved: dict) -> str:
    """A short, path-safe name for a resolved look: its preset (or 'custom')
    plus a hash of every field, so two looks compare equal exactly when they
    render the same."""
    fields = {k: resolved.get(k) for k in looks.FIELDS}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:8]
    return f"{resolved.get('preset') or 'custom'}-{digest}"


__all__ = ["PlanService", "PlanError", "RunBlocked", "SettingsError", "look_key"]
