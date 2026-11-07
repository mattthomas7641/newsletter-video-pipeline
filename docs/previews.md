# Previews and per-video looks

## Three tiers, one rule

Nothing renders at full quality unless you ask for it by name.

| Tier | What you get | Cost | Measured (8 GB M2, 38 s explainer) |
|---|---|---|---|
| **Stills** (default) | One frame per scene at the moment the scene is fully built, plus the opening frame, combined into a labelled storyboard (`storyboard.jpg`, scene type, time, style knobs) | none | 14 s cold (includes aligning the saved narration once), 6-9 s warm; avatar style 9 s |
| **Draft clip** | The whole video at 12 fps, HyperFrames `--quality draft`, 1080x1920 (`draft.mp4`, ~4-6 MB) | none | 27-30 s cold |
| **Final** | The pipeline's own full-quality, voiced render | voice + ~5 min | as today |

Both tiers work from a saved plan (a `plan.json` entry or a rendered video's
`<video>.json`) with **no writer call and no TTS**. Timing comes from cached word
alignment when the lines have been voiced before (the day's narration in
`output/_audio/<date>/NN.wav`, aligned locally once with mlx-whisper, or an earlier
"Draft with voice"); otherwise from a word-count estimate, and the storyboard and
the panel say "estimated timing". "Draft with voice" is the only preview that calls
the voice (Voicebox or Kokoro), and only when you click it.

```bash
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json                  # stills
./venv/bin/python -m newsletter_video.preview output/2026-11-07 --video v02 --preview draft
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json --style avatar --theme dark
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json --final --out /tmp/x.mp4
```

How stills work: the story payload is built exactly as the builders build it, the
right template is copied to a work folder, the story is baked into the copy's
`data-composition-variables` default (HyperFrames `snapshot` takes no variables), a
silent or cached voice track is written, and `hyperframes snapshot --at t1,t2,...`
captures the frames. Each scene's time is after its entrance and its last reveal cue
(list rows, chart bars, a stat's count-up) and before its exit. Previews are written to
`<folder>/_preview/<video>/` with a `preview.json`; a preview is hidden once the
video's script or look changes (its fingerprint no longer matches).

## Plan first, render last

```bash
./venv/bin/python -m newsletter_video.plan_day                 # output/<today>/plan.json, no render
./venv/bin/python -m newsletter_video.plan_day --rewrite v02   # one video's script again (paid)
./venv/bin/python -m newsletter_video.render_plan 2026-11-07 --final --only v01,v03
```

`render_plan --final` is the only command that turns a plan into videos. It runs under
the daily job's lock, watchdog, `status.json` and notifications, writes each sidecar the
moment a video is done, skips any planned story that a rendered video has covered since
planning, and uploads afterwards exactly as the daily run would (`YOUTUBE_UPLOAD`,
`YOUTUBE_PRIVACY`, `FALLBACK_UPLOAD`). `plan.json` is never a sidecar, so planning never
counts as covering a story and never changes what the unattended 7:00 AM job does.

## Look layering

A look is `{style, character, theme, background, recipe}` (`newsletter_video/look.py`,
versioned, validated: an unknown value is an error, never a silent default).

```
default look   .env: VIDEO_STYLE, CHARACTER, VIDEO_THEME, BACKGROUND_STYLE, STYLE_RECIPE
   < run overrides   what a panel action or a CLI --style/--character/... asked for
   < video look      the `look` stored on a plan entry (only the fields you set)
```

Unset fields inherit from the layer above, so "Default (Light)" in the editor keeps
following your settings if you change them later. Both builders honour the resolved
look through `look.applied()` for the duration of one video; a script without a look
renders with the environment untouched (`tests/test_look.py` proves the daily job is
unchanged). Rendered sidecars record the resolved look under `look`, so a preview or
re-render replays it exactly. The character's writing persona is applied at plan time;
changing the narrator afterwards changes the voice and avatar, not the wording.

`recipe` is the style-library policy: `auto` keeps the recipes the writer's planner chose,
`none` strips them, and a recipe id applies that recipe to the middle scenes with no
model call.

## Not done (honest limits)

- No live scrubbable HyperFrames Studio link: embedding another local server without
  the panel's token and lifecycle was not cheap enough to do safely.
- Approval mode (`DAILY_MODE=plan`, Settings → "What the scheduled run does") is the only
  hand-off from the daily job to this flow: it writes the day's plan and notifies, and the
  Studio page shows "Plan ready — review and render" until everything is rendered.
- A reference upload is analysed in place (not atomically), has no duration cap before
  the paid vision call, and the inbox copy is kept; `plan.json` edits take a lock but
  concurrent edits from two panels are not otherwise coordinated.
