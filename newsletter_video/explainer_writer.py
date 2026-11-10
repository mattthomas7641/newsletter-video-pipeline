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
from typing import Any

from . import factcheck
from .scene_plan import ICON_MEANINGS, SCENE_SCHEMA, ScenePlan, ScenePlanError, plan_from_script, validate_plan
from .scraper import Story
from .script_writer import (
    ANTHROPIC_TIMEOUT_SECONDS,
    WriterNetworkError,
    _stream_with_retries,
    DEFAULT_WRITER_EFFORT,
    WRITER_SUMMARY_CHARS,
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
                     post_caption: str = "", hashtags: list[str] | None = None,
                     writer: str = "claude") -> ExplainerScript:
    """`writer` is "claude" for Claude's plans and "heuristic" for structural
    plans, so run_pipeline's FALLBACK_UPLOAD policy applies to both styles."""
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
        writer=writer,
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
@SCOPE@- Skip sponsored items, job posts, generic marketing or growth advice, and minor product \
updates unless something about them is genuinely remarkable.
- One video may cover a single big story, or combine 2-3 stories that share a real thread. \
Combine only when the connection makes the video better; never pad with unrelated stories.
- Never cover the same story in two videos. Newsletters often repeat a story under different \
titles; treat those as one.

The scene library (use `type` exactly; the visual is the hero, text is its label)
- title: opens every video. `kicker` is a 1-2 word topic label; `headline` is the story in \
one line; `icon` is a pictogram for the subject. Its `say` is the spoken hook: open a \
curiosity gap or lead with the single most surprising concrete fact, at most 14 words. Never \
a greeting or "In today's news".
- stat: one number as the hero. `value` is written short ("5.1x", "$7B", "22%", "1,200"); \
`label` says what it is without repeating the number; `context` is one optional line; \
`source` is an optional source name. A percentage up to 100 is drawn as a progress ring. \
When the story gives a series of numbers (a range, growth over time), put them in `trend` \
(2-8 numbers, oldest first) and they are drawn as a mini column chart under the number.
- chart: 2-5 points, each with a short `label` and a numeric `value`, a `title`, an optional \
`unit`, `highlight` (the index that matters) and a `kind`: "bars" (horizontal, for named \
things), "columns" (vertical, for a small series), "line" (growth or decline over time), \
"dots" (shares of 100, e.g. 12% vs 88%: values must sum to at most 100). Only from numbers \
in the stories.
- comparison: two things side by side, each with a `label`, a `value` and a short `note`. \
Set `number` on both sides when the values are comparable quantities (two discs are drawn to \
scale); otherwise 0 on both and give each side an `icon`.
- timeline: 2-4 steps, each with a short `label` ("2024", "Now", "Next") and `text`; a marker \
travels down the rail as the steps are spoken.
- diagram: a concept drawn, with `kind` "flow" (2-4 nodes, a process in order), "stack" (2-4 \
layers, bottom to top), "hub" (a `center` with 3-5 things around it) or "funnel" (3-4 \
narrowing stages). Each node has a `label` and a short `text`.
- device: a product moment as a wireframe, with `kind` "phone" (notifications), "laptop" \
(an app window with rows), "terminal" (commands and output) or "chat" (alternating messages). \
`lines` are the 2-4 lines on its screen, written like the real product would show them; \
`label` is the app or window name. Use it for launches, features and developer tools.
- statement: one sentence the viewer reads, with an optional `emphasis` of 1-3 words copied \
exactly from it, an optional small `label` ("The catch", "Why it matters") and an `icon`.
- list: 2-4 short items under an optional title, one `icon` per item. Mention the items in \
the spoken line, in order, so each appears as it is said.
- quote: a real quotation from the source and who said it. Only when the source actually \
quotes someone.
- outro: closes every video. `text` is the closing line the viewer reads; its `say` is the \
spoken outro, at most 14 words: a reason to comment or follow, tied to the topic.
- object3d, depth, kinetic and glass are the rich scenes of the cinematic look. Use them only \
when a "Look: cinematic" note follows below; otherwise never.
Pictograms (`icon`): each stands for something; pick the one that means what the scene is \
about, use '' when none fits, never a filler, and never the same icon in two scenes in a row. \
Questions and calls to comment use "comment" or "question" (the outro always gets "comment"). \
The set:
{ICON_LIST}

Writing each video
- Structure: a title scene, then 3-7 scenes, then an outro. Prefer the graphic scenes: a \
number gets a stat or chart, a before/after a comparison, a sequence a timeline, a process or \
structure a diagram, a product or launch a device. Rules the plan is checked against: at \
least two graphic scenes (stat, chart, comparison, timeline, diagram, device) per video, \
never two text-only scenes (statement, list, quote) in a row, and at most two of the same \
type in a row. Vary the types across the video; every scene should give the viewer something \
new to look at, not just new text.
- Length: 100-110 spoken words in total across all `say` lines, never under 98 and never \
over 115: the voice speaks about 2.5 words per second, so that is 40-44 seconds. Each scene's `say` is \
one or two short sentences, 8-22 words, conversational, as if explaining to a smart friend. \
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

EXPLAINER_WRITER_SYSTEM = EXPLAINER_WRITER_SYSTEM.replace(
    "{ICON_LIST}", "\n".join(f"- {name}: {meaning}" for name, meaning in ICON_MEANINGS.items()) + "\n"
).replace("@SCOPE@", factcheck.SCOPE_NOTE)

CINEMATIC_NOTE = """
Look: cinematic
- This video renders in the cinematic look: a near-black ground with a perspective grid floor, \
glowing accents in the reference's colours, vignette and grain. Think of a dark 3D motion-graphics \
explainer about infrastructure, networks, hardware or security. The rich scenes below now fit; use \
2-3 of them across the video, mixed with the regular library, and still keep every rule above.
- object3d: a 3D hero drawn with Three.js. `kind` "stack" (2-5 glowing layers bottom to top, each a \
`label` and short `text`: tiers, versions, protocol layers, a pipeline), "bars" (2-5 3D columns from \
`series` numbers, a `unit`, a `highlight`), "orbit" (a `center` with 3-5 `nodes` circling it: an \
ecosystem, dependencies, a platform and its users) or "cards" (2-4 floating cards: products, teams, \
options). At most 4 object3d scenes per video. Use the data from the stories, never decoration.
- depth: 2-4 layered planes the camera pushes through, front to back, each with a `label` and \
`text`: a chain of causes, a sequence of steps, "behind this is this". Deepest layer is the point.
- kinetic: glowing kinetic type: 1-3 `lines` under 24 characters that land one by one, an optional \
big `value` (a numeral or a short word under 4 characters, e.g. "5", "3x", "OSI") and a small `label`. \
A chapter title, a punchline, the single phrase to remember.
- glass: a glassy HUD panel with a header `label` and 2-4 rows (`nodes`: label is the key, text is \
the value under 22 characters): a spec sheet, a routing table, a price list, a status readout.
"""

THREE_D_NOTE = """
This look is a 3D look: every scene, whatever its type, is drawn in 3D from its content. Give it real \
content to draw. The icon you pick chooses the 3D object, so pick the icon that NAMES the thing the scene \
is about (chip, brain, robot, dollar or money, lock or shield, cloud, database, globe, users, box); never \
an icon just to decorate. Put the real figures in the words (a stat's value, a comparison's numbers, a \
chart's series) because figures become 3D bars, towers and gauges; use lists, timelines and diagrams with \
short real labels because they become exploded stacks and hubs; and keep statements short with an \
emphasised phrase because they are set as glowing type in depth. Do not invent numbers or labels for the \
sake of the visual.
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
                    "evidence": factcheck.EVIDENCE_SCHEMA,
                },
                "required": ["title", "badge", "story_ids", "scenes", "post_caption", "hashtags", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["videos"],
    "additionalProperties": False,
}


def _system_prompt() -> str:
    """The explainer prompt plus the active character's persona (tone only;
    the avatar image is irrelevant in this style), plus the cinematic
    scene notes when the resolved look (theme or pinned recipe) asks for
    the cinematic look."""
    prompt = EXPLAINER_WRITER_SYSTEM + factcheck.GROUNDING_RULES
    try:
        from .characters import active

        character = active()
    except Exception:  # a missing CHARACTER folder is reported by run_pipeline, not here
        character = None
    if character and character.persona:
        prompt += (
            f"\nNarrator character\n- The narrator is {character.name}: {character.persona.rstrip('. ')}. "
            "This shapes the tone and word choice of the spoken lines only. Every fact, number and "
            "claim must still come from the stories, and the word budget, scene rules and caps above "
            "still apply.\n"
        )
    try:
        from .look import cinematic_requested, look_from_env, threed_requested
        from .looks import get_preset

        three_d = threed_requested()
        if cinematic_requested() or three_d:
            prompt += CINEMATIC_NOTE
            log.info("Explainer writer: offering the rich 3D scenes (the look is cinematic or 3D)")
        if three_d:
            prompt += THREE_D_NOTE
        preset = get_preset(look_from_env().get("preset") or "")
        if preset and preset.emphasis:
            prompt += (f"\nLook preset: {preset.name}\n- {preset.description} Prefer these scene types where "
                       f"the story supports them: {', '.join(preset.emphasis)}. Every rule above still applies.\n")
    except Exception as exc:  # noqa: BLE001 - the look never blocks writing
        log.warning("Could not resolve the look for the writer (%s: %s); writing the paper look", type(exc).__name__, exc)
    return prompt


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
        client = anthropic.Anthropic(timeout=ANTHROPIC_TIMEOUT_SECONDS, max_retries=2)
        response = _stream_with_retries(
            client, anthropic,
            model=WRITER_MODEL,
            max_tokens=64000,
            system=_system_prompt(),
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
    except (anthropic.APIError, WriterNetworkError) as exc:
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


def _stories_of(video: Any, candidates: list[Story] | None) -> list[Story]:
    ids = [i for i in (video.get("story_ids") or [] if isinstance(video, dict) else []) if isinstance(i, int)]
    return [candidates[i] for i in dict.fromkeys(ids) if candidates and 0 <= i < len(candidates)]


def _check_video(video: Any, candidates: list[Story] | None) -> ScenePlan:
    """One video's plan validated (strict), plus its evidence against its stories when they are known."""
    if not isinstance(video, dict):
        raise ScenePlanError(["a video must be an object"])
    plan = validate_plan(video.get("scenes"), strict=True)
    stories = _stories_of(video, candidates)
    if candidates is not None:
        if not stories:
            if factcheck.evidence_strict("explainer"):  # a video with no source cannot be checked
                raise ScenePlanError(["story_ids must name at least one of the stories by its [id]"])
            return plan
        source = "\n".join(factcheck.story_text(s) for s in stories)
        errors, warnings, _ = factcheck.check_evidence(video.get("evidence"), [sc.say for sc in plan.scenes], source)
        if errors and factcheck.evidence_strict("explainer"):
            raise ScenePlanError(errors)
        plan.repairs += [f"evidence (not enforced): {e}" for e in errors] + [f"evidence: {w}" for w in warnings]
    return plan


def _validated_videos(user: str, n_videos: int, candidates: list[Story] | None = None,
                      first: dict | None = None) -> list[tuple[dict, ScenePlan]] | None:
    """Asks for the videos, validates every plan (strict), and gives Claude
    one chance to fix the rejected ones with the validator's exact errors
    before they are dropped. Returns the accepted (video, plan) pairs in
    order; None when Claude could not be used at all."""
    data = first if first is not None else _call_writer(user)
    if data is None:
        return None
    videos = data.get("videos", [])[:n_videos]
    results: list[tuple[dict, ScenePlan | None, list[str]]] = []
    for video in videos:
        try:
            results.append((video, _check_video(video, candidates), []))
        except ScenePlanError as exc:
            results.append((video, None, exc.errors))
    rejected = [(i, v, errs) for i, (v, p, errs) in enumerate(results) if p is None]
    if rejected:
        notes = "\n".join(
            f"- video {i + 1} ({str(v.get('title', ''))[:60]!r}):\n  " + "\n  ".join(errs) for i, v, errs in rejected
        )
        log.info("Explainer: %d of %d plans rejected, asking Claude to fix them once", len(rejected), len(results))
        # Only the rejected videos are rewritten: a whole day of plans again (~5k output tokens a video) would run
        # into the output limit.
        repair = (
            user + "\n\nThe automatic checks rejected these videos you wrote:\n" + notes
            + f"\n\nRewrite ONLY these {len(rejected)} videos so they pass, in the order listed, each with the same "
            "story_ids.\n\nThe rejected videos as you wrote them:\n" + json.dumps([v for _, v, _ in rejected])
        )
        data2 = _call_writer(repair)
        if data2 is not None:
            videos2 = factcheck.align_rewrites([v for _, v, _ in rejected], list(data2.get("videos") or []))
            for n, (i, _, _) in enumerate(rejected):
                if videos2[n] is None:
                    continue
                try:
                    results[i] = (videos2[n], _check_video(videos2[n], candidates), [])
                except ScenePlanError as exc:
                    results[i] = (videos2[n], None, exc.errors)
    accepted = []
    for video, plan, errs in results:
        title = str(video.get("title", "") if isinstance(video, dict) else "").strip() or "Untitled"
        if plan is None:
            factcheck.note_drop("explainer", "validator", title, _stories_of(video, candidates), errs)
            continue
        for note in plan.repairs:
            log.info("Explainer: %r plan repair: %s", title, note)
        accepted.append((video, plan))
    return accepted


def _candidate(video: dict, plan: ScenePlan, candidates: list[Story]) -> factcheck.Candidate:
    stories = _stories_of(video, candidates)
    source = "\n".join(factcheck.story_text(s) for s in stories)
    _, warnings, mapping = factcheck.check_evidence(video.get("evidence"), [sc.say for sc in plan.scenes], source)
    title = str(video.get("title", "")).strip() or "Untitled"
    return factcheck.Candidate(title=title, stories=stories,
                               items=factcheck.items_explainer(plan.to_list(), title, str(video.get("post_caption", ""))),
                               key=(video, plan), evidence=mapping, evidence_warnings=warnings)


def _fact_check_pass(user: str, accepted: list[tuple[dict, ScenePlan]],
                     candidates: list[Story]) -> list[tuple[dict, ScenePlan, dict]]:
    """factcheck.gate over the validated plans: one repair of a blocked video, verified again, else dropped."""

    def rewrite(pairs):
        prompt = (user + "\n\n" + factcheck.repair_prompt(
            f"Return exactly {len(pairs)} videos, in the order listed, each with the same story_ids.", pairs)
            + "\n\nThe videos as you wrote them:\n" + json.dumps([c.key[0] for c, _ in pairs]))
        data = _call_writer(prompt)

        def check(v):
            try:
                return _candidate(v, _check_video(v, candidates), candidates), []
            except ScenePlanError as exc:
                return None, exc.errors

        return factcheck.checked_rewrites([c.key[0] for c, _ in pairs], list((data or {}).get("videos") or []), check,
                                          lambda tail: list((_call_writer(prompt + tail) or {}).get("videos") or []))

    if not accepted:
        return []
    result = factcheck.gate("explainer", [_candidate(v, p, candidates) for v, p in accepted], rewrite)
    return [(c.key[0], c.key[1], rec) for c, rec in result.accepted]


def _apply_style_library(plan: ScenePlan, title: str) -> None:
    """With a non-empty style library (STYLE_LIBRARY), one planner call per
    video chooses recipes from the owner's references for each scene; the
    plan is untouched otherwise, and on any failure. STYLE_RECIPE (the
    look's recipe policy) can switch this off ("none") or pin one recipe
    instead of asking the planner ("auto", the default)."""
    try:
        from .styles import library_enabled, style_scene_plan

        policy = os.environ.get("STYLE_RECIPE", "auto").strip().lower() or "auto"
        if policy == "none":
            return
        if policy != "auto":
            from .styles.planner import apply_recipe_policy

            apply_recipe_policy(plan, policy)
            return
        if library_enabled():
            style_scene_plan(plan, title)
    except Exception as exc:  # noqa: BLE001 - styling never blocks a video
        log.warning("Style library skipped for %r (%s: %s)", title, type(exc).__name__, exc)


EXPLAINER_VIDEOS_PER_CALL = 5  # ten plans with evidence took 55.9k of the 64k output tokens in one call (2026-11-09)


def _first_drafts(candidates: list[Story], n_videos: int) -> dict | None:
    """The day's first drafts in parallel calls of at most EXPLAINER_VIDEOS_PER_CALL videos, each from its own
    round-robin share of the stories (ids stay global). None only when every call failed."""
    from concurrent.futures import ThreadPoolExecutor

    k = -(-n_videos // EXPLAINER_VIDEOS_PER_CALL)
    counts = [n_videos // k + (1 if g < n_videos % k else 0) for g in range(k)]
    shares = [[i for i in range(len(candidates)) if i % k == g] for g in range(k)]

    def write(g: int) -> dict | None:
        stories = "\n\n".join(f"[{i}] ({candidates[i].edition} / {candidates[i].section}) {candidates[i].title}"
                               f"{_site(candidates[i])}\n{_trim(candidates[i].summary, WRITER_SUMMARY_CHARS)}"
                               for i in shares[g])
        return _call_writer(f"Write exactly {counts[g]} videos from the stories below, each as a scene plan. Order "
                            f"them with the strongest video first. Reference stories by their [id].\n\n{stories}")

    with ThreadPoolExecutor(max_workers=min(3, k)) as pool:
        answers = list(pool.map(write, range(k)))
    if all(a is None for a in answers):
        return None
    videos = [v for a, n in zip(answers, counts) for v in list((a or {}).get("videos") or [])[:n]]
    log.info("Explainer: %d first drafts from %d parallel writer calls (%d failed)", len(videos), k,
             sum(a is None for a in answers))
    return {"videos": videos}


def llm_explainer_scripts(candidates: list[Story], n_videos: int) -> list[ExplainerScript] | None:
    """The explainer counterpart of script_writer._llm_scripts: one call
    for the day's videos. Videos whose plan fails validation are skipped
    with a logged reason (an empty list means every plan was rejected: no
    video that day rather than a broken one); None means Claude could not
    be used at all."""
    stories = "\n\n".join(
        f"[{i}] ({s.edition} / {s.section}) {s.title}{_site(s)}\n{_trim(s.summary, WRITER_SUMMARY_CHARS)}"
        for i, s in enumerate(candidates)
    )
    user = (
        f"Write exactly {n_videos} videos from today's stories below, each as a scene plan. "
        f"Order them with the strongest video first. Reference stories by their [id].\n\n{stories}"
    )
    first = _first_drafts(candidates, n_videos) if n_videos > EXPLAINER_VIDEOS_PER_CALL else None
    if n_videos > EXPLAINER_VIDEOS_PER_CALL and first is None:
        return None
    accepted = _validated_videos(user, n_videos, candidates, first=first)
    if accepted is None:
        return None
    scripts = []
    for video, plan, rec in _fact_check_pass(user, accepted, candidates):
        _apply_style_library(plan, str(video.get("title", "")))
        script = script_from_plan(plan, str(video.get("title", "")).strip() or "Untitled",
                                  str(video.get("badge", "NEWS")), _stories_of(video, candidates),
                                  str(video.get("post_caption", "")), list(video.get("hashtags", [])))
        script.fact_check = rec
        scripts.append(script)
    if len(scripts) < n_videos:
        log.warning("Explainer: %d of %d videos dropped for invalid scene plans", n_videos - len(scripts), n_videos)
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
                                    script.post_caption, script.hashtags, writer="heuristic"))
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
    accepted = _validated_videos(user, 1)
    if accepted is None:
        raise RuntimeError("Claude is unavailable (no ANTHROPIC_API_KEY, SDK, or the call failed)")
    if not accepted:
        raise ScenePlanError(["Claude's plan failed validation twice; see the log for the errors"])
    return accepted[0][1]


def _site(story) -> str:
    """" [link: nytimes.com]": who published the linked article, so an allegation can be attributed to it."""
    from .factcheck import publisher

    site = publisher(getattr(story, "url", ""))
    return f" [link: {site}]" if site else ""
