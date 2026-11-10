"""Clear, honest openings: the jargon glossary and opening checks (jargon.py) and the drama guard
(avx_writer.drama_problems): conflict, threat and hype only when the story itself has them."""
from __future__ import annotations

import copy

import pytest

from newsletter_video import avx_writer as w
from newsletter_video import jargon
from tests.avx_fixtures import VIDEO, STORY


# --- the opening a cold viewer must be able to follow ------------------------------------------------------

@pytest.mark.parametrize("hook", [
    "OpenAI just raised eleven billion dollars, and somehow that still missed its target.",
    "Nvidia has opened an investigation into its CEO, Jensen Huang, after the disclosure of a relationship.",
    "Apple will now let apps pitch you before you cancel a subscription.",
    "NVIDIA just released two new AI models for robots and self-driving cars.",
])
def test_clear_openings_pass(hook):
    assert jargon.opening_problems(hook) == []


@pytest.mark.parametrize("hook,fragment", [
    ("Why wouldn't the labs just do it? That is the question founders now have to answer.", "question"),
    ("Do sales get cheaper? The data says no, even though founders expect it to at scale.", "question"),
    ("Twelve brands. No hires. One owner, and AI is doing the heavy lifting.", "fragment"),
    ("The AI frontier just shifted again this week in a big way.", "abstract"),
    ("This new model has hundreds of billions of parameters, but only uses seventeen billion.", "parameters"),
    ("There are three ways to buy GPU time, and each one works very differently.", "GPU time"),
    ("More of your users will be agents, and that changes how products get adopted.", "agents"),
    ("Anthropic warns that it is very close to RSI capability, as it told investors.", "RSI"),
])
def test_unclear_openings_are_flagged_with_the_reason(hook, fragment):
    problems = jargon.opening_problems(hook)
    assert problems and any(fragment in p for p in problems), problems


def test_jargon_message_offers_the_plain_wording():
    msg = " ".join(jargon.opening_problems("Agents can now prepare your payments for you."))
    assert "AI that takes actions on its own" in msg


def test_a_quick_hits_hook_may_name_several_companies():
    assert jargon.opening_problems("Meta, Nvidia and SpaceX all made billion-dollar AI moves today.", roundup=True) == []


def test_every_glossary_definition_is_short_and_the_prompt_carries_the_glossary():
    assert all(len(plain.split()) <= 9 for plain in jargon.GLOSSARY.values())
    system = w._system_prompt()
    assert "Be understood on the first hearing" in system and "AI that takes actions on its own" in system


def test_an_unclear_hook_is_sent_back_by_the_validator():
    video = copy.deepcopy(VIDEO)
    video["scenes"][0]["say"] = "Why did Nvidia make so much money? Fifty seven billion dollars is the answer."
    with pytest.raises(w.AvxPlanError) as exc:
        w.check_video(video, [STORY])
    assert any("opens with a question" in e for e in exc.value.errors)


# --- no drama the story does not have --------------------------------------------------------------------------

def test_dramatic_words_the_story_lacks_are_flagged():
    source = "Nvidia posted record revenue of $57 billion for the quarter and said demand for its chips remains strong."
    problems = w.drama_problems(["Nvidia wins the chip battle with a shocking $57 billion quarter"], source)
    assert len(problems) == 2 and any("battle" in p for p in problems) and any("shocking" in p for p in problems)


def test_a_dramatic_word_the_story_itself_uses_is_fine():
    source = "The price war between the two cloud providers escalated as each cut rates again."
    assert w.drama_problems(["A price war is breaking out between the two providers"], source) == []


def test_plain_neutral_wording_is_never_flagged():
    source = "Mistral released the weights for Mistral Large 4, a model with a trillion parameters."
    assert w.drama_problems(["Mistral released a very large model that anyone can download"], source) == []


def test_whole_words_only():
    assert w.drama_problems(["The warrant and the warning came on the same day"], "A story about a court order.") == []


def test_the_validator_rejects_invented_drama_in_the_title_or_lines():
    video = copy.deepcopy(VIDEO)
    video["title"] = "Nvidia crushes rivals in a shocking quarter"
    with pytest.raises(w.AvxPlanError) as exc:
        w.check_video(video, [STORY])
    assert any("dramatic word" in e for e in exc.value.errors)


def test_the_prompt_and_editor_state_the_only_when_the_story_has_it_rule():
    system = w._system_prompt()
    assert "ONLY when the story itself has it" in system and "never turn a neutral story into a conflict" in system.lower().replace("never turn a \\\n", "never turn a ")
    assert "contain" in w.CRITIC_SYSTEM and "neutral story dressed up" in w.CRITIC_SYSTEM
