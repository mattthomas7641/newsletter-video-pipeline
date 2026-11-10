"""Waiting for the day's newsletters to drop (edition_wait.py)."""
from __future__ import annotations

from datetime import date

import requests

from newsletter_video import edition_wait as ew

TUESDAY = date(2026, 11, 10)
SATURDAY = date(2026, 11, 7)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(published_at: dict[str, float], max_wait=3 * 3600, poll=120, grace=900, day=TUESDAY):
    clock = Clock()
    result = ew.wait_for_editions(day, list(published_at), max_wait, poll, grace,
                                  published=lambda slug, d: clock.t >= published_at[slug],
                                  sleep=clock.sleep, clock=clock)
    return result, clock.t


def test_starts_as_soon_as_every_edition_is_out():
    result, t = run({"tech": 600, "ai": 700})
    assert result == {"tech": True, "ai": True}
    assert 700 <= t <= 700 + 120  # within one poll of the last edition


def test_does_not_wait_forever_for_a_late_edition():
    result, t = run({"tech": 600, "ai": 600, "founders": 99999})
    assert result == {"tech": True, "ai": True, "founders": False}
    assert 600 + 900 <= t <= 600 + 900 + 120  # the grace period after the first one


def test_gives_up_at_the_longest_wait_when_nothing_appears():
    result, t = run({"tech": 99999, "ai": 99999}, max_wait=1800)
    assert result == {"tech": False, "ai": False}
    assert t == 1800


def test_never_waits_on_a_weekend_or_when_off():
    assert run({"tech": 600}, day=SATURDAY) == ({"tech": False}, 0)
    assert run({"tech": 600}, max_wait=0) == ({"tech": False}, 0)


def test_already_published_returns_immediately():
    assert run({"tech": 0, "ai": 0}) == ({"tech": True, "ai": True}, 0)


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def test_a_redirect_or_404_page_is_not_a_published_edition():
    assert ew.edition_published("ai", TUESDAY, get=lambda *a, **k: Resp(307)) is False
    assert ew.edition_published("ai", TUESDAY, get=lambda *a, **k: Resp(200, "<h1>404 not found</h1>")) is False
    assert ew.edition_published("ai", TUESDAY, get=lambda *a, **k: Resp(200, "<html>TLDR AI</html>")) is True


def test_redirects_are_not_followed_and_network_errors_mean_not_yet():
    seen = {}

    def get(url, **kw):
        seen.update(kw)
        raise requests.exceptions.ConnectionError("down")

    assert ew.edition_published("ai", TUESDAY, get=get) is False
    assert seen["allow_redirects"] is False


def test_env_hook_is_off_by_default(monkeypatch):
    monkeypatch.delenv("EDITION_WAIT_MINUTES", raising=False)
    assert ew.wait_from_env(TUESDAY, ["tech"]) is None
