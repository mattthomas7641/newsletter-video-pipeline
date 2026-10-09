"""Uploads each day's videos to YouTube as Shorts (YouTube Data API v3).

One-time setup (see README "Uploading to YouTube"):
    1. Put your OAuth client file at secrets/youtube_client_secret.json
    2. ./venv/bin/python -m newsletter_video.youtube_upload --auth
       (opens a browser to sign in to the channel; saves secrets/youtube_token.json)

After that, run_pipeline uploads automatically when YOUTUBE_UPLOAD=1 is in .env.
Manual use:
    ./venv/bin/python -m newsletter_video.youtube_upload 2026-10-08            # upload a day
    ./venv/bin/python -m newsletter_video.youtube_upload 2026-10-08 --dry-run  # show what would upload
    ./venv/bin/python -m newsletter_video.youtube_upload 2026-10-08 --publish  # make a day's uploads public

Each video's title/description/tags come from the `<video>.json` sidecar the
pipeline writes next to it. Uploaded video IDs are recorded in
`uploads.json` in the day's folder, so re-running never posts twice.
"""
from __future__ import annotations

import html
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .script_writer import Script

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
SECRETS_DIR = PROJECT_ROOT / "secrets"
CLIENT_SECRET = SECRETS_DIR / "youtube_client_secret.json"
TOKEN = SECRETS_DIR / "youtube_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # show which channel is linked
    "https://www.googleapis.com/auth/youtube",  # change visibility of uploaded videos (--publish)
]

CATEGORY_SCIENCE_TECH = "28"
MAX_TITLE = 100
DEFAULT_TAGS = ["tech news", "ai", "technology", "tldr", "shorts"]

log = logging.getLogger("newsletter_video")


# --- metadata ------------------------------------------------------------

def _clean_url(url: str) -> str:
    """Drops tracking parameters (utm_*, share tokens) and HTML entities."""
    parts = urlsplit(html.unescape(url))
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.startswith("utm_") and k not in {"st", "reflink"}]
    return urlunsplit(parts._replace(query=urlencode(query)))


def write_metadata(script: Script, video_path: Path) -> Path:
    """Writes the post's title/description/tags next to the video, so the
    upload (or a manual post to TikTok) can reuse them later."""
    title = script.title.strip()
    if len(title) + len(" #shorts") <= MAX_TITLE:
        title += " #shorts"
    sources = "\n".join(f"• {s.title} — {_clean_url(s.url)}" for s in script.stories if s.url)
    hashtags = list(dict.fromkeys((script.hashtags or ["technews", "ai"]) + ["shorts"]))
    caption = script.post_caption or f"{script.hook} {script.outro}"
    description = "\n\n".join(part for part in [
        caption,
        f"Sources (via the TLDR newsletter):\n{sources}" if sources else "",
        "Daily tech news in under a minute.",
        " ".join(f"#{h}" for h in hashtags),
    ] if part)
    badge_tags = [w.lower() for w in script.badge.split() if len(w) > 1]
    metadata = {
        "title": title[:MAX_TITLE],
        "caption": caption,  # short post text, e.g. for TikTok
        "description": description[:4900],
        "tags": list(dict.fromkeys(badge_tags + script.hashtags + DEFAULT_TAGS)),
        "stories": [s.title for s in script.stories],  # lets later runs skip covered stories
        "script": {  # lets the video be re-rendered exactly
            "title": script.title, "badge": script.badge, "hook": script.hook,
            "beats": [vars(b) for b in script.beats], "outro": script.outro,
        },
    }
    path = video_path.with_suffix(".json")
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return path


# --- auth --------------------------------------------------------------------

def _credentials(interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN.exists():
        # Use the scopes actually granted (the person may untick the optional
        # read-only one on Google's consent screen); refreshing must not ask for more.
        creds = Credentials.from_authorized_user_file(str(TOKEN))
    # --auth re-asks if the saved sign-in predates a newly added permission.
    missing = interactive and creds is not None and not set(SCOPES) <= set(creds.scopes or [])
    if creds and creds.valid and not missing:
        return creds
    if creds and creds.expired and creds.refresh_token and not missing:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json())
            return creds
        except Exception as exc:  # revoked, or expired after 7 days in "Testing" mode
            log.warning("YouTube token refresh failed (%s)", exc)
    if not interactive:
        return None
    if not CLIENT_SECRET.exists():
        raise FileNotFoundError(f"Put your OAuth client file at {CLIENT_SECRET}")
    from google_auth_oauthlib.flow import InstalledAppFlow

    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # accept a partial grant
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    SECRETS_DIR.mkdir(exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return creds


# --- upload ------------------------------------------------------------------

def _publish_times(day: date, count: int) -> list[str | None]:
    """YOUTUBE_SCHEDULE="12:00,16:00,20:00" spreads the day's videos across
    those local times (needs privacy "private"; YouTube flips them public)."""
    slots = [t.strip() for t in os.environ.get("YOUTUBE_SCHEDULE", "").split(",") if t.strip()]
    times: list[str | None] = []
    for i in range(count):
        if i >= len(slots):
            times.append(None)
            continue
        hour, minute = (int(x) for x in slots[i].split(":"))
        local = datetime(day.year, day.month, day.day, hour, minute).astimezone()
        if local < datetime.now().astimezone():
            local += timedelta(days=1)
        times.append(local.isoformat())
    return times


def upload_day(day: date, dry_run: bool = False) -> list[str]:
    """Uploads every rendered video for `day` not already uploaded. Returns
    the new YouTube video IDs. Never raises for YouTube problems — the
    videos stay on disk and the next run (or a manual run) retries."""
    folder = OUTPUT_DIR / day.isoformat()
    videos = sorted(folder.glob("*.mp4"))
    record_path = folder / "uploads.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    pending = [v for v in videos if v.name not in record]
    if not pending:
        log.info("YouTube: nothing new to upload for %s", day)
        return []

    privacy = os.environ.get("YOUTUBE_PRIVACY", "private")
    publish_at = _publish_times(day, len(pending))
    if dry_run:
        for video, at in zip(pending, publish_at):
            if not video.with_suffix(".json").exists():
                print(f"\n{video.name}: no metadata (rendered before uploads existed) — would skip")
                continue
            meta = json.loads(video.with_suffix(".json").read_text())
            print(f"\n{video.name}  [{privacy}{', publish ' + at if at else ''}]\n"
                  f"TITLE: {meta['title']}\nTAGS: {meta['tags']}\n{meta['description']}")
        return []

    creds = _credentials(interactive=False)
    if creds is None:
        log.error("YouTube: not authorized — run `python -m newsletter_video.youtube_upload --auth`")
        return []

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    uploaded = []
    for video, at in zip(pending, publish_at):
        meta_path = video.with_suffix(".json")
        if not meta_path.exists():
            log.warning("YouTube: no metadata for %s, skipping", video.name)
            continue
        meta = json.loads(meta_path.read_text())
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if at and privacy == "private":
            status["publishAt"] = at
        body = {
            "snippet": {
                "title": meta["title"],
                "description": meta["description"],
                "tags": meta["tags"],
                "categoryId": CATEGORY_SCIENCE_TECH,
            },
            "status": status,
        }
        try:
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=MediaFileUpload(str(video), mimetype="video/mp4", chunksize=-1, resumable=True),
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
        except HttpError as exc:
            log.error("YouTube upload of %s failed: %s", video.name, exc)
            if exc.resp.status in (403, 429):  # quota exhausted or forbidden: stop for today
                break
            continue
        except Exception as exc:  # network timeout mid-upload: retry on the next run
            log.error("YouTube upload of %s failed: %s", video.name, exc)
            continue
        video_id = response["id"]
        record[video.name] = {"id": video_id, "privacy": privacy, "publishAt": at}
        record_path.write_text(json.dumps(record, indent=2))
        uploaded.append(video_id)
        log.info("YouTube: uploaded %s → https://youtube.com/shorts/%s (%s)", video.name, video_id, privacy)
    return uploaded


def publish_day(day: date, privacy: str = "public") -> list[str]:
    """Sets every recorded upload for `day` to `privacy`. Returns the IDs changed."""
    record_path = OUTPUT_DIR / day.isoformat() / "uploads.json"
    if not record_path.exists():
        log.info("YouTube: no uploads recorded for %s", day)
        return []
    record = json.loads(record_path.read_text())
    creds = _credentials(interactive=False)
    if creds is None or SCOPES[2] not in set(creds.scopes or []):
        log.error("YouTube: not authorized to change visibility — rerun --auth")
        return []

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    changed = []
    for name, info in record.items():
        try:
            youtube.videos().update(part="status", body={
                "id": info["id"],
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            }).execute()
        except HttpError as exc:
            log.error("YouTube: could not set %s to %s: %s", name, privacy, exc.reason)
            continue
        info["privacy"] = privacy
        changed.append(info["id"])
        log.info("YouTube: %s is now %s", name, privacy)
    record_path.write_text(json.dumps(record, indent=2))
    return changed


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--auth" in sys.argv:
        creds = _credentials(interactive=True)
        granted = set(creds.scopes or [])
        if SCOPES[0] not in granted:
            print("Upload permission wasn't granted — rerun --auth and allow \"Manage your YouTube videos\".")
            sys.exit(1)
        if SCOPES[1] not in granted:
            print("Authorized for uploads. (Channel name not shown: the optional "
                  "\"View your YouTube account\" permission was not granted.)")
            sys.exit(0)
        from googleapiclient.discovery import build

        channels = build("youtube", "v3", credentials=creds, cache_discovery=False).channels()
        items = channels.list(part="snippet", mine=True).execute().get("items", [])
        if items:
            print(f"Authorized: uploads will go to the channel \"{items[0]['snippet']['title']}\"")
        else:
            print("Authorized, but this Google account has no YouTube channel yet — create one, then rerun --auth.")
    elif "--publish" in sys.argv:
        publish_day(date.fromisoformat(args[0]) if args else date.today())
    else:
        target = date.fromisoformat(args[0]) if args else date.today()
        upload_day(target, dry_run="--dry-run" in sys.argv)
