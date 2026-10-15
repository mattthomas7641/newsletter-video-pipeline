import json
import os
import subprocess
import sys
import time

import pytest

from newsletter_video.ui import jobwrap
from newsletter_video.ui.envstore import EnvStore, SettingsError
from newsletter_video.ui.jobs import JobManager
from newsletter_video.ui.runs import RunBlocked, RunService, lock_held, parse_stage
from tests.ui.conftest import DUMMY_SECRET, FakeSpawner


@pytest.fixture
def jobs(paths, spawner):
    return JobManager(paths.state_dir, paths.root, spawner=spawner,
                      redactor=lambda: EnvStore(paths.env_path).secret_values())


@pytest.fixture
def runs(paths, jobs):
    return RunService(paths, jobs, EnvStore(paths.env_path), lister=lambda: [], environ={})


def finish(jobs, job, code=0, cancelled=False):
    (jobs.jobs_dir / job.id / "result.json").write_text(
        json.dumps({"exit_code": code, "cancelled": cancelled, "finished_at": time.time()}))


# --- job state ---

def test_job_lifecycle_running_done_failed(jobs, spawner):
    job = jobs.create("test", ["python", "x"], {"A": "1"}, {"k": "v"})
    assert job.state == "running" and job.meta == {"k": "v"}
    assert spawner.calls[0]["env"]["PYTHONUNBUFFERED"] == "1"
    finish(jobs, job, 0)
    assert jobs.get(job.id).state == "done"
    other = jobs.create("test", ["x"], {}, {})
    finish(jobs, other, 3)
    view = jobs.get(other.id)
    assert view.state == "failed" and view.exit_code == 3


def test_job_whose_supervisor_vanished_is_interrupted_not_running(jobs):
    class Dead(FakeSpawner):
        def __call__(self, *a):
            super().__call__(*a)
            return 2_999_999  # no such process
    jobs.spawner = Dead()
    job = jobs.create("batch", ["x"], {}, {})
    assert job.state == "interrupted"
    assert jobs.active() == []


def test_state_survives_a_new_manager_instance(paths, jobs):
    """Restarting the panel: a fresh JobManager reads the same directory."""
    job = jobs.create("batch", ["x"], {}, {"videos": 3})
    fresh = JobManager(paths.state_dir, paths.root)
    assert fresh.get(job.id).state == "running"
    assert [j.id for j in fresh.active()] == [job.id]
    finish(jobs, job)
    assert fresh.get(job.id).state == "done"


def test_unknown_or_corrupt_job_files_are_tolerated(paths, jobs):
    assert jobs.get("nope123") is None
    broken = jobs.jobs_dir / "broken1"
    broken.mkdir(parents=True)
    (broken / "job.json").write_text("{not json")
    assert jobs.get("broken1") is None
    assert jobs.list() == []


def test_cancel_kills_the_process_group_and_marks_cancelled(paths, sleeper):
    proc = sleeper()
    spawner = FakeSpawner()
    spawner.pid = proc.pid
    jobs = JobManager(paths.state_dir, paths.root, spawner=spawner)
    job = jobs.create("test", ["x"], {}, {})
    assert job.state == "running"
    assert jobs.cancel(job.id) is True
    proc.wait(timeout=5)
    assert jobs.get(job.id).state == "cancelled"
    assert jobs.cancel(job.id) is False  # nothing left to stop


def test_cancel_refuses_our_own_pid(paths, jobs):
    job = jobs.create("test", ["x"], {}, {})  # FakeSpawner reports os.getpid()
    assert jobs.cancel(job.id) is False


# --- logs ---

def test_log_streaming_by_offset_and_secret_scrubbing(paths, jobs):
    job = jobs.create("test", ["x"], {}, {})
    log = jobs.jobs_dir / job.id / "log.txt"
    log.write_text(f"first line\nusing key {DUMMY_SECRET} now\nBearer abcdefghijklmnop1234\n")
    chunk = jobs.log(job.id, 0)
    assert "first line" in chunk["text"]
    assert DUMMY_SECRET not in chunk["text"] and "abcdefghijklmnop1234" not in chunk["text"]
    assert "[hidden]" in chunk["text"]
    with log.open("a") as handle:
        handle.write("second\n")
    more = jobs.log(job.id, chunk["next"])
    assert more["text"] == "second\n"
    assert jobs.log(job.id, 10**9)["text"] == ""  # offsets past the end are clamped


def test_runaway_logs_return_only_the_latest_chunk(paths, jobs):
    job = jobs.create("test", ["x"], {}, {})
    (jobs.jobs_dir / job.id / "log.txt").write_text("a" * 200_000 + "END")
    chunk = jobs.log(job.id, 0)
    assert chunk["text"].endswith("END") and len(chunk["text"]) <= 64 * 1024


def test_parse_stage_reads_pipeline_log_lines():
    lines = ["2026-10-15 07:00:01,123 [INFO] Scraping editions: ['tech'] for 2026-10-15",
             "2026-10-15 07:01:00,000 [INFO] Selecting top stories and building scripts",
             "2026-10-15 07:02:00,000 [INFO] Rendering 3 shorts to /x"]
    info = parse_stage(lines)
    assert info["stage"] == "Voicing and rendering the video" and info["step"] == 3
    assert info["last_line"] == "[INFO] Rendering 3 shorts to /x"
    assert parse_stage([])["stage"] is None


# --- the supervisor itself (real subprocess, harmless command) ---

def test_jobwrap_records_exit_code_and_output(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    code = jobwrap.main([str(job_dir), "--", sys.executable, "-c", "print('hello from job'); raise SystemExit(3)"])
    assert code == 0
    result = json.loads((job_dir / "result.json").read_text())
    assert result["exit_code"] == 3 and result["cancelled"] is False
    assert "hello from job" in (job_dir / "log.txt").read_text()
    assert json.loads((job_dir / "child.json").read_text())["pid"] > 1


def test_jobwrap_reports_a_command_that_cannot_start(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    jobwrap.main([str(job_dir), "--", "/definitely/not/a/program"])
    assert json.loads((job_dir / "result.json").read_text())["exit_code"] == 127
    assert "Could not start" in (job_dir / "log.txt").read_text()


def test_real_spawner_end_to_end_with_a_trivial_command(paths):
    jobs = JobManager(paths.state_dir, paths.root)
    repo = str(__import__("pathlib").Path(jobwrap.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=repo)
    job = jobs.create("sample", [sys.executable, "-c", "print('ok')"], env, {})
    deadline = time.time() + 20
    while time.time() < deadline and jobs.get(job.id).state == "running":
        time.sleep(0.2)
    assert jobs.get(job.id).state == "done"
    assert "ok" in jobs.log(job.id)["text"]


# --- runs ---

def test_test_run_never_uploads_and_goes_to_a_preview_folder(runs, spawner, paths):
    job = runs.start_test({"VIDEO_STYLE": "explainer", "VIDEO_THEME": "dark", "YOUTUBE_UPLOAD": "1",
                           "VIDEOS_PER_DAY": "9"})
    call = spawner.calls[0]
    env = call["env"]
    assert env["YOUTUBE_UPLOAD"] == "0" and env["VIDEOS_PER_DAY"] == "1" and env["FALLBACK_UPLOAD"] == "0"
    assert env["NOTIFY_WEBHOOK_URL"] == ""
    assert env["VIDEO_STYLE"] == "explainer" and env["VIDEO_THEME"] == "dark"
    argv = call["argv"]
    assert argv[1:3] == ["-m", "newsletter_video.ui.preview_runner"]
    preview = argv[argv.index("--out") + 1]
    assert preview == str(paths.output_dir / "_previews" / job.id)
    assert "run_pipeline" not in " ".join(argv)


def test_test_run_rejects_non_allowlisted_overrides(runs, spawner):
    with pytest.raises(SettingsError):
        runs.start_test({"ANTHROPIC_API_KEY": "x"})
    with pytest.raises(SettingsError):
        runs.start_test({"VIDEOS_PER_DAY": "banana"})
    assert spawner.calls == []


def test_batch_uses_saved_settings_and_plain_run_pipeline(runs, spawner):
    job = runs.start_batch()
    argv = spawner.calls[0]["argv"]
    assert argv[1:] == ["-m", "newsletter_video.run_pipeline"]
    assert job.meta == {"videos": 3, "uploads": False, "privacy": "private"}
    # nothing overridden: the real .env decides, exactly like the 7am job
    env = spawner.calls[0]["env"]
    assert "BYTESIZE_TEST_RUN" not in env
    assert env.get("YOUTUBE_UPLOAD") == os.environ.get("YOUTUBE_UPLOAD")
    assert env.get("VIDEOS_PER_DAY") == os.environ.get("VIDEOS_PER_DAY")


def test_only_one_run_at_a_time(runs):
    runs.start_test()
    with pytest.raises(RunBlocked, match="already being made"):
        runs.start_batch()
    with pytest.raises(RunBlocked):
        runs.start_test()


def test_a_finished_run_does_not_block(runs, jobs):
    job = runs.start_test()
    finish(jobs, job)
    runs.start_batch()


def test_external_pipeline_process_blocks_a_run(paths, jobs):
    rows = [(4321, 1, "/x/venv/bin/python -m newsletter_video.run_pipeline"), (99, 1, "/bin/zsh")]
    runs = RunService(paths, jobs, EnvStore(paths.env_path), lister=lambda: rows, environ={})
    assert [a.source for a in runs.activity()] == ["process"]
    with pytest.raises(RunBlocked, match="outside the panel"):
        runs.start_batch()


def test_scheduled_wrapper_script_is_detected_too(paths, jobs):
    rows = [(5, 1, "/bin/bash /Users/me/repo/run_daily.sh")]
    runs = RunService(paths, jobs, EnvStore(paths.env_path), lister=lambda: rows, environ={})
    assert runs.activity()


def test_our_own_children_are_not_mistaken_for_outside_runs(paths, jobs, spawner):
    runs = RunService(paths, jobs, EnvStore(paths.env_path), lister=lambda: [], environ={})
    job = runs.start_batch()
    own = os.getpid()
    rows = [(777, own, "python -m newsletter_video.run_pipeline"),
            (own + 1, 1, f"python -m newsletter_video.ui.jobwrap /x/{job.id} -- python -m newsletter_video.run_pipeline")]
    runs.lister = lambda: rows
    assert [a.source for a in runs.activity()] == ["panel"]


def test_held_lockfile_blocks_and_stale_one_does_not(paths, jobs, tmp_path):
    lock = tmp_path / "pipeline.lock"
    runs = RunService(paths, jobs, EnvStore(paths.env_path), lister=lambda: [],
                      environ={"PIPELINE_LOCKFILE": str(lock)})
    lock.write_text("99999999\n")  # a dead pid left behind by a crash
    assert runs.activity() == []
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock.write_text(f"{holder.pid}\n")
        assert [a.source for a in runs.activity()] == ["lockfile"]
        with pytest.raises(RunBlocked, match="lockfile"):
            runs.start_test()
    finally:
        holder.kill()
        holder.wait()


def test_lock_held_detects_flock(tmp_path):
    import fcntl

    path = tmp_path / "x.lock"
    path.write_text("")
    assert lock_held(path) is False
    script = ("import fcntl,sys,time;f=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX);"
              "print('locked',flush=True);time.sleep(30)")
    holder = subprocess.Popen([sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert lock_held(path) is True
    finally:
        holder.kill()
        holder.wait()
    assert lock_held(tmp_path / "missing.lock") is False
    _ = fcntl


def test_describe_lists_preview_videos_for_a_test_run(runs, jobs, paths):
    job = runs.start_test()
    folder = paths.output_dir / "_previews" / job.id
    folder.mkdir(parents=True)
    (folder / "01_big-news.mp4").write_bytes(b"\x00" * 10)
    (folder / "01_big-news.json").write_text(json.dumps({"script": {"title": "Big news today"}}))
    finish(jobs, job)
    described = runs.describe(jobs.get(job.id))
    assert described["state"] == "done"
    assert described["videos"][0]["title"] == "Big news today"
    assert described["videos"][0]["url"] == f"/media/preview/{job.id}/01_big-news.mp4"


def test_failed_run_includes_the_log_tail(runs, jobs):
    job = runs.start_batch()
    (jobs.jobs_dir / job.id / "log.txt").write_text("line one\nRuntimeError: No stories scraped\n")
    finish(jobs, job, 1)
    described = runs.describe(jobs.get(job.id))
    assert described["state"] == "failed"
    assert described["error_tail"][-1] == "RuntimeError: No stories scraped"
