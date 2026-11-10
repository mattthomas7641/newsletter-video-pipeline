# Style library

Teach the explainer look by example: drop in videos whose motion you like, let
the system measure and recreate their effects, keep them as a searchable
library of recipes, and have the writer's planner draw on that library when it
plans each new video.

```
style_library/inbox/clip.mp4 ──▶ analyze ──▶ recreate ──▶ recipes ──▶ library.json
                                 scenes,     placeholder  knobs +      searchable
                                 motion,     render with  metadata     index
                                 palette,    our template,
                                 audio,      fidelity
                                 vision      score
                                                  │
   today's scene plan ──▶ planner (retrieval + 1 Claude call) ──▶ style plan ──▶ explainer render
```

The analysis side borrows ideas from [rea-video](https://github.com/JuneYaooo/rea-video)
(Apache-2.0): shot splitting from per-frame colour and pixel evidence with
adaptive thresholds, motion events described by onset maps (easing shape,
sweep direction, stagger), tempo from an onset autocorrelation, and the
"agent looks at the frames and fills in what pixels can't tell" pattern.
Those primitives were rewritten against numpy (rea-video needs OpenCV) and
live in `third_party/rea_video/` with the upstream licence and a list of what
changed. Everything else is new and specific to motion graphics: rea-video
asks "filmed, AI or coded?"; we ask "which layout, type, palette, entrance,
transition, hold and pacing, and which of our recipes reproduces it?"

## Adding a reference

```bash
cp ~/Downloads/clip.mp4 style_library/inbox/
./venv/bin/python -m newsletter_video.styles add style_library/inbox/clip.mp4 \
    --name punchy-type --notes "big words, hard cuts, one accent" --tags favourite,hook
```

What happens, in order:

1. **Probe and segment.** Frames are decoded at 12 fps, 96 px wide. Scenes are
   split at hard cuts (rea-video's rule: a colour/pixel jump well above the
   local baseline) and, because motion graphics change their ink rather than
   their ground, wherever the non-chrome ink layout is replaced across a 0.8 s
   window. Each boundary is named: `cut`, `fade`, `dissolve`, `push-up/down/
   left/right` (the picture translates), `wipe-up/down/left/right` (a change
   front sweeps across a still picture).
2. **Motion per scene.** On a 160 px decode: the activity curve, bursts of
   change as element entrances (easing shape from where the change happens
   in the burst, sweep direction, slide direction, appear/disappear/change),
   stagger interval when three or more arrive on a steady beat, the share of
   the frame that keeps moving, global drift by phase correlation, and loops
   by autocorrelation.
3. **Still evidence.** Palette (dominant colours, ground, accent, ink,
   contrast, light/dark), and the layout of each scene's middle keyframe as
   a 16x16 occupancy grid with alignment, thirds and a text-likeness guess.
   Keyframes (first/mid/last) and a contact sheet with the motion curve go
   to `keyframes/` and `contact_sheet.jpg`.
4. **Audio.** Speech presence (syllabic modulation of the voice band), music
   and tempo (onset autocorrelation, beat grid), and whether cuts and element
   entrances lock to beats or voice onsets.
5. **Vision (with `ANTHROPIC_API_KEY`, unless `STYLE_VISION=off`).** Claude sees
   two downscaled frames per scene plus the numbers above and maps each scene
   onto our vocabulary (`newsletter_video/styles/vocabulary.py`): scene type,
   layout, font class / weight / case / scale, background, entrance,
   transition, hold, type treatment, ease, icon style, captions, effect tags,
   registry search keywords and a one-line GSAP note; then one call over the
   contact sheet summarises the whole video for the index. Everything is
   technique, never content: the prompt forbids transcribing text or brands.
6. **Recreate.** A placeholder scene plan (generic text and numbers, our own
   pictograms) is built with the reference's scene timing and each scene's
   knobs derived from the evidence, rendered with the explainer composition
   (a silent voice track of the right length) and compared with the
   reference: cut-time F1 (0.35 s tolerance), motion-curve correlation,
   transition agreement, palette distance in Lab, layout similarity of
   matched keyframes, combined into a fidelity score. For up to
   `STYLE_RECREATE_ROUNDS` more renders the model sees
   `recreation/side_by_side.jpg` with the numbers and adjusts the knobs; the
   best round is kept.
7. **Recipes and index.** `recipes.json` gets one whole-reference recipe
   (`<name>-look`: the dominant knobs, for "make it look like this") and one
   per distinctive scene, each with what it demonstrates, when to use it,
   which scene types it suits, related registry items and the measured
   fidelity. `README.md` summarises the entry; `library.json` indexes it.

Folder per reference:

```
style_library/<name>/
  reference.mp4         the clip (gitignored)
  meta.json             your name, notes, tags
  analysis.json         everything measured and seen
  keyframes/, contact_sheet.jpg
  recipes.json
  README.md
  recreation/           plan.json, story.json, compare.json, rounds.json,
                        side_by_side.jpg, recreation.mp4 (gitignored)
```

Other commands:

```bash
python -m newsletter_video.styles list
python -m newsletter_video.styles show punchy-type
python -m newsletter_video.styles search "kinetic type with wipe transitions"
python -m newsletter_video.styles recreate punchy-type --rounds 3
python -m newsletter_video.styles remove punchy-type
python -m newsletter_video.styles recipes --query wipe
python -m newsletter_video.styles catalog "mask reveal"      # HyperFrames registry snapshot (448 items)
python -m newsletter_video.styles analyze clip.mp4 --out /tmp/a --no-vision   # look without adding
```

## What a recipe can change

A scene style is a small set of knobs the composition knows how to render;
every value is a closed set and `default` means "exactly what the template
does today":

| knob | values |
|---|---|
| `transition` | default, cut, push-up, push-left, wipe-down, wipe-left, zoom-through, mask-circle, fade, flash |
| `entrance` | default, word-wave, line-slide, typewriter, scale-pop, fade-up, mask-reveal, letter-stagger, blur-in |
| `hold` | default, slow-push, parallax, float, pulse, none |
| `type_treatment` | default, caps, oversized, highlight-band, outline, sans-heavy |
| `background` | default, solid, accent-field, dark-panel, gradient-wash |
| `ease` | default, out, inOut, spring, linear |
| `accent` | `#rrggbb` for the scene |
| `speed` | 0.5-2.0 multiplier on entrance and transition durations |
| `stagger` | 0.02-0.4 s between words in a wave |

Styles are validated twice (`styles/style_plan.py`, and again inside
`scene_plan.validate_plan`): an unknown value, key or out-of-range number
drops that scene's style with a logged note and the scene renders in the
default look. Nothing invalid ever reaches the composition. Reveal timing
still follows the narration: a recipe changes how things move, not when.

Besides the knobs, a recipe records `demonstrates` (vocabulary tags),
`when_to_use`, `scene_types`, `registry_refs` (HyperFrames registry items with
the same idea; `styles catalog` searches the snapshot) and a `gsap` note, so
the planner and you can reason about it without re-watching the reference.
Built-in presets (`styles/recipes.py`) cover the knobs the library may not
have examples of yet.

## Planning a video

With the library on and non-empty, the explainer writer makes one extra call
per video after the scene plan is validated:

1. Retrieval narrows the candidates to about 24 recipes: whole-reference looks
   by measured fidelity, search hits from the story's words, per-scene
   recipes that suit the plan's scene types, and the built-in presets.
2. One structured-output call (`STYLE_PLANNER_MODEL`, effort low) picks a
   recipe and overrides per scene with a one-line reason, under rules that
   keep a video coherent (mostly one reference's look, at most three recipes,
   no two colour hits in a row, a clean first second, a calm outro).
3. `validate_style_plan` checks every scene; bad entries fall back to the
   default look individually, a malformed answer falls back entirely.

The same thing by hand:

```bash
python -m newsletter_video.styles plan --story output/2026-10-13/07_x.json --out /tmp/style.json
./venv/bin/python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/07.mp4 --style-plan /tmp/style.json
./venv/bin/python -m newsletter_video.explainer_builder output/2026-10-13/07_x.json --out /tmp/07.mp4 --style   # planner inline
python -m newsletter_video.styles plan --story plan.json --no-model      # deterministic: the best-measured look
```

Without a key the deterministic planner applies the best-measured
reference's overall look to the middle scenes and keeps title and outro on
the template's timing.

## Cost

- **Per reference** (defaults: Sonnet 5.5, effort low, 2 frames per scene at
  512 px, 4 scenes per call, at most 24 scenes): roughly 6 scene calls at
  ~5k input / ~1.2k output tokens plus one overview call, about **$0.10-0.20**,
  plus ~$0.02 per refinement round (2 by default). `STYLE_VISION=off` makes
  it free (measurements only; recipes then come from the numbers alone).
  Measured in `analysis.json` (`cost_usd`) and the index.
- **Per planned video**: one call, ~3-4k input / ~0.8k output tokens, about
  **$0.02**.
- Time: a few seconds of analysis per minute of video; each render of a
  recreation takes about twice the clip's length on an M2.

## Evaluation

```bash
STYLE_VISION=off ./venv/bin/python -m newsletter_video.styles eval --out output/_style_eval --rounds 0
```

renders three synthetic references with known effects (scene times, transition
per scene, theme, accent, entrances) using our own composition, then runs
them through analyse -> recreate -> compare as if they were strangers' clips,
and writes `report.md` / `report.json`: cut F1, transition accuracy, theme
and accent recovery, and the recreation's fidelity. The numbers from the
first run are in the pull request that added this feature; rerun after
changing the analyzer.

## Limits (honest ones)

- The recreation uses *our* composition and placeholder content. It is a test
  of whether the reference's timing, transitions, palette and motion feel can
  be expressed with our knobs, not a reproduction of the reference.
- Layout similarity compares normalised occupancy grids, so a landscape
  reference against our vertical frame only says "content sits in similar
  places", not more.
- Transition naming is from 96 px thumbnails: pushes versus dissolves blur
  into each other when a push also fades; zooms and circle masks have no
  detector and come out as dissolve or wipe (the vision pass can still name
  them).
- Entrances inside a scene are measured as bursts; typewriter versus
  word-wave is told apart mainly by the model, not the numbers.
- Reveal timing always follows the narration: a reference's beat-synced cuts
  become a tag and a pacing note, not a change to how scenes are timed.
- No downloader: you supply the files.

## Legal note

Study technique on material you have the right to use (your own videos,
licensed stock, references shared with permission). The library stores a copy
of the clip locally and never commits it; the analysis records measurements
and descriptions of technique, and the recreation uses placeholder text, our
own pictograms and no audio. Do not reproduce another creator's branding,
footage, music, voice or likeness, and do not treat a recreation as a licence
to copy a specific video.
