"""Must-cover stories: the day's most important news must reach the viewer even when a video about it fails.

Jev ranks the in-scope stories. The top MUST_COVER_COUNT of them (one per news event: two TLDR editions often carry
the same announcement under different titles) are the day's must-cover list. The writer is told to cover them
first, a dropped video about one of them gets one more try with the reasons it failed (before refill reaches for a
minor story), and the run reports which made it. No extra Claude calls of its own: it only steers the ones the day
already makes.

2026-11-11, why this exists: the editor dropped the day's biggest stories (Anthropic's IPO, twice, Gemini 4
Carbon, ChatGPT business profiles, Claude Sheets) and refill replaced them with minor ones, so the channel posted
seven videos and missed the news that mattered.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Capitalised words that say nothing about which event a story is about.
_STOP = {
    "The", "A", "An", "And", "Or", "But", "In", "On", "At", "To", "For", "Of", "With", "By", "From", "As", "Is",
    "It", "Its", "This", "That", "These", "Those", "We", "You", "Your", "Our", "They", "Their", "He", "She", "His",
    "Her", "I", "My", "New", "Now", "How", "Why", "What", "When", "Where", "Who", "Will", "Can", "Could", "Should",
    "Would", "May", "Might", "More", "Most", "Than", "After", "Before", "Into", "Over", "Under", "Just", "Not",
    "No", "Yes", "All", "Some", "One", "Two", "Three", "AI", "LLM", "LLMs", "API", "CEO", "US", "UK", "EU",
    "Inc", "Tool", "GitHub", "Repo", "Website", "Introducing", "Introduces", "Announces", "Launches", "Releases",
    "Update", "Today", "Here", "There", "If", "So", "Also", "Then", "Every", "Each", "Many", "Much", "Big",
    "Read", "Learn", "See", "Get", "Make", "Inside", "Meet", "Stop", "Go", "Before", "About", "Has", "Have",
    "Was", "Are", "Be", "Been", "Do", "Does", "Did", "Up", "Out", "Off", "Billion", "Million", "Trillion",
}
_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9&\-]*(?:\.[0-9]+)?")
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+")
_TITLE_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "its", "now", "new",
               "how", "why", "what", "by", "from", "as", "at", "it", "this", "that", "are", "be", "tool", "repo",
               "github", "introducing", "ai", "s", "out", "up", "more", "your", "you", "we", "our", "just", "all",
               "will", "can", "into", "over", "after", "about", "here", "not", "no", "one", "two", "after", "website"}


def _text(story: Any, summary_chars: int = 400) -> str:
    return f"{getattr(story, 'title', '')} . {str(getattr(story, 'summary', ''))[:summary_chars]}"


def _proper_nouns(text: str) -> set[str]:
    out = set()
    for w in _PROPER.findall(text):
        w = re.sub(r"'s$|’s$", "", w)
        if w not in _STOP and len(w) > 1:
            out.add(w.lower())
    return out


def _numbers(text: str) -> set[str]:
    """Distinctive numbers only: no years, no single digits ("825", "1.3", "$26" count; "2026", "3" do not)."""
    out = set()
    for raw in _NUMBER.findall(text):
        n = raw.lstrip("$").replace(",", "").rstrip(".")
        if not n:
            continue
        try:
            v = float(n)
        except ValueError:
            continue
        if "." not in n and (len(n) < 2 or 2000 <= v <= 2040):
            continue
        out.add(n)
    return out


_CODE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")


def _codes(text: str) -> set[str]:
    """Model and product codes that mix letters and digits ("8tx", "8i", "GPT-6", "M5"): as telling as a number."""
    return {c.lower() for c in _CODE.findall(text) if not re.fullmatch(r"\d+(st|nd|rd|th|s|x|k|m|b|bn|gb|tb)", c.lower())}


def _title_words(story: Any) -> set[str]:
    title = re.sub(r"['’]s\b", "", str(getattr(story, "title", "")).lower())
    return {w for w in _WORD.findall(title) if w not in _TITLE_STOP}


def same_event(a: Any, b: Any) -> bool:
    """Two stories about the same announcement: they share a distinctive number or product code and at least two
    names, or their titles share most of their words, or two title words one of which is a name ("ChatGPT ...
    ads"), or two names (e.g. "Anthropic" + "IPO")."""
    ta, tb = _text(a), _text(b)
    names = _proper_nouns(ta) & _proper_nouns(tb)
    if (_numbers(ta) & _numbers(tb) or _codes(ta) & _codes(tb)) and len(names) >= 2:
        return True
    wa, wb = _title_words(a), _title_words(b)
    if wa and wb and len(wa & wb) / len(wa | wb) >= 0.5:
        return True
    shared = wa & wb
    both_titles = _proper_nouns(str(getattr(a, "title", ""))) & _proper_nouns(str(getattr(b, "title", "")))
    if len(shared) >= 2 and shared & both_titles:  # "ChatGPT ... ads" and "ChatGPT visual ads": a name in both titles
        return True
    title_names = _proper_nouns(str(getattr(a, "title", ""))) & _proper_nouns(str(getattr(b, "title", "")))
    return len(title_names) >= 2


def event_groups(stories: list[Any]) -> list[list[Any]]:
    """Stories grouped by news event (single-link: a ~ b and b ~ c puts all three together), in input order."""
    parent = list(range(len(stories)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(stories)):
        for j in range(i + 1, len(stories)):
            if same_event(stories[i], stories[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[Any]] = {}
    for i, s in enumerate(stories):
        groups.setdefault(find(i), []).append(s)
    return list(groups.values())


def extra_videos() -> int:
    """MUST_COVER_EXTRA (default 2): how many videos past the day's count refill may add, only to cover must-cover
    stories still missing once the count is reached."""
    try:
        return max(0, int(os.environ.get("MUST_COVER_EXTRA", "2")))
    except ValueError:
        return 2


def must_cover_count(n_videos: int) -> int:
    """MUST_COVER_COUNT (default: the day's video count; 0 switches the feature off)."""
    raw = os.environ.get("MUST_COVER_COUNT", "").strip()
    try:
        return max(0, int(raw)) if raw else n_videos
    except ValueError:
        return n_videos


# --- the day's state (one run = one day; build_scripts sets it, the writers and the run report read it) --------

_day: dict[str, Any] = {"must": [], "mates": {}}


def set_day(ranked: list[Any], n_videos: int, importance: dict[str, float] | None = None) -> list[Any]:
    """Picks today's must-cover stories from Jev's ranking: the most important story of each news event (by Jev's
    "would a well-informed person want to know this today?" probability when given, else the ranking order) until
    the count is reached. Remembers each story's same-event mates so a video on one covers them all."""
    groups = event_groups(ranked)
    if importance:
        order = {id(s): i for i, s in enumerate(ranked)}
        ranked = sorted(ranked, key=lambda s: (-importance.get(s.title, 0.0), order[id(s)]))
        groups = [sorted(g, key=lambda s: (-importance.get(s.title, 0.0), order[id(s)])) for g in groups]
    lead = {id(g[0]): g for g in groups}
    mates: dict[str, set[str]] = {}
    for g in groups:
        titles = {s.title for s in g}
        for s in g:
            mates[s.title] = titles
    k = must_cover_count(n_videos)
    must = [s for s in ranked if id(s) in lead][:k]
    # A day written in several format groups calls this once per group: keep the earlier groups' list too.
    have = {s.title for s in _day["must"]}
    _day["must"] = list(_day["must"]) + [s for s in must if s.title not in have]
    _day["mates"] = {**_day["mates"], **mates}
    if must:
        log.info("Must-cover (%d): %s", len(must), "; ".join(repr(s.title[:60]) for s in must))
    dupes = [g for g in groups if len(g) > 1]
    if dupes:
        log.info("Same news under several titles: %s",
                 " | ".join(" = ".join(repr(s.title[:45]) for s in g) for g in dupes))
    return must


def active() -> bool:
    """True once set_day ran for this run (Jev ranked the stories and MUST_COVER_COUNT is not 0)."""
    return bool(_day["must"])


def clear() -> None:
    _day["must"], _day["mates"] = [], {}


def must_titles() -> list[str]:
    return [s.title for s in _day["must"]]


def mates_of(title: str) -> set[str]:
    return set(_day["mates"].get(title, {title}))


def covered_titles(scripts: Iterable[Any]) -> set[str]:
    """Every story title a set of scripts covers, counting same-event mates as covered."""
    out: set[str] = set()
    for sc in scripts:
        for st in getattr(sc, "stories", []) or []:
            out |= mates_of(st.title)
    return out


def missing(scripts: Iterable[Any]) -> list[Any]:
    covered = covered_titles(scripts)
    return [s for s in _day["must"] if s.title not in covered]


def writer_hint(pool: list[Any], notes: dict[str, list[str]] | None = None, only: Iterable[int] | None = None) -> str:
    """The sentence added to a writer call: which [ids] in `pool` are must-cover, which ids are the same news (one
    video for all of them), and for a story whose earlier video was dropped, why, so the retry avoids it.
    `only`: the ids this call actually shows (a parallel first-draft call sees a share of the pool)."""
    must = set(must_titles())
    shown = set(range(len(pool))) if only is None else set(only)
    ids = {s.title: i for i, s in enumerate(pool) if i in shown}
    pool = [s if i in shown else None for i, s in enumerate(pool)]
    parts = []
    m = [i for i, s in enumerate(pool) if s is not None and s.title in must]
    if m:
        parts.append("MUST-COVER: stories " + ", ".join(f"[{i}]" for i in m) + " are today's most important news. "
                     "Cover every one of them (alone, or combined with a related story) before any other story.")
    seen: set[str] = set()
    same = []
    for s in pool:
        if s is None:
            continue
        g = sorted(ids[t] for t in mates_of(s.title) if t in ids)
        key = ",".join(map(str, g))
        if len(g) > 1 and key not in seen:
            seen.add(key)
            same.append(" = ".join(f"[{i}]" for i in g))
    if same:
        parts.append("SAME NEWS: these ids are one announcement reported twice; cover each group in ONE video "
                     "that uses both, never two videos: " + "; ".join(same) + ".")
    for title, reasons in (notes or {}).items():
        if title in ids and reasons:
            parts.append(f"RETRY [{ids[title]}]: an earlier video on this story was dropped for: "
                         + " | ".join(r[:220] for r in reasons[:4])
                         + ". Write it again so none of these problems happens: explain every name, say each "
                         "number exactly as the story does, one point per scene, nothing the story does not say.")
    return ("\n\n" + "\n".join(parts)) if parts else ""


def duplicate_events(videos_stories: list[list[str]]) -> dict[int, str]:
    """{index: reason} for every video that covers the same news as an earlier one in the list."""
    out: dict[int, str] = {}
    seen: dict[str, int] = {}
    for k, titles in enumerate(videos_stories):
        hit = None
        for t in titles:
            for m in mates_of(t):
                if m in seen:
                    hit = seen[m]
                    break
            if hit is not None:
                break
        if hit is not None:
            out[k] = f"covers the same news as video {hit + 1}"
            continue
        for t in titles:
            for m in mates_of(t):
                seen[m] = k
    return out


def report(scripts: Iterable[Any]) -> list[dict]:
    """[{title, covered}] for today's must-cover list, for the run status."""
    covered = covered_titles(scripts)
    rows = [{"title": s.title, "covered": s.title in covered} for s in _day["must"]]
    if rows:
        miss = [r["title"] for r in rows if not r["covered"]]
        log.info("Must-cover: %d of %d covered%s", len(rows) - len(miss), len(rows),
                 (": missing " + "; ".join(repr(t[:60]) for t in miss)) if miss else "")
    return rows
