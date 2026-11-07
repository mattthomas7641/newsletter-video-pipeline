"""The style library on disk: one folder per reference under
STYLE_LIBRARY (default `style_library/`), with the derived analysis,
keyframes, recipes, recreation results and the owner's notes, and an index
(`library.json`) the planner and the CLI search.

Reference videos are copied in as `reference.<ext>` and gitignored; only
derived, original work is meant to be committed. Search is plain token
scoring over tags, effects, notes and summaries with the vocabulary's
synonyms, so it works offline and is predictable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import vocabulary as V
from .recipes import Recipe, builtin_recipes, recipes_from_analysis

log = logging.getLogger("newsletter_video")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LIBRARY = PROJECT_ROOT / "style_library"
INDEX_NAME = "library.json"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
OFF_VALUES = ("off", "0", "false", "no", "none")


def library_root() -> Path | None:
    """The library folder, or None when STYLE_LIBRARY is switched off."""
    raw = os.environ.get("STYLE_LIBRARY", "").strip()
    if raw.lower() in OFF_VALUES:
        return None
    if not raw:
        return DEFAULT_LIBRARY
    p = Path(raw).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:64]
    if not slug or not _SLUG.match(slug):
        raise ValueError(f"{name!r} does not make a usable name (letters, digits and dashes)")
    return slug


@dataclass
class Entry:
    name: str
    title: str = ""
    added: str = ""
    source_file: str = ""
    duration: float = 0.0
    scene_count: int = 0
    tags: list[str] = field(default_factory=list)
    user_tags: list[str] = field(default_factory=list)
    notes: str = ""
    summary: str = ""
    recipes: list[str] = field(default_factory=list)
    fidelity: float | None = None
    vision_used: bool = False
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        known = {k: d[k] for k in asdict(cls("x")) if k in d}
        return cls(**known)

    def search_text(self) -> str:
        return " ".join([self.name, self.title, " ".join(self.tags), " ".join(self.user_tags), self.notes, self.summary,
                         " ".join(self.recipes)]).lower()


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)

    # --- index ---------------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def entries(self) -> list[Entry]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("Style library index %s is unreadable; treating the library as empty", self.index_path)
            return []
        return [Entry.from_dict(e) for e in data.get("entries", []) if isinstance(e, dict) and e.get("name")]

    def save(self, entries: list[Entry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "entries": [e.to_dict() for e in sorted(entries, key=lambda e: e.name)]}
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
        tmp.replace(self.index_path)

    def get(self, name: str) -> Entry | None:
        return next((e for e in self.entries() if e.name == name), None)

    def entry_dir(self, name: str) -> Path:
        return self.root / name

    def is_empty(self) -> bool:
        return not self.entries()

    # --- files -------------------------------------------------------------------
    def reference_path(self, name: str) -> Path | None:
        d = self.entry_dir(name)
        if not d.exists():
            return None
        for p in sorted(d.iterdir()):
            if p.stem == "reference" and p.suffix.lower() in V_SUFFIXES:
                return p
        return None

    def analysis(self, name: str) -> dict | None:
        p = self.entry_dir(name) / "analysis.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def recipes_for(self, name: str) -> list[Recipe]:
        p = self.entry_dir(name) / "recipes.json"
        if not p.exists():
            return []
        try:
            return [Recipe.from_dict(r) for r in json.loads(p.read_text()).get("recipes", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return []

    def all_recipes(self, include_builtin: bool = True) -> dict[str, Recipe]:
        out: dict[str, Recipe] = dict(builtin_recipes()) if include_builtin else {}
        for e in self.entries():
            for r in self.recipes_for(e.name):
                out[r.id] = r
        return out

    # --- mutations ----------------------------------------------------------------
    def upsert(self, entry: Entry) -> None:
        entries = [e for e in self.entries() if e.name != entry.name] + [entry]
        self.save(entries)

    def remove(self, name: str) -> bool:
        entries = self.entries()
        if not any(e.name == name for e in entries):
            return False
        self.save([e for e in entries if e.name != name])
        d = self.entry_dir(name)
        if d.exists():
            shutil.rmtree(d)
        return True

    def write_recipes(self, name: str, recipes: list[Recipe]) -> None:
        (self.entry_dir(name)).mkdir(parents=True, exist_ok=True)
        (self.entry_dir(name) / "recipes.json").write_text(json.dumps({"recipes": [r.to_dict() for r in recipes]}, indent=1, ensure_ascii=False))

    def write_readme(self, entry: Entry, analysis: dict, recipes: list[Recipe], recreation: dict | None) -> Path:
        cad, pal, aud = analysis.get("cadence", {}), analysis.get("palette", {}), analysis.get("audio", {})
        lines = [f"# {entry.title or entry.name}", "", entry.summary or analysis.get("summary", ""), ""]
        if entry.notes:
            lines += ["## Notes", "", entry.notes, ""]
        lines += ["## Measured", "",
                  f"- {analysis.get('info', {}).get('duration', 0):.1f} s, {analysis.get('info', {}).get('width')}x{analysis.get('info', {}).get('height')}, "
                  f"{cad.get('scene_count', 0)} scenes, median scene {cad.get('median_scene_s', 0)} s ({cad.get('pace', '')} pace)",
                  f"- transitions: {', '.join(f'{v} {k}' for k, v in cad.get('transition_mix', {}).items()) or 'none detected'}",
                  f"- palette: {pal.get('theme', '')} ground {pal.get('background', '')}, accent {pal.get('accent') or 'none'}, "
                  f"{pal.get('colour_feel', '')}, contrast {pal.get('contrast', 0)}:1",
                  f"- audio: " + ("speech" if aud.get("speech") else "no speech") + (f", music ~{aud['tempo_bpm']} BPM" if aud.get("tempo_bpm") else "")
                  + f", cuts {aud.get('sync', 'n/a')}" if aud.get("has_audio") else "- audio: none", ""]
        vis = analysis.get("vision") or {}
        if vis.get("overall"):
            o = vis["overall"]
            lines += ["## What the model saw", "", f"- Typography: {o.get('typography', '')}", f"- Colour: {o.get('colour', '')}",
                      f"- Motion: {o.get('motion', '')}", f"- Pacing: {o.get('pacing', '')}", f"- Reuse: {o.get('reuse', '')}", ""]
        lines += ["## Scenes", "", "| # | time | in | type | entrance | hold | notes |", "|---|---|---|---|---|---|---|"]
        vis_scenes = {int(k): v for k, v in (vis.get("scenes") or {}).items()}
        for s in analysis.get("scenes", []):
            v = vis_scenes.get(s["index"], {})
            lines.append(f"| {s['index'] + 1} | {s['start']:.1f}-{s['end']:.1f} | {s['transition_in']} | {v.get('scene_type', '?')} | "
                         f"{v.get('entrance', '?')} | {v.get('hold', '?')} | {v.get('what', '')} |")
        lines += ["", "## Recipes", ""]
        for r in recipes:
            lines.append(f"- `{r.id}`: {r.description} Knobs: {json.dumps(r.knobs)}. Use for: {r.when_to_use}")
        if recreation:
            lines += ["", "## Recreation", "",
                      f"Fidelity {recreation.get('score', 0):.2f} (cuts F1 {recreation.get('cuts', {}).get('f1', 0)}, motion "
                      f"{recreation.get('motion_correlation', 0)}, transitions {recreation.get('transitions', {}).get('agreement', 0)}, "
                      f"palette {recreation.get('palette_similarity', 0)}, layout {recreation.get('layout_similarity', 0)}) after "
                      f"{recreation.get('rounds', 1)} round(s). See recreation/side_by_side.jpg.", ""]
        lines += ["", f"Tags: {', '.join(entry.tags + entry.user_tags)}", ""]
        p = self.entry_dir(entry.name) / "README.md"
        p.write_text("\n".join(lines))
        return p

    # --- search -------------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[tuple[float, Entry, list[Recipe]]]:
        """Entries (and their matching recipes) ranked by how many query
        tokens (with synonyms) hit their tags, notes, summary and recipes."""
        tokens = V.expand_query(query)
        if not tokens:
            return []
        results = []
        for e in self.entries():
            text = e.search_text()
            tag_set = set(e.tags) | set(e.user_tags)
            score = 0.0
            for t in tokens:
                if t in tag_set:
                    score += 2.0
                elif t in text:
                    score += 1.0
            recipes = [r for r in self.recipes_for(e.name) if any(t in r.search_text() for t in tokens)]
            score += 0.5 * len(recipes)
            if score > 0:
                results.append((score, e, recipes))
        results.sort(key=lambda r: (-r[0], r[1].name))
        return results[:limit]


V_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gif")


def open_library(create: bool = False) -> Library | None:
    root = library_root()
    if root is None:
        return None
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return Library(root)


def library_enabled() -> bool:
    """True when the library is switched on and has at least one entry:
    the condition under which the planner may change how videos look."""
    lib = open_library()
    return bool(lib and lib.root.exists() and not lib.is_empty())


def add_reference(video: Path, name: str, notes: str = "", tags: list[str] | None = None, recreate: bool = True,
                  use_vision: bool | None = None, call: Callable | None = None, rounds: int | None = None,
                  lib: Library | None = None, copy: bool = True) -> Entry:
    """Analyse (and by default recreate) one reference into the library."""
    from .analysis import analyze_video

    lib = lib or open_library(create=True)
    if lib is None:
        raise RuntimeError("STYLE_LIBRARY is switched off")
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    slug = slugify(name)
    d = lib.entry_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    ref = d / f"reference{video.suffix.lower()}"
    if copy and video.resolve() != ref.resolve():
        shutil.copy2(video, ref)
    analysis = analyze_video(ref if copy else video, d, name=slug, use_vision=use_vision, call=call)
    entry = Entry(name=slug, title=name.strip(), added=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  source_file=video.name, duration=analysis["info"]["duration"], scene_count=len(analysis["scenes"]),
                  tags=analysis.get("tags", []), user_tags=[t.strip().lower() for t in (tags or []) if t.strip()],
                  notes=notes.strip(), summary=analysis.get("summary", ""), vision_used=bool(analysis.get("vision")),
                  cost_usd=float(analysis.get("cost_usd", 0.0)))
    meta = {"name": slug, "title": entry.title, "notes": entry.notes, "user_tags": entry.user_tags, "added": entry.added,
            "source_file": video.name}
    (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    recreation = None
    if recreate:
        from .recreate import recreate_reference

        try:
            recreation = recreate_reference(lib, slug, analysis, call=call, rounds=rounds)
            analysis["recreation"] = recreation
            entry.fidelity = recreation.get("score")
            entry.cost_usd = round(entry.cost_usd + float(recreation.get("cost_usd", 0.0)), 4)
            (d / "analysis.json").write_text(json.dumps(analysis, indent=1, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - a failed recreation leaves a usable analysis
            log.error("Recreation of %s failed: %s: %s", slug, type(exc).__name__, exc)
            recreation = {"error": f"{type(exc).__name__}: {exc}"}
    recipes = recipes_from_analysis(slug, analysis)
    lib.write_recipes(slug, recipes)
    entry.recipes = [r.id for r in recipes]
    lib.write_readme(entry, analysis, recipes, recreation if recreation and "score" in recreation else None)
    lib.upsert(entry)
    return entry


from typing import Callable  # noqa: E402  (used in signatures above)
