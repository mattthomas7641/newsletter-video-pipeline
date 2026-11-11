# A second channel from the same stories (no extra Claude calls)

A second YouTube channel can reuse the first channel's finished videos: the same stories and the same scripts, read
by a different narrator in a different voice, with its own sign-in, history and schedule. Nothing is written again,
so the second channel adds no Claude cost; it only needs the first channel's videos to exist.

## How it works

`python -m newsletter_video.mirror_channel [day] [--count 5] [--list] [--render]`

1. Reads the day's finished avatar-explainer videos (their saved scene lists) from `MIRROR_SOURCE`: the first
   channel's bucket (`gs://<bucket>/output`, needs `gsutil` signed in) or a local output folder.
2. Ranks them with Jev (how newsworthy, and would a well-informed follower of AI and tech want to know it; the same
   questions the story screen asks, nearly free) and keeps the best `--count` (default 5, `MIRROR_COUNT`). Stories
   this channel already made are skipped. Without Jev the first channel's own order is kept.
3. Writes `output/<day>/plan.json` with this channel's narrator (`CHARACTER`) and, with `--render`, renders and uploads it
   like `render_plan --final`.

"Most likely to do well" is a proxy: on the first channel's own history Jev's scores did not predict views.

## Setting it up as a separate copy

The cleanest isolation is a second clone of the project in its own folder, sharing the first one's `venv`:

```bash
git clone <this repo> "<project> Peter" && cd "<project> Peter"
ln -s "<first project>/venv" venv
mkdir -p assets/characters secrets output
cp -R "<first project>/assets/characters/<narrator>" assets/characters/
cp "<first project>/secrets/youtube_client_secret.json" secrets/
cp "<first project>/.env" .env && chmod 600 .env     # then edit it, see below
```

In this copy's `.env`: `CHARACTER=<narrator>`, `VIDEO_STYLE=avx`, `TTS_PROVIDER` for that narrator's voice, `MIRROR_SOURCE`,
`MIRROR_COUNT`, `AVX_BACKGROUND` (below), `YOUTUBE_UPLOAD=0` until the channel is signed in, then `1`. Sign in once
with `./venv/bin/python -m newsletter_video.youtube_upload --auth` and pick the new channel. `run_mirror.sh` is the
entry point for a launchd job; schedule it after the first channel's run has finished.

The narrator's voice decides where this can run. The Kokoro voices run anywhere, including the cloud job. A Voicebox
clone only runs on a Mac with the Voicebox app (slow and prone to stalls on 8 GB of RAM).

## Background: colour or gameplay

`AVX_BACKGROUND` in `.env` switches the avatar explainer's background for that channel:

- unset or `plain`: the colour grounds (the first channel's look).
- `gameplay`: muted footage from `assets/background/*.mp4` behind **the same animations**. The scenes and graphics are
  unchanged; each scene's colour ground goes transparent, a dark shade sits over the footage, and the type and cards
  use a light palette. With no clip in `assets/background/` it stays plain.
