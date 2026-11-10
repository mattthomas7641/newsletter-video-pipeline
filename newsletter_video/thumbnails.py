"""Every video's thumbnail is its own title page: the frame where the hook/title is fully on screen,
taken from the finished mp4 and laid out 16:9 for YouTube.

    <video>.title.png   the full 1080x1920 title frame
    <video>.thumb.jpg   1280x720 JPEG (< 2 MB): the title band of that frame, big and readable at small
                        size, on the video's own background (avx), or on a blurred, darkened copy of the
                        frame (explainer); the avatar format's hook card full-bleed over its own gameplay

The builders know when the title is complete (they have the word timings), so each one calls remember()
with that time and its layout; video_builder.build_shorts then calls make_for() right after the render.
That is one fast ffmpeg seek plus PIL, and any failure is logged and skipped: a thumbnail never fails a
video. A video rendered without a remembered time (an older sidecar, a re-render path that does not
report one) simply gets no thumbnail; detect_title_time() is only for the explicit backfill CLI:

    python -m newsletter_video.thumbnails output/2026-11-09            # missing thumbs of a day (detected)
    python -m newsletter_video.thumbnails output/2026-11-09 --sheet    # + a contact sheet
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("newsletter_video")

THUMB_SIZE = (1280, 720)
MAX_BYTES = 2 * 1024 * 1024
LAYOUTS = ("avx", "explainer", "avatar")
_remembered: dict[str, tuple[float, str]] = {}


def thumb_path(video: Path) -> Path:
    return video.with_name(video.stem + ".thumb.jpg")


def title_png_path(video: Path) -> Path:
    return video.with_name(video.stem + ".title.png")


def remember(video: Path, at, layout: str) -> None:
    """A builder reports when its title page is complete (seconds into the video) and which layout it is.
    `at` may be a payload-reading callable; anything that goes wrong here is ignored (no thumbnail)."""
    try:
        at = at() if callable(at) else at
        if layout in LAYOUTS and at is not None and at >= 0:
            _remembered[str(Path(video))] = (round(float(at), 3), layout)
    except Exception as exc:  # noqa: BLE001 - never cost the video
        log.warning("Thumbnail: could not time the title of %s (%s)", Path(video).name, exc)


def remembered(video: Path) -> tuple[float, str] | None:
    return _remembered.get(str(Path(video)))


# --- when the title is complete, per format ---------------------------------------------------------

def avx_title_time(payload: dict) -> float | None:
    """The hook's kinetic type has risen in (every line's words, plus the underline) and has not started to
    leave (it exits 0.28 s before the next scene)."""
    scenes = payload.get("scenes") or []
    if not scenes:
        return None
    first = scenes[0]
    data = first.get("data") or {}
    ends = [0.6]
    for ln in data.get("lines") or []:
        if isinstance(ln.get("at"), (int, float)):
            ends.append(ln["at"] + 0.8 + 0.07 * len(str(ln.get("text", "")).split()))
    if isinstance((data.get("underline") or {}).get("at"), (int, float)):
        ends.append(data["underline"]["at"] + 0.7)
    t = max(ends)
    limit = (scenes[1]["start"] - 0.45) if len(scenes) > 1 else payload.get("duration", t + 1) - 0.3
    return round(max(0.3, min(t, limit)), 3)


def explainer_title_time(payload: dict) -> float | None:
    """The title scene (the first) just before it hands over: its card has finished growing in."""
    scenes = payload.get("scenes") or []
    if not scenes:
        return None
    end = scenes[1]["start"] if len(scenes) > 1 else payload.get("duration", 3.0)
    return round(max(0.4, end - 0.45), 3)


def avatar_title_time(payload: dict) -> float | None:
    """The hook card holds the whole hook once its last word is spoken, until it exits (exitAt)."""
    hook = payload.get("hook") or {}
    words = hook.get("words") or []
    if not words:
        return None
    t = words[-1]["at"] + 0.45
    if isinstance(hook.get("exitAt"), (int, float)):
        t = min(t, hook["exitAt"] - 0.15)
    return round(max(0.3, t), 3)


# --- frames -----------------------------------------------------------------------------------------

def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_frame(video: Path, at: float, out_png: Path) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
                    str(out_png)], check=True, timeout=60)
    if not out_png.exists() or out_png.stat().st_size == 0:
        raise RuntimeError(f"no frame at {at:.2f}s in {video.name}")
    return out_png


def detect_title_time(video: Path, window: float = 12.0, fps: int = 5) -> float | None:
    """For videos rendered before titles were timed: the first scene change after the opening (a big jump
    between consecutive low-res frames) marks the end of the title page; the frame 0.55 s before it is the
    title fully shown. None when no change is found."""
    import numpy as np

    w, h = 54, 96
    proc = subprocess.run([_ffmpeg(), "-loglevel", "error", "-t", f"{window}", "-i", str(video), "-vf",
                           f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True, check=True, timeout=120)
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(frames) // (w * h)
    if n < 3:
        return None
    frames = frames[: n * w * h].reshape(n, h, w).astype(float)
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    for i, d in enumerate(diffs):
        t = (i + 1) / fps
        if t > 1.8 and d > 9.0:
            return round(max(0.5, t - 0.55), 3)
    return None


# --- the 16:9 layout -----------------------------------------------------------------------------------

def _fit(img, box_w: int, box_h: int):
    s = min(box_w / img.width, box_h / img.height)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), resample=3)


def _blurred_fill(frame):
    from PIL import ImageEnhance, ImageFilter

    W, H = THUMB_SIZE
    s = max(W / frame.width, H / frame.height)
    bg = frame.resize((round(frame.width * s), round(frame.height * s)), resample=3)
    top = (bg.height - H) // 3
    bg = bg.crop(((bg.width - W) // 2, top, (bg.width - W) // 2 + W, top + H))
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    return ImageEnhance.Brightness(bg).enhance(0.55)


def _ground(frame) -> tuple[int, int, int]:
    """The plain background of an avx frame: the top-right corner, above the eyebrow (nothing is drawn there)."""
    import numpy as np

    a = np.asarray(frame.convert("RGB").crop((frame.width - 90, 30, frame.width - 20, 130))).reshape(-1, 3)
    return tuple(int(x) for x in np.median(a, axis=0))


def compose(frame, layout: str):
    """The 1280x720 thumbnail from a 1080x1920 title frame."""
    from PIL import Image

    frame = frame.convert("RGB")
    if frame.size != (1080, 1920):
        frame = frame.resize((1080, 1920), resample=3)
    W, H = THUMB_SIZE
    if layout == "avx":
        # Puck on the left (as in the video), the hook type large on the right, on the video's own ground.
        canvas = Image.new("RGB", (W, H), _ground(frame))
        text = frame.crop((40, 170, 1040, 925))
        text = _fit(text, W - 400, H - 40)
        canvas.paste(text, (W - text.width - 10, (H - text.height) // 2))
        avatar = frame.crop((0, 908, 600, 1920))  # Puck stands at y ~940..1920 (the hook's underline ends above 905)
        avatar = _fit(avatar, 600, H)
        # Feather the crop's top and right edges into the canvas: the video's vignette makes that corner a
        # shade darker than the plain ground, which would otherwise show as a box around Puck.
        import numpy as np

        w, h = avatar.size
        fx = np.clip((w - np.arange(w)) / 90.0, 0, 1)[None, :]
        fy = np.clip(np.arange(h) / 18.0, 0, 1)[:, None]
        mask = Image.fromarray((np.minimum(fx, fy) * 255).astype("uint8"))
        canvas.paste(avatar, (-30, H - avatar.height), mask)
        return canvas
    canvas = _blurred_fill(frame)
    if layout == "avatar":
        # The hook card full-bleed over its own gameplay (a 16:9 window of the frame, no seams): pasting the
        # avatar in as a rectangle showed hard-edged gameplay around him.
        band = frame.crop((0, 205, 1080, 205 + 608)).resize((W, H), resample=3)
        return band
    # explainer: the title scene's middle band (kicker, title card) as large as it fits, centred
    band = _fit(frame.crop((60, 340, 1020, 1366)), W, H)  # icon + title card; the captions below are left out
    canvas.paste(band, ((W - band.width) // 2, (H - band.height) // 2))
    return canvas


def save_jpeg(img, out: Path) -> Path:
    for quality in (90, 84, 76, 68):
        img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        if out.stat().st_size < MAX_BYTES:
            break
    return out


def make_thumbnail(video: Path, at: float, layout: str) -> Path:
    """Writes <video>.title.png (the 9:16 frame) and <video>.thumb.jpg (16:9) and returns the JPEG."""
    from PIL import Image

    png = extract_frame(video, at, title_png_path(video))
    with Image.open(png) as frame:
        thumb = compose(frame, layout)
    return save_jpeg(thumb, thumb_path(video))


def make_for(video: Path) -> Path | None:
    """The pipeline's hook (video_builder.build_shorts): the thumbnail of a just-rendered video if its
    builder reported the title time, else None. Never raises."""
    info = remembered(video)
    if info is None:
        log.info("Thumbnail: no title time for %s; skipped", video.name)
        return None
    try:
        out = make_thumbnail(video, *info)
        log.info("Thumbnail: %s (title at %.2fs)", out.name, info[0])
        return out
    except Exception as exc:  # noqa: BLE001 - a thumbnail must never cost the video
        log.warning("Thumbnail for %s failed (%s: %s); the video is unaffected", video.name, type(exc).__name__, exc)
        return None


def layout_of(sidecar: dict) -> str:
    plan = sidecar.get("scene_plan") or {}
    style = plan.get("style") or (sidecar.get("look") or {}).get("style")
    if style == "avx":
        return "avx"
    if plan.get("scenes") or style == "explainer":
        return "explainer"
    return "avatar"


def backfill(folder: Path, force: bool = False) -> list[Path]:
    """Thumbnails for already-rendered videos of a day folder (title time detected from the video)."""
    made = []
    for video in sorted(folder.glob("*.mp4")):
        if thumb_path(video).exists() and not force:
            continue
        try:
            meta = json.loads(video.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {}
        at = detect_title_time(video)
        if at is None:
            log.warning("Thumbnail: no title page found in %s; skipped", video.name)
            continue
        made.append(make_thumbnail(video, at, layout_of(meta)))
        log.info("Thumbnail: %s (detected title at %.2fs)", thumb_path(video).name, at)
    return made


def contact_sheet(thumbs: list[Path], out: Path, cols: int = 4, width: int = 400) -> Path:
    from PIL import Image, ImageDraw

    h = width * 9 // 16
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (width + 12) + 12, rows * (h + 40) + 12), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(thumbs):
        with Image.open(p) as im:
            im = im.convert("RGB").resize((width, h), resample=3)
        x, y = 12 + (i % cols) * (width + 12), 12 + (i // cols) * (h + 40)
        sheet.paste(im, (x, y))
        d.text((x, y + h + 8), p.name[:60], fill=(220, 220, 220))
    sheet.save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--force", action="store_true", help="redo thumbnails that exist")
    parser.add_argument("--sheet", type=Path, nargs="?", const=Path(""), help="also write a contact sheet")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    backfill(args.folder, args.force)
    if args.sheet is not None:
        thumbs = sorted(args.folder.glob("*.thumb.jpg"))
        out = args.sheet if str(args.sheet) else args.folder / "thumbnails-sheet.png"
        print(contact_sheet(thumbs, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
