[Back to the README](../README.md)

# Story selection with Jev
With `TYPESAFE_API_KEY` set (and `typesafe-sdk` installed from `requirements.txt`), Jev, a
decision model from TypeSafe AI, screens every candidate story before
Claude sees it. Jev classifies and scores; it never writes text. For each story's title and
summary it answers two questions:

- **In scope?** A yes/no probability. Stories below 0.5 are dropped
  (`JEV_IN_SCOPE_THRESHOLD`), unless they clear 0.5 on a second, narrow question: major
  investigation, legal, regulatory, national-security, governance or leadership news about big
  tech, chip and AI companies and their leaders (`_JEV_CORPORATE_INSTRUCTIONS`).
- **How newsworthy?** Low (minor, routine or sponsored-feeling), Medium (solid but
  unsurprising) or High (surprising, consequential, weird or genuinely useful).

A third question, **important?** ("would a well-informed person who follows AI and technology want to know this
today?", `_JEV_IMPORTANT_INSTRUCTIONS`), is also a way into scope at 0.4 (`JEV_IMPORTANT_THRESHOLD`) and its score is
added to the ranking. Measured on 595 stories from two weeks of all four editions, labelled must-cover / worth-covering
/ skip by Claude, the first two questions let in 75% of the must-cover stories (Meta's $95B stock offering,
Anthropic's IPO filing and GPT-6 Cyber were all below 0.5 on the topic question); with this question the writer's
top-30 pool held all of them. Jev costs $0.042 per million input tokens with free output, so the extra question is
effectively free.

The in-scope stories are ranked by newsworthiness plus importance, and the top ones go to Claude: three per
requested video, and never fewer than 15 (`JEV_POOL_MULTIPLIER`, `JEV_MIN_POOL` in
`script_writer.py`), so Claude still has real choice and room to combine related stories. A
typical morning: 40 candidates in, 18 in scope, all 18 passed to the writer.

Failure handling:
- No key, or no SDK installed: Claude gets every candidate unranked, exactly as before.
- The first Jev call fails: Jev is treated as down for the run, and Claude gets every
  candidate unranked.
- A later single call fails: that story is kept at the bottom of the ranking rather than
  silently dropped.
