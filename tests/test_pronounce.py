"""The audio-only pronunciation layer (pronounce.py): what the narrator says vs what the screen shows."""
import sys
import types

import pytest

from newsletter_video import pronounce
from newsletter_video.pronounce import speakable, spoken_words


@pytest.mark.parametrize("text, said", [
    # -aaS words (the owner's report: SaaS must sound like "sass")
    ("SaaS companies", "sass companies"),
    ("saas", "sass"),
    ("The SaaSpocalypse", "The sasspocalypse"),
    ("PaaS and IaaS", "pass and eye-az"),
    # acronyms said as words / letter by letter
    ("in SQL", "in sequel"),
    ("PostgreSQL", "Postgres Q L"),
    ("AGI and ARR", "A-G-I and A-R-R"),
    ("an IPO on AWS", "an I-P-O on A-W-S"),
    ("158 IPOs", "158 I-P-O's"),
    ("GPUs, TPUs and LLMs", "G-P-U's, T-P-U's and L-L-M's"),
    ("CEOs and SDRs", "C-E-O's and S-D-R's"),
    ("NASAs", "NASAs"),  # a word-acronym is not spelled
    ("The COO", "The C-O-O"),
    ("a coo", "a coo"),  # case-sensitive entry: the ordinary word stays
    ("10 sec", "10 sec"),
    ("RAG", "rag"),
    # names
    ("Qwen and Qwen3", "Kwen and Kwen 3"),
    ("Craig Federighi", "Craig Fed-uh-ree-ghee"),
    ("GPT-4o", "G-P-T four-oh"),
    ("GPT-5", "GPT 5"),
    # versions, models, numbers
    ("v2", "version 2"),
    ("Qwen3.6-35B-A3B", "Kwen three point six 35 billion A3B"),
    ("4x faster", "4 times faster"),
    ("$1.25T", "one point two five trillion dollars"),
    ("$11B", "11 billion dollars"),
    ("$500M", "500 million dollars"),
    ("$20/mo", "20 dollars per month"),
    ("1M tokens", "1 million tokens"),
    ("594B parameters", "594 billion parameters"),
    ("4K video", "4K video"),
    ("10-12 percent", "10 to 12 percent"),
    ("40ms", "40 milliseconds"),
    ("Claude 3.5", "Claude three point five"),
    ("version 1.4.3", "version 1.4.3"),
    # years, decades, ordinals
    ("in 2026", "in twenty twenty-six"),
    ("Q3 2025.", "Q3 twenty twenty-five."),
    ("since 2005", "since two thousand five"),
    ("2048 GPUs", "2048 G-P-U's"),  # a quantity, not a year
    ("the 1990s", "the nineteen nineties"),
    ("the 2020s", "the twenty twenties"),
    ("the '80s", "the eighties"),
    ("the 100th", "the one hundredth"),
    ("the 3rd", "the 3rd"),
    # literal symbols
    ("#1", "number one"),
    ("24/7", "twenty-four seven"),
    ("this vs that", "this versus that"),
    ("e.g. this", "for example this"),
    ("R & D", "R and D"),
    ("~40%", "about 40%"),
])
def test_speakable(text, said):
    assert speakable(text) == said


def test_the_table_is_documented_and_unaffected_lines_pass_through():
    assert all(e.note for e in pronounce.PRONUNCIATIONS)
    assert all(pronounce.SPELLED.values())
    plain = "Apple wants Siri to act on your behalf, and investors want a plan."
    assert speakable(plain) == plain
    assert speakable("") == ""


def test_spoken_words_map_every_spoken_word_to_its_original_word():
    words = spoken_words("OpenAI raised $11B at 4x the SaaS average")
    originals = "OpenAI raised $11B at 4x the SaaS average".split()
    assert [i for i, _ in words] == sorted(i for i, _ in words)  # in order
    assert {i for i, _ in words} == set(range(len(originals)))  # every original word is said
    said = {originals[i]: [w for j, w in words if j == i] for i in range(len(originals))}
    assert said["$11B"] == ["11", "billion", "dollars"]
    assert said["4x"] == ["4", "times"]
    assert said["SaaS"] == ["sass"]


def _fake_whisper(monkeypatch, words: list[tuple[str, float]]):
    mod = types.ModuleType("mlx_whisper")
    mod.transcribe = lambda *a, **k: {"segments": [{"words": [{"word": w, "start": t} for w, t in words]}]}
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)


def test_alignment_times_original_words_when_the_respelling_changes_the_word_count(monkeypatch, tmp_path):
    """The audio says "eleven billion dollars" and "four times" for "$11B" and "4x": every ORIGINAL word still
    gets the time it was said, and the captions keep the written words."""
    import numpy as np
    import soundfile

    from newsletter_video import video_builder as vb

    wav = tmp_path / "a.wav"
    soundfile.write(str(wav), np.full(24000 * 4, 0.3, dtype="float32"), 24000)
    _fake_whisper(monkeypatch, [("OpenAI", 0.1), ("raised", 0.5), ("11", 0.9), ("billion", 1.2), ("dollars", 1.5),
                                ("and", 1.9), ("sass", 2.2), ("got", 2.5), ("4", 2.8), ("times", 3.0),
                                ("cheaper", 3.3)])
    lines = ["OpenAI raised $11B", "and SaaS got 4x cheaper"]
    per_line = vb._align_script(lines, wav)
    assert [w for w, _ in per_line[0]] == ["OpenAI", "raised", "$11B"]
    assert [w for w, _ in per_line[1]] == ["and", "SaaS", "got", "4x", "cheaper"]
    times = dict(per_line[0] + per_line[1])
    assert times["$11B"] == 0.9 and times["SaaS"] == 2.2 and times["4x"] == 2.8 and times["cheaper"] == 3.3
