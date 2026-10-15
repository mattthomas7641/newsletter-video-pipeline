import io
import json
import sys
import types
from datetime import date

import pytest
from PIL import Image

from newsletter_video.ui import preview_runner
from tests.ui.conftest import ENV_TEXT, SECRETS, make_character, multipart


def png(alpha=True) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", (30, 40), (1, 2, 3, 0) if alpha else (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40


# --- settings ---

def test_get_settings_has_values_support_flags_and_key_presence_only(client):
    status, data = client.get_json("/api/settings")
    assert status == 200
    assert data["values"]["VIDEOS_PER_DAY"] == "3"
    assert data["keys"] == {"ANTHROPIC_API_KEY": True, "TYPESAFE_API_KEY": True,
                            "ELEVENLABS_API_KEY": False, "NOTIFY_WEBHOOK_URL": True}
    assert data["choices"]["YOUTUBE_PRIVACY"] == ["private", "unlisted", "public"]
    assert set(data["supported"]) == set(data["values"])


def test_put_settings_writes_env_and_returns_new_state(client, paths):
    status, data = client.send_json("PUT", "/api/settings", {"updates": {
        "VIDEOS_PER_DAY": 6, "YOUTUBE_UPLOAD": "1", "YOUTUBE_PRIVACY": "unlisted",
        "VIDEO_STYLE": "explainer", "VIDEO_THEME": "dark"}})
    assert status == 200 and sorted(data["changed"]) == sorted(
        ["VIDEOS_PER_DAY", "YOUTUBE_UPLOAD", "YOUTUBE_PRIVACY", "VIDEO_STYLE", "VIDEO_THEME"])
    assert data["values"]["VIDEO_STYLE"] == "explainer"
    text = paths.env_path.read_text()
    assert "VIDEOS_PER_DAY=6" in text and "VIDEO_THEME=dark" in text and "UNRELATED_SETTING=hello world" in text


def test_put_settings_rejects_secrets_unknown_keys_and_bad_values(client, paths):
    for updates in ({"ANTHROPIC_API_KEY": "hacked"}, {"PATH": "/tmp"}, {"VIDEOS_PER_DAY": "99"},
                    {"YOUTUBE_PRIVACY": "world"}):
        status, data = client.send_json("PUT", "/api/settings", {"updates": updates})
        assert status == 400 and data["fields"], updates
    assert client.send_json("PUT", "/api/settings", {"updates": {}})[0] == 400
    assert client.send_json("PUT", "/api/settings", {"nothing": 1})[0] == 400
    assert paths.env_path.read_text() == ENV_TEXT


def test_selecting_an_unknown_character_is_refused(client, paths):
    status, data = client.send_json("PUT", "/api/settings", {"updates": {"CHARACTER": "ghost"}})
    assert status == 400 and "CHARACTER" in data["fields"]
    make_character(paths)
    assert client.send_json("PUT", "/api/settings", {"updates": {"CHARACTER": "gizmo"}})[0] == 200
    assert client.send_json("PUT", "/api/settings", {"updates": {"CHARACTER": ""}})[0] == 200


# --- secrets never leave the server ---

def test_no_get_endpoint_ever_leaks_a_secret(client, paths, app, jobs=None):
    make_character(paths)
    job = app.jobs.create("test", ["x"], {}, {})
    (app.jobs.jobs_dir / job.id / "log.txt").write_text("\n".join(SECRETS) + "\n")
    (paths.output_dir / date.today().isoformat()).mkdir()
    for path in ["/", "/api/settings", "/api/characters", "/api/voicebox/profiles", "/api/setup", "/api/runs",
                 "/api/videos", f"/api/jobs/{job.id}", f"/api/jobs/{job.id}?since=0"]:
        status, _, body = client.request("GET", path)
        assert status == 200, path
        for secret in SECRETS:
            assert secret.encode() not in body, (path, secret)
    # the page embeds only the per-launch token, never the settings file
    _, _, page = client.request("GET", "/")
    assert b"hello world" not in page


def test_error_responses_do_not_echo_secrets(client):
    status, _, body = client.request("PUT", "/api/settings", json_body={"updates": {"ANTHROPIC_API_KEY": "x"}})
    assert status == 400
    assert not any(s.encode() in body for s in SECRETS)


# --- characters ---

def test_characters_list_includes_default_and_attached_voice(client, paths):
    make_character(paths)
    make_character(paths, slug="plain", name="Plain", voice=False)
    _, data = client.get_json("/api/characters")
    items = {c["slug"]: c for c in data["characters"]}
    assert list(items)[0] == "" and items[""]["is_default"] and items[""]["name"] == "Default"
    assert items["gizmo"]["voice"]["kind"] == "cloned" and items["gizmo"]["persona"] == "chirpy"
    assert items["plain"]["voice"]["kind"] == "standard"
    assert items["gizmo"]["avatar_url"].startswith("/media/avatar/gizmo")
    assert "vb-profile-1234" not in json.dumps(data)  # profile ids aren't needed by the browser


def test_avatar_media_is_served_and_path_traversal_is_not(client, paths):
    make_character(paths)
    (paths.root / "secret.png").write_bytes(png())
    status, headers, body = client.request("GET", "/media/avatar/gizmo")
    assert status == 200 and headers["Content-Type"] == "image/png" and body.startswith(b"\x89PNG")
    for bad in ("/media/avatar/..%2Fsecret", "/media/avatar/%2e%2e", "/media/avatar/ghost", "/media/avatar/default"):
        assert client.request("GET", bad)[0] == 404
    (paths.legacy_character_dir / "me.png").write_bytes(png())
    assert client.request("GET", "/media/avatar/default")[0] == 200


def test_voicebox_profiles_and_graceful_failure(client, voicebox):
    _, data = client.get_json("/api/voicebox/profiles")
    assert data["available"] and [p["name"] for p in data["profiles"]] == ["Narrator Nina", "Bob"]
    voicebox.up = False
    _, data = client.get_json("/api/voicebox/profiles")
    assert data == {"available": False, "profiles": []}


def test_add_character_clone_flow_creates_a_background_job(client, paths, spawner):
    body, ctype = multipart({"name": "Captain Zap", "voice_mode": "clone", "persona": "dramatic"},
                            [("avatar", "../../evil.png", png()), ("voice", "a.wav", WAV), ("voice", "b.wav", WAV)])
    status, _, raw = client.request("POST", "/api/characters", body=body, content_type=ctype)
    data = json.loads(raw)
    assert status == 202, data
    call = spawner.calls[-1]
    spec_path = call["argv"][call["argv"].index("--spec") + 1]
    spec = json.loads(open(spec_path).read())
    assert spec["name"] == "Captain Zap" and spec["persona"] == "dramatic" and len(spec["voice_audio"]) == 2
    # files are stored under our own generated names, never the client's filename
    assert all(str(paths.state_dir / "uploads") in p for p in [spec["avatar"], *spec["voice_audio"]])
    assert "evil" not in spec["avatar"] and spec["avatar"].endswith("avatar.png")
    assert call["env"]["BYTESIZE_ENV_FILE"] == str(paths.env_path)
    assert call["argv"][1:4] == ["-m", "newsletter_video.ui.workers", "add"]


@pytest.mark.parametrize("fields,files,message", [
    ({"name": "!!!", "voice_mode": "default"}, [("avatar", "a.png", png())], "letters or numbers"),
    ({"name": "Ok", "voice_mode": "default"}, [("avatar", "a.png", b"not an image")], "PNG, JPEG or WebP"),
    ({"name": "Ok", "voice_mode": "default"}, [], "empty"),
    ({"name": "Ok", "voice_mode": "clone"}, [("avatar", "a.png", png())], "at least one voice clip"),
    ({"name": "Ok", "voice_mode": "clone"}, [("avatar", "a.png", png()), ("voice", "a.txt", b"nope")], "audio"),
    ({"name": "Ok", "voice_mode": "profile", "profile_id": "../../x"}, [("avatar", "a.png", png())], "Voicebox voices"),
    ({"name": "Ok", "voice_mode": "wat"}, [("avatar", "a.png", png())], "Unknown voice option"),
])
def test_add_character_validation_errors_are_plain_language(client, spawner, fields, files, message):
    body, ctype = multipart(fields, files)
    status, _, raw = client.request("POST", "/api/characters", body=body, content_type=ctype)
    assert status == 400 and message in json.loads(raw)["error"]
    assert spawner.calls == []


def test_add_character_refuses_duplicate_names(client, paths, spawner):
    make_character(paths)
    body, ctype = multipart({"name": "Gizmo", "voice_mode": "default"}, [("avatar", "a.png", png())])
    status, _, raw = client.request("POST", "/api/characters", body=body, content_type=ctype)
    assert status == 400 and "already exists" in json.loads(raw)["error"]


def test_only_one_character_is_added_at_a_time(client, spawner):
    body, ctype = multipart({"name": "One", "voice_mode": "default"}, [("avatar", "a.png", png())])
    assert client.request("POST", "/api/characters", body=body, content_type=ctype)[0] == 202
    body, ctype = multipart({"name": "Two", "voice_mode": "default"}, [("avatar", "a.png", png())])
    assert client.request("POST", "/api/characters", body=body, content_type=ctype)[0] == 409


def test_add_job_reports_worker_outcome(client, paths, app):
    body, ctype = multipart({"name": "Zed", "voice_mode": "profile", "profile_id": "p1"}, [("avatar", "a.png", png())])
    _, _, raw = client.request("POST", "/api/characters", body=body, content_type=ctype)
    job = json.loads(raw)["job"]
    folder = paths.state_dir / "uploads" / job["meta"]["upload_id"]
    (folder / "outcome.json").write_text(json.dumps({"ok": False, "message": "Voicebox isn't running.", "slug": None}))
    (app.jobs.jobs_dir / job["id"] / "result.json").write_text(json.dumps({"exit_code": 1, "finished_at": 1}))
    _, data = client.get_json(f"/api/jobs/{job['id']}")
    assert data["job"]["state"] == "failed" and data["outcome"]["message"] == "Voicebox isn't running."


# --- voice samples ---

def test_sample_starts_a_worker_then_serves_the_cached_file(client, paths, spawner, app):
    make_character(paths)
    status, data = client.send_json("POST", "/api/characters/gizmo/sample", {})
    assert status == 202 and data["state"] == "running"
    call = spawner.calls[-1]["argv"]
    assert call[1:4] == ["-m", "newsletter_video.ui.workers", "sample"] and call[call.index("--slug") + 1] == "gizmo"
    out = call[call.index("--out") + 1]
    __import__("pathlib").Path(out).parent.mkdir(parents=True, exist_ok=True)
    open(out, "wb").write(WAV)
    status, data = client.send_json("POST", "/api/characters/gizmo/sample", {})
    assert status == 202 and data["state"] == "ready"
    status, headers, body = client.request("GET", data["url"])
    assert status == 200 and headers["Content-Type"] == "audio/wav" and body == WAV


def test_sample_for_default_and_unknown_characters(client, spawner):
    assert client.send_json("POST", "/api/characters/default/sample", {})[0] == 202
    assert spawner.calls[-1]["argv"][spawner.calls[-1]["argv"].index("--slug") + 1] == "default"
    assert client.send_json("POST", "/api/characters/ghost/sample", {})[0] == 404


def test_sample_media_rejects_odd_names(client):
    for bad in ("/media/sample/..%2F..%2F.env", "/media/sample/x.wav", "/media/sample/a-123456789abc.mp3"):
        assert client.request("GET", bad)[0] == 404


# --- runs ---

def test_start_test_run_via_api_ignores_unsaved_secrets_and_never_uploads(client, spawner):
    status, data = client.send_json("POST", "/api/runs", {"kind": "test", "overrides": {
        "VIDEO_STYLE": "explainer", "YOUTUBE_UPLOAD": "1"}})
    assert status == 201 and data["job"]["kind"] == "test" and data["job"]["state"] == "running"
    env = spawner.calls[-1]["env"]
    assert env["YOUTUBE_UPLOAD"] == "0" and env["VIDEO_STYLE"] == "explainer"
    status, data = client.send_json("POST", "/api/runs", {"kind": "test", "overrides": {"ANTHROPIC_API_KEY": "x"}})
    assert status == 409 or status == 400  # blocked by the first run, or invalid; either way nothing started


def test_batch_needs_confirmation_and_is_refused_while_running(client, spawner):
    assert client.send_json("POST", "/api/runs", {"kind": "batch"})[0] == 400
    assert spawner.calls == []
    assert client.send_json("POST", "/api/runs", {"kind": "batch", "confirm": True})[0] == 201
    status, data = client.send_json("POST", "/api/runs", {"kind": "batch", "confirm": True})
    assert status == 409 and "already being made" in data["error"]
    assert len(spawner.calls) == 1


def test_runs_endpoint_reports_activity_and_latest(client):
    client.send_json("POST", "/api/runs", {"kind": "test"})
    _, data = client.get_json("/api/runs")
    assert data["activity"][0]["source"] == "panel" and data["latest"]["kind"] == "test"
    assert "schedule" in data


def test_cancel_endpoint(client, paths, sleeper, app):
    proc = sleeper()
    from tests.ui.conftest import FakeSpawner
    spawner = FakeSpawner()
    spawner.pid = proc.pid
    app.jobs.spawner = spawner
    _, data = client.send_json("POST", "/api/runs", {"kind": "test"})
    status, out = client.send_json("POST", f"/api/jobs/{data['job']['id']}/cancel", {})
    assert status == 200 and out["cancelled"] is True
    proc.wait(timeout=5)
    _, data2 = client.get_json(f"/api/jobs/{data['job']['id']}")
    assert data2["job"]["state"] == "cancelled"


def test_job_endpoint_streams_log_by_offset(client, app):
    _, data = client.send_json("POST", "/api/runs", {"kind": "test"})
    job_id = data["job"]["id"]
    log = app.jobs.jobs_dir / job_id / "log.txt"
    log.write_text("2026-10-15 07:00:00,1 [INFO] Scraping editions: x\n")
    _, first = client.get_json(f"/api/jobs/{job_id}?since=0")
    assert "Scraping" in first["log"]["text"] and first["job"]["stage"] == "Reading today's newsletters"
    log.write_text(log.read_text() + "more\n")
    _, second = client.get_json(f"/api/jobs/{job_id}?since={first['log']['next']}")
    assert second["log"]["text"] == "more\n"
    assert client.get_json("/api/jobs/zzzzzz")[0] == 404


# --- history and media ---

def test_videos_endpoint_is_tolerant_of_missing_and_partial_files(client, paths):
    today = paths.output_dir / date.today().isoformat()
    today.mkdir()
    (today / "01_a.mp4").write_bytes(b"\x00" * 100)
    (today / "01_a.json").write_text(json.dumps({"title": "Story A", "script": {"title": "Story A!"}}))
    (today / "02_b.mp4").write_bytes(b"\x00" * 10)  # no sidecar at all
    (today / "03_c.json").write_text("{half written")  # corrupt sidecar, no video
    (today / "uploads.json").write_text(json.dumps({"01_a.mp4": {"id": "abcDEF12345", "privacy": "private"}}))
    (today / "02_b.status.json").write_text(json.dumps({"state": "failed", "error": "render crashed"}))
    status, data = client.get_json("/api/videos")
    assert status == 200
    videos = {v["name"]: v for v in data["today"]["videos"]}
    assert videos["01_a.mp4"]["title"] == "Story A!"
    assert videos["01_a.mp4"]["youtube"]["url"] == "https://www.youtube.com/shorts/abcDEF12345"
    assert videos["01_a.mp4"]["status"]["state"] == "Uploaded"
    assert videos["02_b.mp4"]["status"] == {"state": "failed", "error": "render crashed", "tone": "bad"}
    assert videos["03_c.mp4"]["has_file"] is False and videos["03_c.mp4"]["url"] is None


def test_videos_endpoint_with_nothing_on_disk(client):
    _, data = client.get_json("/api/videos")
    assert data["today"]["videos"] == [] and data["days"] == [] and data["previews"] == []


def test_day_level_status_json_in_several_shapes(client, paths):
    today = paths.output_dir / date.today().isoformat()
    today.mkdir()
    for n in ("01_a", "02_b"):
        (today / f"{n}.mp4").write_bytes(b"x")
    (today / "status.json").write_text(json.dumps({"videos": {"01_a.mp4": {"status": "rendering"},
                                                              "02_b.mp4": {"status": "done"}}}))
    _, data = client.get_json("/api/videos")
    states = {v["name"]: (v["status"]["state"], v["status"]["tone"]) for v in data["today"]["videos"]}
    assert states == {"01_a.mp4": ("rendering", "active"), "02_b.mp4": ("done", "good")}
    (today / "status.json").write_text("[1, 2, 3]")  # unexpected shape: ignored, not a crash
    assert client.get_json("/api/videos")[0] == 200


def test_video_media_supports_range_requests(client, paths):
    folder = paths.output_dir / "2026-10-14"
    folder.mkdir()
    payload = bytes(range(256)) * 4
    (folder / "01_a.mp4").write_bytes(payload)
    status, headers, body = client.request("GET", "/media/video/2026-10-14/01_a.mp4")
    assert status == 200 and body == payload and headers["Accept-Ranges"] == "bytes"
    status, headers, body = client.request("GET", "/media/video/2026-10-14/01_a.mp4", headers={"Range": "bytes=10-19"})
    assert status == 206 and body == payload[10:20] and headers["Content-Range"] == "bytes 10-19/1024"
    status, _, body = client.request("GET", "/media/video/2026-10-14/01_a.mp4", headers={"Range": "bytes=-5"})
    assert status == 206 and body == payload[-5:]
    assert client.request("GET", "/media/video/2026-10-14/01_a.mp4", headers={"Range": "bytes=5000-"})[0] == 416


def test_video_media_refuses_traversal_and_non_videos(client, paths):
    (paths.output_dir / "2026-10-14").mkdir()
    (paths.output_dir / "2026-10-14" / "01_a.json").write_text("{}")
    (paths.root / "other.mp4").write_bytes(b"x")
    for bad in ("/media/video/2026-10-14/01_a.json", "/media/video/2026-10-14/..%2F..%2Fother.mp4",
                "/media/video/2026-10-14/%2e%2e%2f%2e%2e%2fother.mp4", "/media/video/..%2F/other.mp4",
                "/media/video/2026-10-14/missing.mp4", "/media/preview/zzzzzz/..%2Fx.mp4"):
        assert client.request("GET", bad)[0] == 404, bad


def test_symlinked_video_outside_output_is_not_served(client, paths, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    folder = paths.output_dir / "2026-10-14"
    folder.mkdir()
    (folder / "01_link.mp4").symlink_to(outside)
    assert client.request("GET", "/media/video/2026-10-14/01_link.mp4")[0] == 404


def test_preview_videos_are_listed_and_served(client, paths):
    folder = paths.output_dir / "_previews" / "abc123def"
    folder.mkdir(parents=True)
    (folder / "01_t.mp4").write_bytes(b"vid")
    _, data = client.get_json("/api/videos")
    assert data["previews"][0]["url"] == "/media/preview/abc123def/01_t.mp4"
    assert client.request("GET", "/media/preview/abc123def/01_t.mp4")[2] == b"vid"


def test_setup_check_reports_presence_without_values(client, paths):
    (paths.secrets_dir).mkdir()
    (paths.secrets_dir / "youtube_token.json").write_text('{"refresh_token": "SUPERSECRETTOKEN"}')
    _, _, raw = client.request("GET", "/api/setup")
    assert b"SUPERSECRETTOKEN" not in raw
    checks = {c["id"]: c for c in json.loads(raw)["checks"]}
    assert checks["writer"]["ok"] is True and checks["youtube"]["ok"] is True and checks["voicebox"]["ok"] is True


def test_schedule_is_read_from_the_plist(client, paths):
    import plistlib

    paths.launchd_plist.write_bytes(plistlib.dumps({
        "Label": "com.tldrbrainrot.daily", "StartCalendarInterval": {"Hour": 7, "Minute": 0},
        "EnvironmentVariables": {"ANTHROPIC_API_KEY": "should-never-be-shown"}}))
    _, _, raw = client.request("GET", "/api/runs")
    data = json.loads(raw)
    assert data["schedule"]["time"] == "07:00" and b"should-never-be-shown" not in raw


# --- preview runner: redirects output, switches uploads off ---

def test_preview_runner_redirects_and_disables_uploads(tmp_path, monkeypatch):
    calls = {}
    rp = types.ModuleType("newsletter_video.run_pipeline")
    yu = types.ModuleType("newsletter_video.youtube_upload")

    def fake_build(scripts, out_dir, audio_dir, target_date=None):
        calls["build"] = (out_dir, audio_dir)
        (out_dir / "01_x.mp4").write_bytes(b"v")
        return [out_dir / "01_x.mp4"]

    def real_upload(*a, **k):
        calls["UPLOADED"] = True
        return ["id"]

    def fake_run(day):
        paths = rp.build_shorts(["s"], tmp_path / "output" / "2026-10-15", tmp_path / "output" / "_audio", day)
        rp.upload_recent(day)
        yu.upload_day(day)
        rp.clean_old_outputs(day)
        return paths

    rp.build_shorts, rp.upload_recent, rp.run = fake_build, real_upload, fake_run
    rp.clean_old_outputs = lambda *a, **k: calls.setdefault("CLEANED", True)
    yu.upload_day = real_upload
    pkg = sys.modules.setdefault("newsletter_video", types.ModuleType("newsletter_video"))
    monkeypatch.setitem(sys.modules, "newsletter_video.run_pipeline", rp)
    monkeypatch.setitem(sys.modules, "newsletter_video.youtube_upload", yu)
    monkeypatch.setattr(pkg, "run_pipeline", rp, raising=False)
    monkeypatch.setattr(pkg, "youtube_upload", yu, raising=False)
    monkeypatch.setenv("YOUTUBE_UPLOAD", "1")

    out = tmp_path / "preview"
    assert preview_runner.main(["--out", str(out)]) == 0
    assert calls["build"][0] == out.resolve() and str(out.resolve()) in str(calls["build"][1])
    assert (out / "01_x.mp4").exists()
    assert "UPLOADED" not in calls and "CLEANED" not in calls
    import os
    assert os.environ["YOUTUBE_UPLOAD"] == "0" and os.environ["VIDEOS_PER_DAY"] == "1"
