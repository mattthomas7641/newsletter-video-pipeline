import json
import sys
import types
from pathlib import Path

import pytest

from newsletter_video import characters
from newsletter_video.ui import workers


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("BYTESIZE_ENV_FILE", str(tmp_path / "missing.env"))


def write_spec(tmp_path, **extra):
    folder = tmp_path / "up"
    (folder / "files").mkdir(parents=True)
    (folder / "files" / "avatar.png").write_bytes(b"x")
    spec = {"name": "Zed", "avatar": str(folder / "files" / "avatar.png"), "voice_audio": [],
            "profile_id": "", "persona": "p", **extra}
    (folder / "spec.json").write_text(json.dumps(spec))
    return folder / "spec.json"


def test_add_worker_success_writes_outcome_and_deletes_uploads(tmp_path, monkeypatch):
    spec = write_spec(tmp_path)
    seen = {}

    def fake_add(**kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(name="Zed", slug="zed")

    monkeypatch.setattr(characters, "add", fake_add)
    assert workers.main(["add", "--spec", str(spec)]) == 0
    assert json.loads((spec.parent / "outcome.json").read_text()) == {"ok": True, "message": "", "slug": "zed"}
    assert seen["force"] is False and seen["persona"] == "p" and seen["profile_id"] is None
    assert not (spec.parent / "files").exists()  # the uploaded originals aren't kept


def test_add_worker_reports_user_errors_from_systemexit(tmp_path, monkeypatch):
    spec = write_spec(tmp_path)

    def refuse(**kwargs):
        raise SystemExit("'zed' already exists")

    monkeypatch.setattr(characters, "add", refuse)
    assert workers.main(["add", "--spec", str(spec)]) == 1
    outcome = json.loads((spec.parent / "outcome.json").read_text())
    assert outcome["ok"] is False and "already exists" in outcome["message"]


def test_add_worker_translates_a_missing_voicebox(tmp_path, monkeypatch):
    spec = write_spec(tmp_path)

    def boom(**kwargs):
        raise RuntimeError("HTTPConnectionPool(host='localhost', port=17493): Max retries exceeded")

    monkeypatch.setattr(characters, "add", boom)
    assert workers.main(["add", "--spec", str(spec)]) == 1
    assert "Voicebox isn't running" in json.loads((spec.parent / "outcome.json").read_text())["message"]


def test_sample_worker_selects_the_character_through_the_environment(tmp_path, monkeypatch):
    calls = {}
    fake_tts = types.ModuleType("newsletter_video.tts")

    def synthesize(text, out_path, speed=None):
        import os

        calls["character"] = os.environ.get("CHARACTER")
        Path(out_path).write_bytes(b"RIFF")
        return out_path

    fake_tts.synthesize = synthesize
    monkeypatch.setitem(sys.modules, "newsletter_video.tts", fake_tts)
    monkeypatch.setattr("newsletter_video.tts", fake_tts, raising=False)
    out = tmp_path / "s" / "x.wav"
    assert workers.main(["sample", "--slug", "gizmo", "--out", str(out)]) == 0
    assert calls["character"] == "gizmo" and out.read_bytes() == b"RIFF"
    assert workers.main(["sample", "--slug", "default", "--out", str(out)]) == 0
    assert calls["character"] == ""  # the legacy default: no character selected
