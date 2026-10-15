# Explainer style — design system

The `VIDEO_STYLE=explainer` look: an editorial explainer with no presenter and
no footage, where every scene is built around a graphic (a pictogram, a
number, a chart, a diagram, a device wireframe) and the text is its label.
One composition (`index.html`) renders every video from a validated scene plan
(`newsletter_video/scene_plan.py`); this file is the contract the composition
and the plan caps were designed against.

Reference: a product-launch or newspaper explainer graphic in motion, not a
slideshow. One accent colour, one serif, one sans, one mono, one stroke style.
Hierarchy comes from scale, weight and whitespace, never from decoration.
Everything drawn is authored vector (CSS and inline SVG); no stock imagery,
logos or screenshots.

## Canvas and safe zones

1080 × 1920. Platform UI (TikTok, Shorts, Reels) covers the edges, so every
load-bearing element lives inside the content box.

| Zone | Pixels | Holds |
|---|---|---|
| Top margin | y 0–176 | nothing (status bar, platform chrome) |
| Chrome row | y 176–230 | kicker (date) left, scene counter right, progress rule |
| Stage | y 272–1336, x 80–900 | the scene: head (label, title) at the top line, hero graphic filling the middle, foot (context, source, brand line) at the bottom |
| Captions | y 1376–1500, x 80–900 | lower-third caption, max 2 lines |
| Bottom | y 1540–1920 | nothing (title, handle, action buttons) |
| Right column | x 900–1080 | nothing (like / comment / share) |

Content column: left margin 80 px, width 820 px. The stage is a flex column
(`head` · `hero` grows · `foot`), so a scene always starts on the same top
line and its graphic takes the full remaining height. The dot grid is the
only element allowed to run under the safe zones.

## Colour tokens

Defined once on `:root`; the dark set overrides them under `[data-theme="dark"]`
(`VIDEO_THEME=light|dark`, default light). Contrast is WCAG AA at every size
used (`npx hyperframes check` audits it; 0 findings in both themes).

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#F6F4EE` warm paper | `#111318` | ground |
| `--ink` | `#17191E` | `#F3F1EA` | primary text, pictogram strokes |
| `--ink-2` | `#5B606B` | `#A3A8B3` | labels, captions, secondary, neutral discs |
| `--hair` | ink @ 16 % | ink @ 18 % | hairline rules, node borders, grid lines |
| `--dot` | ink @ 11 % | ink @ 10 % | dot-grid texture |
| `--accent` | `#1F49D6` cobalt | `#7B96FF` | the one accent: emphasis, lead bars/discs/bands, nodes, rails, wipe edge |
| `--accent-soft` | accent @ 14 % | accent @ 18 % | line-chart area, timeline marker fill |
| `--bar` | `#DCD9D0` | `#2A2F3A` | neutral bars, tracks, waffle dots |
| `--panel` | `#FFFFFF` | `#181B22` | diagram nodes, device frames |
| `--screen` | `#17191E` | `#050608` | terminal screen |

Rules: one accent per scene (the emphasised phrase, the lead bar, the counted
number's rule, the highlighted disc). No gradients, no blobs, no glow, no
shadows, no second hue. Neutrals are tinted warm (light) or cool (dark),
never dead grey.

## Type

Three self-hosted families (`fonts/`, OFL):

- **Instrument Serif** — headlines, statements, quotes, scene titles. The
  editorial voice. Negative tracking at display sizes, `text-wrap: balance`
  so no headline ends on a one-word line.
- **Geist** (variable) — body lines, list rows, notes, captions, and every
  figure (700, tabular numerals, −0.04 em).
- **Geist Mono** — chrome: kicker, labels, counters, sources, timeline dates,
  chart labels, terminal text. Uppercase, 0.14 em tracking.

Scale (px on the 1080 canvas):

| Role | Family | Size | Line | Notes |
|---|---|---|---|---|
| display | serif | 136 | 1.0 | title headline, ≤ 56 chars |
| h1 | serif | 100 | 1.06 | statement / outro, ≤ 72 chars |
| quote | serif italic | 80 | 1.15 | ≤ 110 chars |
| h2 | serif | 68 | 1.1 | scene titles, ≤ 40 chars |
| stat | sans 700 | 300 (190 inside a ring) | 1.0 | ≤ 9 chars, shrinks to fit the column |
| value | sans 700 | 88 | 1.0 | comparison figures, ≤ 12 chars |
| figure | sans 700 | 40–44 | 1.0 | bar, column, legend and callout values |
| body | sans 500 | 44 / 48 / 56 | 1.3 | context · timeline text · list rows |
| caption | sans 500 | 40 | 1.3 | lower third |
| label | mono 500 | 26 | 1.2 | uppercase, tracked |

Every text field has a hard character cap in `scene_plan.CAPS`; the scale
above fits the longest allowed string in its box. A build-time fit pass
(`fitAll()` in `index.html`) is the belt to those braces: display, h1, quote
and stat values shrink in 4–8 px steps if a font substitution ever makes
them taller or wider than their box. Nothing is ever clipped.

## Spacing

8-point scale: 8 · 16 · 24 · 32 · 48 · 64 · 96. Vertical rhythm inside a
scene: label → 24/32 → main element → 32/48 → secondary element. Hairlines
are 2 px of `--hair`; node and frame borders are 2–4 px of `--hair`/`--ink`.

## Visual vocabulary

| Element | Construction | Motion |
|---|---|---|
| Pictogram (`icons.js`, 45 names) | 24-unit grid, 1.5-unit round stroke, no fills except small dots; 360 px as a hero, 180 px in a comparison tile, 72 px in a list row | strokes draw in (dash offset, `power2.out`, 0.6–0.8 s, 90 ms apart), fills fade last |
| Big number | sans 700 tabular, optional accent rule under it | counts up over 1.3 s (pre-baked frames, `power2.out`), scales 0.94 → 1 with the count |
| Trend mini-chart | 2–8 columns under the number, last column accent | columns grow from the baseline, 80 ms apart |
| Progress ring | 620 px circle, 22 px stroke, number inside | stroke draws from 12 o'clock to the percentage with the count |
| Bars / columns | neutral tracks, lead item accent, values ride the bar end | `scaleX` / `scaleY` from authored sizes, 0.9 s `power3.out`, one per cue |
| Line chart | 8 px accent line, soft area fill, points, three hairline grid lines, callout figure | line draws left to right across the cues, points pop as it passes, area fades in at the midpoint |
| Dots of 100 | 10 × 10 waffle, each series a colour (accent, ink-2, bar) | dots recolour in reading order per series, legend values count |
| Comparison discs | two circles whose area is proportional to the numbers, max 374 px, lead in accent, "vs" between | discs scale 0 → 1 on their cues; both columns land together so baselines match |
| Comparison tiles | 300 px hairline squares with a pictogram each | columns slide in from their sides, pictograms draw |
| Timeline | 4 px rail, 20 px nodes, 44 px accent marker ring | rail fills down; the marker rides to each node on its cue; nodes turn accent as it arrives |
| Flow diagram | panel nodes (label + text) joined by 72 px arrow connectors | nodes arrive on cues, each connector draws just before its node |
| Stack diagram | panel layers built bottom-up, top layer accent-bordered | layers land from below, 450 ms apart |
| Hub diagram | 260 px accent disc centre, 3–5 panel nodes on an ellipse, spokes | centre scales in, each spoke draws then its node lands |
| Funnel | 3–4 narrowing bands (820 → ≈370 px), last band accent | bands expand from their top edge in order |
| Device: phone | 470 × 880 rounded frame with a notch, notification cards with an accent stub | cards slide up on their cues |
| Device: laptop | 820 × 540 window with a title bar, rows with a meter | rows slide in, meters fill |
| Device: terminal | dark screen, mono text, accent block caret | lines type on character by character (discrete sets), caret blinks until the next line |
| Device: chat | bubbles alternating sides, the reply in accent | bubbles slide in from their side |

## Scene library

| Type | Head | Hero | Foot |
|---|---|---|---|
| title | kicker | pictogram · headline (word wave) | brand line |
| statement | label | sentence with one accent phrase · pictogram | — |
| stat | label | number · rule · trend chart, or number inside a ring | context · source |
| comparison | title | discs or tiles, labels, figures, notes | — |
| list | title | 2–4 rows with pictogram or index | — |
| timeline | title | rail, marker, 2–4 steps | — |
| quote | — | quotation mark · quote (word wave) · attribution | — |
| chart | title | bars · columns · line · dots | — |
| diagram | title | flow · stack · hub · funnel | — |
| device | title | phone · laptop · terminal · chat | — |
| outro | label | closing line · megaphone pictogram | brand line |

Scene timing is the voice: a scene starts 0.2 s before its first spoken word.
Reveal cues inside a scene come from the word timestamps
(`explainer_builder.cue_times`, stem-matched); items the voice does not name
are spread evenly through the first 70 % of the line. A stat counts when its
number is said; an emphasis phrase turns accent when it is said.

## Transitions and motion tokens

| Token | Value |
|---|---|
| push (even scenes) | outgoing lifts 56 px and fades over 0.3 s `power2.in`; incoming rises 72 px over 0.5 s `power3.out` |
| wipe (odd scenes) | incoming is revealed top-to-bottom by a `clip-path` inset over 0.5 s `power3.inOut` behind a 4 px accent edge that travels the stage; outgoing fades underneath; parts of the incoming scene are already in place |
| hold | the hero layer pushes in 1 → 1.035 over the whole scene, linear — the one continuous motion while a scene holds |
| enter | 0.6 s `power3.out`, y 36 → 0, opacity 0 → 1; every revealed element is hidden from t = 0 and arrives on its cue |
| row / node / bar arrival | 0.45–0.55 s `power3.out`, y 20–40 → 0 |
| word wave | per word, 0.5 s `power4.out` from y 44, binary opacity, 55 ms apart, whole line lands by ~0.7 s |
| count-up | 1.3 s `power2.out` (0.8–0.9 s for chart figures), scale 0.94 → 1 in step |
| draw | dash-offset to 0, `power2.out`, 0.4–0.8 s |
| emphasis | 0.35 s colour change to accent, on the spoken word |
| marker travel | 0.3–0.6 s `power3.inOut`, arriving 0.3 s before the step is spoken |
| caption | chunk fades up 0.2 s; the spoken word takes ink colour |
| progress rule | linear over the whole video |

No bounce, elastic or overshoot anywhere (numbers are data, not decoration).
No breathing, no idle drift, no camera moves beyond the slow push. With
captions changing every 1–2 s, a count, a draw or a cue landing in every
scene and the progress rule always moving, something meaningful moves every
1–2 seconds. All motion is on the one paused GSAP timeline, driven from the
plan's times, so every frame is a pure function of time (seek-safe,
deterministic); counts and typing are discrete sets, never `onUpdate` state.

## Why one file

HyperFrames lint flags the composition as large and suggests sub-compositions.
This template is deliberately a single data-driven composition: scene count,
order and timing come from the plan at render time, which a static host of
sub-composition slots cannot express; and a single DOM lets the build-time
fit pass and the cue measurements (timeline marker, bar widths) see every
scene. Registry charts and count-ups were reviewed
(`npx hyperframes catalog --query ...`) and not adopted because they carry
their own type and colour tokens; the scene library applies this system's
tokens instead, following the same seek-safe recipes (pre-baked count frames,
`scaleX` fills from authored widths, dash-offset draws).
