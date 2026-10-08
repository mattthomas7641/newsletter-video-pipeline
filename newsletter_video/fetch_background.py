"""Downloads a gameplay/background video from a URL (YouTube etc.) into
assets/background/, where video_builder.py picks it up automatically.

Usage:
    python -m newsletter_video.fetch_background "https://youtube.com/watch?v=..."

You are responsible for having the right to use whatever you download here
(e.g. footage you're allowed to reuse, or your own recordings) — this is
just a thin wrapper around yt-dlp, it doesn't check licensing for you.
"""
from __future__ import annotations

import sys
from pathlib import Path

import imageio_ffmpeg
import yt_dlp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_DIR = PROJECT_ROOT / "assets" / "background"


def download_background(url: str) -> Path:
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        # Cap resolution — we crop to 1080x1920 anyway, no need for 4K.
        "format": "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/best[height<=1920][ext=mp4]/best",
        "outtmpl": str(BACKGROUND_DIR / "%(title).80s.%(ext)s"),
        "merge_output_format": "mp4",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if path.suffix != ".mp4":
            path = path.with_suffix(".mp4")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m newsletter_video.fetch_background <url> [<url2> ...]")
        sys.exit(1)
    for url in sys.argv[1:]:
        out = download_background(url)
        print(f"Saved: {out}")
