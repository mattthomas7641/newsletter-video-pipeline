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

`preset` names a look preset (`newsletter_video/looks/presets.json`, `--preset <id>` on
the CLIs, `LOOK_PRESET` in `.env`): it resolves into a theme and a `look-<id>` recipe
wherever the look did not set those by hand. The `cinematic` theme and the rich scene
types it unlocks are described in `docs/cinematic-look.md`.

## The Look workbench: reference, test video, tweak

The panel's second page turns the pieces above into one loop for trying a look
(`newsletter_video/ui/static/js/views/workbench.js`). Everything on it is a thin layer over
the API already described; nothing new runs on the backend except one listing field.

1. **References.** `GET /api/styles` lists each reference with its contact sheet, recipes and
   `frames`: one served keyframe per detected scene (`keyframes/sNN_mid.jpg`, else the first
   keyframe), read from the entry's `analysis.json` and only when the file exists inside the
   keyframes folder. Adding a reference is the same background `style-add` job as before.
2. **Test video.** *Make a test video* is `POST /api/plans {ref: null, overrides}` with the
   chosen recipe (and `style: explainer`) as run overrides. `plan_day` stores run overrides on
   the entry's `look`, so the test video's resolved recipe names the reference it was styled
   by, and the page draws that link from the recipe's `source` rather than remembering it.
3. **Tweak and compare.** The reference's scene keyframes sit beside the storyboard's frames
   (`preview.stills.frames`), row by row. Every control change is `PUT .../look` followed by
   `POST .../preview {tier: stills}`: the fingerprint change hides the old storyboard, so the
   page keeps the last storyboard in `sessionStorage` and shows it dimmed as "previous look"
   until the new capture lands, and lists earlier looks below for comparison. *Try another
   recipe* steps through `GET /api/plans` → `recipes` (library recipes first). Rewrite and
   final render go through the same confirmed endpoints as the daily page.

The rail at the top is computed from the same state (references present, a test plan loaded,
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
