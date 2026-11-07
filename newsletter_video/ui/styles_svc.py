"""The style library for the panel: listing references with their contact
sheets, tags, notes, fidelity and recipes; adding one from an uploaded
video (validated by content and size, stored in the library's inbox under
a generated name, analysed by a background `styles add` job); searching;
removing. Everything reuses newsletter_video.styles; nothing here talks to
a model itself. Reference clips never leave the machine except through the
model calls the library already makes when analysing (docs/style-library.md).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from newsletter_video.styles.library import OFF_VALUES, Library, ReferenceTooLong, check_reference_duration, slugify

from . import security
from .config import UIPaths
from .envstore import EnvStore
from .jobs import JobManager, JobView
from .runs import RunBlocked, RunService

MAX_VIDEO_BYTES = 300 * 1024 * 1024
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MEDIA = re.compile(r"^(contact_sheet\.jpg|keyframes/[A-Za-z0-9_-]{1,60}\.(jpg|png)|recreation/side_by_side\.jpg)$")


def sniff_video(data: bytes) -> str | None:
    """Container kind from the bytes, not the filename: mp4/mov (ftyp),
    webm/mkv (EBML), avi (RIFF AVI)."""
    head = data[:16]
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "mov" if brand in (b"qt  ",) else "mp4"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    return None


def validate_video(data: bytes) -> str:
    if not data:
        raise security.UploadError("The video file is empty.")
    if len(data) > MAX_VIDEO_BYTES:
        raise security.UploadError(f"The video is larger than {MAX_VIDEO_BYTES // (1024 * 1024)} MB.")
    kind = sniff_video(data)
    if kind is None:
        raise security.UploadError("That isn't a recognised video file (use MP4, MOV, WebM or AVI).")
    return kind


class StyleService:
    def __init__(self, paths: UIPaths, jobs: JobManager, store: EnvStore, runs: RunService):
        self.paths = paths
        self.jobs = jobs
        self.store = store
        self.runs = runs

    # --- where ---

    def root(self) -> Path | None:
        """The library folder from the saved settings, or None when off."""
        raw = self.store.read()["values"].get("STYLE_LIBRARY", "style_library").strip()
        if raw.lower() in OFF_VALUES:
            return None
        p = Path(raw).expanduser()
        return p if p.is_absolute() else self.paths.root / p

    def library(self) -> Library | None:
        root = self.root()
        return Library(root) if root is not None else None

    # --- listing ---

    def describe(self, query: str = "") -> dict:
        lib = self.library()
        enabled = lib is not None
        entries = lib.entries() if lib else []
        hits = None
        if lib and query.strip():
            hits = {e.name: score for score, e, _ in lib.search(query, limit=50)}
        refs = []
        for e in entries:
            if hits is not None and e.name not in hits:
                continue
            folder = lib.entry_dir(e.name)
            recipes = [self._recipe_json(r) for r in lib.recipes_for(e.name)]
            refs.append({
                "name": e.name, "title": e.title or e.name, "added": e.added, "duration": e.duration,
                "scene_count": e.scene_count, "tags": list(e.tags)[:16], "user_tags": list(e.user_tags),
                "notes": e.notes, "summary": e.summary[:400], "fidelity": e.fidelity, "vision_used": e.vision_used,
                "cost_usd": e.cost_usd, "recipes": recipes, "score": hits.get(e.name) if hits else None,
                "contact_sheet": f"/media/style/{e.name}/contact_sheet.jpg" if (folder / "contact_sheet.jpg").is_file() else None,
                "side_by_side": f"/media/style/{e.name}/recreation/side_by_side.jpg"
                if (folder / "recreation" / "side_by_side.jpg").is_file() else None,
                "has_reference": lib.reference_path(e.name) is not None,
            })
        if hits:
            refs.sort(key=lambda r: -(r["score"] or 0))
        builtin = []
        if lib is not None:
            from newsletter_video.styles.recipes import builtin_recipes

            builtin = [self._recipe_json(r) for r in builtin_recipes().values()]
        values = self.store.read()["values"]
        root = self.root()
        shown = None
        if root is not None:
            try:
                shown = str(root.resolve().relative_to(self.paths.root.resolve()))
            except ValueError:
                shown = str(root)
        return {"enabled": enabled, "root": shown, "references": refs,
                "builtin_recipes": builtin, "policy": values.get("STYLE_RECIPE", "auto"),
                "library_setting": values.get("STYLE_LIBRARY", "style_library"),
                "keys": {"ANTHROPIC_API_KEY": self.store.secret_presence().get("ANTHROPIC_API_KEY", False)},
                "adding": [j.to_json() for j in self.jobs.active(kinds=("style-add",))]}

    @staticmethod
    def _recipe_json(r) -> dict:
        return {"id": r.id, "name": r.name, "description": r.description[:240], "knobs": r.knobs,
                "scene_types": r.scene_types, "when_to_use": r.when_to_use[:200], "source": r.source,
                "fidelity": r.fidelity, "demonstrates": r.demonstrates[:10]}

    def recipe_choices(self) -> list[dict]:
        """Recipe ids and names for the per-video look picker (library first)."""
        lib = self.library()
        if lib is None:
            return []
        items = []
        for r in lib.all_recipes().values():
            items.append({"id": r.id, "name": r.name, "source": r.source, "fidelity": r.fidelity})
        items.sort(key=lambda r: (r["source"] == "builtin", -(r["fidelity"] or 0), r["id"]))
        return items

    def media_path(self, name: str, rel: str) -> Path | None:
        lib = self.library()
        if lib is None or not NAME.match(name or "") or not MEDIA.match(rel or ""):
            return None
        target = lib.entry_dir(name) / rel
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            return None
        base = lib.root.resolve()
        return resolved if base in resolved.parents and resolved.is_file() else None

    # --- adding and removing ---

    def start_add(self, fields: dict[str, str], video: bytes) -> JobView:
        lib = self.library()
        if lib is None:
            raise security.UploadError("The style library is switched off in Settings.")
        title = security.clean_display_name(fields.get("name", ""), 60)
        try:
            slug = slugify(title)
        except ValueError:
            raise security.UploadError("Give the reference a name with letters or numbers in it.") from None
        if lib.get(slug) is not None or (lib.entry_dir(slug) / "meta.json").exists():
            raise security.UploadError(f"A reference called \"{title}\" already exists. Pick a different name.")
        kind = validate_video(video)
        notes = security.clean_persona(fields.get("notes", ""), 600)
        tags = [t.strip() for t in fields.get("tags", "").lower().split(",")]
        tags = [t for t in tags if re.fullmatch(r"[a-z0-9-]{1,30}", t)][:12]
        if self.jobs.active(kinds=("style-add",)):
            raise RunBlocked("A reference is already being analysed. Wait for it to finish.")
        self.runs.guard()
        job_id = self.jobs.new_id()
        inbox = lib.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        clip = inbox / f"{slug}-{job_id}.{kind}"
        clip.write_bytes(video)
        try:
            self._check_duration(clip)
        except BaseException:
            clip.unlink(missing_ok=True)
            raise
        argv = [sys.executable, "-m", "newsletter_video.styles", "add", str(clip), "--name", slug]
        if notes:
            argv += ["--notes", notes]
        if tags:
            argv += ["--tags", ",".join(tags)]
        if fields.get("vision") == "0":
            argv.append("--no-vision")
        env = dict(os.environ, BYTESIZE_ENV_FILE=str(self.paths.env_path), STYLE_LIBRARY=str(lib.root))
        try:
            return self.jobs.create("style-add", argv, env, {"name": slug, "title": title, "clip": clip.name,
                                                           "vision": fields.get("vision") != "0", "size": len(video)},
                                    job_id=job_id)
        except BaseException:
            clip.unlink(missing_ok=True)
            raise

    def _check_duration(self, clip: Path) -> None:
        """Rejects a reference over STYLE_MAX_SECONDS before the paid
        analysis starts. The cap is read from the project's .env (the add
        job reads the same file). A clip ffprobe cannot read is left to
        the job, which fails with its own message."""
        raw = str(self.store.read()["values"].get("STYLE_MAX_SECONDS", "") or "").strip()
        limit = float(raw) if raw else None  # validated by the settings schema
        try:
            check_reference_duration(clip, limit)
        except ReferenceTooLong as exc:
            raise security.UploadError(str(exc)) from None
        except Exception:  # noqa: BLE001 - unreadable clip: the job reports it
            return

    def add_outcome(self, job: JobView) -> dict:
        """What the finished `styles add` produced, from the library itself."""
        lib = self.library()
        if lib is not None and job.state != "done":
            lib.clean_partials()  # a cancelled or failed add leaves nothing behind
        name = job.meta.get("name", "")
        entry = lib.get(name) if lib and NAME.match(name) else None
        tail = self.jobs.tail(job.id, 6)
        cost = None
        for line in reversed(tail):
            m = re.search(r"est\. \$([0-9.]+)", line)
            if m:
                cost = float(m.group(1))
                break
        return {"ok": entry is not None and job.state == "done", "name": name, "cost_usd": cost,
                "fidelity": entry.fidelity if entry else None, "scene_count": entry.scene_count if entry else None,
                "error": None if entry is not None else (tail[-1] if tail else "The analysis did not finish.")}

    def remove(self, name: str) -> bool:
        lib = self.library()
        if lib is None or not NAME.match(name or ""):
            raise KeyError(name)
        removed = lib.remove(name)
        inbox = lib.root / "inbox"
        if inbox.is_dir():
            for clip in inbox.glob(f"{name}-*"):
                clip.unlink(missing_ok=True)
        return removed


_ = (json, shutil)
