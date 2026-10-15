"""Runs the normal pipeline for a single throwaway test video.

    python -m newsletter_video.ui.preview_runner --out output/_previews/<id>

It calls `run_pipeline.run()` unchanged and only redirects where the finished
video goes, without editing run_pipeline.py:
- the render and its metadata go to --out instead of output/<today>/
- the YouTube step and the old-video cleanup are switched off

Story selection still reads the real history, so a test never repeats a story
already posted, and, because it writes nowhere else, it never uses one up:
tomorrow's real run can still cover it.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("date", nargs="?", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    # Belt and braces: the panel already sets these, and everything below also
    # turns the upload code into a no-op.
    os.environ["YOUTUBE_UPLOAD"] = "0"
    os.environ["VIDEOS_PER_DAY"] = "1"

    from newsletter_video import run_pipeline as rp
    from newsletter_video import youtube_upload as yu

    preview: Path = args.out.resolve()
    preview.mkdir(parents=True, exist_ok=True)
    work = preview / "_work"
    real_build = rp.build_shorts

    def build_into_preview(scripts, out_dir, audio_dir, *rest, **kwargs):  # noqa: ARG001
        kwargs.pop("out_dir", None)
        kwargs.pop("audio_dir", None)
        return real_build(scripts, preview, work / "audio", *rest, **kwargs)

    def no_upload(*_args, **_kwargs):
        return []

    rp.build_shorts = build_into_preview
    for owner in (rp, yu):
        for name in ("upload_recent", "upload_day", "publish_day"):
            if hasattr(owner, name):
                setattr(owner, name, no_upload)
    if hasattr(rp, "clean_old_outputs"):
        rp.clean_old_outputs = lambda *a, **k: None

    paths = rp.run(args.date or date.today())
    print(f"Test video ready: {', '.join(p.name for p in paths)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
