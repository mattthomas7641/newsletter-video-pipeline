"""The avatar-explainer (AVX) format: palette, cue timing, payload and template wiring.
Pure functions only: no TTS, no render."""
import json
import re

import pytest

from newsletter_video import avx_builder as avx
from newsletter_video.colors import contrast


@pytest.mark.parametrize("bg", ["#f3efe6", "#2f4fe0", "#0d0f14", "#ffffff", "#000000", "#7a7a7a", "#ffd23f", "#1f6f4a"])
def test_palette_is_readable_on_any_ground(bg):
    pal = avx.palette(bg)
    assert pal["bg"] == bg
    assert contrast(pal["ink"], bg) >= 4.5
    assert contrast(pal["mute"], bg) >= 4.5
    assert contrast(pal["accent"], bg) >= 3.0
    assert contrast(pal["accent2"], bg) >= 3.0
    assert contrast(pal["onAccent"], pal["accent"]) >= 3.0


def test_palette_flips_ink_with_the_ground():
    assert avx.palette("#f3efe6")["light"] is True
    assert avx.palette("#0d0f14")["light"] is False
    assert avx.palette("#0d0f14", accent="#ff3d8a")["accent"] == "#ff3d8a"


def test_cues_resolve_to_spoken_word_times():
    words = [("Siri", 1.0), ("arrived", 1.3), ("in", 1.6), ("2011.", 1.8), ("Apple", 3.0)]
    data = {"nodes": [{"cue": "Siri"}, {"cue": "2011"}], "tail": {"cue": "Apple"}, "miss": {"cue": "zebra"}}
    out = avx._resolve_cues(data, words, 0.8, 4.0)
    assert out["nodes"][0]["at"] == 1.0
    assert out["nodes"][1]["at"] == 1.8
    assert out["tail"]["at"] == 3.0
    assert 0.8 < out["miss"]["at"] < 4.0  # proportional fallback stays inside the scene


def _fake_lines(scenes):
    t, out = 0.2, []
    for s in scenes:
        line = []
        for w in s["say"].split():
            line.append((w, round(t, 3)))
            t += 0.3
        out.append(line)
        t += 0.4
    return out


def test_draft_payload_shape():
    per_line = _fake_lines(avx.DRAFT_SCENES)
    end = per_line[-1][-1][1] + 0.5
    p = avx.build_payload(avx.DRAFT_SCENES, per_line, end, draft_labels=True)
    assert p["draft_labels"] is True
    assert [s["style"] for s in p["scenes"]] == [s["style"] for s in avx.DRAFT_SCENES]
    starts = [s["start"] for s in p["scenes"]]
    assert starts == sorted(starts) and starts[0] == 0.0
    for a, b in zip(p["scenes"], p["scenes"][1:]):
        assert a["end"] == b["start"]
    assert p["scenes"][-1]["end"] == p["duration"]
    # at least three different grounds, and a wipe wherever the ground changes
    assert len({s["pal"]["bg"] for s in p["scenes"]}) >= 3
    for a, b in zip(p["scenes"], p["scenes"][1:]):
        if a["pal"]["bg"] != b["pal"]["bg"]:
            assert b["transition"] in ("iris", "shutter")
    # scenes flagged captions=False (kinetic hook/outro: the type IS the words) get no captions
    silent = {i for i, s in enumerate(avx.DRAFT_SCENES) if s.get("captions") is False}
    assert silent and not any(c["scene"] in silent for c in p["captions"])
    # every cue in the data got a time
    blob = json.dumps([s["data"] for s in p["scenes"]])
    assert blob.count('"cue"') == blob.count('"at"')


def test_every_style_has_a_template_builder():
    html = (avx.TEMPLATE_DIR / "index.html").read_text()
    for style in avx.STYLES:
        assert re.search(rf"BUILD\.{style}\s*=", html), style


def test_template_is_offline_and_separate():
    html = (avx.TEMPLATE_DIR / "index.html").read_text()
    assert "cdn.jsdelivr" not in html and "https://" not in html.split("<body>")[0].split("-->", 1)[1]
    for src in re.findall(r'<script src="([^"]+)"', html):
        assert (avx.TEMPLATE_DIR / src).exists(), src
    for font in re.findall(r'url\("(fonts/[^"]+)"\)', html):
        assert (avx.TEMPLATE_DIR / font).exists(), font
    assert "Math.random" not in html and "Date.now" not in html
    assert avx.TEMPLATE_DIR.name == "hf_template_avx"


def test_inject_default_round_trips(tmp_path):
    (tmp_path / "index.html").write_text((avx.TEMPLATE_DIR / "index.html").read_text())
    payload = {"duration": 1.0, "scenes": [], "captions": [], "note": "it's"}
    avx.inject_default(tmp_path, payload)
    html = (tmp_path / "index.html").read_text()
    blob = re.search(r"data-composition-variables='([^']*)'", html).group(1).replace("&#39;", "'")
    assert json.loads(json.loads(blob)[0]["default"]) == payload
