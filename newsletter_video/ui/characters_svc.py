"""Characters for the panel: listing (with the built-in Default), voice
samples, Voicebox lookups and the add-character flow. All heavy work (TTS,
voice cloning) runs in a worker process through the job manager."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import security
from .config import UIPaths
from .envstore import EnvStore
from .jobs import JobManager, JobView
from .runs import RunBlocked
from .workers import SAMPLE_TEXT

VOICEBOX_URL = "http://localhost:17493"


def voicebox_get(path: str, timeout: float = 2.0):
    with urllib.request.urlopen(f"{VOICEBOX_URL}{path}", timeout=timeout) as resp:
        return json.load(resp)


class CharacterService:
    def __init__(self, paths: UIPaths, jobs: JobManager, store: EnvStore,
                 voicebox: Callable[[str], object] = voicebox_get):
        self.paths = paths
        self.jobs = jobs
        self.store = store
        self.voicebox = voicebox

    # --- listing ---

    def _characters(self):
        from newsletter_video import characters

        return characters.list_characters()

    def legacy_avatar(self) -> Path | None:
        folder = self.paths.legacy_character_dir
        if not folder.is_dir():
            return None
        images = sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg"))
        return images[0] if images else None

    def list(self) -> list[dict]:
        provider = self.store.read_only().get("TTS_PROVIDER") or "kokoro"
        if provider == "voicebox" and self.store.has_value("VOICEBOX_PROFILE_ID"):
            default_voice = {"kind": "voicebox", "label": "Your Voicebox voice (from settings)"}
        elif provider == "voicebox":
            default_voice = {"kind": "missing", "label": "No Voicebox voice chosen yet"}
        else:
            default_voice = {"kind": "standard", "label": "Standard narrator voice"}
        items = [{
            "slug": "", "name": "Default", "is_default": True,
            "avatar_url": f"/media/avatar/default?v={self._mtime(self.legacy_avatar())}" if self.legacy_avatar() else None,
            "voice": default_voice, "persona": "",
            "note": "The original setup: the image in assets/character and the voice from your settings.",
        }]
        for c in self._characters():
            cloned = bool(c.voicebox_profile_id)
            items.append({
                "slug": c.slug, "name": c.name, "is_default": False,
                "avatar_url": f"/media/avatar/{c.slug}?v={self._mtime(c.avatar)}" if c.avatar else None,
                "voice": {"kind": "cloned" if cloned else "standard",
                          "label": "Cloned voice attached" if cloned else "Standard narrator voice"},
                "persona": c.persona or "",
            })
        return items

    @staticmethod
    def _mtime(path: Path | None) -> int:
        try:
            return int(path.stat().st_mtime) if path else 0
        except OSError:
            return 0

    def avatar_path(self, slug: str) -> Path | None:
        if slug == "default":
            return self.legacy_avatar()
        if not security.is_slug(slug):
            return None
        for c in self._characters():
            if c.slug == slug and c.avatar:
                base = self.paths.characters_dir.resolve()
                resolved = c.avatar.resolve()
                if base in resolved.parents and resolved.is_file():
                    return resolved
        return None

    # --- Voicebox ---

    def voicebox_status(self) -> dict:
        try:
            self.voicebox("/health")
            return {"running": True}
        except Exception:  # noqa: BLE001 - any failure means "not reachable"
            return {"running": False}

    def voicebox_profiles(self) -> dict:
        try:
            data = self.voicebox("/profiles")
        except Exception:  # noqa: BLE001
            return {"available": False, "profiles": []}
        rows = data if isinstance(data, list) else (data.get("profiles") or data.get("items") or []
                                                    if isinstance(data, dict) else [])
        profiles = []
        for row in rows[:200]:
            if isinstance(row, dict) and row.get("id"):
                profiles.append({"id": str(row["id"])[:80],
                                 "name": security.clean_display_name(str(row.get("name") or row["id"]), 60)})
        return {"available": True, "profiles": profiles}

    # --- voice samples ---

    def _fingerprint(self, slug: str) -> str:
        manifest = self.paths.characters_dir / slug / "character.json"
        parts = [slug]
        for path in (manifest, self.paths.env_path):
            try:
                stat = path.stat()
                parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                parts.append("-")
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def start_sample(self, slug: str) -> dict:
        known = {c["slug"] or "default" for c in self.list()}
        slug = slug or "default"
        if slug not in known:
            raise KeyError(slug)
        name = f"{slug}-{self._fingerprint('' if slug == 'default' else slug)}.wav"
        target = self.paths.state_dir / "samples" / name
        if target.is_file() and target.stat().st_size > 0:
            return {"state": "ready", "url": f"/media/sample/{name}", "job": None}
        running = self.jobs.active(kinds=("sample",))
        if running:
            if running[0].meta.get("file") == name:
                return {"state": "running", "url": None, "job": running[0].to_json()}
            raise RunBlocked("Another voice sample is still being generated. Try again in a moment.")
        env = dict(os.environ, BYTESIZE_ENV_FILE=str(self.paths.env_path))
        argv = [sys.executable, "-m", "newsletter_video.ui.workers", "sample",
                "--slug", slug, "--out", str(target), "--text", SAMPLE_TEXT]
        job = self.jobs.create("sample", argv, env, {"slug": slug, "file": name,
                                                      "url": f"/media/sample/{name}"})
        return {"state": "running", "url": None, "job": job.to_json()}

    def sample_path(self, name: str) -> Path | None:
        import re

        if not re.fullmatch(r"[a-z0-9-]{1,64}-[0-9a-f]{12}\.wav", name):
            return None
        path = self.paths.state_dir / "samples" / name
        return path if path.is_file() else None

    # --- adding ---

    def start_add(self, fields: dict[str, str], avatar: bytes, clips: list[tuple[str, bytes]]) -> JobView:
        from newsletter_video import characters

        name = security.clean_display_name(fields.get("name", ""))
        slug = characters.slugify(name)
        if not slug or not security.is_slug(slug):
            raise security.UploadError("Give the character a name with letters or numbers in it.")
        if (self.paths.characters_dir / slug / characters.MANIFEST).exists():
            raise security.UploadError(f"A character called \"{name}\" already exists. Pick a different name.")
        info = security.validate_image(avatar)
        mode = fields.get("voice_mode", "default")
        profile_id = (fields.get("profile_id") or "").strip()
        if mode == "clone":
            if not clips:
                raise security.UploadError("Add at least one voice clip to clone, or choose another voice option.")
            if len(clips) > security.MAX_AUDIO_CLIPS:
                raise security.UploadError(f"Use at most {security.MAX_AUDIO_CLIPS} voice clips.")
        elif mode == "profile":
            import re

            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile_id):
                raise security.UploadError("Choose one of your Voicebox voices.")
            clips = []
        elif mode == "default":
            clips, profile_id = [], ""
        else:
            raise security.UploadError("Unknown voice option.")
        if self.jobs.active(kinds=("add",)):
            raise RunBlocked("A character is already being added. Wait for it to finish.")

        job_id = self.jobs.new_id()
        folder = self.paths.state_dir / "uploads" / job_id
        files = folder / "files"
        files.mkdir(parents=True, exist_ok=True)
        try:
            ext = {"png": "png", "jpeg": "jpg", "webp": "webp"}[info["kind"]]
            avatar_path = files / f"avatar.{ext}"
            avatar_path.write_bytes(avatar)
            clip_paths = []
            for i, (_label, data) in enumerate(clips):
                kind = security.validate_audio(data, f"voice clip {i + 1}")
                clip_path = files / f"clip{i}.{kind}"
                clip_path.write_bytes(data)
                clip_paths.append(str(clip_path))
            spec = {"name": name, "avatar": str(avatar_path), "voice_audio": clip_paths,
                    "profile_id": profile_id if mode == "profile" else "",
                    "persona": security.clean_persona(fields.get("persona", ""))}
            (folder / "spec.json").write_text(json.dumps(spec))
            env = dict(os.environ, BYTESIZE_ENV_FILE=str(self.paths.env_path))
            argv = [sys.executable, "-m", "newsletter_video.ui.workers", "add", "--spec", str(folder / "spec.json")]
            return self.jobs.create("add", argv, env, {"name": name, "slug": slug, "upload_id": job_id,
                                                        "voice_mode": mode,
                                                        "no_transparency": not info["has_transparency"]},
                                    job_id=job_id)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def add_outcome(self, job: JobView) -> dict | None:
        upload_id = job.meta.get("upload_id", "")
        if not security.JOB_ID.match(upload_id):
            return None
        try:
            return json.loads((self.paths.state_dir / "uploads" / upload_id / "outcome.json").read_text())
        except (OSError, ValueError):
            return None
