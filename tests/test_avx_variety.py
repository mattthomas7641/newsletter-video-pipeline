"""Variety between avatar explainer videos: palette families, transition sets, rhythms and layout variants,
all deterministic, consecutive videos never alike, the explicit lineup colour still one plain ground."""
from __future__ import annotations

import copy
import re

import pytest

from newsletter_video import avx_builder as avx
from newsletter_video.colors import contrast
from tests.avx_fixtures import VIDEO, STORY

TEMPLATE = avx.TEMPLATE_DIR / "index.html"


def _scenes():
    from newsletter_video import avx_writer as w

    return w.check_video(copy.deepcopy(VIDEO), [STORY])[0]


@pytest.mark.parametrize("name", list(avx.PALETTE_FAMILIES))
def test_every_family_passes_the_contrast_rules(name):
    for ground, accent in avx.PALETTE_FAMILIES[name]:
        pal = avx.palette(ground, accent)
        assert contrast(pal["ink"], ground) >= 4.5 and contrast(pal["mute"], ground) >= 4.5
        assert contrast(pal["accent"], ground) >= 3.0 and contrast(pal["accent2"], ground) >= 3.0


def test_the_original_house_look_is_one_of_the_families_and_the_default():
    assert avx.PALETTE_FAMILIES["house"] == ((avx.LIGHT, None), (avx.MID, None), (avx.DARK, None))
    assert avx.video_variety("") == {"family": "house", "transitions": ("iris", "shutter"), "rhythm": "classic"}


def test_variety_is_deterministic_and_neighbours_never_repeat():
    days = ["2026-11-10", "2026-11-11", "2026-12-01"]
    for day in days:
        picks = [avx.video_variety(f"{day}-{i}") for i in range(10)]
        assert picks == [avx.video_variety(f"{day}-{i}") for i in range(10)]
        for a, b in zip(picks, picks[1:]):
            assert a["family"] != b["family"] and a["transitions"] != b["transitions"]
        assert len({p["family"] for p in picks}) == len(avx.PALETTE_FAMILIES)  # a day uses every family


def test_apply_look_colours_a_video_from_its_family_with_its_transitions():
    seed = next(f"2026-11-10-{i}" for i in range(10) if avx.video_variety(f"2026-11-10-{i}")["family"] == "forest")
    v = avx.video_variety(seed)
    scenes = avx.apply_look(_scenes(), "", "", seed=seed)
    light, mid, dark = avx.PALETTE_FAMILIES["forest"]
    assert scenes[0]["bg"] == light[0] and scenes[-1]["bg"] == light[0]
    assert {s["bg"] for s in scenes} <= {light[0], mid[0], dark[0]} and len({s["bg"] for s in scenes}) == 3
    assert all(s["accent"] == dict(avx.PALETTE_FAMILIES["forest"])[s["bg"]] for s in scenes)
    changes = [s.get("transition") for i, s in enumerate(scenes) if i and scenes[i - 1]["bg"] != s["bg"]]
    assert changes[-1] == v["transitions"][1] and set(changes[:-1]) <= {v["transitions"][0]}
    p = avx.build_payload(scenes, [[("w", 1.0 + i)] for i in range(len(scenes))], 20.0)
    assert all(s["transition"] in avx.TRANSITIONS for s in p["scenes"]) and all(s["variant"] for s in p["scenes"])


def test_an_explicit_lineup_colour_still_means_one_plain_ground_whatever_the_seed():
    scenes = avx.apply_look(_scenes(), "#112233", "", seed="2026-11-10-4")
    assert {s["bg"] for s in scenes} == {"#112233"} and not any(s.get("transition") for s in scenes)
    assert not any("accent" in s for s in scenes)
    accent = avx.apply_look(_scenes(), "", "#ff0066", seed="2026-11-10-4")
    assert {s["accent"] for s in accent} == {"#ff0066"}


@pytest.mark.parametrize("rhythm", avx.RHYTHMS)
def test_rhythms_open_and_close_on_paper_and_use_all_three_grounds(rhythm):
    fam = avx.PALETTE_FAMILIES["violet"]
    for n in range(5 if rhythm == "classic" else 4, 9):  # classic at 4 is the original L L D L
        r = avx.house_rhythm(n, fam, rhythm)
        assert len(r) == n and r[0] == r[-1] == fam[0][0] and set(r) == {g for g, _ in fam}
    assert avx.house_rhythm(8) == [s["bg"] for s in avx.DRAFT_SCENES]  # the original, unchanged


def test_variants_are_seeded_fit_the_data_and_never_repeat_within_a_video():
    stat_pct = {"style": "stat", "data": {"value": 40, "suffix": "%"}}
    stat_big = {"style": "stat", "data": {"value": 57, "suffix": "B"}}
    seen = set()
    for i in range(30):
        scenes = avx.assign_variants([copy.deepcopy(stat_big), copy.deepcopy(stat_pct), {"style": "list", "data": {}},
                                      {"style": "list", "data": {}}], f"d-{i}")
        assert scenes[0]["variant"] in ("left", "center")  # no ring or bar for a non-percentage: a made-up scale
        assert scenes[2]["variant"] != scenes[3]["variant"]
        seen.add(scenes[1]["variant"])
    assert seen == {"left", "center", "ring", "bar"}
    assert avx.assign_variants([{"style": "list", "data": {}}], "") == [{"style": "list", "data": {}, "variant": "rows"}]
    kept = avx.assign_variants([{"style": "chart", "data": {"series": [{"values": [1, 2]}]}, "variant": "bars"}], "x")
    assert kept[0]["variant"] == "bars"  # the writer's (or a stored plan's) choice wins when it fits


def test_the_template_implements_every_transition_and_variant():
    html = TEMPLATE.read_text()
    wipes = set(re.findall(r"^\s{10}(\w+): \(bg, t\) =>", html, re.M))
    assert set(avx.TRANSITIONS) - {"cut"} <= wipes
    for style, variants in avx.VARIANTS.items():
        for v in variants[1:]:
            if style in ("kinetic", "outro"):
                assert f'"{v}"' in html  # handled inside BUILD.kinetic
            elif style == "stat":
                assert f'variant === "{v}"' in html
            else:
                assert f'VARIANT["{style}:{v}"]' in html
