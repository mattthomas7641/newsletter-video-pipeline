"""Plans, previews and final renders through the panel's API."""
from __future__ import annotations

import json
from datetime import date

from newsletter_video import plan_manifest as pm
from tests.conftest import make_script
from tests.ui.conftest import make_character

DAY = date.today()


def seed_plan(paths, ref=None, n=2):
    folder = paths.output_dir / (ref or DAY.isoformat())
    pm.write_manifest(folder, pm.new_manifest([make_script(f"Story {i}") for i in range(1, n + 1)], DAY))
    return folder


def test_plans_list_and_describe(client, paths):
    _, empty = client.get_json("/api/plans")
    assert empty["plans"] == [] and "recipes" in empty
    seed_plan(paths)
    _, data = client.get_json("/api/plans")
    assert data["plans"][0]["ref"] == DAY.isoformat() and data["plans"][0]["count"] == 2
    status, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    assert status == 200 and [v["id"] for v in plan["videos"]] == ["v01", "v02"]
    v = plan["videos"][0]
    assert v["resolved"]["style"] in ("avatar", "explainer") and v["look"] == {} and v["preview"] == {}
    assert client.get_json("/api/plans/2000-01-01")[0] == 404
    assert client.get_json("/api/plans/..%2F..")[0] == 404


def test_set_look_validates_and_persists(client, paths):
    folder = seed_plan(paths)
    make_character(paths)
    status, plan = client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v01/look",
                                    {"look": {"style": "avatar", "character": "gizmo", "theme": ""}})
    assert status == 200 and plan["videos"][0]["look"] == {"style": "avatar", "character": "gizmo"}
    assert plan["videos"][0]["resolved"]["character"] == "gizmo"
    assert pm.read_manifest(folder)["videos"][0]["look"] == {"version": 1, "style": "avatar", "character": "gizmo"}
    status, err = client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v01/look", {"look": {"style": "cartoon"}})
    assert status == 400 and "look.style" in err["error"]
    status, err = client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v01/look", {"look": {"character": "ghost"}})
    assert status == 400 and "doesn't exist" in err["error"]
    assert client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v09/look", {"look": {}})[0] == 404
    assert client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v01/look", {"look": "x"})[0] == 400


def test_plan_job_for_a_day_and_for_a_test(client, spawner, paths):
    status, data = client.send_json("POST", "/api/plans", {"ref": DAY.isoformat(), "videos": 2,
                                                            "overrides": {"style": "explainer", "theme": "dark"}})
    assert status == 201 and data["job"]["kind"] == "plan"
    argv = spawner.calls[-1]["argv"]
    assert argv[1:3] == ["-m", "newsletter_video.plan_day"] and DAY.isoformat() in argv
    assert argv[argv.index("--videos") + 1] == "2" and "--style" in argv and argv[argv.index("--theme") + 1] == "dark"
    assert spawner.calls[-1]["env"]["NOTIFY_WEBHOOK_URL"] == ""
    assert client.send_json("POST", "/api/plans", {"ref": None})[0] == 409  # one heavy job at a time
    (paths.state_dir / "jobs" / data["job"]["id"] / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    status, data = client.send_json("POST", "/api/plans", {"ref": None, "overrides": {}})
    assert status == 201 and data["job"]["meta"]["test"] is True
    argv = spawner.calls[-1]["argv"]
    assert "--test" in argv and str(paths.output_dir / "_previews" / data["job"]["id"]) in argv
    assert argv[argv.index("--videos") + 1] == "1"


def test_plan_job_rejects_bad_input(client, spawner):
    assert client.send_json("POST", "/api/plans", {"ref": DAY.isoformat(), "overrides": {"style": "cartoon"}})[0] == 400
    assert client.send_json("POST", "/api/plans", {"ref": DAY.isoformat(), "videos": 99})[0] == 400
    assert client.send_json("POST", "/api/plans", {"ref": "not-a-date"})[0] in (400, 404)
    assert client.send_json("POST", "/api/plans", {"ref": DAY.isoformat(), "overrides": {"ANTHROPIC_API_KEY": "x"}})[0] == 400
    assert spawner.calls == []


def test_preview_jobs_default_to_stills_and_never_pass_voice_unless_asked(client, spawner, paths):
    seed_plan(paths)
    status, data = client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v01/preview", {})
    assert status == 201 and data["job"]["kind"] == "preview" and data["job"]["meta"]["tier"] == "stills"
    argv = spawner.calls[-1]["argv"]
    assert argv[1:3] == ["-m", "newsletter_video.preview"] and argv[argv.index("--preview") + 1] == "stills"
    assert "--voice" not in argv and "--final" not in argv
    (paths.state_dir / "jobs" / data["job"]["id"] / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    status, data = client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v02/preview",
                                    {"tier": "draft", "voice": True, "overrides": {"theme": "dark"}})
    argv = spawner.calls[-1]["argv"]
    assert status == 201 and "--voice" in argv and argv[argv.index("--preview") + 1] == "draft" and "--theme" in argv
    assert client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v02/preview", {"tier": "final"})[0] in (400, 409)


def test_preview_of_an_existing_rendered_video(client, spawner, paths):
    folder = paths.output_dir / DAY.isoformat()
    folder.mkdir(parents=True)
    (folder / "03_thing.json").write_text(json.dumps({"title": "Thing", "script": {"title": "Thing", "hook": "h", "beats": [], "outro": "o"}}))
    status, data = client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/03_thing/preview", {"tier": "stills"})
    assert status == 201 and str(folder / "03_thing.json") in spawner.calls[-1]["argv"]
    assert client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/..%2Fx/preview", {})[0] == 404
    assert client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/04_missing/preview", {})[0] == 404


def test_preview_results_are_reported_and_served(client, paths, app, spawner):
    seed_plan(paths)
    out = paths.output_dir / DAY.isoformat() / "_preview" / "v01"
    (out / "frames").mkdir(parents=True)
    (out / "storyboard.jpg").write_bytes(b"\xff\xd8\xff")
    (out / "frames" / "00_001.2s.png").write_bytes(b"\x89PNG")
    fp = pm.entry_fingerprint(pm.get_entry(pm.read_manifest(paths.output_dir / DAY.isoformat()), "v01"))
    (out / "preview.json").write_text(json.dumps({"version": 1, "key": "v01", "look": {"style": "explainer"}, "stills": {
        "tier": "stills", "file": "storyboard.jpg", "timing": "estimated timing", "seconds": 12.3, "at": 1, "fingerprint": fp,
        "duration": 40, "frames": [{"label": "1 · title", "at": 1.2, "detail": "", "file": "00_001.2s.png"}]}}))
    _, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    stills = plan["videos"][0]["preview"]["stills"]
    assert stills["url"] == f"/media/plan/{DAY.isoformat()}/v01/storyboard.jpg" and stills["timing"] == "estimated timing"
    assert stills["frames"][0]["url"].endswith("/frames/00_001.2s.png")
    status, headers, body = client.request("GET", stills["url"])
    assert status == 200 and body == b"\xff\xd8\xff" and headers["Content-Type"] == "image/jpeg"
    assert client.request("GET", stills["frames"][0]["url"])[0] == 200
    for bad in (f"/media/plan/{DAY.isoformat()}/v01/preview.json", f"/media/plan/{DAY.isoformat()}/v01/frames/..%2Fstoryboard.jpg",
                f"/media/plan/{DAY.isoformat()}/..%2F..%2F.env", "/media/plan/zz/v01/storyboard.jpg"):
        assert client.request("GET", bad)[0] == 404, bad
    # a finished preview job reports the same outcome
    _, data = client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v01/preview", {})
    (app.jobs.jobs_dir / data["job"]["id"] / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    _, job = client.get_json(f"/api/jobs/{data['job']['id']}")
    assert job["outcome"]["stills"]["url"] == stills["url"]


def test_rewrite_needs_confirmation_and_starts_the_writer_only_then(client, spawner, paths):
    seed_plan(paths)
    assert client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v01/rewrite", {})[0] == 400
    assert spawner.calls == []
    status, data = client.send_json("POST", f"/api/plans/{DAY.isoformat()}/videos/v01/rewrite", {"confirm": True})
    assert status == 201 and data["job"]["kind"] == "rewrite"
    argv = spawner.calls[-1]["argv"]
    assert argv[1:3] == ["-m", "newsletter_video.plan_day"] and argv[argv.index("--rewrite") + 1] == "v01"


def test_final_render_is_explicit_confirmed_and_selectable(client, spawner, paths):
    seed_plan(paths)
    ref = DAY.isoformat()
    assert client.send_json("POST", f"/api/plans/{ref}/render", {"only": ["v01"]})[0] == 400  # no confirmation
    assert client.send_json("POST", f"/api/plans/{ref}/render", {"confirm": True, "only": ["v09"]})[0] == 400
    assert client.send_json("POST", f"/api/plans/{ref}/render", {"confirm": True, "only": []})[0] == 400
    assert spawner.calls == []
    status, data = client.send_json("POST", f"/api/plans/{ref}/render", {"confirm": True, "only": ["v02"]})
    assert status == 201 and data["job"]["kind"] == "render" and data["job"]["meta"]["videos"] == 1
    argv = spawner.calls[-1]["argv"]
    assert argv[1:3] == ["-m", "newsletter_video.render_plan"] and "--final" in argv and argv[argv.index("--only") + 1] == "v02"
    assert "--test" not in argv
    assert client.send_json("POST", f"/api/plans/{ref}/render", {"confirm": True})[0] == 409


def test_test_plan_renders_never_upload(client, spawner, paths):
    seed_plan(paths, ref="_previews/abc123def")
    status, data = client.send_json("POST", "/api/plans/abc123def/render", {"confirm": True})
    assert status == 201 and data["job"]["meta"]["uploads"] is False
    call = spawner.calls[-1]
    assert "--test" in call["argv"] and call["env"]["YOUTUBE_UPLOAD"] == "0"
    _, plans = client.get_json("/api/plans")
    assert plans["plans"][0]["ref"] == "abc123def" and plans["plans"][0]["kind"] == "day"


def test_stale_previews_are_not_reported(client, paths):
    folder = seed_plan(paths)
    entry = pm.get_entry(pm.read_manifest(folder), "v01")
    out = folder / "_preview" / "v01"
    out.mkdir(parents=True)
    (out / "storyboard.jpg").write_bytes(b"\xff\xd8\xff")
    (out / "preview.json").write_text(json.dumps({"version": 1, "stills": {
        "tier": "stills", "file": "storyboard.jpg", "timing": "estimated timing", "frames": [],
        "fingerprint": pm.entry_fingerprint(entry)}}))
    _, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    assert "stills" in plan["videos"][0]["preview"]
    client.send_json("PUT", f"/api/plans/{DAY.isoformat()}/videos/v01/look", {"look": {"theme": "dark"}})
    _, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    assert plan["videos"][0]["preview"] == {}  # the look changed: the old storyboard is hidden


def test_no_plan_endpoint_leaks_secrets(client, paths):
    from tests.ui.conftest import SECRETS

    seed_plan(paths)
    for path in ("/api/plans", f"/api/plans/{DAY.isoformat()}", "/api/styles"):
        status, _, body = client.request("GET", path)
        assert status == 200 and not any(s.encode() in body for s in SECRETS), path
