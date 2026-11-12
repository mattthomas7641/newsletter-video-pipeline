[Back to the README](../README.md)

# When things fail

The run is unattended, so every outcome is reported and every stage has a time limit.

**You always hear how it ended.** A macOS notification is shown at the end of every run:
"Pipeline finished" with a one-line summary (videos made, uploaded, minutes, anything that
failed or is still awaiting upload), or "Pipeline failed" / "Pipeline killed (watchdog)" with
the reason. Set `NOTIFY_WEBHOOK_URL` to also get it on your phone: an
[ntfy.sh](https://ntfy.sh) topic URL (plain text), or a Discord or Slack incoming-webhook URL
(JSON; picked from the host, or force with `NOTIFY_WEBHOOK_FORMAT=text|json`).
`NOTIFY_MACOS=0` turns the desktop notification off. Everything is also in `pipeline.log`, and
`python -m newsletter_video.status [date]` prints a day's per-video record
(`output/<date>/status.json`).

**What each failure does:**

| Failure | What happens |
|---|---|
| Bad `.env` (unknown `TTS_PROVIDER`, Voicebox without a profile id, uploads on without a sign-in, low disk, no `npx`) | Fails in the first second with one message listing every problem; nothing is rendered |
| No network at run time (asleep, DNS not up) | Waits up to 5 min, the scraper retries with backoff, then the run fails with a notification; rerun later |
| TLDR page empty or changed | "No stories scraped": fails before any TTS/render work |
| Jev (TypeSafe) down, rate-limited or erroring | Claude gets every candidate unranked (first call fails) or that story unranked (later call) |
| Claude API error, refusal, `max_tokens`, bad JSON, a video that violates the schema or is far too long/short | Bad videos are dropped; if none are left, the avatar format falls back to the rule-based writer, while the explainer and avx formats end the run with "No stories selected" (for them the rule-based writer is used only when Claude is unavailable). With `YOUTUBE_UPLOAD=1` those fallback scripts are **not published**: by default (`FALLBACK_UPLOAD=skip`) the run stops with a notification and nothing is rendered, so the stories are tried again tomorrow; `private` renders and uploads them private for review; `public` is the old behaviour |
| Voicebox not running / crashed between runs | Relaunched (`open -a Voicebox`), waits up to 90 s for `/health` |
| Voicebox wedged (generation stuck at "generating", heartbeats forever) | Each clip has a wall-clock limit (`VOICEBOX_GENERATION_TIMEOUT`, 420 s). On expiry the app is quit via osascript, relaunched, and the clip retried once; then that video fails |
| Voicebox not installed / won't start | Fatal for the day immediately (no 90 s wait per video) |
| TTS returns silence, an empty file or a clip far too short for its text | That video fails (never rendered or uploaded) |
| mlx-whisper missing or erroring | Word timing falls back to a proportional estimate (as before) |
| ffmpeg error, HyperFrames/`npx` failure, Chrome crash, out of memory | Two render attempts, each capped at `RENDER_TIMEOUT_SECONDS` (20 min; the whole process tree is killed on timeout); then that video fails and the rest of the day continues. Renders go to `.partial/` and are moved into place only when complete |
| One video fails for any reason | Recorded in `status.json` with the error; the other videos are still made and uploaded; the summary says which failed |
| Disk full | Caught at startup (needs 3 GB free); mid-run it surfaces as a failed video or a failed run |
| Process crash / `kill` / watchdog at video 7 | Videos 1–6 already have their `.json` sidecars (written the moment each render finishes), so they upload and their stories count as covered. Rerun the same date: it resumes, making only the missing videos, numbered after the highest number already used |
| Run exceeds `RUN_DEADLINE_MINUTES` (default 240) | A watchdog notifies, kills every child process and exits; the next morning is not blocked |
| Two runs overlap (yesterday's still going, or a manual run during the scheduled one) | The second refuses to start (`.pipeline.lock`, `flock`) and notifies which pid holds it. The lock is released by the kernel when the holder exits, so there is never a stale lock to clean up: if the holder is stuck, kill it |
| YouTube sign-in expired or revoked (incl. a 401 mid-run) | "YouTube sign-in needs attention" notification; videos stay on disk and upload on the next run after `--auth` |
| Quota or `uploadLimitExceeded` | The batch stops; the videos stay pending and are picked up by a later run, unless they are older than `UPLOAD_MAX_AGE_DAYS` (default 2; `off` removes the limit) |
| Upload interrupted mid-file | Resumable 8 MB chunks with exponential-backoff retries on 5xx/429/socket errors and a 120 s HTTP timeout of their own (the 5 s network-probe timeout no longer leaks into uploads) |
| The process dies between YouTube accepting a video and `uploads.json` being written | The attempt is marked in `uploads.json` *before* the insert. On the next run that video is looked up among the channel's recent uploads (needs the read-only scope from `--auth`) and recorded, not re-posted; without that scope it is left alone with an error asking you to check |
| `launchctl stop` / SIGTERM | Reported as a failed run with a notification; state on disk stays consistent |

**Resuming.** Rerun the same date (`./venv/bin/python -m newsletter_video.run_pipeline
YYYY-MM-DD`): already-made videos are kept and only the shortfall to `VIDEOS_PER_DAY` is made;
pending uploads from the last `UPLOAD_MAX_AGE_DAYS` days (default 2) are retried. `python -m newsletter_video.youtube_upload YYYY-MM-DD`
retries just the uploads.

**Tests.** `./venv/bin/python -m pytest tests` runs the fault-injection suite offline (a fake
Voicebox server that wedges, stubbed Claude/YouTube clients, stubbed renders): no paid API,
no uploads, no real renders.
