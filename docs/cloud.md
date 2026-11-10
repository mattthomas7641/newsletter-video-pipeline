# Running the daily job in the cloud (Google Cloud Run)

The daily run can happen with no laptop involved: a Cloud Scheduler trigger starts a Cloud Run **Job** that runs
`python -m newsletter_video.cloud_run`, which is the normal supervised run (`run_pipeline.main`) wrapped in "load the
saved state, run, save it again". On the Mac nothing changes: `run_daily.sh` still calls `run_pipeline` directly.

```
Cloud Scheduler (noon, your time zone)
   -> Cloud Run Job (container: Python 3.11, Node 22, headless Chrome, ffmpeg, Kokoro voice, faster-whisper)
        reads   : Secret Manager (API keys, YouTube sign-in)
        syncs   : a private Storage bucket (story history, uploads.json, status, config/lineup.json)
        calls   : Anthropic, TypeSafe (Jev), YouTube
        alerts  : an ntfy topic (phone) and Cloud Monitoring (email)
```

## What was made portable

| Mac-only piece | Cloud replacement |
|---|---|
| `mlx-whisper` caption timing | `faster-whisper` (CPU); chosen automatically, or `WHISPER_BACKEND=mlx\|faster` |
| macOS notification | `NOTIFY_WEBHOOK_URL` (ntfy / Discord / Slack); the desktop popup only appears on macOS |
| State on disk | `cloud_state.py` copies `output/` state to and from the bucket (`STATE_BUCKET`); off when unset |
| `.env` and `secrets/` | environment variables from Secret Manager; `cloud_run.py` writes the YouTube files from env |
| launchd + caffeinate | Cloud Scheduler |

Everything else (the render, ffmpeg, the Claude and Jev calls, the upload code) already ran in a container.

## Files

- `Dockerfile`, `requirements-cloud.txt`: the image. Chrome, the Kokoro voice and the Whisper model are baked in, so a
  job never waits on a first-run download.
- `newsletter_video/cloud_run.py`: the entry point (cleans secret env values, writes secret files, pulls state, runs,
  pushes state every 5 minutes and at the end).
- `newsletter_video/cloud_state.py`: the bucket sync (`python -m newsletter_video.cloud_state seed|config|pull`).
- `scripts/cloud_secrets.sh`: copies your keys from `.env` / `secrets/` into Secret Manager, with per-secret access
  for the job's own service account. Run it yourself; values never print.
- `newsletter_video/edition_wait.py`: optional "start when today's newsletters appear" (`EDITION_WAIT_MINUTES`, off by
  default).

## Setup outline

1. A GCP project with billing; enable Cloud Run, Cloud Scheduler, Cloud Storage, Secret Manager, Artifact Registry,
   Cloud Build and Cloud Monitoring.
2. Move the YouTube OAuth app to **In production** (it needs a homepage and a privacy-policy link, see
   `docs/privacy.md`), then re-run `python -m newsletter_video.youtube_upload --auth` once. A token issued in
   "Testing" expires every 7 days; a production one does not.
3. Create a private bucket (uniform access, public access prevention on, a lifecycle rule that deletes
   `.mp4/.png/.jpg` under `output/` after 7 days).
4. Create a dedicated service account for the job with only: log writer, object access on that bucket, and secret
   accessor on its own secrets. A second account with "Cloud Run Invoker" on the job is what Scheduler uses.
5. `gcloud builds submit --tag <region>-docker.pkg.dev/<project>/<repo>/daily .`
   (`.gcloudignore` keeps `.env`, `secrets/` and local output out of the upload; the narrator folder
   `assets/characters/<name>` is shipped even though git ignores it.)
6. `./scripts/cloud_secrets.sh`, then `python -m newsletter_video.cloud_state seed` to upload the story history and
   `lineup.json`.
7. Create the job (4 vCPU / 8 GiB, 2 h task timeout, no retries) pinned to the image digest, with the environment of
   your `.env` (`TTS_PROVIDER`, `KOKORO_VOICE`, `CHARACTER`, `VIDEO_STYLE=avx`, `YOUTUBE_UPLOAD`, `YOUTUBE_PRIVACY`,
   `STATE_BUCKET`, `RUN_DEADLINE_MINUTES`, `CLAUDE_RUN_BUDGET_USD`) and the secrets attached.
8. Create the Scheduler job and the alert policies (a failed execution; a scheduler error).

## Day-to-day

- **How many videos:** edit `lineup.json`, then `python -m newsletter_video.cloud_state config`.
- **Roll back a bad build:** `gcloud run jobs update <job> --image <previous digest>` (note the digest before every
  deploy; the job is pinned to a digest, so a rebuild changes nothing until you update the job).
- **Look at a run:** the job's logs in Cloud Run; the phone alert carries the summary line.

## Cost (measured November 2026)

- Cloud Run at 4 vCPU / 8 GiB: an estimated $0.35 for a day of 5 to 10 videos (about 90 minutes at list price, before the free tier; not yet seen on a bill). A single video rendered in 2 minutes 46 seconds.
- The expensive part is the Claude writer, and about 65% of its billed output is hidden reasoning. Measured per
  finished video: about $0.14 with the original writer prompt and about $0.22 with the lean prompt that adds the
  clarity and honesty checks, so $0.70 to $2.20 a day for 5 to 10 videos.
- Guards: `CLAUDE_RUN_BUDGET_USD` (default 4.0) is a hard stop for one run; the run summary ends with a cost line;
  the job has a task timeout and an in-job deadline. A monthly spend limit in the Anthropic console is the real
  backstop.
