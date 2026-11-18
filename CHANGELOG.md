# Changelog

## November 2026

- **Avatar explainer format ("avx"):** a narrator on a plain colour background with one purpose-built motion graphic
  per beat (kinetic type, a counting number, a timeline, a versus, a chart, a relation, a checklist). The writer is
  `avx_writer.py`; the look is `avx_builder.py` and `hf_template_avx/`. See [Video styles](docs/video-styles.md).
- **Runs in the cloud:** a Cloud Run job started by Cloud Scheduler makes and posts the day's videos with no laptop
  involved: [docs/cloud.md](docs/cloud.md). A single video renders in under 3 minutes there.
- **Wider story scope:** Jev now also asks "would a well-informed follower of AI and tech want to know this today?",
  which lifted the share of must-cover stories in the writer's top-30 pool from 73% to 100% in a labelled two-week sample (595 stories; the labels came from Claude, so they are a proxy for your own judgment).
  Short stories are expanded from their linked article, or bundled into a quick-hits video.
- **Clearer, honest writing:** the writer is told to open on the news in plain words, explain every unfamiliar name,
  and report a story as it is. Code checks reject question openers, fragments, jargon in the hook and dramatic words
  the story does not use (`jargon.py`, `avx_writer.py`). This is the newer, clearer writer: it is in the code and
  tested, but it costs about $0.22 a video against $0.14, so the daily cloud job currently runs the earlier writer to
  stay under its daily budget. Deploying it is a rebuild and a job update ([docs/cloud.md](docs/cloud.md)).
- **Cost controls:** a per-run Claude budget, a cost line in every run summary, a cheap shortening step for text over
  its character cap, and measured numbers in [Cost](docs/cost.md).
