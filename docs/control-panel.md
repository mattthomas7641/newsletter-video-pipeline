[Back to the README](../README.md)

# Control panel

Everything in [the configuration](configuration.md) can be set up by clicking instead of editing files. Start the panel:

```bash
./ui.sh                                   # or double-click ui.command in Finder
./venv/bin/python -m newsletter_video.ui  # the same thing
```

It opens `http://127.0.0.1:8765` in your browser (another port is used if that one is busy)
and needs nothing beyond the packages already in `requirements.txt`. The panel is always light. Press Ctrl+C in the terminal to stop it; anything already running keeps going.

Three pages. Screenshots are in `docs/screenshots/lineup/`.

- **Lineup** (the landing page): a standing description of the day's videos that the automatic
  run reads, so there is no list of planned videos to review. Set *Videos per day* (1-20), split
  them into **batches** (a count, a format and colours each), save. Example: 3 *Clean paper* in one
  colour and 4 *Dark glass* in alternating colours, plus 3 avatar videos. A batch is Explainer
  (*Clean paper* or *Dark glass*, a background colour, and the animation colour as *One colour*,
  *Alternate* through a palette, or *Random* from it, seeded per day so a re-render keeps its colour)
  or Avatar (the avatars you have, one standard voice set in Settings). Quick starts (*All dark
  glass, alternating*, *All clean paper*, *50/50*), *Randomize* (with undo) and *Shuffle colours*
  get you going; *Preview* on a batch makes a free draft of that format on a sample story. The slot
  strip shows the day in publish order (*Interleave batches* spreads formats through the day);
  click a slot to change just that one. *Save lineup* uses it every day until changed; *Only for
  the next run* is a one-off after which the previous lineup is back. The banner always says what
  the next run will make. Saved in `lineup.json` (gitignored, next to `.env`; no file means the
  old behaviour from `VIDEOS_PER_DAY` and the `LOOK_*` settings, which stay as fallbacks);
  `VIDEOS_PER_DAY` follows the lineup's total. The daily run writes one script batch per format and
  records each video's format (style, preset, colours, avatar, batch, slot, date) in its sidecar and
  in `uploads.json`; *Results by format* at the bottom lists the last 14 days of uploads grouped by
  format, to compare formats over time (views are not fetched yet).
- **Design lab**: for trying new designs, or seeing how a render looks without drafting a video.
  Pick a look, *Make a test video* (one throwaway video from today's newsletter, never uploaded),
  then compare storyboards of the same story in two looks side by side; every look control is next
  to it and each change re-captures the storyboard (free, about 15 s). Not part of the daily flow. The plan review for `DAILY_MODE=plan` (approval mode), planning by hand and the rendered videos live in its *Plan review and rendered videos* section.
- **Settings.** The narrators and their voices (*Hear voice*, *Add a character*),
  videos per day, what the scheduled run does (make everything, or plan and ask you), YouTube
  upload and privacy, what happens to videos made by the fallback writer, and a *Setup check*
  showing whether Voicebox, an API key and the YouTube sign-in are present (never their values).
  Changes are only written when you press *Save*, and apply from the next run, including the
  scheduled one, because they live in `.env`.

See `docs/previews.md` for the preview tiers, timings and the look layering rules.

**Safety.** The panel listens on `127.0.0.1` only. It will only ever read or write these
`.env` keys: `CHARACTER`, `VIDEO_STYLE`, `VIDEO_THEME`, `BACKGROUND_STYLE`, `VIDEOS_PER_DAY`,
`DAILY_MODE`, `YOUTUBE_UPLOAD`, `YOUTUBE_PRIVACY`, `FALLBACK_UPLOAD`, `LOOK_PRESET`, `LOOK_BG`, `LOOK_ACCENT`, `LOOK_ACCENT_ROTATE`. Comments, other lines and the file's
permissions are left as they were. API keys and tokens are never sent to the browser or
written to its logs; the panel only says whether one is set. Requests are checked for the
loopback `Host`, a same-origin `Origin`, and a random per-launch token, so another web page
can't drive it. The reference-video style library is a command-line feature only (see [Style library](video-styles.md#style-library-teach-the-look-by-example)); the panel no longer uploads or analyses reference videos. Uploaded pictures and recordings are checked by content, size-capped and
stored under generated names.
