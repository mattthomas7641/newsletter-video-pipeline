"""Turns scraped TLDR stories into short narration beats for the video.

Default mode is template-based (no API key needed). If ANTHROPIC_API_KEY is
set in the environment, beats are optionally punched up with Claude for
punchier, more "brainrot" phrasing — this is purely additive; everything
still works without it.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass

from .scraper import Edition, Story

MAX_STORIES_PER_VIDEO = 8

HOOKS = [
    "Yo, you're NOT gonna believe this —",
    "Okay wait, this is actually crazy —",
    "Bro I need you to sit down for this one —",
    "Real quick, huge news —",
    "Ok so apparently —",
    "This just happened and it's wild —",
]


@dataclass
class Beat:
    text: str
    caption: str
    source_title: str


def _template_beat(story: Story) -> Beat:
    hook = random.choice(HOOKS)
    summary = story.summary.strip()
    if len(summary) > 220:
        summary = summary[:217].rsplit(" ", 1)[0] + "..."
    narration = f"{hook} {story.title}. {summary}"
    caption = story.title
    return Beat(text=narration, caption=caption, source_title=story.title)


def _select_stories(editions: list[Edition], limit: int) -> list[Story]:
    all_stories = [s for e in editions for s in e.stories]
    random.shuffle(all_stories)
    return all_stories[:limit]


def _maybe_llm_polish(beats: list[Beat]) -> list[Beat]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return beats
    try:
        import anthropic
    except ImportError:
        return beats

    client = anthropic.Anthropic(api_key=api_key)
    polished = []
    for beat in beats:
        try:
            resp = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=150,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Rewrite this tech-news blurb as one punchy, fast, "
                            "meme-inflected sentence for a brainrot-style TikTok "
                            "narration (casual slang is fine, keep it under 35 words, "
                            "no hashtags, no emoji):\n\n" + beat.text
                        ),
                    }
                ],
            )
            new_text = resp.content[0].text.strip()
            polished.append(Beat(text=new_text, caption=beat.caption, source_title=beat.source_title))
        except Exception:
            polished.append(beat)
    return polished


def build_script(editions: list[Edition], use_llm: bool = True) -> list[Beat]:
    stories = _select_stories(editions, MAX_STORIES_PER_VIDEO)
    beats = [_template_beat(s) for s in stories]
    if use_llm:
        beats = _maybe_llm_polish(beats)
    return beats


if __name__ == "__main__":
    from datetime import date

    from .scraper import fetch_editions

    editions = fetch_editions(target_date=date.today())
    for beat in build_script(editions):
        print("-", beat.text)
