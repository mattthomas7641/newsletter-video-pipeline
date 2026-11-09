"""The lineup through the panel: reading and saving lineup.json, expanding a
draft lineup into slots for the strip, trying a format on a sample story, and
the read-only results (which format each uploaded video was).

The file and its rules live in newsletter_video/lineup.py (the unattended
daily run reads the same file); this module only adds what the panel needs:
the next run's date, the avatars that exist, and keeping VIDEOS_PER_DAY in
sync with the lineup's total.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import lineup as lu
from ..colors import DEFAULT_ROTATION


class LineupService:
    def __init__(self, paths, store, characters, plans, schedule):
        self.paths = paths
        self.store = store
        self.characters = characters
        self.plans = plans
        self._schedule = schedule  # callable -> {"time": "07:30" | None, ...}

    @property
    def path(self) -> Path:
        given = os.environ.get("BYTESIZE_LINEUP_FILE", "").strip()
        return Path(given) if given else self.paths.root / lu.FILE_NAME

    def _avatars(self) -> set[str]:
        return {c["slug"] for c in self.characters.list()}

    def next_run(self, now: datetime | None = None) -> dict:
        """The date (and time of day) of the next automatic run: today while its time has not passed, else tomorrow."""
        now = now or datetime.now()
        time = (self._schedule() or {}).get("time")
        when = date.today()
        if time:
            try:
                hh, mm = (int(x) for x in time.split(":"))
                if now.hour * 60 + now.minute >= hh * 60 + mm:
                    when = now.date() + timedelta(days=1)
            except ValueError:
                pass
        return {"date": when.isoformat(), "time": time}

    def get(self) -> dict:
        doc = lu.load(self.path)
        nxt = self.next_run()
        saved = doc is not None
        base = doc or lu.default_from_environ({**os.environ, **self.store.read()["values"]})
        once = base.get("once") if saved else None
        if once and once["date"] < date.today().isoformat():
            once = None  # a one-off for a day that has passed is spent
        active = {k: (once or base)[k] for k in ("total", "order", "batches", "slots")}
        which = "once" if once and once["date"] == nxt["date"] else "default"
        if which == "default":
            active = {k: base[k] for k in ("total", "order", "batches", "slots")}
        day = date.fromisoformat(nxt["date"])
        return {
            "saved": saved, "lineup": {k: base[k] for k in ("total", "order", "batches", "slots")},
            "once": ({k: once[k] for k in ("date", "total", "order", "batches", "slots")} if once else None),
            "next_run": {**nxt, "uses": which, "total": active["total"], "slots": lu.expand(active, day)},
            "avatars": [{"slug": c["slug"], "name": c.get("name") or "Default avatar", "avatar_url": c.get("avatar_url")}
                        for c in self.characters.list()],
            "rotation_default": list(DEFAULT_ROTATION), "max_total": lu.MAX_TOTAL, "max_batches": lu.MAX_BATCHES,
        }

    def expand(self, raw: dict, day_iso: str | None = None) -> dict:
        try:
            clean = lu.validate({**raw, "version": 1}, self._avatars())
        except lu.LineupError as exc:
            return {"ok": False, "errors": exc.errors}
        day = date.fromisoformat(day_iso) if day_iso else date.fromisoformat(self.next_run()["date"])
        core = {k: clean[k] for k in ("total", "order", "batches", "slots")}
        return {"ok": True, "total": core["total"], "slots": lu.expand(core, day)}

    def save(self, raw: dict, once: bool) -> dict:
        """Saves the standing lineup, or (once) a one-off for the next run that leaves the standing one alone."""
        core = lu.validate({**raw, "version": 1}, self._avatars())
        existing = lu.load(self.path)
        if once:
            standing = existing or lu.validate(lu.default_from_environ({**os.environ, **self.store.read()["values"]}))
            doc = {**{k: standing[k] for k in ("total", "order", "batches", "slots")},
                   "once": {"date": self.next_run()["date"], **{k: core[k] for k in ("total", "order", "batches", "slots")}}}
        else:
            doc = {k: core[k] for k in ("total", "order", "batches", "slots")}  # saving the standing lineup clears a pending one-off
            self.store.write({"VIDEOS_PER_DAY": str(core["total"])})  # the fallback key follows the lineup
        lu.save(doc, self.path, self._avatars())
        return self.get()

    def clear_once(self) -> dict:
        doc = lu.load(self.path)
        if doc and doc.get("once"):
            doc.pop("once")
            lu.save(doc, self.path)
        return self.get()

    # --- try a format ---

    def sample(self) -> tuple[str, str] | None:
        """(plan ref, video id) of the newest planned video that has a script to preview, or None."""
        for card in self.plans.list_plans(date.today()):
            try:
                plan = self.plans.describe(card["ref"])
            except KeyError:
                continue
            for v in plan["videos"]:
                return card["ref"], v["id"]
        return None

    # --- results ---

    def results(self, today: date | None = None, days: int = 14) -> dict:
        """The last `days` days of uploaded videos grouped by format, newest first. Read-only; views are not fetched."""
        today = today or date.today()
        groups: dict[str, dict] = {}
        total = 0
        for offset in range(days):
            day = today - timedelta(days=offset)
            folder = self.paths.output_dir / day.isoformat()
            try:
                uploads = json.loads((folder / "uploads.json").read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(uploads, dict):
                continue
            for name, rec in sorted(uploads.items()):
                if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                    continue
                try:
                    meta = json.loads((folder / name).with_suffix(".json").read_text())
                except (OSError, ValueError):
                    meta = {}
                info = meta.get("lineup") if isinstance(meta, dict) else None
                look = (meta.get("look") if isinstance(meta, dict) else None) or {}
                label = lu.format_label(info if isinstance(info, dict) else {k: rec.get(k) for k in ("format", "character") if rec.get(k)} or None, look)
                title = str((meta.get("script") or {}).get("title") or name)[:120] if isinstance(meta, dict) else name
                g = groups.setdefault(label, {"format": label, "videos": []})
                g["videos"].append({"date": day.isoformat(), "title": title, "id": rec["id"], "url": f"https://www.youtube.com/shorts/{rec['id']}",
                                    "slot": (info or {}).get("slot") or rec.get("slot"), "accent": (info or {}).get("accent") or look.get("accent") or "",
                                    "accent_mode": (info or {}).get("accent_mode") or "", "bg": (info or {}).get("bg") or look.get("bg") or ""})
                total += 1
        ordered = sorted(groups.values(), key=lambda g: (-len(g["videos"]), g["format"]))
        for g in ordered:
            g["count"] = len(g["videos"])
        return {"days": days, "total": total, "groups": ordered}
