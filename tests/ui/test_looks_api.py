"""The look library through the panel: listing presets with served
thumbnails, the per-video `preset` field, the LOOK_PRESET default, the kept
storyboards two looks are compared with, and the retired reference-video
routes answering 404."""
from __future__ import annotations

import json
from datetime import date

from newsletter_video import plan_manifest as pm
from newsletter_video.looks import load_presets
from newsletter_video.ui.envstore import EnvStore
from newsletter_video.ui.plans import look_key
from tests.ui.test_plans_api import seed_plan

DAY = date.today()


def test_looks_lists_every_preset_with_a_served_thumbnail(client):
    status, data = client.get_json("/api/looks")
    assert status == 200 and data["default"] == "paper"
    ids = [p["id"] for p in data["presets"]]
    assert ids == [p.id for p in load_presets()] and len(ids) >= 5
    paper = data["presets"][0]
    assert paper["default"] is True and paper["recipe"] == "" and paper["good_for"]
    glass = next(p for p in data["presets"] if p["id"] == "cinematic-glass")
    assert glass["theme"] == "cinematic" and glass["cinematic"] is True and glass["recipe"] == "look-cinematic-glass"
    for p in data["presets"]:
        assert p["thumbnail"].startswith("/static/looks/") and p["name"] and p["description"]
        status, headers, body = client.request("GET", p["thumbnail"])
        assert status == 200 and headers["Content-Type"].startswith("image/jpeg") and body[:3] == b"\xff\xd8\xff"


def test_per_video_preset_is_saved_validated_and_resolved(client, paths):
    folder = seed_plan(paths)
    ref = DAY.isoformat()
    status, plan = client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": "cinematic-glass"}})
    assert status == 200
    v = plan["videos"][0]
    assert v["look"] == {"preset": "cinematic-glass"}
    assert v["resolved"]["preset"] == "cinematic-glass" and v["resolved"]["theme"] == "cinematic"
    assert v["resolved"]["recipe"] == "look-cinematic-glass" and v["look_key"].startswith("cinematic-glass-")
    assert pm.read_manifest(folder)["videos"][0]["look"] == {"version": 1, "preset": "cinematic-glass"}
    # an explicit field still wins over the preset's
    _, plan = client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": "cinematic-glass", "theme": "light"}})
    assert plan["videos"][0]["resolved"]["theme"] == "light" and plan["videos"][0]["resolved"]["recipe"] == "look-cinematic-glass"
    # back to the default look: the field is dropped, nothing lingers
    _, plan = client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": ""}})
    assert plan["videos"][0]["look"] == {} and plan["videos"][0]["resolved"]["preset"] == ""
    status, err = client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": "no-such-look"}})
    assert status == 400 and "look.preset" in err["error"]


def test_preset_travels_as_a_run_override_to_plan_preview_and_studio(client, spawner, paths, app):
    seed_plan(paths)
    ref = DAY.isoformat()
    status, data = client.send_json("POST", "/api/plans", {"ref": None, "overrides": {"preset": "data-focus", "style": "explainer"}})
    argv = spawner.calls[-1]["argv"]
    assert status == 201 and argv[argv.index("--preset") + 1] == "data-focus" and "--test" in argv
    (paths.state_dir / "jobs" / data["job"]["id"] / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    status, _ = client.send_json("POST", f"/api/plans/{ref}/videos/v01/preview", {"overrides": {"preset": "glow-type"}})
    argv = spawner.calls[-1]["argv"]
    assert status == 201 and argv[argv.index("--preset") + 1] == "glow-type" and argv[argv.index("--preview") + 1] == "stills"
    assert client.send_json("POST", "/api/plans", {"ref": None, "overrides": {"preset": "nope"}})[0] == 400
    (paths.state_dir / "jobs" / spawner.calls[-1]["job_dir"].name / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    app.plans.studio_wait = 0.05
    status, _ = client.send_json("POST", f"/api/plans/{ref}/videos/v02/studio", {"overrides": {"preset": "depth-cards"}})
    assert status == 201 and spawner.calls[-1]["argv"][spawner.calls[-1]["argv"].index("--preset") + 1] == "depth-cards"


def test_default_preset_setting_is_validated_and_shapes_the_default_look(client, paths):
    seed_plan(paths)
    _, settings = client.get_json("/api/settings")
    assert settings["values"]["LOOK_PRESET"] == "" and "LOOK_PRESET" in settings["supported"]
    for bad in ("not-a-preset", "../x", "PAPER"):
        status, data = client.send_json("PUT", "/api/settings", {"updates": {"LOOK_PRESET": bad}})
        assert status == 400 and "LOOK_PRESET" in data["fields"], bad
    assert client.send_json("PUT", "/api/settings", {"updates": {"LOOK_PRESET": "depth-cards"}})[0] == 200
    assert EnvStore(paths.env_path).read()["values"]["LOOK_PRESET"] == "depth-cards"
    _, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    assert plan["default_look"]["preset"] == "depth-cards" and plan["default_look"]["theme"] == "dark"
    v = plan["videos"][0]
    assert v["look"] == {} and v["resolved"]["preset"] == "depth-cards" and v["resolved"]["recipe"] == "look-depth-cards"
    # the old style-library keys are no longer the panel's to write
    for key in ("STYLE_LIBRARY", "STYLE_RECIPE", "STYLE_MAX_SECONDS"):
        assert client.send_json("PUT", "/api/settings", {"updates": {key: "off"}})[0] == 400, key
    assert "STYLE_RECIPE" not in settings["values"]


def test_daily_recipe_policy_is_reflected_but_not_edited(client, paths):
    """STYLE_RECIPE in .env still shapes the look the panel shows (it is the
    daily job's setting), without becoming a panel setting."""
    seed_plan(paths)
    with paths.env_path.open("a") as handle:
        handle.write("STYLE_RECIPE=kinetic-slam\nSTYLE_LIBRARY=off\n")
    _, plan = client.get_json(f"/api/plans/{DAY.isoformat()}")
    assert plan["default_look"]["recipe"] == "kinetic-slam"
    _, settings = client.get_json("/api/settings")
    assert "STYLE_RECIPE" not in settings["values"] and "STYLE_LIBRARY" not in settings["values"]


def _write_stills(out, fp, label="1 · title"):
    (out / "frames").mkdir(parents=True, exist_ok=True)
    (out / "storyboard.jpg").write_bytes(b"\xff\xd8\xff" + label.encode())
    (out / "frames" / "00_001.2s.png").write_bytes(b"\x89PNG" + label.encode())
    (out / "preview.json").write_text(json.dumps({"version": 1, "key": "v01", "look": {"style": "explainer"}, "stills": {
        "tier": "stills", "file": "storyboard.jpg", "timing": "estimated timing", "seconds": 12.3, "at": 1700000000,
        "fingerprint": fp, "duration": 40, "frames": [{"label": label, "at": 1.2, "detail": "", "file": "00_001.2s.png"}]}}))


def test_changing_the_look_keeps_the_old_storyboard_for_comparison(client, paths, spawner):
    folder = seed_plan(paths)
    ref = DAY.isoformat()
    out = folder / "_preview" / "v01"
    _write_stills(out, pm.entry_fingerprint(pm.get_entry(pm.read_manifest(folder), "v01")))
    _, plan = client.get_json(f"/api/plans/{ref}")
    v = plan["videos"][0]
    assert "stills" in v["preview"] and "looks" not in v["preview"]
    first_key = v["look_key"]
    # a new preset: the old storyboard is hidden (stale) but kept under its look
    _, plan = client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": "glow-type"}})
    v = plan["videos"][0]
    assert "stills" not in v["preview"]
    kept = v["preview"]["looks"]
    assert [k["key"] for k in kept] == [first_key] and kept[0]["look"]["preset"] == "" and kept[0]["at"] == 1700000000
    assert kept[0]["url"] == f"/media/plan/{ref}/v01/looks/{first_key}/storyboard.jpg"
    assert kept[0]["frames"][0]["url"] == f"/media/plan/{ref}/v01/looks/{first_key}/frames/00_001.2s.png"
    status, headers, body = client.request("GET", kept[0]["url"])
    assert status == 200 and headers["Content-Type"] == "image/jpeg" and body.startswith(b"\xff\xd8\xff")
    assert client.request("GET", kept[0]["frames"][0]["url"])[0] == 200
    for bad in (f"/media/plan/{ref}/v01/looks/{first_key}/../storyboard.jpg", f"/media/plan/{ref}/v01/looks.json",
                f"/media/plan/{ref}/v01/looks/{first_key}/frames/x.png", f"/media/plan/{ref}/v01/looks/Bad_Key/storyboard.jpg"):
        assert client.request("GET", bad)[0] == 404, bad
    # a storyboard of the new look, then "storyboard again": the current look is kept too, newest first
    _write_stills(out, pm.entry_fingerprint(pm.get_entry(pm.read_manifest(folder), "v01")), label="glow")
    status, _ = client.send_json("POST", f"/api/plans/{ref}/videos/v01/preview", {})
    assert status == 201
    _, plan = client.get_json(f"/api/plans/{ref}")
    v = plan["videos"][0]
    assert [k["key"] for k in v["preview"]["looks"]] == [v["look_key"], first_key]
    assert v["look_key"].startswith("glow-type-") and v["look_key"] == look_key(v["resolved"])
    # a stale storyboard is never kept
    (paths.state_dir / "jobs" / spawner.calls[-1]["job_dir"].name / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    client.send_json("PUT", f"/api/plans/{ref}/videos/v01/look", {"look": {"preset": "paper"}})
    _, plan = client.get_json(f"/api/plans/{ref}")
    assert len(plan["videos"][0]["preview"]["looks"]) == 2  # unchanged: the glow storyboard had already been replaced by a stale one


def test_reference_video_routes_are_gone(client):
    assert client.get_json("/api/styles")[0] == 404
    assert client.request("GET", "/media/style/punchy/contact_sheet.jpg")[0] == 404
    assert client.send_json("POST", "/api/styles/punchy/remove", {"confirm": True})[0] == 404
    status, _, _ = client.request("POST", "/api/styles", body=b"x", content_type="multipart/form-data; boundary=x")
    assert status == 404
