"""Ground-truth round-trip evaluation of the style library.

Three small synthetic references are rendered with our own composition
from scene lists whose effects are known exactly (scene times, transition
per scene, theme, accent, entrances). Each is then treated like a stranger's
video: analysed, recreated with placeholder content, compared. The report
says how well the analyzer recovered the truth (cut times, transition
kinds, theme, accent, scene count) and how close the recreation came
(fidelity score and its parts). Optionally the same analyse -> recreate ->
compare loop runs on real sample videos (no truth, fidelity only).

    python -m newsletter_video.styles eval [--out DIR] [--rounds N] [--no-vision]
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .analysis import analyze_video
from .compare import cut_alignment
from .frames import hex_to_rgb, palette_similarity
from .library import Library
from .recreate import PLACEHOLDER, payload_from_scenes, recreate_reference, render_recreation
from .style_plan import validate_style

log = logging.getLogger("newsletter_video")

SCRATCH_DEFAULT = Path(__file__).resolve().parent.parent.parent / "output" / "_style_eval"

# Each synthetic reference: theme, accent, and (type, start, end, style knobs).
SYNTHETIC: dict[str, dict] = {
    "cuts-and-pushes": {
        "theme": "light", "accent": "",
        "scenes": [
            ("title", 0.0, 2.6, {"transition": "default"}),
            ("stat", 2.6, 5.0, {"transition": "cut"}),
            ("list", 5.0, 8.2, {"transition": "push-up", "entrance": "line-slide"}),
            ("statement", 8.2, 10.4, {"transition": "cut", "type_treatment": "caps"}),
            ("outro", 10.4, 13.0, {"transition": "push-left"}),
        ],
    },
    "wipes-dark": {
        "theme": "dark", "accent": "#ff7a59",
        "scenes": [
            ("title", 0.0, 2.8, {}),
            ("chart", 2.8, 5.8, {"transition": "wipe-down"}),
            ("statement", 5.8, 8.0, {"transition": "wipe-left", "background": "accent-field"}),
            ("timeline", 8.0, 11.0, {"transition": "fade", "entrance": "scale-pop", "ease": "spring"}),
            ("outro", 11.0, 13.5, {"transition": "wipe-down"}),
        ],
    },
    "fast-flash": {
        "theme": "light", "accent": "#0fa36b",
        "scenes": [
            ("title", 0.0, 1.5, {"entrance": "scale-pop", "type_treatment": "caps", "ease": "spring"}),
            ("stat", 1.5, 2.9, {"transition": "flash"}),
            ("statement", 2.9, 4.2, {"transition": "cut", "entrance": "letter-stagger"}),
            ("comparison", 4.2, 5.8, {"transition": "zoom-through"}),
            ("stat", 5.8, 7.1, {"transition": "cut", "hold": "pulse"}),
            ("quote", 7.1, 8.9, {"transition": "mask-circle", "entrance": "blur-in"}),
            ("outro", 8.9, 10.5, {"transition": "cut"}),
        ],
    },
}

# Which analyzer label counts as recovering which authored transition
_TRUTH_BASE = {"cut": {"cut"}, "flash": {"cut"}, "push-up": {"push"}, "push-left": {"push"}, "wipe-down": {"wipe"},
               "wipe-left": {"wipe"}, "fade": {"fade", "dissolve"}, "zoom-through": {"dissolve", "fade", "push"},
               "mask-circle": {"dissolve", "fade", "wipe"}, "default": {"push", "wipe", "dissolve"}}


def synthetic_scenes(spec: dict) -> list[dict]:
    scenes = []
    for t, a, b, knobs in spec["scenes"]:
        knobs = dict(knobs)
        if spec.get("accent"):
            knobs["accent"] = spec["accent"]
        style, errs = validate_style(knobs)
        assert not errs, errs
        s = {"type": t, "start": a, "end": b, **json.loads(json.dumps(PLACEHOLDER[t]))}
        if style:
            s["style"] = style
        scenes.append(s)
    return scenes


def score_analysis(spec: dict, analysis: dict) -> dict:
    truth = spec["scenes"]
    true_cuts = [a for _, a, _, _ in truth[1:]]
    found = analysis["scenes"]
    cuts = cut_alignment(true_cuts, [s["start"] for s in found[1:]])
    # transition kinds at matched boundaries
    pairs = []
    for (_, a, _, knobs) in truth[1:]:
        near = [s for s in found[1:] if abs(s["start"] - a) <= 0.35]
        if near:
            authored = knobs.get("transition", "default")
            got = near[0]["transition_in"].split("-")[0]
            pairs.append({"at": a, "authored": authored, "found": near[0]["transition_in"], "ok": got in _TRUTH_BASE.get(authored, set())})
    trans_ok = sum(1 for p in pairs if p["ok"])
    pal = analysis.get("palette", {})
    theme_ok = pal.get("theme") == spec["theme"]
    accent_ok = None
    if spec.get("accent"):
        accent_ok = bool(pal.get("accent")) and palette_similarity([spec["accent"]], [pal["accent"]]) > 0.8
    return {
        "scene_count": {"truth": len(truth), "found": len(found)},
        "cuts": cuts,
        "transitions": {"checked": len(pairs), "correct": trans_ok, "accuracy": round(trans_ok / len(pairs), 3) if pairs else None, "pairs": pairs},
        "theme": {"truth": spec["theme"], "found": pal.get("theme"), "ok": theme_ok},
        "accent": {"truth": spec.get("accent") or None, "found": pal.get("accent"), "ok": accent_ok},
    }


def run_eval(out_dir: Path | None = None, rounds: int = 0, use_vision: bool | None = False, samples: list[Path] | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else SCRATCH_DEFAULT
    out_dir.mkdir(parents=True, exist_ok=True)
    lib = Library(out_dir / "library")
    lib.root.mkdir(parents=True, exist_ok=True)
    report: dict = {"synthetic": {}, "samples": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    for name, spec in SYNTHETIC.items():
        t0 = time.time()
        d = lib.entry_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        scenes = synthetic_scenes(spec)
        duration = spec["scenes"][-1][2]
        payload = payload_from_scenes(scenes, duration, spec["theme"], kicker="EVAL", captions=False)
        ref = d / "reference.mp4"
        render_recreation(payload, ref, d)
        (d / "truth.json").write_text(json.dumps({"theme": spec["theme"], "accent": spec["accent"], "scenes": scenes}, indent=1))
        analysis = analyze_video(ref, d, name=name, use_vision=use_vision)
        recovered = score_analysis(spec, analysis)
        recreation = recreate_reference(lib, name, analysis, rounds=rounds, call=None if use_vision else _no_call)
        analysis["recreation"] = recreation
        (d / "analysis.json").write_text(json.dumps(analysis, indent=1))
        # did the recreation's knobs match the authored ones?
        knob_hits = knob_total = 0
        for truth_scene, style in zip(scenes[1:], recreation.get("scene_styles", [None])[1:]):
            authored = (truth_scene.get("style") or {}).get("transition", "default")
            if authored == "default":
                continue
            knob_total += 1
            got = (style or {}).get("transition", "default")
            if got == authored or (got.split("-")[0] == authored.split("-")[0]) or (authored in ("fade",) and got in ("fade",)):
                knob_hits += 1
        report["synthetic"][name] = {
            "analyzer": recovered,
            "recreation": {k: recreation.get(k) for k in ("score", "cuts", "motion_correlation", "transitions", "palette_similarity", "layout_similarity", "rounds")},
            "transition_knobs": {"checked": knob_total, "recovered": knob_hits},
            "seconds": round(time.time() - t0, 1),
        }
        log.info("Eval %s: cuts F1 %.2f, transitions %s/%s, theme %s, fidelity %.2f", name, recovered["cuts"]["f1"],
                 recovered["transitions"]["correct"], recovered["transitions"]["checked"], recovered["theme"]["ok"], recreation["score"])
    for sample in samples or []:
        sample = Path(sample)
        if not sample.exists():
            continue
        name = "sample-" + sample.stem[:40].lower().replace("_", "-")
        d = lib.entry_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        analysis = analyze_video(sample, d, name=name, use_vision=use_vision)
        recreation = recreate_reference(lib, name, analysis, rounds=rounds, call=None if use_vision else _no_call)
        report["samples"][sample.name] = {
            "scenes": len(analysis["scenes"]), "transitions": analysis["cadence"].get("transition_mix"),
            "tags": analysis.get("tags"), "fidelity": {k: recreation.get(k) for k in ("score", "cuts", "motion_correlation", "transitions", "palette_similarity", "layout_similarity")},
            "seconds": round(time.time() - t0, 1),
        }
    syn = report["synthetic"].values()
    report["summary"] = {
        "references": len(report["synthetic"]),
        "mean_cut_f1": round(sum(r["analyzer"]["cuts"]["f1"] for r in syn) / max(1, len(syn)), 3),
        "transition_accuracy": round(sum(r["analyzer"]["transitions"]["correct"] for r in syn) / max(1, sum(r["analyzer"]["transitions"]["checked"] for r in syn)), 3),
        "theme_correct": sum(1 for r in syn if r["analyzer"]["theme"]["ok"]),
        "accent_correct": sum(1 for r in syn if r["analyzer"]["accent"]["ok"]),
        "accent_checked": sum(1 for r in syn if r["analyzer"]["accent"]["ok"] is not None),
        "mean_fidelity": round(sum(r["recreation"]["score"] for r in syn) / max(1, len(syn)), 3),
        "transition_knobs_recovered": f"{sum(r['transition_knobs']['recovered'] for r in syn)}/{sum(r['transition_knobs']['checked'] for r in syn)}",
        "samples": {k: v["fidelity"]["score"] for k, v in report["samples"].items()},
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=1))
    (out_dir / "report.md").write_text(_markdown(report))
    report["report_path"] = str(report_path)
    return report


def _no_call(*_args, **_kwargs):
    raise RuntimeError("no model in this run")


def _markdown(report: dict) -> str:
    lines = ["# Style library round-trip eval", "", f"Run: {report['started']}", "", "## Synthetic references (known effects)", "",
             "| reference | scenes truth/found | cut F1 | transitions | theme | accent | fidelity | cuts | motion | palette | layout |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in report["synthetic"].items():
        a, rec = r["analyzer"], r["recreation"]
        lines.append(f"| {name} | {a['scene_count']['truth']}/{a['scene_count']['found']} | {a['cuts']['f1']} | "
                     f"{a['transitions']['correct']}/{a['transitions']['checked']} | {'ok' if a['theme']['ok'] else 'wrong'} | "
                     f"{'ok' if a['accent']['ok'] else ('n/a' if a['accent']['ok'] is None else 'wrong')} | {rec['score']} | "
                     f"{rec['cuts']['f1']} | {rec['motion_correlation']} | {rec['palette_similarity']} | {rec['layout_similarity']} |")
    lines += ["", "### Transition detail", ""]
    for name, r in report["synthetic"].items():
        for p in r["analyzer"]["transitions"]["pairs"]:
            lines.append(f"- {name} @ {p['at']} s: authored `{p['authored']}`, found `{p['found']}` ({'ok' if p['ok'] else 'miss'})")
    if report["samples"]:
        lines += ["", "## Sample videos (no truth; fidelity of the recreation)", "", "| sample | scenes | transitions | fidelity | cuts | motion | palette | layout |", "|---|---|---|---|---|---|---|---|"]
        for name, s in report["samples"].items():
            f = s["fidelity"]
            lines.append(f"| {name} | {s['scenes']} | {s['transitions']} | {f['score']} | {f['cuts']['f1']} | {f['motion_correlation']} | {f['palette_similarity']} | {f['layout_similarity']} |")
    lines += ["", "## Summary", "", "```", json.dumps(report["summary"], indent=1), "```", ""]
    return "\n".join(lines)
