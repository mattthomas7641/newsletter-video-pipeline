"""Composes narration audio + captions + background into a vertical MP4.

Background: if you've dropped your own gameplay clips (e.g. Subway Surfers
footage you have the rights to use) into assets/background/*.mp4, one is
picked at random, looped, and center-cropped to fill the frame. Until then,
a generated animated placeholder is used instead — no copyrighted footage
is downloaded or scraped by this script.

Character: drop any number of cutout images (transparent PNGs) into
assets/character/. One is shown per story segment, bouncing gently, and the
pool is shuffled once per render and cycled through so you get a different
pose every segment instead of the same static image the whole video. An
empty assets/character/ just skips the overlay.
"""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoClip,
    VideoFileClip,
    concatenate_videoclips,
)

from .script_writer import Beat
from .tts import audio_duration_seconds, synthesize

WIDTH, HEIGHT = 1080, 1920
FPS = 30

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_DIR = PROJECT_ROOT / "assets" / "background"
CHARACTER_DIR = PROJECT_ROOT / "assets" / "character"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
AUDIO_DIR = PROJECT_ROOT / "output" / "_audio"

FALLBACK_FONT_CANDIDATES = [
    FONTS_DIR / "Anton-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
]


def _pick_font() -> str:
    for candidate in FALLBACK_FONT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        "No usable font found. Drop a .ttf into assets/fonts/ "
        "(e.g. Anton-Regular.ttf, a common brainrot-caption font)."
    )


def _generated_background(duration: float) -> VideoClip:
    """A moving gradient placeholder — swap in real gameplay clips later."""
    phase_offset = random.uniform(0, 2 * np.pi)

    def make_frame(t):
        phase = t * 0.6 + phase_offset
        y = np.linspace(0, 1, HEIGHT).reshape(-1, 1)
        hue = (y + phase * 0.15) % 1.0
        r = (np.sin(2 * np.pi * hue) * 0.5 + 0.5) * 255
        g = (np.sin(2 * np.pi * hue + 2.094) * 0.5 + 0.5) * 255
        b = (np.sin(2 * np.pi * hue + 4.188) * 0.5 + 0.5) * 255
        frame = np.stack([r, g, b], axis=-1).astype(np.uint8)
        frame = np.repeat(frame, WIDTH, axis=1)
        return frame

    return VideoClip(make_frame, duration=duration).with_fps(FPS)


def _background_clip(duration: float) -> VideoClip:
    clips = list(BACKGROUND_DIR.glob("*.mp4"))
    if not clips:
        return _generated_background(duration)

    src = VideoFileClip(str(random.choice(clips)))
    if src.duration < duration:
        loops_needed = int(duration // src.duration) + 1
        src = concatenate_videoclips([src] * loops_needed)
    src = src.subclipped(0, duration)

    scale = max(WIDTH / src.w, HEIGHT / src.h)
    src = src.resized(scale)
    x_center, y_center = src.w / 2, src.h / 2
    src = src.cropped(
        x_center=x_center, y_center=y_center, width=WIDTH, height=HEIGHT
    )
    return src


def _character_image_pool() -> list[Path]:
    return sorted(CHARACTER_DIR.glob("*.png")) + sorted(CHARACTER_DIR.glob("*.jpg"))


def _character_clip(duration: float, image_path: Path) -> ImageClip:
    img = ImageClip(str(image_path)).with_duration(duration)
    img = img.resized(width=WIDTH * 0.6)

    def bounce_scale(t):
        return 1.0 + 0.04 * abs(np.sin(t * 6))

    img = img.resized(bounce_scale)
    img = img.with_position(("center", "bottom"))
    return img


def _caption_clip(text: str, duration: float, font: str) -> TextClip:
    caption = TextClip(
        font=font,
        text=text,
        font_size=64,
        color="white",
        stroke_color="black",
        stroke_width=6,
        size=(int(WIDTH * 0.85), None),
        method="caption",
        text_align="center",
    ).with_duration(duration)
    caption = caption.with_position(("center", HEIGHT * 0.10))
    return caption


def _title_card(text: str, duration: float, font: str) -> VideoClip:
    bg = ColorClip(size=(WIDTH, HEIGHT), color=(10, 10, 10)).with_duration(duration)
    caption = TextClip(
        font=font,
        text=text,
        font_size=80,
        color="yellow",
        stroke_color="black",
        stroke_width=8,
        size=(int(WIDTH * 0.85), None),
        method="caption",
        text_align="center",
    ).with_duration(duration).with_position("center")
    return CompositeVideoClip([bg, caption], size=(WIDTH, HEIGHT))


def _beat_clip(beat: Beat, index: int, font: str, character_image: Path | None) -> VideoClip:
    audio_path = AUDIO_DIR / f"beat_{index:02d}.wav"
    synthesize(beat.text, audio_path)
    duration = audio_duration_seconds(audio_path) + 0.4

    audio = AudioFileClip(str(audio_path))
    background = _background_clip(duration)
    layers = [background]

    if character_image is not None:
        layers.append(_character_clip(duration, character_image))

    layers.append(_caption_clip(beat.caption, duration, font))

    composite = CompositeVideoClip(layers, size=(WIDTH, HEIGHT)).with_duration(duration)
    composite = composite.with_audio(audio)
    return composite


def build_video(beats: list[Beat], out_path: Path, headline: str = "TLDR Daily") -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    font = _pick_font()

    character_pool = _character_image_pool()
    if character_pool:
        random.shuffle(character_pool)

    segments = [_title_card(headline, 2.0, font)]
    for i, beat in enumerate(beats):
        character_image = character_pool[i % len(character_pool)] if character_pool else None
        segments.append(_beat_clip(beat, i, font, character_image))
    segments.append(_title_card("Follow for more brainrot news", 2.0, font))

    final = concatenate_videoclips(segments, method="compose")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        str(out_path), fps=FPS, codec="libx264", audio_codec="aac", logger=None
    )
    return out_path
