"""Scrapes TLDR newsletter editions from their public web archive (tldr.tech).

No login or API key needed — each edition's daily page is public and
server-rendered, so a plain GET request returns the full story list.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

# The mainstream TLDR editions. Add/remove slugs as you like — each one
# maps directly to a path on tldr.tech, e.g. "ai" -> https://tldr.tech/ai
DEFAULT_EDITIONS = ["tech", "ai", "marketing", "founders"]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) newsletter-video-pipeline/1.0"
MAX_LOOKBACK_DAYS = 5  # how many days to walk back if today isn't published yet

# The daily run fires via launchd at a fixed wall-clock time, which can land
# while the machine is still asleep or mid-wake (Wi-Fi not yet reassociated,
# DNS not yet resolving). Retrying with backoff rides out that window instead
# of failing the whole day's video outright.
FETCH_ATTEMPTS = 5
FETCH_BACKOFF_SECONDS = 8


@dataclass
class Story:
    edition: str
    section: str
    title: str
    summary: str
    url: str
    read_time: str | None = None


@dataclass
class Edition:
    slug: str
    date_str: str
    headline: str
    stories: list[Story] = field(default_factory=list)


def _fetch(url: str) -> str | None:
    last_exc: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resp.status_code != 200:
                return None
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(FETCH_BACKOFF_SECONDS * (attempt + 1))
    raise last_exc


def _parse_read_time(heading_text: str) -> tuple[str, str | None]:
    """Splits "Some headline (3 minute read)" into (title, "3 minute read")."""
    match = re.search(r"\(([^()]*read[^()]*)\)\s*$", heading_text)
    if not match:
        return heading_text.strip(), None
    title = heading_text[: match.start()].strip()
    return title, match.group(1).strip()


def parse_edition_html(html: str, slug: str, date_str: str) -> Edition:
    soup = BeautifulSoup(html, "html.parser")

    headline_tag = soup.find("h1") or soup.find("h2")
    headline = headline_tag.get_text(strip=True) if headline_tag else slug

    edition = Edition(slug=slug, date_str=date_str, headline=headline)

    section_name = "General"
    for el in soup.find_all(["h3", "article"]):
        if el.name == "h3":
            if el.find_parent("article") is not None:
                continue  # this is a story headline, handled via its <article>
            text = el.get_text(strip=True)
            if text:
                section_name = text
            continue

        # el.name == "article"
        heading = el.find(["h1", "h2", "h3", "h4"])
        if not heading:
            continue
        raw_title = heading.get_text(strip=True)
        if not raw_title or "(Sponsor)" in raw_title:
            continue  # skip sponsor placements / empty headings

        link = el.find("a", href=True)
        url = link["href"] if link else ""

        summary_tag = el.find("div", class_="newsletter-html") or el.find("p")
        summary = summary_tag.get_text(strip=True) if summary_tag else ""

        title, read_time = _parse_read_time(raw_title)
        if not title:
            continue

        edition.stories.append(
            Story(
                edition=slug,
                section=section_name,
                title=title,
                summary=summary,
                url=url,
                read_time=read_time,
            )
        )

    return edition


def fetch_edition(slug: str, target_date: date | None = None) -> Edition | None:
    """Fetches one edition, walking back a few days if today isn't published yet."""
    target_date = target_date or date.today()
    for offset in range(MAX_LOOKBACK_DAYS):
        d = target_date - timedelta(days=offset)
        date_str = d.isoformat()
        html = _fetch(f"https://tldr.tech/{slug}/{date_str}")
        if html and "404" not in html[:200]:
            edition = parse_edition_html(html, slug, date_str)
            if edition.stories:
                return edition
    return None


def fetch_editions(
    slugs: list[str] | None = None, target_date: date | None = None
) -> list[Edition]:
    slugs = slugs or DEFAULT_EDITIONS
    editions = []
    for slug in slugs:
        edition = fetch_edition(slug, target_date)
        if edition:
            editions.append(edition)
    return editions


if __name__ == "__main__":
    import sys

    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    for edition in fetch_editions(target_date=d):
        print(f"\n=== {edition.slug.upper()} — {edition.date_str} — {edition.headline} ===")
        for story in edition.stories:
            print(f"[{story.section}] {story.title} ({story.read_time})")
            print(f"    {story.summary[:120]}")
