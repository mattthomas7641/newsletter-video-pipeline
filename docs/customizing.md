[Back to the README](../README.md)

# Customizing

## Changing the look
The composition is `newsletter_video/hf_template/index.html`. To preview it with sample
data, put any `voice.wav` in `newsletter_video/hf_template/assets/` (gitignored), then:
```bash
cd newsletter_video/hf_template && npx hyperframes preview
```
The pipeline passes each video in as the `story` composition variable (badge, hook words,
cards with highlight and timing, captions), so the template works the same in
Studio and in automated renders.

## The narrator character
A character is an avatar image, optionally a cloned voice, and optionally a writing
persona, stored in `assets/characters/<name>/` (gitignored, so it never goes into the repo).
The composition places the avatar at the bottom-left of the frame with a slight idle bob,
shrinks wide images so they don't crowd the cards, and keeps cards and captions clear of it.

```bash
# clone a voice from a clean clip and add the avatar (a transparent-background PNG works best)
python -m newsletter_video.characters add "My Character" --avatar me.png --voice-audio me.wav \
  --persona "upbeat, loves puns" --use
python -m newsletter_video.characters list
python -m newsletter_video.characters say my-character "Does this sound right?"
python -m newsletter_video.characters use none      # back to the original setup
```

- **Voice:** `--voice-audio` clones a voice in Voicebox (the app must be installed; it's started
  for you). Use 5-30 seconds of clean, single-speaker speech, with no background music, laughs or
  long pauses; those leak into the cloned voice as odd pauses and noise. Pass `--voice-audio` more
  than once for several clips, or `--profile-id` to reuse a voice you already made in Voicebox.
  Without either, the character uses your default `TTS_PROVIDER` voice.
- **Persona:** `--persona` tells the script writer how this narrator talks. It changes tone and
  wording only; the facts still come from the stories.
- **Selecting:** `CHARACTER=<name>` in `.env` (what `use` sets). With it blank, the pipeline uses
  the first image in `assets/character/` and `VOICEBOX_PROFILE_ID`, as before.
- Only use avatars and voices you have the rights to.

## Gameplay background
Set `BACKGROUND_STYLE=none` to skip it entirely. Otherwise, drop any vertical gameplay `.mp4` into `assets/background/` (or download one with
`./venv/bin/python -m newsletter_video.fetch_background "<url>"`). The first run converts
each clip once to a light 1080×1920 copy in `assets/background/.cache/`, so later cuts take
seconds instead of re-decoding the original. Each video gets a
different stretch of a randomly chosen clip (seeded by date, so re-runs match), cut to the
video's exact length, muted and darkened. With the folder empty, videos use a graph-paper
background.

## Custom voice
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
  to another voice, so a day's videos never mix two narrators. If it accepts a clip but never
  finishes it (a known wedge), it is quit and relaunched once (see
  [When things fail](failure-handling.md)).

**ElevenLabs (cloud).** Set `TTS_PROVIDER=elevenlabs` with `ELEVENLABS_API_KEY` and
`VOICE_ID` to use a voice from your own ElevenLabs account.
