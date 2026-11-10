"""The cloud pieces: state sync through a bucket, secrets from the environment, the Whisper backend choice and
the notification platform guard. A fake in-memory bucket stands in for Cloud Storage."""
from __future__ import annotations

import base64
import hashlib
import stat
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from newsletter_video import cloud_run, cloud_state, notify, video_builder
from newsletter_video.cloud_state import CloudState


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def data(self):
        return self.bucket.store.get(self.name)

    @property
    def size(self):
        return len(self.data)

    @property
    def md5_hash(self):
        return base64.b64encode(hashlib.md5(self.data).digest()).decode()

    def exists(self):
        return self.name in self.bucket.store

    def upload_from_filename(self, path):
        self.bucket.store[self.name] = Path(path).read_bytes()
        self.bucket.uploads.append(self.name)

    def download_to_filename(self, path):
        Path(path).write_bytes(self.data)


class FakeBucket:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix=""):
        return [FakeBlob(self, n) for n in sorted(self.store) if n.startswith(prefix)]


TODAY = date(2026, 11, 10)


def make_state(tmp_path, bucket=None, name="disk"):
    out = tmp_path / name / "output"
    out.mkdir(parents=True, exist_ok=True)
    return CloudState(bucket or FakeBucket(), output_dir=out, lineup_file=tmp_path / name / "lineup.json",
                      today=TODAY), out


def write(path: Path, data: bytes | str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def test_push_sends_state_and_recent_media_but_not_scratch_or_old_videos(tmp_path):
    state, out = make_state(tmp_path)
    write(out / "2026-11-10" / "uploads.json", "{}")
    write(out / "2026-11-10" / "01.mp4", b"video")
    write(out / "2026-11-10" / "_work" / "scene.png", b"scratch")
    write(out / "2026-11-02" / "03.json", '{"stories": ["a"]}')  # old day: its history still travels
    write(out / "2026-11-02" / "03.mp4", b"old video")  # ...but its video does not
    write(out / "_audio" / "2026-11-10" / "a.wav", b"audio")
    write(out / "_drafts" / "x" / "big.mp4", b"draft")
    result = state.push()
    assert set(state.bucket.store) == {"output/2026-11-10/uploads.json", "output/2026-11-10/01.mp4",
                                       "output/2026-11-02/03.json"}
    assert result == {"uploaded": 3, "failed": 0}


def test_push_twice_uploads_nothing_the_second_time_and_notices_changes(tmp_path):
    state, out = make_state(tmp_path)
    write(out / "2026-11-10" / "uploads.json", '{"a": 1}')
    state.push()
    state.bucket.uploads.clear()
    assert state.push() == {"uploaded": 0, "failed": 0}
    write(out / "2026-11-10" / "uploads.json", '{"a": 2}')  # same size, different content
    assert state.push()["uploaded"] == 1


def test_a_fresh_disk_pulls_what_another_pushed(tmp_path):
    bucket = FakeBucket()
    first, out = make_state(tmp_path, bucket, "first")
    write(out / "2026-11-10" / "01.json", '{"stories": ["Nvidia"]}')
    write(out / "2026-11-10" / "01.mp4", b"video")
    write(tmp_path / "first" / "lineup.json", '{"batches": []}')
    first.push()
    assert first.push_config()

    second, out2 = make_state(tmp_path, bucket, "second")
    assert second.pull() == {"downloaded": 2, "skipped": 0}
    assert (out2 / "2026-11-10" / "01.json").read_text() == '{"stories": ["Nvidia"]}'
    assert (out2 / "2026-11-10" / "01.mp4").read_bytes() == b"video"
    assert (tmp_path / "second" / "lineup.json").read_text() == '{"batches": []}'
    assert second.pull() == {"downloaded": 0, "skipped": 2}  # nothing to do the second time


def test_pull_leaves_no_partial_files_and_skips_unwanted_blobs(tmp_path):
    bucket = FakeBucket()
    bucket.store["output/2026-11-10/_work/x.png"] = b"scratch"
    bucket.store["output/2026-10-01/01.mp4"] = b"too old"
    state, out = make_state(tmp_path, bucket)
    assert state.pull()["downloaded"] == 0
    assert not list(out.rglob("*"))


def test_push_survives_a_failing_upload_and_counts_it(tmp_path):
    state, out = make_state(tmp_path)
    write(out / "2026-11-10" / "a.json", "{}")
    write(out / "2026-11-10" / "b.json", "{}")
    real = FakeBlob.upload_from_filename

    def flaky(self, path):
        if self.name.endswith("a.json"):
            raise OSError("network")
        real(self, path)

    FakeBlob.upload_from_filename = flaky
    try:
        assert state.push() == {"uploaded": 1, "failed": 1}
    finally:
        FakeBlob.upload_from_filename = real
    assert state.push() == {"uploaded": 1, "failed": 0}  # the next push retries it


def test_from_env_is_off_without_a_bucket(monkeypatch):
    monkeypatch.delenv("STATE_BUCKET", raising=False)
    assert cloud_state.from_env() is None


def test_materialize_secrets_writes_private_files(tmp_path):
    written = cloud_run.materialize_secrets({"YOUTUBE_TOKEN_JSON": '{"token": "t"}\n', "OTHER": "x"},
                                            secrets_dir=tmp_path / "secrets")
    assert written == ["youtube_token.json"]
    path = tmp_path / "secrets" / "youtube_token.json"
    assert path.read_text() == '{"token": "t"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert cloud_run.materialize_secrets({}, secrets_dir=tmp_path / "none") == []
    assert not (tmp_path / "none").exists()


def test_whisper_backend_choice(monkeypatch):
    monkeypatch.setenv("WHISPER_BACKEND", "faster")
    assert video_builder._whisper_backend() == "faster"
    monkeypatch.setenv("WHISPER_BACKEND", "mlx")
    assert video_builder._whisper_backend() == "mlx"
    monkeypatch.setenv("WHISPER_BACKEND", "auto")
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))  # a stand-in has no spec
    assert video_builder._whisper_backend() == "mlx"
    monkeypatch.delitem(sys.modules, "mlx_whisper")
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert video_builder._whisper_backend() == "faster"


def test_faster_whisper_words_come_back_as_word_and_start(monkeypatch, tmp_path):
    W = types.SimpleNamespace
    seg = W(words=[W(word=" Hello", start=0.1), W(word=" world", start=0.5)])

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            assert kw["word_timestamps"] is True
            return iter([seg]), None

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setenv("WHISPER_BACKEND", "faster")
    monkeypatch.setattr(video_builder, "_faster_whisper_model", None)
    assert video_builder._transcribe_words(tmp_path / "a.wav") == [("Hello", 0.1), ("world", 0.5)]


def test_macos_popup_only_on_a_mac(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_macos_notification", lambda t, m: calls.append((t, m)))
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("NOTIFY_MACOS", "1")
    monkeypatch.setattr(sys, "platform", "linux")
    notify.notify("t", "m")
    assert calls == []
    monkeypatch.setattr(sys, "platform", "darwin")
    notify.notify("t", "m")
    assert calls == [("t", "m")]
