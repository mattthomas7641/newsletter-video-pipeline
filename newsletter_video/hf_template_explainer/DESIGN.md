# Explainer style — design system

The `VIDEO_STYLE=explainer` look: a typographic, editorial explainer with no
presenter and no footage. One composition (`index.html`) renders every video
from a validated scene plan (`newsletter_video/scene_plan.py`); this file is
the contract the composition and the plan caps were designed against.

Reference: a product-launch or newspaper explainer graphic, not a slideshow.
One accent colour, one serif, one sans, one mono. Hierarchy comes from scale,
weight and whitespace, never from decoration.

## Canvas and safe zones

1080 × 1920. Platform UI (TikTok, Shorts, Reels) covers the edges, so every
load-bearing element lives inside the content box.

| Zone | Pixels | Holds |
|---|---|---|
| Top margin | y 0–176 | nothing (status bar, platform chrome) |
| Chrome row | y 176–230 | kicker (date) left, scene counter right, progress rule |
| Stage | y 300–1300, x 80–900 | the scene, vertically centred |
| Captions | y 1380–1500, x 80–900 | lower-third caption, max 2 lines |
| Bottom | y 1540–1920 | nothing (title, handle, action buttons) |
| Right column | x 900–1080 | nothing (like / comment / share) |

Content column: left margin 80 px, width 820 px. The dot grid is the only
element allowed to run under the safe zones.

## Colour tokens

Defined once on `:root`; the dark set overrides them under `[data-theme="dark"]`
(`VIDEO_THEME=light|dark`, default light). Contrast is WCAG AA at every size
used (`npx hyperframes check` audits it).

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#F6F4EE` warm paper | `#111318` | ground |
| `--ink` | `#17191E` | `#F3F1EA` | primary text |
| `--ink-2` | `#5B606B` | `#A3A8B3` | labels, captions, secondary |
| `--hair` | ink @ 16 % | ink @ 18 % | hairline rules |
| `--dot` | ink @ 11 % | ink @ 10 % | dot-grid texture |
| `--accent` | `#1F49D6` cobalt | `#7B96FF` | the one accent: emphasis, lead bars, nodes |
| `--bar` | `#DCD9D0` | `#2A2F3A` | neutral bars and tracks |

Rules: one accent moment per scene (the emphasised phrase, the lead bar, the
counted number's rule). No gradients, no blobs, no glow, no shadows, no second
hue. Neutrals are tinted warm (light) or cool (dark), never dead grey.

## Type

Three self-hosted families (`fonts/`, OFL):

- **Instrument Serif** — headlines, statements, quotes, scene titles. The
  editorial voice. Negative tracking at display sizes, `text-wrap: balance`
  so no headline ends on a one-word line.
- **Geist** (variable) — body lines, list rows, notes, captions, and the big
  figures (700, tabular numerals, −0.045 em).
- **Geist Mono** — chrome: kicker, labels, counters, sources, timeline dates,
  chart labels. Uppercase, 0.14 em tracking.

Scale (px on the 1080 canvas):

| Role | Family | Size | Line | Notes |
|---|---|---|---|---|
| display | serif | 124 | 1.02 | title headline, ≤ 56 chars |
| h1 | serif | 92 | 1.08 | statement / outro, ≤ 72 chars |
| quote | serif italic | 76 | 1.15 | ≤ 110 chars |
| h2 | serif | 68 | 1.10 | scene titles, ≤ 40 chars |
| stat | sans 700 | 272 | 1.0 | ≤ 9 chars, shrinks to fit the column |
| value | sans 700 | 96 | 1.0 | comparison figures, ≤ 12 chars |
| body | sans 500 | 44 / 52 | 1.3 | context lines / list rows |
| caption | sans 500 | 40 | 1.3 | lower third |
| label | mono 500 | 26 | 1.2 | uppercase, tracked |

Every text field has a hard character cap in `scene_plan.CAPS`; the scale
above fits the longest allowed string in its box. A build-time fit pass
(`fitAll()` in `index.html`) is the belt to those braces: display, h1, quote
and stat values shrink in 4–8 px steps if a font substitution ever makes
them taller or wider than their box. Nothing is ever clipped.

## Spacing

8-point scale: 8 · 16 · 24 · 32 · 48 · 64 · 96. Vertical rhythm inside a
scene: label → 32 → main element → 32/48 → secondary element. Hairlines are
2 px of `--hair`; list rows and comparison columns are separated by them.

## Scene library

| Type | Layout | Reveal |
|---|---|---|
| title | kicker · headline · brand line | kicker, then words of the headline as a wave, then the brand line |
| statement | label · sentence with one emphasised phrase | sentence in; the phrase turns accent when it is spoken |
| stat | label · big number · accent rule · context · source | number counts up (pre-baked frames) while the rule fills; both land together |
| comparison | title · two columns (label, figure, note, bar) | columns in on their cues; bars fill to scale, the larger in accent |
| list | title · 2–4 numbered rows | each row lands when its words are spoken |
| timeline | title · rail with 2–4 nodes | rail draws down; nodes and steps land on their cues |
| quote | accent quotation mark · quote · attribution | mark, words as a wave, attribution |
| chart | title · 2–5 horizontal bars with labels and values · baseline | bars fill left-to-right, values count, the highlighted bar in accent |
| outro | label · closing line · brand line | same as title |

Scene timing is the voice: a scene starts 0.2 s before its first spoken word
and exits 0.28 s before the next scene starts. Reveal cues inside a scene come
from the word timestamps (`explainer_builder.cue_times`); items the voice does
not name verbatim are spread evenly through the first 70 % of the line.

## Motion tokens

| Token | Value |
|---|---|
| enter | 0.6 s, `power3.out`, y 36 → 0, opacity 0 → 1 |
| exit | 0.28 s, `power2.in`, y 0 → −24, opacity 1 → 0 |
| row / step / bar arrival | 0.45–0.5 s, `power3.out`, y 28 → 0 |
| word wave | per word, 0.5 s `power4.out` from y 44, binary opacity, 55 ms apart, whole line lands by ~0.7 s |
| count-up | 1.3 s, `power2.out`, number scale 0.94 → 1 in step with the rule fill |
| bar fill | 0.9 s, `power3.out` (chart) / `power2.out` (comparison), `scaleX` from the left |
| emphasis | 0.35 s colour change to accent, on the spoken word |
| progress rule | linear over the whole video |

No bounce, elastic or overshoot anywhere (numbers are data, not decoration).
No breathing, no idle drift, no camera moves: a held frame stays still. All
motion is on the one paused GSAP timeline, driven from the plan's times, so
every frame is a pure function of time (seek-safe, deterministic).

## Why one file

HyperFrames lint flags the composition as large and suggests sub-compositions.
This template is deliberately a single data-driven composition: scene count,
order and timing come from the plan at render time, which a static host of
sub-composition slots cannot express; and a single DOM lets the build-time
fit pass measure every scene. Registry charts and count-ups were reviewed
(`npx hyperframes catalog --query ...`) and not adopted because they carry
their own type and colour tokens; the scene library applies this system's
tokens instead, following the same seek-safe recipes (pre-baked count frames,
`scaleX` fills from authored widths).
