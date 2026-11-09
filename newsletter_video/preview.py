"""Fast design previews from a saved plan, in three tiers. None of them
calls the writer (no model cost) and none calls the voice unless asked.

    stills   one frame per scene, at the moment the scene is fully built,
             combined into a labelled storyboard (target under ~20 s)
    draft    a low-resolution, low-frame-rate render of the whole video
             with cached audio when there is some, else a silent track on
             estimated timing (target ~30-60 s for a ~40 s video)
    final    the real thing: full quality, voiced; only ever on --final

    python -m newsletter_video.preview output/2026-11-07/10_x.json                  # stills (default)
    python -m newsletter_video.preview output/2026-11-07/10_x.json --preview draft
    python -m newsletter_video.preview output/2026-11-07 --video v02 --preview draft --voice
    python -m newsletter_video.preview output/2026-11-07/10_x.json --final --out /tmp/x.mp4
    python -m newsletter_video.preview ... --style avatar --theme dark --recipe none   # try a look
    python -m newsletter_video.preview output/2026-11-07 --video v02 --studio --port 3102   # live Studio

`--studio` prepares the same work copy as the stills (resolved look, cached
or estimated timing, no voice) and serves it with HyperFrames Studio on
127.0.0.1 until the process is stopped; nothing is rendered or written
outside `_preview/<video>/_studio`.

The source is a rendered video's sidecar (<video>.json) or a planned video
in a plan.json (plan_manifest.py). Timing comes from cached word alignment
when the video has been voiced before (the day's narration under
output/_audio, or an earlier voiced preview); otherwise from a word-count
estimate, and the storyboard says "estimated timing" so nobody mistakes it
for the final cut. Both styles (explainer and avatar) are supported: the
right composition is prepared exactly as the builders prepare it, with the
story variable baked into the work copy of index.html so HyperFrames'
`snapshot` (which takes no variables) and `render` see the same thing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import explainer_builder as eb
from . import plan_manifest as pm
from . import video_builder as vb
from .look import LookError, applied, describe, resolve_look
from .script_writer import Script
from .tts import HYPERFRAMES_VERSION, hyperframes_env, npx_path

log = logging.getLogger("newsletter_video")

TIERS = ("stills", "draft")
WORDS_PER_SECOND = 2.5  # the voice's pace at the pipeline's default speed (see script_writer)
LINE_PAUSE = 0.3  # a beat between spoken lines
ONSET = 0.15
DRAFT_FPS = 12
DRAFT_QUALITY = "draft"
PREVIEW_DIRNAME = "_preview"
AUDIO_CACHE_DIRNAME = "_preview_audio"
STUDIO_DIRNAME = "_studio"  # the live work copy, under _preview/<video>/
STUDIO_HOST = "127.0.0.1"
SNAPSHOT_TIMEOUT = 240
DRAFT_TIMEOUT = 900
FRAME_W = 270  # storyboard tile width (a 1080x1920 frame at 1/4)
LABEL_H = 54
COLUMNS = 4
PREVIEW_VERSION = 1


@dataclass
class Source:
    """What to preview: a script with a resolved look and where results go."""

    script: Script
    look: dict
    folder: Path  # where previews and the audio cache live
    key: str  # stable id for files, e.g. "v02" or "10_tsmc-..."
    day: date
    origin: str  # "sidecar" | "plan"
    audio_hint: Path | None = None  # a known narration file for this exact script
    cached_timing: bool = False
    fingerprint: str = ""  # of script + plan + look overrides: a stale preview is hidden
    trial_look: dict | None = None  # a trial: the look this preview shows INSTEAD of the saved one (not saved)

    @property
    def lines(self) -> list[str]:
        plan = getattr(self.script, "plan", None)
        if plan is not None and self.look.get("style") == "explainer":
            return plan.lines
        if self.look.get("style") == "avx":
            return [s["say"] for s in self.avx_scenes]
        return [self.script.hook] + [b.say for b in self.script.beats] + [self.script.outro]

    @property
    def avx_scenes(self) -> list[dict]:
        """The avatar explainer scenes coloured for this look (its own list, or a structural one)."""
        from .avx_builder import apply_look
        from .avx_writer import scenes_for

        return apply_look(scenes_for(self.script), self.look.get("bg") or "", self.look.get("accent") or "")

    @property
    def kicker(self) -> str:
        return f"TLDR · {self.day:%b} {self.day.day}".upper()

    @property
    def out_dir(self) -> Path:
        return self.folder / PREVIEW_DIRNAME / self.key


# --- sources -------------------------------------------------------------------------

def _day_of(folder: Path) -> date:
    try:
        return date.fromisoformat(folder.name)
    except ValueError:
        return date.today()


def _saved_look(saved: dict | None, overrides: dict | None) -> dict | None:
    """A video's saved look with what an explicit override replaces taken out:
    naming another preset (or a recipe or theme) on the command line must win
    over the saved one, including the theme and recipe the saved preset had
    pinned, so the new preset supplies its own."""
    if not saved or not overrides:
        return saved
    out = dict(saved)
    if "preset" in overrides:
        for key in ("preset", "theme", "recipe", "bg", "accent"):
            if key not in overrides:
                out.pop(key, None)
    return out


def source_from_sidecar(path: Path, overrides: dict | None = None) -> Source:
    meta = json.loads(Path(path).read_text())
    script = pm.script_from_sidecar(meta)
    look = resolve_look(_saved_look(script.look, overrides), overrides)
    script.look = {k: v for k, v in look.items()}
    folder = Path(path).resolve().parent
    hint = None
    m = re.match(r"^(\d{2,})_", Path(path).stem)
    if m:
        candidate = folder.parent / "_audio" / folder.name / f"{m.group(1)}.wav"
        hint = candidate if candidate.is_file() else None
    return Source(script, look, folder, Path(path).stem, _day_of(folder), "sidecar", hint,
                  fingerprint=pm.sidecar_fingerprint(meta) if not overrides else "")


def source_from_plan(folder: Path, vid: str, overrides: dict | None = None, trial: bool = False) -> Source:
    data = pm.read_manifest(folder)
    if data is None:
        raise pm.ManifestError(f"No plan in {folder}")
    entry = pm.get_entry(data, vid)
    script = pm.script_from_entry(entry)
    if trial:
        # A trial look REPLACES the saved one for this preview and is not saved: the fingerprint is that of the
        # video as it would be if the trial look were saved, so "use this style" keeps the preview valid.
        from .look import compact, validate_look

        trial_look = compact(validate_look(overrides or {}))
        look = resolve_look(overrides, index=pm._position(entry))
        script.look = dict(look)
        try:
            day = date.fromisoformat(data.get("date", ""))
        except ValueError:
            day = _day_of(folder)
        return Source(script, look, Path(folder).resolve(), vid, day, "plan",
                      fingerprint=pm.fingerprint(entry.get("script"), entry.get("scene_plan"), trial_look), trial_look=trial_look)
    look = resolve_look(_saved_look(entry.get("look"), overrides), overrides, index=pm._position(entry))
    script.look = dict(look)
    try:
        day = date.fromisoformat(data.get("date", ""))
    except ValueError:
        day = _day_of(folder)
    return Source(script, look, Path(folder).resolve(), vid, day, "plan",
                  fingerprint=pm.entry_fingerprint(entry) if not overrides else "")


def apply_look_to_plan(source: Source) -> None:
    """The look's recipe policy, applied to the scene plan (free)."""
    plan = getattr(source.script, "plan", None)
    if plan is not None and source.look.get("recipe"):
        from .styles.planner import apply_recipe_policy

        apply_recipe_policy(plan, source.look["recipe"])


# --- timing ------------------------------------------------------------------------------

def estimate_timing(lines: list[str]) -> tuple[list[list[tuple[str, float]]], float]:
    """Word times from word counts alone: WORDS_PER_SECOND plus a pause per
    line, spread by vb._word_weight so long words and numbers take longer."""
    words = sum(len(line.split()) for line in lines)
    speech = ONSET + words / WORDS_PER_SECOND + LINE_PAUSE * max(0, len(lines) - 1)
    per_line = vb._proportional_word_times(lines, ONSET, speech - 0.2)
    return per_line, round(speech, 3)


def _lines_key(lines: list[str]) -> str:
    return hashlib.sha256(json.dumps(lines).encode()).hexdigest()[:16]


def cached_timing(source: Source) -> tuple[Path, list[list[tuple[str, float]]], float] | None:
    """(wav, per_line, duration) for these exact lines if the voice track
    exists already: an earlier voiced preview, or the day's narration file
    (aligned locally once, then cached). Never synthesises."""
    cache = source.folder / AUDIO_CACHE_DIRNAME
    key = _lines_key(source.lines)
    wav, meta = cache / f"{key}.wav", cache / f"{key}.json"
    if wav.is_file() and meta.is_file():
        try:
            data = json.loads(meta.read_text())
            if data.get("lines") == source.lines:
                return wav, [[(w, t) for w, t in line] for line in data["per_line"]], float(data["duration"])
        except (ValueError, KeyError, TypeError):
            pass
    if source.audio_hint and source.audio_hint.is_file():
        try:
            per_line = vb._align_script(source.lines, source.audio_hint)
            _, _, length = vb._speech_span(source.audio_hint)
        except Exception as exc:  # noqa: BLE001 - alignment is a convenience
            log.warning("Could not align the saved narration (%s); using estimated timing", exc)
            return None
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy(source.audio_hint, wav)
        meta.write_text(json.dumps({"lines": source.lines, "per_line": per_line, "duration": length}))
        return wav, per_line, length
    return None


def voice_timing(source: Source) -> tuple[Path, list[list[tuple[str, float]]], float]:
    """Voices the lines (TTS, possibly Voicebox) and caches the result.
    Only called for an explicit "with voice" preview."""
    cache = source.folder / AUDIO_CACHE_DIRNAME
    cache.mkdir(parents=True, exist_ok=True)
    key = _lines_key(source.lines)
    wav, meta = cache / f"{key}.wav", cache / f"{key}.json"
    with applied(source.look):
        per_line, duration = eb._voice_paced(source.lines, wav, None)
    meta.write_text(json.dumps({"lines": source.lines, "per_line": per_line, "duration": duration}))
    return wav, per_line, duration


# --- payload and work dir -----------------------------------------------------------------

def build_payload(source: Source, per_line, speech: float) -> dict:
    style = source.look["style"]
    if style == "avx":
        from . import avx_builder

        return avx_builder.build_payload(source.avx_scenes, per_line, speech)
    if style == "explainer":
        plan = eb.plan_for(source.script)
        tail = min(eb.EXPLAINER_MAX_TAIL, max(vb.TAIL_SECONDS, vb.MIN_VIDEO_SECONDS + 0.2 - speech))
        from .look import world_of

        with applied(source.look):  # LOOK_BG / LOOK_ACCENT reach the payload the way the daily render sees them
            return eb.story_payload(plan, per_line, speech, source.kicker, tail, source.look.get("theme", "light"),
                                    world=world_of(source.look) or None)
    tail = min(vb.MAX_TAIL_SECONDS, max(vb.TAIL_SECONDS, vb.MIN_VIDEO_SECONDS + 0.2 - speech))
    return vb._story_payload(source.script, per_line, speech, source.kicker, tail)


def inject_story(index_html: Path, story: dict) -> None:
    """Bake the story into the composition's declared variable default, so
    `snapshot` and Studio (which take no variables) show this video."""
    text = index_html.read_text(encoding="utf-8")
    declared = json.dumps([{"id": "story", "type": "string", "label": "Story JSON", "default": json.dumps(story)}])
    attr = declared.replace("&", "&amp;").replace("'", "&#39;")
    new, n = re.subn(r"data-composition-variables='[\s\S]*?\]'", "data-composition-variables='" + attr + "'", text, count=1)
    if n != 1:
        raise RuntimeError(f"{index_html}: could not find the composition's variable declaration")
    index_html.write_text(new, encoding="utf-8")


def prepare_workdir(source: Source, payload: dict, audio: Path | None, work_dir: Path) -> Path:
    """A render-ready copy of the right template: assets in place and the
    story baked in (and written to story.json for `render`)."""
    style = source.look["style"]
    if style == "explainer":
        eb._prepare_workdir(work_dir)
    elif style == "avx":
        from . import avx_builder

        avx_builder._prepare_workdir(work_dir)
        with applied(source.look):  # CHARACTER: whose poses
            payload["avatar"] = avx_builder._avatar_assets(work_dir / "assets", f"{source.day.isoformat()}-{source.key}",
                                                           [s["start"] for s in payload["scenes"]],
                                                           [s.get("pose") for s in source.avx_scenes])
    else:
        vb._prepare_workdir(work_dir)
    assets = work_dir / "assets"
    if audio is not None:
        vb._pad_audio(audio, assets / "voice.wav", max(0.0, payload["duration"] - _length(audio)))
    else:
        from .styles.media import write_silence

        write_silence(assets / "voice.wav", payload["duration"])
    if style == "avatar":
        with applied(source.look):
            payload["background"] = vb._cut_background(vb.background_clips(), assets / "bg.mp4", payload["duration"],
                                                       f"{source.day.isoformat()}-{source.key}")
            character = vb.character_image()
        if character is not None:
            payload["avatar"] = {"height": vb._avatar_height(character)}
            shutil.copy(character, assets / "character.png")
        else:
            from PIL import Image

            Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(assets / "character.png")  # the img must resolve
    (work_dir / "story.json").write_text(json.dumps({"story": json.dumps(payload)}))
    inject_story(work_dir / "index.html", payload)
    return work_dir


def _length(path: Path) -> float:
    try:
        return vb._speech_span(path)[2]
    except Exception:  # noqa: BLE001
        return 0.0


# --- stills ---------------------------------------------------------------------------------

@dataclass
class Still:
    at: float
    scene: int
    label: str
    detail: str = ""
    file: str = ""


def still_times(payload: dict, style: str) -> list[Still]:
    """When each scene is fully built: after its entrance and its last
    reveal cue, with a margin, but before its exit. Plus the opening frame."""
    stills: list[Still] = []
    if style == "avx":  # each scene once its last cued element has landed, before it exits
        for i, s in enumerate(payload["scenes"]):
            cues = re.findall(r'"at": ([0-9.]+)', json.dumps(s["data"]))
            built = max([s["start"] + 1.6] + [float(c) + 0.9 for c in cues])
            at = max(min(built, s["end"] - 0.45), s["start"] + 0.6)
            stills.append(Still(round(at, 2), i, f"{i + 1} · {s['style']}", s["pal"]["bg"]))
        return stills
    if style == "explainer":
        scenes = payload["scenes"]
        first = scenes[0]
        stills.append(Still(round(min(first["start"] + 0.45, first["end"] - 0.1), 2), 0, "hook (first frame)", _style_detail(first)))
        for i, s in enumerate(scenes):
            cues = [c for c in s.get("cues", []) if c is not None]
            built = s["start"] + (2.2 if s["type"] == "title" else 1.5)  # the title's words wave in one by one
            if cues:
                built = max(built, cues[-1] + 0.7)
            for key in ("countAt", "emphasisAt"):
                if s.get(key):
                    built = max(built, s[key] + 0.8)
            if s["type"] == "outro":
                built = max(built, s["start"] + 2.7)
            at = min(built, s["end"] - 0.4)
            at = max(at, s["start"] + 0.6)
            stills.append(Still(round(at, 2), i, f"{i + 1} · {s['type']}", _style_detail(s)))
        return stills
    # avatar: the hook, each card once built (highlight swept), the outro
    hook = payload["hook"]
    stills.append(Still(round(max(0.3, min(hook["exitAt"] - 0.3, hook["words"][-1]["at"] + 0.4)), 2), 0, "hook", ""))
    cards = payload["cards"]
    for i, c in enumerate(cards):
        nxt = cards[i + 1]["start"] if i + 1 < len(cards) else payload["outro"]["start"]
        at = min(max(c["highlightAt"] + 0.5, c["start"] + 0.8), nxt - 0.3)
        stills.append(Still(round(max(at, c["start"] + 0.4), 2), i + 1, f"{i + 1} · {'stat' if c.get('kind') == 'stat' else 'card'}", ""))
    stills.append(Still(round(min(payload["outro"]["start"] + 0.8, payload["duration"] - 0.2), 2), len(cards) + 1, "outro", ""))
    return stills


def _style_detail(scene: dict) -> str:
    st = scene.get("style")
    if not isinstance(st, dict):
        return ""
    bits = []
    for k in ("look", "transition", "entrance", "hold", "type_treatment", "background"):
        if st.get(k) and st[k] != "default":
            bits.append(f"{k.replace('_', ' ')} {st[k]}")
    if st.get("recipe"):
        bits.insert(0, st["recipe"])
    return ", ".join(bits)


def _snapshot(work_dir: Path, times: list[float], out_dir: Path, timeout: float = SNAPSHOT_TIMEOUT) -> list[Path]:
    """`hyperframes snapshot --at t1,t2,...`; returns the PNGs in time order."""
    from .supervisor import run_subprocess

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    npx = npx_path()
    cmd = [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "snapshot", str(work_dir), "--output", str(out_dir),
           "--at", ",".join(f"{t:.2f}" for t in times), "--no-end", "--describe", "false", "--no-proxy", "--timeout", "15000"]
    run_subprocess(cmd, timeout=timeout, env=hyperframes_env(npx), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    pngs = sorted(out_dir.glob("*.png"), key=_snapshot_sort_key)
    if len(pngs) < len(times):
        raise RuntimeError(f"snapshot wrote {len(pngs)} frames for {len(times)} times")
    return pngs[: len(times)]


def _snapshot_sort_key(path: Path):
    m = re.search(r"(\d+(?:[._]\d+)?)s", path.stem)
    if m:
        try:
            return (0, float(m.group(1).replace("_", ".")), path.name)
        except ValueError:
            pass
    m = re.search(r"(\d+)", path.stem)
    return (1, int(m.group(1)) if m else 0, path.name)


def storyboard(frames: list[Path], stills: list[Still], out: Path, title: str, subtitle: str, note: str) -> Path:
    """Labelled contact sheet: each frame at FRAME_W with its scene label and
    time, a header with the title and the look, and a timing note."""
    from PIL import Image, ImageDraw, ImageFont

    font = _font(15)
    small = _font(12)
    head_h = 64
    tile_h = int(FRAME_W * 16 / 9) + LABEL_H
    cols = min(COLUMNS, max(1, len(frames)))
    rows = (len(frames) + cols - 1) // cols
    gap = 12
    width = cols * FRAME_W + (cols + 1) * gap
    height = head_h + rows * tile_h + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), (246, 245, 242))
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 12), title[:90], fill=(28, 28, 30), font=_font(17))
    draw.text((gap, 36), subtitle[:140], fill=(100, 100, 104), font=small)
    if note:
        tw = draw.textlength(note, font=small)
        draw.rounded_rectangle((width - gap - tw - 16, 30, width - gap, 52), radius=6, fill=(255, 237, 204))
        draw.text((width - gap - tw - 8, 34), note, fill=(120, 76, 0), font=small)
    for i, (frame, still) in enumerate(zip(frames, stills)):
        with Image.open(frame) as im:
            im = im.convert("RGB")
            im.thumbnail((FRAME_W, FRAME_W * 16 // 9))
        x = gap + (i % cols) * (FRAME_W + gap)
        y = head_h + gap + (i // cols) * (tile_h + gap)
        sheet.paste(im, (x, y))
        draw.rectangle((x, y, x + FRAME_W - 1, y + im.height - 1), outline=(205, 205, 200))
        draw.text((x, y + im.height + 8), f"{still.label}", fill=(28, 28, 30), font=font)
        tlabel = f"{still.at:.1f}s"
        draw.text((x + FRAME_W - draw.textlength(tlabel, font=small), y + im.height + 10), tlabel, fill=(100, 100, 104), font=small)
        if still.detail:
            draw.text((x, y + im.height + 30), still.detail[:44], fill=(100, 100, 104), font=small)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=86)
    return out


def _font(size: int):
    from PIL import ImageFont

    for candidate in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                      "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


# --- the tiers -------------------------------------------------------------------------------

def _timing(source: Source, voice: bool) -> tuple[Path | None, list, float, str]:
    if voice:
        wav, per_line, speech = voice_timing(source)
        return wav, per_line, speech, "voiced"
    cached = cached_timing(source)
    if cached:
        wav, per_line, speech = cached
        return wav, per_line, speech, "cached voice"
    per_line, speech = estimate_timing(source.lines)
    return None, per_line, speech, "estimated timing"


def _write_result(source: Source, result: dict) -> Path:
    source.out_dir.mkdir(parents=True, exist_ok=True)
    path = source.out_dir / "preview.json"
    from .runstate import write_json_atomic

    existing = {}
    try:
        existing = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    existing.update({"version": PREVIEW_VERSION, "key": source.key, "title": source.script.title, "look": source.look})
    result["fingerprint"] = source.fingerprint
    if source.trial_look is not None:
        result["trial_look"] = source.trial_look
    existing[result["tier"]] = result
    write_json_atomic(path, existing)
    return path


def preview_stills(source: Source, voice: bool = False) -> dict:
    t0 = time.monotonic()
    apply_look_to_plan(source)
    audio, per_line, speech, timing = _timing(source, voice)
    payload = build_payload(source, per_line, speech)
    style = source.look["style"]
    work = source.out_dir / "_work"
    prepare_workdir(source, payload, audio, work)
    stills = still_times(payload, style)
    frames_dir = source.out_dir / "frames"
    pngs = _snapshot(work, [s.at for s in stills], frames_dir)
    frames: list[Path] = []
    for png, still in zip(pngs, stills):
        target = frames_dir / f"{still.scene:02d}_{still.at:05.1f}s.png"
        if png != target:
            png.replace(target)
        still.file = target.name
        frames.append(target)
    for extra in list(frames_dir.glob("*.png")) + list(frames_dir.glob("*.jpg")):
        if extra not in frames:
            extra.unlink()  # snapshot's own contact sheet and any end frame
    sheet = storyboard(frames, stills, source.out_dir / "storyboard.jpg", source.script.title,
                       describe(source.look), "" if timing != "estimated timing" else "estimated timing")
    shutil.rmtree(work, ignore_errors=True)
    result = {
        "tier": "stills", "file": sheet.name, "frames": [s.__dict__ for s in stills], "timing": timing,
        "duration": payload["duration"], "seconds": round(time.monotonic() - t0, 1), "at": time.time(),
    }
    _write_result(source, result)
    log.info("Stills: %d frames in %.1fs (%s) -> %s", len(frames), result["seconds"], timing, sheet)
    return result


def preview_draft(source: Source, voice: bool = False, fps: int = DRAFT_FPS, quality: str = DRAFT_QUALITY,
                  resolution: str | None = None) -> dict:
    t0 = time.monotonic()
    apply_look_to_plan(source)
    audio, per_line, speech, timing = _timing(source, voice)
    payload = build_payload(source, per_line, speech)
    work = source.out_dir / "_work"
    prepare_workdir(source, payload, audio, work)
    out = source.out_dir / "draft.mp4"
    partial = source.out_dir / ".partial" / out.name
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)
    npx = npx_path()
    cmd = [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render", str(work), "--output", str(partial),
           "--variables-file", str(work / "story.json"), "--strict-variables", "--quiet",
           "--quality", quality, "--fps", str(fps)]
    if resolution:
        cmd += ["--resolution", resolution]
    from .supervisor import run_subprocess

    run_subprocess(cmd, timeout=DRAFT_TIMEOUT, env=hyperframes_env(npx))
    if not partial.exists() or partial.stat().st_size == 0:
        raise RuntimeError("The draft render wrote no video")
    partial.replace(out)
    shutil.rmtree(work, ignore_errors=True)
    result = {
        "tier": "draft", "file": out.name, "timing": timing, "fps": fps, "quality": quality,
        "duration": payload["duration"], "size": out.stat().st_size, "seconds": round(time.monotonic() - t0, 1),
        "at": time.time(),
    }
    _write_result(source, result)
    log.info("Draft: %.1fs (%s, %d fps, %s) -> %s", result["seconds"], timing, fps, quality, out)
    return result


def prepare_studio(source: Source) -> tuple[Path, str, dict]:
    """The work copy HyperFrames Studio serves: the same template copy the
    stills use, with the video's resolved look and cached (never
    synthesised) timing. Returns (work_dir, timing, payload)."""
    apply_look_to_plan(source)
    audio, per_line, speech, timing = _timing(source, voice=False)
    payload = build_payload(source, per_line, speech)
    work = source.out_dir / STUDIO_DIRNAME
    prepare_workdir(source, payload, audio, work)
    return work, timing, payload


def studio_command(work_dir: Path, port: int, npx: str) -> list[str]:
    """What `hyperframes preview --background` itself spawns (attached, no
    browser, a fresh server on exactly this port), so the panel's job
    supervisor owns the process and can stop it."""
    return [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "preview", str(work_dir), "--port", str(port),
            "--foreground", "--no-open", "--force-new", "--no-proxy"]


def run_studio(source: Source, port: int) -> int:
    """Prepares the work copy and serves it with Studio until stopped
    (SIGTERM/SIGINT stop the server's whole process tree). Blocks; returns
    the server's exit code. Never renders, voices or uploads."""
    from .supervisor import kill_process_tree

    work, timing, payload = prepare_studio(source)
    npx = npx_path()
    url = f"http://{STUDIO_HOST}:{port}/#project/{STUDIO_DIRNAME}"
    log.info("Studio: %s (%s, %.1fs) <- %s", url, timing, payload["duration"], work)
    env = {**hyperframes_env(npx), "HYPERFRAMES_PREVIEW_HOST": STUDIO_HOST}
    proc = subprocess.Popen(studio_command(work, port, npx), env=env, stdin=subprocess.DEVNULL)

    def stop(signum, frame):  # noqa: ARG001
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        code = proc.wait()
    finally:
        if proc.poll() is None:
            kill_process_tree(proc.pid)
    log.info("Studio stopped (exit %s)", code)
    return 0 if code in (0, -signal.SIGTERM, -signal.SIGINT) else int(code)


def render_final(source: Source, out_path: Path) -> Path:
    """The full-quality, voiced render of one source, through the same
    builder the pipeline uses (so the result is exactly a pipeline video).
    Only reachable through --final."""
    t0 = time.monotonic()
    apply_look_to_plan(source)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_root = source.out_dir / "_final_work"
    work_root.mkdir(parents=True, exist_ok=True)
    audio = work_root / "audio" / "voice.wav"
    with applied(source.look):
        if source.look["style"] == "explainer":
            eb._prepared.discard(work_root / "_hf_work_explainer")
            path = eb.build_explainer_short(source.script, out_path, work_root / "audio" / "x.wav", source.kicker)
        else:
            work_dir = work_root / "_hf_work"
            vb._prepare_workdir(work_dir)
            path = vb._build_short(source.script, out_path, audio, work_dir, source.kicker,
                                   f"{source.day.isoformat()}-{source.key}")
    shutil.rmtree(work_root, ignore_errors=True)
    log.info("Final: %.1fs -> %s", time.monotonic() - t0, path)
    return path


# --- CLI ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(os.environ.get("BYTESIZE_ENV_FILE") or eb.PROJECT_ROOT / ".env")  # the panel may point at another .env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.preview", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="a video sidecar (<video>.json) or a folder/date with a plan.json")
    parser.add_argument("--video", help="with a plan: which video, e.g. v02")
    parser.add_argument("--preview", choices=TIERS, default="stills", help="which tier (default: stills)")
    parser.add_argument("--final", action="store_true", help="a full-quality voiced render instead (explicit)")
    parser.add_argument("--out", type=Path, help="--final only: where to write the mp4")
    parser.add_argument("--voice", action="store_true", help="draft/stills: voice the lines first (TTS, may use Voicebox)")
    parser.add_argument("--fps", type=int, default=DRAFT_FPS, help=f"draft frame rate (default {DRAFT_FPS})")
    parser.add_argument("--resolution", help="draft: a HyperFrames resolution preset, if you want one")
    parser.add_argument("--studio", action="store_true",
                        help="serve the prepared composition with HyperFrames Studio (127.0.0.1) until stopped")
    parser.add_argument("--port", type=int, default=3002, help="--studio only: the port to serve on (default 3002)")
    parser.add_argument("--trial", action="store_true",
                        help="plan videos: preview the look given by the look flags INSTEAD of the saved one, without saving it")
    from .plan_day import _overrides_from_args, add_look_arguments

    add_look_arguments(parser)
    args = parser.parse_args(argv)
    try:
        overrides = _overrides_from_args(args)
        target = Path(args.source)
        if target.suffix == ".json" and target.is_file() and target.name != pm.MANIFEST_NAME:
            source = source_from_sidecar(target, overrides)
        else:
            from .render_plan import resolve_folder

            folder, _ = resolve_folder(args.source)
            if not args.video:
                data = pm.read_manifest(folder)
                ids = [e["id"] for e in (data or {}).get("videos", [])]
                sys.exit(f"Pick a planned video with --video: {', '.join(ids) or 'none planned'}")
            source = source_from_plan(folder, args.video, overrides, trial=args.trial)
        if args.final and args.studio:
            sys.exit("--studio and --final are different things: pick one")
        if args.final:
            out = args.out or (source.out_dir / "final.mp4")
            print(f"Wrote {render_final(source, out)}")
            return 0
        if args.studio:
            if not 1 <= args.port <= 65535:
                sys.exit("--port must be between 1 and 65535")
            return run_studio(source, args.port)
        if args.preview == "draft":
            result = preview_draft(source, voice=args.voice, fps=args.fps, resolution=args.resolution)
        else:
            result = preview_stills(source, voice=args.voice)
        print(f"{result['tier']}: {source.out_dir / result['file']}  ({result['seconds']}s, {result['timing']})")
        return 0
    except (LookError, pm.ManifestError, RuntimeError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
