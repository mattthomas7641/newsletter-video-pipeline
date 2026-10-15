"""The writer path for the "explainer" video style (VIDEO_STYLE=explainer).

Claude returns a scene plan per video (see scene_plan.py) instead of a
hook/beats/outro script: an ordered list of scenes from the fixed scene
library, each with typed on-screen fields under hard length caps and the
line the voice speaks over it. One structured-output call writes the whole
day, like the classic writer. Every plan is validated in code before it is
accepted; a plan that fails validation is logged with its errors and that
video is skipped — never rendered broken, never quietly swapped for
something else.

The result is an ExplainerScript: a script_writer.Script (so metadata,
narration and uploads work unchanged) that also carries its ScenePlan.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

from .scene_plan import SCENE_SCHEMA, ScenePlan, ScenePlanError, plan_from_script, validate_plan
from .scraper import Story
from .script_writer import (
    DEFAULT_WRITER_EFFORT,
    LLM_SUMMARY_CHARS,
    WRITER_MODEL,
    Beat,
    Script,
    _trim,
)

log = logging.getLogger("newsletter_video")


@dataclass
class ExplainerScript(Script):
    """A Script whose hook/beats/outro are derived from its scene plan, so
    everything downstream (narration length, metadata sidecar, upload text)
    keeps working; the plan itself is what gets rendered."""

    plan: ScenePlan | None = field(default=None, repr=False)


def _beat_from_scene(scene) -> Beat:
    f = scene.fields
    if scene.type == "stat":
        return Beat(say=scene.say, card=f["label"], highlight=None, kind="stat", stat=f["value"])
    if scene.type == "statement":
        return Beat(say=scene.say, card=f["text"], highlight=f.get("emphasis") or None)
    if scene.type == "quote":
        return Beat(say=scene.say, card=f["text"], highlight=None)
    if scene.type == "list":
        return Beat(say=scene.say, card=f.get("title") or " · ".join(f["items"]), highlight=None)
    if scene.type == "timeline":
        return Beat(say=scene.say, card=f.get("title") or " → ".join(s["text"] for s in f["steps"]), highlight=None)
    if scene.type == "comparison":
        return Beat(say=scene.say, card=f.get("title") or f"{f['left']['label']} vs {f['right']['label']}", highlight=None)
    return Beat(say=scene.say, card=f.get("title", ""), highlight=None)


def script_from_plan(plan: ScenePlan, title: str, badge: str, stories: list[Story],
                     post_caption: str = "", hashtags: list[str] | None = None) -> ExplainerScript:
    scenes = plan.scenes
    return ExplainerScript(
        title=title,
        badge=badge.upper()[:14],
        hook=scenes[0].say,
        beats=[_beat_from_scene(s) for s in scenes[1:-1]],
        outro=scenes[-1].say,
        stories=stories,
        post_caption=post_caption.strip(),
        hashtags=[re.sub(r"[^\w]", "", h).lower() for h in (hashtags or []) if h.strip()][:5],
        plan=plan,
    )


def plan_dict(plan: ScenePlan) -> dict:
    """What the metadata sidecar stores so the video can be re-rendered."""
    return {"style": "explainer", "scenes": plan.to_list()}


# --- the prompt ------------------------------------------------------------------

EXPLAINER_WRITER_SYSTEM = """\
You are the head writer for a daily tech-news channel on TikTok and YouTube Shorts. \
Each video is a clean, typographic explainer: a narrator reads the story while designed \
scenes appear on screen (a headline, a big number, a comparison, a short list, a timeline, \
a quote, a small bar chart). No presenter, no footage. Think of a sharp editorial graphic, \
not a slideshow. Your viewers are curious, tech-savvy people aged 18-35 who scroll fast and \
leave the moment a video gets boring or generic.

Your job: read today's newsletter stories and write the day's videos as scene plans.

Choosing what to cover
- The channel covers technology: AI and AI models, software and developer tools, big tech \
companies and startups, chips and hardware, gadgets, apps and platforms, cybersecurity, and \
the money and policy fights around them.
- Pick the most interesting of those stories: surprising, consequential, weird, or genuinely \
useful to know. Big launches, real numbers, power moves between companies, failures, and \
things that change how people work or live.
- Do not cover pure science (physics, space, biology, medicine, climate) unless the story is \
really about a tech product or company.
- Skip sponsored items, job posts, generic marketing or growth advice, and minor product \
updates unless something about them is genuinely remarkable.
- One video may cover a single big story, or combine 2-3 stories that share a real thread. \
Combine only when the connection makes the video better; never pad with unrelated stories.
- Never cover the same story in two videos. Newsletters often repeat a story under different \
titles; treat those as one.

The scene library (use `type` exactly)
- title: opens every video. `kicker` is a 1-2 word topic label; `headline` is the story in \
one line. Its `say` is the spoken hook: open a curiosity gap or lead with the single most \
surprising concrete fact, at most 14 words. Never a greeting or "In today's news".
- statement: one sentence the viewer reads, with an optional `emphasis` of 1-3 words copied \
exactly from it, and an optional small `label` ("The catch", "Why it matters").
- stat: one number as the hero. `value` is written short ("5.1x", "$7B", "22%", "1,200"); \
`label` says what it is without repeating the number; `context` is one optional line; \
`source` is an optional source name.
- comparison: two things side by side, each with a `label`, a `value` and a short `note`. \
Set `number` on both sides only when the values are comparable quantities (bars are drawn \
to scale); otherwise null on both.
- list: 2-4 short items under an optional title. Mention the items in the spoken line, in \
order, so each appears as it is said.
- timeline: 2-4 steps, each with a short `label` ("2024", "Now", "Next") and `text`.
- quote: a real quotation from the source and who said it. Only when the source actually \
quotes someone.
- chart: 2-5 bars with a short `label` and a numeric `value` each, a `title`, an optional \
`unit`, and `highlight` (the index of the bar that matters). Only from numbers in the stories.
- outro: closes every video. `text` is the closing line the viewer reads; its `say` is the \
spoken outro, at most 14 words: a reason to comment or follow, tied to the topic.

Writing each video
- Structure: a title scene, then 3-7 scenes, then an outro. Choose each scene's type from \
what the beat is about: a number gets a stat, a before/after a comparison, a sequence a \
timeline, several parallel points a list. Vary the types; at most two of the same type in a \
row (statements excepted). Every video should have at least one data scene (stat, chart or \
comparison) when the stories have real numbers.
- Length: 100-110 spoken words in total across all `say` lines. The voice speaks about 2.5 \
words per second, so that is 40-44 seconds. Never exceed 115 words. Each scene's `say` is \
one or two short sentences, 6-30 words, conversational, as if explaining to a smart friend. \
Build momentum: the what, the surprising detail, why it matters, the catch or open question.
- On-screen text is punchier than the spoken line and never a copy of it. Respect every \
character cap in the field descriptions; text that is too long is rejected and the video is \
dropped.
- `title`: a scroll-stopping post title under 70 characters. `post_caption`: 1-3 short \
sentences posted under the video: what happened and why it matters, plus a question that \
invites comments. `hashtags`: 3-5 relevant hashtags without the # sign; no "shorts".

Accuracy and tone
- Use only facts present in the provided stories. Do not invent numbers, quotes, or \
outcomes; say "reportedly" for anything the source frames as a report or rumor. Every \
number in a stat, chart or comparison must appear in the stories.
- Be energetic but credible: no hype words such as "game-changer", "revolutionary", or \
"insane", no emojis, no hashtags in spoken or on-screen text.
- `say` is read by a text-to-speech voice: write it exactly as it should be spoken, with no \
URLs, parentheses, bullet characters, or markdown. Write money and units in words ("ten \
cents per million tokens", "1.8 billion dollars", "five times cheaper"); symbols like \
"$0.10" belong only in on-screen fields. Spell made-up or blended words the way they should \
sound in `say`, keeping the original spelling on screen.
"""

_EXPLAINER_SCHEMA = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "badge": {"type": "string", "description": "1-2 word topic label, under 14 characters"},
                    "story_ids": {"type": "array", "items": {"type": "integer"}},
                    "scenes": {"type": "array", "items": SCENE_SCHEMA},
                    "post_caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "badge", "story_ids", "scenes", "post_caption", "hashtags"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["videos"],
    "additionalProperties": False,
}


def _system_prompt() -> str:
    """The explainer prompt plus the active character's persona (tone only;
    the avatar image is irrelevant in this style)."""
    try:
        from .characters import active

        character = active()
    except Exception:  # a missing CHARACTER folder is reported by run_pipeline, not here
        character = None
    if not (character and character.persona):
        return EXPLAINER_WRITER_SYSTEM
    return (
        EXPLAINER_WRITER_SYSTEM
        + f"\nNarrator character\n- The narrator is {character.name}: {character.persona.rstrip('. ')}. "
        "This shapes the tone and word choice of the spoken lines only. Every fact, number and "
        "claim must still come from the stories, and the word budget, scene rules and caps above "
        "still apply.\n"
    )


def _call_writer(user: str) -> dict | None:
    """One structured-output call; None when Claude is unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        output_config = {"format": {"type": "json_schema", "schema": _EXPLAINER_SCHEMA}}
        effort = os.environ.get("WRITER_EFFORT", DEFAULT_WRITER_EFFORT)
        if effort:
            output_config["effort"] = effort
        client = anthropic.Anthropic()
        with client.beta.messages.stream(
            model=WRITER_MODEL,
            max_tokens=64000,
            system=_system_prompt(),
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        ) as stream:
            response = stream.get_final_message()
    except anthropic.APIError as exc:
        log.warning("Claude explainer writer failed (%s: %s)", type(exc).__name__, getattr(exc, "message", exc))
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude explainer writer stopped with %s", response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Claude explainer writer returned unparseable JSON")
        return None
    log.info("Claude wrote %d scene plans (%d in / %d out tokens)", len(data.get("videos", [])),
             response.usage.input_tokens, response.usage.output_tokens)
    return data


def _accept(video: dict, stories: list[Story]) -> ExplainerScript | None:
    """Validates one video's plan; logs and drops it when it fails."""
    title = str(video.get("title", "")).strip() or "Untitled"
    try:
        plan = validate_plan(video.get("scenes"))
    except ScenePlanError as exc:
        log.error("Explainer: skipping %r, its scene plan failed validation:\n  %s",
                  title, "\n  ".join(exc.errors))
        return None
    for note in plan.repairs:
        log.info("Explainer: %r plan repair: %s", title, note)
    return script_from_plan(plan, title, str(video.get("badge", "NEWS")), stories,
                            str(video.get("post_caption", "")), list(video.get("hashtags", [])))


def llm_explainer_scripts(candidates: list[Story], n_videos: int) -> list[ExplainerScript] | None:
    """The explainer counterpart of script_writer._llm_scripts: one call
    for the day's videos. Videos whose plan fails validation are skipped
    with a logged reason (an empty list means every plan was rejected: no
    video that day rather than a broken one); None means Claude could not
    be used at all."""
    stories = "\n\n".join(
        f"[{i}] ({s.edition} / {s.section}) {s.title}\n{_trim(s.summary, LLM_SUMMARY_CHARS)}"
        for i, s in enumerate(candidates)
    )
    user = (
        f"Write exactly {n_videos} videos from today's stories below, each as a scene plan. "
        f"Order them with the strongest video first. Reference stories by their [id].\n\n{stories}"
    )
    data = _call_writer(user)
    if data is None:
        return None
    scripts = []
    for video in data.get("videos", [])[:n_videos]:
        ids = [i for i in video.get("story_ids", []) if isinstance(i, int) and 0 <= i < len(candidates)]
        script = _accept(video, [candidates[i] for i in ids])
        if script is not None:
            scripts.append(script)
    written = len(data.get("videos", [])[:n_videos])
    if len(scripts) < written:
        log.warning("Explainer: %d of %d videos dropped for invalid scene plans", written - len(scripts), written)
    return scripts


def structural_explainer_scripts(scripts: list[Script]) -> list[ExplainerScript]:
    """Wraps rule-based scripts (no LLM available) in structural plans so
    the explainer style still renders; logged loudly because these plans
    are plainer than Claude's (headlines and statements only)."""
    out = []
    for script in scripts:
        try:
            plan = plan_from_script(script)
        except ScenePlanError as exc:
            log.error("Explainer: skipping %r, no valid structural plan:\n  %s", script.title, "\n  ".join(exc.errors))
            continue
        log.warning("Explainer: %r uses a structural plan derived from the rule-based script (no Claude plan)",
                    script.title)
        out.append(script_from_plan(plan, script.title, script.badge, script.stories,
                                    script.post_caption, script.hashtags))
    return out


def replan_sidecar(meta: dict) -> ScenePlan:
    """A scene plan for an already-written video (a saved <video>.json),
    from its script and story titles, in one call. For re-rendering old
    videos in the explainer style and for samples; raises when Claude is
    unavailable or the plan is invalid."""
    script = meta["script"]
    source = "\n".join([
        f"Title: {script['title']}",
        f"Topic: {script['badge']}",
        "Stories: " + "; ".join(meta.get("stories", [])),
        f"Caption: {meta.get('caption', '')}",
        "Narration as written for the previous version of this video (all facts available to you):",
        script["hook"],
        *[b["say"] for b in script["beats"]],
        script["outro"],
        "On-screen cards from that version: " + "; ".join(
            f"{b['card']}" + (f" [{b['stat']}]" if b.get("stat") else "") for b in script["beats"]),
    ])
    user = (
        "Write exactly 1 video as a scene plan from the material below. Keep the same story and "
        "the same facts (you may reuse or rephrase the narration), but choose scene types that "
        "fit each beat. Use story_ids [0].\n\n[0] " + source
    )
    data = _call_writer(user)
    if data is None:
        raise RuntimeError("Claude is unavailable (no ANTHROPIC_API_KEY, SDK, or the call failed)")
    videos = data.get("videos", [])
    if not videos:
        raise RuntimeError("Claude returned no video")
    return validate_plan(videos[0].get("scenes"))
