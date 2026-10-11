"""Text-to-speech backend, pluggable via the TTS_PROVIDER env var.

Default: Kokoro-82M via the HyperFrames CLI (TTS_PROVIDER=kokoro) — a
natural-sounding neural voice that runs locally, free, no API key. Needs
`kokoro-onnx` + `soundfile` in the venv; the voice data (~27 MB) downloads
once on first use. Pick a voice with KOKORO_VOICE (`npx hyperframes tts
--list`) and pace with KOKORO_SPEED.

VOICE_EFFECT=cartoon (off by default) pitches whatever voice comes out up into
a squeaky, nasal cartoon register without changing its pace; CARTOON_PITCH
sets how far (1.0 = off). VOICE_EFFECT=none (default) keeps the natural voice.

Fallback: macOS's built-in `say` command (TTS_PROVIDER=say) — robotic, but
always available on this machine.

Optional: ElevenLabs (TTS_PROVIDER=elevenlabs), for a custom/cloned voice —
e.g. a Peter-Griffin-style voice you've added to your own ElevenLabs Voice
Library. Requires ELEVENLABS_API_KEY and VOICE_ID in your .env. This script
does not source, scrape, or download any third-party voice model for you —
bring your own account and voice.

Voicebox (TTS_PROVIDER=voicebox) — a cloned voice via the local Voicebox app
(voicebox.sh, MIT-licensed, runs entirely on this machine). Requires the
Voicebox app running (its API listens on localhost:17493) and
VOICEBOX_PROFILE_ID set to a voice profile you've already cloned there from
your own reference audio — this script doesn't source or clone the
reference voice for you.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np

DEFAULT_SAY_VOICE = "Samantha"  # any voice from `say -v ?`
DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_SPEED = "1.1"  # a touch quicker reads better for shorts
# Pinned to match hf_template/package.json so renders stay reproducible.
HYPERFRAMES_VERSION = "0.8.141"

# Use the ffmpeg binary bundled with imageio-ffmpeg (a pip dependency of
# moviepy) instead of relying on a system-wide ffmpeg on PATH.
_FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def _say_backend(text: str, out_path: Path) -> Path:
    aiff_path = out_path.with_suffix(".aiff")
    voice = os.environ.get("SAY_VOICE", DEFAULT_SAY_VOICE)
    subprocess.run(
        ["say", "-v", voice, "-o", str(aiff_path), text],
        check=True,
    )
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(aiff_path), str(out_path)],
        check=True,
    )
    aiff_path.unlink(missing_ok=True)
    return out_path


def npx_path() -> str:
    """launchd runs with a bare PATH, so fall back to an nvm-installed node."""
    found = os.environ.get("HYPERFRAMES_NPX") or shutil.which("npx")
    if found:
        return found
    nvm = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/npx")))
    if nvm:
        return nvm[-1]
    raise FileNotFoundError("npx not found — install Node.js or set HYPERFRAMES_NPX")


def hyperframes_env(npx: str) -> dict:
    """HyperFrames shells out to ffmpeg/ffprobe, node and (for TTS) a Python
    with kokoro-onnx; this venv (a conda env) provides all but node."""
    venv_bin = str(Path(sys.executable).parent)
    path = os.pathsep.join([venv_bin, str(Path(npx).parent), os.environ.get("PATH", "")])
    return {**os.environ, "PATH": path, "HYPERFRAMES_PYTHON": sys.executable}


# Kokoro model files, downloaded once by `hyperframes tts` (or its first use).
KOKORO_DIR = Path.home() / ".cache" / "hyperframes" / "tts"
KOKORO_MODEL = KOKORO_DIR / "models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = KOKORO_DIR / "voices" / "voices-v1.0.bin"
_kokoro_model = None


def _kokoro_in_process(text: str, out_path: Path, voice: str, speed: float) -> None:
    """Same synthesis as `hyperframes tts`, but the model is loaded once per
    run instead of once per line (saves ~6s of startup per spoken line)."""
    global _kokoro_model
    import soundfile
    from kokoro_onnx import Kokoro

    if _kokoro_model is None:
        _kokoro_model = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    lang = "en-gb" if voice.startswith("b") else "en-us"

    def create(value: str):
        try:
            return _kokoro_model.create(value, voice=voice, speed=speed, lang=lang)
        except IndexError:  # line too long for one pass: split at the space nearest the middle
            if len(value) < 2:
                raise
            spaces = [i for i, c in enumerate(value) if c.isspace()] or [len(value) // 2]
            mid = min(spaces, key=lambda i: abs(i - len(value) // 2))
            (left, rate), (right, _) = create(value[:mid]), create(value[mid:])
            return np.concatenate([left, right]), rate

    samples, rate = create(text)
    soundfile.write(str(out_path), samples, rate)


def _kokoro_backend(text: str, out_path: Path, speed: float | None = None) -> Path:
    voice = os.environ.get("KOKORO_VOICE", DEFAULT_KOKORO_VOICE)
    speed = float(speed or os.environ.get("KOKORO_SPEED", DEFAULT_KOKORO_SPEED))
    if KOKORO_MODEL.exists() and KOKORO_VOICES.exists():
        _kokoro_in_process(text, out_path, voice, speed)
        return out_path
    # First run on a new machine: let the HyperFrames CLI download the model.
    npx = npx_path()
    subprocess.run(
        [npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "tts", text,
         "--voice", voice, "--speed", str(speed), "--output", str(out_path), "--json"],
        check=True,
        stdout=subprocess.DEVNULL,
        env=hyperframes_env(npx),
    )
    return out_path


VOICEBOX_BASE_URL = "http://localhost:17493"
DEFAULT_VOICEBOX_ENGINE = "qwen"
DEFAULT_VOICEBOX_MODEL_SIZE = "0.6B"
DEFAULT_VOICEBOX_INSTRUCT = ""  # natural delivery by default — "more emphasis" instructions
# pushed Qwen3-TTS into exaggerated delivery that distorted sibilants into a lisp.
VOICEBOX_TIMEOUT_SECONDS = 120
VOICEBOX_STARTUP_TIMEOUT_SECONDS = 90


def _voicebox_is_up() -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{VOICEBOX_BASE_URL}/health", timeout=5):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _ensure_voicebox_running() -> None:
    """The daily run is unattended (launchd at 7am) and Voicebox is a
    regular desktop app, not a daemon — its server can also die between
    runs (observed once: the backend process exited while the app shell
    stayed up). Launch/relaunch it and wait for the API before generating."""
    import subprocess
    import time

    if _voicebox_is_up():
        return
    subprocess.run(["open", "-a", "Voicebox"], check=True)
    deadline = time.monotonic() + VOICEBOX_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _voicebox_is_up():
            return
        time.sleep(3)
    raise RuntimeError(
        f"Voicebox didn't come up within {VOICEBOX_STARTUP_TIMEOUT_SECONDS}s of launching it."
    )


def _voicebox_wait_for_generation(gen_id: str, already_done: str | None) -> None:
    """/generate/{id}/status is a long-lived SSE stream, not a snapshot to
    repoll — read it as one connection and consume lines as they arrive."""
    import json
    import urllib.request

    if already_done == "completed":
        return
    if already_done == "failed":
        raise RuntimeError("Voicebox generation failed")

    url = f"{VOICEBOX_BASE_URL}/generate/{gen_id}/status"
    with urllib.request.urlopen(url, timeout=VOICEBOX_TIMEOUT_SECONDS) as resp:
        for raw_line in resp:
            line = raw_line.decode().strip()
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:") :].strip())
            status = payload.get("status")
            if status == "failed":
                raise RuntimeError(f"Voicebox generation failed: {payload.get('error')}")
            if status == "completed":
                return
    raise RuntimeError("Voicebox status stream ended without a terminal status")


def _voicebox_backend(text: str, out_path: Path, speed: float | None = None) -> Path:
    import json
    import urllib.error
    import urllib.request

    _ensure_voicebox_running()
    profile_id = os.environ["VOICEBOX_PROFILE_ID"]
    engine = os.environ.get("VOICEBOX_ENGINE", DEFAULT_VOICEBOX_ENGINE)
    model_size = os.environ.get("VOICEBOX_MODEL_SIZE", DEFAULT_VOICEBOX_MODEL_SIZE)
    instruct = os.environ.get("VOICEBOX_INSTRUCT", DEFAULT_VOICEBOX_INSTRUCT)

    request_body = {"profile_id": profile_id, "text": text, "engine": engine, "model_size": model_size}
    if instruct:
        request_body["instruct"] = instruct
    payload = json.dumps(request_body).encode()
    req = urllib.request.Request(
        f"{VOICEBOX_BASE_URL}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            gen = json.load(resp)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Couldn't reach Voicebox at localhost:17493 — is the Voicebox app running?"
        ) from exc
    gen_id = gen["id"]
    _voicebox_wait_for_generation(gen_id, gen.get("status"))

    with urllib.request.urlopen(f"{VOICEBOX_BASE_URL}/audio/{gen_id}", timeout=30) as resp:
        out_path.write_bytes(resp.read())

    if speed and speed != 1.0:
        _apply_speed(out_path, speed)
    return out_path


def _apply_speed(path: Path, speed: float) -> None:
    """Time-stretches audio by `speed` in place (atempo supports 0.5-2.0,
    comfortably covering this project's MIN_SPEED..MAX_SPEED range)."""
    tmp = path.with_name(f"{path.stem}.spd{path.suffix}")
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(path), "-af", f"atempo={speed:.4f}", str(tmp)],
        check=True,
    )
    tmp.replace(path)


def _elevenlabs_backend(text: str, out_path: Path) -> Path:
    api_key = os.environ["ELEVENLABS_API_KEY"]
    voice_id = os.environ["VOICE_ID"]
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
    )
    with open(out_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return out_path


DEFAULT_VOICE_EFFECT = "none"
DEFAULT_CARTOON_PITCH = "1.45"


def _cartoon_effect(path: Path) -> None:
    """Raises pitch by CARTOON_PITCH while keeping duration (resample up,
    then time-stretch back), plus a mid/upper-mid boost for a nasal tone."""
    import soundfile

    pitch = float(os.environ.get("CARTOON_PITCH", DEFAULT_CARTOON_PITCH))
    if pitch == 1.0:
        return
    rate = soundfile.info(str(path)).samplerate
    filters = ",".join([
        f"asetrate={rate}*{pitch}",
        f"aresample={rate}",
        f"atempo={1 / pitch:.4f}",
        "highpass=f=180",
        "equalizer=f=1400:t=o:w=1.2:g=5",
        "equalizer=f=3200:t=o:w=1:g=3",
        "alimiter=limit=0.9",
    ])
    tmp = path.with_name(f"{path.stem}.fx{path.suffix}")
    subprocess.run(
        [_FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(path), "-af", filters, str(tmp)],
        check=True,
    )
    tmp.replace(path)


_SCALES = {"k": "thousand", "m": "million", "b": "billion", "t": "trillion"}
_MONEY_RE = re.compile(
    r"\$(\d[\d,]*)(?:\.(\d+))?"
    r"(?:\s?([KMBT])\b|\s(thousand|million|billion|trillion)\b)?",
    re.IGNORECASE,
)


def _say_money(match: re.Match) -> str:
    whole, decimals = match.group(1).replace(",", ""), match.group(2) or ""
    scale = match.group(4) or _SCALES.get((match.group(3) or "").lower())
    if scale:  # "$1.8 billion", "$2.5B" -> "1.8 billion dollars"
        number = whole + (f".{decimals}" if decimals else "")
        return f"{number} {scale.lower()} dollars"
    cents = int((decimals + "00")[:2]) if decimals else 0
    if int(whole) == 0 and decimals:  # "$0.05" -> "5 cents", "$0.005" -> "0.5 cents"
        if len(decimals) > 2:
            return f"{float('0.' + decimals) * 100:g} cents"
        return "1 cent" if cents == 1 else f"{cents} cents"
    dollars = "1 dollar" if int(whole) == 1 else f"{int(whole):,} dollars"
    return f"{dollars} and {cents} cents" if cents else dollars


# Coined/brand words the voice gets wrong, respelled the way they're meant
# to be said. Matched case-insensitively as whole words.
PRONUNCIATIONS = {
    "Mathocalypse": "Math-apocalypse",
}


def speakable(text: str) -> str:
    """Rewrites symbols TTS voices read literally ("zero dot zero five") into
    the words a person would say, and respells known coined words."""
    for word, spoken in PRONUNCIATIONS.items():
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text, flags=re.IGNORECASE)
    text = _MONEY_RE.sub(_say_money, text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)x\b", r"\1 times", text)
    text = re.sub(r"~\s?(?=\d)", "about ", text)
    text = text.replace(" & ", " and ").replace(" vs. ", " versus ").replace(" vs ", " versus ")
    return text


def base_speed() -> float:
    return float(os.environ.get("KOKORO_SPEED", DEFAULT_KOKORO_SPEED))


def synthesize(text: str, out_path: Path, speed: float | None = None) -> Path:
    """Renders `text` to a WAV/MP3 file at `out_path` and returns it.
    `speed` overrides KOKORO_SPEED (Kokoro only)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = speakable(text)
    provider = os.environ.get("TTS_PROVIDER", "kokoro")
    if provider == "voicebox":
        _voicebox_backend(text, out_path, speed)
    elif provider == "elevenlabs":
        _elevenlabs_backend(text, out_path)
    elif provider == "say":
        _say_backend(text, out_path)
    else:
        _kokoro_backend(text, out_path, speed)
    if os.environ.get("VOICE_EFFECT", DEFAULT_VOICE_EFFECT) == "cartoon":
        _cartoon_effect(out_path)
    return out_path


def audio_duration_seconds(path: Path) -> float:
    from moviepy import AudioFileClip

    with AudioFileClip(str(path)) as clip:
        return clip.duration


if __name__ == "__main__":
    out = synthesize("Yo, this is a test of the brainrot news voice.", Path("/tmp/tldr_tts_test.wav"))
    print(f"Wrote {out}, duration={audio_duration_seconds(out):.2f}s")
