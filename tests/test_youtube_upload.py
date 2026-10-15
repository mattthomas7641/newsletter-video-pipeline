"""Upload robustness against a fake YouTube client: no network, no posts."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from newsletter_video import run_pipeline, youtube_upload
from newsletter_video.runstate import load_state
from newsletter_video.youtube_upload import (
    YouTubeAuthError, days_with_pending_uploads, pending_videos, upload_day, upload_recent, write_metadata,
)
from tests.conftest import make_script

DAY = date(2026, 10, 14)


class FakeHttpError(Exception):
    def __init__(self, status, reason="err", content=b""):
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()
        self.reason = reason
        self.content = content


class FakeYouTube:
    """Just enough of the Data API surface: videos().insert() with resumable
    next_chunk(), plus channels/playlistItems for verification."""

    def __init__(self):
        self.inserted: list[dict] = []
        self.fail_with: list = []  # exceptions raised by the next inserts, in order
        self.existing_titles: dict[str, str] = {}  # title -> id on the channel
        self.can_list = True
        self.chunks_per_upload = 3

    def videos(self):
        fake = self

        class Videos:
            def insert(self, part, body, media_body):
                class Request:
                    def __init__(self):
                        self.calls = 0

                    def next_chunk(self, num_retries=0):
                        self.calls += 1
                        if fake.fail_with:
                            raise fake.fail_with.pop(0)
                        if self.calls < fake.chunks_per_upload:
                            return None, None
                        vid = f"vid{len(fake.inserted) + 1}"
                        fake.inserted.append({"body": body, "id": vid, "retries": num_retries})
                        return None, {"id": vid}

                return Request()

        return Videos()

    def channels(self):
        fake = self

        class Channels:
            def list(self, part, mine):
                class Exe:
                    def execute(self):
                        if not fake.can_list:
                            raise youtube_upload_errors.HttpError(403, "insufficientPermissions")
                        return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}

                return Exe()

        return Channels()

    def playlistItems(self):
        fake = self

        class Items:
            def list(self, part, playlistId, maxResults):
                class Exe:
                    def execute(self):
                        return {"items": [{"snippet": {"title": t, "resourceId": {"videoId": i}}}
                                          for t, i in fake.existing_titles.items()]}

                return Exe()

        return Items()


class youtube_upload_errors:  # the HttpError class the code catches, swapped in by the fixture
    HttpError = FakeHttpError


@pytest.fixture
def fake_youtube(monkeypatch, tmp_path):
    fake = FakeYouTube()
    monkeypatch.setattr(youtube_upload, "_credentials", lambda interactive: object())
    monkeypatch.setattr(youtube_upload, "_youtube_client", lambda creds: fake)
    import googleapiclient.errors
    import googleapiclient.http

    monkeypatch.setattr(googleapiclient.errors, "HttpError", FakeHttpError)
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", lambda *a, **k: ("media", a, k))
    return fake


def _day_folder(out, day=DAY, n=2, writer="claude", privacy=None):
    folder = out / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        video = folder / f"{i:02d}_story-{i}.mp4"
        video.write_bytes(b"\x00" * 2048)
        write_metadata(make_script(f"Story {i}", writer=writer), video, privacy=privacy)
    return folder


def test_uploads_pending_videos_and_records_each(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env)
    ids = upload_day(DAY)
    assert ids == ["vid1", "vid2"]
    record = json.loads((folder / "uploads.json").read_text())
    assert record["01_story-1.mp4"]["id"] == "vid1" and record["02_story-2.mp4"]["privacy"] == "public"
    assert fake_youtube.inserted[0]["retries"] == youtube_upload.UPLOAD_RETRIES
    assert load_state(folder)["videos"]["01_story-1"]["status"] == "uploaded"
    assert upload_day(DAY) == [] and len(fake_youtube.inserted) == 2  # idempotent


def test_private_override_from_sidecar(fake_youtube, isolated_env):
    _day_folder(isolated_env, writer="heuristic", privacy="private")
    upload_day(DAY)
    assert fake_youtube.inserted[0]["body"]["status"]["privacyStatus"] == "private"


def test_unrecorded_upload_is_verified_not_reposted(fake_youtube, isolated_env):
    """The process died after YouTube accepted video 1 but before uploads.json
    got its id. The retry finds it on the channel and records it."""
    folder = _day_folder(isolated_env)
    (folder / "uploads.json").write_text(json.dumps({"01_story-1.mp4": {"id": None, "started": "x"}}))
    fake_youtube.existing_titles = {json.loads((folder / "01_story-1.json").read_text())["title"]: "already"}
    ids = upload_day(DAY)
    assert ids == ["vid1"]  # only video 2 was actually posted
    record = json.loads((folder / "uploads.json").read_text())
    assert record["01_story-1.mp4"]["id"] == "already" and record["02_story-2.mp4"]["id"] == "vid1"
    assert [i["body"]["snippet"]["title"] for i in fake_youtube.inserted] == [
        json.loads((folder / "02_story-2.json").read_text())["title"]]


def test_unrecorded_upload_not_on_channel_is_reposted(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=1)
    (folder / "uploads.json").write_text(json.dumps({"01_story-1.mp4": {"id": None}}))
    assert upload_day(DAY) == ["vid1"]


def test_unrecorded_upload_without_list_permission_is_left_alone(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=1)
    (folder / "uploads.json").write_text(json.dumps({"01_story-1.mp4": {"id": None}}))
    fake_youtube.can_list = False
    assert upload_day(DAY) == [] and fake_youtube.inserted == []  # never risks a duplicate
    assert pending_videos(folder) == [folder / "01_story-1.mp4"]  # still visible as pending


def test_attempt_is_marked_before_insert(fake_youtube, isolated_env):
    """A transport error mid-file (outcome unknown) leaves an "in doubt"
    marker, so the next run verifies instead of blindly re-posting."""
    folder = _day_folder(isolated_env, n=1)
    fake_youtube.fail_with = [ConnectionResetError("dropped mid-upload")]
    assert upload_day(DAY) == []  # the generic except records the failure, nothing is raised
    record = json.loads((folder / "uploads.json").read_text())
    assert record["01_story-1.mp4"]["id"] is None and "started" in record["01_story-1.mp4"]
    assert load_state(folder)["videos"]["01_story-1"]["status"] == "upload_failed"


def test_quota_error_stops_the_batch_and_keeps_videos_pending(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=3)
    fake_youtube.fail_with = [FakeHttpError(403, "quotaExceeded")]
    assert upload_day(DAY) == []
    assert len(pending_videos(folder)) == 3  # the 403 attempt is not left as "in doubt"
    assert load_state(folder)["videos"]["01_story-1"]["status"] == "upload_failed"


def test_upload_limit_exceeded_stops_the_batch(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=2)
    fake_youtube.fail_with = [FakeHttpError(400, "bad", content=b'{"reason": "uploadLimitExceeded"}')]
    assert upload_day(DAY) == [] and len(pending_videos(folder)) == 2


def test_auth_failure_is_raised_not_swallowed(fake_youtube, isolated_env, monkeypatch):
    _day_folder(isolated_env, n=1)
    monkeypatch.setattr(youtube_upload, "_credentials", lambda interactive: None)
    with pytest.raises(YouTubeAuthError):
        upload_day(DAY)


def test_401_mid_run_is_an_auth_error(fake_youtube, isolated_env):
    _day_folder(isolated_env, n=1)
    fake_youtube.fail_with = [FakeHttpError(401, "unauthorized")]
    with pytest.raises(YouTubeAuthError):
        upload_day(DAY)


def test_pending_uploads_are_found_however_old(fake_youtube, isolated_env):
    old = DAY - timedelta(days=12)
    _day_folder(isolated_env, day=old, n=1)
    _day_folder(isolated_env, day=DAY, n=1)
    (isolated_env / "not-a-date").mkdir()
    assert days_with_pending_uploads(DAY) == [old, DAY]
    assert upload_recent(DAY) == ["vid1", "vid2"]  # before: a 3-day look-back left `old` orphaned forever
    assert days_with_pending_uploads(DAY) == []


def test_cleanup_keeps_videos_whose_upload_is_unconfirmed(isolated_env):
    old = DAY - timedelta(days=10)
    folder = _day_folder(isolated_env, day=old, n=2)
    (folder / "uploads.json").write_text(json.dumps({
        "01_story-1.mp4": {"id": "ok"}, "02_story-2.mp4": {"id": None, "started": "x"}}))
    run_pipeline.clean_old_outputs(DAY)
    assert not (folder / "01_story-1.mp4").exists()
    assert (folder / "02_story-2.mp4").exists()


def test_corrupt_record_file_does_not_crash(fake_youtube, isolated_env):
    folder = _day_folder(isolated_env, n=1)
    (folder / "uploads.json").write_text("{not json")
    assert upload_day(DAY) == ["vid1"]
