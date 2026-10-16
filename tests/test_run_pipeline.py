"""End-to-end orchestration with every external stage stubbed: scrape,
writer, renderer and uploader are fakes; notifications are captured."""
from __future__ import annotations

import json
from datetime import date

import pytest

from newsletter_video import run_pipeline, supervisor
from newsletter_video.runstate import load_state, record_video
from newsletter_video.scraper import Edition
from newsletter_video.supervisor import EXIT_CONFIG, EXIT_LOCKED, RunLock
from newsletter_video.youtube_upload import YouTubeAuthError, write_metadata
from tests.conftest import make_script, make_story

DAY = date(2026, 10, 14)


@pytest.fixture
def stubbed_pipeline(monkeypatch, isolated_env):
    """Fakes for every stage; `ctl` lets a test change what each returns."""
    ctl = {
        "editions": [Edition("tech", DAY.isoformat(), "TLDR", [make_story("A"), make_story("B"), make_story("C")])],
        "writer": "claude",
        "built": [],
        "uploads": [],
        "upload_error": None,
    }
    monkeypatch.setattr(run_pipeline, "_wait_for_network", lambda: True)
    monkeypatch.setattr(run_pipeline, "validate_config", lambda *a: None)
    class FakeSource:
        def describe(self):
            return "fake"

        def fetch(self, target_date):
            return ctl["editions"]

    monkeypatch.setattr(run_pipeline, "get_source", lambda: FakeSource())
    monkeypatch.setattr(run_pipeline, "build_scripts",
                        lambda editions, n_videos=None, exclude_titles=frozenset():
                        [make_script(f"Story {i}", writer=ctl["writer"]) for i in range(n_videos)])

    def fake_build_shorts(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, script in enumerate(scripts, start=1):
            path = out_dir / f"{i:02d}_{script.title.lower().replace(' ', '-')}.mp4"
            path.write_bytes(b"\x00" * 100)
            on_rendered(script, path)
            record_video(out_dir, path.stem, "rendered")
            paths.append(path)
        ctl["built"].extend(paths)
        return paths

    monkeypatch.setattr(run_pipeline, "build_shorts", fake_build_shorts)

    def fake_upload_recent(today):
        if ctl["upload_error"]:
            raise ctl["upload_error"]
        ctl["uploads"].append(today)
        return []

    monkeypatch.setattr(run_pipeline, "upload_recent", fake_upload_recent)
    monkeypatch.setattr(run_pipeline, "days_with_pending_uploads", lambda today: [])
    return ctl


def test_happy_path_writes_sidecars_and_state(stubbed_pipeline, isolated_env, notifications):
    assert run_pipeline.main([DAY.isoformat()]) == 0
    folder = isolated_env / DAY.isoformat()
    assert len(list(folder.glob("*.mp4"))) == 3 and len(list(folder.glob("0*.json"))) == 3
    state = load_state(folder)
    assert state["run"]["finished"] and state["run"]["writer"] == "claude"
    assert len(notifications) == 1 and notifications[0][2] is True
    assert "3 video(s) made" in notifications[0][1]


def test_rerun_resumes_only_the_missing_videos(stubbed_pipeline, isolated_env, notifications):
    folder = isolated_env / DAY.isoformat()
    folder.mkdir()
    for i in (1, 2):
        write_metadata(make_script(f"Old {i}"), folder / f"0{i}_old-{i}.mp4")
    run_pipeline.main([DAY.isoformat()])
    assert len(stubbed_pipeline["built"]) == 1  # VIDEOS_PER_DAY=3, two already done


def test_nothing_to_do_when_day_is_complete(stubbed_pipeline, isolated_env, monkeypatch):
    folder = isolated_env / DAY.isoformat()
    folder.mkdir()
    for i in (1, 2, 3):
        write_metadata(make_script(f"Old {i}"), folder / f"0{i}_old-{i}.mp4")
    class NoFetch:
        def describe(self):
            return "fake"

        def fetch(self, target_date):
            pytest.fail("should not scrape")

    monkeypatch.setattr(run_pipeline, "get_source", lambda: NoFetch())
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")
    assert run_pipeline.main([DAY.isoformat()]) == 0
    assert stubbed_pipeline["uploads"] == [DAY]  # still retries uploads


def test_empty_scrape_fails_loudly(stubbed_pipeline, notifications):
    stubbed_pipeline["editions"] = []
    assert run_pipeline.main([DAY.isoformat()]) == 1
    assert notifications[-1][0] == "Pipeline failed" and "No stories scraped" in notifications[-1][1]


def test_heuristic_scripts_are_not_published_by_default(stubbed_pipeline, isolated_env, notifications, monkeypatch):
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")
    stubbed_pipeline["writer"] = "heuristic"
    assert run_pipeline.main([DAY.isoformat()]) == 1
    assert stubbed_pipeline["built"] == [] and stubbed_pipeline["uploads"] == []
    assert "FALLBACK_UPLOAD=skip" in notifications[-1][1]


def test_heuristic_scripts_upload_private_when_asked(stubbed_pipeline, isolated_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")
    monkeypatch.setenv("FALLBACK_UPLOAD", "private")
    stubbed_pipeline["writer"] = "heuristic"
    assert run_pipeline.main([DAY.isoformat()]) == 0
    sidecar = json.loads(next((isolated_env / DAY.isoformat()).glob("01_*.json")).read_text())
    assert sidecar["privacy"] == "private" and sidecar["writer"] == "heuristic"


def test_heuristic_scripts_render_normally_without_uploads(stubbed_pipeline, isolated_env):
    stubbed_pipeline["writer"] = "heuristic"
    assert run_pipeline.main([DAY.isoformat()]) == 0
    assert len(stubbed_pipeline["built"]) == 3
    sidecar = json.loads(next((isolated_env / DAY.isoformat()).glob("01_*.json")).read_text())
    assert "privacy" not in sidecar


def test_youtube_auth_failure_notifies_but_keeps_videos(stubbed_pipeline, isolated_env, notifications, monkeypatch):
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")
    stubbed_pipeline["upload_error"] = YouTubeAuthError("YouTube sign-in missing or expired — run --auth")
    assert run_pipeline.main([DAY.isoformat()]) == 0
    titles = [n[0] for n in notifications]
    assert "YouTube sign-in needs attention" in titles
    assert len(list((isolated_env / DAY.isoformat()).glob("*.mp4"))) == 3


def test_overlapping_run_is_refused(stubbed_pipeline, isolated_env, notifications):
    with RunLock(run_pipeline.LOCK_PATH):
        assert run_pipeline.main([DAY.isoformat()]) == EXIT_LOCKED
    assert notifications[-1][0] == "Pipeline skipped" and stubbed_pipeline["built"] == []


def test_bad_config_fails_fast(stubbed_pipeline, notifications, monkeypatch):
    def bad(*a):
        raise supervisor.ConfigError("Configuration problems:\n  - VIDEOS_PER_DAY='x' is not a whole number")

    monkeypatch.setattr(run_pipeline, "validate_config", bad)
    assert run_pipeline.main([DAY.isoformat()]) == EXIT_CONFIG
    assert notifications[-1][0] == "Pipeline not started" and "VIDEOS_PER_DAY" in notifications[-1][1]
    assert stubbed_pipeline["built"] == []


def test_crash_mid_day_keeps_finished_videos_and_notifies(stubbed_pipeline, isolated_env, notifications, monkeypatch):
    def crash_at_second(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "01_first.mp4"
        path.write_bytes(b"x")
        on_rendered(scripts[0], path)
        record_video(out_dir, path.stem, "rendered")
        raise MemoryError("Chrome fell over")

    monkeypatch.setattr(run_pipeline, "build_shorts", crash_at_second)
    assert run_pipeline.main([DAY.isoformat()]) == 1
    folder = isolated_env / DAY.isoformat()
    assert (folder / "01_first.json").exists()  # uploadable, and its story is marked covered
    assert "1 video(s) made" in notifications[-1][1] and "MemoryError" in notifications[-1][1]
    assert "Chrome fell over" in load_state(folder)["run"]["error"]


def test_partial_failure_is_reported_as_problems(stubbed_pipeline, isolated_env, notifications, monkeypatch):
    def one_fails(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        record_video(out_dir, "02_second", "failed", error="RuntimeError: TTS clip is silent")
        path = out_dir / "01_first.mp4"
        path.write_bytes(b"x")
        on_rendered(scripts[0], path)
        record_video(out_dir, path.stem, "rendered")
        return [path]

    monkeypatch.setattr(run_pipeline, "build_shorts", one_fails)
    assert run_pipeline.main([DAY.isoformat()]) == 0
    assert notifications[-1][0] == "Pipeline finished with problems" and notifications[-1][2] is False
    assert "1 failed: 02_second" in notifications[-1][1]


def test_corrupt_sidecar_is_skipped_not_fatal(stubbed_pipeline, isolated_env):
    old = isolated_env / "2026-10-01"
    old.mkdir()
    (old / "01_broken.json").write_text("{half written")
    (old / "02_fine.json").write_text(json.dumps({"stories": ["Covered already"]}))
    assert run_pipeline._covered_titles() == {"Covered already"}


def test_network_probe_restores_default_socket_timeout(monkeypatch):
    import socket

    monkeypatch.setattr(run_pipeline.socket, "gethostbyname", lambda host: "1.2.3.4")
    socket.setdefaulttimeout(None)
    assert run_pipeline._wait_for_network() is True
    assert socket.getdefaulttimeout() is None  # before: left at 5s for every later socket, incl. uploads


def test_status_report(stubbed_pipeline, isolated_env, capsys):
    from newsletter_video import status

    run_pipeline.main([DAY.isoformat()])
    text = status.report(DAY)
    assert "01_story-0: rendered" in text and "finished" in text
    assert "no output folder" in status.report(date(2020, 1, 1))
