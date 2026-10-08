# Newsletter-to-Video Pipeline

An unattended daily pipeline that turns the [TLDR](https://tldr.tech) newsletters into a
narrated, captioned 1080×1920 short-form video, ready before the day starts.

```
tldr.tech (4 editions) ──▶ scrape ──▶ LLM script ──▶ text-to-speech ──▶ composite ──▶ MP4
   tech · ai ·              ~50          (Claude)       (say /           background, captions,
   marketing · founders     stories       8 beats        ElevenLabs)      character, intro/outro
```

- **Runs itself:** a macOS `launchd` job renders the day's video at 7:00 AM with no
  manual steps (about 10 minutes per render), logging every stage.
- **LLM in the loop, with a fallback:** Claude condenses roughly 50 stories into 8
  narration beats. Without an API key, a template writer keeps the pipeline running.
- **Pluggable parts:** text-to-speech (free on-device voice or ElevenLabs), background
  footage and character overlays are all swappable without code changes.
- **Rights-aware by default:** the defaults use only generated or free assets. You supply
  your own licensed voice and footage, and the tool never posts anything publicly.

## Content rights

Popular short-form formats often borrow a famous character's voice or mobile-game
footage. Both are someone else's IP, so this repository ships none of it. Anything you
add to `assets/` is your responsibility to license before posting publicly.

## Quick start

```bash
cd newsletter-video-pipeline
./venv/bin/python -m newsletter_video.run_pipeline          # renders today's video
./venv/bin/python -m newsletter_video.run_pipeline 2026-09-22  # a specific date
```

Output lands in `output/YYYY-MM-DD.mp4`. Logs go to `pipeline.log`.

The Python environment (`venv/`) is a self-contained conda env with ffmpeg
already installed — you don't need Homebrew or a system ffmpeg. It was
built with:

```bash
conda create -p ./venv python=3.11 ffmpeg -c conda-forge
./venv/bin/python -m pip install -r requirements.txt
```

## Customizing voice, footage and overlays

Right now it runs on free defaults so it works out of the box:
- **Voice**: macOS's built-in `say` command (robotic but free/local).
- **Background**: a generated animated gradient (no copyright risk, but not
  licensed gameplay footage).
- **Character**: none — just captions.

To upgrade each piece:

### 1. Custom voice
Copy `.env.example` to `.env`, then set:
```
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your key
VOICE_ID=the voice's id
```
You'll need your own ElevenLabs account. Find or add a voice in your Voice
Library yourself — this script won't do that part for you (it's the one
step with real IP exposure, so it's worth being deliberate about it).

### 2. Real gameplay background
Two ways to get a clip into `assets/background/`:

- **Download one**: `./venv/bin/python -m newsletter_video.fetch_background "<url>"`
  (works with YouTube and most sites yt-dlp supports). You're responsible
  for having the right to use whatever you point it at.
- **Drop in your own file** directly.

The video builder picks a random `.mp4` from that folder per run, loops it
to fill each segment's duration, and center-crops it to 1080x1920. Longer
clips (a full uninterrupted gameplay run) work best since it just loops
from the start otherwise.

### 3. Character overlay — cutout avatar
Drop any number of transparent PNGs (different poses/expressions of the
same character) into `assets/character/`. One is shown per story segment
with a gentle bounce; the pool is shuffled once per render and cycled
through so you get a different pose each segment instead of one static
image the whole video — the look of typical short-form explainer clips. An
empty `assets/character/` folder just skips the overlay.

**Must be `.png` or `.jpg`** — `.webp` is silently ignored (moviepy's
`ImageClip` doesn't load it). If a cutout you download turns out to be a
`.webp` with a baked-in near-white background instead of real transparency
(common with stock clipart sites), convert it with something like:
```python
from PIL import Image, ImageDraw
import numpy as np
img = Image.open("in.webp").convert("RGB")
work = img.copy()
for seed in [(0,0),(img.width-1,0),(0,img.height-1),(img.width-1,img.height-1)]:
    ImageDraw.floodfill(work, seed, (255,0,255), thresh=100)
is_bg = np.all(np.array(work) == [255,0,255], axis=-1)
alpha = np.where(is_bg, 0, 255).astype(np.uint8)
Image.fromarray(np.dstack([np.array(img), alpha])).save("out.png")
```

## How the pipeline works

1. `newsletter_video/scraper.py` — scrapes `tldr.tech/{edition}/{date}` (public,
   no login) for the configured editions (default: tech, ai, marketing,
   founders — edit `DEFAULT_EDITIONS` to change).
2. `newsletter_video/script_writer.py` — turns headlines into short narration
   beats. Set `ANTHROPIC_API_KEY` in `.env` to have Claude punch up the
   phrasing; otherwise a simple template is used.
3. `newsletter_video/tts.py` — renders each beat's narration to audio.
4. `newsletter_video/video_builder.py` — composites background + character +
   captions + audio into one vertical MP4 per beat, then stitches them with
   an intro/outro card.
5. `newsletter_video/run_pipeline.py` — runs the above in order for a given date.

## Running it automatically every day

A `launchd` job is set up to run the pipeline every morning. See
`com.tldrbrainrot.daily.plist` in `~/Library/LaunchAgents/`.

Useful commands:
```bash
# check whether it's loaded
launchctl list | grep tldrbrainrot

# trigger it manually right now (without waiting for the schedule)
launchctl start com.tldrbrainrot.daily

# view logs from the scheduled run
tail -f launchd.log launchd.err.log

# stop the daily schedule entirely
launchctl unload ~/Library/LaunchAgents/com.tldrbrainrot.daily.plist
```

**Scope note**: the automated job only renders and saves the video file
locally — it does not post to TikTok, YouTube, or anywhere else. Posting
would need separate account setup and your explicit go-ahead each time.
