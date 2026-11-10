"""Waits for today's newsletters to be published, then lets the run carry on.

The TLDR editions come out at a fixed time each weekday morning (about 6:00 AM Eastern, per third-party
sources; the exact minute varies). A run that starts before that would quietly use the previous edition
(the scraper walks back a few days when today's is missing). With EDITION_WAIT_MINUTES set, the run instead
checks every couple of minutes and starts the moment the editions appear:

  EDITION_WAIT_MINUTES   longest wait (0 = off, the default: the Mac's 7:30 run does not wait)
  EDITION_POLL_SECONDS   how often to look (default 120)
  EDITION_GRACE_MINUTES  once the first edition is out, wait this long for the rest, then go (default 15)

Weekends are never waited for: TLDR does not publish them. A published page is a plain 200; an unpublished
date answers with a redirect to the latest edition, so redirects are not followed.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date

import requests

log = logging.getLogger("newsletter_video")

USER_AGENT = "Mozilla/5.0 (compatible; ByteSizeNews/1.0)"
DEFAULT_POLL_SECONDS = 120
DEFAULT_GRACE_MINUTES = 15


def edition_published(slug: str, day: date, get=requests.get) -> bool:
    """True if tldr.tech/<slug>/<day> is live. Network trouble counts as "not yet"."""
    try:
        resp = get(f"https://tldr.tech/{slug}/{day.isoformat()}", headers={"User-Agent": USER_AGENT},
                   timeout=20, allow_redirects=False)
    except requests.exceptions.RequestException:
        return False
    return resp.status_code == 200 and "404" not in resp.text[:200]


def wait_for_editions(day: date, slugs: list[str], max_wait_s: float, poll_s: float = DEFAULT_POLL_SECONDS,
                      grace_s: float = DEFAULT_GRACE_MINUTES * 60, *, published=edition_published,
                      sleep=time.sleep, clock=time.monotonic) -> dict[str, bool]:
    """Polls until every edition is out, or `grace_s` after the first one appeared, or `max_wait_s` passes.
    Returns {slug: was published}. Never waits on a weekend."""
    if day.weekday() >= 5 or max_wait_s <= 0 or not slugs:
        return {s: False for s in slugs}
    start = clock()
    live: dict[str, float] = {}
    while True:
        for slug in slugs:
            if slug not in live and published(slug, day):
                live[slug] = clock()
                log.info("Edition %s for %s is out (%.0f min after the wait began)", slug, day, (clock() - start) / 60)
        now = clock()
        if len(live) == len(slugs):
            break
        if live and now - min(live.values()) >= grace_s:
            log.info("Going with %s; still missing after the grace period: %s",
                     sorted(live), sorted(set(slugs) - set(live)))
            break
        if now - start >= max_wait_s:
            log.warning("Waited %.0f min for the %s editions; %s still not out", (now - start) / 60, day,
                        sorted(set(slugs) - set(live)) or "none")
            break
        sleep(min(poll_s, max(0.0, max_wait_s - (now - start))))
    return {s: s in live for s in slugs}


def wait_from_env(day: date, slugs: list[str]) -> dict[str, bool] | None:
    """The run's hook: waits per the EDITION_* settings, or does nothing (None) when they are off."""
    minutes = float(os.environ.get("EDITION_WAIT_MINUTES", "0") or 0)
    if minutes <= 0:
        return None
    return wait_for_editions(
        day, slugs, minutes * 60,
        float(os.environ.get("EDITION_POLL_SECONDS", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS),
        float(os.environ.get("EDITION_GRACE_MINUTES", DEFAULT_GRACE_MINUTES) or DEFAULT_GRACE_MINUTES) * 60)
