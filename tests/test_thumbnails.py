"""Thumbnails: the title page of every video, set on YouTube after the upload (fakes only: no API calls)."""
from __future__ import annotations

import json
import subprocess

import pytest
from PIL import Image, ImageDraw

from newsletter_video import thumbnails, youtube_upload
from newsletter_video.youtube_upload import set_day_thumbnails, upload_day
from tests.test_youtube_upload import DAY, FakeHttpError, _day_folder, fake_youtube  # noqa: F401 (fixture)


def _thumbs_api(fake, fail=None):
    calls = []

    class Thumbs:
        def set(self, videoId, media_body):
            class Exe:
                def execute(self):
                    calls.append(videoId)
                    if fail is not None:
                        raise fail
                    return {}

            return Exe()

    fake.thumbnails = lambda: Thumbs()
    return calls


def _with_thumbs(folder):
    for video in folder.glob("*.mp4"):
        Image.new("RGB", (1280, 720), (200, 100, 50)).save(thumbnails.thumb_path(video))


def test_thumbnail_is_set_after_each_upload_and_recorded(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env)
    _with_thumbs(folder)
    calls = _thumbs_api(fake_youtube)
    assert upload_day(DAY) == ["vid1", "vid2"]
    assert calls == ["vid1", "vid2"]
    record = json.loads((folder / "uploads.json").read_text())
    assert record["01_story-1.mp4"]["thumbnail"] == "ok"


def test_a_thumbnail_failure_never_fails_or_undoes_the_upload(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env)
    _with_thumbs(folder)
    _thumbs_api(fake_youtube, fail=FakeHttpError(403, "forbidden"))
    assert upload_day(DAY) == ["vid1", "vid2"]  # both videos still go up
    record = json.loads((folder / "uploads.json").read_text())
    assert record["01_story-1.mp4"]["id"] == "vid1"
    assert record["01_story-1.mp4"]["thumbnail"].startswith("failed:HTTP 403")
    assert record["02_story-2.mp4"]["thumbnail"].startswith("failed:")


def test_a_network_error_on_the_thumbnail_is_recorded_too(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=1)
    _with_thumbs(folder)
    _thumbs_api(fake_youtube, fail=TimeoutError("slow"))
    assert upload_day(DAY) == ["vid1"]
    assert json.loads((folder / "uploads.json").read_text())["01_story-1.mp4"]["thumbnail"].startswith("failed:TimeoutError")


def test_videos_without_a_thumbnail_upload_as_before(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env)
    calls = _thumbs_api(fake_youtube)
    assert upload_day(DAY) == ["vid1", "vid2"] and calls == []
    assert "thumbnail" not in json.loads((folder / "uploads.json").read_text())["01_story-1.mp4"]


def test_backfill_sets_only_the_missing_ones(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=3)
    _thumbs_api(fake_youtube)
    upload_day(DAY)  # no thumbnail files yet: nothing set
    _with_thumbs(folder)
    record = json.loads((folder / "uploads.json").read_text())
    record["02_story-2.mp4"]["thumbnail"] = "ok"  # already done earlier
    (folder / "uploads.json").write_text(json.dumps(record))
    calls = _thumbs_api(fake_youtube)
    assert set_day_thumbnails(DAY) == {"01_story-1.mp4": "ok", "03_story-3.mp4": "ok"}
    assert calls == ["vid1", "vid3"]
    assert set_day_thumbnails(DAY) == {}  # idempotent


def test_metadata_names_the_thumbnail_when_there_is_one(isolated_env):
    from newsletter_video.youtube_upload import write_metadata
    from tests.conftest import make_script

    video = isolated_env / "01_x.mp4"
    video.write_bytes(b"0")
    assert "thumbnail" not in json.loads(write_metadata(make_script("X"), video).read_text())
    Image.new("RGB", (8, 8)).save(thumbnails.thumb_path(video))
    assert json.loads(write_metadata(make_script("X"), video).read_text())["thumbnail"] == "01_x.thumb.jpg"


# --- title times ----------------------------------------------------------------------------------------

def test_title_times_per_format():
    avx = {"duration": 30, "scenes": [{"start": 0, "data": {"lines": [{"text": "OpenAI raised", "at": 0.3},
                                                                       {"text": "$11 billion", "at": 1.1}],
                                                             "underline": {"at": 1.6}}},
                                      {"start": 4.0}]}
    assert thumbnails.avx_title_time(avx) == pytest.approx(2.3)
    avx["scenes"][1]["start"] = 2.0  # a short hook: never past the start of its exit
    assert thumbnails.avx_title_time(avx) == pytest.approx(1.55)
    assert thumbnails.explainer_title_time({"scenes": [{"start": 0}, {"start": 3.4}]}) == pytest.approx(2.95)
    av = {"hook": {"words": [{"word": "a", "at": 0.2}, {"word": "b", "at": 1.9}], "exitAt": 3.0}}
    assert thumbnails.avatar_title_time(av) == pytest.approx(2.35)
    assert thumbnails.avatar_title_time({"hook": {"words": []}}) is None


# --- frames and layout ----------------------------------------------------------------------------------

def _title_video(path, seconds=4):
    """A 1080x1920 clip: 2 s of a 'title' frame, then a different scene."""
    import imageio_ffmpeg

    a, b = path.with_suffix(".a.png"), path.with_suffix(".b.png")
    img = Image.new("RGB", (1080, 1920), (243, 239, 230))
    ImageDraw.Draw(img).rectangle((80, 300, 900, 600), fill=(20, 20, 20))
    img.save(a)
    Image.new("RGB", (1080, 1920), (13, 15, 20)).save(b)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-t", "2", "-i", str(a), "-loop", "1", "-t",
                    str(seconds - 2), "-i", str(b), "-filter_complex", "[0][1]concat=n=2:v=1:a=0,fps=10,format=yuv420p",
                    str(path)], check=True)
    return path


@pytest.mark.parametrize("layout", thumbnails.LAYOUTS)
def test_make_thumbnail_writes_a_small_16_9_jpeg_and_the_full_frame(tmp_path, layout):
    video = _title_video(tmp_path / "01_x.mp4")
    out = thumbnails.make_thumbnail(video, 1.0, layout)
    assert out.name == "01_x.thumb.jpg" and out.stat().st_size < thumbnails.MAX_BYTES
    with Image.open(out) as im:
        assert im.size == (1280, 720) and im.format == "JPEG"
    with Image.open(thumbnails.title_png_path(video)) as im:
        assert im.size == (1080, 1920)


def test_avx_thumbnail_sits_on_the_videos_own_ground(tmp_path):
    video = _title_video(tmp_path / "01_x.mp4")
    with Image.open(thumbnails.make_thumbnail(video, 1.0, "avx")) as im:
        r, g, b = im.convert("RGB").getpixel((1270, 10))
    assert abs(r - 243) < 6 and abs(g - 239) < 6 and abs(b - 230) < 6


def test_detect_title_time_finds_the_first_scene_change(tmp_path):
    video = _title_video(tmp_path / "01_x.mp4")
    at = thumbnails.detect_title_time(video)
    assert at is not None and 1.0 <= at <= 1.8


def test_make_for_skips_untimed_videos_and_never_raises(tmp_path, monkeypatch):
    video = tmp_path / "02_y.mp4"
    assert thumbnails.make_for(video) is None  # no title time remembered (an old render): skipped
    thumbnails.remember(video, 1.0, "avx")
    assert thumbnails.make_for(video) is None  # the file is missing: logged, not raised
    good = _title_video(tmp_path / "03_z.mp4")
    thumbnails.remember(good, 1.0, "explainer")
    assert thumbnails.make_for(good) == thumbnails.thumb_path(good)


def test_build_shorts_makes_the_thumbnail_before_the_sidecar(tmp_path, monkeypatch):
    from newsletter_video import video_builder as vb
    from tests.conftest import make_script

    order = []

    def fake_build(script, out_path, audio_path, work_dir, kicker, background_seed):
        out_path.write_bytes(b"v")
        thumbnails.remember(out_path, 1.0, "avx")
        return out_path

    monkeypatch.setattr(vb, "build_short", fake_build)
    monkeypatch.setattr(vb, "_prepare_workdir", lambda d: d.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(vb, "_clean_render_cache", lambda d: None)
    monkeypatch.setattr(thumbnails, "make_for", lambda v: order.append(("thumb", v.name)))
    paths = vb.build_shorts([make_script("A title")], tmp_path / "out", tmp_path / "audio" / "d",
                            on_rendered=lambda s, p: order.append(("sidecar", p.name)))
    assert [k for k, _ in order] == ["thumb", "sidecar"] and len(paths) == 1


def test_cleanup_removes_the_title_frame_with_the_video_but_keeps_the_thumbnail(isolated_env):
    from datetime import timedelta

    from newsletter_video import run_pipeline

    old = DAY - timedelta(days=10)
    folder = _day_folder(isolated_env, day=old, n=1)
    video = folder / "01_story-1.mp4"
    for p in (thumbnails.thumb_path(video), thumbnails.title_png_path(video)):
        p.write_bytes(b"img")
    (folder / "uploads.json").write_text(json.dumps({"01_story-1.mp4": {"id": "ok"}}))
    run_pipeline.clean_old_outputs(DAY)
    assert not video.exists() and not thumbnails.title_png_path(video).exists()
    assert thumbnails.thumb_path(video).exists()  # small, and what a later --thumbnails backfill uploads


def test_the_uploaded_thumbnail_is_the_vertical_title_page(tmp_path):
    """A Short's thumbnail is shown 9:16, so the full title frame goes up, not the 16:9 composition
    (YouTube would centre-crop that into a narrow strip)."""
    from PIL import Image

    from newsletter_video.thumbnails import thumb_path, title_png_path
    from newsletter_video.youtube_upload import set_thumbnail

    video = tmp_path / "01_x.mp4"
    video.write_bytes(b"x")
    Image.new("RGB", (1080, 1920), (240, 236, 226)).save(title_png_path(video))
    Image.new("RGB", (1280, 720), (10, 10, 10)).save(thumb_path(video), "JPEG")
    sent = {}

    class Thumbs:
        def set(self, videoId, media_body):
            sent["media"] = media_body
            return self

        def execute(self):
            return {}

    class Fake:
        def thumbnails(self):
            return Thumbs()

    assert set_thumbnail(Fake(), "abc", video) == "ok"
    media = sent["media"]
    media_bytes = media.getbytes(0, media.size())
    img = Image.open(__import__("io").BytesIO(media_bytes))
    assert img.size == (1080, 1920) and media.mimetype() == "image/jpeg" and media.size() < 2_000_000
