# Style library

Reference videos whose look you want the explainer to learn from. Full
documentation: `docs/style-library.md`.

The inbox workflow:

1. Drop a video you have the right to study into `inbox/` (any mp4/mov/webm).
2. Add it: `./venv/bin/python -m newsletter_video.styles add inbox/clip.mp4 --name a-short-name --notes "what you like about it"`
   (analysis, a vision pass if you have an API key, a placeholder recreation
   and a fidelity score; a few minutes, roughly $0.15).
3. Open `<name>/README.md` and `<name>/recreation/side_by_side.jpg` to see
   what was understood and how close the recreation came. Edit `meta.json`
   notes/tags if you like, then `python -m newsletter_video.styles recreate <name>`
   to try again.
4. From then on every explainer video the pipeline writes gets one planner
   call that chooses recipes from this library per scene. Set
   `STYLE_LIBRARY=off` in `.env` to switch it off; with no entries here the
   look is unchanged.

Search what you have: `python -m newsletter_video.styles search "big type, fast cuts"`.

What is committed: the analyses, keyframes, recipes and READMEs (derived,
original work). What stays local: the clips in `inbox/` and each
`<name>/reference.*`, and the recreation renders.
