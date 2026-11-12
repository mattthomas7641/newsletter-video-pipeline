"""Must-cover stories: the day's most important news reaches the viewer even when a video about it fails."""
from __future__ import annotations

import copy
from datetime import date

import pytest

from newsletter_video import avx_writer as w
from newsletter_video import factcheck as fc
from newsletter_video import must_cover as mc
from newsletter_video import script_writer as sw
from newsletter_video.scraper import Story
from tests.avx_fixtures import STORY, VIDEO


def S(title, summary="A short summary of the story.", edition="tech"):
    return Story(edition, "AI", title, summary, "https://example.com/" + str(abs(hash(title))))


@pytest.fixture
def day(monkeypatch):
    monkeypatch.setenv("FACTCHECK", "1")
    monkeypatch.delenv("MUST_COVER_COUNT", raising=False)
    monkeypatch.delenv("MUST_COVER_EXTRA", raising=False)
    fc._drops.clear()
    mc.clear()
    yield monkeypatch
    mc.clear()


# --- the same news under two titles (real pairs from 2026-11-10 and 2026-11-11) ----------------------------------

IPO = S("Anthropic to Seek $1.3 Trillion Valuation in IPO",
        "Anthropic plans to sell 825 million shares at a range of $70 to $78 a share in its initial public offering, "
        "which is expected to start trading on Thursday of next week. The company expects to raise an additional $26 "
        "billion from big investors, including Google and AMD.")
ROADSHOW = S("Anthropic's IPO Roadshow",
             "Anthropic kicked off its IPO roadshow, offering 825 million shares priced between $70 and $78.", "ai")
SHEETS = S("Working with Claude Sheets, Whiteboards, and Interactive Connectors",
           "Anthropic introduced Claude Sheets, Whiteboards, and Data, new artifact types that let teams build "
           "spreadsheets, organize ideas, and create dashboards with Claude without switching apps.", "ai")
CHIPS = S("Google Escalates Nvidia Rivalry With Specialized Versions of AI Chips",
          "Google is rolling out two new variations of its Tensor Processing Units. The 8tx is aimed at handling "
          "secure, confidential work. The 8i Max is designed to run AI applications that require long-context windows.")
TPU = S("Google's New TPU Lineup", "Google introduced TPU 8i Max for massive context windows and higher compute "
        "density, alongside TPU 8tx for secure and confidential inference.", "ai")
ADS_SEARCH = S("AI search activity is nearly invisible in Google Ads search terms reports: Study",
               "A study found AI search activity barely shows up in Google Ads reports.", "marketing")
ADS_IMG = S("ChatGPT Is Now Showing Ads While Images Are Being Generated",
            "For free ChatGPT users, image generation now comes with ads attached.", "marketing")
ADS_VIS = S("ChatGPT visual ads are rolling out", "OpenAI is rolling out visual ads in ChatGPT.", "founders")
LEADS = S("Before You Spend More on Ads, Find Out What Happened to Your Last 300 Leads",
          "Audit your last leads before spending more on ads.", "marketing")
GEMINI = S("Gemini 4 Carbon is now available", "Google released Gemini 4 Carbon to developers and consumers.")
HEALTH = S("Introducing Gemini for healthcare", "Google introduced Gemini for healthcare providers.", "ai")


@pytest.mark.parametrize("a,b", [(IPO, ROADSHOW), (CHIPS, TPU), (ADS_IMG, ADS_VIS)])
def test_the_same_announcement_under_two_titles_is_one_event(a, b):
    assert mc.same_event(a, b) and mc.same_event(b, a)


@pytest.mark.parametrize("a,b", [(IPO, SHEETS), (CHIPS, ADS_SEARCH), (LEADS, ADS_VIS), (GEMINI, HEALTH)])
def test_different_news_about_the_same_company_stays_apart(a, b):
    assert not mc.same_event(a, b)


def test_events_group_in_input_order():
    groups = mc.event_groups([IPO, SHEETS, ROADSHOW, CHIPS, TPU])
    assert [[s.title for s in g] for g in groups] == [[IPO.title, ROADSHOW.title], [SHEETS.title], [CHIPS.title, TPU.title]]


# --- picking the day's list ----------------------------------------------------------------------------------------

def test_the_list_takes_one_story_per_event_from_the_top_of_the_ranking(day):
    ranked = [IPO, ROADSHOW, SHEETS, CHIPS, TPU, GEMINI]
    must = mc.set_day(ranked, 3)
    assert [s.title for s in must] == [IPO.title, SHEETS.title, CHIPS.title]
    assert mc.mates_of(ROADSHOW.title) == {IPO.title, ROADSHOW.title}


def test_the_count_is_configurable_and_zero_switches_it_off(day):
    day.setenv("MUST_COVER_COUNT", "2")
    assert len(mc.set_day([IPO, SHEETS, CHIPS], 10)) == 2
    mc.clear()
    day.setenv("MUST_COVER_COUNT", "0")
    assert mc.set_day([IPO, SHEETS, CHIPS], 10) == [] and not mc.active()


def test_a_video_on_one_title_covers_its_same_news_mate(day):
    mc.set_day([IPO, ROADSHOW, SHEETS], 2)
    assert mc.missing([_script("roadshow", ROADSHOW)]) == [SHEETS]
    assert mc.report([_script("roadshow", ROADSHOW)]) == [{"title": IPO.title, "covered": True},
                                                          {"title": SHEETS.title, "covered": False}]


def test_the_writer_is_told_what_to_cover_what_is_one_story_and_why_a_retry_failed(day):
    mc.set_day([IPO, ROADSHOW, SHEETS, GEMINI], 2)
    hint = mc.writer_hint([GEMINI, IPO, SHEETS, ROADSHOW], {IPO.title: ["scene 3 (unclear): the $26B is not explained"]})
    assert "MUST-COVER: stories [1], [2]" in hint
    assert "SAME NEWS" in hint and "[1] = [3]" in hint
    assert "RETRY [1]" in hint and "$26B is not explained" in hint
    assert "[2]" not in mc.writer_hint([GEMINI, IPO, SHEETS], only=[0, 1])  # a parallel call only hears its own ids


# --- refill: retry the important story, not a minor one --------------------------------------------------------------

def _script(title, story):
    return sw.Script(title=title, badge="AI", hook="h", beats=[], outro="o", stories=[story], writer="claude")


def test_a_dropped_must_cover_story_is_retried_with_its_reasons_before_minor_stories(day):
    minor = [S(f"Minor story {i}") for i in range(4)]
    stories = [IPO, SHEETS] + minor
    mc.set_day(stories, 2)
    asked = []

    def write(pool, n, done, notes=None):
        asked.append(([s.title for s in pool], n, notes))
        if len(asked) == 1:  # the IPO video is dropped by the editor, a minor story ships instead
            fc.note_drop("avx", "editor", "IPO video", [IPO], ["still flagged by the editor:", "scene 3 (unclear): x"])
            return [_script("sheets", SHEETS)]
        return [_script(f"refill {pool[k].title}", pool[k]) for k in range(n)]

    scripts = sw.with_refill("avx", write, stories, 2)
    assert [s.title for s in scripts] == ["sheets", f"refill {IPO.title}"]
    pool, n, notes = asked[1]
    assert pool[0] == IPO.title and n == 1  # the dropped must-cover story comes first
    assert notes == {IPO.title: ["scene 3 (unclear): x"]}


def test_a_must_cover_story_gets_one_retry_not_endless_ones(day):
    stories = [IPO, SHEETS] + [S(f"Minor story {i}") for i in range(6)]
    mc.set_day(stories, 2)
    pools = []

    def write(pool, n, done, notes=None):
        pools.append([s.title for s in pool])
        out = []
        for s in pool[:n]:
            if s.title == IPO.title:
                fc.note_drop("avx", "fact check", "IPO", [IPO], ["contradicted"])
            else:
                out.append(_script(s.title, s))
        return out

    sw.with_refill("avx", write, stories, 2)
    assert pools[1][0] == IPO.title  # retried once
    assert all(IPO.title not in p for p in pools[2:])  # dropped twice: not tried a third time


def test_refill_may_go_past_the_count_to_cover_a_missing_must_cover_story(day):
    minor = [S(f"Minor story {i}") for i in range(3)]
    stories = [IPO, SHEETS, CHIPS] + minor
    mc.set_day(stories, 3)
    asked = []

    def write(pool, n, done, notes=None):
        asked.append(n)
        if len(asked) == 1:  # the writer filled the day but skipped CHIPS for a minor story
            return [_script("ipo", IPO), _script("sheets", SHEETS), _script("minor", minor[0])]
        return [_script(pool[0].title, pool[0])]

    scripts = sw.with_refill("avx", write, stories, 3)
    assert len(scripts) == 4 and scripts[-1].stories == [CHIPS]
    day.setenv("MUST_COVER_EXTRA", "0")
    asked.clear()
    assert len(sw.with_refill("avx", write, stories, 3)) == 3 and asked == [3]


def test_without_a_must_cover_day_refill_behaves_as_before(day):
    stories = [S(f"Story {i}") for i in range(5)]
    calls = []

    def write(pool, n, done):  # the old three-argument writer still works
        calls.append(n)
        if len(calls) == 1:
            fc.note_drop("avx", "editor", "x", [pool[0]], ["unclear"])
            return [_script("a", pool[1])]
        return [_script(f"r{k}", pool[k]) for k in range(n)]

    assert len(sw.with_refill("avx", write, stories, 2)) == 2 and calls == [2, 1]


# --- the avatar explainer writer ---------------------------------------------------------------------------------------

def test_a_second_video_on_the_same_news_is_dropped_before_the_editor(day):
    other = copy.deepcopy(STORY)
    mc.set_day([STORY], 1)
    scenes = w.check_video(copy.deepcopy(VIDEO), [STORY])[0]
    pair = (copy.deepcopy(VIDEO), scenes)
    out = w._drop_duplicate_news([pair, copy.deepcopy(pair)], [STORY, other])
    assert len(out) == 1 and fc.drops_since(0)[-1]["stage"] == "duplicate"
    assert w._drop_duplicate_news([copy.deepcopy(pair)], [STORY], taken={STORY.title}) == []


def test_a_thin_must_cover_story_is_still_offered_to_the_writer(day):
    rich = [S(f"Rich story {i}", "word " * 60) for i in range(6)]
    thin = S("Anthropic files for IPO", "Short summary.")
    mc.set_day([thin], 1)
    assert thin in w.select_stories(rich + [thin], 3)


def test_lineup_days_keep_a_must_cover_video_written_past_the_count(day):
    from newsletter_video import lineup

    look = {"style": "avx", "character": "puck"}
    slots = [{"slot": k + 1, "look": look, "batch": 1, "batch_name": "Avatar", "accent_mode": "one", "label": "Avatar"}
             for k in range(2)]
    made = [_script("a", IPO), _script("b", SHEETS), _script("extra", CHIPS)]
    out = lineup.write_scripts(slots, date(2026, 11, 11), 2, [], set(), lambda *a, **k: list(made))
    assert [s.title for s in out] == ["a", "b", "extra"] and out[-1].lineup["slot"] == 3


def test_jevs_need_to_know_score_picks_the_list_when_it_is_there(day):
    ranked = [ADS_SEARCH, LEADS, IPO, GEMINI]  # ranked by "surprising" first
    must = mc.set_day(ranked, 2, importance={IPO.title: 0.95, GEMINI.title: 0.9, ADS_SEARCH.title: 0.2, LEADS.title: 0.1})
    assert [s.title for s in must] == [IPO.title, GEMINI.title]
