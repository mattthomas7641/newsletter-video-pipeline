# ByteSize News (Newsletter-to-Video Pipeline)

An unattended daily pipeline that turns the [TLDR](https://tldr.tech) newsletters into
40–45 second vertical (1080×1920) tech-news explainers and uploads them to YouTube Shorts.

```
tldr.tech (4 editions) ──▶ scrape ──▶ Jev screens ──▶ Claude writes ──▶ voice each line ──▶ HyperFrames ──▶ MP4s ──▶ YouTube
   tech · ai ·              ~40–50     in scope? how     N scripts:        (cloned voice via   render                  Shorts
   marketing · founders     stories    newsworthy?       hook, beats,      Voicebox, or Kokoro)
                                       (optional)        cards, outro
```

Each video covers one big story or 2–3 related ones. It opens with a spoken hook shown as
big kinetic text, then plays as a comic-style explainer over full-screen gameplay footage:
a topic badge, one punchy card per spoken beat that slides, drops, zooms or flips in
(numbers get a big stamped stat), word-by-word captions with the spoken word highlighted,
and a narrator character at the bottom of the frame. An outro card
asks viewers to comment.

- **Runs itself:** a macOS `launchd` job writes, voices, renders and uploads the day's
  videos at 7:00 AM, logging every stage. With a locally cloned voice, 10 videos take about
  85 minutes on a laptop.
- **Two-stage story selection:** with a TypeSafe API key, Jev screens every candidate for
  scope and newsworthiness first, so Claude only sees the shortlist (see
  [Story selection with Jev](#story-selection-with-jev)).
- **Claude as head writer, with a fallback:** with an API key, Claude Sonnet 5.5 picks the
  most interesting tech stories from the candidates, combines related ones, and writes each script
  plus its post title, caption and hashtags (prompt: `WRITER_SYSTEM` in `script_writer.py`).
  Without a key, a rule-based writer groups TLDR's top stories into videos.
- **Designed in HyperFrames:** the look lives in an HTML composition
  (`newsletter_video/hf_template/`) you can open and edit in HyperFrames Studio.
- **Accurate timing:** every spoken line is voiced as its own clip, so cards and captions
  start exactly on sentence boundaries (average caption error 0.21s, down from 0.70s when
  timing was estimated across the whole narration).
- **Safe uploads:** each upload is recorded the moment it succeeds, so re-runs never post
  twice; stories covered on any earlier day are skipped, even when retitled (fuzzy title
  match against every past day's output).

## Content rights

You supply three things, and you're responsible for having the right to use each of them
before posting publicly:

- **The narrator character:** whatever image you put in `assets/character/` (gitignored).
- **The voice:** with Voicebox or ElevenLabs, a voice you cloned from reference audio. Clone
  your own voice or one you have permission to use. The default Kokoro voice is an open
  model and needs no reference audio.
- **The gameplay background:** whatever you put in `assets/background/`.

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
| `VIDEOS_PER_DAY` | Videos per run (default 3; "~6 uploads/day" is a commonly cited quota estimate, not a confirmed or enforced limit — 16 uploads succeeded in one day during testing with no quota errors) |
| `TYPESAFE_API_KEY` | Jev screens and ranks stories before Claude sees them (see [Story selection with Jev](#story-selection-with-jev)) |
| `TTS_PROVIDER` | `kokoro` (default), `voicebox` (local cloned voice), `elevenlabs` (cloud cloned voice) or `say` (macOS) |
| `KOKORO_VOICE`, `KOKORO_SPEED` | Kokoro voice and pace (`npx hyperframes tts --list` for voices) |
| `VOICEBOX_PROFILE_ID`, `VOICEBOX_ENGINE`, `VOICEBOX_MODEL_SIZE`, `VOICEBOX_INSTRUCT` | Voicebox settings (see [Custom voice](#custom-voice)) |
| `VOICE_EFFECT`, `CARTOON_PITCH` | `cartoon` pitches any voice up (1.3 subtle to 1.6 squeaky); `none` (default) keeps it natural |
| `BACKGROUND_STYLE` | `gameplay` (default) or `none` for the plain graph-paper background |
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
cards with highlight and timing, captions), so the template works the same in
Studio and in automated renders.

### The narrator character
The narrator is a single cutout image: the first `.png` or `.jpg` in `assets/character/`
(gitignored, so it never goes into the repo). The composition places it at the bottom of
the frame with a slight idle bob, and keeps the cards and captions clear of its head. To
change the narrator, swap the file. With the folder empty, videos render without one.

### Gameplay background
Set `BACKGROUND_STYLE=none` to skip it entirely. Otherwise, drop any vertical gameplay `.mp4` into `assets/background/` (or download one with
`./venv/bin/python -m newsletter_video.fetch_background "<url>"`). The first run converts
each clip once to a light 1080×1920 copy in `assets/background/.cache/`, so later cuts take
seconds instead of re-decoding the original. Each video gets a
different stretch of a randomly chosen clip (seeded by date, so re-runs match), cut to the
video's exact length, muted and darkened. With the folder empty, videos use a graph-paper
background.

### Custom voice
Two ways to use a cloned voice:

**Voicebox (local, free).** [Voicebox](https://voicebox.sh) is an MIT-licensed desktop app
that clones a voice from a short reference clip and runs entirely on your Mac.
1. Install Voicebox, create a voice profile from your reference audio, and copy its profile ID.
2. In `.env`, set `TTS_PROVIDER=voicebox` and `VOICEBOX_PROFILE_ID=<that id>`.
3. Optional: `VOICEBOX_ENGINE` (default `qwen`, Qwen3-TTS), `VOICEBOX_MODEL_SIZE` (default
   `0.6B`) and `VOICEBOX_INSTRUCT`, a plain-English delivery note. Leave it blank at first:
   asking for "more energy" distorted sibilants into a lisp on the voice this was tuned with.

How it runs unattended:
- Voicebox is a regular app, not a background service, and its server can exit between
  runs. Before voicing, the pipeline checks `localhost:17493/health` and, if needed,
  relaunches the app and waits up to 90 seconds for it to come up.
- Each line is requested from `POST /generate`, its progress is read from the
  `/generate/{id}/status` event stream, and the finished audio is downloaded.
- Pace is adjusted afterwards with ffmpeg's `atempo`, so the 40–45 second target works the
  same as with Kokoro.
- If Voicebox can't be reached, the run stops with a clear error. It does not quietly switch
  to another voice, so a day's videos never mix two narrators.

**ElevenLabs (cloud).** Set `TTS_PROVIDER=elevenlabs` with `ELEVENLABS_API_KEY` and
`VOICE_ID` to use a voice from your own ElevenLabs account.

### Story selection with Jev
With `TYPESAFE_API_KEY` set (and `typesafe-sdk` installed from `requirements.txt`), Jev, a
decision model from TypeSafe AI, screens every candidate story before
Claude sees it. Jev classifies and scores; it never writes text. For each story's title and
summary it answers two questions:

- **In scope?** A yes/no probability. Stories below 0.5 are dropped
  (`JEV_IN_SCOPE_THRESHOLD`).
- **How newsworthy?** Low (minor, routine or sponsored-feeling), Medium (solid but
  unsurprising) or High (surprising, consequential, weird or genuinely useful).

The in-scope stories are ranked by newsworthiness, and the top ones go to Claude: three per
requested video, and never fewer than 15 (`JEV_POOL_MULTIPLIER`, `JEV_MIN_POOL` in
`script_writer.py`), so Claude still has real choice and room to combine related stories. A
typical morning: 40 candidates in, 18 in scope, all 18 passed to the writer.

Failure handling:
- No key, or no SDK installed: Claude gets every candidate unranked, exactly as before.
- The first Jev call fails: Jev is treated as down for the run, and Claude gets every
  candidate unranked.
- A later single call fails: that story is kept at the bottom of the ranking rather than
  silently dropped.

## How the pipeline works

1. `scraper.py` — scrapes `tldr.tech/{edition}/{date}` (public, no login) for the
   configured editions (`DEFAULT_EDITIONS`).
2. `script_writer.py` — dedupes stories across editions, drops pure-science sections and
   stories covered on any earlier day, has Jev screen and rank the rest (if configured), and
   writes `VIDEOS_PER_DAY` scripts: a hook, 4–6 beats
   (a spoken line plus shorter on-screen card text with one highlighted phrase, or a big
   stat), an outro, and the post title/caption/hashtags.
3. `tts.py` — voices each line with the configured provider (Voicebox, Kokoro, ElevenLabs
   or `say`) after normalizing money, units and known pronunciations.
4. `video_builder.py` — joins the lines, times every card, caption chunk and highlight to
   them, cuts a gameplay stretch, and renders the HyperFrames template.
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

Limits: some guides claim YouTube forces API uploads from unaudited projects to stay private
until the project passes Google's free YouTube API Services audit — in practice, setting
`YOUTUBE_PRIVACY=public` here posted videos as public directly, no audit involved, so that
claim didn't hold for this project. If it does for yours, `--publish` (or YouTube Studio) flips
already-uploaded videos public. While the consent screen is in "Testing", the sign-in expires
every 7 days; rerun `--auth`. "~6 uploads/day" on the default quota is a commonly cited estimate,
not a confirmed or enforced limit — 16 uploads succeeded in one day here with no quota errors;
check Google Cloud Console → APIs & Services → YouTube Data API v3 → Quotas for the real number.

## Running it automatically every day

A `launchd` job runs `run_daily.sh` every morning. See
`com.tldrbrainrot.daily.plist` in `~/Library/LaunchAgents/`.

```bash
launchctl list | grep tldrbrainrot                                     # is it loaded?
launchctl start com.tldrbrainrot.daily                                 # run now
tail -f pipeline.log                                                   # watch a run
launchctl unload ~/Library/LaunchAgents/com.tldrbrainrot.daily.plist   # stop the schedule
```
