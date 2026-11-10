"""Thin stories: expanded from their linked article, or bundled into one quick-hits video, so short but
important news is not lost for being short (avx_writer.enrich_thin_stories / _roundup_scripts)."""
from __future__ import annotations

import copy

from newsletter_video import avx_writer as w
from newsletter_video.scraper import Story
from tests.avx_fixtures import VIDEO


def thin(title, n_words=20, url="https://example.com/a"):
    return Story("ai", "Business", title, " ".join(["word"] * n_words), url)


def rich(title):
    return Story("ai", "Business", title, " ".join(["word"] * 60), "https://example.com/r")


# --- expanding a thin story from its article ----------------------------------------------------------------

def test_a_thin_story_gets_the_start_of_its_article_and_becomes_rich(monkeypatch):
    monkeypatch.delenv("AVX_ENRICH_THIN", raising=False)
    stories = [thin("Meta offers $95B in stock", 14), rich("A long story")]
    article = "Meta plans a $95 billion stock offering to fund AI infrastructure. " * 20
    out = w.enrich_thin_stories(stories, fetch=lambda url: article)
    assert out[1] is stories[1]  # a rich story is untouched
    assert out[0] is not stories[0] and stories[0].summary.count("From the linked article") == 0  # a copy
    assert "From the linked article: Meta plans a $95 billion" in out[0].summary
    assert w._words(out[0].summary) >= w.MIN_SUMMARY_WORDS and not w.is_thin(out[0])
    assert len(out[0].summary) <= len(stories[0].summary) + len("\n\nFrom the linked article: ") + w.ENRICH_EXCERPT_CHARS + 2


def test_an_unreadable_article_leaves_the_story_as_it_was(monkeypatch):
    monkeypatch.delenv("AVX_ENRICH_THIN", raising=False)
    stories = [thin("Behind a paywall", 15), thin("No link", 15, url="")]
    out = w.enrich_thin_stories(stories, fetch=lambda url: "")
    assert out[0] is stories[0] and out[1] is stories[1]


def test_enrichment_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AVX_ENRICH_THIN", "0")
    stories = [thin("A thin one", 15)]
    assert w.enrich_thin_stories(stories, fetch=lambda url: "x " * 200) == stories


# --- the quick-hits video ---------------------------------------------------------------------------------------

def _wire(monkeypatch, video_ids, capture=None):
    """Stand in for the model: the writer answers with one video built on `video_ids`; the checks pass it through."""
    def validated(user, n, pool, avoid=()):
        if capture is not None:
            capture.update(user=user, n=n, pool=list(pool))
        v = copy.deepcopy(VIDEO)
        v["story_ids"] = video_ids
        return [(v, [{"style": "kinetic", "say": "x"}])]

    monkeypatch.setattr(w, "_validated_videos", validated)
    monkeypatch.setattr(w, "_critic_pass", lambda user, acc, pool: acc)
    monkeypatch.setattr(w, "_fact_check_pass", lambda user, acc, pool, avoid=(): [(v, sc, {"verdict": "ok"}) for v, sc in acc])
    monkeypatch.setattr(w, "script_from_scenes", lambda scenes, title, badge, stories, caption, tags:
                        type("S", (), {"title": title, "badge": badge, "stories": stories, "fact_check": None,
                                       "avx_scenes": scenes})())


def test_thin_stories_are_bundled_into_one_quick_hits_video_that_takes_one_slot(monkeypatch):
    monkeypatch.delenv("AVX_ROUNDUP", raising=False)
    seen = {}
    _wire(monkeypatch, [0, 1, 2], seen)
    pool = [rich("Big one"), thin("Meta $95B", 16), thin("Broadcom debt", 15), thin("Marvell deal", 31),
            thin("Too small to say anything", 5)]
    scripts, used = w._roundup_scripts(pool, 10)
    assert len(scripts) == 1 and len(used) == 3
    assert {id(s) for s in pool[1:4]} == used  # exactly the three thin stories it covered
    assert seen["n"] == 1 and len(seen["pool"]) == 3  # only thin stories that have something to say (>= 12 words)
    assert "QUICK HITS" in seen["user"] and "[0]" in seen["user"] and "Big one" not in seen["user"]


def test_no_quick_hits_video_without_enough_thin_stories_or_room_in_the_day(monkeypatch):
    monkeypatch.delenv("AVX_ROUNDUP", raising=False)
    _wire(monkeypatch, [0, 1])
    two_thin = [rich("Big"), thin("One", 20), thin("Two", 20)]
    assert w._roundup_scripts(two_thin, 10) == ([], set())
    three_thin = [thin(f"T{i}", 20) for i in range(3)]
    assert w._roundup_scripts(three_thin, 3) == ([], set())  # a 3-video day has no room
    monkeypatch.setenv("AVX_ROUNDUP", "0")
    assert w._roundup_scripts(three_thin, 10) == ([], set())


def test_a_quick_hits_video_that_covers_a_single_story_is_dropped(monkeypatch):
    monkeypatch.delenv("AVX_ROUNDUP", raising=False)
    _wire(monkeypatch, [0])
    scripts, used = w._roundup_scripts([thin(f"T{i}", 20) for i in range(4)], 10)
    assert scripts == [] and used == set()


def test_a_failure_in_the_quick_hits_step_never_breaks_the_day(monkeypatch):
    monkeypatch.delenv("AVX_ROUNDUP", raising=False)

    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(w, "_validated_videos", boom)
    assert w._roundup_scripts([thin(f"T{i}", 20) for i in range(4)], 10) == ([], set())


def test_the_prompt_carries_the_engagement_and_quick_hits_rules():
    system = w._system_prompt()
    for phrase in ("Keeping people watching", "Frame one is the news", "Open a loop, then close it",
                   "End so the video loops", "Quick-hits video", "NEEDS to know", "Vary the day"):
        assert phrase in system, phrase
    assert "quick-hits video" in w.CRITIC_SYSTEM


def test_the_day_loses_one_single_story_video_to_the_quick_hits_video_and_never_repeats_its_stories(monkeypatch):
    monkeypatch.delenv("AVX_ENRICH_THIN", raising=False)
    monkeypatch.setenv("AVX_ENRICH_THIN", "0")
    stories = [rich(f"Big {i}") for i in range(12)] + [thin("Meta $95B", 16), thin("Broadcom debt", 15), thin("Marvell", 31)]
    roundup_script = type("S", (), {"avx_scenes": [{"style": "kinetic"}, {"style": "stat"}, {"style": "outro"}]})()
    used = {id(stories[12]), id(stories[13]), id(stories[14])}
    monkeypatch.setattr(w, "_roundup_scripts", lambda cands, n, avoid=(): ([roundup_script], used))
    seen = {}

    def validated(user, n, pool, avoid=(), first=None):
        seen.update(n=n, pool=list(pool), avoid=avoid)
        return []

    monkeypatch.setattr(w, "_validated_videos", validated)
    monkeypatch.setattr(w, "_first_drafts", lambda cands, n, avoid: {"videos": []})
    out = w.llm_avx_scripts(stories, 10)
    assert out == [roundup_script]  # no single videos in this stub, and the roundup is appended last
    assert seen["n"] == 9  # the roundup takes one of the ten
    assert all(not is_used for is_used in (id(c) in used for c in seen["pool"]))  # its stories are not told again
    assert ("kinetic" not in seen["avoid"][-1]) and seen["avoid"][-1] == ("stat",)  # and its shape is avoided


# --- the cheap repair for text a few characters over its cap -------------------------------------------------------

def _over_cap_video():
    v = copy.deepcopy(VIDEO)
    v["scenes"][1]["label"] = "growth from a year ago, in total"  # 33 characters; the cap is 28
    return v


def test_text_over_its_cap_is_shortened_without_rewriting_the_video(monkeypatch):
    from tests.avx_fixtures import STORY

    calls = []

    def fake_claude(system, user, schema, what, key):
        calls.append(what)
        import json

        items = json.loads(user)
        assert items == [{"id": 0, "text": "growth from a year ago, in total", "length": 32, "max": 28}]
        return {"items": [{"id": 0, "text": "growth from a year ago"}]}

    monkeypatch.setattr(w, "_claude_json", fake_claude)
    monkeypatch.setattr(w, "_call_writer", lambda user: {"videos": [_over_cap_video()]})
    accepted = w._validated_videos("write", 1, [STORY])
    assert calls == ["shortener"]  # one tiny call, and no second writer call to rewrite the video
    assert len(accepted) == 1 and accepted[0][1][1]["data"]["label"] == "growth from a year ago"


def test_a_failed_shortening_falls_back_to_the_full_rewrite(monkeypatch):
    from tests.avx_fixtures import STORY

    calls = []

    def fake_claude(system, user, schema, what, key):
        calls.append(what)
        return {"items": [{"id": 0, "text": "still far too long for the label to fit here"}]}  # over the cap again

    one_long_word = _over_cap_video()
    one_long_word["scenes"][1]["label"] = "Supercalifragilisticexpialidocious"  # one 34-character word: code cannot trim it either
    answers = [{"videos": [one_long_word]}, {"videos": [copy.deepcopy(VIDEO)]}]
    monkeypatch.setattr(w, "_claude_json", fake_claude)
    monkeypatch.setattr(w, "_call_writer", lambda user: answers.pop(0))
    accepted = w._validated_videos("write", 1, [STORY])
    assert calls == ["shortener"] and not answers  # the rewrite happened: both writer answers were used
    assert len(accepted) == 1


def test_a_video_with_other_problems_is_never_sent_to_the_shortener(monkeypatch):
    from tests.avx_fixtures import STORY

    v = _over_cap_video()
    v["scenes"][0]["say"] = "Why did Nvidia make so much money? Fifty seven billion dollars is the answer."  # also an opening problem
    called = []
    monkeypatch.setattr(w, "_claude_json", lambda *a, **k: called.append(1) or {"items": []})
    results = [(v, None, ["scene 2 (stat): label 'growth from a year ago, in total' is 33 characters (at most 28)",
                          "the hook opens with a question ('Why did Nvidia make so much money?'); open with the news"])]
    assert w._shorten_over_cap(results, [STORY]) == 0 and called == []


def test_a_text_still_over_after_the_first_round_is_shortened_again_with_its_new_length(monkeypatch):
    import json

    from tests.avx_fixtures import STORY

    seen = []
    replies = iter(["growth from last year!!", "growth from a year ago"])  # 23 characters, then 22: the cap is 28

    def fake_claude(system, user, schema, what, key):
        items = json.loads(user)
        seen.append((items[0]["text"], items[0]["length"]))
        return {"items": [{"id": 0, "text": next(replies)}]}

    video = _over_cap_video()
    video["scenes"][1]["label"] = "growth from the same quarter last year, in total"  # 48 characters
    monkeypatch.setattr(w, "_claude_json", fake_claude)
    monkeypatch.setattr(w, "_call_writer", lambda user: {"videos": [video]})
    # the cap is tightened to 22: the first reply (23 characters) is still over, the second (22) fits
    monkeypatch.setitem(w.CAPS, "stat_label", 22)
    accepted = w._validated_videos("write", 1, [STORY])
    assert seen == [("growth from the same quarter last year, in total", 48), ("growth from last year!!", 23)]
    assert len(accepted) == 1 and accepted[0][1][1]["data"]["label"] == "growth from a year ago"


def test_code_trims_a_text_the_model_could_not_shorten():
    assert w.trim_to("GPT-6 Cyber", 10) == "GPT-6"
    assert w.trim_to("growth from a year ago, in total", 28) == "growth from year ago, total"
    assert w.trim_to("or single-sentence apps?", 16) == "single-sentence"  # not just "or"
    assert w.trim_to("flaws in one run.", 16) == "flaws in one run"
    assert w.trim_to("Supercalifragilisticexpialidocious", 20) == ""  # one word that does not fit: leave it to a rewrite
    assert w.trim_to("fits", 10) == "fits"
    assert all(len(w.trim_to(t, 12)) <= 12 for t in ["a very long label indeed", "Nvidia owns data centers", "x"])
