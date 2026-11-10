"""How the narrator should SAY a line: the audio-only rewrite applied right before text-to-speech.

On-screen text and captions keep the original spelling; only the voice hears this. Every rule here
was found (or confirmed) by a round trip: the line was voiced by Kokoro (am_puck) and either its
phonemes (kokoro_onnx's tokenizer) or a Whisper transcript of the audio was compared with what a
person would say. The audit notes are next to each entry.

    speakable("158 SaaS IPOs in 2026")  ->  "158 sass I-P-O's in twenty twenty-six"

Two layers:
- PRONUNCIATIONS: a table of words, one comment each, matched case-insensitively on word
  boundaries (a few short ones that are also ordinary words are case-sensitive, marked `cs`).
- structural rules (money, scales, years, decades, ordinals, ranges, units, model names, plural
  acronyms) that a table cannot express, applied in a fixed order in speakable().

spoken_words() maps each original word to the words actually voiced, so forced alignment can match
the transcript of the respelled audio and still time the ORIGINAL words (video_builder._align_script).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Say:
    word: str  # as written (a regex when `regex`)
    spoken: str  # how it should sound, spelled for Kokoro
    note: str  # why: what the voice did wrong before
    cs: bool = False  # case-sensitive: the written form is also an ordinary lower-case word
    regex: bool = False
    prefix: bool = False  # also match at the start of a longer word ("SaaSpocalypse", "Qwen3")


def _spell(acronym: str) -> str:
    """Letter by letter: "A-G-I". Hyphens force Kokoro to name each letter (it reads some acronyms
    as words: AGI was "AH-ghee", IPO "EYE-poh", AWS "awz")."""
    return "-".join(acronym.upper())


# Acronyms that must be spelled letter by letter. The ones marked (wrong) were read as a word by
# Kokoro (phonemes checked); the rest it already spelled, and are pinned so context never changes that.
SPELLED = {
    "AGI": "wrong: 'AH-ghee'",
    "ARR": "wrong: 'are' (one syllable)",
    "IPO": "wrong: 'EYE-poh'",
    "AWS": "wrong: 'awz'",
    "YC": "wrong: 'ick'",
    "UFC": "wrong: 'uhfk'",
    "QA": "wrong: 'kah'",
    "ROI": "wrong: 'roy'",
    "ACV": "wrong: 'akv'",
    "ICP": "wrong: 'ikp'",
    "PII": "wrong: 'pih-eye'",
    "OSS": "wrong: 'oss'",
    "OKR": "wrong: 'oh-ker' in the plural",
}
# Acronyms Kokoro already spells correctly in the singular, so they are NOT respelled there (forcing
# hyphens on them made the next word run on: "AI S-D-R writes" was heard "AISDR-Writ"), but their plural
# was dropped ("GPUs" said "GPU") or mangled, which _plural_acronym fixes.
PLURAL_SPELLED = {"MRR", "NRR", "SDR", "CEO", "CFO", "CTO", "GPU", "TPU", "NPU", "CPU", "API", "SDK", "LLM", "KPI",
                  "AI", "VC", "PC"}
# Short acronyms that are also everyday words: only the capitalised form is spelled.
SPELLED_CS = {
    "COO": "wrong: 'coo' (only the capitalised form: 'coo' is a word)",
    "CRO": "wrong: 'crow'",
    "SEC": "wrong: 'sek' (only capitalised: 'sec' is short for second)",
    "MAI": "wrong: 'my' (Microsoft's MAI models; 'Mai' is also a name)",
}

PRONUNCIATIONS: list[Say] = [
    # --- "-aaS" business models: Kokoro said "sah-ESS"; people say them as words.
    Say("SaaS", "sass", "was 'sah ESS'; it rhymes with 'class'", prefix=True),
    Say("PaaS", "pass", "was 'pah ESS'", prefix=True),
    Say("IaaS", "eye-az", "was 'EYE-ah ESS'", prefix=True),
    # --- acronyms said as words
    Say("PostgreSQL", "Postgres Q L", "was 'postger S-Q-L'"),
    Say("MySQL", "My sequel", "was 'my S-Q-L'"),
    Say("SQL", "sequel", "the owner's (and most developers') pronunciation; Kokoro spelled it"),
    Say("RAG", "rag", "retrieval-augmented generation, said 'rag' (pinned: Kokoro does this already)"),
    Say("EBITDA", "E-bit-dah", "was 'EB-it-duh'"),
    # --- company, product and people names
    Say("Qwen", "Kwen", "was 'Q-wen' (letter Q)", prefix=True),
    Say("Federighi", "Fed-uh-ree-ghee", "was 'fed-er-RIG-hee'"),
    Say("Amodei", "Ah-mo-day", "was 'AM-oh-dye'"),
    Say("xAI", "x A-I", "pinned: 'ex A I'"),
    Say("GPT-4o", "G-P-T four-oh", "the 'o' is said 'oh'"),
    Say("4o", "four-oh", "GPT-4o written without the GPT"),
    Say("Mathocalypse", "Math-apocalypse", "coined word"),
    # --- abbreviations read literally
    Say(r"e\.g\.", "for example", "was 'E G'", regex=True),
    Say(r"i\.e\.", "that is", "was 'I E'", regex=True),
    Say(r"vs\.?(?=\s|$)", "versus", "was 'V S'", regex=True),
    Say(r"#\s?1\b", "number one", "was 'hash one'", regex=True),
    Say(r"24/7", "twenty-four seven", "was 'twenty-four slash seven'", regex=True),
]

_SCALES = {"k": "thousand", "m": "million", "b": "billion", "t": "trillion"}
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_UNITS = {  # after a number: "40ms", "2 GW"
    "ms": "milliseconds", "GW": "gigawatts", "MW": "megawatts", "kWh": "kilowatt hours", "GB": "gigabytes",
    "TB": "terabytes", "PB": "petabytes",
}
_PER = {"mo": "month", "month": "month", "yr": "year", "year": "year", "hr": "hour", "hour": "hour", "day": "day",
        "sec": "second", "s": "second", "user": "user", "seat": "seat", "token": "token", "min": "minute"}


def _int_words(n: int) -> str:
    """0..999,999 in words ("one hundred fifty-eight")."""
    if n < 100:
        return _two(n)
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" {_two(r)}" if r else "")
    t, r = divmod(n, 1000)
    return f"{_int_words(t)} thousand" + (f" {_int_words(r)}" if r else "")


def _decimal(m: re.Match) -> str:
    """"1.25" -> "one point two five". Kokoro voiced "1.25 trillion" so that Whisper heard "25 trillion"
    (the "one point" swallowed) in every round trip; the words come out right."""
    whole, frac = int(m.group(1)), m.group(2)
    if whole >= 1_000_000:
        return m.group(0)
    return f"{_int_words(whole)} point " + " ".join(_ONES[int(d)] for d in frac)


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _year(n: int) -> str:
    """2026 -> "twenty twenty-six", 1999 -> "nineteen ninety-nine", 2005 -> "two thousand five",
    2000 -> "two thousand". Kokoro said every year as "two thousand twenty-six"."""
    hi, lo = divmod(n, 100)
    if lo == 0:
        return "two thousand" if n == 2000 else f"{_two(hi)} hundred"
    if 2000 < n < 2010:
        return f"two thousand {_ONES[lo]}"
    return f"{_two(hi)} {'oh-' + _ONES[lo] if lo < 10 else _two(lo)}"


def _decade(n: int) -> str:
    """1990 -> "nineteen nineties", 2020 -> "twenty twenties" (was "two thousand twenty z")."""
    hi, lo = divmod(n, 100)
    if n == 2000:
        return "two thousands"
    word = {0: "hundreds", 10: "tens"}.get(lo) or _TENS[lo // 10][:-1] + "ies"
    return f"{_two(hi)} {word}"


def _say_money(match: re.Match) -> str:
    whole, decimals = match.group(1).replace(",", ""), match.group(2) or ""
    scale = match.group(4) or _SCALES.get((match.group(3) or "").lower())
    if scale:  # "$1.8 billion", "$2.5B" -> "1.8 billion dollars"
        number = whole + (f".{decimals}" if decimals else "")
        return f"{number} {scale.lower()} dollars"
    cents = int((decimals + "00")[:2]) if decimals else 0
    if int(whole) == 0 and decimals:  # "$0.05" -> "5 cents", "$0.005" -> "0.5 cents"
        if len(decimals) > 2:
            return f"{float('0.' + decimals) * 100:g} cents"
        return "1 cent" if cents == 1 else f"{cents} cents"
    dollars = "1 dollar" if int(whole) == 1 else f"{int(whole):,} dollars"
    return f"{dollars} and {cents} cents" if cents else dollars


_MONEY_RE = re.compile(
    r"\$(\d[\d,]*)(?:\.(\d+))?"
    r"(?:\s?([KMBT])\b|\s(thousand|million|billion|trillion)\b)?",
    re.IGNORECASE,
)


def _compile(entry: Say) -> re.Pattern:
    body = entry.word if entry.regex else re.escape(entry.word)
    tail = r"(?=[A-Za-z0-9]|\b|$)" if entry.prefix else r"(?![\w])"
    head = "" if entry.regex and entry.word.startswith("#") else r"(?<![\w])"
    return re.compile(head + body + tail, 0 if entry.cs else re.IGNORECASE)


_TABLE = [(_compile(e), e.spoken) for e in PRONUNCIATIONS]
_SPELL_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(SPELLED, key=len, reverse=True)) + r")(?![\w-])", re.IGNORECASE)
_SPELL_CS_RE = re.compile(r"(?<![\w-])(" + "|".join(SPELLED_CS) + r")(?![\w-])")
_PLURAL_ACRONYM = re.compile(r"(?<![\w-])([A-Z]{2,5})s\b")
_WORD_ACRONYMS = {"SAAS", "PAAS", "IAAS", "RAG", "NASA", "SQL"}


def _plural_acronym(m: re.Match) -> str:
    """"GPUs" was read "GPU" (the plural lost) and "IPOs" "EYE-poh": spell the letters and add 's
    ("G-P-U's" is voiced "gee-pee-yooz"). Only acronyms Kokoro spells anyway (at most one vowel, or
    listed in SPELLED); "NASAs" and the like are left alone."""
    acr = m.group(1)
    if acr in _WORD_ACRONYMS:
        return m.group(0)
    if acr in SPELLED or acr in SPELLED_CS or acr in PLURAL_SPELLED or sum(c in "AEIOU" for c in acr) <= 1:
        return _spell(acr) + "'s"
    return m.group(0)


def _ordinal(m: re.Match) -> str:
    """Round ordinals were cut short ("100th" -> "one hundred th")."""
    n = int(m.group(1))
    if n % 1000 == 0 and 1000 <= n < 1_000_000:
        return ("one" if n == 1000 else str(n // 1000)) + " thousandth"
    if n % 100 == 0 and 100 <= n < 1000:
        return f"{_ONES[n // 100]} hundredth"
    return m.group(0)


def _scale(m: re.Match) -> str:
    number, letter = m.group(1), m.group(2).lower()
    if letter == "k" and number in ("4", "8"):  # 4K / 8K video: said "four K"
        return m.group(0)
    return f"{number} {_SCALES[letter]}"


_YEAR_RE = re.compile(r"(?<![\w$.,-])(19[5-9]\d|20\d\d)(?![\w%]|[.,]\d)")
_YEAR_BEFORE = re.compile(
    r"(?:\b(?:in|since|by|from|until|till|through|of|early|late|mid|before|after|during|to|and|or|year|fiscal|"
    r"Q[1-4]|H[12]|FY|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|spring|summer|fall|autumn|winter)\.?,?\s*$)",
    re.IGNORECASE)


def _maybe_year(m: re.Match) -> str:
    """A 4-digit number is read as a year ("twenty twenty-six"; Kokoro said "two thousand twenty-six") when
    the words around it say it is one: after "in", "since", a month, "Q3"..., or ending a clause.
    "2048 GPUs" stays a quantity (Kokoro's own reading, which is fine for a year too)."""
    before, after = m.string[: m.start()], m.string[m.end():]
    if _YEAR_BEFORE.search(before[-20:]) or re.match(r"\s*(?:[,.;:!?)]|$|and\b|to\b)", after):
        return _year(int(m.group(1)))
    return m.group(0)


def speakable(text: str) -> str:
    """Rewrites a line into what the narrator should say (see the module docstring)."""
    if not text:
        return text
    text = _MONEY_RE.sub(_say_money, text)
    for pattern, spoken in _TABLE:
        text = pattern.sub(spoken, text)
    # Model names and versions: "GPT-5" -> "GPT 5", "Qwen3.6-35B-A3B" -> "Qwen 3.6 35B A3B" ("dash" was spoken).
    text = re.sub(r"(?<=[A-Za-z])-(?=\d)", " ", text)
    text = re.sub(r"(?<=\d)-(?=[A-Za-z]{1,3}\d)", " ", text)
    text = re.sub(r"(?<=\d[BMKT])-(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=\d)-(?=\d+[A-Za-z])", " ", text)  # "3.6-35B": a model name, not a range
    text = re.sub(r"\b(Kwen)(?=\d)", r"\1 ", text)
    text = re.sub(r"(?<![\w.])v(\d+(?:\.\d+)*)\b", r"version \1", text)  # "v2" was "vee two"
    # Scales without a dollar sign: "594B parameters", "1M tokens" (was "594 B", "one M").
    text = re.sub(r"(?<![\w$.,])(\d+(?:\.\d+)?)([KMBT])(?![\w])", _scale, text)
    # Multipliers: "4x" -> "4 times"; "~40%" -> "about 40%".
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)x\b", r"\1 times", text)
    text = re.sub(r"~\s?(?=\d)", "about ", text)
    # Number ranges: "10-12 percent" was "ten dash twelve".
    text = re.sub(r"(?<![\w.$-])(\d+(?:\.\d+)?)\s?[-–]\s?(\d+(?:\.\d+)?)(?![\w.])", r"\1 to \2", text)
    # Decimals in words (after scales and multipliers have been read off them); not versions like 1.4.3.
    text = re.sub(r"(?<![\w.,])(\d{1,6})\.(\d{1,4})(?![\w.]*\d|\.\d)", _decimal, text)
    # Units after a number, and "per" units.
    text = re.sub(r"(?<=\d)\s?(" + "|".join(_UNITS) + r")\b", lambda m: " " + _UNITS[m.group(1)], text)
    text = re.sub(r"(?<=\w)/(" + "|".join(_PER) + r")\b", lambda m: " per " + _PER[m.group(1)], text)
    # Decades, then years: "the 2020s" -> "the twenty twenties", "in 2026" -> "in twenty twenty-six".
    text = re.sub(r"(?<![\w$.,])(19[0-9]0|20[0-9]0)s\b", lambda m: _decade(int(m.group(1))), text)
    text = re.sub(r"(?<![\w$.,])'?([2-9]0)s\b", lambda m: _TENS[int(m.group(1)) // 10][:-1] + "ies", text)
    text = _YEAR_RE.sub(_maybe_year, text)
    text = re.sub(r"(?<![\w.,])(\d+)th\b", _ordinal, text)
    # Acronyms: plurals first ("IPOs"), then the letter-by-letter list.
    text = _PLURAL_ACRONYM.sub(_plural_acronym, text)
    text = _SPELL_RE.sub(lambda m: _spell(m.group(1)), text)
    text = _SPELL_CS_RE.sub(lambda m: _spell(m.group(1)), text)
    text = text.replace(" & ", " and ")
    return text


def spoken_words(line: str) -> list[tuple[int, str]]:
    """(index of the original word, spoken word) for every word the voice says, rewriting each
    original word on its own. Forced alignment matches the transcript against these and gives an
    original word the time of its first spoken word, so "4x" -> "4 times" or "$1.25T" -> "1.25
    trillion dollars" change the word count without shifting any caption."""
    out: list[tuple[int, str]] = []
    for i, word in enumerate(line.split()):
        spoken = speakable(word).split() or [word]
        out += [(i, s) for s in spoken]
    return out
