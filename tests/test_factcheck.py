"""The fact check (factcheck.py) and its use by the writers: evidence quotes, the deterministic comparison
checks, the verifier's verdicts, one repair then drop, fail-open on infrastructure only, the kill switch,
refilling the day, the sidecar record, article fetching and the widened Jev scope. Claude, Jev and the network
are stubbed throughout."""
from __future__ import annotations

import copy
import json
import re
import types
from datetime import date

import pytest

from newsletter_video import avx_writer as w
from newsletter_video import factcheck as fc
from newsletter_video import plan_manifest as pm
from newsletter_video import script_writer as sw
from newsletter_video.scraper import Story
from newsletter_video.youtube_upload import write_metadata
from tests.avx_fixtures import STORY, VIDEO

SALES = Story(edition="founders", section="Strategies", title="SaaS S&M Does Not Get More Efficient With Scale",
              summary=("A VC firm checked 158 SaaS IPOs and found the median fell from $0.71 of new annual revenue per "
                       "sales and marketing dollar the year before listing to $0.66 in the listing year. The firm "
                       "treats $0.50 to $0.70 as a normal range for a company that goes public. Palantir came in at "
                       "$0.33 and Snowflake at $0.57."), url="https://example.com/sales")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("FACTCHECK", "1")
    fc._drops.clear()
    return monkeypatch


# --- evidence quotes ---------------------------------------------------------------------------------------

def test_a_real_quote_is_found_ignoring_case_punctuation_and_an_ellipsis():
    src = STORY.title + "\n" + STORY.summary
    assert fc.quote_in_source("nvidia reported revenue of $57 billion", src)[0]
    assert fc.quote_in_source("Nvidia reported revenue of $57 billion ... up 62% from a year ago", src)[0]
    assert fc.quote_in_source("CEO Jensen Huang said “demand for Blackwell chips is off the charts”", src)[0]


def test_an_invented_quote_is_rejected():
    src = STORY.title + "\n" + STORY.summary
    ok, why = fc.quote_in_source("Nvidia expects demand to double next year", src)
    assert not ok and "not in the source" in why
    # fragments must also come in the source's order
    assert not fc.quote_in_source("due in 2026 ... Nvidia reported revenue", src)[0]


def test_check_evidence_flags_fake_quotes_missing_lines_and_unquoted_facts():
    src = STORY.title + "\n" + STORY.summary
    lines = ["Nvidia made fifty seven billion dollars.", "Demand will double next year.", "Will AMD catch up?"]
    ev = [{"line": 1, "kind": "factual", "quotes": ["revenue of $57 billion"]},
          {"line": 2, "kind": "factual", "quotes": ["demand will double next year"]}]
    errors, _, mapping = fc.check_evidence(ev, lines, src)
    assert any("line 2" in e and "not in the story text" in e for e in errors)
    assert any("spoken line 3" in e and "no evidence entry" in e for e in errors)
    ev[1] = {"line": 2, "kind": "factual", "quotes": []}
    ev.append({"line": 3, "kind": "question", "quotes": []})
    errors, _, mapping = fc.check_evidence(ev, lines, src)
    assert errors == ["evidence: spoken line 2 ('Demand will double next year.') states facts but quotes no source text"]
    assert mapping[0] == {"line": 1, "kind": "factual", "quotes": ["revenue of $57 billion"]}
    assert fc.check_evidence(None, lines, src)[0][0].startswith("evidence is missing")


# --- comparisons -----------------------------------------------------------------------------------------------

def test_the_snowflake_case_is_caught_in_code():
    errs, _ = fc.check_comparisons("Snowflake's fifty seven cents was well below the fifty to seventy cent range.", [])
    assert errs == ["says 0.57 is below the range 0.5-0.7, but 0.57 is inside it"]
    assert fc.check_comparisons("Snowflake's 57 cents sits within the 50-70 cent range.", []) == ([], [])
    # the range only in the evidence quote
    errs, _ = fc.check_comparisons("Snowflake's fifty seven cents per dollar sits below the normal range.",
                                   ["treats $0.50 to $0.70 as a normal range"])
    assert errs and "0.57 is inside it" in errs[0]  # cents and dollars compare as the same quantity
    errs, _ = fc.check_comparisons("Snowflake's fifty seven cents per dollar sits below the normal range.",
                                   ["a normal range of 50-70 cents per dollar"])
    assert errs and "inside it" in errs[0]
    _, warns = fc.check_comparisons("Snowflake's fifty seven cents sits below the normal range.", ["nothing here"])
    assert warns and "cannot verify" in warns[0]


def test_pairwise_direction_and_qualifier_checks():
    assert fc.check_comparisons("OpenAI raised eleven billion dollars, above its thirty billion target.", [])[0]
    assert not fc.check_comparisons("OpenAI raised eleven billion dollars, well below its thirty billion target.", [])[0]
    errs, _ = fc.check_comparisons("Revenue is up sixty two percent.", ["revenue fell 62% from a year ago"])
    assert errs and "going down" in errs[0]
    assert not fc.check_comparisons("Revenue is up sixty two percent.", ["up 62% from a year ago"])[0]
    errs, _ = fc.check_comparisons("An investor in more than seven hundred companies.", ["an investor in nearly 700 companies"])
    assert errs and "nearly" in errs[0]
    # "to more than" after a percentage is not a comparison of the two numbers
    assert not fc.check_comparisons("Revenue rose sixty two percent to more than fifty seven billion dollars.", [])[0]


def test_spoken_numbers_are_read():
    assert fc.tokenize_numbers("one point two five trillion dollars")[1] == [1.25e12]
    assert fc.tokenize_numbers("fifty seven cents and $51.2 billion and 2,350")[1] == [0.57, 51.2e9, 2350]


# --- the avx validator with the fact check on -------------------------------------------------------------------

def test_avx_videos_need_real_evidence_when_the_fact_check_is_on(on):
    assert w.check_video(VIDEO, [STORY])[0]
    bad = copy.deepcopy(VIDEO)
    bad["evidence"][1]["quotes"] = ["revenue tripled in a single quarter"]
    with pytest.raises(w.AvxPlanError, match="not in the story text"):
        w.check_video(bad, [STORY])
    on.setenv("FACTCHECK", "0")  # the kill switch: evidence problems are only notes
    scenes, repairs = w.check_video(bad, [STORY])
    assert any("not enforced" in r for r in repairs)


def test_a_false_comparison_in_a_line_rejects_the_video(on):
    v = copy.deepcopy(VIDEO)
    v["scenes"][1]["say"] = "Revenue grew sixty two percent, which is below the fifty to sixty percent range."
    v["scenes"][1]["cue"] = "sixty"
    with pytest.raises(w.AvxPlanError, match="62 is below the range 50-60, but 62 is above it"):
        w.check_video(v, [STORY])


# --- verdicts ----------------------------------------------------------------------------------------------------

def test_which_verdicts_block():
    assert fc.is_blocker({"verdict": "contradicted"}) and fc.is_blocker({"verdict": "unsupported"})
    assert not fc.is_blocker({"verdict": "supported"}) and not fc.is_blocker({"verdict": "not_a_claim"})
    assert fc.is_blocker({"verdict": "partially_supported", "changes_meaning": True, "misleads": "that X was below"})
    assert not fc.is_blocker({"verdict": "partially_supported", "changes_meaning": True, "misleads": ""})
    assert not fc.is_blocker({"verdict": "partially_supported", "changes_meaning": False, "misleads": ""})
    assert fc.is_blocker({"verdict": "partially_supported", "error_type": "allegation", "misleads": ""})


def stub_verifier(bad_words=("BAD",), calls=None, fail=False):
    """A stand-in verifier: contradicted for any item containing a bad word, supported otherwise."""

    def call(user):
        if calls is not None:
            calls.append(user)
        if fail:
            return None
        videos = []
        for n, block in enumerate(user.split("\nVIDEO ")[1:], start=1):
            items = []
            for m in re.finditer(r"^  (\d+)\. \[[^\]]*\] (.*)$", block, re.MULTILINE):
                bad = any(b in m.group(2) for b in bad_words)
                items.append({"item": int(m.group(1)), "verdict": "contradicted" if bad else "supported",
                              "misleads": "a wrong thing" if bad else "", "changes_meaning": bad,
                              "error_type": "number" if bad else "none", "quote": "the source quote",
                              "reason": "the source says otherwise" if bad else ""})
            videos.append({"video": n, "items": items})
        return {"videos": videos}

    return call


def _cand(title, text, story=STORY):
    return fc.Candidate(title=title, stories=[story], items=[fc.Item("scene 1 spoken", "spoken", text, line=1)], key=title)


def test_gate_passes_clean_videos_repairs_once_and_drops_what_still_fails(on):
    calls, repairs = [], []

    def rewrite(pairs):
        repairs.append(pairs)
        return [_cand(c.title, "fixed line") if c.title == "fixable" else _cand(c.title, "still BAD") for c, _ in pairs]

    cands = [_cand("clean", "fine line"), _cand("fixable", "a BAD line"), _cand("hopeless", "BAD again")]
    result = fc.gate("avx", cands, rewrite, call=stub_verifier(calls=calls))
    assert [c.title for c, _ in result.accepted] == ["clean", "fixable"]
    assert [rec["status"] for _, rec in result.accepted] == ["passed", "repaired"]
    assert result.accepted[1][1]["repaired_for"][0].startswith("scene 1 spoken: 'a BAD line' is contradicted (number)")
    assert [c.title for c, _ in result.dropped] == ["hopeless"]
    assert len(calls) == 2 and len(repairs) == 1  # one verify round, one repair round, one re-verify
    assert "the source quote" in repairs[0][0][1][0]  # the writer gets the reason and the quote
    assert [d["stage"] for d in fc.drops_since(0)] == ["fact check"]


def test_infrastructure_failures_fail_open_but_verdicts_never_do(on):
    # verifier down: the validated videos ship, marked unverified
    result = fc.gate("avx", [_cand("a", "BAD line")], None, call=stub_verifier(fail=True))
    assert [rec["status"] for _, rec in result.accepted] == ["unverified"]
    # a stub that raises is the same
    result = fc.gate("avx", [_cand("a", "x")], None, call=lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    assert [rec["status"] for _, rec in result.accepted] == ["unverified"]
    # a blocker, then the verifier goes down for the repair: the repair is NOT shipped unverified
    answers = [stub_verifier()]

    def flaky(user):
        f = answers.pop(0) if answers else stub_verifier(fail=True)
        return f(user)

    result = fc.gate("avx", [_cand("b", "BAD line")], lambda pairs: [_cand("b", "fixed")], call=flaky)
    assert result.accepted == [] and "could not be verified" in result.dropped[0][1][-1]
    # a repair that fails validation (None) is dropped too
    result = fc.gate("avx", [_cand("c", "BAD")], lambda pairs: [None], call=stub_verifier())
    assert result.accepted == [] and result.dropped


def test_the_kill_switch_and_per_format_switches(monkeypatch):
    calls = []
    monkeypatch.setenv("FACTCHECK", "0")
    result = fc.gate("avx", [_cand("a", "BAD")], None, call=stub_verifier(calls=calls))
    assert calls == [] and result.accepted[0][1]["status"] == "off"
    monkeypatch.setenv("FACTCHECK", "1")
    monkeypatch.setenv("FACTCHECK_EXPLAINER", "0")
    assert fc.enabled("avx") and not fc.enabled("explainer")
    monkeypatch.delenv("FACTCHECK")
    monkeypatch.delenv("FACTCHECK_EXPLAINER")
    assert fc.enabled("avx") == fc.DEFAULT_ON["avx"] and fc.enabled("explainer") == fc.DEFAULT_ON["explainer"]


def test_verify_batches_videos_and_a_skipped_item_means_unverified(on):
    on.setenv("FACTCHECK_BATCH", "5")
    calls = []
    rows = fc.verify([_cand(f"v{i}", "fine") for i in range(7)], call=stub_verifier(calls=calls))
    assert len(calls) == 2 and all(r is not None for r in rows)

    def partial(user):  # judges nothing for video 1
        return {"videos": [{"video": 1, "items": []}]}

    assert fc.verify([_cand("v", "fine")], call=partial) == [None]


# --- the writers -------------------------------------------------------------------------------------------------

def test_avx_writer_repairs_a_contradicted_video_with_the_checkers_reasons(on):
    bad = copy.deepcopy(VIDEO)
    bad["scenes"][1]["say"] = "Revenue is up sixty two percent from a year ago, and data centers brought in most of it."
    bad["post_caption"] = "Nvidia's quarter hit BAD numbers."
    prompts = []
    answers = [{"videos": [bad]}, {"videos": [copy.deepcopy(VIDEO)]}]
    on.setattr(w, "_call_writer", lambda user: prompts.append(user) or answers.pop(0))
    on.setattr(w, "_call_critic", lambda user: {"reviews": []})
    on.setattr(fc, "_call_verifier", stub_verifier())
    scripts = w.llm_avx_scripts([STORY], 1)
    assert len(scripts) == 1 and scripts[0].fact_check["status"] == "repaired"
    assert "independent fact-checker" in prompts[1] and "the source says otherwise" in prompts[1]
    assert scripts[0].fact_check["evidence"][0]["quotes"]


def test_avx_writer_drops_a_video_that_fails_after_its_repair(on):
    bad = copy.deepcopy(VIDEO)
    bad["post_caption"] = "BAD caption"
    on.setattr(w, "_call_writer", lambda user: {"videos": [copy.deepcopy(bad)]})
    on.setattr(w, "_call_critic", lambda user: {"reviews": []})
    on.setattr(fc, "_call_verifier", stub_verifier())
    assert w.llm_avx_scripts([STORY], 1) == []
    assert fc.drops_since(0)[-1]["stage"] == "fact check"


def test_avx_writer_ships_unverified_when_the_verifier_is_down(on):
    on.setattr(w, "_call_writer", lambda user: {"videos": [copy.deepcopy(VIDEO)]})
    on.setattr(w, "_call_critic", lambda user: None)
    on.setattr(fc, "_call_verifier", lambda user: None)
    scripts = w.llm_avx_scripts([STORY], 1)
    assert len(scripts) == 1 and scripts[0].fact_check["status"] == "unverified"


def test_explainer_writer_runs_the_gate(on):
    from newsletter_video import explainer_writer as ew

    scenes = [
        {"type": "title", "say": "Nvidia just reported revenue of fifty seven billion dollars for its third quarter, and the numbers keep climbing.", "kicker": "AI",
         "headline": "Nvidia's $57B quarter", "icon": "chip"},
        {"type": "stat", "say": "Revenue is up sixty two percent from a year ago, a huge jump for a company this big.", "value": "62%",
         "label": "growth in a year", "context": "", "source": "", "trend": []},
        {"type": "comparison", "say": "Data center sales reached fifty one point two billion dollars of that total, by far the biggest part.",
         "title": "Where it came from", "left": {"label": "Data center", "value": "$51.2B", "note": "", "number": 0, "icon": "cloud"},
         "right": {"label": "Total", "value": "$57B", "note": "", "number": 0, "icon": "dollar"}},
        {"type": "statement", "say": "Chief executive Jensen Huang said demand for the Blackwell chips is off the charts right now.",
         "text": "Demand is off the charts", "emphasis": "", "label": "", "icon": ""},
        {"type": "timeline", "say": "Meanwhile rival AMD is racing to catch up with its MI450 chips, which are due in 2026.",
         "title": "", "steps": [{"label": "Now", "text": "Nvidia leads"}, {"label": "2026", "text": "AMD MI450"}]},
        {"type": "outro", "say": "Can anyone actually catch Nvidia now, or is the race already over? Tell me what you think below.", "text": "Can anyone catch up?",
         "icon": "comment"},
    ]
    video = {"title": "Nvidia's quarter", "badge": "AI", "story_ids": [0], "scenes": scenes, "post_caption": "cap",
             "hashtags": ["ai"], "evidence": [
                 {"line": 1, "kind": "factual", "quotes": ["Nvidia reported revenue of $57 billion"]},
                 {"line": 2, "kind": "factual", "quotes": ["up 62% from a year ago"]},
                 {"line": 3, "kind": "factual", "quotes": ["Data center sales reached $51.2 billion"]},
                 {"line": 4, "kind": "factual", "quotes": ["CEO Jensen Huang said demand for Blackwell chips is off the charts"]},
                 {"line": 5, "kind": "factual", "quotes": ["Rival AMD is racing to catch up with its MI450 chips, due in 2026"]},
                 {"line": 6, "kind": "question", "quotes": []}]}
    from newsletter_video.scene_plan import validate_plan

    try:
        validate_plan(scenes, strict=True)
    except Exception as exc:  # the explainer's own rules are tested elsewhere; skip if the fixture drifts
        pytest.skip(f"fixture plan is not valid under the current scene rules: {exc}")
    on.setattr(ew, "_call_writer", lambda user: {"videos": [copy.deepcopy(video)]})
    on.setattr(ew, "_apply_style_library", lambda plan, title: None)
    on.setattr(fc, "_call_verifier", stub_verifier(bad_words=("huge jump",)))
    assert ew.llm_explainer_scripts([STORY], 1) == []  # "a huge jump" stays in the repair too: dropped
    on.setattr(fc, "_call_verifier", stub_verifier(bad_words=("nothing",)))
    out = ew.llm_explainer_scripts([STORY], 1)
    assert len(out) == 1 and out[0].fact_check["status"] == "passed"


# --- refill ------------------------------------------------------------------------------------------------------

def _script(title, story):
    return sw.Script(title=title, badge="AI", hook="h", beats=[], outro="o", stories=[story], writer="claude")


def test_refill_replaces_dropped_videos_from_unused_stories(on):
    stories = [Story("tech", "AI", f"Story {i}", "summary", f"https://e.com/{i}") for i in range(6)]
    asked = []

    def write(pool, n, done):
        asked.append(([s.title for s in pool], n))
        if len(asked) == 1:  # the first round: 1 good video, 2 dropped (one story each)
            fc.note_drop("avx", "fact check", "dropped A", [pool[1]], ["contradicted"])
            fc.note_drop("avx", "editor", "dropped B", [pool[2]], ["unclear"])
            return [_script("ok", pool[0])]
        return [_script(f"refill {len(asked)}-{k}", pool[k]) for k in range(n)]

    scripts = sw.with_refill("avx", write, stories, 3)
    assert [s.title for s in scripts] == ["ok", "refill 2-0", "refill 2-1"]
    assert asked[1] == (["Story 3", "Story 4", "Story 5"], 2)  # neither the used nor the dropped stories


def test_refill_is_bounded_and_never_pads_with_failures(on):
    stories = [Story("tech", "AI", f"Story {i}", "s", "") for i in range(20)]
    rounds = []

    def write(pool, n, done):
        rounds.append(n)
        fc.note_drop("avx", "fact check", "x", [pool[0]], ["contradicted"])
        return []

    assert sw.with_refill("avx", write, stories, 3) == [] and len(rounds) == 3  # first try + 2 refills
    on.setenv("FACTCHECK_REFILL_ROUNDS", "0")
    rounds.clear()
    assert sw.with_refill("avx", write, stories, 3) == [] and len(rounds) == 1
    assert sw.with_refill("avx", lambda p, n, d: None, stories, 3) is None  # Claude unavailable


# --- the record --------------------------------------------------------------------------------------------------

def test_the_record_is_kept_in_the_sidecar_and_the_plan_entry(tmp_path, on):
    scenes, _ = w.check_video(VIDEO, [STORY])
    s = w.script_from_scenes(scenes, VIDEO["title"], VIDEO["badge"], [STORY], VIDEO["post_caption"], VIDEO["hashtags"])
    result = fc.gate("avx", [w._candidate(VIDEO, scenes, [STORY])], None, call=stub_verifier())
    s.fact_check = result.accepted[0][1]
    rec = s.fact_check
    assert rec["status"] == "passed" and rec["verdicts"]["supported"] > 6 and len(rec["lines"]) == 6
    assert rec["evidence"][0]["quotes"][0].startswith("Nvidia reported revenue")
    mp4 = tmp_path / "01_x.mp4"
    mp4.write_bytes(b"x")
    meta = json.loads(write_metadata(s, mp4).read_text())
    assert meta["fact_check"] == rec and len(json.dumps(rec)) < 6000  # compact
    entry = json.loads(json.dumps(pm.entry_from_script(s, 1)))
    assert entry["fact_check"] == rec and pm.script_from_entry(entry).fact_check == rec


def test_items_cover_every_on_screen_string():
    scenes, _ = w.check_video(VIDEO, [STORY])
    items = fc.items_avx(scenes, "T", "C")
    text = " | ".join(i.text for i in items)
    for s in ("big number: 62%", "growth from a year ago", "Off the charts", "Racing to catch up", "MI450 chips",
              "Chips stay scarce", "Tell me below"):
        assert s in text
    assert [i.kind for i in items].count("spoken") == 6 and items[0].kind == "title" and items[-1].kind == "caption"


# --- article fetching ----------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, ctype="text/html", body=b""):
        self.status_code, self.headers, self._body, self.encoding = status, {"Content-Type": ctype}, body, "utf-8"

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


def _session(resp=None, exc=None, calls=None):
    def get(url, **kw):
        if calls is not None:
            calls.append(url)
        if exc:
            raise exc
        return resp
    return types.SimpleNamespace(get=get)


ARTICLE = ("<html><body><nav>Menu Home About Contact Subscribe now please</nav><article>"
           + "".join(f"<p>Paragraph {i} of the article says something specific about the chips and the money.</p>"
                     for i in range(20)) + "<script>var x = 1;</script></article></body></html>").encode()


def test_article_fetch_extracts_caches_and_fails_quietly(on, tmp_path):
    on.setenv("FACTCHECK_CACHE_DIR", str(tmp_path / "cache"))
    fc._articles.clear()
    calls = []
    text = fc.fetch_article("https://news.example/a", session=_session(_Resp(body=ARTICLE), calls=calls))
    assert "Paragraph 3 of the article" in text and "Menu Home" not in text and "var x" not in text
    assert len(text) <= fc.ARTICLE_CHARS + 2
    fc._articles.clear()  # a new process: the disk cache answers, no second request
    assert fc.fetch_article("https://news.example/a", session=_session(exc=AssertionError("no refetch"))) == text
    import requests

    for i, sess in enumerate([_session(exc=requests.exceptions.Timeout("slow")), _session(_Resp(status=403)),
                              _session(_Resp(ctype="application/pdf", body=b"%PDF")),
                              _session(_Resp(body=b"<html><p>Subscribe to read</p></html>"))]):
        assert fc.fetch_article(f"https://news.example/fail{i}", session=sess) == ""
    assert fc.fetch_article("not a url") == ""


def test_articles_reach_the_sources_only_when_switched_on(on, tmp_path):
    on.setenv("FACTCHECK_CACHE_DIR", str(tmp_path / "cache"))
    fc._articles.clear()
    fc._articles[STORY.url] = "The full article says Nvidia sold more chips than ever."
    assert "full article" not in fc.sources_block([STORY]) and "full article" not in fc.story_text(STORY)
    on.setenv("FACTCHECK_FETCH_ARTICLES", "1")
    assert "excerpt of the linked article" in fc.sources_block([STORY]) and "full article" in fc.story_text(STORY)
    assert "published by: example.com" in fc.sources_block([STORY])
    fc._articles.clear()


# --- the widened Jev scope ---------------------------------------------------------------------------------------

def test_jev_lets_in_corporate_and_national_security_stories_by_the_second_question(monkeypatch):
    seen = []

    class Noul:
        def __init__(self, instructions):
            self.instructions = instructions

    class Score(Noul):
        def __init__(self, instructions, criteria):
            self.instructions = instructions

    class Client:
        def system_one(self, state, questions):
            seen.append(sorted(questions))
            ceo = "investigation of its CEO" in state
            junk = "webinar" in state
            general = 0.28 if ceo else 0.1 if junk else 0.8
            corporate = 0.86 if ceo else 0.05
            return types.SimpleNamespace(answers={
                "in_scope": types.SimpleNamespace(noul=general), "corporate": types.SimpleNamespace(noul=corporate),
                "important": types.SimpleNamespace(noul=0.05),
                "newsworthiness": types.SimpleNamespace(score=0.9 if ceo else 0.5)})

    fake = types.SimpleNamespace(Noul=Noul, Score=Score, TypeSafeClient=Client, TypeSafeError=Exception)
    monkeypatch.setitem(__import__("sys").modules, "typesafe_sdk", fake)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-real")
    stories = [Story("tech", "Big Tech", "Nvidia's investigation of its CEO", "x", ""),
               Story("tech", "Big Tech", "How to grow (webinar)", "x", ""),
               Story("ai", "Launches", "A model launch", "x", "")]
    ranked = sw._jev_rank_candidates(stories, 1)
    assert [s.title for s in ranked] == ["Nvidia's investigation of its CEO", "A model launch"]
    assert seen[0] == ["corporate", "important", "in_scope", "newsworthiness"]
    assert "espionage" in sw._JEV_CORPORATE_INSTRUCTIONS and "NOT personal gossip" in sw._JEV_CORPORATE_INSTRUCTIONS


def test_the_writers_carry_the_grounding_and_allegation_rules():
    from newsletter_video import explainer_writer as ew

    for prompt in (w._system_prompt(), ew._system_prompt(), sw._writer_system()):
        assert "IN THE SAME SENTENCE" in prompt and "WORD FOR WORD" in prompt and fc.SCOPE_NOTE in prompt
    assert "allegation" in fc.VERIFIER_SYSTEM and "SPY" in fc.VERIFIER_SYSTEM
    for schema in (w._AVX_SCHEMA, sw._WRITER_SCHEMA):
        assert "evidence" in schema["properties"]["videos"]["items"]["required"]


def test_a_dated_day_reports_its_drops_in_the_plan(on, tmp_path, monkeypatch):
    from newsletter_video import plan_day
    from newsletter_video.scraper import Edition

    class Src:
        def describe(self):
            return "stub"

        def fetch(self, d):
            return [Edition("tech", d.isoformat(), "TLDR", [STORY])]

    def build(editions, n_videos=None, exclude_titles=frozenset()):
        fc.note_drop("avx", "fact check", "Dropped one", [STORY], ["contradicted: says X"])
        scenes, _ = w.check_video(VIDEO, [STORY])
        return [w.script_from_scenes(scenes, "Kept", "AI", [STORY])]

    monkeypatch.setattr(plan_day, "get_source", lambda: Src())
    monkeypatch.setattr(plan_day, "_wait_for_network", lambda: True)
    monkeypatch.setattr(plan_day, "_covered_titles", lambda: set())
    monkeypatch.setattr(plan_day, "build_scripts", build)
    data = plan_day.plan(date(2026, 11, 10), tmp_path / "plan", 2, kind="test")
    assert [v["title"] for v in data["videos"]] == ["Kept"] and data["dropped"][0]["title"] == "Dropped one"


def test_evidence_is_flat_strings_so_the_writer_grammar_stays_small():
    # A nested evidence schema made the API refuse the avx writer call (400: compiled grammar too large).
    assert fc.EVIDENCE_SCHEMA == {"type": "array", "description": fc.EVIDENCE_SCHEMA["description"], "items": {"type": "string"}}
    parsed = fc.parse_evidence(["1: raised $11 billion", "1: fell short of its target", "Line 2 - at a $1.25 trillion valuation",
                                "3: QUESTION", "4: cta", "nonsense"])
    assert parsed == [{"line": 1, "kind": "factual", "quotes": ["raised $11 billion", "fell short of its target"]},
                      {"line": 2, "kind": "factual", "quotes": ["at a $1.25 trillion valuation"]},
                      {"line": 3, "kind": "question", "quotes": []}, {"line": 4, "kind": "cta", "quotes": []}]
    src = STORY.title + "\n" + STORY.summary
    errors, _, _ = fc.check_evidence(["1: revenue of $57 billion", "2: QUESTION"], ["Nvidia made $57 billion.", "Why?"], src)
    assert errors == []


def test_a_big_avx_day_is_drafted_in_parallel_calls_of_at_most_four(monkeypatch):
    # Ten avx videos in one call ran past the 64k output limit (~5k tokens a video); the drafts are split.
    stories = [Story("tech", "AI", f"Story {i}", "summary " * 50, f"https://e.com/{i}") for i in range(20)]
    prompts = []

    def writer(user):
        prompts.append(user)
        n = int(re.search(r"Write exactly (\d+) videos", user).group(1))
        if "[1]" in user:  # one of the calls fails: the others still count
            return None
        return {"videos": [{"title": f"t{len(prompts)}-{k}"} for k in range(n + 1)]}  # one too many is cut

    monkeypatch.setattr(w, "_call_writer", writer)
    out = w._first_drafts(stories, 10, "")
    assert len(prompts) == 3 and sorted(int(re.search(r"exactly (\d+)", p).group(1)) for p in prompts) == [3, 3, 4]
    ids = [set(map(int, re.findall(r"^\[(\d+)\]", p, re.MULTILINE))) for p in prompts]
    assert set().union(*ids) == set(range(20)) and all(not (a & b) for a in ids for b in ids if a is not b)
    assert len(out["videos"]) == 7  # share 2 (3 videos) failed; shares 1 and 3 wrote 4 + 3
    monkeypatch.setattr(w, "_call_writer", lambda user: None)
    assert w._first_drafts(stories, 10, "") is None


def test_a_leading_one_is_not_a_quantity():
    # From the audit of published video 18: "One investor ..." was read as 1 and "1 is more than 700" flagged.
    sentence = "One investor in more than seven hundred companies walks through how AI startups should price and who pays the token bill."
    assert fc.check_comparisons(sentence, ["An investor in more than 700 companies walks through how AI startups should price"]) == ([], [])
    assert fc.tokenize_numbers(sentence)[1] == [700]
    for s in ("No one is above the law.", "One of them sits below the others.", "It is one more than before."):
        assert fc.check_comparisons(s, [])[0] == []
    assert fc.tokenize_numbers("one million users and one percent")[1] == [1e6, 1]
    assert fc.check_comparisons("One percent is above two percent.", [])[0]  # a real quantity still counts


def test_rewrites_are_matched_to_their_videos_by_story():
    a, b = {"title": "A", "story_ids": [1]}, {"title": "B", "story_ids": [4]}
    ra, rb = {"title": "A2", "story_ids": [1]}, {"title": "B2", "story_ids": [4]}
    assert fc.align_rewrites([a, b], [rb, ra]) == [ra, rb]  # returned out of order: still matched
    assert fc.align_rewrites([a, b], [{"title": "X", "story_ids": [9]}]) == [{"title": "X", "story_ids": [9]}, None]


def test_a_ten_video_explainer_day_is_drafted_in_two_parallel_calls(monkeypatch):
    # Ten explainer plans with evidence used 55.9k of the 64k output tokens in one call on 2026-11-09.
    from newsletter_video import explainer_writer as ew

    stories = [Story("tech", "AI", f"Story {i}", "summary", "") for i in range(20)]
    prompts = []
    monkeypatch.setattr(ew, "_call_writer", lambda user: prompts.append(user) or {"videos": [{"title": "x"}] * 9})
    out = ew._first_drafts(stories, 10)
    assert len(prompts) == 2 and all("Write exactly 5 videos" in p for p in prompts) and len(out["videos"]) == 10
    assert ew.EXPLAINER_VIDEOS_PER_CALL == 5


def test_avx_needs_thirty_seconds_of_narration():
    assert w.MIN_WORDS == 80 and 82 / w.WORDS_PER_SECOND < w.MIN_SECONDS <= 83 / w.WORDS_PER_SECOND


def test_a_repair_that_only_breaks_a_format_rule_gets_one_more_try():
    asked = []

    def check(v):
        return (v["title"], []) if v.get("ok") else (None, ["say has 29 words, allowed 4-28"])

    out = fc.checked_rewrites([{"story_ids": [1]}, {"story_ids": [2]}],
                              [{"story_ids": [1], "title": "a", "ok": True}, {"story_ids": [2], "title": "b"}], check,
                              lambda tail: asked.append(tail) or [{"story_ids": [2], "title": "b2", "ok": True}])
    assert out == ["a", "b2"] and len(asked) == 1 and "29 words" in asked[0]
    out = fc.checked_rewrites([{"story_ids": [2]}], [{"story_ids": [2], "title": "b"}], check, lambda tail: None)
    assert out == [None]


def test_jev_lets_in_important_news_the_topic_question_scored_low_and_ranks_it_higher(monkeypatch):
    """The 2026-11-10 audit: Meta's $95B stock offering scored 0.44 on the topic question and 0.35 on the corporate
    one, yet any follower of AI news wants it. The "important" question lets it in and lifts it in the ranking."""
    class Noul:
        def __init__(self, instructions):
            self.instructions = instructions

    class Score(Noul):
        def __init__(self, instructions, criteria):
            self.instructions = instructions

    table = {  # title -> (topic, corporate, important, newsworthiness)
        "Meta announces a $95 billion stock offering": (0.44, 0.35, 0.85, 1.7),
        "A routine minor update": (0.8, 0.05, 0.1, 1.9),
        "Ten growth hacks": (0.1, 0.02, 0.05, 1.5),
    }

    class Client:
        def system_one(self, state, questions):
            topic, corp, imp, news = table[state.split("\n")[0]]
            return types.SimpleNamespace(answers={
                "in_scope": types.SimpleNamespace(noul=topic), "corporate": types.SimpleNamespace(noul=corp),
                "important": types.SimpleNamespace(noul=imp), "newsworthiness": types.SimpleNamespace(score=news)})

    fake = types.SimpleNamespace(Noul=Noul, Score=Score, TypeSafeClient=Client, TypeSafeError=Exception)
    monkeypatch.setitem(__import__("sys").modules, "typesafe_sdk", fake)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-real")
    stories = [Story("ai", "Business", t, "x", "") for t in table]
    ranked = sw._jev_rank_candidates(stories, 1)
    # the offering is in scope (important 0.85) and outranks the routine update (1.7+0.85 vs 1.9+0.1);
    # the growth-hacks piece fails every question
    assert [s.title for s in ranked] == ["Meta announces a $95 billion stock offering", "A routine minor update"]
    assert sw.JEV_IMPORTANT_THRESHOLD == 0.4 and "well-informed" in sw._JEV_IMPORTANT_INSTRUCTIONS
