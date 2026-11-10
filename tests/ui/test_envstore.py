import os
import stat
from unittest import mock

import pytest

from newsletter_video.ui.envstore import SCHEMA, EnvStore, SettingsError
from tests.ui.conftest import DUMMY_SECRET, ENV_TEXT


@pytest.fixture
def store(paths):
    return EnvStore(paths.env_path)


def test_read_returns_only_allowlisted_keys_with_defaults(store):
    data = store.read()
    assert set(data["values"]) == set(SCHEMA)
    assert data["values"]["VIDEOS_PER_DAY"] == "3"
    assert data["values"]["VIDEO_STYLE"] == "avatar"  # absent -> default
    assert sorted(data["set"]) == ["VIDEOS_PER_DAY", "YOUTUBE_PRIVACY", "YOUTUBE_UPLOAD"]
    blob = repr(data)
    assert DUMMY_SECRET not in blob and "UNRELATED" not in blob and "hello world" not in blob


def test_read_flags_invalid_values_and_falls_back(paths, store):
    paths.env_path.write_text("VIDEOS_PER_DAY=lots\nYOUTUBE_PRIVACY=everyone\n")
    data = store.read()
    assert data["values"]["VIDEOS_PER_DAY"] == "3"
    assert set(data["invalid"]) == {"VIDEOS_PER_DAY", "YOUTUBE_PRIVACY"}


def test_write_preserves_comments_order_and_unrelated_lines(paths, store):
    changed = store.write({"VIDEOS_PER_DAY": 5, "YOUTUBE_UPLOAD": True})
    assert sorted(changed) == ["VIDEOS_PER_DAY", "YOUTUBE_UPLOAD"]
    lines = paths.env_path.read_text().splitlines()
    original = ENV_TEXT.splitlines()
    assert lines[:len(original)] == [
        l.replace("VIDEOS_PER_DAY=3", "VIDEOS_PER_DAY=5").replace("YOUTUBE_UPLOAD=0", "YOUTUBE_UPLOAD=1")
        for l in original
    ]
    assert "# keep me" in lines and "UNRELATED_SETTING=hello world" in lines
    assert f"ANTHROPIC_API_KEY={DUMMY_SECRET}" in lines
    assert "TTS_PROVIDER=kokoro   # inline comment" in lines


def test_new_keys_are_appended_once_under_a_marker(paths, store):
    store.write({"VIDEO_STYLE": "explainer", "VIDEO_THEME": "dark"})
    text = paths.env_path.read_text()
    assert text.startswith(ENV_TEXT)
    tail = text[len(ENV_TEXT):].splitlines()
    assert tail[-3:] == ["# Added by the control panel", "VIDEO_STYLE=explainer", "VIDEO_THEME=dark"]
    store.write({"VIDEO_STYLE": "avatar"})
    assert paths.env_path.read_text().count("VIDEO_STYLE=") == 1
    assert paths.env_path.read_text().count("# Added by the control panel") == 1


def test_no_change_means_no_write(paths, store):
    before = paths.env_path.stat().st_mtime_ns
    assert store.write({"VIDEOS_PER_DAY": "3"}) == []
    assert paths.env_path.stat().st_mtime_ns == before


def test_file_mode_is_preserved(paths, store):
    paths.env_path.chmod(0o640)
    store.write({"VIDEOS_PER_DAY": 7})
    assert stat.S_IMODE(paths.env_path.stat().st_mode) == 0o640


def test_new_file_is_private(tmp_path):
    path = tmp_path / "fresh.env"
    EnvStore(path).write({"VIDEOS_PER_DAY": 2})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().endswith("VIDEOS_PER_DAY=2\n")


def test_write_is_atomic_and_leaves_no_temp_files(paths, store):
    original = paths.env_path.read_text()
    with mock.patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.write({"VIDEOS_PER_DAY": 9})
    assert paths.env_path.read_text() == original  # untouched
    assert [p.name for p in paths.env_path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_crlf_files_keep_their_line_endings(paths, store):
    paths.env_path.write_bytes(b"VIDEOS_PER_DAY=3\r\n# note\r\n")
    store.write({"VIDEOS_PER_DAY": 4})
    assert paths.env_path.read_bytes() == b"VIDEOS_PER_DAY=4\r\n# note\r\n"


def test_rejects_keys_outside_the_allowlist(paths, store):
    for key in ("ANTHROPIC_API_KEY", "UNRELATED_SETTING", "TTS_PROVIDER", "PATH", "NOTIFY_WEBHOOK_URL"):
        with pytest.raises(SettingsError) as exc:
            store.write({key: "x"})
        assert key in exc.value.errors
    assert paths.env_path.read_text() == ENV_TEXT


@pytest.mark.parametrize("key,value", [
    ("VIDEOS_PER_DAY", "0"), ("VIDEOS_PER_DAY", "21"), ("VIDEOS_PER_DAY", "3; rm -rf"), ("VIDEOS_PER_DAY", "-1"),
    ("YOUTUBE_PRIVACY", "everyone"), ("YOUTUBE_UPLOAD", "yes"), ("VIDEO_STYLE", "cartoon"),
    ("CHARACTER", "../etc"), ("CHARACTER", "Has Spaces"), ("CHARACTER", "a\nB=1"), ("VIDEO_THEME", "dark\nX=1"),
])
def test_rejects_invalid_values_without_touching_the_file(paths, store, key, value):
    with pytest.raises(SettingsError):
        store.write({key: value})
    assert paths.env_path.read_text() == ENV_TEXT


def test_one_bad_value_blocks_the_whole_update(paths, store):
    with pytest.raises(SettingsError):
        store.write({"VIDEOS_PER_DAY": 8, "YOUTUBE_PRIVACY": "nope"})
    assert paths.env_path.read_text() == ENV_TEXT


def test_blank_character_is_allowed(paths, store):
    store.write({"CHARACTER": "gizmo"})
    store.write({"CHARACTER": ""})
    assert "CHARACTER=\n" in paths.env_path.read_text()
    assert store.read()["values"]["CHARACTER"] == ""


def test_secret_presence_reports_booleans_only(paths, store):
    presence = store.secret_presence(environ={})
    assert presence == {"ANTHROPIC_API_KEY": True, "TYPESAFE_API_KEY": True,
                        "ELEVENLABS_API_KEY": False, "NOTIFY_WEBHOOK_URL": True}
    assert all(isinstance(v, bool) for v in presence.values())


def test_secret_presence_also_checks_the_environment(tmp_path):
    store = EnvStore(tmp_path / "none.env")
    assert store.secret_presence(environ={"ANTHROPIC_API_KEY": "x"})["ANTHROPIC_API_KEY"] is True
    assert store.secret_presence(environ={})["ANTHROPIC_API_KEY"] is False


def test_has_value_refuses_arbitrary_keys(store):
    assert store.has_value("VOICEBOX_PROFILE_ID", environ={}) is True
    with pytest.raises(KeyError):
        store.has_value("UNRELATED_SETTING")


def test_secret_values_for_redaction_are_collected(store):
    values = store.secret_values(environ={})
    assert DUMMY_SECRET in values and "ts-dummy-secret-654321" in values
    assert "hello world" not in values
