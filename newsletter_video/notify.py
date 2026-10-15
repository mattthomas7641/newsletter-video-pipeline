"""Failure/success notifications for the unattended daily run.

By default a macOS notification is shown (via osascript — works from a
launchd user agent). Set NOTIFY_WEBHOOK_URL to also POST each notification
to a webhook: ntfy.sh (plain text with a Title header), Discord
(`{"content": ...}`) or Slack (`{"text": ...}`); the format is picked from
the URL's host, or forced with NOTIFY_WEBHOOK_FORMAT=text|json. Set
NOTIFY_MACOS=0 to silence the desktop notification.

Notifications never raise: a failing notifier must not take the pipeline
down with it, and this module has no third-party dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.request

log = logging.getLogger("newsletter_video")

WEBHOOK_TIMEOUT_SECONDS = 15
APP_NAME = "ByteSize News"


def _macos_notification(title: str, message: str) -> None:
    script = 'display notification {msg} with title {app} subtitle {title}'.format(
        msg=json.dumps(message[:300]), app=json.dumps(APP_NAME), title=json.dumps(title[:100]),
    )
    subprocess.run(["osascript", "-e", script], check=False, timeout=15,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _webhook_format(url: str) -> str:
    forced = os.environ.get("NOTIFY_WEBHOOK_FORMAT", "").strip().lower()
    if forced in ("text", "json"):
        return forced
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    if "discord" in host or "slack" in host:
        return "json"
    return "text"  # ntfy.sh and most generic receivers take a plain body


def _webhook(url: str, title: str, message: str, ok: bool) -> None:
    if _webhook_format(url) == "json":
        text = f"{title}\n{message}"
        body = json.dumps({"text": text, "content": text[:1900], "title": title,
                           "message": message, "status": "ok" if ok else "failed"}).encode()
        headers = {"Content-Type": "application/json"}
    else:
        body = message.encode()
        headers = {"Content-Type": "text/plain; charset=utf-8", "Title": title.encode("ascii", "replace").decode(),
                   "Priority": "default" if ok else "high", "Tags": "white_check_mark" if ok else "rotating_light"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS):
        pass


def notify(title: str, message: str, ok: bool = False) -> None:
    """Shows `title`/`message` wherever the owner has asked to see it. Logs
    at the matching level too, so pipeline.log has the same line."""
    (log.info if ok else log.error)("NOTIFY %s: %s", title, message)
    if os.environ.get("NOTIFY_MACOS", "1") != "0":
        try:
            _macos_notification(title, message)
        except Exception as exc:  # no GUI session, osascript missing...
            log.warning("macOS notification failed: %s", exc)
    url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    if url:
        try:
            _webhook(url, title, message, ok)
        except Exception as exc:
            log.warning("Webhook notification failed: %s", exc)
