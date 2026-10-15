"""Renders scene plans (scene_plan.py) in the "explainer" video style:
no avatar, no footage — a typographic editorial explainer built from the
fixed scene library in hf_template_explainer/ (design system: DESIGN.md).

Selected with VIDEO_STYLE=explainer. The default (unset, or "avatar") is
the original look in video_builder.py, which this module leaves untouched:
it reuses video_builder's voicing, forced alignment and pacing, and only
swaps the composition and the payload it is fed.

Scene timing comes from the real word timestamps of the narration: each
scene appears when the voice reaches its line, list rows / timeline steps /
chart bars reveal when their words are spoken (or evenly through the line
when they aren't said verbatim), a stat counts up when its number is said,
and a subtle lower-third caption follows the words.

Re-render a saved video in this style, or preview a plan, without the
daily pipeline:

    python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/x.mp4
    python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/x.mp4 --write-plan  # one Claude call
    python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/x.mp4 --plan plan.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from .scene_plan import ScenePlan, ScenePlanError, plan_from_script, validate_plan
from .tts import HYPERFRAMES_VERSION, base_speed, hyperframes_env, npx_path
from .video_builder import (
    MAX_SPEED,
    MAX_TAIL_SECONDS,
    MAX_VIDEO_SECONDS,
    MIN_SPEED,
    MIN_VIDEO_SECONDS,
    RENDER_ATTEMPTS,
    RENDER_CRF,
    TAIL_SECONDS,
    TARGET_MAX_SPEED,
    TARGET_VIDEO_SECONDS,
    _pad_audio,
    _voice_script,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "hf_template_explainer"

VIDEO_STYLES = ("avatar", "explainer")
THEMES = ("light", "dark")
SCENE_LEAD_SECONDS = 0.2  # a scene lands this long before its first word is spoken
CAPTION_WORDS = 4  # max words per lower-third chunk
CAPTION_CHARS = 24  # ...or fewer, if the words are long
CUE_MIN_DELAY = 0.35  # a cued element never lands before the scene has settled
CUE_SPAN = 0.7  # un-cued items spread across this fraction of the spoken line
FIRST_CUE_MAX = 1.6  # the first item lands by then even when the voice names it later (no empty hero)
CUE_MAX_GAP = 1.4  # ...and each later item within this much of the previous one
EXPLAINER_MIN_SPEED = 0.88  # a touch slower than the avatar floor: a 100-word line from a fast cloned voice still reaches ~38-40 s
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
              "was", "were", "it", "its", "this", "that", "than", "then", "now", "how", "what", "who"}

log = logging.getLogger("newsletter_video")


def video_style() -> str:
    """VIDEO_STYLE from the environment, validated: "avatar" (default) or
    "explainer". Read at call time so a value from .env takes effect."""
    style = os.environ.get("VIDEO_STYLE", "avatar").strip().lower() or "avatar"
    if style not in VIDEO_STYLES:
        raise ValueError(f"VIDEO_STYLE={style!r} is not one of {', '.join(VIDEO_STYLES)}")
    return style


def explainer_mode() -> bool:
    return video_style() == "explainer"


def video_theme() -> str:
    theme = os.environ.get("VIDEO_THEME", "light").strip().lower() or "light"
    if theme not in THEMES:
        raise ValueError(f"VIDEO_THEME={theme!r} is not one of {', '.join(THEMES)}")
    return theme


def plan_for(script) -> ScenePlan:
    """The scene plan to render for a script: its own (an ExplainerScript
    from explainer_writer) or, for a classic hook/beats/outro Script, a
    structural plan derived from it."""
    plan = getattr(script, "plan", None)
    if plan is not None:
        return plan
    log.warning("Explainer: %r has no scene plan (rule-based script); rendering a structural plan derived from it",
                getattr(script, "title", "?"))
    return plan_from_script(script)


# --- timing -------------------------------------------------------------------

def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _key_words(text: str) -> list[str]:
    """The words of an on-screen item worth looking for in the spoken line:
    long enough to be distinctive, not function words."""
    return [w for w in (_norm(t) for t in text.split()) if len(w) >= 4 and w not in _STOPWORDS]


def _same_word(spoken: str, key: str) -> bool:
    """Exact, or the same 5-letter stem ("seniors" / "senior", "narrows" /
    "narrower"): the on-screen wording and the spoken line rarely inflect
    the same way."""
    return spoken == key or (len(spoken) >= 5 and len(key) >= 5 and spoken[:5] == key[:5])


def cue_times(items: list[str], words: list[tuple[str, float]], start: float, end: float) -> list[float]:
    """When each on-screen item (list row, timeline step, chart bar) should
    appear: the moment the voice says one of its key words, searched in
    order so reveals never go backwards; items the voice doesn't name are
    spread evenly through the line. Always non-decreasing and inside the
    scene."""
    spoken = [(_norm(w), t) for w, t in words]
    floor = start + CUE_MIN_DELAY
    span_end = max(start + CUE_SPAN * (end - start), floor)
    cues: list[float | None] = []
    cursor = 0
    for item in items:
        keys = set(_key_words(item))
        hit = None
        for j in range(cursor, len(spoken)):
            if any(_same_word(spoken[j][0], k) for k in keys):
                hit = max(spoken[j][1], floor)
                cursor = j + 1
                break
        cues.append(hit)
    # Unknown runs are spread evenly between their known neighbours; a run
    # at the start begins at the floor, a run at the end reaches span_end.
    n = len(cues)
    i = 0
    while i < n:
        if cues[i] is not None:
            i += 1
            continue
        j = i
        while j < n and cues[j] is None:
            j += 1
        lo = cues[i - 1] if i > 0 else floor
        hi = cues[j] if j < n else max(span_end, lo)
        if i == 0 and j == n:
            steps = max(n - 1, 1)
            for k in range(n):
                cues[k] = lo + (hi - lo) * k / steps
        elif i == 0:
            for k in range(j):
                cues[k] = lo + (hi - lo) * k / j
        elif j == n:
            for k in range(i, n):
                cues[k] = lo + (hi - lo) * (k - i + 1) / (n - i)
        else:
            for k in range(i, j):
                cues[k] = lo + (hi - lo) * (k - i + 1) / (j - i + 1)
        i = j
    result: list[float] = []
    last = floor
    for k, c in enumerate(cues):
        # Speech sync, but never a hole: the k-th item lands no later than
        # FIRST_CUE_MAX + k * CUE_MAX_GAP after the scene starts, even when the
        # voice names it later (an empty tile reads as broken, not as timing).
        c = min(float(c), start + FIRST_CUE_MAX + k * CUE_MAX_GAP)
        c = min(max(c, last), max(end - 0.3, last))
        result.append(round(c, 3))
        last = c
    return result


def _number_cue(value: str, words: list[tuple[str, float]], start: float) -> float:
    """When a stat's number is spoken: the first word containing a digit, or
    the first spoken number word; else shortly after the scene lands."""
    digits = re.sub(r"[^\d]", "", value)
    number_words = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                    "eleven", "twelve", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
                    "eighty", "ninety", "hundred", "thousand", "million", "billion", "trillion", "half"}
    for w, t in words:
        n = _norm(w)
        if (digits and any(ch.isdigit() for ch in n)) or n in number_words:
            return round(max(t, start + CUE_MIN_DELAY), 3)
    return round(start + 0.5, 3)


def _emphasis_cue(emphasis: str, words: list[tuple[str, float]], start: float) -> float | None:
    if not emphasis:
        return None
    keys = set(_key_words(emphasis)) or {_norm(emphasis.split()[0])}
    for w, t in words:
        if any(_same_word(_norm(w), k) for k in keys):
            return round(max(t, start + 0.5), 3)
    return None


_GLUE = {"a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "than",
         "as", "by", "from", "into", "that", "its", "is", "are", "was", "were", "not", "no", "per"}
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                 "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
                 "hundred", "thousand", "million", "billion", "trillion", "point", "percent", "times",
                 "half", "dollars", "cents", "and"}
CAPTION_MAX_WORDS = 8  # a chunk may stretch this far to keep a phrase or number together


def _is_number_word(word: str) -> bool:
    w = _norm(word)
    return bool(w) and (any(ch.isdigit() for ch in w) or w in _NUMBER_WORDS)


def caption_chunks(words: list[tuple[str, float]]) -> list[list[tuple[str, float]]]:
    """Lower-third chunks that read as phrases: about 4 words / 24 characters,
    cut at punctuation, never cut inside a spoken number ("one point eight
    and five point one"), and never starting on a dangling function word
    ("and", "to", "of"...): such a word stays with the phrase before it."""
    chunks: list[list[tuple[str, float]]] = []
    chunk: list[tuple[str, float]] = []
    n = len(words)

    def clean_cut(left: str, right: str) -> bool:
        """A break between `left` and `right` leaves neither a chunk ending
        on a function word, one starting on one, nor a number split in two."""
        return (_norm(left) not in _GLUE and _norm(right) not in _GLUE
                and not (_is_number_word(left) and _is_number_word(right) and left[-1] not in ".!?"))

    for i, (word, at) in enumerate(words):
        chunk.append((word, at))
        nxt = words[i + 1][0] if i + 1 < n else None
        if nxt is None:
            break
        chars = sum(len(w) for w, _ in chunk)
        full = len(chunk) >= CAPTION_WORDS or chars >= CAPTION_CHARS
        ends_clause = word[-1] in ",.!?:;" and len(chunk) >= 2
        hard = len(chunk) >= CAPTION_MAX_WORDS
        if ends_clause or (full and clean_cut(word, nxt)):
            chunks.append(chunk)
            chunk = []
        elif hard:
            # Too long to wait for a clean cut at the end: cut at the last clean
            # spot inside the chunk and carry the rest forward.
            for j in range(len(chunk) - 1, 1, -1):
                if clean_cut(chunk[j - 1][0], chunk[j][0]):
                    chunks.append(chunk[:j])
                    chunk = chunk[j:]
                    break
            else:
                chunks.append(chunk)
                chunk = []
    if chunk:
        if len(chunk) == 1 and chunks and len(chunks[-1]) < CAPTION_MAX_WORDS:
            chunks[-1].extend(chunk)
        else:
            chunks.append(chunk)
    return chunks


def story_payload(plan: ScenePlan, per_line: list[list[tuple[str, float]]], speech_duration: float,
                  kicker: str, tail: float, theme: str = "light") -> dict:
    """The `story` composition variable: scenes with absolute start/end
    times and reveal cues, plus lower-third caption chunks. `per_line` holds
    the real word times of each scene's spoken line, in order."""
    if len(per_line) != len(plan.scenes):
        raise ValueError(f"{len(per_line)} timed lines for {len(plan.scenes)} scenes")
    duration = round(speech_duration + tail, 3)
    starts = [max(0.0, words[0][1] - SCENE_LEAD_SECONDS) if words else 0.0 for words in per_line]
    starts[0] = 0.0
    # Starts never go backwards (a line the aligner mistimed can't swallow its neighbour).
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + 1.0)
    scenes = []
    for i, (scene, words) in enumerate(zip(plan.scenes, per_line)):
        start = round(starts[i], 3)
        end = round(starts[i + 1], 3) if i + 1 < len(starts) else duration
        entry: dict = {"type": scene.type, "start": start, "end": end, **scene.fields}
        f = scene.fields
        if scene.type == "statement" and f.get("emphasis"):
            at = _emphasis_cue(f["emphasis"], words, start)
            if at is not None:
                entry["emphasisAt"] = at
        elif scene.type == "stat":
            entry["countAt"] = _number_cue(f["value"], words, start)
        elif scene.type == "list":
            entry["cues"] = cue_times(f["items"], words, start, end)
        elif scene.type == "timeline":
            entry["cues"] = cue_times([s["label"] + " " + s["text"] for s in f["steps"]], words, start, end)
        elif scene.type == "chart":
            entry["cues"] = cue_times([p["label"] for p in f["series"]], words, start, end)
        elif scene.type == "comparison":
            entry["cues"] = cue_times([f["left"]["label"], f["right"]["label"]], words, start, end)
        elif scene.type == "diagram":
            entry["cues"] = cue_times([n["label"] + " " + n["text"] for n in f["nodes"]], words, start, end)
        elif scene.type == "device":
            entry["cues"] = cue_times(f["lines"], words, start, end)
        scenes.append(entry)

    all_words = [(w, t) for words in per_line for w, t in words]
    chunks = caption_chunks(all_words)
    captions = []
    for j, chunk in enumerate(chunks):
        nxt = chunks[j + 1][0][1] if j + 1 < len(chunks) else speech_duration
        captions.append({
            "words": [w for w, _ in chunk],
            "at": [t for _, t in chunk],
            "start": chunk[0][1],
            "end": round(min(nxt, speech_duration), 3),
        })
    return {"theme": theme, "kicker": kicker, "duration": duration, "scenes": scenes, "captions": captions}


# --- rendering ------------------------------------------------------------------

def _prepare_workdir(work_dir: Path) -> None:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(
        TEMPLATE_DIR, work_dir,
        ignore=shutil.ignore_patterns("renders", "snapshots", "node_modules", ".thumbnails", "assets"),
    )
    (work_dir / "assets").mkdir()


def _voice_paced(lines: list[str], audio_path: Path) -> tuple[list[list[tuple[str, float]]], float]:
    """One TTS call, re-voiced faster or slower if the result misses the
    40-45 s window. Mirrors video_builder.build_short's pacing so both
    styles land the same lengths."""
    per_line, speech_duration = _voice_script(lines, audio_path, speed=None)
    floor = MIN_VIDEO_SECONDS - TAIL_SECONDS + 1.0
    target = TARGET_VIDEO_SECONDS - TAIL_SECONDS - 0.3
    hard = MAX_VIDEO_SECONDS - TAIL_SECONDS - 0.5
    speed = None
    if speech_duration > target:
        speed = min(base_speed() * speech_duration / target, TARGET_MAX_SPEED)
        if speech_duration * base_speed() / speed > hard:
            speed = min(base_speed() * speech_duration / hard, MAX_SPEED)
    elif speech_duration < floor:
        speed = max(base_speed() * speech_duration / floor, min(MIN_SPEED, EXPLAINER_MIN_SPEED))
    if speed is not None:
        per_line, speech_duration = _voice_script(lines, audio_path, speed=speed)
    return per_line, speech_duration


def render_plan(plan: ScenePlan, out_path: Path, audio_path: Path, work_dir: Path, kicker: str,
                theme: str | None = None) -> Path:
    theme = theme or video_theme()
    per_line, speech_duration = _voice_paced(plan.lines, audio_path)
    tail = min(MAX_TAIL_SECONDS, max(TAIL_SECONDS, MIN_VIDEO_SECONDS + 0.2 - speech_duration))
    assets = work_dir / "assets"
    _pad_audio(audio_path, assets / "voice.wav", tail)

    payload = story_payload(plan, per_line, speech_duration, kicker, tail, theme)
    variables_path = work_dir / "story.json"
    variables_path.write_text(json.dumps({"story": json.dumps(payload)}))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    npx = npx_path()
    cmd = [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render", str(work_dir),
           "--output", str(out_path), "--variables-file", str(variables_path),
           "--strict-variables", "--quiet", "--crf", RENDER_CRF]
    for attempt in range(RENDER_ATTEMPTS):
        try:
            subprocess.run(cmd, check=True, env=hyperframes_env(npx))
            break
        except subprocess.CalledProcessError:
            if attempt == RENDER_ATTEMPTS - 1:
                raise
    return out_path


_prepared: set[Path] = set()  # work dirs already set up in this process


def build_explainer_short(script, out_path: Path, audio_path: Path, kicker: str) -> Path:
    """The explainer-style counterpart of video_builder.build_short, called
    from it per video so numbering, isolation and metadata stay the
    caller's. The work dir sits next to the avatar one (_hf_work_explainer
    beside _hf_work) and is prepared once per process."""
    plan = plan_for(script)  # validated before any voicing
    work_dir = audio_path.parent.parent / "_hf_work_explainer"
    if work_dir not in _prepared or not (work_dir / "index.html").exists():
        _prepare_workdir(work_dir)
        _prepared.add(work_dir)
    log.info("Explainer: rendering %r (%d scenes: %s)", script.title, len(plan.scenes),
             ", ".join(s.type for s in plan.scenes))
    return render_plan(plan, out_path, audio_path, work_dir, kicker)


# --- CLI: re-render a saved sidecar in this style ------------------------------

def _plan_from_sidecar(meta: dict, plan_path: Path | None, write_plan: bool) -> ScenePlan:
    if plan_path is not None:
        raw = json.loads(plan_path.read_text())
        return validate_plan(raw["scenes"] if isinstance(raw, dict) else raw)
    if meta.get("scene_plan"):
        return validate_plan(meta["scene_plan"]["scenes"])
    if write_plan:
        from .explainer_writer import replan_sidecar

        return replan_sidecar(meta)
    return plan_from_script(meta["script"])


def main(argv: list[str] | None = None) -> None:
    import argparse

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.explainer_builder",
                                     description="Render a saved video's sidecar in the explainer style.")
    parser.add_argument("sidecar", type=Path, help="a <video>.json written by the pipeline")
    parser.add_argument("--out", type=Path, required=True, help="output .mp4")
    parser.add_argument("--plan", type=Path, help="render this scene plan JSON instead of deriving one")
    parser.add_argument("--write-plan", action="store_true",
                        help="ask Claude for a scene plan from the sidecar's script (one call)")
    parser.add_argument("--save-plan", type=Path, help="also write the plan used to this JSON file")
    parser.add_argument("--theme", choices=THEMES, help="override VIDEO_THEME")
    parser.add_argument("--date", type=date.fromisoformat, help="date for the kicker (default: sidecar folder)")
    args = parser.parse_args(argv)

    meta = json.loads(args.sidecar.read_text())
    try:
        plan = _plan_from_sidecar(meta, args.plan, args.write_plan)
    except ScenePlanError as exc:
        sys.exit("Invalid scene plan:\n  " + "\n  ".join(exc.errors))
    for note in plan.repairs:
        log.info("Plan repair: %s", note)
    if args.save_plan:
        args.save_plan.parent.mkdir(parents=True, exist_ok=True)
        args.save_plan.write_text(json.dumps({"scenes": plan.to_list()}, indent=2, ensure_ascii=False))
    try:
        day = args.date or date.fromisoformat(args.sidecar.parent.name)
    except ValueError:
        day = date.today()
    work_dir = args.out.parent / "_hf_work_explainer"
    _prepare_workdir(work_dir)
    kicker = f"TLDR · {day:%b} {day.day}".upper()
    render_plan(plan, args.out, args.out.with_suffix(".wav"), work_dir, kicker, args.theme)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
