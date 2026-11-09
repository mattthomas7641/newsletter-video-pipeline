"""Avatar explainer (AVX): a separate video format. Puck stands bottom-left on a
plain, changeable background colour while purpose-built motion graphics
(kinetic type, count-ups, timelines, versus cards, charts, callouts, lists)
play in the space above and to the right of him.

The composition lives in newsletter_video/hf_template_avx/ and is independent
of hf_template and hf_template_explainer. In the pipeline it is the "avx"
video style (look.STYLES, a lineup's "Avatar explainer" batch): avx_writer.py
writes the scenes, video_builder._build_short calls build_avx_short, which
colours the scenes from the video's look (apply_look: one ground for every
scene, or the house rhythm) and renders with the same discipline as the other
builders (wall-clock timeout, one retry, a .partial file moved into place).

A video is a list of scenes. Each scene is one spoken line plus one graphic:

    {"style": "stat", "pose": 1, "say": "...", "bg": "#f3efe6", "data": {...}}

`style` picks the scene renderer in the template (see STYLES). Inside `data`,
any dict with a "cue" key gets an "at" time: the moment that word is spoken
(real forced alignment of the TTS clip, same as the main builder).

Draft render (writes into output/_drafts/avatar-explainer/, never output/<date>/):

    venv/bin/python -m newsletter_video.avx_builder --draft
"""
from __future__ import annotations

import argparse
import colorsys
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .colors import contrast, luminance, normalise_hex, rgb, to_hex

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "hf_template_avx"
DRAFT_DIR = PROJECT_ROOT / "output" / "_drafts" / "avatar-explainer"
log = logging.getLogger("newsletter_video")

STYLES = ("kinetic", "stat", "timeline", "versus", "chart", "relation", "list", "outro")
SCENE_LEAD = 0.22  # a scene's graphic starts this long before its first word
TAIL_SECONDS = 1.2  # hold after the last word
AVATAR_HEIGHT = 980  # same footprint as the avatar format (video_builder.AVATAR_MAX_HEIGHT)
INK_DARK = "#101114"
INK_LIGHT = "#f7f6f2"


# --- palette: everything derives from the one background colour -----------

def _mix(a: str, b: str, t: float) -> str:
    ca, cb = rgb(a), rgb(b)
    return to_hex(tuple(ca[i] + (cb[i] - ca[i]) * t for i in range(3)))


def _push_contrast(colour: str, ground: str, target: float, toward: str) -> str:
    out = colour
    for i in range(1, 41):
        if contrast(out, ground) >= target:
            return out
        out = _mix(colour, toward, i / 40)
    return out


def _default_accent(bg: str) -> str:
    """An accent that sits well on the ground: warm orange-red on light paper,
    a sunny yellow on saturated mid tones, a mint on near-black."""
    lum = luminance(bg)
    r, g, b = (x / 255 for x in rgb(bg))
    _, light, sat = colorsys.rgb_to_hls(r, g, b)
    if lum > 0.45:
        return "#ff4d1f"
    if lum < 0.03:
        return "#5ef2b0"
    return "#ffd23f" if sat > 0.35 else "#ff7a45"


def palette(bg: str, accent: str | None = None) -> dict:
    """Readable colours for a scene, all derived from `bg`:
    ink (text), mute (secondary text), hair (rules/axes), surface (cards),
    accent (+ a second accent), onAccent (text on accent fills), shadow."""
    bg = normalise_hex(bg) or "#f3efe6"
    light_ground = contrast(INK_DARK, bg) >= contrast(INK_LIGHT, bg)
    ink = INK_DARK if light_ground else INK_LIGHT
    if contrast(ink, bg) < 4.5:  # a mid-tone ground: only pure black/white still reads
        ink = "#000000" if light_ground else "#ffffff"
    mute = _push_contrast(_mix(ink, bg, 0.42), bg, 4.5, ink)
    hair = _mix(ink, bg, 0.82)
    if light_ground:
        surface = _mix(bg, "#ffffff", 0.72)
    else:
        surface = _mix(bg, "#ffffff", 0.08 if luminance(bg) < 0.03 else 0.14)
    acc = normalise_hex(accent) if accent else _default_accent(bg)
    acc = _push_contrast(acc, bg, 3.0, "#000000" if light_ground else "#ffffff")
    # Second accent: the complementary hue, same treatment; used for the other side of a comparison.
    r, g, b = (x / 255 for x in rgb(acc))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    rr, gg, bb = colorsys.hls_to_rgb((h + 0.5) % 1.0, min(max(l, 0.45), 0.6), max(s, 0.6))
    acc2 = _push_contrast(to_hex((rr * 255, gg * 255, bb * 255)), bg, 3.0, "#000000" if light_ground else "#ffffff")
    on_accent = INK_DARK if contrast(INK_DARK, acc) >= contrast(INK_LIGHT, acc) else INK_LIGHT
    shadow = "16,17,20" if light_ground else "0,0,0"
    return {"bg": bg, "ink": ink, "mute": mute, "hair": hair, "surface": surface, "accent": acc,
            "accent2": acc2, "onAccent": on_accent, "shadow": shadow, "light": light_ground}


# --- timing -----------------------------------------------------------------

def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _resolve_cues(node, words: list[tuple[str, float]], start: float, end: float):
    """Gives every {"cue": "word"} dict an "at": when that word is spoken in
    this scene (first match at or after any earlier cue), else a proportional
    fallback inside the scene."""
    state = {"from": 0}

    def find(cue: str) -> float:
        target = _norm(cue.split()[0])
        for k in range(state["from"], len(words)):
            if _norm(words[k][0]) == target:
                state["from"] = k
                return words[k][1]
        for k, (w, t) in enumerate(words):
            if _norm(w) == target:
                return t
        return start + 0.4 * (end - start)

    def walk(x):
        if isinstance(x, dict):
            if "cue" in x and "at" not in x:
                x["at"] = round(find(str(x["cue"])), 3)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(node)
    return node


def build_payload(scenes: list[dict], per_line: list[list[tuple[str, float]]], speech_end: float,
                  draft_labels: bool = False, avatar: dict | None = None) -> dict:
    """The `story` variable the template reads. Pure: no I/O."""
    from .video_builder import _caption_chunks

    duration = round(speech_end + TAIL_SECONDS, 3)
    starts = []
    for i, words in enumerate(per_line):
        starts.append(0.0 if i == 0 else round(max(0.0, words[0][1] - SCENE_LEAD), 3))
    out_scenes, captions = [], []
    prev_bg = None
    for i, (scene, words) in enumerate(zip(scenes, per_line)):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else duration
        pal = palette(scene.get("bg") or (out_scenes[-1]["pal"]["bg"] if out_scenes else "#f3efe6"),
                      scene.get("accent"))
        transition = scene.get("transition")
        if transition is None:
            transition = "cut" if prev_bg is None or prev_bg == pal["bg"] else "iris"
        data = _resolve_cues(copy.deepcopy(scene.get("data", {})), words, start, end)
        out_scenes.append({
            "index": i, "style": scene["style"], "label": scene.get("label", scene["style"]),
            "eyebrow": scene.get("eyebrow", ""), "start": start, "end": round(end, 3),
            "pal": pal, "transition": transition if i else "cut", "data": data,
            "words": [{"w": w, "t": t} for w, t in words],
        })
        prev_bg = pal["bg"]
        if scene.get("captions", True):
            chunks = _caption_chunks(words)
            for j, chunk in enumerate(chunks):
                nxt = chunks[j + 1][0][1] if j + 1 < len(chunks) else end - 0.05
                captions.append({"words": [w for w, _ in chunk], "at": [t for _, t in chunk],
                                 "start": chunk[0][1], "end": round(nxt, 3), "scene": i})
    payload = {"duration": duration, "draft_labels": bool(draft_labels), "scenes": out_scenes,
               "captions": captions}
    if avatar:
        payload["avatar"] = avatar
    return payload


# --- render -------------------------------------------------------------------

def _prepare_workdir(work_dir: Path) -> None:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(TEMPLATE_DIR, work_dir,
                    ignore=shutil.ignore_patterns("renders", "snapshots", "node_modules", ".thumbnails",
                                                  "assets", ".hyperframes"))
    (work_dir / "assets").mkdir()


def _avatar_assets(assets: Path, seed: str, scene_starts: list[float],
                   scene_poses: list[int | None] | None = None) -> dict:
    """The narrator's poses as assets/pose-N.png and when to swap them. The active character (CHARACTER),
    else the legacy image in assets/character; a pose a character does not have falls back to its main one."""
    from .video_builder import _avatar_height, character_image, character_poses, pose_changes

    main = character_image()
    if main is None:  # no avatar at all: an empty image, so the template's <img> still resolves
        from PIL import Image

        Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(assets / "pose-1.png")
        return {"height": AVATAR_HEIGHT, "poses": ["pose-1.png"], "first": 0, "changes": []}
    shutil.copy(main, assets / "pose-1.png")
    poses = ["pose-1.png"]
    for k, pose in enumerate(character_poses(), start=2):
        shutil.copy(pose, assets / f"pose-{k}.png")
        poses.append(f"pose-{k}.png")
    if scene_poses:
        scene_poses = [p if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < len(poses) else 0
                       for p in scene_poses]
    height = min(AVATAR_HEIGHT, _avatar_height(main))
    if scene_poses and all(p is not None for p in scene_poses):
        # Chosen per scene (pointing at a chart, shrugging at a question...): one change per scene start.
        changes, current = [], scene_poses[0]
        for at, pose in zip(scene_starts[1:], scene_poses[1:]):
            if pose != current and pose < len(poses):
                changes.append({"at": round(at, 3), "pose": pose})
                current = pose
    else:
        changes = pose_changes(len(poses), scene_starts[1:], seed)
    return {"height": height, "poses": poses, "first": scene_poses[0] if scene_poses and scene_poses[0] else 0,
            "changes": changes}


SCENE_GAP = 0.4  # breath between scenes: lets each graphic land before the next line starts


def _voice_scenes(scenes: list[dict], out: Path, work_dir: Path, speed: float | None):
    """Voices each scene on its own and joins the clips with a short breath
    (scene["pause_after"], default SCENE_GAP), so every graphic gets a beat
    to land. Word times come from forced alignment of each clip, offset into
    the joined track."""
    import numpy as np
    import soundfile

    from .video_builder import _voice_script

    parts, per_line, offset, rate = [], [], 0.0, None
    for i, scene in enumerate(scenes):
        clip = work_dir / f"line_{i:02d}.wav"
        words, _ = _voice_script([scene["say"]], clip, speed=speed)
        samples, sr = soundfile.read(str(clip), dtype="float32", always_2d=True)
        rate = rate or sr
        if sr != rate:
            raise RuntimeError("TTS clips came back at different sample rates")
        mono = samples.mean(axis=1)
        # trim trailing silence so the breath is exactly the pause we ask for
        loud = np.flatnonzero(np.abs(mono) > 0.02 * (np.abs(mono).max() or 1.0))
        if len(loud):
            mono = mono[: min(len(mono), loud[-1] + int(0.12 * sr))]
        per_line.append([(w, round(t + offset, 3)) for w, t in words[0]])
        gap = scene.get("pause_after", SCENE_GAP) if i + 1 < len(scenes) else 0.0
        parts.append(mono)
        parts.append(np.zeros(int(gap * sr), dtype="float32"))
        offset += len(mono) / sr + gap
    joined = np.concatenate(parts)
    soundfile.write(str(out), joined, rate)
    return per_line, len(joined) / rate


def inject_default(work_dir: Path, payload: dict) -> None:
    """Writes `payload` as the composition variable's default in the work
    copy's index.html, so Studio preview and `hyperframes snapshot` (which take
    no variables file) show the real video."""
    index = work_dir / "index.html"
    html = index.read_text()
    start = html.index("data-composition-variables='") + len("data-composition-variables='")
    end = html.index("'", start)
    spec = [{"id": "story", "type": "string", "label": "Story JSON", "default": json.dumps(payload)}]
    blob = json.dumps(spec).replace("'", "&#39;")
    index.write_text(html[:start] + blob + html[end:])


def payload_from_work(work_dir: Path) -> dict:
    return json.loads(json.loads((work_dir / "story.json").read_text())["story"])


MAX_SECONDS = 58.0  # a Short must stay under a minute: a longer voice track is re-voiced faster
DRAFT_CRF = "20"


def render(work_dir: Path, variables: Path, out_path: Path, log_path: Path, crf: str = DRAFT_CRF) -> Path:
    """Renders the prepared work dir with the same discipline as the other builders
    (video_builder._build_short, explainer_builder.render_story): a wall-clock cap per attempt
    (RENDER_TIMEOUT_SECONDS) that kills the whole process tree, one retry, and the video written to a
    .partial file that is moved into place only when complete. Raises CalledProcessError /
    TimeoutExpired / RuntimeError exactly like them, so the per-video failure handling is identical.
    The renderer's own log goes to `log_path` (grep it for 404 / media_load_failed)."""
    from .supervisor import run_subprocess
    from .tts import HYPERFRAMES_VERSION, hyperframes_env, npx_path
    from .video_builder import DEFAULT_RENDER_TIMEOUT_SECONDS, RENDER_ATTEMPTS

    partial = out_path.parent / ".partial" / out_path.name
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)
    npx = npx_path()
    cmd = [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render", str(work_dir), "--output", str(partial),
           "--variables-file", str(variables), "--strict-variables", "--crf", str(crf)]
    timeout = float(os.environ.get("RENDER_TIMEOUT_SECONDS", DEFAULT_RENDER_TIMEOUT_SECONDS))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(RENDER_ATTEMPTS):
        try:
            with open(log_path, "w") as fh:
                run_subprocess(cmd, timeout=timeout, env=hyperframes_env(npx), stdout=fh, stderr=subprocess.STDOUT)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("Render attempt %d/%d of %s failed: %s", attempt + 1, RENDER_ATTEMPTS, out_path.name,
                        type(exc).__name__)
            if attempt == RENDER_ATTEMPTS - 1:
                partial.unlink(missing_ok=True)
                raise
    if not partial.exists() or partial.stat().st_size == 0:
        raise RuntimeError(f"Renderer exited cleanly but wrote no video for {out_path.name}")
    partial.replace(out_path)
    return out_path


def build_avx(scenes: list[dict], out_path: Path, work_dir: Path, *, draft_labels: bool = False,
              speed: float | None = None, seed: str = "avx", render_video: bool = True, crf: str = DRAFT_CRF,
              audio_out: Path | None = None, log_path: Path | None = None) -> dict:
    """Voices the scenes (one TTS call per scene), aligns words, writes the payload and
    renders. Returns timing info. render_video=False stops after the payload (for
    snapshots). `audio_out` keeps a copy of the narration (the pipeline's _audio/<date>/NN.wav)."""
    from .video_builder import _pad_audio

    for s in scenes:
        if s["style"] not in STYLES:
            raise ValueError(f"unknown AVX style {s['style']!r}")
    _prepare_workdir(work_dir)
    assets = work_dir / "assets"
    raw = work_dir / "voice_raw.wav"
    t0 = time.time()
    per_line, length = _voice_scenes(scenes, raw, work_dir, speed)
    if speed is None and length > MAX_SECONDS - TAIL_SECONDS:  # too long for a Short: re-voice a little faster
        from .tts import base_speed
        from .video_builder import MAX_SPEED

        faster = min(base_speed() * length / (MAX_SECONDS - TAIL_SECONDS - 1.0), MAX_SPEED)
        log.info("Avatar explainer: %.1fs of narration, re-voicing at speed %.2f", length, faster)
        per_line, length = _voice_scenes(scenes, raw, work_dir, faster)
    if audio_out is not None:
        audio_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(raw, audio_out)
    speech_end = max(t for line in per_line for _, t in line) + 0.6
    speech_end = min(max(speech_end, length), length + 0.2)
    payload_probe = build_payload(scenes, per_line, speech_end, draft_labels)
    avatar = _avatar_assets(assets, seed, [s["start"] for s in payload_probe["scenes"]],
                            [s.get("pose") for s in scenes])
    payload = build_payload(scenes, per_line, speech_end, draft_labels, avatar)
    _pad_audio(raw, assets / "voice.wav", max(0.0, payload["duration"] - length))
    variables = work_dir / "story.json"
    variables.write_text(json.dumps({"story": json.dumps(payload)}))
    info = {"voice_seconds": round(time.time() - t0, 1), "duration": payload["duration"], "payload": payload}
    if not render_video:
        inject_default(work_dir, payload)  # Studio / `hyperframes snapshot` read the default
        return info
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t1 = time.time()
    log_path = log_path or out_path.with_suffix(".render.log")
    render(work_dir, variables, out_path, log_path, crf=crf)
    info["render_seconds"] = round(time.time() - t1, 1)
    info["render_log"] = str(log_path)
    return info


# --- the pipeline: a video's look decides the colours ----------------------------------------

LATE_CUE_PAUSE = 0.9


def _late_cue(scene: dict) -> bool:
    """Whether an element of the scene is cued on one of the last two words of its line (and only there)."""
    toks = [_norm(w) for w in str(scene.get("say", "")).split()]
    tail = {t for t in toks[-2:] if t}
    cues = re.findall(r'"cue": "([^"]*)"', json.dumps(scene.get("data") or {}))
    return any(c.split() and _norm(c.split()[0]) in tail and _norm(c.split()[0]) not in toks[:-2] for c in cues)


def house_rhythm(n: int) -> list[str]:
    """The draft's background rhythm for n scenes: light paper for the hook and the first beat, then the
    blue, then near-black for the rest, and light again for the outro (L L M M D D D L for eight)."""
    if n <= 0:
        return []
    if n <= 2:
        return [LIGHT] * n
    if n == 3:
        return [LIGHT, MID, LIGHT]
    rest = n - 3
    mids = rest // 2
    return [LIGHT, LIGHT] + [MID] * mids + [DARK] * (rest - mids) + [LIGHT]


def apply_look(scenes: list[dict], bg: str = "", accent: str = "") -> list[dict]:
    """Copies of `scenes` coloured for one video: `bg` set = that one plain ground for every scene (scenes
    then change with each graphic's own directional exit, no wipes); blank = the house rhythm, with a wipe
    (iris, the outro a shutter) wherever the ground changes. `accent` set = that animation colour on every
    scene (palette() keeps it readable on each ground); blank = each ground's own accent."""
    bg = normalise_hex(bg or "") or ""
    accent = normalise_hex(accent or "") or ""
    grounds = [bg] * len(scenes) if bg else house_rhythm(len(scenes))
    out = []
    for i, (scene, ground) in enumerate(zip(scenes, grounds)):
        s = copy.deepcopy(scene)
        if "pause_after" not in s and _late_cue(s):
            s["pause_after"] = LATE_CUE_PAUSE  # its last element lands at the end of the line: let it be seen
        s["bg"] = ground
        if accent:
            s["accent"] = accent
        else:
            s.pop("accent", None)
        s.pop("transition", None)  # derived from the grounds in build_payload: a cut on the same ground
        if not bg and i == len(scenes) - 1 and i and grounds[i - 1] != ground:
            s["transition"] = "shutter"
        out.append(s)
    return out


def build_avx_short(script, out_path: Path, audio_path: Path, kicker: str = "", seed: str = "avx") -> Path:
    """The avatar explainer counterpart of video_builder.build_short, called from it per video (so
    numbering, isolation, the sidecar and status.json stay the caller's). The look is already in the
    environment (look.applied): CHARACTER narrates, LOOK_BG / LOOK_ACCENT colour the scenes."""
    from .avx_writer import scenes_for

    scenes = apply_look(scenes_for(script), os.environ.get("LOOK_BG", ""), os.environ.get("LOOK_ACCENT", ""))
    work_dir = audio_path.parent.parent / "_hf_work_avx"
    from .video_builder import RENDER_CRF

    log.info("Avatar explainer: rendering %r (%d scenes: %s; %s)", getattr(script, "title", "?"), len(scenes),
             ", ".join(s["style"] for s in scenes),
             f"background {scenes[0]['bg']}" if len({s['bg'] for s in scenes}) == 1 else "house rhythm")
    build_avx(scenes, out_path, work_dir, seed=seed, crf=RENDER_CRF, audio_out=audio_path,
              log_path=audio_path.with_suffix(".render.log"))
    return out_path


# --- the draft: one story, eight scenes, eight styles, three backgrounds ----

LIGHT, MID, DARK = "#f3efe6", "#2f4fe0", "#0d0f14"

DRAFT_SCENES: list[dict] = [
    {"style": "kinetic", "pose": 0, "label": "Kinetic type", "bg": LIGHT, "captions": False, "eyebrow": "Apple · AI",
     "say": "Tim Cook wants Apple to go way bigger on AI.",
     "data": {"lines": [{"text": "Tim Cook wants", "cue": "Tim"},
                        {"text": "Apple to go", "cue": "Apple"},
                        {"text": "way bigger", "em": True, "cue": "way"},
                        {"text": "on AI.", "cue": "on"}],
              "underline": {"cue": "bigger"}}},
    {"style": "stat", "pose": 1, "label": "Count-up + unit chart", "bg": LIGHT, "eyebrow": "The stakes",
     "say": "The stakes are huge: Apple's AI could reach over two billion active devices.",
     "data": {"value": 2.0, "decimals": 1, "prefix": "", "suffix": "B+", "count": {"cue": "two"},
              "label": "active Apple devices", "dots": 20, "dot_label": "1 dot = 100 million devices",
              "source": "Apple, Jan 2025: 2.35B active devices"}},
    {"style": "timeline", "pose": 2, "label": "Timeline path draw", "bg": MID, "eyebrow": "How we got here",
     "say": "Siri arrived in 2011. ChatGPT hit in 2022. Apple answered two years later, with small, careful features.",
     "data": {"nodes": [{"year": "2011", "title": "Siri", "icon": "wave", "cue": "Siri"},
                        {"year": "2022", "title": "ChatGPT", "icon": "spark", "cue": "ChatGPT"},
                        {"year": "2024", "title": "Apple Intelligence", "icon": "chip", "cue": "Apple"}],
              "tail": {"text": "Small, careful features", "cue": "small"}}},
    {"style": "versus", "pose": 3, "pause_after": 0.7, "label": "Versus cards", "bg": MID, "eyebrow": "The pace gap",
     "say": "Rivals shipped chatbots and agents fast. Apple played it safe.",
     "data": {"right": {"title": "Rivals", "tag": "Fast", "items": ["Chatbots", "Agents", "Ship fast"],
                        "cue": "Rivals"},
              "left": {"title": "Apple", "tag": "Safe", "items": ["Small features", "Privacy first", "Ship when ready"],
                       "cue": "Apple"},
              "pace": {"label": "Pace", "right": "Fast", "left": "Slow"},
              "verdict": {"text": "Played it safe", "cue": "safe"}}},
    {"style": "chart", "pose": 1, "label": "Line chart + callout", "bg": DARK, "eyebrow": "What investors see",
     "say": "Investors can see that gap growing, and now they want a real plan.",
     "data": {"note": "Illustrative", "y_label": "AI launches",
              "x_labels": ["2022", "2023", "2024", "2025", "2026"],
              "series": [{"name": "Rivals", "values": [2, 9, 18, 30, 44], "cue": "Investors"},
                         {"name": "Apple", "values": [0, 1, 5, 8, 11], "cue": "can"}],
              "gap": {"text": "The gap", "cue": "gap"},
              "callout": {"kicker": "Investors", "text": "Show us the plan", "cue": "plan"}}},
    {"style": "relation", "pose": 0, "pause_after": 0.6, "label": "Relationship + marker callout", "bg": DARK, "eyebrow": "Inside Apple",
     "say": "So Cook is pushing software chief Craig Federighi to think bigger.",
     "data": {"from": {"name": "Tim Cook", "role": "CEO", "initials": "TC", "cue": "Cook"},
              "to": {"name": "Craig Federighi", "role": "Software chief", "initials": "CF", "cue": "Craig"},
              "verb": {"text": "pushes", "cue": "pushing"},
              "phrase": {"text": "Think bigger.", "cue": "think"}}},
    {"style": "list", "pose": 1, "label": "Checklist reveal", "bg": DARK, "eyebrow": "A bolder plan",
     "say": "A bolder plan could mean a smarter Siri, AI inside every app, and bigger partnerships.",
     "data": {"title": "A bolder plan could mean", "items": [
         {"text": "A smarter Siri", "cue": "smarter"},
         {"text": "AI inside every app", "cue": "inside"},
         {"text": "Bigger partnerships", "cue": "partnerships"}]}},
    {"style": "outro", "pose": 3, "label": "Outro + CTA", "bg": LIGHT, "eyebrow": "Your call", "captions": False, "transition": "shutter",
     "say": "Would you trust Apple's AI? Tell me below.",
     "data": {"lines": [{"text": "Would you trust", "cue": "Would"},
                        {"text": "Apple's AI?", "em": True, "cue": "Apple's"}],
              "cta": {"text": "Tell me below", "cue": "Tell"}}},
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--draft", action="store_true", help="render the 8-style draft")
    parser.add_argument("--no-render", action="store_true", help="voice + payload only (for snapshots)")
    parser.add_argument("--work", default=None, help="work dir (default: a scratch dir next to the draft)")
    parser.add_argument("--out", default=str(DRAFT_DIR / "avatar-explainer-draft.mp4"))
    parser.add_argument("--speed", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    os.environ["CHARACTER"] = "puck"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path(args.out)
    work = Path(args.work) if args.work else DRAFT_DIR / "_work"
    info = build_avx(DRAFT_SCENES, out, work, draft_labels=True, speed=args.speed, render_video=not args.no_render)
    summary = {k: v for k, v in info.items() if k != "payload"}
    summary["scenes"] = [(s["style"], s["start"], s["end"], s["pal"]["bg"]) for s in info["payload"]["scenes"]]
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
