"""The style library through the panel: listing, uploads (validated by
content), the background add job, removal and the settings keys."""
from __future__ import annotations

import json

from newsletter_video.styles.library import Entry, Library
from newsletter_video.ui import styles_svc
from newsletter_video.ui.envstore import EnvStore
from tests.ui.conftest import multipart

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 64


def seed_library(paths, name="punchy"):
    lib = Library(paths.root / "style_library")
    folder = lib.entry_dir(name)
    folder.mkdir(parents=True)
    (folder / "contact_sheet.jpg").write_bytes(b"\xff\xd8\xff")
    (folder / "recipes.json").write_text(json.dumps({"recipes": [
        {"id": f"{name}-look", "name": "Punchy look", "description": "hard cuts", "knobs": {"transition": "cut"},
         "scene_types": ["*"], "source": name, "fidelity": 0.71}]}))
    lib.upsert(Entry(name=name, title="Punchy", duration=12.5, scene_count=4, tags=["kinetic-type", "fast-cuts"],
                     user_tags=["favourite"], notes="big words", summary="Bold type, hard cuts.", fidelity=0.71,
                     recipes=[f"{name}-look"], cost_usd=0.12))
    return lib


def test_styles_listing_with_and_without_a_library(client, paths):
    _, data = client.get_json("/api/styles")
    assert data["enabled"] is True and data["references"] == [] and data["policy"] == "auto"
    assert any(r["id"] == "kinetic-slam" for r in data["builtin_recipes"])
    seed_library(paths)
    _, data = client.get_json("/api/styles")
    ref = data["references"][0]
    assert ref["name"] == "punchy" and ref["fidelity"] == 0.71 and ref["recipes"][0]["id"] == "punchy-look"
    assert ref["contact_sheet"] == "/media/style/punchy/contact_sheet.jpg"
    assert client.request("GET", ref["contact_sheet"])[0] == 200
    _, hit = client.get_json("/api/styles?q=kinetic")
    assert [r["name"] for r in hit["references"]] == ["punchy"]
    _, miss = client.get_json("/api/styles?q=watercolour")
    assert miss["references"] == []
    _, plans = client.get_json("/api/plans")
    assert plans["recipes"][0]["id"] == "punchy-look"  # library recipes first in the per-video picker


def test_style_media_refuses_traversal(client, paths):
    seed_library(paths)
    for bad in ("/media/style/punchy/..%2Flibrary.json", "/media/style/..%2F.env/x.jpg", "/media/style/punchy/recipes.json",
                "/media/style/ghost/contact_sheet.jpg"):
        assert client.request("GET", bad)[0] == 404, bad


def test_library_off_setting_hides_everything(client, paths):
    seed_library(paths)
    assert client.send_json("PUT", "/api/settings", {"updates": {"STYLE_LIBRARY": "off"}})[0] == 200
    _, data = client.get_json("/api/styles")
    assert data["enabled"] is False and data["references"] == []
    assert client.request("GET", "/media/style/punchy/contact_sheet.jpg")[0] == 404
    body, ctype = multipart({"name": "New"}, [("video", "a.mp4", MP4)])
    assert client.request("POST", "/api/styles", body=body, content_type=ctype)[0] == 400
    assert client.send_json("PUT", "/api/settings", {"updates": {"STYLE_LIBRARY": "../outside"}})[0] == 400
    assert client.send_json("PUT", "/api/settings", {"updates": {"STYLE_RECIPE": "kinetic-slam"}})[0] == 200
    assert client.send_json("PUT", "/api/settings", {"updates": {"STYLE_RECIPE": "no spaces!"}})[0] == 400
    assert EnvStore(paths.env_path).read()["values"]["STYLE_RECIPE"] == "kinetic-slam"


def test_video_sniffing_by_content():
    assert styles_svc.sniff_video(MP4) == "mp4" and styles_svc.sniff_video(WEBM) == "webm"
    assert styles_svc.sniff_video(b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 8) == "avi"
    for bad in (b"", b"<html>", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, b"RIFF\x00\x00\x00\x00WAVE"):
        assert styles_svc.sniff_video(bad) is None


def test_add_reference_stores_the_clip_under_a_generated_name_and_starts_the_job(client, spawner, paths):
    body, ctype = multipart({"name": "Punchy Type!", "notes": "big words", "tags": "fav, hook, bad tag!"},
                            [("video", "../../evil.mp4", MP4)])
    status, _, raw = client.request("POST", "/api/styles", body=body, content_type=ctype)
    data = json.loads(raw)
    assert status == 202, data
    job = data["job"]
    assert job["kind"] == "style-add" and job["meta"]["name"] == "punchy-type" and job["meta"]["vision"] is True
    argv = spawner.calls[-1]["argv"]
    assert argv[1:4] == ["-m", "newsletter_video.styles", "add"]
    clip = argv[4]
    assert clip.startswith(str(paths.root / "style_library" / "inbox")) and "evil" not in clip and clip.endswith(".mp4")
    assert open(clip, "rb").read() == MP4
    assert argv[argv.index("--name") + 1] == "punchy-type" and argv[argv.index("--tags") + 1] == "fav,hook"
    assert spawner.calls[-1]["env"]["STYLE_LIBRARY"].endswith("style_library")
    # one at a time
    body, ctype = multipart({"name": "Other"}, [("video", "b.mp4", MP4)])
    assert client.request("POST", "/api/styles", body=body, content_type=ctype)[0] == 409


def test_add_reference_validation(client, spawner, paths):
    seed_library(paths)
    cases = [
        ({"name": "!!!"}, [("video", "a.mp4", MP4)], "letters or numbers"),
        ({"name": "Ok"}, [("video", "a.mp4", b"not a video")], "recognised video"),
        ({"name": "Ok"}, [], "empty"),
        ({"name": "Punchy"}, [("video", "a.mp4", MP4)], "already exists"),
    ]
    for fields, files, message in cases:
        body, ctype = multipart(fields, files)
        status, _, raw = client.request("POST", "/api/styles", body=body, content_type=ctype)
        assert status == 400 and message in json.loads(raw)["error"], (fields, raw)
    assert spawner.calls == []
    assert not list((paths.root / "style_library" / "inbox").glob("*")) if (paths.root / "style_library" / "inbox").is_dir() else True


def test_add_outcome_reads_cost_and_fidelity_from_the_library(client, spawner, paths, app):
    body, ctype = multipart({"name": "Punchy", "vision": "0"}, [("video", "a.mp4", MP4)])
    _, _, raw = client.request("POST", "/api/styles", body=body, content_type=ctype)
    job = json.loads(raw)["job"]
    assert "--no-vision" in spawner.calls[-1]["argv"]
    seed_library(paths)
    (app.jobs.jobs_dir / job["id"] / "log.txt").write_text("Added punchy: 4 scenes, 12.5 s, vision no, fidelity 0.71, est. $0.012\n")
    (app.jobs.jobs_dir / job["id"] / "result.json").write_text(json.dumps({"exit_code": 0, "finished_at": 1}))
    _, data = client.get_json(f"/api/jobs/{job['id']}")
    assert data["outcome"] == {"ok": True, "name": "punchy", "cost_usd": 0.012, "fidelity": 0.71, "scene_count": 4, "error": None}


def test_remove_needs_confirmation_and_deletes_the_folder(client, paths):
    lib = seed_library(paths)
    (lib.root / "inbox").mkdir()
    (lib.root / "inbox" / "punchy-abc123.mp4").write_bytes(MP4)
    assert client.send_json("POST", "/api/styles/punchy/remove", {})[0] == 400
    status, data = client.send_json("POST", "/api/styles/punchy/remove", {"confirm": True})
    assert status == 200 and data["removed"] is True
    assert not lib.entry_dir("punchy").exists() and not list((lib.root / "inbox").glob("punchy-*"))
    assert client.send_json("POST", "/api/styles/..%2Fx/remove", {"confirm": True})[0] == 404
    assert client.send_json("POST", "/api/styles/ghost/remove", {"confirm": True})[1]["removed"] is False
