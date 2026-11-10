"""A realistic avatar explainer writer answer (Claude's flat scene schema) and the story it is checked against."""
from __future__ import annotations

import copy

from newsletter_video.scraper import Story

STORY = Story(
    edition="tech", section="Big Tech & Startups",
    title="Nvidia posts a record $57 billion quarter as data center sales jump",
    summary=("Nvidia reported revenue of $57 billion for its third quarter, up 62% from a year ago. Data center sales "
             "reached $51.2 billion. CEO Jensen Huang said demand for Blackwell chips is off the charts. Rival AMD is "
             "racing to catch up with its MI450 chips, due in 2026."),
    url="https://example.com/nvidia")

_EMPTY_SIDE = {"name": "", "tag": "", "items": [], "initials": "", "cue": ""}
_BLANK = {"lines": [], "items": [], "nodes": [], "a": _EMPTY_SIDE, "b": _EMPTY_SIDE, "value": 0, "decimals": 0, "prefix": "",
          "suffix": "", "label": "", "source": "", "series": [], "x_labels": [], "text": "", "text_cue": "", "text2": "",
          "text2_cue": "", "cue": ""}


def _scene(**fields):
    return {**copy.deepcopy(_BLANK), **fields}


SCENES = [
    _scene(style="kinetic", pose=0, eyebrow="Nvidia · AI",
           say="Nvidia just made fifty seven billion dollars in three months, and it is still not enough to meet demand.",
           lines=[{"text": "Nvidia just made", "em": False, "cue": "Nvidia"}, {"text": "$57 billion", "em": True, "cue": "fifty"}],
           cue="billion"),
    _scene(style="stat", pose=1, eyebrow="The growth",
           say="Revenue is up sixty two percent from a year ago, and data centers brought in most of it.",
           value=62, suffix="%", label="growth from a year ago", cue="sixty"),
    _scene(style="relation", pose=2, eyebrow="The boss",
           say="Chief executive Jensen Huang says demand for the new Blackwell chips is off the charts.",
           a={"name": "Jensen Huang", "tag": "Nvidia CEO", "items": [], "initials": "JH", "cue": "Jensen"},
           b={"name": "Blackwell", "tag": "Nvidia chips", "items": [], "initials": "BW", "cue": "Blackwell"},
           text="Off the charts", text_cue="off", text2="hypes", text2_cue="says"),
    _scene(style="versus", pose=3, eyebrow="The race",
           say="Nvidia owns the data center today, while rival AMD is racing to catch up with chips due in 2026.",
           a={"name": "Nvidia", "tag": "Leader", "items": ["Blackwell", "Data centers"], "initials": "", "cue": "Nvidia"},
           b={"name": "AMD", "tag": "Rival", "items": ["MI450 chips", "Due 2026"], "initials": "", "cue": "AMD"},
           text="Racing to catch up", text_cue="racing"),
    _scene(style="list", pose=1, eyebrow="Why it matters",
           say="So what does that mean for you? Demand is real, chips stay scarce, and rivals are racing.",
           label="What it means", items=[{"text": "Demand is real", "cue": "Demand"}, {"text": "Chips stay scarce", "cue": "chips"},
                                         {"text": "Rivals are racing", "cue": "rivals"}]),
    _scene(style="outro", pose=3, eyebrow="Your call", say="Can AMD ever catch Nvidia? Tell me what you think below.",
           lines=[{"text": "Can AMD ever", "em": False, "cue": "Can"}, {"text": "catch Nvidia?", "em": True, "cue": "catch"}],
           text="Tell me below", text_cue="Tell"),
]

EVIDENCE = [
    {"line": 1, "kind": "factual", "quotes": ["Nvidia reported revenue of $57 billion for its third quarter",
                                              "demand for Blackwell chips is off the charts"]},
    {"line": 2, "kind": "factual", "quotes": ["up 62% from a year ago", "Data center sales reached $51.2 billion"]},
    {"line": 3, "kind": "factual", "quotes": ["CEO Jensen Huang said demand for Blackwell chips is off the charts"]},
    {"line": 4, "kind": "factual", "quotes": ["Rival AMD is racing to catch up with its MI450 chips, due in 2026"]},
    {"line": 5, "kind": "factual", "quotes": ["demand for Blackwell chips is off the charts", "Rival AMD is racing to catch up"]},
    {"line": 6, "kind": "question", "quotes": []},
]

VIDEO = {"title": "Nvidia made $57 billion in one quarter", "badge": "AI CHIPS", "story_ids": [0], "scenes": SCENES,
         "post_caption": "Nvidia's quarter hit $57 billion. Can anyone catch up?", "hashtags": ["nvidia", "#AI", "technews"],
         "evidence": EVIDENCE}


def video(**changes) -> dict:
    out = copy.deepcopy(VIDEO)
    out.update(changes)
    return out
