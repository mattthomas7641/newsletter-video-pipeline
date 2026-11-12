[Back to the README](../README.md)

# Uploading to YouTube Shorts

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
every 7 days; rerun `--auth`. Moving the OAuth app to "In production" (it needs a homepage and a privacy-policy link;
[docs/privacy.md](privacy.md) is one) and re-authorizing once gives a token that does not expire weekly.
`YOUTUBE_PRIVACY=public` with no `YOUTUBE_SCHEDULE` publishes each video the moment it is uploaded, and the daily run
uploads each video while the next one renders (`STREAM_UPLOADS=0` goes back to uploading at the end);
`YOUTUBE_SCHEDULE=asap` publishes five minutes after each upload instead. "~6 uploads/day" on the default quota is a commonly cited estimate,
not a confirmed or enforced limit — 16 uploads succeeded in one day here with no quota errors;
check Google Cloud Console → APIs & Services → YouTube Data API v3 → Quotas for the real number.
