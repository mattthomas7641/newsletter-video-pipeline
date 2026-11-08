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

Four scene types in `scene_plan.SCENE_TYPES`, validated like every other
(hard caps, bounds, repairs), fed from the story, revealing on the voice's
word cues (`explainer_builder.story_payload`). They render in every theme but
are designed for the cinematic one; the writer only sees them when the
resolved look is cinematic (`look.cinematic_requested`, `explainer_writer.CINEMATIC_NOTE`).

| Type | Fields | Hero | Built with |
|---|---|---|---|
| `object3d` | `kind` stack \| bars \| orbit \| cards; `nodes` (stack 2-5, orbit 3-5 + `center`, cards 2-4) or `series` 2-5 + `unit` + `highlight`; optional `title` | a real perspective 3D world drawn with Three.js, full frame width: **stack** = neon-edged glass plates floating over a glowing grid floor (with their reflections), rising in on the voice's cues then spreading apart while the camera orbits, climbs and dollies in, a glowing cube lifting out of the top plate on a shaft of light; **bars** = dark-glass columns with neon edges and lit caps growing from the floor, values pinned above; **orbit** = a rotating core cube in a wire shell, a neon ring and glass satellites joined to it by beams; **cards** = floating glass cards at different depths. Every label is an HTML chip pinned to its object: each frame its anchor (a plate corner, a column top, a satellite) is projected through the camera and a leader line joins it to the chip | Three.js 0.181.2 via the HyperFrames `hf-seek` adapter. One shared `WebGLRenderer` draws every 3D scene from the seek time alone (no clock, no loop) into an off-DOM canvas that is copied into each scene's 2D canvas, plus a second copy that CSS blurs and screen-blends over it (a real bloom, stateless so it seeks). Floor = a shader (`fwidth` grid lines, brighter every fifth, radial fade). `window.__hf.buildReady` holds the first capture until the scenes exist. At most 4 per video (`MAX_OBJECT3D`). |
| `depth` | `nodes` 2-4 layers (label + text), optional `label`, `title` | glass planes with neon edges at increasing distance, their text drawn on the planes (canvas textures, so it takes the perspective); the camera dollies plane to plane on the spoken cues with a lateral sway, planes already passed dissolve before the lens, deeper ones sink into the dark, dust and the grid floor give parallax | Three.js as above (it was CSS 3D with GSAP in the first version; the CSS planes rendered flat and small, see "Why the showcase row was blank"). |
| `kinetic` | `lines` 1-3 (≤ 24 chars), optional big `value` (≤ 4 chars), `label` | a huge glowing numeral and heavy caps lines landing word by word with scale, the spoken line lit in the accent, an underline that sweeps | GSAP; the lit line uses the `caption-neon-glow` shadow stack |
| `glass` | `nodes` 2-4 rows (label = key, text = value ≤ 22 chars), optional `label`, `title`, `icon` | a HUD panel: header with a blinking status dot, key/value rows arriving on cues, a scan sweep, the accent walking the values | CSS glass (gradient, light edge, backdrop blur) + GSAP |

The 3D slot bleeds to the full 1080 px frame width (the stage is 820 px), so scene wipes use `OPEN`, an `inset` a little wider than the stage, instead of `inset(0)`; otherwise a wipe transition clips the 3D world to the stage box.

## 3D-ification (`threed.py`)

The 3D looks should show several 3D scenes in an ordinary story. `threeify_plan(plan)` rewrites suitable middle scenes of a validated plan in place (no model call, narration untouched, every result re-validated, no run of three identical layouts, at most 4 3D scenes counting those already there):

| Scene | Becomes |
|---|---|
| diagram `stack` | object3d `stack` |
| diagram `hub` | object3d `orbit` |
| chart | object3d `bars` |
| comparison with numbers | object3d `bars` (two columns) |
| comparison without numbers, timeline, diagram `flow`/`funnel` | `depth` |

It runs from `apply_recipe_policy` when the recipe is `look-<preset>` of a preset with `"threed": true` (cinematic-glass, depth-cards, data-focus), so the preview, the planner, the writer path and `render_plan` all get it. For those presets the writer prompt also carries `THREE_D_NOTE` ("prefer 3D scenes, at least 3 of the middle scenes"). `paper` and the flat presets never change (`tests/test_threed.py`).

## Presets

`newsletter_video/looks/presets.json` (loader: `newsletter_video.looks`,
`load_presets()` / `presets_json()` for the panel backend). A preset is `{id,
name, description, look: {theme}, knobs, emphasis, registry, thumbnail}`:

| id | ground | knobs (middle scenes) | emphasis | registry items |
|---|---|---|---|---|
| `paper` (default) | light | none | — | — |
| `cinematic-glass` | cinematic, 3D | fade / fade-up / slow-push / inOut, neon palette, speed 0.9 | object3d, glass, depth, kinetic | grain-overlay, aurora-drift (mesh-gradient installed, see status above) |
| `depth-cards` | dark, 3D | push-left / line-slide / parallax, accent `#7b96ff`, violet-cyan palette, stagger 0.1 | depth, glass, device, diagram | processes become glass planes the camera dollies through |
| `data-focus` | dark, 3D | wipe-down / scale-pop / sans-heavy, mint-blue palette, speed 1.15 | chart, stat, comparison, object3d | charts and comparisons become 3D columns on the grid floor |
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

Thumbnails (`newsletter_video/ui/static/looks/<id>.jpg`, 482x282) are crops of the most 3D-looking frame of each preset: a free `stills` run of an ordinary diagram/chart/timeline/comparison story (`output/rich-3d/02_ordinary-story.json`, no writer call, no voice, estimated timing) for cinematic-glass (stack), data-focus (bars) and the flat presets, a `--preview draft` frame for depth-cards (the dolly, two glass planes nested), and the kinetic still of the showcase plan for glow-type. `docs/screenshots/rich-scenes/`: `presets-contact.jpg` (every preset over the same story), `filmstrip-draft.jpg` (the draft clip every 0.7 s: stack, depth dolly, orbit, bars), `showcase-vs-reference.jpg` (our draft frames above the owner's reference keyframes).

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

## Why the showcase row was blank, and what the proof is

`showcase-vs-reference.jpg` had an empty top row. The template was not the cause: the committed template at that commit renders the stack and bars (checked by rendering the same showcase with it, stills and draft), so the blank came from the one-off script that assembled the sheet, which is not in the repo and most likely pointed at frame files that were not there (not verified). That sheet proved nothing, so this round proves each scene type from real video: a free draft clip (`python -m newsletter_video.preview output/rich-3d/01_three-d-showcase.json --preview draft --preset cinematic-glass`, no voice, 12 fps, about 35 s) with frames extracted every 0.7 s (`filmstrip-draft.jpg`) and looked at one by one. What that found and fixed: the first `depth` was CSS 3D and read as small flat cards that vanished; the first object3d stack was small, cropped by its 820 px slot and clipped by the stage wipe; the orbit was blown-out blobs. All three are now Three.js with real perspective, bloom and a grid floor.

## Sources and licences

- Three.js 0.181.2 (MIT), imported from jsdelivr with the version pinned, as the `three` adapter docs prescribe. Everything else in the 3D scenes (plates, floor shader, bloom copy, projection-pinned labels, cameras) is written here, following the registry's `exploded-stack` block for the shape of the job (Three.js + `hf-seek`/clock, labels projected from 3D anchors).
- HyperFrames registry search (`hyperframes catalog --query`, about 20 queries: 3d, isometric, layers stack, glass, orbit, globe, cube, network graph, camera rig, depth, parallax, hologram, grid floor, neon, chart 3d, shader orb, three). Candidates installed and read, then removed again because they do not fit this template: `exploded-stack` (isometric white plates, 1920x1080 landscape, four fixed layers, one self-driven 5 s clock, no word cues), `three-orbiting-cards` (cards on an orbit, no labels or data), `gallery-tunnel` (image corridor), the WebGPU orb blocks `plastic`, `liquid-metal`, `obsidian`, `light-edge` (an orb on black: nothing to carry a label or a number). `camera-rig-depth-stack` and `parallax-*` are CSS-3D card moves, which is what the first `depth` was and why it was replaced. Registry items are not scene types, so they cannot take the story's labels and numbers or the voice's cues without being rewritten, which is why the scenes are ours.
- `aurora-drift`, `grain-overlay`, `caption-neon-glow` (registry, adapted inline) and the `mesh-gradient` block are as described above.

## Honest gaps

- **Genuinely 3D**: the stack, bars, orbit and cards are real meshes under a perspective camera (orbit, climb, dolly) over a grid floor in perspective; the depth scene is real planes with a real dolly. Shadows are blob textures on the floor and the floor reflection is a mirrored copy of the plates at low opacity, not a rendering of the floor's reflection.
- **Faked**: the bloom is a blurred copy of the frame, not a post-processing pass; the plate "circuits" are random thin boxes; labels are HTML chips pinned by projection (they sit over the 3D, they are not geometry); the cube is a plain box, not a model.
- **Still differs from the reference**: it has 5+ big layers with a photo, a phone with a glowing bezel, a mascot, maps and a router route; ours are 2-5 plain plates, no hero artwork, no mascot, labels as small chips, no in-plate UI. Our orbit and bars scenes have no counterpart in the reference. Depth planes show text only. The draft shows these at 12 fps; the final render has not been run for this change.
- The writer is only *offered* the rich scenes; for the 3D presets it is told to prefer them and the transform guarantees several 3D scenes for any story with a diagram, chart, comparison or timeline, but a story made only of statements and stats gets none.
- Grain is a stepping tile, not per-frame noise. The `mesh-gradient` block still fails in `render` on this CLI version (see above).
- The style-library reference analysis is unchanged by this work (the reference-video part is being retired in favour of presets).
