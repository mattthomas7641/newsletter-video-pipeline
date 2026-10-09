"""Renders each Script (see script_writer.py) as its own SWE-Toons-style
explainer short using HyperFrames: graph-paper background, topic badge,
highlighted callout cards popping in as the narration reaches them, and an
animated explainer avatar that lip-syncs to the voice and points at cards.

The composition lives in newsletter_video/hf_template/ (open it in
HyperFrames Studio to tweak the design). Per story this module copies the
narration into a working copy of that project and renders it with the
story's cards and lip-sync data passed as the `story` composition variable.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile

from .script_writer import Script
from .tts import HYPERFRAMES_VERSION, audio_duration_seconds, base_speed, hyperframes_env, npx_path, speakable, synthesize

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "hf_template"
BACKGROUND_DIR = PROJECT_ROOT / "assets" / "background"

TAIL_SECONDS = 0.6  # silence after the narration before the short ends
HOOK_EXIT_SECONDS = 0.25  # hook clears this long before the first card lands
HIGHLIGHT_MIN_DELAY = 0.3  # let the card land before its highlight sweeps in
RENDER_ATTEMPTS = 2
RENDER_CRF = "21"  # ~25-35 MB per video; platforms re-encode uploads anyway
MOUTH_FPS = 15  # lip-sync keyframes per second
CAPTION_WORDS = 3  # max words per caption chunk
CAPTION_CHARS = 16  # ...or fewer, if the words are long
MIN_VIDEO_SECONDS = 40.0  # re-voice a bit slower below this
TARGET_VIDEO_SECONDS = 45.0  # preferred ceiling: re-voice a bit faster past this
MAX_VIDEO_SECONDS = 60.0  # hard ceiling
TARGET_MAX_SPEED = 1.25  # how far we'll push pace to hit the preferred ceiling
MAX_SPEED = 1.4  # beyond this Kokoro sounds rushed
MIN_SPEED = 0.95  # below this it drags

_FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def _mouth_track(voice_path: Path) -> list[float]:
    """Mouth openness (0..1) per 1/MOUTH_FPS s from the voice's loudness —
    deterministic lip sync without a phoneme model."""
    samples, rate = soundfile.read(str(voice_path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    hop = max(1, rate // MOUTH_FPS)
    frames = len(mono) // hop
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(mono[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    loud = np.percentile(rms, 95) or 1.0
    level = np.clip(rms / loud, 0.0, 1.0)
    level[level < 0.12] = 0.0  # pauses close the mouth fully
    return [round(float(v), 2) for v in level]


def background_clips() -> list[Path]:
    return sorted(BACKGROUND_DIR.glob("*.mp4"))


def _cut_background(clips: list[Path], dst: Path, duration: float, seed: str) -> bool:
    """Cuts a muted stretch of gameplay exactly `duration` long into `dst`,
    from a seeded-random clip and offset (so each video and each day differs,
    but a re-run of the same day is identical). Re-encoding to light H.264
    keeps the render fast whatever the source codec. False = no background."""
    if not clips:
        return False
    rng = random.Random(seed)
    source = rng.choice(clips)
    length = audio_duration_seconds(source)  # works for video containers too
    if length <= duration:
        return False
    start = rng.uniform(0, length - duration - 0.5)
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(source),
         "-t", f"{duration:.3f}", "-an", "-vf", "fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
         "-g", "30", "-keyint_min", "30", "-movflags", "+faststart", str(dst)],  # keyframe/s: seek-safe
        check=True,
    )
    return True


def _pad_audio(src: Path, dst: Path, seconds: float) -> None:
    """Appends `seconds` of silence: the composition's length is inferred
    from the voice track, so this is what sets the short's duration."""
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"apad=pad_dur={seconds:.3f}", str(dst)],
        check=True,
    )


# --- narration timing ------------------------------------------------------
#
# Each spoken line (hook, every beat, outro) is voiced as its own clip and the
# clips are joined, so every card and caption chunk starts exactly when its
# line does. Inside a line, words are placed by how long they take to say
# (their spoken form: "$0.10" -> "10 cents") plus pauses at punctuation.

def _speech_span(path: Path) -> tuple[float, float, float]:
    """(speech onset, speech end, clip length) in seconds, by 10ms loudness."""
    samples, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    mono = np.abs(samples.mean(axis=1))
    hop = max(1, rate // 100)
    frames = len(mono) // hop
    length = len(mono) / rate
    if frames == 0:
        return 0.0, length, length
    level = mono[: frames * hop].reshape(frames, hop).max(axis=1)
    loud = np.flatnonzero(level > 0.05 * (level.max() or 1.0))
    if not len(loud):
        return 0.0, length, length
    return loud[0] / 100, (loud[-1] + 1) / 100, length


def _word_weight(word: str) -> float:
    """Roughly how long a word takes to say, in 'letters'."""
    spoken = speakable(word)
    # Digits are said as words ("5.5" -> "five point five"): ~4 letters each,
    # and a decimal point is a whole word.
    digits = sum(c.isdigit() for c in spoken)
    letters = len(re.sub(r"[^A-Za-z]", "", spoken))
    weight = letters + 4.0 * digits + 5.0 * len(re.findall(r"\d\.\d", spoken)) + 2.0  # +2: gap
    weight += 1.5 * sum(1 for c in re.sub(r"[^A-Z]", "", word))  # acronyms are spelled out
    if word[-1] in ",;:":
        weight += 9.0
    elif word[-1] in ".!?":
        weight += 13.0
    return weight


def _word_times(text: str, start: float, end: float) -> list[tuple[str, float]]:
    """When each word of `text` starts, spread over the speech span [start, end]."""
    words = text.split()
    weights = [_word_weight(w) for w in words]
    total = sum(weights) or 1.0
    times, cursor = [], 0.0
    for word, weight in zip(words, weights):
        times.append((word, round(start + (end - start) * cursor / total, 3)))
        cursor += weight
    return times


def _voice_lines(lines: list[str], seg_dir: Path, speed: float | None) -> list[dict]:
    seg_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for i, line in enumerate(lines):
        path = seg_dir / f"line_{i:02d}.wav"
        synthesize(line, path, speed=speed)
        onset, offset, length = _speech_span(path)
        segments.append({"path": path, "onset": onset, "offset": offset, "length": length})
    return segments


def _join_lines(segments: list[dict], out: Path) -> list[dict]:
    """Concatenates the clips into `out`; returns each line's absolute speech span."""
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{seg['path']}'\n" for seg in segments))
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), str(out)],
        check=True,
    )
    listing.unlink()
    spans, clock = [], 0.0
    for seg in segments:
        spans.append({"start": round(clock + seg["onset"], 3), "end": round(clock + seg["offset"], 3)})
        clock += seg["length"]
    return spans


def _caption_chunks(words: list[tuple[str, float]]) -> list[list[tuple[str, float]]]:
    chunks: list[list[tuple[str, float]]] = []
    chunk: list[tuple[str, float]] = []
    for word, at in words:
        chunk.append((word, at))
        chars = sum(len(w) for w, _ in chunk)
        ends_clause = word[-1] in ",.!?:;" and len(chunk) >= 2
        if len(chunk) >= CAPTION_WORDS or chars >= CAPTION_CHARS or ends_clause:
            chunks.append(chunk)
            chunk = []
    if chunk:
        # A lone trailing word ("catch.") joins the previous chunk instead.
        if len(chunk) == 1 and chunks and len(chunks[-1]) < CAPTION_WORDS + 1:
            chunks[-1].extend(chunk)
        else:
            chunks.append(chunk)
    return chunks


def _story_payload(script: Script, spans: list[dict], speech_duration: float,
                   kicker: str, tail: float) -> dict:
    """`spans`: the speech span of each line — hook, each beat, then outro."""
    hook_span, beat_spans, outro_span = spans[0], spans[1:-1], spans[-1]
    hook_words = [{"word": w, "at": t} for w, t in _word_times(script.hook, **hook_span)]

    cards, captions = [], []
    for beat, span in zip(script.beats, beat_spans):
        words = _word_times(beat.say, **span)
        start = span["start"]
        highlight_at = start + HIGHLIGHT_MIN_DELAY
        if beat.highlight:
            first = beat.highlight.split()[0].lower().strip(".,!?:;")
            for word, at in words:
                if word.lower().strip(".,!?:;\"'") == first:
                    highlight_at = max(highlight_at, at)
                    break
        cards.append({
            "text": beat.card,
            "highlight": beat.highlight,
            "kind": beat.kind,
            "stat": beat.stat,
            "start": start,
            "highlightAt": round(highlight_at, 3),
        })
        captions.extend(_caption_chunks(words))

    outro_start = outro_span["start"]
    # Each chunk stays up until the next one starts (or the outro arrives).
    timed = []
    for j, chunk in enumerate(captions):
        nxt = captions[j + 1][0][1] if j + 1 < len(captions) else outro_start
        timed.append({
            "words": [w for w, _ in chunk],
            "at": [t for _, t in chunk],
            "start": chunk[0][1],
            "end": round(min(nxt, outro_start), 3),
        })

    return {
        "badge": script.badge,
        "kicker": kicker,
        "duration": round(speech_duration + tail, 3),
        "hook": {
            "words": hook_words,
            "exitAt": round(max(cards[0]["start"] - HOOK_EXIT_SECONDS, 0.5), 3),
        },
        "cards": cards,
        "outro": {"text": script.outro, "start": outro_start},
        "captions": timed,
    }


def _prepare_workdir(work_dir: Path) -> None:
    """Fresh copy of the template; its sample assets/ (gitignored, may be
    absent) are replaced per story."""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(
        TEMPLATE_DIR,
        work_dir,
        ignore=shutil.ignore_patterns("renders", "snapshots", "node_modules", ".thumbnails", "assets"),
    )
    (work_dir / "assets").mkdir()


def build_short(script: Script, out_path: Path, audio_path: Path, work_dir: Path,
                kicker: str, background_seed: str) -> Path:
    lines = [script.hook] + [b.say for b in script.beats] + [script.outro]
    seg_dir = audio_path.with_suffix("")
    segments = _voice_lines(lines, seg_dir, speed=None)
    speech_duration = sum(seg["length"] for seg in segments)
    # Pace, not content, absorbs length misses: a long script is re-voiced
    # slightly faster (aiming under 45s, always under a minute) and a short
    # one slightly slower (to reach 40s), within limits that still sound natural.
    floor = MIN_VIDEO_SECONDS - TAIL_SECONDS + 1.0  # Kokoro speed isn't perfectly linear
    target = TARGET_VIDEO_SECONDS - TAIL_SECONDS - 0.3
    hard = MAX_VIDEO_SECONDS - TAIL_SECONDS - 0.5
    speed = None
    if speech_duration > target:
        speed = min(base_speed() * speech_duration / target, TARGET_MAX_SPEED)
        if speech_duration * base_speed() / speed > hard:
            speed = min(base_speed() * speech_duration / hard, MAX_SPEED)
    elif speech_duration < floor:
        speed = max(base_speed() * speech_duration / floor, MIN_SPEED)
    if speed is not None:
        segments = _voice_lines(lines, seg_dir, speed=speed)
        speech_duration = sum(seg["length"] for seg in segments)
    spans = _join_lines(segments, audio_path)

    # Still just short of the minimum? Hold the outro card a little longer.
    tail = max(TAIL_SECONDS, MIN_VIDEO_SECONDS + 0.2 - speech_duration)
    assets = work_dir / "assets"
    _pad_audio(audio_path, assets / "voice.wav", tail)

    variables_path = work_dir / "story.json"
    payload = _story_payload(script, spans, speech_duration, kicker, tail)
    payload["mouth"] = {"fps": MOUTH_FPS, "open": _mouth_track(assets / "voice.wav")}
    payload["background"] = _cut_background(
        background_clips(), assets / "bg.mp4", payload["duration"], background_seed
    )
    variables_path.write_text(json.dumps({"story": json.dumps(payload)}))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    npx = npx_path()
    cmd = [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render", str(work_dir),
           "--output", str(out_path), "--variables-file", str(variables_path),
           "--strict-variables", "--quiet", "--crf", RENDER_CRF]
    # One retry: under memory pressure the renderer's ffmpeg startup probe
    # has failed transiently and then succeeded on the next attempt.
    for attempt in range(RENDER_ATTEMPTS):
        try:
            subprocess.run(cmd, check=True, env=hyperframes_env(npx))
            break
        except subprocess.CalledProcessError:
            if attempt == RENDER_ATTEMPTS - 1:
                raise
    return out_path


def build_shorts(scripts: list[Script], out_dir: Path, audio_dir: Path,
                 target_date: date | None = None) -> list[Path]:
    """Numbering continues after any videos already in `out_dir`, so a second
    run on the same day adds videos instead of overwriting them."""
    target_date = target_date or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    work_dir = audio_dir.parent / "_hf_work"
    _prepare_workdir(work_dir)

    first = len(list(out_dir.glob("*.mp4")))
    paths = []
    for i, script in enumerate(scripts, start=first):
        slug = re.sub(r"-+", "-", "".join(c if c.isalnum() else "-" for c in script.title.lower()))[:40].strip("-")
        kicker = f"TLDR · {target_date:%b} {target_date.day}".upper()
        out_path = out_dir / f"{i + 1:02d}_{slug}.mp4"
        audio_path = audio_dir / f"{i + 1:02d}.wav"
        build_short(script, out_path, audio_path, work_dir, kicker,
                    background_seed=f"{target_date.isoformat()}-{i}")
        paths.append(out_path)
    return paths
