# Looks: the cinematic look, rich scenes and the preset library

The explainer style has two look families and a small library of presets
built on them. Nothing here changes the daily video: with no preset, no
recipe and `VIDEO_THEME` unset, the composition renders exactly as before
(`tests/test_cinematic.py::test_default_look_and_payload_are_unchanged`).

## Why

A reference video the owner liked (a dark, 3D-looking motion-graphics
explainer) produced a recipe that only carried motion knobs and an accent, and
the explainer only had flat 2D scenes on a light paper ground, so the "styled"
test video looked exactly like every other one. The fix is a second ground
(`cinematic`) and four richer scene types, selectable by data.

## The cinematic look

`story.theme = "cinematic"` (from `VIDEO_THEME=cinematic`, a look preset, or any
scene style with `look: cinematic`) switches the whole composition:

| Part | What changes |
|---|---|
| Ground | near-black `#050509`, a faint line grid above, a perspective **grid floor** below the stage, the accent's light pooling on it |
| Aurora | three blurred colour fields in screen blend drift through one sine cycle over the video (adapted from the registry's `aurora-drift` component; the pose is a function of a tweened phase) |
| Mesh gradient | scenes whose style background is `mesh-gradient` show the registry's `mesh-gradient` WebGPU shader block (`compositions/mesh-gradient.html`, mounted at 1920x1080 and scaled to cover the frame) under a dark multiply; it is only mounted when a scene asks for it, so the default never loads WebGPU. **Status:** it renders in `snapshot` (stills, `check`) on this Mac, but `hyperframes@0.8.141 render` blocks the clip (`sub_timeline_script_failure`: the block's script throws in the render worker), so no preset sets it by default; it stays available as a knob for when the CLI renders WebGPU blocks |
| Vignette and grain | a radial vignette and film grain adapted from the registry's `grain-overlay` component (a turbulence tile stepping between ten offsets every 0.5 s; the registry loops it forever, here the iteration count is set from the video's duration so it is finite and seekable) |
| Palette | `story.palette` (from the recipe's `palette` knob, up to 4 colours) fills `--glow-1..4`; `--accent` is the first colour lifted to a text-safe luminance (`inkSafe`, 7:1 on the ground) and `--glow-rgb` keeps it raw for halos |
| Type | display/h1/h2 switch to the heavy sans with a soft glow; the spoken caption word wears the registry's `caption-neon-glow` three-layer text-shadow |
| Existing scenes | pictograms, stat rules, lead bars, discs, rails and markers get glow halos; diagram nodes, device frames, tiles and panels become glass (translucent, light edge, backdrop blur) |

Contrast stays WCAG AA (`npx hyperframes check`: 58/58 text checks on the
showcase, 55/55 on the real story in the default look).

## The rich scene types

Four new scene types in `scene_plan.SCENE_TYPES`, validated like every other
(hard caps, bounds, repairs), fed from the story, revealing on the voice's
word cues (`explainer_builder.story_payload`). They render in every theme but
are designed for the cinematic one; the writer only sees them when the
resolved look is cinematic (`look.cinematic_requested`, `explainer_writer.CINEMATIC_NOTE`).

| Type | Fields | Hero | Built with |
|---|---|---|---|
| `object3d` | `kind` stack \| bars \| orbit \| cards; `nodes` (stack 2-5, orbit 3-5 + `center`, cards 2-4) or `series` 2-5 + `unit` + `highlight`; optional `title` | a Three.js canvas (820x560) in the hero: neon-framed layer plates rising bottom to top, 3D columns growing from the floor, satellites orbiting a glowing core, floating glass cards; an HTML legend / value row under it | Three.js 0.181 via the HyperFrames `hf-seek` adapter: every object3d scene renders its own small WebGL canvas from the seek time (no clock, no animation loop); `window.__hf.buildReady` holds the first capture until the scenes exist. At most 2 per video (`MAX_OBJECT3D`). |
| `depth` | `nodes` 2-4 layers (label + text), optional `label`, `title` | layered glass planes in a CSS 3D world; the camera dollies from plane to plane as each is spoken, passed planes fade before the lens, deeper ones peek out dim above | GSAP tweens on `z`, one sequence per property (no overlapping whole-scene tween); cards start turned away and square up as the rig reaches them (adapted from `camera-rig-depth-stack`) |
| `kinetic` | `lines` 1-3 (≤ 24 chars), optional big `value` (≤ 4 chars), `label` | a huge glowing numeral and heavy caps lines landing word by word with scale, the spoken line lit in the accent, an underline that sweeps | GSAP; the lit line uses the `caption-neon-glow` shadow stack |
| `glass` | `nodes` 2-4 rows (label = key, text = value ≤ 22 chars), optional `label`, `title`, `icon` | a HUD panel: header with a blinking status dot, key/value rows arriving on cues, a scan sweep, the accent walking the values | CSS glass (gradient, light edge, backdrop blur) + GSAP |

## Presets

`newsletter_video/looks/presets.json` (loader: `newsletter_video.looks`,
`load_presets()` / `presets_json()` for the panel backend). A preset is `{id,
name, description, look: {theme}, knobs, emphasis, registry, thumbnail}`:

| id | ground | knobs (middle scenes) | emphasis | registry items |
|---|---|---|---|---|
| `paper` (default) | light | none | — | — |
| `cinematic-glass` | cinematic | fade / fade-up / slow-push / inOut, neon palette, speed 0.9 | object3d, glass, depth, kinetic | grain-overlay, aurora-drift (mesh-gradient installed, see status above) |
| `depth-cards` | dark | push-left / line-slide / parallax, accent `#7b96ff`, stagger 0.1 | depth, glass, device, diagram | camera-rig-depth-stack |
| `data-focus` | light | wipe-down / scale-pop / sans-heavy, speed 1.15 | chart, stat, comparison, object3d | animated-bar-chart (growth pattern; the chart scenes are the template's own) |
| `glow-type` | cinematic | cut / letter-stagger / caps / pulse, magenta-cyan palette, speed 1.2 | kinetic, statement, stat, glass | grain-overlay, caption-neon-glow |
| `quiet-editorial` | light | fade / fade-up / float, speed 0.8 | quote, statement, timeline, list | — |

How a preset reaches a video: `look.preset` (a new look field; env
`LOOK_PRESET`, CLI `--preset <id>` / `--look-preset <id>` on `preview`,
`plan_day`, `render_plan`) resolves into `theme` and `recipe = look-<id>`
wherever a layer did not set those by hand (`look.resolve_look`), so a
per-video look can still override any field. Each preset's knobs are a
built-in recipe (`styles.recipes.builtin_recipes` includes
`looks.preset_recipes()`), so the existing recipe policy applies them to the
middle scenes, the planner can pick them, and the panel's recipe picker lists
them. A cinematic recipe makes the whole video cinematic
(`explainer_builder.resolve_theme`), and the writer gets the preset's scene
emphasis plus the cinematic scene notes.

```bash
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json --preset cinematic-glass
./venv/bin/python -m newsletter_video.preview output/2026-11-07/10_x.json --preview draft --preset glow-type
./venv/bin/python -m newsletter_video.plan_day --test --videos 1 --preset cinematic-glass
```

Thumbnails (`newsletter_video/ui/static/looks/<id>.jpg`) and the contact
sheet `docs/screenshots/rich-scenes/presets-contact.jpg` are stills of one
saved real story (`output/2026-11-07/10_tsmc-...`) in every preset, made with
the preview CLI (no writer call, no voice; the day's narration gives real timing).

Registry items are installed with `npx hyperframes add` into
`hf_template_explainer/compositions/` (recorded in `hyperframes.json`); the
block `mesh-gradient` is mounted as a sub-composition, the components are
adapted inline in `index.html` (the composition is one data-driven file by
design, see DESIGN.md "Why one file"). `lib/` at the template root is a
symlink to `compositions/lib` so a runtime-mounted block resolves its shader
library.

## Measured (8 GB M2)

| What | Time |
|---|---|
| Stills, real story, default look (7 frames, cached voice) | 12.2 s |
| Stills, real story, cinematic recipe | 16.0 s |
| Stills, showcase with two Three.js scenes, cinematic | ~16 s |
| Draft clip (44 s, 12 fps), showcase with two Three.js scenes, cinematic recipe | 34 s |
| Draft clip, same, preset `cinematic-glass` (aurora + grain) | 33.1 s |
| Draft clip with a `mesh-gradient` scene | fails on this CLI version (see status above) |
| `hyperframes check`, cinematic showcase | 0 errors |

## Honest gaps

- The look is a family resemblance to a dark 3D motion-graphics reference,
  not a reproduction: the reference had an animated mascot, maps with routes,
  a phone with a glowing bezel and hand-animated camera moves through a layer
  stack. We have the ground, the glow palette, the neon layer stack, glass
  panels, big numbered titles and a plane-to-plane dolly.
- Three.js glow is a cheap bloom (additive halo copies of each frame), not a
  post-processing pass; grain is a stepping tile, not per-frame noise.
- Object3d labels are HTML under the canvas rather than attached to the 3D
  objects, so they can never overflow but they do not float in the scene.
- The writer is only *offered* the rich scenes; whether it uses them depends on
  the story (one test call with a cinematic recipe produced a plan with the
  regular types). `emphasis` in a preset is advice, not a rule.
- The style-library reference analysis is unchanged by this work (the
  reference-video part is being retired in favour of presets).
