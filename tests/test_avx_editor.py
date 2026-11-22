"""Script coherence for the avatar explainer: thin stories skipped, no two videos with one shape, and the
editor (critic) pass with one rewrite. Every Claude call is a stub."""
from __future__ import annotations

import copy

from newsletter_video import avx_writer as w
from newsletter_video.scraper import Story
from tests.avx_fixtures import SCENES, STORY, VIDEO, _scene

# The failing example from 2026-11-09 (18_charge-by-the-token...): a garbled paraphrase of the story's opening
# line as the hook, and a background number (700+ companies) dressed up as the key stat.
MARGIN_STORY = Story(
    edition="founders", section="Fundraising", title="7 Ways to Handle Your AI Gross Margin Problem",
    summary=("Charge AI customers by the token, and they'll ask whether you're four times as good as the model "
             "you're marking up. An investor in more than 700 companies walks through how AI startups should price "
             "and who pays the token bill. The advice is to charge for a unit your product controls, like a "
             "personalized email from an AI SDR. Keep measuring the token cost behind each one, since two support "
             "resolutions can bring in the same revenue and cost wildly different amounts."),
    url="https://example.com/margins")
CHARGE_HOOK = ("Charge by the token, they ask four times as good? Customers will wonder whether you beat the model "
               "by that much.")


def _margin_video(hook: str = CHARGE_HOOK) -> dict:
    scenes = [
        _scene(style="kinetic", pose="neutral", eyebrow="AI pricing", say=hook,
               lines=[{"text": "Charge by the", "em": False, "cue": "Charge"}, {"text": "token?", "em": True, "cue": "token"}],
               cue="token"),
        _scene(style="stat", pose="point", eyebrow="The investor",
               say="One investor in more than seven hundred companies walks through how AI startups should price.",
               value=700, suffix="+", label="Companies invested in", cue="seven"),
        _scene(style="versus", pose="present", eyebrow="The advice",
               say="The advice is to charge for a unit your product controls, not for tokens, like one personalized email.",
               a={"name": "Per unit", "tag": "You control", "items": ["Personal email", "AI SDR"], "initials": "", "cue": "unit"},
               b={"name": "Tokens", "tag": "Marked up", "items": ["Compare to model", "Margin exposed"], "initials": "",
                  "cue": "tokens"}),
        _scene(style="list", pose="think", eyebrow="Keep watching",
               say=("Keep measuring the token cost behind each sale, since two support answers can bring in the same "
                    "revenue and still cost wildly different amounts."),
               label="Keep measuring", items=[{"text": "Token cost", "cue": "token"}, {"text": "Each sale", "cue": "sale"}]),
        _scene(style="outro", pose="shrug", eyebrow="Your call",
               say="Would you pay per token or per result? Tell me what you think below, and follow for more.",
               lines=[{"text": "Pay per token", "em": False, "cue": "pay"}, {"text": "or per result?", "em": True, "cue": "or"}],
               text="Tell me below", text_cue="Tell"),
    ]
    return {"title": "Charge by the token?", "badge": "AI PRICING", "story_ids": [1], "scenes": scenes,
            "post_caption": "How should AI startups price?", "hashtags": ["ai", "startups"]}


def _keyword_critic(calls):
    """A stand-in editor: flags garbled hooks (a question-fragment opening) and background numbers as stats."""

    def critic(user: str):
        calls.append(user)
        reviews = []
        for n, block in enumerate(user.split("VIDEO ")[1:], start=1):
            problems = []
            if "they ask four times as good?" in block:
                problems.append({"scene": 1, "kind": "unclear", "detail": "the hook is a garbled half-sentence"})
            if "Companies invested in" in block:
                problems.append({"scene": 2, "kind": "graphic_mismatch", "detail": "700+ companies does not support the point"})
            problems.append({"scene": 3, "kind": "unclear", "severity": "minor", "detail": "could be punchier"})
            reviews.append({"video": n, "verdict": "fix" if len(problems) > 1 else "ok", "problems": problems})
        return {"reviews": reviews}

    return critic


def _fixed_margin_video() -> dict:
    v = _margin_video("AI startups that charge by the token get asked if they are four times better than the model.")
    v["scenes"][0]["lines"] = [{"text": "AI startups that", "em": False, "cue": "AI"},
                               {"text": "charge by the", "em": False, "cue": "charge"},
                               {"text": "token get asked", "em": True, "cue": "token"}]
    v["scenes"][1] = _scene(style="list", pose="point", eyebrow="Who pays",
                            say="An investor explains who should pay the token bill and how to price around it.",
                            label="Who pays the bill", items=[{"text": "Token bill", "cue": "token"},
                                                              {"text": "Pricing", "cue": "price"}])
    return v


def test_the_charge_by_the_token_video_is_flagged_rewritten_and_shipped(monkeypatch):
    critic_calls, writer_calls = [], []
    answers = [{"videos": [copy.deepcopy(VIDEO), _margin_video()]}, {"videos": [_fixed_margin_video()]}]

    def writer(user):
        writer_calls.append(user)
        return answers.pop(0)

    monkeypatch.setattr(w, "_call_writer", writer)
    monkeypatch.setattr(w, "_call_critic", _keyword_critic(critic_calls))
    monkeypatch.setattr(w, "select_stories", lambda c, n: list(c))
    scripts = w.llm_avx_scripts([STORY, MARGIN_STORY], 2)
    assert len(critic_calls) == 2 and len(writer_calls) == 2  # one review of the day, one of the rewrite
    assert "garbled half-sentence" in writer_calls[1] and "700+ companies" in writer_calls[1]
    assert "Charge by the token, they ask" not in critic_calls[1]  # only the rewritten video is re-read
    assert [s.title for s in scripts] == [VIDEO["title"], "Charge by the token?"]
    assert scripts[1].hook.startswith("AI startups that charge by the token")


def test_a_video_still_flagged_after_its_rewrite_is_dropped(monkeypatch):
    answers = [{"videos": [copy.deepcopy(VIDEO), _margin_video()]}, {"videos": [_margin_video()]}]
    monkeypatch.setattr(w, "_call_writer", lambda user: answers.pop(0))
    monkeypatch.setattr(w, "_call_critic", _keyword_critic([]))
    monkeypatch.setattr(w, "select_stories", lambda c, n: list(c))
    scripts = w.llm_avx_scripts([STORY, MARGIN_STORY], 2)
    assert [s.title for s in scripts] == [VIDEO["title"]]


def test_a_rewrite_that_fails_validation_is_dropped_and_clean_videos_need_no_rewrite(monkeypatch):
    bad = _fixed_margin_video()
    bad["scenes"][1]["label"] = "Who pays the bill: 99 ways"  # a number the story does not give
    answers = [{"videos": [copy.deepcopy(VIDEO), _margin_video()]}, {"videos": [bad]}, {"videos": [bad]}]
    writer_calls = []
    monkeypatch.setattr(w, "_call_writer", lambda user: writer_calls.append(user) or answers.pop(0))
    monkeypatch.setattr(w, "_call_critic", _keyword_critic([]))
    monkeypatch.setattr(w, "select_stories", lambda c, n: list(c))
    assert [s.title for s in w.llm_avx_scripts([STORY, MARGIN_STORY], 2)] == [VIDEO["title"]]
    assert len(writer_calls) == 3 and "99 is not in the story" in writer_calls[2]  # one validator retry of the rewrite
    # ...and when that retry passes, the rewrite ships (after the editor reads it)
    writer_calls.clear()
    answers[:] = [{"videos": [copy.deepcopy(VIDEO), _margin_video()]}, {"videos": [bad]}, {"videos": [_fixed_margin_video()]}]
    assert [s.title for s in w.llm_avx_scripts([STORY, MARGIN_STORY], 2)] == [VIDEO["title"], "Charge by the token?"]
    # all clean: the editor reads them once and nothing is rewritten
    writer_calls.clear()
    answers[:] = [{"videos": [copy.deepcopy(VIDEO)]}]
    assert len(w.llm_avx_scripts([STORY], 1)) == 1 and len(writer_calls) == 1


def test_without_the_editor_the_validated_videos_still_ship(monkeypatch):
    monkeypatch.setattr(w, "_call_writer", lambda user: {"videos": [copy.deepcopy(VIDEO)]})
    monkeypatch.setattr(w, "_call_critic", lambda user: None)
    assert len(w.llm_avx_scripts([STORY], 1)) == 1


def test_the_editor_sees_story_lines_and_graphics():
    seen = []
    w_critic = _keyword_critic(seen)
    import newsletter_video.avx_writer as mod

    old = mod._call_critic
    mod._call_critic = w_critic
    try:
        flagged = w.critique([("A", w.check_video(_margin_video(), [STORY, MARGIN_STORY])[0], MARGIN_STORY.summary)])
    finally:
        mod._call_critic = old
    assert set(flagged) == {0} and any("graphic_mismatch" in r for r in flagged[0])
    assert "[hook] says:" in seen[0] and "graphic (stat): '700+ Companies invested in'" in seen[0]
    assert "four times as good as the model" in seen[0]  # the story text is in front of the editor


# --- shape variety ---------------------------------------------------------------------------------------

def test_two_videos_with_the_same_middle_sequence_are_sent_back(monkeypatch):
    twin = copy.deepcopy(VIDEO)
    twin["title"] = "Nvidia again"
    fixed = copy.deepcopy(VIDEO)
    fixed["title"] = "Nvidia, reshaped"
    fixed["scenes"] = [SCENES[0], SCENES[2], SCENES[1], SCENES[3], SCENES[4], SCENES[5]]  # relation before stat
    answers = [{"videos": [copy.deepcopy(VIDEO), twin]}, {"videos": [fixed]}]  # only the rejected one is rewritten
    prompts = []
    monkeypatch.setattr(w, "_call_writer", lambda user: prompts.append(user) or answers.pop(0))
    scripts = w.llm_avx_scripts([STORY], 2)
    assert "same sequence as video 1" in prompts[1]
    assert [s.title for s in scripts] == [VIDEO["title"], "Nvidia, reshaped"]


def test_sequence_clashes_only_flag_the_later_twin():
    a, b = ("stat", "list"), ("list", "stat")
    clashes = w.sequence_clashes([a, b, a, None, b])
    assert sorted(clashes) == [2, 4]
    assert "video 1's" in clashes[2] and "video 2's" in clashes[4]


def test_every_line_must_end_as_a_sentence():
    v = copy.deepcopy(VIDEO)
    v["scenes"][2]["say"] = "Chief executive Jensen Huang says demand for the new Blackwell chips is off the charts and"
    try:
        w.check_video(v, [STORY])
        raise AssertionError("expected a rejection")
    except w.AvxPlanError as exc:
        assert any("complete sentence" in e for e in exc.errors)


# --- story selection ----------------------------------------------------------------------------------

def _story(n_words: int, title: str) -> Story:
    return Story(edition="tech", section="AI", title=title, summary=" ".join(["word"] * n_words), url="")


def test_thin_stories_are_skipped_but_the_day_keeps_its_count():
    stories = [_story(60, "rich 1"), _story(12, "thin"), _story(45, "rich 2"), _story(30, "medium"), _story(25, "medium 2")]
    assert [s.title for s in w.select_stories(stories, 1)] == ["rich 1", "rich 2"]
    # 3 videos want 6 stories to choose from: the fullest thinner ones come back, never the thinnest
    assert [s.title for s in w.select_stories(stories, 3)] == ["rich 1", "rich 2", "medium", "medium 2"]
    assert w.select_stories([_story(5, "only")], 1)[0].title == "only"  # nothing else at all: still something


def test_the_writer_sees_the_whole_summary():
    long = Story(edition="tech", section="AI", title="T", summary="A sentence. " * 70, url="")
    seen = []

    def writer(user):
        seen.append(user)
        return None

    import newsletter_video.avx_writer as mod

    old = mod._call_writer
    mod._call_writer = writer
    try:
        assert w.llm_avx_scripts([long], 1) is None
    finally:
        mod._call_writer = old
    assert seen[0].count("A sentence.") > 60  # 400 characters would have been ~33


def test_the_prompt_asks_for_plain_complete_sentences_and_one_idea_per_scene():
    p = w.AVX_WRITER_SYSTEM
    assert "ONE clear, complete sentence" in p and "Never paraphrase" in p
    assert "exactly ONE idea" in p and "directly proves the point" in p
    assert "must not share one middle sequence" in p


# --- what a still-flagged rewrite does: polish ships, accuracy drops ---------------------------------------------------

def _critic_pass_with(monkeypatch, first, second):
    """Runs the editor pass on one valid video: the editor answers `first`, the writer rewrites it (the same valid
    video), the editor answers `second` for the rewrite."""
    answers = iter([first, second])
    monkeypatch.setattr(w, "critique", lambda videos: next(answers))
    monkeypatch.setattr(w, "_call_writer", lambda user: {"videos": [copy.deepcopy(VIDEO)]})
    scenes = w.check_video(copy.deepcopy(VIDEO), [STORY])[0]
    return w._critic_pass("write", [(copy.deepcopy(VIDEO), scenes)], [STORY])


def test_clarity_notes_that_survive_the_rewrite_ship_the_video(monkeypatch):
    out = _critic_pass_with(monkeypatch, {0: ["scene 1 (opaque): Muse is never explained"]},
                            {0: ["scene 1 (opaque): still not explained", "scene 4 (unclear): vague takeaway",
                                 "scene 2 (repetitive): repeats the hook"]})
    assert len(out) == 1  # accurate but a little clunky: shipped, with the notes logged


def test_an_accuracy_flag_that_survives_the_rewrite_still_drops_the_video(monkeypatch):
    for kind in ("unsupported", "graphic_mismatch", "incoherent"):
        out = _critic_pass_with(monkeypatch, {0: [f"scene 2 ({kind}): wrong"]},
                                {0: ["scene 1 (opaque): still not explained", f"scene 2 ({kind}): still wrong"]})
        assert out == [], kind


def test_a_rewrite_the_editor_clears_ships_as_before(monkeypatch):
    assert len(_critic_pass_with(monkeypatch, {0: ["scene 1 (opaque): x"]}, {})) == 1
