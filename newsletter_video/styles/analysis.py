"""analyze_video: the full read of one reference into analysis.json.

Order of work: probe; decode a 96 px thumbnail track and split it into
scenes with named transitions; per scene, a motion profile (bursts,
easing, stagger, drift, loops), a palette and the layout of its middle
keyframe; keyframes and a contact sheet to disk; the soundtrack (speech /
music / tempo / sync with cuts); then, when a model is available and not
switched off, the vision pass that maps each scene onto our vocabulary.
Everything measured is kept even when the vision pass fails, and the
whole thing runs in a few seconds per minute of video before the model.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio as audio_mod
from . import frames as fr
from . import media, segment, vision
from . import vocabulary as V

log = logging.getLogger("newsletter_video")

ANALYSIS_VERSION = 1
KEYFRAME_SIDE = 540  # long side of saved keyframes (readable, small on disk)
SHEET_COLS = 6
SHEET_CELL = 180


def _sha256_head(path: Path, limit: int = 32 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()[:16]


def contact_sheet(frames: list[np.ndarray], labels: list[str], out: Path, activity: list[float] | None = None,
                  cols: int = SHEET_COLS, cell_w: int = SHEET_CELL) -> Path:
    """One frame per scene in a grid with a label bar each, and the whole
    video's activity curve as a strip underneath."""
    from PIL import Image, ImageDraw

    cells = []
    for frame, label in zip(frames, labels):
        im = Image.fromarray(frame)
        im = im.resize((cell_w, max(1, int(im.height * cell_w / im.width))))
        cell = Image.new("RGB", (cell_w, im.height + 22), (255, 255, 255))
        cell.paste(im, (0, 0))
        d = ImageDraw.Draw(cell)
        d.rectangle([0, im.height, cell_w, im.height + 22], fill=(30, 30, 34))
        d.text((4, im.height + 5), label[:32], fill=(255, 255, 255))
        cells.append(cell)
    if not cells:
        cells = [Image.new("RGB", (cell_w, 100), (200, 200, 200))]
    ch = max(c.height for c in cells)
    rows = (len(cells) + cols - 1) // cols
    strip_h = 60 if activity else 0
    width = cell_w * min(cols, len(cells))
    sheet = Image.new("RGB", (width, ch * rows + strip_h), (240, 240, 238))
    for k, c in enumerate(cells):
        sheet.paste(c, ((k % cols) * cell_w, (k // cols) * ch))
    if activity:
        d = ImageDraw.Draw(sheet)
        y0 = ch * rows
        d.rectangle([0, y0, width, y0 + strip_h], fill=(250, 250, 248))
        a = np.asarray(activity, dtype=np.float64)
        if a.max() > 0:
            a = a / a.max()
        pts = [(int(i * (width - 1) / max(1, len(a) - 1)), y0 + strip_h - 4 - int(v * (strip_h - 10))) for i, v in enumerate(a)]
        if len(pts) > 1:
            d.line(pts, fill=(31, 73, 214), width=1)
        d.text((4, y0 + 2), "motion", fill=(90, 90, 90))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


def derive_tags(analysis: dict) -> list[str]:
    """Searchable tags from the measurements (the vision pass adds its own)."""
    tags: set[str] = set()
    cad = analysis.get("cadence", {})
    if cad.get("pace") == "fast":
        tags.add("fast-cuts")
    elif cad.get("pace") == "slow":
        tags.add("slow-build")
    for kind in cad.get("transition_mix", {}):
        tags.add({"cut": "cut", "fade": "fade", "dissolve": "fade", "push": "push-up", "wipe": "wipe-down"}.get(kind, kind))
    pal = analysis.get("palette", {})
    if pal.get("theme") in ("dark", "light"):
        tags.add(pal["theme"])
    if pal.get("colour_feel"):
        tags.add(pal["colour_feel"])
    if pal.get("contrast", 0) >= 7:
        tags.add("high-contrast")
    aud = analysis.get("audio", {})
    if aud.get("has_audio"):
        if aud.get("speech"):
            tags.add("narrated")
        if aud.get("music") and not aud.get("speech"):
            tags.add("music-only")
        if aud.get("sync") in ("beat-synced", "voice-synced"):
            tags.add(aud["sync"])
    scenes = analysis.get("scenes", [])
    if scenes:
        text_like = sum(1 for s in scenes if (s.get("layout") or {}).get("text_like"))
        if text_like >= 0.5 * len(scenes):
            tags.add("kinetic-type")
        if any((s.get("motion") or {}).get("loop_period") for s in scenes):
            tags.add("looping")
        if any((s.get("motion") or {}).get("stagger") for s in scenes):
            tags.add("list")
        sweeps = [e.get("sweep") for s in scenes for e in (s.get("motion") or {}).get("events", [])]
        if sum(1 for v in sweeps if v) >= 3:
            tags.add("line-draw")
        drifts = [abs((s.get("motion") or {}).get("drift", {}).get("dy", 0)) + abs((s.get("motion") or {}).get("drift", {}).get("dx", 0)) for s in scenes]
        if drifts and np.median(drifts) > 0.5:
            tags.add("camera-move")
    vis = analysis.get("vision") or {}
    overall = vis.get("overall") or {}
    tags.update(overall.get("feel", []))
    tags.update(t for t in overall.get("tags", []) if t)
    for d in (vis.get("scenes") or {}).values():
        tags.update(d.get("effects", []))
        if d.get("entrance", "default") != "default":
            tags.add(d["entrance"])
        if d.get("type_treatment", "default") != "default":
            tags.add(d["type_treatment"])
    return sorted(t for t in tags if t)


def summarize(analysis: dict) -> str:
    info, cad, pal, aud = analysis.get("info", {}), analysis.get("cadence", {}), analysis.get("palette", {}), analysis.get("audio", {})
    overall = (analysis.get("vision") or {}).get("overall") or {}
    if overall.get("summary"):
        return overall["summary"]
    parts = [f"{info.get('duration', 0):.0f} s {info.get('orientation', '')} video, {cad.get('scene_count', 0)} scenes "
             f"(median {cad.get('median_scene_s', 0)} s, {cad.get('pace', '')} pace)",
             f"{pal.get('theme', '')} {pal.get('colour_feel', '')} palette on {pal.get('background', '')}"
             + (f" with accent {pal['accent']}" if pal.get("accent") else "")]
    mix = cad.get("transition_mix", {})
    if mix:
        parts.append("transitions: " + ", ".join(f"{v} {k}" for k, v in mix.items()))
    if aud.get("has_audio"):
        parts.append(("narrated" if aud.get("speech") else "no speech") + (f", music at {aud['tempo_bpm']} BPM" if aud.get("tempo_bpm") else ", no clear beat")
                     + f", cuts {aud.get('sync', 'free')}")
    else:
        parts.append("silent")
    return "; ".join(parts) + "."


def analyze_video(path: Path, out_dir: Path, name: str | None = None, use_vision: bool | None = None,
                  call: Callable | None = None, keyframes: bool = True, vision_config: dict | None = None) -> dict:
    """Analyse one video into `out_dir` (analysis.json, keyframes/,
    contact_sheet.jpg). `use_vision=None` means "if a model is available";
    `call` injects a fake model for tests."""
    t0 = time.time()
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = media.probe(path)
    if info.duration <= 0 or info.width == 0:
        raise ValueError(f"{path.name}: not a readable video")
    thumbs = media.read_frames(path, segment.TRACK_FPS, segment.TRACK_W)
    track = segment.build_track(thumbs, segment.TRACK_FPS)
    scenes = segment.detect_scenes(track, duration=info.duration)
    activity = segment.activity_curve(track.gray)
    global_palette = fr.palette(thumbs[:: max(1, len(thumbs) // 120)])
    scene_dicts: list[dict] = []
    sheet_frames: list[np.ndarray] = []
    sheet_labels: list[str] = []
    key_dir = out_dir / "keyframes"
    for s in scenes:
        d = s.to_dict()
        mf = media.read_frames(path, 12.0, segment.MOTION_W, start=s.start, duration=max(0.2, s.duration))
        d["motion"] = segment.motion_profile(mf, 12.0, s.start)
        d["motion"].pop("activity", None)  # the global curve is kept; per-scene stats are enough
        lo, hi = int(s.start * segment.TRACK_FPS), max(int(s.start * segment.TRACK_FPS) + 1, int(s.end * segment.TRACK_FPS))
        d["palette"] = {k: v for k, v in fr.palette(thumbs[lo:hi:max(1, (hi - lo) // 12)]).items()
                        if k in ("colours", "background", "accent", "theme", "contrast")} if hi > lo else global_palette
        times = {"first": s.start + min(0.25, s.duration * 0.15), "mid": (s.start + s.end) / 2, "last": max(s.start, s.end - 0.2)}
        d["keyframes"] = []
        mid_frame = None
        for label, t in times.items():
            frame = media.frame_at(path, t, KEYFRAME_SIDE if info.width >= info.height else int(KEYFRAME_SIDE * info.width / info.height))
            if frame is None:
                continue
            if label == "mid":
                mid_frame = frame
            if keyframes:
                rel = f"keyframes/s{s.index:02d}_{label}.jpg"
                media.save_jpeg(frame, out_dir / rel)
                d["keyframes"].append(rel)
        if mid_frame is not None:
            lay = fr.layout(mid_frame, d["palette"].get("background"))
            d["layout"] = lay
            sheet_frames.append(mid_frame)
            sheet_labels.append(f"{s.index + 1} {s.start:.1f}s {s.transition_in}")
        scene_dicts.append(d)
    cadence = segment.cadence_stats(scenes, info.duration)
    samples = media.extract_audio(path) if info.has_audio else None
    event_times = [e["t0"] for d in scene_dicts for e in d["motion"].get("events", [])]
    aud = audio_mod.analyze_audio(samples, [s.start for s in scenes[1:]], event_times)
    aud["beats"] = aud.get("beats", [])[:200]
    aud["voice_onsets"] = aud.get("voice_onsets", [])[:200]
    analysis: dict = {
        "version": ANALYSIS_VERSION,
        "name": name or path.stem,
        "source": {"file": path.name, "sha256_head": _sha256_head(path)},
        "info": info.to_dict(),
        "scenes": scene_dicts,
        "activity": [round(float(v), 4) for v in activity],
        "activity_fps": segment.TRACK_FPS,
        "cadence": cadence,
        "palette": global_palette,
        "audio": aud,
        "vision": None,
        "seconds": 0.0,
    }
    sheet_path = out_dir / "contact_sheet.jpg"
    if keyframes and sheet_frames:
        contact_sheet(sheet_frames, sheet_labels, sheet_path, analysis["activity"])
        analysis["contact_sheet"] = sheet_path.name
    want_vision = vision.vision_available() if use_vision is None else use_vision
    if want_vision and (call is not None or vision.vision_available()):
        overview = {"info": analysis["info"], "cadence": cadence, "palette": {k: v for k, v in global_palette.items() if k != "colours"},
                    "audio": {k: v for k, v in aud.items() if k in ("speech", "music", "tempo_bpm", "sync")}}

        def frames_for(i: int):
            d = scene_dicts[i]
            out = []
            for rel in d.get("keyframes", []):
                label = rel.rsplit("_", 1)[-1].split(".")[0]
                if label not in ("first", "mid", "last"):
                    continue
                from PIL import Image

                im = np.asarray(Image.open(out_dir / rel).convert("RGB"))
                jpeg = media.jpeg_bytes(im, vision.FRAME_JPEG_QUALITY, vision.FRAME_MAX_SIDE)
                scale = min(1.0, vision.FRAME_MAX_SIDE / max(im.shape[:2]))
                out.append((label, jpeg, (int(im.shape[1] * scale), int(im.shape[0] * scale))))
            order = {"first": 0, "mid": 1, "last": 2}
            return sorted(out, key=lambda x: order[x[0]])

        def evidence_for(i: int):
            d = scene_dicts[i]
            m = d["motion"]
            return {"duration": d["duration"], "transition_in": d["transition_in"], "stagger": m.get("stagger"),
                    "loop_period": m.get("loop_period"), "continuous_share": m.get("continuous_share"), "drift": m.get("drift"),
                    "events": [{k: e[k] for k in ("t0", "t1", "kind", "easing", "sweep", "slide", "area")} for e in m.get("events", [])[:6]],
                    "palette": {k: d["palette"].get(k) for k in ("background", "accent", "theme")},
                    "layout": {k: d.get("layout", {}).get(k) for k in ("kind", "align", "fill", "text_like", "blobs")}}

        sheet = None
        if sheet_path.exists():
            from PIL import Image

            im = np.asarray(Image.open(sheet_path).convert("RGB"))
            sheet = (media.jpeg_bytes(im, 70, 1024), (min(1024, im.shape[1]), int(im.shape[0] * min(1.0, 1024 / im.shape[1]))))
        analysis["vision"] = vision.describe_scenes([{"index": d["index"], "start": d["start"], "end": d["end"], "transition_in": d["transition_in"]}
                                                     for d in scene_dicts], frames_for, evidence_for, overview, sheet, call=call, config=vision_config)
    analysis["tags"] = derive_tags(analysis)
    analysis["summary"] = summarize(analysis)
    analysis["cost_usd"] = (analysis["vision"] or {}).get("cost_usd", 0.0)
    analysis["seconds"] = round(time.time() - t0, 1)
    (out_dir / "analysis.json").write_text(json.dumps(analysis, indent=1, ensure_ascii=False))
    log.info("Analysed %s: %d scenes in %.1f s (vision: %s, est. $%.3f)", path.name, len(scenes), analysis["seconds"],
             "yes" if analysis["vision"] else "no", analysis["cost_usd"])
    return analysis


def light_analysis(path: Path, out_dir: Path) -> dict:
    """The measured part only (no model), for comparing a recreation."""
    return analyze_video(path, out_dir, use_vision=False, keyframes=True)


__all__ = ["analyze_video", "light_analysis", "derive_tags", "summarize", "contact_sheet", "V"]
