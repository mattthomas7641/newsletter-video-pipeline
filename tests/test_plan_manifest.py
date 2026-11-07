"""plan.json round trips, the no-implicit-final-render rule, and the final
render of a plan through the pipeline's own builder (stubbed)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from newsletter_video import plan_manifest as pm
from newsletter_video import render_plan, run_pipeline
from newsletter_video.explainer_writer import script_from_plan
from newsletter_video.runstate import NON_SIDECAR_JSON, sidecars
from newsletter_video.scene_plan import validate_plan
from newsletter_video.youtube_upload import write_metadata
from tests.conftest import make_script, make_story

DAY = date(2026, 11, 7)
PLAN = [
    {"type": "title", "say": "Agent teams cost up to five times more. Do they actually win?",
     "kicker": "AI AGENTS", "headline": "Agent teams cost 5x more. Worth it?", "icon": "robot"},
    {"type": "stat", "say": "Teams cost up to five point one times as much as a single agent.",
     "value": "5.1x", "label": "Top cost multiplier for teams"},
    {"type": "statement", "say": "Whether a team pays off depends on the model, the task, and the budget.",
     "text": "It depends on model, task and budget", "emphasis": "depends"},
    {"type": "list", "say": "Three things decide it: the model, the task, and the budget you have.",
     "items": ["The model", "The task", "The budget"]},
    {"type": "outro", "say": "Do you use multiple agents? Tell us if it's worth it.", "text": "Worth the bill?"},
]


def explainer_script(title="Agent teams"):
    return script_from_plan(validate_plan(PLAN), title, "AI", [make_story("Agent teams story")], "caption", ["ai"])


def test_manifest_round_trip_keeps_scripts_plans_stories_and_looks(tmp_path):
    scripts = [make_script("Classic one"), explainer_script()]
    data = pm.new_manifest(scripts, DAY, kind="day", look_overrides={"theme": "dark"})
    pm.write_manifest(tmp_path, data)
    back = pm.read_manifest(tmp_path)
    assert back["date"] == "2026-11-07" and [v["id"] for v in back["videos"]] == ["v01", "v02"]
    assert back["videos"][1]["scene_plan"]["scenes"][0]["type"] == "title"
    assert back["videos"][0]["look"] == {"version": 1, "theme": "dark"}
    assert back["videos"][0]["stories"][0]["summary"]  # full stories, so a rewrite is possible later
    one = pm.script_from_entry(back["videos"][0])
    two = pm.script_from_entry(back["videos"][1])
    assert one.title == "Classic one" and one.beats[0].card == "Card text" and one.look == {
        "style": None, "character": None, "theme": "dark", "background": None, "recipe": None}
    assert two.plan is not None and two.plan.scenes[1].type == "stat" and two.hook.startswith("Agent teams")


def test_update_entry_validates_and_stores_only_set_fields(tmp_path):
    pm.write_manifest(tmp_path, pm.new_manifest([make_script("A")], DAY))
    entry = pm.update_entry(tmp_path, "v01", look={"style": "avatar", "theme": ""})
    assert entry["look"] == {"version": 1, "style": "avatar"}
    with pytest.raises(Exception):
        pm.update_entry(tmp_path, "v01", look={"style": "cartoon"})
    with pytest.raises(pm.ManifestError):
        pm.update_entry(tmp_path, "v99", status="rendered")
    with pytest.raises(pm.ManifestError):
        pm.get_entry(pm.read_manifest(tmp_path), "../x")


def test_corrupt_or_foreign_manifests_read_as_absent(tmp_path):
    assert pm.read_manifest(tmp_path) is None
    (tmp_path / "plan.json").write_text("{half")
    assert pm.read_manifest(tmp_path) is None
    (tmp_path / "plan.json").write_text(json.dumps({"version": 7, "videos": []}))
    assert pm.read_manifest(tmp_path) is None


def test_plan_json_is_never_a_sidecar(tmp_path):
    assert "plan.json" in NON_SIDECAR_JSON
    pm.write_manifest(tmp_path, pm.new_manifest([make_script("A")], DAY))
    assert sidecars(tmp_path) == []
    assert run_pipeline._covered_titles.__doc__  # the title scan uses sidecars' "stories", a plan has none at top level


def test_sidecar_records_the_resolved_look_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "explainer")
    monkeypatch.setenv("VIDEO_THEME", "light")
    script = explainer_script()
    script.look = {"theme": "dark"}
    path = write_metadata(script, tmp_path / "01_x.mp4")
    meta = json.loads(path.read_text())
    assert meta["look"] == {"version": 1, "style": "explainer", "character": "", "theme": "dark",
                            "background": "none", "recipe": "auto"}
    back = pm.script_from_sidecar(meta)
    assert back.plan is not None and back.look["theme"] == "dark" and back.look["style"] == "explainer"


def test_replace_script_keeps_id_and_look(tmp_path):
    pm.write_manifest(tmp_path, pm.new_manifest([make_script("Old")], DAY, look_overrides={"style": "avatar"}))
    fresh = pm.replace_script(tmp_path, "v01", make_script("New"))
    assert fresh["id"] == "v01" and fresh["title"] == "New" and fresh["look"] == {"version": 1, "style": "avatar"}


# --- the no-implicit-full-render rule -------------------------------------------------

def test_render_plan_refuses_without_final(tmp_path):
    pm.write_manifest(tmp_path, pm.new_manifest([make_script("A")], DAY))
    with pytest.raises(render_plan.NotFinal):
        render_plan.render(tmp_path, final=False)
    assert render_plan.main([str(tmp_path)]) == 2  # the CLI too
    assert not list(tmp_path.glob("*.mp4"))


def test_render_plan_builds_selected_videos_with_their_looks_and_updates_the_plan(tmp_path, monkeypatch, isolated_env):
    folder = isolated_env / DAY.isoformat()
    scripts = [make_script("Alpha"), make_script("Beta"), make_script("Gamma")]
    pm.write_manifest(folder, pm.new_manifest(scripts, DAY))
    pm.update_entry(folder, "v02", look={"style": "explainer", "theme": "dark"})
    seen = []

    def fake_build_shorts(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        paths = []
        for i, s in enumerate(scripts, start=1):
            seen.append((s.title, s.look))
            p = out_dir / f"{i:02d}_{s.title.lower()}.mp4"
            p.write_bytes(b"\x00")
            on_rendered(s, p)
            paths.append(p)
        return paths

    monkeypatch.setattr(render_plan, "build_shorts", fake_build_shorts)
    monkeypatch.setattr(render_plan, "validate_config", lambda *a: None)
    monkeypatch.setattr(render_plan, "upload_recent", lambda day: pytest.fail("uploads are off"))
    paths = render_plan.render(folder, only=["v02", "v03"], final=True)
    assert [p.name for p in paths] == ["01_beta.mp4", "02_gamma.mp4"]
    assert seen[0][1]["style"] == "explainer" and seen[0][1]["theme"] == "dark" and seen[1][1]["style"] is None
    data = pm.read_manifest(folder)
    status = {v["id"]: v["status"] for v in data["videos"]}
    assert status == {"v01": "planned", "v02": "rendered", "v03": "rendered"}
    assert json.loads((folder / "01_beta.json").read_text())["look"]["theme"] == "dark"
    # a second --final render only makes what is still missing
    assert render_plan.render(folder, final=True) and [p.name for p in render_plan.render(folder, final=True)] == []


def test_render_plan_main_runs_under_the_pipeline_supervisor(tmp_path, monkeypatch, isolated_env, notifications):
    folder = isolated_env / DAY.isoformat()
    pm.write_manifest(folder, pm.new_manifest([make_script("Alpha")], DAY))
    monkeypatch.setattr(render_plan, "OUTPUT_DIR", isolated_env)
    monkeypatch.setattr(render_plan, "validate_config", lambda *a: None)

    def fake_build_shorts(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        p = out_dir / "01_alpha.mp4"
        p.write_bytes(b"\x00")
        on_rendered(scripts[0], p)
        from newsletter_video.runstate import record_video

        record_video(out_dir, p.stem, "rendered")
        return [p]

    monkeypatch.setattr(render_plan, "build_shorts", fake_build_shorts)
    assert render_plan.main([DAY.isoformat(), "--final"]) == 0
    assert notifications and notifications[-1][0] == "Final render finished"
    assert (folder / "01_alpha.json").exists()


def test_render_plan_skips_stories_covered_since_planning(tmp_path, monkeypatch, isolated_env):
    """The plan may be hours old: a story the 07:00 run rendered meanwhile
    is skipped, so Render all never duplicates the daily job."""
    folder = isolated_env / DAY.isoformat()
    pm.write_manifest(folder, pm.new_manifest([make_script("Alpha"), make_script("Beta")], DAY))
    earlier = isolated_env / "2026-11-06"
    earlier.mkdir()
    write_metadata(make_script("Alpha"), earlier / "01_alpha.mp4")  # the same story, covered yesterday
    monkeypatch.setattr(render_plan, "validate_config", lambda *a: None)
    monkeypatch.setattr(render_plan, "upload_recent", lambda day: None)
    seen = []

    def fake_build_shorts(scripts, out_dir, audio_dir, target_date=None, on_rendered=None):
        for i, s in enumerate(scripts, start=1):
            seen.append(s.title)
            p = out_dir / f"{i:02d}_{s.title.lower()}.mp4"
            p.write_bytes(b"\x00")
            on_rendered(s, p)
        return []

    monkeypatch.setattr(render_plan, "build_shorts", fake_build_shorts)
    render_plan.render(folder, final=True)
    assert seen == ["Beta"]
    status = {v["id"]: (v["status"], v.get("error") or "") for v in pm.read_manifest(folder)["videos"]}
    assert status["v01"][0] == "failed" and "already covered" in status["v01"][1] and status["v02"][0] == "rendered"


def test_previews_are_hidden_once_the_script_or_look_changes(tmp_path):
    folder = tmp_path / "2026-11-07"
    pm.write_manifest(folder, pm.new_manifest([make_script("A")], DAY))
    entry = pm.get_entry(pm.read_manifest(folder), "v01")
    before = pm.entry_fingerprint(entry)
    assert before == pm.entry_fingerprint(pm.get_entry(pm.read_manifest(folder), "v01"))  # stable
    pm.update_entry(folder, "v01", look={"theme": "dark"})
    assert pm.entry_fingerprint(pm.get_entry(pm.read_manifest(folder), "v01")) != before
    pm.preview_dir(folder, "v01").mkdir(parents=True)
    (pm.preview_dir(folder, "v01") / "storyboard.jpg").write_bytes(b"x")
    pm.replace_script(folder, "v01", make_script("B"))
    assert not pm.preview_dir(folder, "v01").exists()  # a rewritten script drops its previews
