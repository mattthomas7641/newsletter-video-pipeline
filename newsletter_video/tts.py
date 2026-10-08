"""Text-to-speech backend, pluggable via the TTS_PROVIDER env var.

Default: macOS's built-in `say` command — free, local, no API key, always
available on this machine. Good enough placeholder voice until you wire up
something more expressive.

Optional: ElevenLabs (TTS_PROVIDER=elevenlabs), for a custom/cloned voice —
e.g. a Peter-Griffin-style voice you've added to your own ElevenLabs Voice
Library. Requires ELEVENLABS_API_KEY and VOICE_ID in your .env. This script
does not source, scrape, or download any third-party voice model for you —
bring your own account and voice.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import imageio_ffmpeg

DEFAULT_SAY_VOICE = "Samantha"  # any voice from `say -v ?`

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


def synthesize(text: str, out_path: Path) -> Path:
    """Renders `text` to a WAV/MP3 file at `out_path` and returns it."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = os.environ.get("TTS_PROVIDER", "say")
    if provider == "elevenlabs":
        return _elevenlabs_backend(text, out_path)
    return _say_backend(text, out_path)


def audio_duration_seconds(path: Path) -> float:
    from moviepy import AudioFileClip

    with AudioFileClip(str(path)) as clip:
        return clip.duration


if __name__ == "__main__":
    out = synthesize("Yo, this is a test of the brainrot news voice.", Path("/tmp/tldr_tts_test.wav"))
    print(f"Wrote {out}, duration={audio_duration_seconds(out):.2f}s")
