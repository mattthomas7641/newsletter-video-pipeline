[Back to the README](../README.md)

# Cost

Measured November 2026 on `claude-sonnet-5-5` ($2 / $10 per million input / output tokens), per finished avatar video:
about **$0.14** with the original writer prompt and about **$0.22** with the lean prompt that adds the clarity and
honesty rules; Cloud Run adds an estimated $0.35 a day (4 vCPU / 8 GiB for about 90 minutes, before the free tier). Roughly 65% of the writer's billed output is hidden reasoning, which is
why a longer prompt costs more (a longer clarity prompt raised output per video by 46%) and why a lower effort
setting, which cut tokens 22%, also cut first-pass success from 6 of 10 to 3 of 10. What did not work: optional
fields or per-style variants in the output schema (the API refuses both: "Schema is too complex", "compiled grammar is
too large"), and Haiku 5.5 as the drafter (thinking off cannot count or follow several rules; with it on it reasons
without end). What did work: Haiku 5.5 for shortening on-screen text, with a plain-code trim as the fallback.

Guards: `CLAUDE_RUN_BUDGET_USD` (default 4.0, 0 = off) stops a run's Claude calls once it has spent that much, the run
summary ends with a cost line, `AVX_DRAFT_MODEL` / `AVX_SHORTEN_MODEL` pick the models for drafting and shortening, and
the Batch API (50% off, results usually within an hour) is the next lever if you need the cost lower.
