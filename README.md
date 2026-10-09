# Newsletter-to-Video Pipeline

An unattended daily pipeline that turns the [TLDR](https://tldr.tech) newsletters into
40–45 second vertical (1080×1920) tech-news explainers and uploads them to YouTube Shorts.

```
tldr.tech (4 editions) ──▶ scrape ──▶ Claude writes ──▶ voice each line ──▶ HyperFrames ──▶ MP4s ──▶ YouTube
   tech · ai ·              ~50        N scripts:        (Kokoro, local)     render                  Shorts
   marketing · founders     stories    hook, beats,
                                       cards, outro
```

Each video covers one big story or 2–3 related ones. It opens with a spoken hook shown as
big kinetic text, then plays as a comic-style explainer over full-screen gameplay footage:
a topic badge, one punchy card per spoken beat that slides, drops, zooms or flips in
(numbers get a big stamped stat), word-by-word captions with the spoken word highlighted,
and an animated explainer avatar that lip-syncs and gestures at each card. An outro card
asks viewers to comment.

- **Runs itself:** a macOS `launchd` job writes, renders and uploads the day's videos at
  7:00 AM (about 2.5 minutes of rendering per video), logging every stage.
- **Claude as head writer, with a fallback:** with an API key, Claude Sonnet 5.5 picks the
  most interesting tech stories of roughly 50, combines related ones, and writes each script
  plus its post title, caption and hashtags (prompt: `WRITER_SYSTEM` in `script_writer.py`).
  Without a key, a rule-based writer groups TLDR's top stories into videos.
- **Designed in HyperFrames:** the look lives in an HTML composition
  (`newsletter_video/hf_template/`) you can open and edit in HyperFrames Studio.
- **Accurate timing:** every spoken line is voiced as its own clip, so cards and captions
  start exactly on sentence boundaries (average caption error 0.21s, down from 0.70s when
  timing was estimated across the whole narration).
- **Safe uploads:** each upload is recorded the moment it succeeds, so re-runs never post
  twice; stories covered earlier in the day are skipped.

## Content rights

The avatar is original and drawn in code, and the voice is a local open model. The gameplay
background is whatever you put in `assets/background/` — you're responsible for having the
right to use it before posting publicly.

## Quick start

```bash
cd newsletter-video-pipeline
./venv/bin/python -m newsletter_video.run_pipeline             # today's videos
./venv/bin/python -m newsletter_video.run_pipeline 2026-09-22  # a specific date
```

Output lands in `output/YYYY-MM-DD/01_<title-slug>.mp4`, `02_…`, each with a `.json`
holding its title, description, tags, the stories it covered and its full script. Running
again on the same day adds new videos after the existing ones. Logs go to `pipeline.log`.

Rendering uses the [HyperFrames](https://github.com/heygen-com/hyperframes) CLI through
`npx` (pinned in `tts.py`), so Node.js must be installed; it renders locally and needs no
account.

The Python environment (`venv/`) is a self-contained conda env with ffmpeg already
installed. It was built with:

```bash
conda create -p ./venv python=3.11 ffmpeg -c conda-forge
./venv/bin/python -m pip install -r requirements.txt
```

## Configuration (`.env`)

Copy `.env.example` to `.env`. Everything is optional:

| Setting | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Claude writes the scripts (much more engaging than the rule-based fallback) |
| `WRITER_EFFORT` | How hard Claude reasons (default `medium`: same script quality as the model default at about 4x fewer output tokens) |
| `VIDEOS_PER_DAY` | Videos per run (default 3; YouTube's free quota allows about 6 uploads a day) |
| `KOKORO_VOICE`, `KOKORO_SPEED` | Voice and pace (`npx hyperframes tts --list` for voices) |
| `TTS_PROVIDER` | `kokoro` (default), `say` (macOS) or `elevenlabs` |
| `YOUTUBE_UPLOAD` | `1` uploads each day's videos after rendering |
| `YOUTUBE_PRIVACY`, `YOUTUBE_SCHEDULE` | Visibility and timed publishing (once your API project is audited) |

Videos land between 40 and 45 seconds: Claude aims for 100–110 words, and if the voiceover
still runs long or short it's re-voiced slightly faster or slower. Prices and units are
rewritten into spoken words before voicing ("$0.10" → "10 cents"), and coined words the voice
gets wrong can be respelled in `PRONUNCIATIONS` in `tts.py`.

## Customizing

### Changing the look
The composition is `newsletter_video/hf_template/index.html`. To preview it with sample
data, put any `voice.wav` in `newsletter_video/hf_template/assets/` (gitignored), then:
```bash
cd newsletter_video/hf_template && npx hyperframes preview
```
The pipeline passes each video in as the `story` composition variable (badge, hook words,
cards with highlight and timing, captions, lip-sync data), so the template works the same in
Studio and in automated renders.

### The explainer avatar
An original cartoon robot drawn as SVG in the template, so every part can act: its mouth
follows the voice's loudness frame by frame, it blinks, gestures one arm at a time toward
each new card, and raises its eyebrows when a highlight lands. Restyle it by editing the
`#avatar` SVG; the animation targets its `av-*` group ids.

### Gameplay background
Drop any vertical gameplay `.mp4` into `assets/background/` (or download one with
`./venv/bin/python -m newsletter_video.fetch_background "<url>"`). The first run converts
each clip once to a light 1080×1920 copy in `assets/background/.cache/`, so later cuts take
seconds instead of re-decoding the original. Each video gets a
different stretch of a randomly chosen clip (seeded by date, so re-runs match), cut to the
video's exact length, muted and darkened. With the folder empty, videos use a graph-paper
background.

### Custom voice
Set `TTS_PROVIDER=elevenlabs` with `ELEVENLABS_API_KEY` and `VOICE_ID` to use a voice from
your own ElevenLabs account.

## How the pipeline works

1. `scraper.py` — scrapes `tldr.tech/{edition}/{date}` (public, no login) for the
   configured editions (`DEFAULT_EDITIONS`).
2. `script_writer.py` — dedupes stories across editions, drops pure-science sections and
   stories already covered today, and writes `VIDEOS_PER_DAY` scripts: a hook, 4–6 beats
   (a spoken line plus shorter on-screen card text with one highlighted phrase, or a big
   stat), an outro, and the post title/caption/hashtags.
3. `tts.py` — voices each line with Kokoro (model loaded once per run) after normalizing
   money, units and known pronunciations.
4. `video_builder.py` — joins the lines, times every card, caption chunk and highlight to
   them, computes lip sync, cuts a gameplay stretch, and renders the HyperFrames template.
5. `youtube_upload.py` — uploads the videos and records each upload.
6. `run_pipeline.py` — runs the above in order for a given date, then frees disk space:
   video files older than 7 days are deleted once confirmed uploaded (their `.json`
   metadata and scripts are kept).

## Uploading to YouTube Shorts

With `YOUTUBE_UPLOAD=1`, the daily run uploads each video with its title, description
(Claude's caption, source links and hashtags) and tags.

```bash
./venv/bin/python -m newsletter_video.youtube_upload YYYY-MM-DD            # upload/retry a day
./venv/bin/python -m newsletter_video.youtube_upload YYYY-MM-DD --dry-run  # preview posts
./venv/bin/python -m newsletter_video.youtube_upload YYYY-MM-DD --publish  # make them public
```

One-time setup (Google account, about 10 minutes):
1. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable
   **YouTube Data API v3**.
2. **OAuth consent screen**: user type *External*, your app name and email, add yourself as a
   test user, and add the scopes `youtube.upload`, `youtube.readonly` (shows which channel is
   linked) and `youtube` (needed only for `--publish`).
3. **Credentials → Create credentials → OAuth client ID → Desktop app**; save the JSON as
   `secrets/youtube_client_secret.json` (gitignored).
4. Run `./venv/bin/python -m newsletter_video.youtube_upload --auth` and pick the channel.
5. Set `YOUTUBE_UPLOAD=1` in `.env`.

Limits: YouTube keeps uploads from unaudited API projects private until the project passes
Google's free YouTube API Services audit, so videos are published with `--publish` (or in
YouTube Studio). While the consent screen is in "Testing", the sign-in expires every 7 days;
rerun `--auth`. The default quota covers about 6 uploads plus publishing per day.

## Running it automatically every day

A `launchd` job runs `run_daily.sh` every morning. See
`com.tldrbrainrot.daily.plist` in `~/Library/LaunchAgents/`.

```bash
launchctl list | grep tldrbrainrot                                     # is it loaded?
launchctl start com.tldrbrainrot.daily                                 # run now
tail -f pipeline.log                                                   # watch a run
launchctl unload ~/Library/LaunchAgents/com.tldrbrainrot.daily.plist   # stop the schedule
```
