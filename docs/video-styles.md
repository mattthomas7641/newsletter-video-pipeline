[Back to the README](../README.md)

# Video styles

`VIDEO_STYLE` in `.env` picks one of three looks: `explainer`, `avatar`, or `avx` (the avatar explainer: a narrator
on a plain colour background with one purpose-built motion graphic per beat, written by `avx_writer.py` and rendered
by `avx_builder.py`; this is what the cloud job runs). **The default is `explainer`.** Everything
else (fetching, Jev, story exclusion, the cloned voice, word alignment, upload, metadata) is
shared.

- **`avatar`**: the original look: a narrator cutout, gameplay footage,
  highlighted cards and heavy captions. Set `VIDEO_STYLE=avatar` to keep it; it is fully
  supported, just no longer the default.
- **`explainer`** (default): no avatar, no footage. A clean editorial explainer where every scene is
  built around a graphic: a hand-drawn pictogram set, big numbers with a trend chart or
  progress ring, bar / column / line / dots-of-100 charts, comparison discs drawn to scale, a
  timeline with a travelling marker, diagrams (flow, stack, hub, funnel) and device
  wireframes (phone, laptop, terminal, chat), all authored vector, with self-hosted fonts,
  one accent colour, light or dark theme (`VIDEO_THEME`), designed push/wipe transitions and
  a subtle lower-third caption. Shorts/TikTok/Reels safe zones are respected: nothing
  important sits in the top 150 px, the bottom 380 px or the right-hand button column.

In explainer mode Claude writes a **scene plan** instead of a card script: an ordered list of
scenes, each with a scene type, typed on-screen fields under hard length caps and the line
spoken over it (`EXPLAINER_WRITER_SYSTEM` in `explainer_writer.py`; still one call per day).
Every plan is validated in code (`scene_plan.py`) before anything is voiced: caps, shape,
98+ spoken words, at least two graphic scenes, never two text-only scenes in a row. A plan
that fails gets one repair round with the validator's exact errors; if it still fails it is
logged and that video is skipped, so a bad plan never crashes the day's run and is never
rendered broken. Scene timing comes from the real word timestamps: a scene appears when the
voice reaches its line, rows, nodes and bars land as their words are spoken, a stat counts up
when its number is said. The selected character's persona still shapes the tone; its avatar
image is simply not used.

Without an Anthropic key, the rule-based scripts are rendered as a plainer structural plan
(headlines and statements), with a warning in the log. Each video's `.json` sidecar gains a
`scene_plan` key so it can be re-rendered exactly; any older sidecar can be re-rendered in
this style too:

```bash
./venv/bin/python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/07.mp4               # structural plan from the saved script
./venv/bin/python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/07.mp4 --write-plan  # ask Claude for a scene plan (one call)
```

Quality gates for the explainer style:

```bash
./venv/bin/python scripts/motion_audit.py output/2026-10-16/01_x.mp4   # fails on any static stretch > 1.2 s (caption band excluded)
cd newsletter_video/hf_template_explainer && npx hyperframes check         # lint, layout, motion, WCAG contrast
./venv/bin/python -m pytest tests/                                         # validator, timing, audit and pipeline tests
```

`scripts/motion_audit.py` samples a render at 8 fps, masks the lower-third caption band and
reports every stretch where fewer than 0.06 % of pixels change (by more than 20 levels) for longer than 1.2 s, plus a
motion-per-second curve; exit code 1 means the video holds a still frame somewhere. While
iterating on a plan, `--reuse-audio` on the `explainer_builder` CLI caches the voice track and
word alignment per script, so re-renders skip Voicebox.

The composition is `newsletter_video/hf_template_explainer/index.html`; its design system
(tokens, type scale, spacing, motion, safe zones, the scene library) is documented in
`newsletter_video/hf_template_explainer/DESIGN.md`. Validate it after changes with
`npx hyperframes check` in that folder (lint, layout, motion and WCAG contrast), and run the
validator tests with `./venv/bin/python -m pytest tests/` (`pytest` is the only extra dependency).

## Style library: teach the look by example

Drop videos you like into `style_library/inbox/` and add them; the system splits
each into scenes, measures its motion (transitions, entrances, easing, stagger,
holds, pacing), palette, layout and audio, has Claude describe each scene in the
explainer's own vocabulary, recreates it with placeholder content using the
explainer composition, scores the fidelity, and keeps the result as reusable
**recipes**. From then on the writer makes one extra call per video that picks
recipes per scene from the library (the planner), and the composition renders
them through a small set of validated style knobs. Empty library or
`STYLE_LIBRARY=off`: nothing changes.

```bash
./venv/bin/python -m newsletter_video.styles add style_library/inbox/clip.mp4 --name punchy-type --notes "big words, hard cuts"
./venv/bin/python -m newsletter_video.styles search "kinetic type with wipe transitions"
./venv/bin/python -m newsletter_video.styles plan --story output/2026-10-13/07_x.json --out /tmp/style.json
./venv/bin/python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/07.mp4 --style-plan /tmp/style.json
```

Costs about $0.15 per reference and $0.02 per planned video; `docs/style-library.md`
has the design, the eval and the limits. Study only material you have the right
to use; clips never leave your machine and are never committed.
