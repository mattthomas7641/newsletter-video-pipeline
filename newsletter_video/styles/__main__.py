"""CLI for the style library.

    python -m newsletter_video.styles add <video> --name N [--notes "..."] [--tags a,b] [--no-recreate] [--no-vision]
    python -m newsletter_video.styles analyze <video> --out DIR [--no-vision]      # analysis only, outside the library
    python -m newsletter_video.styles list
    python -m newsletter_video.styles show <name>
    python -m newsletter_video.styles search "kinetic type with wipe transitions"
    python -m newsletter_video.styles recreate <name> [--rounds N]
    python -m newsletter_video.styles remove <name>
    python -m newsletter_video.styles plan --story <sidecar.json | plan.json> [--out style_plan.json] [--no-model]
    python -m newsletter_video.styles recipes [--query "..."]
    python -m newsletter_video.styles catalog "query words"                       # the HyperFrames registry snapshot
    python -m newsletter_video.styles eval [--out DIR] [--rounds N]              # ground-truth round trip
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .library import PROJECT_ROOT, add_reference, library_root, open_library, slugify


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def _lib_or_exit(create: bool = False):
    lib = open_library(create=create)
    if lib is None:
        sys.exit("STYLE_LIBRARY is switched off in the environment")
    return lib


def cmd_add(args) -> None:
    lib = _lib_or_exit(create=True)
    tags = [t for t in (args.tags or "").split(",") if t.strip()]
    entry = add_reference(Path(args.video), args.name, notes=args.notes or "", tags=tags, recreate=not args.no_recreate,
                          use_vision=False if args.no_vision else None, rounds=args.rounds, lib=lib)
    print(f"Added {entry.name}: {entry.scene_count} scenes, {entry.duration:.1f} s, vision {'yes' if entry.vision_used else 'no'}, "
          f"fidelity {entry.fidelity if entry.fidelity is not None else 'n/a'}, est. ${entry.cost_usd:.3f}")
    print(f"  {lib.entry_dir(entry.name) / 'README.md'}")


def cmd_analyze(args) -> None:
    from .analysis import analyze_video

    a = analyze_video(Path(args.video), Path(args.out), use_vision=False if args.no_vision else None)
    print(json.dumps({k: a[k] for k in ("summary", "tags", "cadence", "palette", "cost_usd", "seconds")}, indent=1))
    print(f"Wrote {Path(args.out) / 'analysis.json'}")


def cmd_list(args) -> None:
    lib = _lib_or_exit()
    entries = lib.entries()
    if not entries:
        print(f"No references in {lib.root} (drop videos in {lib.root / 'inbox'} and run `add`)")
        return
    for e in entries:
        fid = f"{e.fidelity:.2f}" if e.fidelity is not None else "n/a"
        print(f"{e.name:28s} {e.duration:6.1f}s {e.scene_count:3d} scenes  fidelity {fid}  {', '.join((e.user_tags + e.tags)[:8])}")


def cmd_show(args) -> None:
    lib = _lib_or_exit()
    e = lib.get(args.name)
    if e is None:
        sys.exit(f"No reference named {args.name!r}")
    print(json.dumps(e.to_dict(), indent=1, ensure_ascii=False))
    readme = lib.entry_dir(e.name) / "README.md"
    if readme.exists():
        print()
        print(readme.read_text())


def cmd_search(args) -> None:
    lib = _lib_or_exit()
    hits = lib.search(args.query, limit=args.limit)
    if not hits:
        print("No matches")
        return
    for score, e, recipes in hits:
        print(f"{score:5.1f}  {e.name}: {e.summary[:110]}")
        for r in recipes[:4]:
            print(f"        recipe {r.id}: {r.description[:90]}")


def cmd_recreate(args) -> None:
    from .recreate import recreate_reference

    lib = _lib_or_exit()
    e = lib.get(args.name)
    analysis = lib.analysis(args.name)
    if e is None or analysis is None:
        sys.exit(f"No analysed reference named {args.name!r}")
    result = recreate_reference(lib, e.name, analysis, rounds=args.rounds)
    analysis["recreation"] = result
    (lib.entry_dir(e.name) / "analysis.json").write_text(json.dumps(analysis, indent=1, ensure_ascii=False))
    from .recipes import recipes_from_analysis

    recipes = recipes_from_analysis(e.name, analysis)
    lib.write_recipes(e.name, recipes)
    e.fidelity = result.get("score")
    e.recipes = [r.id for r in recipes]
    lib.write_readme(e, analysis, recipes, result)
    lib.upsert(e)
    print(f"Recreated {e.name}: fidelity {result['score']:.3f} after {result['rounds']} round(s); "
          f"{lib.entry_dir(e.name) / result['side_by_side']}")


def cmd_remove(args) -> None:
    lib = _lib_or_exit()
    print("Removed" if lib.remove(args.name) else f"No reference named {args.name!r}")


def _scenes_from_story(path: Path) -> tuple[list[dict], str]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and data.get("scene_plan"):
        return data["scene_plan"]["scenes"], str(data.get("title", ""))
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        return data["scenes"], str(data.get("title", ""))
    if isinstance(data, list):
        return data, ""
    if isinstance(data, dict) and data.get("script"):
        from ..scene_plan import plan_from_script

        return plan_from_script(data["script"]).to_list(), str(data.get("title", ""))
    sys.exit(f"{path}: not a sidecar with a scene_plan, a plan JSON ({{'scenes': [...]}}) or a scene list")


def cmd_plan(args) -> None:
    from ..scene_plan import ScenePlanError, validate_plan
    from .planner import plan_styles

    lib = _lib_or_exit()
    if lib.is_empty():
        sys.exit("The style library is empty; add a reference first")
    scenes, title = _scenes_from_story(Path(args.story))
    try:
        plan = validate_plan(scenes)
    except ScenePlanError as exc:
        sys.exit("The scene plan is invalid:\n  " + "\n  ".join(exc.errors))
    style_plan = plan_styles(plan.to_list(), lib, title=title, use_model=False if args.no_model else None)
    if style_plan is None:
        sys.exit("No style plan (the planner call failed or the answer was rejected); the default look would be used")
    out = style_plan.to_dict()
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out} ({style_plan.styled_count} of {len(plan.scenes)} scenes styled)")
    else:
        print(text)
    for i, (s, r) in enumerate(zip(style_plan.scenes, style_plan.reasons)):
        if s:
            print(f"  scene {i + 1} ({plan.scenes[i].type}): {s.get('recipe') or 'overrides'} - {r}")
    for note in style_plan.repairs:
        print(f"  note: {note}")


def cmd_recipes(args) -> None:
    lib = _lib_or_exit()
    recipes = lib.all_recipes()
    q = (args.query or "").lower().split()
    for r in sorted(recipes.values(), key=lambda r: (r.source != "builtin", r.id)):
        if q and not all(t in r.search_text() for t in q):
            continue
        fid = f" fidelity {r.fidelity:.2f}" if r.fidelity is not None else ""
        print(f"{r.id:34s} [{r.source}{fid}] {', '.join(r.scene_types)}: {r.description[:100]}")


def cmd_catalog(args) -> None:
    data = json.loads((Path(__file__).parent / "data" / "hyperframes_catalog.json").read_text())
    q = args.query.lower().split()
    hits = []
    for item in data["items"]:
        text = " ".join([item["name"], item.get("description", ""), " ".join(item.get("tags", []))]).lower()
        score = sum(1 for t in q if t in text)
        if score:
            hits.append((score, item))
    hits.sort(key=lambda h: -h[0])
    print(f"registry snapshot v{data['version']}: {len(hits)} of {len(data['items'])} items match")
    for score, item in hits[: args.limit]:
        print(f"{item['name']:30s} {item['type']:9s} {item.get('description', '')[:100]}  [{', '.join(item.get('tags', [])[:6])}]")


def cmd_eval(args) -> None:
    from .evaluate import run_eval

    report = run_eval(Path(args.out) if args.out else None, rounds=args.rounds, use_vision=False if args.no_vision else None,
                      samples=[Path(s) for s in (args.sample or [])])
    print(json.dumps(report["summary"], indent=1))
    print(f"Wrote {report['report_path']}")


def main(argv: list[str] | None = None) -> None:
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.styles", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="analyse and recreate a reference video into the library")
    p.add_argument("video", help="a video file you have the right to study")
    p.add_argument("--name", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--tags", default="", help="comma-separated")
    p.add_argument("--no-recreate", action="store_true")
    p.add_argument("--no-vision", action="store_true", help="skip the Claude vision pass (free, less descriptive)")
    p.add_argument("--rounds", type=int, default=None, help="refinement renders (default STYLE_RECREATE_ROUNDS or 2, max 3)")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("analyze", help="analyse a video into a folder without adding it to the library")
    p.add_argument("video")
    p.add_argument("--out", required=True)
    p.add_argument("--no-vision", action="store_true")
    p.set_defaults(func=cmd_analyze)
    sub.add_parser("list", help="list the references").set_defaults(func=cmd_list)
    p = sub.add_parser("show", help="show one reference's index entry and README")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)
    p = sub.add_parser("search", help="search by effect, tag or feel")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("recreate", help="re-run the recreation of a reference")
    p.add_argument("name")
    p.add_argument("--rounds", type=int, default=None)
    p.set_defaults(func=cmd_recreate)
    p = sub.add_parser("remove", help="remove a reference and its folder")
    p.add_argument("name")
    p.set_defaults(func=cmd_remove)
    p = sub.add_parser("plan", help="choose styles for a story's scene plan")
    p.add_argument("--story", required=True, help="a video sidecar .json, a plan JSON or a scene list")
    p.add_argument("--out", help="write the style plan here (feed it to explainer_builder --style-plan)")
    p.add_argument("--no-model", action="store_true", help="use the deterministic planner (no Claude call)")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("recipes", help="list recipes (built-in and from the library)")
    p.add_argument("--query", default="")
    p.set_defaults(func=cmd_recipes)
    p = sub.add_parser("catalog", help="search the HyperFrames registry snapshot")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_catalog)
    p = sub.add_parser("eval", help="render synthetic references with known effects and measure the round trip")
    p.add_argument("--out", default=None)
    p.add_argument("--rounds", type=int, default=0)
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--sample", action="append", help="also run analyse -> recreate -> compare on this real video (repeatable)")
    p.set_defaults(func=cmd_eval)
    args = parser.parse_args(argv)
    if args.cmd in ("add",) and args.name:
        slugify(args.name)  # fail early on a bad name
    args.func(args)


if __name__ == "__main__":
    main()
