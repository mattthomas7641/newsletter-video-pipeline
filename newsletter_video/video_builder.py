"""Renders each Script (see script_writer.py) as its own SWE-Toons-style
explainer short using HyperFrames: graph-paper background, topic badge,
highlighted callout cards popping in as the narration reaches them, and a
static explainer avatar with a light idle bob.

The composition lives in newsletter_video/hf_template/ (open it in
HyperFrames Studio to tweak the design). Per story this module copies the
narration into a working copy of that project and renders it with the
story's cards and timing data passed as the `story` composition variable.

Caption/card timing comes from real word-level timestamps (mlx-whisper,
local, runs on this Mac's own GPU) aligned against the known script text —
not an estimate — so text lands exactly when each word is actually spoken.
A proportional-estimate fallback only kicks in if local transcription is
unavailable.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
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
CHARACTER_DIR = PROJECT_ROOT / "assets" / "character"

TAIL_SECONDS = 0.6  # silence after the narration before the short ends
MAX_TAIL_SECONDS = 2.0  # cap on the outro hold for scripts too short to reach MIN_VIDEO_SECONDS by pace alone
HOOK_EXIT_SECONDS = 0.25  # hook clears this long before the first card lands
HIGHLIGHT_MIN_DELAY = 0.3  # let the card land before its highlight sweeps in
RENDER_ATTEMPTS = 2
RENDER_CRF = "21"  # ~25-35 MB per video; platforms re-encode uploads anyway
CAPTION_WORDS = 3  # max words per caption chunk
CAPTION_CHARS = 16  # ...or fewer, if the words are long
MIN_VIDEO_SECONDS = 40.0  # re-voice a bit slower below this
TARGET_VIDEO_SECONDS = 45.0  # preferred ceiling: re-voice a bit faster past this
MAX_VIDEO_SECONDS = 60.0  # hard ceiling
TARGET_MAX_SPEED = 1.25  # how far we'll push pace to hit the preferred ceiling
MAX_SPEED = 1.4  # beyond this Kokoro sounds rushed
MIN_SPEED = 0.95  # below this it drags

WHISPER_MODEL = "mlx-community/whisper-tiny"

_FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
log = logging.getLogger("newsletter_video")

# mlx-whisper shells out to a bare `ffmpeg` on PATH rather than using
# imageio-ffmpeg's bundled binary (which isn't named `ffmpeg`). This venv's
# own bin/ has a real one (installed via conda) — make sure it's on PATH
# regardless of how this process was launched (a launchd job's PATH is
# bare, so this can't be assumed from the caller).
_venv_bin = str(Path(sys.executable).parent)
if _venv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.pathsep.join([_venv_bin, os.environ.get("PATH", "")])


def background_clips() -> list[Path]:
    """BACKGROUND_STYLE=none skips the gameplay footage entirely, leaving the
    composition's plain graph-paper background (read here, not at import, so
    a value from .env — loaded after this module — takes effect)."""
    if os.environ.get("BACKGROUND_STYLE", "gameplay").strip().lower() == "none":
        return []
    return sorted(BACKGROUND_DIR.glob("*.mp4"))


def character_image() -> Path | None:
    """The static avatar cutout — just one image; the composition handles
    all its motion (a slight idle bob, see hf_template/index.html)."""
    images = sorted(CHARACTER_DIR.glob("*.png")) + sorted(CHARACTER_DIR.glob("*.jpg"))
    return images[0] if images else None


def _prepared_clip(source: Path) -> Path:
    """A one-time conversion of a gameplay clip to light 1080x1920 30fps H.264
    with a keyframe every second, cached next to it. Decoding the original
    (often AV1 at 60fps) for every video cost ~40s; cutting from this is fast."""
    cache = source.parent / ".cache" / f"{source.stem}.h264.mp4"
    if cache.exists() and cache.stat().st_mtime >= source.stat().st_mtime:
        return cache
    cache.parent.mkdir(exist_ok=True)
    log.info("Preparing gameplay clip %s (one-time conversion)", source.name)
    tmp = cache.with_suffix(".tmp.mp4")
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(source), "-an",
         "-vf", "fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-g", "30", "-keyint_min", "30", "-movflags", "+faststart", str(tmp)],
        check=True,
    )
    tmp.replace(cache)
    return cache


def _cut_background(clips: list[Path], dst: Path, duration: float, seed: str) -> bool:
    """Cuts a muted stretch of gameplay exactly `duration` long into `dst`,
    from a seeded-random clip and offset (so each video and each day differs,
    but a re-run of the same day is identical). False = no background."""
    if not clips:
        return False
    rng = random.Random(seed)
    source = rng.choice(clips)
    length = audio_duration_seconds(source)  # works for video containers too
    if length <= duration:
        return False
    start = rng.uniform(0, length - duration - 0.5)
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(_prepared_clip(source)),
         "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
         "-pix_fmt", "yuv420p", "-g", "30", "-keyint_min", "30",  # keyframe/s: seek-safe
         "-movflags", "+faststart", str(dst)],
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


# --- narration timing: real forced alignment, not an estimate -------------
#
# One TTS call renders the whole script (hook + every beat + outro joined).
# Where each line starts, and where each word inside it falls, used to come
# from an estimate (first: silence gaps between sentences; before that: a
# proportional "how long does this word take to say" guess). Both looked
# right on clean test scripts and then drifted on real ones — a mid-line
# pause got mistaken for a line break, which cascaded into wrong timing for
# every line after it. Real word-level timestamps from local transcription,
# aligned against the known script text, replace both: measured, not
# guessed. The proportional estimate is kept only as a fallback for when
# transcription itself is unavailable.

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
    """Roughly how long a word takes to say, in 'letters' — used only as a
    fallback when local transcription isn't available."""
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


def _proportional_word_times(lines: list[str], onset: float, offset: float) -> list[list[tuple[str, float]]]:
    """Fallback: spread _word_weight() proportionally across the whole clip."""
    all_words = [(i, w) for i, line in enumerate(lines) for w in line.split()]
    weights = [_word_weight(w) for _, w in all_words]
    total = sum(weights) or 1.0
    per_line: list[list[tuple[str, float]]] = [[] for _ in lines]
    cursor = 0.0
    for (line_i, word), weight in zip(all_words, weights):
        t = onset + (offset - onset) * cursor / total
        per_line[line_i].append((word, round(t, 3)))
        cursor += weight
    return per_line


def _normalize_token(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _align_script(lines: list[str], audio_path: Path) -> list[list[tuple[str, float]]]:
    """Transcribes the one generated clip with word-level timestamps and
    aligns the recognized words against the known script text (difflib,
    tolerant of minor ASR differences like "gonna" -> "going to", or
    "twenty billion dollars" -> "$20 billion") to get each word's real
    spoken time."""
    onset, offset, _ = _speech_span(audio_path)
    try:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(audio_path), path_or_hf_repo=WHISPER_MODEL, word_timestamps=True
        )
        recognized = [
            (w["word"].strip(), float(w["start"]))
            for seg in result["segments"]
            for w in seg.get("words", [])
        ]
        if not recognized:
            raise ValueError("no words recognized")
    except Exception:
        log.warning("Local transcription unavailable; falling back to estimated word timing", exc_info=True)
        return _proportional_word_times(lines, onset, offset)

    ref = [(i, w) for i, line in enumerate(lines) for w in line.split()]
    ref_norm = [_normalize_token(w) for _, w in ref]
    rec_norm = [_normalize_token(w) for w, _ in recognized]

    times: list[float | None] = [None] * len(ref)
    sm = difflib.SequenceMatcher(None, ref_norm, rec_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ("equal", "replace"):
            continue
        n = min(i2 - i1, j2 - j1)
        for k in range(n):
            times[i1 + k] = recognized[j1 + k][1]

    # Interpolate any unmatched words from their nearest known neighbors.
    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        return _proportional_word_times(lines, onset, offset)
    for i in range(len(times)):
        if times[i] is not None:
            continue
        before = max([k for k in known if k < i], default=None)
        after = min([k for k in known if k > i], default=None)
        if before is not None and after is not None:
            times[i] = times[before] + (times[after] - times[before]) * (i - before) / (after - before)
        else:
            times[i] = times[before] if before is not None else times[after]

    per_line: list[list[tuple[str, float]]] = [[] for _ in lines]
    for (line_i, word), t in zip(ref, times):
        per_line[line_i].append((word, round(t, 3)))
    return per_line


def _voice_script(lines: list[str], out_path: Path, speed: float | None) -> tuple[list[list[tuple[str, float]]], float]:
    """One TTS call for the whole script; returns per-line word times (from
    real forced alignment) and the clip's total length."""
    synthesize(" ".join(lines), out_path, speed=speed)
    _, _, length = _speech_span(out_path)
    return _align_script(lines, out_path), length


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


def _story_payload(script: Script, per_line: list[list[tuple[str, float]]], speech_duration: float,
                   kicker: str, tail: float) -> dict:
    """`per_line`: real word times for each line — hook, each beat, then outro."""
    hook_words_raw, beat_word_lists, outro_words = per_line[0], per_line[1:-1], per_line[-1]
    hook_words = [{"word": w, "at": t} for w, t in hook_words_raw]

    cards, captions = [], []
    for beat, words in zip(script.beats, beat_word_lists):
        start = words[0][1]
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

    outro_start = outro_words[0][1]
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
    per_line, speech_duration = _voice_script(lines, audio_path, speed=None)
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
        per_line, speech_duration = _voice_script(lines, audio_path, speed=speed)

    # Still short of the minimum even at the slowest natural pace? Hold the
    # outro a little longer, but don't turn it into a silent dead stretch —
    # a shorter video beats several seconds of frozen, silent outro.
    tail = min(MAX_TAIL_SECONDS, max(TAIL_SECONDS, MIN_VIDEO_SECONDS + 0.2 - speech_duration))
    assets = work_dir / "assets"
    _pad_audio(audio_path, assets / "voice.wav", tail)

    variables_path = work_dir / "story.json"
    payload = _story_payload(script, per_line, speech_duration, kicker, tail)
    payload["background"] = _cut_background(
        background_clips(), assets / "bg.mp4", payload["duration"], background_seed
    )
    variables_path.write_text(json.dumps({"story": json.dumps(payload)}))

    character = character_image()
    if character is not None:
        shutil.copy(character, assets / "character.png")

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
    _clean_render_cache(work_dir)
    return paths


def _clean_render_cache(work_dir: Path) -> None:
    """HyperFrames caches every extracted frame of the background video for
    reuse, but each of our videos uses a different gameplay stretch, so the
    cache only grows (~150 MB per video). Clear it after the day's renders."""
    npx = npx_path()
    subprocess.run(
        [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "clean", str(work_dir)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=hyperframes_env(npx),
    )
