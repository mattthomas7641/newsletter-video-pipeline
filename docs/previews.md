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
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json --preset cinematic-glass   # a look preset
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

## Open in HyperFrames Studio (live, scrubbable)

Stills and draft clips are snapshots; **Open in Studio** on a planned video serves the
live composition with HyperFrames Studio so you can scrub the timeline, inspect scenes
and try tweaks without rendering anything.

```bash
./venv/bin/python -m newsletter_video.preview output/2026-11-07 --video v02 --studio --port 3102
```

What happens: the same work copy the stills use is prepared under
`<folder>/_preview/<video>/_studio/` (template copy, the video's resolved look, cached
or estimated timing, silent or cached voice track, the story baked into the
composition's variable default), then `npx hyperframes@0.8.141 preview <work> --port N
--foreground --no-open --force-new` serves it on `127.0.0.1` (the exact command
`hyperframes preview --background` spawns, kept attached so the panel owns the process).
Measured: the server answers in about 2 s once the npx package is cached.
No writer call, no TTS, no `render`, no upload, nothing written outside `_preview/`.

From the panel, `POST /api/plans/<ref>/videos/<vid>/studio` (token-guarded, no
confirmation: it is free and local) picks a free port, starts the server as a `studio`
job, waits up to ~25 s for it to answer and returns `{job, url, ready}`; the button
opens the URL in a new tab, and the video shows "Studio open" with a Stop button while
it runs. Rules:

- at most one Studio server at a time: starting it for another video stops the previous one;
- it stops with the panel (`python -m newsletter_video.ui` cancels it on exit) or via
  `POST /api/jobs/<id>/cancel`;
- it is not a "heavy" run: storyboards and drafts can be made while it is open, but it
  does not start while a final render or the daily job is using the machine;
- Studio edits live in the work copy only. Change the look or the script through the
  panel to affect the real plan; re-open Studio to see the result.

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
counts as covering a story and never changes what the unattended daily job does.

## Look layering

A look is `{style, character, theme, background, recipe, preset, bg, accent}` (`newsletter_video/look.py`,
versioned, validated: an unknown value is an error, never a silent default).

```
default look   .env: VIDEO_STYLE, CHARACTER, VIDEO_THEME, BACKGROUND_STYLE, LOOK_PRESET, LOOK_BG, LOOK_ACCENT (STYLE_RECIPE for the daily job)
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

`recipe` is the style-library policy (the daily job's `STYLE_RECIPE`; the panel shows its effect but no longer edits it): `auto` keeps the recipes the writer's planner chose,
`none` strips them, and a recipe id applies that recipe to the middle scenes with no
model call.

`bg` and `accent` are the two colours of the look (`#rrggbb`, blank = the look's own): the ground, and the colour of the animations (paper: strokes, highlights, bars; glass: the glow palette is derived from it). `colors.fit_colors` keeps text readable: paper needs a light ground, glass a dark one, and an accent must keep 3:1 contrast against its ground; an adjusted colour is reported (`color_notes`) and the panel says so. A preset change starts from the new preset's own colours. They travel as `--bg` / `--accent`, `LOOK_BG` / `LOOK_ACCENT`, and reach the composition as `story.colors` (and `story.palette` for glass).

`LOOK_ACCENT_ROTATE` (comma-separated colours, at most 8, blank = off) makes a day's videos alternate their accent: `resolve_look(..., index=i)` gives a Dark glass explainer video that no layer gave an accent `palette[i % n]` (`i` = its place in the plan, or in the unattended run's loop via `look.with_rotation`), so consecutive videos differ, a re-render keeps its colour (the sidecar records the resolved accent), and a per-video accent wins. A tile's editor starts from the colour its position gives it (`default_look` per video).

`preset` names a look preset (`newsletter_video/looks/presets.json`, `--preset <id>` on
the CLIs, `LOOK_PRESET` in `.env`): it resolves into a theme and a `look-<id>` recipe
wherever the look did not set those by hand. The `cinematic` theme and the rich scene
types it unlocks are described in `docs/cinematic-look.md`.

## The Videos page and the Design lab

`#/` (Videos, `views/videos.js` + `views/plan.js` + `stylepicker.js`) is the daily routine; `#/lab`
(Design lab, `views/workbench.js`) is for new designs. Both sit on the API described above; the old
`#/workbench` and `#/styles` routes redirect to the lab.

- **Default style.** `PUT /api/settings` with `VIDEO_STYLE`, `LOOK_PRESET`, `LOOK_BG`, `LOOK_ACCENT`,
  `CHARACTER`; the scheduled run reads them from `.env` like any look default. `GET /api/looks` lists the
  two presets with their default colours and swatches (`presets.json` `colors`).
- **Trial previews.** *Preview this video* is `POST .../preview {tier: draft, trial: true, overrides}`:
  the CLI's `--trial` previews the given look INSTEAD of the saved one without saving it, and writes
  `trial_look` into `preview.json`; the fingerprint is that of the video with that look saved, so the
  draft is shown (flagged `trial`) and stays valid after *Use this style for this video* saves exactly
  that look. Before a preview starts, the current fresh storyboard and draft are kept under
  `_preview/<vid>/looks/<look-key>/` (newest eight, `looks.json`, served at
  `/media/plan/<ref>/<vid>/looks/<key>/draft.mp4`), which is how the tile shows the saved style's draft
  beside the trial.
- **Design lab.** `GET /api/looks` library, a test plan, storyboards per look kept the same way, a
  side-by-side compare, *Try another look* re-capturing stills. Never part of the Videos flow.

The default look preset (Settings, `LOOK_PRESET`) is validated against the library file and is
what a planned video uses until it picks its own. 

The rail at the top is computed from the same state (a look picked, a test plan loaded,
a storyboard present, a running job) and names the next free or paid step. Costs shown
on buttons are the fixed estimates from this document; the session ledger counts writer
calls, previews and renders started from this browser session.

## Limits

- The Studio tab is HyperFrames' own server on a second local port, outside the panel's
  token guard (it binds 127.0.0.1 only), and its edits are not written back to the plan.
- Approval mode (`DAILY_MODE=plan`, Settings → "What the scheduled run does") is the hand-off
  from the daily job to this flow: it writes the day's plan and notifies, and the Today's
  videos page shows "Plan ready: review and render" until everything is rendered. There is
  no automatic render if you never review.
- The "previous look" and "earlier looks" on the workbench are kept in the browser session
  only; a reload after the browser is closed starts that comparison afresh.
