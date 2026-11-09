"""Reading reference videos: probing, decoding frames into numpy arrays and
pulling the soundtrack, all through the ffmpeg bundled with imageio-ffmpeg
(no OpenCV, no moviepy decode path). Everything is downscaled: the analyzer
works on thumbnails, and only keyframes are kept at a readable size."""
from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gif")


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:  # pragma: no cover - imageio-ffmpeg is a hard dependency of moviepy
        return "ffmpeg"


def ffprobe_exe() -> str:
    """ffprobe next to the venv's ffmpeg, else `ffmpeg` itself is used to
    probe (its stderr banner) when no ffprobe is on the PATH."""
    import shutil

    for candidate in (Path(ffmpeg_exe()).with_name("ffprobe"), Path(ffmpeg_exe()).parent / "ffprobe"):
        if candidate.exists():
            return str(candidate)
    import sys

    venv = Path(sys.executable).parent / "ffprobe"
    if venv.exists():
        return str(venv)
    return shutil.which("ffprobe") or ""


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)

    @property
    def orientation(self) -> str:
        return "vertical" if self.height > self.width else ("square" if self.height == self.width else "horizontal")

    def to_dict(self) -> dict:
        return {"file": self.path.name, "width": self.width, "height": self.height, "fps": round(self.fps, 3),
                "duration": round(self.duration, 3), "has_audio": self.has_audio, "orientation": self.orientation}


def probe(path: Path) -> VideoInfo:
    probe_bin = ffprobe_exe()
    if probe_bin:
        out = subprocess.run(
            [probe_bin, "-v", "error", "-show_entries", "format=duration:stream=width,height,r_frame_rate,avg_frame_rate,codec_type",
             "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
        width = height = 0
        fps = 30.0
        has_audio = False
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not width:
                width, height = int(s.get("width", 0)), int(s.get("height", 0))
                rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "30/1"
                if rate and rate != "0/0":
                    num, _, den = rate.partition("/")
                    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
            elif s.get("codec_type") == "audio":
                has_audio = True
        duration = float(data.get("format", {}).get("duration") or 0.0)
        return VideoInfo(path=path, width=width, height=height, fps=fps or 30.0, duration=duration, has_audio=has_audio)
    # ffmpeg -i prints the stream description on stderr
    import re

    res = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    text = res.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    duration = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    width, height = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    m = re.search(r"([\d.]+) fps", text)
    fps = float(m.group(1)) if m else 30.0
    return VideoInfo(path=path, width=width, height=height, fps=fps, duration=duration, has_audio="Audio:" in text)


def read_frames(path: Path, fps: float, width: int, height: int | None = None, start: float = 0.0,
                duration: float | None = None, gray: bool = False) -> np.ndarray:
    """Frames at `fps`, scaled to `width` px wide (height keeps the aspect
    unless given), as (n, h, w, 3) RGB uint8 or (n, h, w) grayscale."""
    info = probe(path) if height is None else None
    if height is None:
        height = max(2, int(round(width * info.height / max(1, info.width) / 2)) * 2)
    cmd = [ffmpeg_exe(), "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vf", f"fps={fps},scale={width}:{height}", "-pix_fmt", "gray" if gray else "rgb24", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    channels = 1 if gray else 3
    frame_bytes = width * height * channels
    n = len(out) // frame_bytes
    arr = np.frombuffer(out[: n * frame_bytes], dtype=np.uint8)
    return arr.reshape(n, height, width) if gray else arr.reshape(n, height, width, 3)


def frame_at(path: Path, t: float, width: int) -> np.ndarray | None:
    """One RGB frame at time t, `width` px wide; None past the end."""
    frames = read_frames(path, fps=30, width=width, start=max(0.0, t), duration=0.08)
    return frames[0] if len(frames) else None


def extract_audio(path: Path, sr: int = 22050) -> np.ndarray | None:
    """Mono float32 samples at `sr`, or None when the file has no audio."""
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or len(res.stdout) < 4 * sr // 10:
        return None
    return np.frombuffer(res.stdout, dtype=np.float32).copy()


def save_jpeg(rgb: np.ndarray, out: Path, quality: int = 82, max_side: int | None = None) -> Path:
    from PIL import Image

    im = Image.fromarray(rgb)
    if max_side and max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=quality)
    return out


def jpeg_bytes(rgb: np.ndarray, quality: int = 70, max_side: int = 512) -> bytes:
    """A small JPEG for a vision call: `max_side` px on the long edge."""
    from PIL import Image

    im = Image.fromarray(rgb)
    if max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def write_silence(out: Path, seconds: float, sr: int = 22050) -> Path:
    import soundfile

    out.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(out), np.zeros(int(sr * max(seconds, 0.1)), dtype="float32"), sr)
    return out
