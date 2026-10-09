"""Turns the day's TLDR stories into a few 40-60 second explainer scripts.

With ANTHROPIC_API_KEY set, Claude acts as the channel's head writer: it
picks the most interesting technology stories, may combine related ones
into one video, and writes a hook, spoken beats with punchier on-screen
cards, and an outro (see WRITER_SYSTEM). Without a key, a rule-based writer
groups the top-ranked stories into videos of the same length.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import zlib
from dataclasses import dataclass, field

from .scraper import Edition, Story

log = logging.getLogger("newsletter_video")

VIDEOS_PER_DAY = int(os.environ.get("VIDEOS_PER_DAY", "3"))
TITLE_SIMILARITY_THRESHOLD = 0.6  # for deduping the same story across editions

LOW_PRIORITY_EDITIONS = {"marketing"}
# Sections that aren't the channel's kind of tech news (pure science: physics,
# space, biology). Their stories are never offered to either writer.
EXCLUDED_SECTIONS = {"science & futuristic technology"}
# The default voice speaks ~2.5 words/s at the pipeline's 1.1x speed (numbers
# and prices take longer to say), so this word budget for hook + beats +
# outro lands each video at roughly 40-45 seconds. video_builder speeds up
# any narration that would still run past 45 seconds.
MIN_WORDS = 98
MAX_WORDS = 110

WRITER_MODEL = "claude-sonnet-5-5"

BADGE_ALIASES = {
    "big tech & startups": "BIG TECH",
    "science & futuristic technology": "SCI-TECH",
    "programming, design & data science": "DEV",
    "headlines & launches": "AI NEWS",
    "deep dives & analysis": "DEEP DIVE",
    "miscellaneous": "NEWS",
    "quick links": "LINKS",
}

# Generic capitalized words that shouldn't be picked as a "highlight" just
# because they start a sentence.
_HIGHLIGHT_STOPWORDS = {
    "the", "a", "an", "this", "that", "these", "those", "new", "after",
    "with", "how", "why", "what", "it", "its", "as", "in", "on", "for",
}


@dataclass
class Beat:
    say: str  # spoken by the voice
    card: str  # on-screen text shown while it's spoken
    highlight: str | None = None  # substring of `card` to draw highlighted
    kind: str = "fact"  # "fact" or "stat"
    stat: str = ""  # the big number shown on a "stat" card


@dataclass
class Script:
    title: str  # video title for posting; also names the file
    badge: str
    hook: str  # spoken + shown big before the first beat
    beats: list[Beat]
    outro: str  # spoken + shown as the closing card
    stories: list[Story] = field(default_factory=list)
    post_caption: str = ""  # text for the YouTube description / TikTok caption
    hashtags: list[str] = field(default_factory=list)

    @property
    def narration(self) -> str:
        """Everything the voice says, in order; video_builder relies on this
        exact joining to place the hook words and each beat in the audio."""
        parts = [self.hook] + [b.say for b in self.beats] + [self.outro]
        return " ".join(_sentence(p) for p in parts if p)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _dedupe_stories(editions: list[Edition]) -> list[Story]:
    """The same story often runs in multiple TLDR editions on the same day
    (e.g. a big AI launch in both `tech` and `ai`) — drop near-duplicate
    titles, keeping the first (earliest-section) occurrence."""
    seen: list[str] = []
    unique: list[Story] = []
    for edition in editions:
        for story in edition.stories:
            norm = _normalize_title(story.title)
            if any(
                difflib.SequenceMatcher(None, norm, s).ratio() > TITLE_SIMILARITY_THRESHOLD
                for s in seen
            ):
                continue
            seen.append(norm)
            unique.append(story)
    return unique


def _heuristic_rank(editions: list[Edition], candidates: list[Story], n: int) -> list[Story]:
    """No-LLM fallback: TLDR already orders each edition's own sections by
    importance, so ranking by (how early that section appears in its
    edition) approximates "most important" without an API call."""
    section_order: dict[str, list[str]] = {}
    for edition in editions:
        order: list[str] = []
        for story in edition.stories:
            if story.section not in order:
                order.append(story.section)
        section_order[edition.slug] = order

    def rank_key(story: Story) -> int:
        order = section_order.get(story.edition, [])
        return order.index(story.section) if story.section in order else len(order)

    return sorted(candidates, key=rank_key)[:n]


_STAT_RE = re.compile(
    r"\$\d[\d,.]*\d?(?:\s?(?:[KMBT]\b|thousand\b|million\b|billion\b|trillion\b))?"
    r"|\b\d+(?:\.\d+)?\s?%"
    r"|\b\d+(?:\.\d+)?x\b"
    r"|\b\d[\d,.]*\s(?:thousand|million|billion|trillion)\b"
    r"|\b(?:one|two|three|five|ten|hundred)-(?:thousand|million|billion|trillion)\b"
    r"|\b\d{1,3}(?:,\d{3})+\b",
    re.IGNORECASE,
)
# A capitalized word, optionally followed by more capitalized words or
# version numbers ("Claude Haiku 5.5", "Gemini 3.6 Flash").
_NAME_RE = re.compile(r"\b[A-Z][\w-]*(?:\s+(?:[A-Z][\w-]*|\d+(?:\.\d+)*)){0,2}")


def _pick_highlight(text: str) -> str | None:
    """Picks the one phrase worth visually highlighting in a card: a
    number/amount if there is one (most "important item" signal), else the
    most prominent proper-noun-looking phrase. A lone capitalized word at the
    very start of the text is just sentence case ("However", "Experts"), so
    it only counts if it's part of a multi-word name or brand-cased."""
    stat = _STAT_RE.search(text)
    if stat:
        return stat.group(0).strip()

    for match in _NAME_RE.finditer(text):
        words = match.group(0).rstrip(".-").split()
        while words and words[0].lower() in _HIGHLIGHT_STOPWORDS:
            words.pop(0)
        phrase = " ".join(words)
        if len(phrase) <= 2:
            continue
        is_brand = bool(re.search(r"[A-Z0-9]", phrase[1:]))  # OpenAI, GitHub, GPT5
        if match.start() == 0 and len(words) == 1 and not is_brand:
            continue
        return phrase

    count = re.search(r"\b\d{2,}\b", text)
    return count.group(0) if count else None


MAX_SUMMARY_CARDS = 2
MAX_SUMMARY_CHARS = 220


def _summary_sentences(summary: str) -> list[str]:
    """Splits the summary into whole sentences (one card each) so narration
    never stops mid-sentence; keeps at most MAX_SUMMARY_CARDS sentences and
    roughly MAX_SUMMARY_CHARS characters, always keeping the first."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", summary.strip()) if s.strip()]
    kept: list[str] = []
    total = 0
    for sentence in sentences:
        if kept and (len(kept) >= MAX_SUMMARY_CARDS or total + len(sentence) > MAX_SUMMARY_CHARS):
            break
        kept.append(sentence)
        total += len(sentence)
    return kept


def _badge_for(section: str) -> str:
    alias = BADGE_ALIASES.get(section.strip().lower())
    return alias or section.split("&")[0].strip().upper()[:14]


_SURPRISE_RE = re.compile(
    r"\b(?:no one|nobody|no human|can't|cannot|impossible|fail\w*|bans?|banned|lawsuit|sue[ds]?|"
    r"leak\w*|fired|layoffs?|shut\w*|hack\w*|breach\w*|warn\w*|crisis|backlash|secret\w*)\b",
    re.IGNORECASE,
)
_RELEASE_RE = re.compile(
    r"\b(?:launch\w*|releas\w*|introduc\w*|unveil\w*|debut\w*|ships?|announc\w*|"
    r"available|rolls? out|new)\b|\b\d+\.\d+\b",
    re.IGNORECASE,
)
_HOOKS = {
    "surprise": [
        "Okay, this one is genuinely wild.",
        "Nobody saw this one coming.",
        "This is the strangest tech story of the day.",
    ],
    "release": [
        "{name} just dropped. Here's what actually matters.",
        "Everyone's about to be talking about {name}.",
        "{name} is here, and it's a bigger deal than it sounds.",
    ],
    "stat": [
        "Wait until you hear this number.",
        "One number explains this whole story.",
        "This number is kind of insane.",
    ],
    "named": [
        "{name} just made a move you need to know about.",
        "Here's what {name} just did, in twenty seconds.",
    ],
    "plain": [
        "Here's the tech story you'll hear about all day.",
        "This one's worth twenty seconds of your time.",
    ],
}


def _lead_name(story: Story) -> str | None:
    """The leading product/company name in the title ("Claude Haiku 5.5"),
    if the title starts with one."""
    match = _NAME_RE.match(story.title)
    if not match:
        return None
    words = match.group(0).rstrip(".-").split()
    while words and words[0].lower() in _HIGHLIGHT_STOPWORDS:
        words.pop(0)
    # Sentence-case titles capitalize their opening verb ("Sharing AI progress").
    if words and re.fullmatch(r"[A-Z][a-z]+(?:ing|ed)", words[0]):
        words.pop(0)
    name = " ".join(words)
    if len(name) <= 2 or (len(words) == 1 and not re.search(r"[A-Z0-9]", name[1:])):
        return None
    return name


def _heuristic_hook(story: Story) -> str:
    """No-LLM fallback: picks a hook style from what the story is (surprising,
    a release, number-driven...), rotating phrasings deterministically by
    title so reruns of a day produce the same video."""
    text = f"{story.title} {story.summary}"
    name = _lead_name(story)
    if _SURPRISE_RE.search(text):
        kind = "surprise"
    elif name and _RELEASE_RE.search(text):
        kind = "release"
    elif _STAT_RE.search(text):
        kind = "stat"
    elif name:
        kind = "named"
    else:
        kind = "plain"
    options = _HOOKS[kind]
    return options[zlib.crc32(story.title.encode()) % len(options)].format(name=name)


_OUTROS = [
    "Which of these surprised you most? Tell me below.",
    "Follow for tomorrow's tech news in under a minute.",
    "Would you use this? Let me know in the comments.",
]


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


MAX_CARD_WORDS = 12
_CLAUSE_BREAK_RE = re.compile(r",\s|\s[—–-]\s|:\s|;\s|\s(?:and|but|which|while|after|as|to)\s")


def _card_text(text: str) -> str:
    """Shortens a spoken sentence into on-screen card text: its first clause
    if that's a sensible length, else its first MAX_CARD_WORDS words."""
    text = text.strip().rstrip(".")
    words = text.split()
    if len(words) <= MAX_CARD_WORDS:
        return text
    for match in _CLAUSE_BREAK_RE.finditer(text):
        clause_words = len(text[: match.start()].split())
        if 5 <= clause_words <= MAX_CARD_WORDS:
            return text[: match.start()]
    return " ".join(words[:MAX_CARD_WORDS - 2]) + "…"


def _make_beat(text: str) -> Beat:
    card = _card_text(text)
    highlight = _pick_highlight(card)
    is_stat = bool(highlight and _STAT_RE.fullmatch(highlight))
    return Beat(say=text, card=card, highlight=highlight,
                kind="stat" if is_stat else "fact", stat=highlight if is_stat else "")


def _story_beats(story: Story, first: bool) -> list[Beat]:
    sentences = _summary_sentences(story.summary)
    # "Claude Haiku 5.5" + "Claude Haiku 5.5 is Anthropic's..." would say the
    # name twice in a row; the first sentence works as the headline there.
    if sentences and sentences[0].lower().startswith(story.title.rstrip(".").lower()):
        texts = sentences
    else:
        texts = [story.title] + sentences
    beats = [_make_beat(t) for t in texts]
    if not first:
        beats[0].say = f"Next up. {_sentence(beats[0].say)}"
    return beats


def _heuristic_scripts(editions: list[Edition], candidates: list[Story], n_videos: int) -> list[Script]:
    """No-LLM fallback: walks the ranked stories, filling each video with
    stories (grouped by edition, so related topics share a video) until it
    reaches the 40-60 second word budget."""
    ranked = _heuristic_rank(editions, candidates, len(candidates))
    # Marketing tips aren't tech news; use them only if nothing else is left.
    ranked.sort(key=lambda s: s.edition in LOW_PRIORITY_EDITIONS)
    scripts: list[Script] = []
    pool = list(ranked)
    while pool and len(scripts) < n_videos:
        lead = pool.pop(0)
        hook = _heuristic_hook(lead)
        outro = _OUTROS[zlib.crc32(lead.title.encode()) % len(_OUTROS)]
        script = Script(title=lead.title, badge=_badge_for(lead.section), hook=hook,
                        beats=_story_beats(lead, first=True), outro=outro, stories=[lead])
        same_edition = [s for s in pool if s.edition == lead.edition]
        for story in same_edition + [s for s in pool if s.edition != lead.edition]:
            if script.word_count >= MIN_WORDS:
                break
            beats = _story_beats(story, first=False)
            extra = sum(len(b.say.split()) for b in beats)
            if script.word_count + extra > MAX_WORDS:
                continue
            script.beats += beats
            script.stories.append(story)
            pool.remove(story)
        scripts.append(script)
    return scripts


WRITER_SYSTEM = """\
You are the head writer for a daily tech-news channel on TikTok and YouTube Shorts. \
Each video is narrated by a friendly cartoon explainer while punchy text cards pop up \
on screen. Your viewers are curious, tech-savvy people aged 18-35 who scroll fast and \
leave the moment a video gets boring or generic.

Your job: read today's newsletter stories and write the day's videos.

Choosing what to cover
- The channel covers technology: AI and AI models, software and developer tools, big \
tech companies and startups, chips and hardware, gadgets, apps and platforms, \
cybersecurity, and the money and policy fights around them.
- Pick the most interesting of those stories: surprising, consequential, weird, or \
genuinely useful to know. Big launches, real numbers, power moves between companies, \
failures, and things that change how people work or live.
- Do not cover pure science (physics, space, biology, medicine, climate) unless the \
story is really about a tech product or company.
- Skip sponsored items, job posts, generic marketing or growth advice, and minor \
product updates unless something about them is genuinely remarkable.
- One video may cover a single big story, or combine 2-3 stories that share a real \
thread (a price war, a race between labs, a trend). Combine only when the connection \
makes the video better; never pad with unrelated stories.
- Never cover the same story in two videos. Newsletters often repeat a story under \
different titles; treat those as one.

Writing each video
- Length: 100-110 spoken words in total across hook, beats, and outro. The voice \
speaks about 2.5 words per second, so that is 40-44 seconds. Never exceed 112 words. \
Numbers, prices, and version names take longer to say, so use only the ones that matter.
- Hook (first line, at most 12 words): open a curiosity gap or lead with the single \
most surprising concrete fact. Never open with a greeting, "In today's news", or a \
question you then answer with "well...".
- Beats (4-6): each `say` is one or two short sentences, conversational, as if \
explaining to a smart friend. Use specific names and numbers. Build momentum: the \
what, then the surprising detail, then why it matters to the viewer, and the catch or \
the open question if there is one. Vary sentence length. When moving to a second \
story, bridge it with the shared thread rather than "next up".
- Each beat has a `card`: the on-screen text, at most 10 words, punchier than the \
spoken line and never a copy of it. `highlight` is the 1-4 most important words, \
copied exactly from `card`.
- When a beat is built around a number, set `kind` to "stat" and put the number, \
written short, in `stat` ("$0.10", "372", "1 trillion", "5x"); otherwise `kind` is \
"fact" and `stat` is "". The stat is shown huge above the card text, so the card \
text must not repeat that number: it says what the number means ("per million \
input tokens", "proofs no human has read").
- Outro (at most 12 words): a reason to comment or follow, tied to this video's topic.
- `title`: a scroll-stopping post title under 70 characters. `badge`: a 1-2 word \
topic label in capitals, under 14 characters.
- `post_caption`: the text posted under the video, 1-3 short sentences: what \
happened and why it matters, plus a question that invites comments. Not a copy of \
the hook. `hashtags`: 3-5 relevant hashtags without the # sign, e.g. "openai", \
"technews"; no "shorts".

Accuracy and tone
- Use only facts present in the provided stories. Do not invent numbers, quotes, or \
outcomes; say "reportedly" for anything the source frames as a report or rumor.
- Be energetic but credible: no hype words such as "game-changer", "revolutionary", \
or "insane", no emojis, no hashtags.
- `say` is read by a text-to-speech voice: write it exactly as it should be spoken, \
with no URLs, parentheses, bullet characters, or markdown. Write money and units in \
words ("ten cents per million tokens", "1.8 billion dollars", "five times cheaper"); \
symbols like "$0.10" belong only in `card` and `stat`. Spell made-up or blended \
words the way they should sound ("Mathocalypse" → "Math-apocalypse") in `say`, \
keeping the original spelling in `card`.
"""

_WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "badge": {"type": "string"},
                    "story_ids": {"type": "array", "items": {"type": "integer"}},
                    "hook": {"type": "string"},
                    "beats": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "say": {"type": "string"},
                                "card": {"type": "string"},
                                "highlight": {"type": "string"},
                                "kind": {"type": "string", "enum": ["fact", "stat"]},
                                "stat": {"type": "string"},
                            },
                            "required": ["say", "card", "highlight", "kind", "stat"],
                            "additionalProperties": False,
                        },
                    },
                    "outro": {"type": "string"},
                    "post_caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "badge", "story_ids", "hook", "beats", "outro",
                             "post_caption", "hashtags"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["videos"],
    "additionalProperties": False,
}


def _llm_scripts(candidates: list[Story], n_videos: int) -> list[Script] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None

    stories = "\n\n".join(
        f"[{i}] ({s.edition} / {s.section}) {s.title}\n{s.summary}" for i, s in enumerate(candidates)
    )
    user = (
        f"Write exactly {n_videos} videos from today's stories below. Order them with "
        f"the strongest video first. Reference stories by their [id].\n\n{stories}"
    )
    try:
        client = anthropic.Anthropic()
        # Streamed: several scripts can run past the non-streaming output limit
        # (~3k output tokens per video).
        with client.beta.messages.stream(
            model=WRITER_MODEL,
            max_tokens=64000,
            system=WRITER_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _WRITER_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            # On a safety-classifier decline, re-run on Anthropic's recommended model.
            extra_body={"fallbacks": "default"},
        ) as stream:
            response = stream.get_final_message()
    except anthropic.APIError as e:
        log.warning("Claude script writer failed (%s); using rule-based scripts", type(e).__name__)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude script writer stopped with %s; using rule-based scripts", response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        videos = json.loads(text)["videos"]
    except (json.JSONDecodeError, KeyError):
        log.warning("Claude script writer returned unparseable JSON; using rule-based scripts")
        return None
    log.info("Claude wrote %d scripts (%d in / %d out tokens)", len(videos),
             response.usage.input_tokens, response.usage.output_tokens)

    scripts = []
    for video in videos[:n_videos]:
        beats = []
        for raw in video["beats"]:
            highlight = raw["highlight"] if raw["highlight"] and raw["highlight"] in raw["card"] else None
            is_stat = raw["kind"] == "stat" and raw["stat"].strip()
            beats.append(Beat(say=raw["say"], card=raw["card"], highlight=highlight,
                              kind="stat" if is_stat else "fact", stat=raw["stat"].strip() if is_stat else ""))
        if not beats:
            continue
        scripts.append(Script(
            title=video["title"],
            badge=video["badge"].upper()[:14],
            hook=video["hook"],
            beats=beats,
            outro=video["outro"],
            stories=[candidates[i] for i in video["story_ids"] if 0 <= i < len(candidates)],
            post_caption=video["post_caption"].strip(),
            hashtags=[re.sub(r"[^\w]", "", h).lower() for h in video["hashtags"] if h.strip()][:5],
        ))
    return scripts or None


def build_scripts(editions: list[Edition], n_videos: int = VIDEOS_PER_DAY,
                  exclude_titles: set[str] = frozenset()) -> list[Script]:
    """`exclude_titles`: stories already covered by earlier videos today."""
    covered = {_normalize_title(t) for t in exclude_titles}
    candidates = [
        s for s in _dedupe_stories(editions)
        if _normalize_title(s.title) not in covered and s.section.strip().lower() not in EXCLUDED_SECTIONS
    ]
    if not candidates:
        return []
    return _llm_scripts(candidates, n_videos) or _heuristic_scripts(editions, candidates, n_videos)


if __name__ == "__main__":
    from datetime import date

    from .scraper import fetch_editions

    editions = fetch_editions(target_date=date.today())
    for script in build_scripts(editions):
        print(f"\n[{script.badge}] {script.title}  ({script.word_count} words)\n  hook: {script.hook}")
        for beat in script.beats:
            print(f"  - say: {beat.say}\n    card: {beat.card!r} (highlight={beat.highlight!r}, {beat.kind} {beat.stat})")
        print(f"  outro: {script.outro}")
