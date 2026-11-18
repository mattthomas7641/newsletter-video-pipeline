[Back to the README](../README.md)

# Accuracy

## What the editor may drop, and what it may not

The avx writer's editor pass reads every video like a first-time viewer. A flagged video gets one rewrite. After
it, **accuracy flags still drop the video** (an unsupported claim, a graphic that shows the wrong thing or has the
actor and the one acted on the wrong way round, two stories mixed together), while **clarity flags ship it with the
notes logged** (an unexplained name, a vague line, a repeated point). Dropping on any remaining flag threw away three
accurate videos in a row on 2026-11-10.

## Accuracy: evidence, fact check and refill
Videos go through three accuracy layers (`newsletter_video/factcheck.py`, shared by all three writers). The fact
check is on by default for the explainer and avx formats and off for avatar (`FACTCHECK_AVATAR=1` turns it on):

1. **Evidence.** With each video the writer returns, for every spoken line, the words of the story
   that support it, copied verbatim (or marks the line as a question, call to action or transition).
   Code checks each quote really occurs in the story (case, punctuation and "..." tolerated), so
   evidence cannot be made up, and checks the relations code can check: a number said to be below,
   above or within a range or another number, a percentage going up or down, "more than" vs
   "nearly". A failure goes back to the writer once with the exact error, like the other validators.
2. **An independent fact check.** A separate Claude call that sees only the sources and the
   finished video (every spoken line, every on-screen string, the title and caption) judges each
   item supported, partially supported, unsupported, contradicted or not a claim, with the source
   quote and a reason. It enforces: nothing beyond the source, correct comparisons, numbers, units
   and time frames, the source's attribution ("reportedly", "plans to"), no added causes, motives or
   superlatives, the right entities, and for allegations about named people: attribution in the
   same sentence, the source's caveats kept, no loaded words and no accusatory question. A video
   with a blocker is repaired once with those reasons and checked again; if it still fails it is
   not made. One call checks five videos; a day takes two or three calls (~20 s each).
3. **Refill.** When any check drops a video, the writer is asked again for the shortfall from the
   stories nobody has used (at most `FACTCHECK_REFILL_ROUNDS` extra rounds). A day can still end
   short: a failing video is never shipped to hit the count.

If the checker itself is unavailable (no key, API down, timeout, a malformed answer) the run does
not stop: the validated videos ship marked `unverified` and the log says so loudly. A verdict of
unsupported or contradicted never fails open. Each sidecar and plan entry keeps a small
`fact_check` record (verdict counts, the quote behind each spoken line, anything flagged, any
repair), and the day's dropped videos with their reasons go to `status.json` (`dropped`) and the
plan (`dropped`).
