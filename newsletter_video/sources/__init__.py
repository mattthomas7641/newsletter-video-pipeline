"""Where the day's stories come from, behind one small interface.

The pipeline is a newsletter-to-video pipeline today, but the newsletter
is a detail: everything after fetching (selection, writing, voicing,
rendering, upload) only sees `Edition` and `Story` objects. A source is
anything with `fetch(date) -> list[Edition]`; `SOURCE` in .env picks one
(`tldr`, the default and the only adapter so far).

    from newsletter_video.sources import get_source
    editions = get_source().fetch(date.today())

Adding a source: write a class with a `name` and a `fetch` method, return it
from `get_source` for its name, and document the `SOURCE` value. Nothing
downstream changes.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Protocol

from ..scraper import DEFAULT_EDITIONS, Edition, fetch_editions

DEFAULT_SOURCE = "tldr"


class StorySource(Protocol):
    name: str

    def fetch(self, target_date: date) -> list[Edition]:
        """Every edition (section groups of stories) published for the date."""
        ...


class TldrSource:
    """The TLDR newsletters (tech, ai, ...), scraped from tldr.tech."""

    name = "tldr"

    def __init__(self, slugs: list[str] | None = None):
        self.slugs = list(slugs or DEFAULT_EDITIONS)

    def fetch(self, target_date: date) -> list[Edition]:
        return fetch_editions(slugs=self.slugs, target_date=target_date)

    def describe(self) -> str:
        return f"tldr.tech {self.slugs}"


def get_source(name: str | None = None) -> StorySource:
    """The configured source. Read at call time so a value from .env (loaded
    after import) takes effect; an unknown name fails before any work."""
    name = (name or os.environ.get("SOURCE", DEFAULT_SOURCE)).strip().lower() or DEFAULT_SOURCE
    if name == "tldr":
        return TldrSource()
    raise ValueError(f"SOURCE={name!r} is not a known story source (available: tldr)")
