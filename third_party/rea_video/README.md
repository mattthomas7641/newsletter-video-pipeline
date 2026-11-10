# Vendored pieces of rea-video

Upstream: [JuneYaooo/rea-video](https://github.com/JuneYaooo/rea-video),
Apache-2.0 (`LICENSE`, `NOTICE` alongside this file). rea-video splits a
video into shots, gathers per-shot evidence and classifies each shot as
filmed / AI-generated / coded. Our style library (`newsletter_video/styles/`)
borrows its *evidence-gathering ideas* for a different purpose: describing
motion-graphic references in our own vocabulary so we can recreate and reuse
their effects.

Only what we use is vendored, and it is rewritten rather than copied:
upstream is built on OpenCV, which this project does not install, so every
function in `primitives.py` was reimplemented against numpy. The file header
marks it as modified.

| `primitives.py` | Adapted from upstream | What changed |
|---|---|---|
| `local_median` | `rea_video/shots.py::_local_median` | same algorithm (median of the window excluding the centre), vectorised with a sliding window |
| `boundary_score` | `rea_video/shots.py::detect_shots` (score, `strong` / `relative` rules) | histogram distance computed from numpy 3-D histograms instead of `cv2.compareHist`; same 0.6 / 0.4 weighting and the adaptive `base * 2.5 + 0.06` rule |
| `easing_shape` | `rea_video/motion.py::_easing` | identical thresholds (0.68 / 0.32 / cv 0.45) |
| `sweep_direction` | `rea_video/motion.py::_direction` | identical (correlation of onset time with position, |r| > 0.6) |
| `tempo_from_onsets` | `rea_video/audio.py::analyze_audio` (tempo autocorrelation and beat grid) | autocorrelation over 60-190 BPM, octave preference, comb-phase grid, snap to local peaks |
| `on_grid_ratio` | `rea_video/audio.py::vocal_style` (onsets-on-beat ratio and chance level) | generalised to any list of event times |

Nothing else from upstream is used. In particular we do not vendor its CLI,
its agent skill, its report generator, the filmed / AI / coded classifier,
OCR, face detection, stem separation or any VLM client; the `install_skill.sh`
script was never run.
