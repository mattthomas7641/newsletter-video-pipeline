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
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .runstate import now_iso, read_json, record_video, write_json_atomic
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
# Uploads go up in resumable chunks (so a dropped connection costs one chunk,
# not the whole 30 MB file) with the client library's own exponential-backoff
# retries on 5xx/429/socket errors, and an HTTP timeout of their own —
# independent of any process-wide socket default.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
UPLOAD_RETRIES = 5
UPLOAD_HTTP_TIMEOUT_SECONDS = 120

log = logging.getLogger("newsletter_video")


class YouTubeAuthError(RuntimeError):
    """The saved sign-in is missing, expired or revoked: nothing can be
    uploaded until the owner reruns `--auth`. Raised (not just logged) so the
    daily run can notify instead of silently skipping every upload."""


# --- metadata ------------------------------------------------------------

def _clean_url(url: str) -> str:
    """Drops tracking parameters (utm_*, share tokens) and HTML entities."""
    parts = urlsplit(html.unescape(url))
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.startswith("utm_") and k not in {"st", "reflink"}]
    return urlunsplit(parts._replace(query=urlencode(query)))


def write_metadata(script: Script, video_path: Path, privacy: str | None = None) -> Path:
    """Writes the post's title/description/tags next to the video, so the
    upload (or a manual post to TikTok) can reuse them later. `privacy`
    pins this one video's visibility regardless of YOUTUBE_PRIVACY (used to
    keep rule-based fallback scripts private)."""
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
        "writer": getattr(script, "writer", ""),
        "script": {  # lets the video be re-rendered exactly
            "title": script.title, "badge": script.badge, "hook": script.hook,
            "beats": [vars(b) for b in script.beats], "outro": script.outro,
        },
    }
    if privacy:
        metadata["privacy"] = privacy
    if getattr(script, "plan", None) is not None:  # explainer style: the scene plan is what was rendered
        from .explainer_writer import plan_dict

        metadata["scene_plan"] = plan_dict(script.plan)
    path = video_path.with_suffix(".json")
    write_json_atomic(path, metadata)
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


def _youtube_client(creds):
    """A client whose HTTP timeout is its own, not whatever
    socket.setdefaulttimeout() happens to be in this process."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=UPLOAD_HTTP_TIMEOUT_SECONDS))
    return build("youtube", "v3", http=http, cache_discovery=False)


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _recent_upload_ids(youtube) -> dict[str, str] | None:
    """{normalized title: video id} for the channel's 50 most recent
    uploads (2 quota units), or None if the sign-in lacks the read scope.
    Used to settle an upload whose result was never recorded."""
    from googleapiclient.errors import HttpError

    try:
        channels = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
        if not channels:
            return {}
        playlist = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = youtube.playlistItems().list(part="snippet", playlistId=playlist, maxResults=50).execute()
    except HttpError as exc:
        log.warning("YouTube: cannot list recent uploads to verify an unrecorded upload (%s)", exc.reason)
        return None
    return {_normalize_title(item["snippet"]["title"]): item["snippet"]["resourceId"]["videoId"]
            for item in items.get("items", [])}


def pending_videos(folder: Path) -> list[Path]:
    """Videos in `folder` with no completed upload record: never attempted,
    or attempted without a recorded result (the process died between the
    insert and writing uploads.json) — those must be verified, not re-posted."""
    record = read_json(folder / "uploads.json", default={}) or {}
    return [v for v in sorted(folder.glob("*.mp4")) if not (record.get(v.name) or {}).get("id")]


def upload_day(day: date, dry_run: bool = False) -> list[str]:
    """Uploads every rendered video for `day` not already uploaded. Returns
    the new YouTube video IDs. YouTube-side problems (quota, a network drop
    mid-file) are logged and recorded, not raised — the videos stay on disk
    and the next run (or a manual run) retries. Raises YouTubeAuthError when
    the sign-in itself is unusable, since nothing can upload until it is fixed."""
    folder = OUTPUT_DIR / day.isoformat()
    record_path = folder / "uploads.json"
    record = read_json(record_path, default={}) or {}
    pending = pending_videos(folder)
    if not pending:
        log.info("YouTube: nothing new to upload for %s", day)
        return []

    default_privacy = os.environ.get("YOUTUBE_PRIVACY", "private")
    publish_at = _publish_times(day, len(pending))
    if dry_run:
        for video, at in zip(pending, publish_at):
            meta = read_json(video.with_suffix(".json"))
            if not meta:
                print(f"\n{video.name}: no metadata (rendered before uploads existed) — would skip")
                continue
            privacy = meta.get("privacy") or default_privacy
            print(f"\n{video.name}  [{privacy}{', publish ' + at if at else ''}]\n"
                  f"TITLE: {meta['title']}\nTAGS: {meta['tags']}\n{meta['description']}")
        return []

    creds = _credentials(interactive=False)
    if creds is None:
        raise YouTubeAuthError("YouTube sign-in missing or expired — run `python -m newsletter_video.youtube_upload --auth`")

    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = _youtube_client(creds)
    recent: dict[str, str] | None | bool = False  # fetched lazily, once
    uploaded = []
    for video, at in zip(pending, publish_at):
        meta = read_json(video.with_suffix(".json"))
        if not meta:
            log.warning("YouTube: no metadata for %s, skipping", video.name)
            continue
        privacy = meta.get("privacy") or default_privacy
        if video.name in record:  # an earlier attempt's outcome is unknown: look before posting again
            if recent is False:
                recent = _recent_upload_ids(youtube)
            if recent is None:
                log.error("YouTube: %s may already be uploaded (no recorded result) and this sign-in can't "
                          "list the channel to check — rerun --auth with the read permission, or fix "
                          "uploads.json by hand", video.name)
                continue
            found = recent.get(_normalize_title(meta["title"]))
            if found:
                record[video.name] = {"id": found, "privacy": record[video.name].get("privacy", privacy),
                                      "publishAt": at, "verified": now_iso()}
                write_json_atomic(record_path, record)
                record_video(folder, video.stem, "uploaded", youtube_id=found)
                log.info("YouTube: %s was already uploaded as %s; recorded, not re-posted", video.name, found)
                continue
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
        # Mark the attempt before it starts: if the process dies after YouTube
        # accepted the file but before the id is written, the next run sees
        # an unfinished attempt and verifies instead of posting a duplicate.
        record[video.name] = {"id": None, "privacy": privacy, "publishAt": at, "started": now_iso()}
        write_json_atomic(record_path, record)
        try:
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=MediaFileUpload(str(video), mimetype="video/mp4",
                                           chunksize=UPLOAD_CHUNK_BYTES, resumable=True),
            )
            response = None
            while response is None:
                _, response = request.next_chunk(num_retries=UPLOAD_RETRIES)
        except HttpError as exc:  # YouTube answered with an error: definitely not uploaded
            log.error("YouTube upload of %s failed: %s", video.name, exc)
            record.pop(video.name, None)
            write_json_atomic(record_path, record)
            record_video(folder, video.stem, "upload_failed", error=f"HTTP {exc.resp.status}: {exc.reason}"[:300])
            if exc.resp.status == 401:
                raise YouTubeAuthError("YouTube rejected the sign-in (401) — rerun --auth") from exc
            # Daily API quota, or the channel's own rolling upload limit: further
            # uploads would fail too; leftovers are retried by the next run.
            if exc.resp.status in (403, 429) or "uploadLimitExceeded" in str(exc.content):
                break
            continue
        except Exception as exc:  # network timeout mid-upload: retry on the next run
            log.error("YouTube upload of %s failed: %s", video.name, exc)
            record_video(folder, video.stem, "upload_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            continue
        video_id = response["id"]
        record[video.name] = {"id": video_id, "privacy": privacy, "publishAt": at}
        write_json_atomic(record_path, record)
        record_video(folder, video.stem, "uploaded", youtube_id=video_id, privacy=privacy)
        uploaded.append(video_id)
        log.info("YouTube: uploaded %s → https://youtube.com/shorts/%s (%s)", video.name, video_id, privacy)
    return uploaded


def days_with_pending_uploads(today: date) -> list[date]:
    """Every dated output folder (up to and including `today`) that still
    holds a video without a completed upload — however old, so a video
    blocked by a limit for a week is still picked up, not forgotten."""
    days = []
    for folder in OUTPUT_DIR.iterdir() if OUTPUT_DIR.is_dir() else []:
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if folder.is_dir() and day <= today and pending_videos(folder):
            days.append(day)
    return sorted(days)


def upload_recent(today: date, days_back: int | None = None) -> list[str]:
    """Uploads anything still pending from earlier days (oldest first), then
    today's videos — so a video blocked by a limit goes up on a later run.
    `days_back` limits how far back to look (default: no limit)."""
    uploaded = []
    for day in days_with_pending_uploads(today):
        if days_back is not None and (today - day).days > days_back:
            continue
        uploaded += upload_day(day)
    return uploaded


def publish_day(day: date, privacy: str = "public") -> list[str]:
    """Sets every recorded upload for `day` to `privacy`. Returns the IDs changed."""
    record_path = OUTPUT_DIR / day.isoformat() / "uploads.json"
    if not record_path.exists():
        log.info("YouTube: no uploads recorded for %s", day)
        return []
    record = read_json(record_path, default={}) or {}
    creds = _credentials(interactive=False)
    if creds is None or SCOPES[2] not in set(creds.scopes or []):
        raise YouTubeAuthError("YouTube: not authorized to change visibility — rerun --auth")

    from googleapiclient.errors import HttpError

    youtube = _youtube_client(creds)
    changed = []
    for name, info in record.items():
        if not info.get("id"):
            continue  # an attempt with no recorded result; nothing to publish
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
    write_json_atomic(record_path, record)
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
    else:
        try:
            if "--publish" in sys.argv:
                publish_day(date.fromisoformat(args[0]) if args else date.today())
            else:
                target = date.fromisoformat(args[0]) if args else date.today()
                upload_day(target, dry_run="--dry-run" in sys.argv)
        except YouTubeAuthError as exc:
            print(exc)
            sys.exit(1)
