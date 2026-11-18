"""Words an ordinary smartphone user does not know, with a plain replacement, for the avatar explainer's writer.

2026-11-10: a cold-viewer judge (a smart 25-year-old who uses ChatGPT but is not an insider) rated the 21 openings
published on 2026-11-09 at 2.9 out of 5 on average; 16 scored 3 or lower. The causes were jargon nobody defined
("parameters", "agents", "GPU time", "token", "pull request"), names a general viewer does not know used as if they
were common ("AI Frontiers", "OpenRouter", "the labs"), and cryptic opening fragments.

The glossary below goes into the writer's prompt (so it can swap the word, or define it in a few words), and
`opening_problems` checks the hook against it. Definitions are deliberately short: at most eight words.
"""
from __future__ import annotations

import re

# term (as it is likely to appear) -> plain meaning, at most 8 words. These are the terms a general audience needs
# explained or replaced; terms a tech-savvy viewer knows (API, IPO, LLM, valuation, antitrust) are left out.
GLOSSARY: dict[str, str] = {
    "frontier model": "the most powerful AI available today",
    "frontier": "the most advanced AI available",
    "AGI": "AI as capable as a person",
    "RSI": "AI that improves its own design",
    "recursive self-improvement": "AI that improves its own design",
    "agent": "AI that takes actions on its own",
    "agents": "AI that takes actions on its own",
    "agentic": "able to take actions, not just answer",
    "inference": "running an AI to produce an answer",
    "token": "a small chunk of text an AI reads",
    "tokens": "small chunks of text an AI reads",
    "context window": "how much text an AI can read at once",
    "fine-tuning": "extra training for one specific job",
    "fine-tuned": "given extra training for one job",
    "open weights": "the AI's files are free to download",
    "distillation": "training a small AI to copy a big one",
    "distilled": "trained to copy a bigger AI",
    "RAG": "AI that looks things up before answering",
    "MoE": "only part of the model runs each time",
    "mixture of experts": "only part of the model runs each time",
    "parameters": "the numbers an AI learns; more means bigger",
    "benchmark": "a standard test for comparing AIs",
    "benchmarks": "standard tests for comparing AIs",
    "SOTA": "the best result so far",
    "GPU": "a chip that powers AI",
    "GPUs": "chips that power AI",
    "TPU": "Google's own AI chip",
    "compute": "computing power",
    "hyperscaler": "a giant cloud company like Amazon or Google",
    "hyperscalers": "giant cloud companies like Amazon and Google",
    "gigawatt": "a billion watts, enough to power a city",
    "gigawatts": "billions of watts, enough to power a city",
    "S-1": "the paperwork to sell shares to the public",
    "prospectus": "the document describing a company to investors",
    "equity offering": "selling new shares of the company",
    "stock offering": "selling new shares of the company",
    "debt deal": "a big loan",
    "export controls": "limits on selling technology abroad",
    "jailbreak": "tricking an AI past its safety rules",
    "red team": "testers who try to break it",
    "alignment": "making AI do what people intend",
    "multimodal": "handles text, images and audio",
    "SDK": "a toolkit for building with software",
    "harness": "the code that runs around the AI",
    "CLI": "typing commands instead of clicking",
    "scaling laws": "bigger models tend to get smarter",
    "reasoning model": "an AI that thinks before it answers",
    "chain of thought": "an AI showing its step-by-step thinking",
    "embedding": "numbers that capture the meaning of text",
    "foundation model": "one big AI that many apps build on",
    "RISC-V": "a free, open chip design",
    "NPU": "an AI chip inside a phone or laptop",
    "on-device": "running on your own phone or laptop",
    "synthetic data": "training data made by AI",
    "capex": "money spent on equipment and buildings",
    "ARR": "yearly income if this pace continues",
    "MRR": "monthly subscription income",
    "monthly recurring revenue": "monthly subscription income",
    "pull request": "a proposed code change for review",
    "router": "a tool that picks which AI answers",
    "GPU time": "rented time on AI chips",
    "SaaS": "software sold by subscription",
    "LLM": "the kind of AI behind ChatGPT",
}

# Acronyms and names a general viewer knows; anything else in capitals needs explaining in the hook.
KNOWN_ACRONYMS = {"AI", "US", "UK", "EU", "CEO", "CFO", "CTO", "IPO", "API", "TV", "PC", "OS", "ID", "FBI", "CIA",
                  "NASA", "NATO", "AMD", "IBM", "AWS", "iOS", "USB", "WiFi", "VR", "AR", "5G", "SEC", "FTC", "DOJ",
                  "GPT", "EV", "OLED", "NFL", "NBA", "UN", "OK", "NVIDIA", "TSMC", "ASML", "HP", "AT", "T",
                  "LG", "BMW", "GM", "UPS", "ESPN", "CNN", "BBC", "NYT", "WSJ", "SAP", "TCL"}

# Openings that say nothing concrete ("The AI frontier just shifted.")
_ABSTRACT_OPENERS = re.compile(r"^\W*(the\s+)?(ai\s+|tech\s+)?(frontier|landscape|era|race|wave|paradigm|future|age|"
                               r"ecosystem|space|world|battle|moment)\b", re.I)
_QUESTION_WORDS = re.compile(r"^\W*(why|how|what|who|when|where|can|could|will|would|should|do|does|did|is|are|"
                             r"was|were|have|has)\b", re.I)
MAX_HOOK_WORDS = 20  # the first sentence of the hook, spoken
MIN_HOOK_WORDS = 6  # below this it is a fragment ("Twelve brands.")


def _find(term: str, text: str) -> bool:
    flags = 0 if (term.isupper() or any(c.isupper() for c in term[1:])) else re.I
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text, flags) is not None


def jargon_in(text: str) -> list[tuple[str, str]]:
    """[(term, plain meaning)] for every glossary term found in `text`, longest terms first, each once."""
    found, taken = [], text
    for term in sorted(GLOSSARY, key=len, reverse=True):
        if _find(term, taken):
            found.append((term, GLOSSARY[term]))
            taken = re.sub(rf"(?<![\w-]){re.escape(term)}(?![\w-])", " ", taken, flags=0 if term.isupper() else re.I)
    return found


def unknown_acronyms(text: str) -> list[str]:
    return sorted({a for a in re.findall(r"\b[A-Z][A-Z0-9]{1,5}\b", text)
                   if a not in KNOWN_ACRONYMS and a not in GLOSSARY and not a.isdigit()})


def first_sentence(text: str) -> str:
    m = re.match(r"\s*(.+?[.!?])(\s|$)", text.strip())
    return (m.group(1) if m else text.strip())


def opening_problems(hook_say: str, hook_lines: str = "", roundup: bool = False) -> list[str]:
    """What a cold viewer would trip on in a video's first spoken sentence, as messages for the writer (empty when
    the opening is fine). Checked on the hook's `say` and its on-screen lines."""
    errors: list[str] = []
    first = first_sentence(hook_say)
    n = len(first.split())
    if first.rstrip().endswith("?") or (_QUESTION_WORDS.match(first) and "?" in hook_say.split(".")[0]):
        errors.append(f"the hook opens with a question ({first[:60]!r}); open with the news itself as a plain "
                      "statement: who did what")
    elif n < MIN_HOOK_WORDS:
        errors.append(f"the hook's first sentence is a fragment ({first[:60]!r}); make it one complete sentence "
                      "that says who did what")
    if n > MAX_HOOK_WORDS:
        errors.append(f"the hook's first sentence is {n} words; keep it under {MAX_HOOK_WORDS}")
    if _ABSTRACT_OPENERS.match(first):
        errors.append(f"the hook starts with an abstract phrase ({first[:40]!r}); name who did what instead "
                      "(a company or product the viewer knows, and a concrete verb)")
    combined = f"{hook_say} {hook_lines}"
    for term, plain in jargon_in(combined):
        errors.append(f"the hook uses {term!r}, which an ordinary viewer does not know; say it in plain words "
                      f"(roughly: {plain}) or leave it out")
    odd = unknown_acronyms(combined)
    if odd and not roundup:
        errors.append(f"the hook uses {', '.join(odd)}, which an ordinary viewer does not know; spell it out "
                      "in plain words or leave it out")
    return errors


# The terms that actually come up in the day's stories, for the writer's prompt (the full GLOSSARY still backs the
# opening check): a shorter list keeps the prompt, and the writer's thinking about it, small.
LEAN_TERMS = ("agents", "tokens", "parameters", "inference", "GPUs", "compute", "benchmark", "open weights",
              "frontier model", "context window", "fine-tuning", "distillation", "hyperscaler", "gigawatt",
              "prospectus", "stock offering", "export controls", "jailbreak", "harness", "RAG", "MoE",
              "reasoning model", "SDK", "pull request", "ARR", "MRR", "router", "SaaS", "RSI", "AGI")
