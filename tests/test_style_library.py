"""Style library tests: scene segmentation and motion metrics on synthetic
frames, palette and layout, tempo, fidelity metrics, the style validator,
recipes, the library index and search, and the planner with a fake model.
Everything runs offline; only the two end-to-end tests touch ffmpeg (tiny
generated clips) and they skip when it is not installed."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newsletter_video.scene_plan import validate_plan  # noqa: E402
from newsletter_video.styles import audio as audio_mod  # noqa: E402
from newsletter_video.styles import frames as fr  # noqa: E402
from newsletter_video.styles import library as lib_mod  # noqa: E402
from newsletter_video.styles import planner, recipes, segment, style_plan, vision  # noqa: E402
from newsletter_video.styles.compare import compare_analyses, cut_alignment, motion_correlation  # noqa: E402
from newsletter_video.styles.recreate import payload_from_scenes, placeholder_scenes  # noqa: E402
from newsletter_video.styles.vocabulary import all_tags, expand_query  # noqa: E402
from third_party.rea_video.primitives import easing_shape, sweep_direction  # noqa: E402

FPS = 12.0
H, W = 170, 96


def solid(colour, n):
    f = np.zeros((n, H, W, 3), np.uint8)
    f[:] = colour
    return f


def with_box(frames, y0, y1, x0, x1, colour):
    frames = frames.copy()
    frames[:, y0:y1, x0:x1] = colour
    return frames


def test_hard_cuts_and_fade_are_found_on_synthetic_frames():
    paper = (246, 244, 238)
    a = with_box(solid(paper, 36), 40, 90, 10, 80, (23, 25, 30))          # 3 s: a dark block on paper
    b = with_box(solid(paper, 36), 100, 150, 20, 60, (31, 73, 214))       # 3 s: a different block (hard cut)
    black = solid((0, 0, 0), 4)                                           # a fade through black
    c = with_box(solid((17, 19, 24), 36), 30, 60, 10, 86, (243, 241, 234))  # 3 s dark scene
    frames = np.concatenate([a, b, black, c])
    track = segment.build_track(frames, FPS)
    scenes = segment.detect_scenes(track, duration=len(frames) / FPS)
    starts = [round(s.start, 2) for s in scenes]
    assert len(scenes) == 3, starts
    assert abs(starts[1] - 3.0) <= 0.2
    assert scenes[1].transition_in == "cut"
    assert abs(starts[2] - 6.0) <= 0.5
    assert scenes[2].transition_in in ("fade", "dissolve", "cut")


def test_wipe_transition_is_named_with_its_direction():
    paper = (246, 244, 238)
    a = with_box(solid(paper, 30), 20, 60, 10, 80, (23, 25, 30))
    b = with_box(solid(paper, 30), 100, 150, 10, 80, (23, 25, 30))
    # a top-to-bottom wipe over 6 frames: the new frame replaces the old row by row
    wipe = []
    for k in range(1, 7):
        f = a[0].copy()
        edge = int(H * k / 7)
        f[:edge] = b[0][:edge]
        wipe.append(f)
    frames = np.concatenate([a, np.stack(wipe), b])
    track = segment.build_track(frames, FPS)
    scenes = segment.detect_scenes(track, duration=len(frames) / FPS)
    assert len(scenes) == 2, [s.to_dict() for s in scenes]
    assert scenes[1].transition_in == "wipe-down", scenes[1].to_dict()


def test_entrance_bursts_easing_and_stagger_on_synthetic_frames():
    n = 60
    frames = solid((246, 244, 238), n)
    # three rows appearing 0.5 s apart, each growing left to right over 4 frames (ease-out: most change early)
    for row, t0 in enumerate((6, 12, 18)):
        y0 = 20 + row * 40
        widths = [40, 64, 76, 80]
        for k, w in enumerate(widths):
            frames[t0 + k:, y0:y0 + 24, 8:8 + w] = (23, 25, 30)
    prof = segment.motion_profile(frames, FPS, 0.0)
    assert len(prof["events"]) >= 3
    assert prof["stagger"] is not None and abs(prof["stagger"]["interval"] - 0.5) < 0.12
    kinds = {e["kind"] for e in prof["events"]}
    assert "appear" in kinds
    assert any(e["sweep"] == "right" for e in prof["events"])
    assert prof["continuous_share"] < 0.05


def test_easing_shape_and_sweep_primitives():
    assert easing_shape(np.array([8, 4, 2, 1, 1, 0.5])) == "ease-out"
    assert easing_shape(np.array([0.5, 1, 1, 2, 4, 8])) == "ease-in"
    assert easing_shape(np.array([2, 2, 2, 2, 2, 2])) == "linear"
    assert easing_shape(np.array([1])) == "instant"
    xs = np.arange(20, dtype=float)
    assert sweep_direction(xs, np.zeros(20), xs) == "right"
    assert sweep_direction(xs[::-1], np.zeros(20), xs) == "left"
    assert sweep_direction(np.ones(20), np.zeros(20), xs) is None


def test_palette_layout_and_similarities():
    frames = with_box(solid((246, 244, 238), 6), 30, 80, 10, 50, (31, 73, 214))
    frames = with_box(frames, 100, 140, 10, 80, (23, 25, 30))
    pal = fr.palette(frames)
    assert pal["theme"] == "light"
    assert pal["accent"] is not None and fr.palette_similarity([pal["accent"]], ["#1f49d6"]) > 0.85
    assert pal["ink"] is not None
    lay = fr.layout(frames[0], pal["background"])
    assert lay["align"] == "left"
    assert lay["blobs"] == 2
    assert fr.layout_similarity(lay["grid"], lay["grid"]) == 1.0
    assert fr.palette_similarity(["#000000"], ["#000000"]) == 1.0
    assert fr.palette_similarity(["#000000"], ["#ffffff"]) < 0.2
    dark = fr.palette(solid((17, 19, 24), 3))
    assert dark["theme"] == "dark"


def test_tempo_from_a_click_track_and_cuts_on_beat():
    sr = audio_mod.SR
    seconds = 12.0
    x = np.zeros(int(sr * seconds), np.float32)
    period = 0.5  # 120 BPM
    rng = np.random.default_rng(1)
    for k in range(int(seconds / period)):
        i = int(k * period * sr)
        burst = rng.normal(0, 1, int(0.02 * sr)).astype(np.float32) * np.linspace(1, 0, int(0.02 * sr)).astype(np.float32)
        x[i:i + len(burst)] += burst
    x += rng.normal(0, 0.002, len(x)).astype(np.float32)
    cuts = [2.0, 4.0, 6.0, 8.0, 10.0]
    out = audio_mod.analyze_audio(x, cuts)
    assert out["has_audio"] and out["music"]
    assert any(abs(out["tempo_bpm"] - t) < 4 for t in (120.0, 60.0, 240.0)), out["tempo_bpm"]
    assert out["cuts_on_beat"]["ratio"] >= 0.8
    assert out["sync"] == "beat-synced"
    assert audio_mod.analyze_audio(None, []) == {"has_audio": False}


def test_fidelity_metrics():
    assert cut_alignment([1.0, 2.0, 3.0], [1.1, 2.0, 3.3])["f1"] == 1.0
    partial = cut_alignment([1.0, 2.0, 3.0], [1.0, 5.0])
    assert partial["recall"] == pytest.approx(1 / 3, abs=1e-3) and partial["precision"] == 0.5
    assert cut_alignment([], [])["f1"] == 1.0
    curve = list(np.sin(np.linspace(0, 6, 100)) + 1)
    assert motion_correlation(curve, curve) == 1.0
    assert motion_correlation(curve, list(reversed(curve))) < 0.6

    def fake(scenes, colours):
        return {"scenes": scenes, "activity": curve, "palette": {"colours": [{"hex": c} for c in colours]}, "info": {"duration": 6}}

    grid = [[0.5] * 16 for _ in range(16)]
    a = fake([{"start": 0, "end": 3, "transition_in": "none", "layout": {"grid": grid}},
              {"start": 3, "end": 6, "transition_in": "wipe-down", "layout": {"grid": grid}}], ["#f6f4ee", "#1f49d6"])
    b = fake([{"start": 0, "end": 3.1, "transition_in": "none", "layout": {"grid": grid}},
              {"start": 3.1, "end": 6, "transition_in": "wipe-left", "layout": {"grid": grid}}], ["#f6f4ee", "#1f49d6"])
    c = compare_analyses(a, b)
    assert c["cuts"]["f1"] == 1.0 and c["transitions"]["agreement"] == 1.0 and c["palette_similarity"] == 1.0
    assert c["score"] > 0.95


def test_style_validator_is_strict_and_default_is_inert():
    ok, errs = style_plan.validate_style({"transition": "wipe-down", "entrance": "scale-pop", "speed": 1.3, "accent": "#FF0000", "recipe": "kinetic-slam"})
    assert not errs and ok["accent"] == "#ff0000" and ok["speed"] == 1.3 and ok["hold"] == "default"
    assert not style_plan.is_default(ok)
    assert style_plan.is_default(style_plan.validate_style({"transition": "default", "speed": 1.0})[0])
    for bad in ({"transition": "spin"}, {"speed": 9}, {"accent": "red"}, {"stagger": 2}, {"bogus": 1}, {"recipe": "Not A Slug"}, "wipe"):
        style, errs = style_plan.validate_style(bad)
        assert style is None and errs, bad
    assert style_plan.validate_style({"recipe": "x"}, known_recipes={"y"})[0] is None


def test_style_plan_validation_drops_only_the_bad_scenes():
    recipes_knobs = {"kinetic-slam": recipes.builtin_recipes()["kinetic-slam"].knobs}
    raw = {"global": {"accent": "#123456", "feel": "punchy"}, "scenes": [
        {"index": 0, "recipe": "", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
         "background": "default", "ease": "default", "speed": 1.0, "reason": "title"},
        {"index": 1, "recipe": "kinetic-slam", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
         "background": "default", "ease": "default", "speed": 1.0, "reason": "a stat"},
        {"index": 2, "recipe": "unknown-recipe", "transition": "default", "entrance": "default", "hold": "default",
         "type_treatment": "default", "background": "default", "ease": "default", "speed": 1.0, "reason": "x"},
        {"index": 3, "recipe": "", "transition": "spin", "entrance": "default", "hold": "default", "type_treatment": "default",
         "background": "default", "ease": "default", "speed": 1.0, "reason": "x"},
        {"index": 9, "recipe": "", "transition": "cut", "entrance": "default", "hold": "default", "type_treatment": "default",
         "background": "default", "ease": "default", "speed": 1.0, "reason": "x"},
    ]}
    plan = style_plan.validate_style_plan(raw, 4, recipes_knobs)
    assert plan.scenes[0]["accent"] == "#123456" and style_plan.is_default({**plan.scenes[0], "accent": ""})
    assert plan.scenes[1]["transition"] == "cut" and plan.scenes[1]["type_treatment"] == "caps" and plan.scenes[1]["recipe"] == "kinetic-slam"
    assert plan.scenes[2] is None and plan.scenes[3] is None
    assert len(plan.repairs) == 3 and plan.recipes_used == ["kinetic-slam"]
    with pytest.raises(style_plan.StylePlanError):
        style_plan.validate_style_plan({"nope": 1}, 4, recipes_knobs)


def good_plan():
    return [
        {"type": "title", "say": "Agent teams cost up to five times more. Do they actually win?", "kicker": "AI AGENTS",
         "headline": "Agent teams cost 5x more. Worth it?", "style": {"transition": "cut", "entrance": "scale-pop"}},
        {"type": "stat", "say": "Teams cost between one point eight and five point one times as much, says the report.",
         "value": "5.1x", "label": "Top cost multiplier for teams", "style": {"transition": "spin"}},
        {"type": "chart", "say": "The multiplier depends on the model you pick and how hard the task is, they found.",
         "title": "Cost multiplier by model", "unit": "x", "series": [{"label": "Sol low", "value": 1.8}, {"label": "Opus", "value": 5.1}], "highlight": 1},
        {"type": "statement", "say": "Whether a team pays off depends on the model, the task, and the budget you have.",
         "text": "It depends on model, task and budget", "emphasis": "depends", "style": {"transition": "default"}},
        {"type": "outro", "say": "Do you use multiple agents? Tell us if it is worth the bill.", "text": "Are agent teams worth the bill?"},
    ]


def test_scene_plan_carries_valid_styles_and_drops_invalid_ones():
    plan = validate_plan(good_plan())
    assert plan.scenes[0].fields["style"]["entrance"] == "scale-pop"
    assert "style" not in plan.scenes[1].fields  # invalid: dropped, not fatal
    assert any("style dropped" in r for r in plan.repairs)
    assert "style" not in plan.scenes[3].fields  # all-default: nothing to carry
    assert plan.to_list()[0]["style"]["transition"] == "cut"


def test_story_payload_passes_styles_to_the_composition():
    from newsletter_video.explainer_builder import story_payload

    plan = validate_plan(good_plan())
    per_line = [[(w, 0.3 * k + 10 * i) for k, w in enumerate(s.say.split())] for i, s in enumerate(plan.scenes)]
    payload = story_payload(plan, per_line, 46.0, "TLDR", 1.0)
    assert payload["scenes"][0]["style"]["transition"] == "cut"
    assert "style" not in payload["scenes"][1]


def test_builtin_recipes_are_valid_and_knobs_from_analysis_follow_the_evidence():
    seen = set()
    for r in recipes.BUILTIN_RECIPES:
        assert r.id not in seen
        seen.add(r.id)
        style, errs = style_plan.validate_style(r.knobs)
        assert not errs, (r.id, errs)
        assert all(t in all_tags() for t in r.demonstrates), (r.id, r.demonstrates)
    scene = {"index": 1, "transition_in": "wipe-down", "duration": 3.0,
             "motion": {"events": [{"t0": 1.0, "t1": 1.4, "kind": "appear", "easing": "ease-out", "sweep": "right", "slide": None}],
                        "stagger": {"n": 3, "interval": 0.15}, "drift": {"dx": 0, "dy": 0}, "continuous_share": 0.0, "loop_period": None}}
    k = recipes.knobs_from_scene(scene, None, {"accent": "#ff0000"})
    assert k["transition"] == "wipe-down" and k["entrance"] == "mask-reveal" and k["ease"] == "out"
    assert k["stagger"] == 0.15 and k["accent"] == "#ff0000" and 0.5 <= k["speed"] <= 2.0
    k2 = recipes.knobs_from_scene({**scene, "transition_in": "push-down"}, {"entrance": "typewriter", "hold": "pulse", "transition_in": "default",
                                                                           "type_treatment": "default", "background": "default", "ease": "default", "case": "upper"}, None)
    assert k2["transition"] == "push-up" and k2["entrance"] == "typewriter" and k2["hold"] == "pulse" and k2["type_treatment"] == "caps"


def fake_analysis(name="ref", n=4):
    scenes = []
    for i in range(n):
        scenes.append({"index": i, "start": 3.0 * i, "end": 3.0 * (i + 1), "duration": 3.0,
                       "transition_in": "none" if i == 0 else ("wipe-down" if i % 2 else "push-up"),
                       "motion": {"events": [{"t0": 3.0 * i + 0.4, "t1": 3.0 * i + 0.9, "kind": "appear", "easing": "ease-out", "sweep": None, "slide": "left"}],
                                  "stagger": None, "drift": {"dx": 0, "dy": 0}, "continuous_share": 0.0, "loop_period": None},
                       "palette": {"background": "#f6f4ee", "accent": "#1f49d6", "theme": "light"},
                       "layout": {"grid": [[0.2] * 16 for _ in range(16)], "kind": "stack", "text_like": True, "blobs": 10, "align": "left", "fill": 0.1},
                       "keyframes": [f"keyframes/s{i:02d}_first.jpg", f"keyframes/s{i:02d}_mid.jpg", f"keyframes/s{i:02d}_last.jpg"]})
    return {"version": 1, "name": name, "info": {"duration": 3.0 * n, "width": 1080, "height": 1920, "orientation": "vertical"},
            "scenes": scenes, "activity": [0.01] * 100, "cadence": {"scene_count": n, "pace": "steady", "transition_mix": {"wipe": 2, "push": 1}, "median_scene_s": 3.0},
            "palette": {"colours": [{"hex": "#f6f4ee", "share": 0.9}, {"hex": "#1f49d6", "share": 0.05}], "background": "#f6f4ee", "accent": "#1f49d6",
                        "theme": "light", "colour_feel": "mono-accent", "contrast": 12.0},
            "audio": {"has_audio": True, "speech": True, "music": False, "sync": "free"}, "vision": None,
            "tags": ["kinetic-type", "wipe-down", "light"], "summary": "a test reference"}


def test_recipes_from_analysis_and_placeholder_recreation_plan():
    a = fake_analysis()
    a["recreation"] = {"score": 0.8}
    rs = recipes.recipes_from_analysis("ref", a)
    assert rs[0].id == "ref-look" and rs[0].fidelity == 0.8 and rs[0].knobs["transition"] == "wipe-down"
    assert all(not style_plan.validate_style(r.knobs)[1] for r in rs)
    scenes = placeholder_scenes(a)
    assert [s["type"] for s in scenes] == ["title", "statement", "statement", "outro"]
    assert "style" not in scenes[0] or "transition" not in scenes[0].get("style", {}) or scenes[0]["style"]["transition"] == "default"
    assert scenes[1]["style"]["transition"] == "wipe-down" and scenes[1]["style"]["entrance"] == "line-slide"
    payload = payload_from_scenes(scenes, 12.0, "light", captions=True)
    assert payload["duration"] == 12.0 and payload["scenes"][0]["end"] == 3.0 and payload["captions"]
    # short reference scenes are merged into their neighbour
    a2 = fake_analysis(n=3)
    a2["scenes"][1]["duration"] = 0.3
    assert len(placeholder_scenes(a2)) == 2


def test_library_index_search_and_recipes(tmp_path, monkeypatch):
    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "lib"))
    assert not lib_mod.library_enabled()
    lib = lib_mod.open_library(create=True)
    assert lib is not None and lib.is_empty()
    a = fake_analysis("punchy")
    lib.entry_dir("punchy").mkdir(parents=True)
    (lib.entry_dir("punchy") / "analysis.json").write_text(json.dumps(a))
    rs = recipes.recipes_from_analysis("punchy", a)
    lib.write_recipes("punchy", rs)
    entry = lib_mod.Entry(name="punchy", title="Punchy", tags=a["tags"], user_tags=["favourite"], notes="big type, fast wipes",
                          summary=a["summary"], recipes=[r.id for r in rs], fidelity=0.7, duration=12.0, scene_count=4)
    lib.upsert(entry)
    lib.write_readme(entry, a, rs, {"score": 0.7, "cuts": {"f1": 1}, "motion_correlation": 0.8, "transitions": {"agreement": 1},
                                    "palette_similarity": 0.9, "layout_similarity": 0.6, "rounds": 1})
    assert (lib.entry_dir("punchy") / "README.md").read_text().startswith("# Punchy")
    assert lib_mod.library_enabled()
    assert lib.get("punchy").fidelity == 0.7
    hits = lib.search("kinetic type with wipe transitions")
    assert hits and hits[0][1].name == "punchy" and any(r.id == "punchy-look" for r in hits[0][2])
    assert lib.search("favourite")[0][1].name == "punchy"
    assert lib.search("underwater") == []
    assert "kinetic-type" in expand_query("kinetic typography") and "wipe-down" in expand_query("wipes")
    allr = lib.all_recipes()
    assert "kinetic-slam" in allr and "punchy-look" in allr
    assert lib.remove("punchy") and not lib.remove("punchy") and lib.is_empty()
    monkeypatch.setenv("STYLE_LIBRARY", "off")
    assert lib_mod.open_library() is None and not lib_mod.library_enabled()


def _seed_library(tmp_path, monkeypatch, fidelity=0.75):
    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "lib"))
    lib = lib_mod.open_library(create=True)
    a = fake_analysis("punchy")
    a["recreation"] = {"score": fidelity}
    lib.entry_dir("punchy").mkdir(parents=True)
    (lib.entry_dir("punchy") / "analysis.json").write_text(json.dumps(a))
    rs = recipes.recipes_from_analysis("punchy", a)
    lib.write_recipes("punchy", rs)
    lib.upsert(lib_mod.Entry(name="punchy", title="Punchy", tags=a["tags"], summary=a["summary"], recipes=[r.id for r in rs], fidelity=fidelity))
    return lib


def test_planner_with_a_fake_model_validates_and_applies(tmp_path, monkeypatch):
    lib = _seed_library(tmp_path, monkeypatch)
    plan = validate_plan([{k: v for k, v in s.items() if k != "style"} for s in good_plan()])
    scenes = plan.to_list()
    seen = {}

    def fake_call(system, content, schema):
        seen["system"], seen["content"], seen["schema"] = system, content, schema
        cands = json.loads(content[1]["text"].split("Candidate recipes: ", 1)[1])
        assert any(c["id"] == "punchy-look" for c in cands) and any(c["source"] == "builtin" for c in cands)
        return ({"global": {"accent": "", "feel": "like punchy"}, "scenes": [
            {"index": 0, "recipe": "", "transition": "default", "entrance": "scale-pop", "hold": "default", "type_treatment": "default",
             "background": "default", "ease": "spring", "speed": 1.0, "reason": "hook"},
            {"index": 1, "recipe": "punchy-look", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
             "background": "default", "ease": "default", "speed": 1.2, "reason": "the stat"},
            {"index": 2, "recipe": "kinetic-slam", "transition": "wobble", "entrance": "default", "hold": "default", "type_treatment": "default",
             "background": "default", "ease": "default", "speed": 1.0, "reason": "bad override"},
            {"index": 3, "recipe": "", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
             "background": "default", "ease": "default", "speed": 1.0, "reason": "default"},
            {"index": 4, "recipe": "", "transition": "default", "entrance": "default", "hold": "default", "type_treatment": "default",
             "background": "default", "ease": "default", "speed": 1.0, "reason": "outro"},
        ]}, {"input_tokens": 1000, "output_tokens": 300})

    sp = planner.plan_styles(scenes, lib, call=fake_call, title="Agent teams")
    assert sp is not None and sp.styled_count == 2 and sp.recipes_used == ["punchy-look"]
    assert sp.scenes[1]["transition"] == "wipe-down" and sp.scenes[1]["speed"] == 1.2 and sp.scenes[2] is None
    assert "transition" in seen["system"] and seen["schema"] is style_plan.STYLE_PLAN_SCHEMA
    assert planner.apply_style_plan(plan, sp) == 2
    assert plan.scenes[0].fields["style"]["entrance"] == "scale-pop" and "style" not in plan.scenes[2].fields
    # a broken answer or a failing call means the default look, never an exception
    assert planner.plan_styles(scenes, lib, call=lambda *a: ({"nope": 1}, {}), title="x") is None

    def boom(*a):
        raise RuntimeError("down")

    assert planner.plan_styles(scenes, lib, call=boom, title="x") is None
    # the heuristic planner needs no model
    hp = planner.plan_styles(scenes, lib, use_model=False)
    assert hp is not None and hp.global_notes["planner"] == "heuristic" and hp.styled_count >= 3
    assert hp.scenes[0] is None or style_plan.is_default({**hp.scenes[0], "accent": ""})
    # off or empty library: None
    monkeypatch.setenv("STYLE_LIBRARY", "off")
    assert planner.plan_styles(scenes, None, call=fake_call) is None


def test_style_scene_plan_entry_point_is_inert_without_a_library(tmp_path, monkeypatch):
    from newsletter_video.styles import style_scene_plan

    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "nothing-here"))
    plan = validate_plan([{k: v for k, v in s.items() if k != "style"} for s in good_plan()])
    before = plan.to_list()
    assert style_scene_plan(plan, "x") == 0 and plan.to_list() == before
    _seed_library(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert style_scene_plan(plan, "x") >= 1  # heuristic planner
    assert all(not style_plan.validate_style(s.fields["style"])[1] for s in plan.scenes if "style" in s.fields)


def test_vision_normalisation_and_cost_estimate():
    d = vision._normalize_scene({"index": 0, "what": "x", "scene_type": "stat", "layout": "bogus", "entrance": "scale-pop",
                                 "transition_in": "nope", "effects": ["kinetic-type", "made-up"], "hierarchy_levels": 2})
    assert d["scene_type"] == "stat" and d["layout"] == "centered-stack" and d["transition_in"] == "default"
    assert d["effects"] == ["kinetic-type"] and d["entrance"] == "scale-pop"
    assert vision.estimate_cost("claude-sonnet-5-5", 1_000_000, 100_000) == 3.0
    assert vision.image_tokens(512, 910) == 621
    assert not vision.vision_available()  # no key in tests


def _ffmpeg():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg")


def _tiny_video(path: Path) -> Path:
    """Two colour scenes with a hard cut at 1.5 s plus a tone, 3 s, 48x84."""
    ff = _ffmpeg()
    if not ff:
        pytest.skip("ffmpeg not available")
    cmd = [ff, "-v", "error", "-y",
           "-f", "lavfi", "-i", "color=c=#f6f4ee:s=48x84:r=12:d=1.5",
           "-f", "lavfi", "-i", "color=c=#111318:s=48x84:r=12:d=1.5",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
           "-filter_complex", "[0:v]drawbox=x=8:y=10:w=30:h=20:color=#1f49d6:t=fill[a];[1:v]drawbox=x=8:y=50:w=30:h=20:color=#f3f1ea:t=fill[b];[a][b]concat=n=2:v=1:a=0[v]",
           "-map", "[v]", "-map", "2:a", "-shortest", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_analyze_video_end_to_end_with_a_fake_vision_call(tmp_path):
    from newsletter_video.styles.analysis import analyze_video

    video = _tiny_video(tmp_path / "tiny.mp4")
    calls = []

    def fake_call(system, content, schema):
        calls.append(schema)
        if schema is vision.SCENE_DESCRIPTION_SCHEMA:
            idx = [json.loads(b["text"].split("Evidence: ", 1)[1]) and int(b["text"].split(":")[0].split()[1]) for b in content if b["type"] == "text" and b["text"].startswith("Scene ") and "Evidence" in b["text"]]
            return ({"scenes": [{"index": i, "what": "a block", "scene_type": "statement", "layout": "left-column", "font_class": "sans-grotesk",
                                 "weight": "bold", "case": "upper", "scale": "large", "hierarchy_levels": 1, "background": "solid", "entrance": "scale-pop",
                                 "transition_in": "cut", "hold": "none", "type_treatment": "caps", "ease": "out", "icon_style": "none", "captions": "none",
                                 "effects": ["bold", "flat"], "registry_keywords": ["block reveal"], "gsap": "scale 0.6->1"} for i in idx]},
                    {"input_tokens": 2000, "output_tokens": 400})
        return ({"summary": "two blocks", "feel": ["bold", "flat"], "typography": "t", "colour": "c", "motion": "m", "pacing": "p", "reuse": "r",
                 "tags": ["two-tone"]}, {"input_tokens": 1500, "output_tokens": 200})

    a = analyze_video(video, tmp_path / "an", name="tiny", use_vision=True, call=fake_call)
    assert len(a["scenes"]) == 2 and abs(a["scenes"][1]["start"] - 1.5) < 0.2 and a["scenes"][1]["transition_in"] == "cut"
    assert a["vision"]["calls"] == 2 and a["vision"]["scenes"][0]["type_treatment"] == "caps"
    assert a["cost_usd"] == pytest.approx(vision.estimate_cost("claude-sonnet-5-5", 3500, 600))
    assert "caps" in a["tags"] and "bold" in a["tags"] and "two-tone" in a["tags"]
    assert (tmp_path / "an" / "analysis.json").exists() and (tmp_path / "an" / "contact_sheet.jpg").exists()
    assert len(a["scenes"][0]["keyframes"]) == 3
    assert a["audio"]["has_audio"]
    # the vision call failing leaves the measurements intact
    b = analyze_video(video, tmp_path / "an2", name="tiny", use_vision=True, call=lambda *x: (_ for _ in ()).throw(RuntimeError("down")))
    assert len(b["scenes"]) == 2 and b["vision"]["calls"] == 0 and b["vision"]["errors"]


def test_add_reference_with_injected_render_and_no_model(tmp_path, monkeypatch):
    from newsletter_video.styles.library import add_reference

    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "lib"))
    monkeypatch.setenv("STYLE_RECREATE_ROUNDS", "1")
    video = _tiny_video(tmp_path / "tiny.mp4")
    from newsletter_video.styles import recreate as rec

    renders = []

    def fake_render(payload, out_path, work_root):
        renders.append(payload)
        shutil.copy(video, out_path)  # the "recreation" is the reference itself
        return out_path

    monkeypatch.setattr(rec, "render_recreation", fake_render)
    entry = add_reference(video, "Tiny Clip", notes="two blocks", tags=["test"], use_vision=False)
    lib = lib_mod.open_library()
    assert entry.name == "tiny-clip" and entry.scene_count == 2 and entry.fidelity is not None and entry.fidelity > 0.8
    assert renders and renders[0]["duration"] == pytest.approx(3.0, abs=0.1)
    assert (lib.entry_dir("tiny-clip") / "reference.mp4").exists()
    assert (lib.entry_dir("tiny-clip") / "recreation" / "compare.json").exists()
    assert (lib.entry_dir("tiny-clip") / "recreation" / "side_by_side.jpg").exists()
    assert lib.get("tiny-clip").recipes and lib_mod.library_enabled()
    assert lib.search("test")[0][1].name == "tiny-clip"


def test_add_reference_stages_in_a_partial_folder_and_leaves_nothing_on_failure(tmp_path, monkeypatch):
    from newsletter_video.styles import analysis as an_mod
    from newsletter_video.styles.library import Library, add_reference

    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "lib"))
    video = _tiny_video(tmp_path / "tiny.mp4")
    lib = lib_mod.open_library(create=True)
    (lib.root / "stale.partial").mkdir()  # a cancelled add from earlier
    seen = {}
    real_analyze = an_mod.analyze_video

    def boom(path, out_dir, **kw):
        seen["out_dir"] = Path(out_dir)
        raise RuntimeError("model down")

    monkeypatch.setattr(an_mod, "analyze_video", boom)
    with pytest.raises(RuntimeError):
        add_reference(video, "Tiny Clip", use_vision=False, recreate=False)
    assert seen["out_dir"] == lib.root / "tiny-clip.partial"  # built in the partial folder...
    assert not (lib.root / "tiny-clip").exists() and not (lib.root / "tiny-clip.partial").exists()  # ...and gone on failure
    assert not (lib.root / "stale.partial").exists() and lib.get("tiny-clip") is None
    monkeypatch.setattr(an_mod, "analyze_video", real_analyze)
    # success: the inbox copy is deleted, the folder is final, and no partial remains
    inbox = lib.root / "inbox"
    inbox.mkdir()
    clip = shutil.copy(video, inbox / "tiny-clip-job1.mp4")
    entry = add_reference(Path(clip), "Tiny Clip", use_vision=False, recreate=False)
    assert entry.name == "tiny-clip" and lib.get("tiny-clip") is not None
    assert (lib.root / "tiny-clip" / "reference.mp4").exists() and not list(lib.root.glob("*.partial"))
    assert not Path(clip).exists()
    # an orphan folder the index does not know about can still be removed
    (lib.root / "orphan").mkdir()
    assert Library(lib.root).remove("orphan") is True and not (lib.root / "orphan").exists()
    assert Library(lib.root).remove("ghost") is False


def test_reference_duration_cap_runs_before_any_analysis(tmp_path, monkeypatch):
    from newsletter_video.styles import analysis as an_mod
    from newsletter_video.styles import media
    from newsletter_video.styles.library import ReferenceTooLong, add_reference, check_reference_duration, max_reference_seconds

    monkeypatch.setenv("STYLE_LIBRARY", str(tmp_path / "lib"))
    monkeypatch.delenv("STYLE_MAX_SECONDS", raising=False)
    assert max_reference_seconds() == 120
    monkeypatch.setenv("STYLE_MAX_SECONDS", "nope")
    assert max_reference_seconds() == 120
    monkeypatch.setenv("STYLE_MAX_SECONDS", "30")
    clip = tmp_path / "long.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    monkeypatch.setattr(media, "probe", lambda p: media.VideoInfo(path=p, width=1080, height=1920, fps=30, duration=45.0, has_audio=False))
    with pytest.raises(ReferenceTooLong, match="45 s long.*capped at 30 s"):
        check_reference_duration(clip)
    assert check_reference_duration(clip, 60) == 45.0
    monkeypatch.setenv("STYLE_MAX_SECONDS", "0")  # disabled
    assert check_reference_duration(clip) == 45.0
    monkeypatch.setenv("STYLE_MAX_SECONDS", "30")
    monkeypatch.setattr(an_mod, "analyze_video", lambda *a, **k: pytest.fail("the paid analysis must not run"))
    with pytest.raises(ReferenceTooLong):
        add_reference(clip, "Long One")
    assert not list((tmp_path / "lib").glob("long-one*"))
