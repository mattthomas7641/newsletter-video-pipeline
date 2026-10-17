# What performing tech explainers and Shorts have in common

Research notes behind the explainer style (third pass). Sources are
creator-economy and tooling blogs plus one academic thesis; none of the
numbers come from YouTube itself, so they are treated as directional and
checked against our own analytics once videos run. A planned read-only
YouTube Data API sample of top Shorts could not run from this worktree (no
upload credentials here), so title/channel patterns below come from the
written sources only.

## Findings

| Area | What the sources agree on | Source |
|---|---|---|
| First 1–3 s | The swipe decision happens in seconds 0–3 (30–50 % of viewers leave); cut every intro, logo and fade; the first frame must make sense with the sound off and show the outcome or the tension, not the setup. Trimming a slow opening by a fraction of a second visibly flattens the retention cliff. | aibrify playbook; gofaceless two-second rule; blitzcut hooks |
| Cadence | A visual change (cut, zoom, text swap, colour shift, camera move) roughly every 1.5–2 s; faster than ~1.2 s reads as noise; gaps over ~2.5 s dip retention. Long-form tolerates 5–8 s. | aibrify playbook |
| Captions | 60–80 % of Shorts start with sound off; captions present by second 1; 2–4 (at most 5) words per frame; high contrast; lower-centre third; word-by-word or short-phrase styles outperform static subtitles. | aibrify; clipspeed; blitzcut captions; opus.pro |
| Length | 20–35 s general sweet spot; educational/list formats 25–40 s; over 45 s makes an 80 % average-view hard. | aibrify |
| Density | One idea per shot; the frame should be full (a Fireship frame is either teaching, entertaining or transitioning, nothing else); hierarchy from scale, not decoration. Aesthetically appealing design measurably improves how information is received. | Fireship style brief; Kurzgesagt process page; FH St. Pölten thesis on animated explainers |
| Retention devices | Open loops ("here's the catch"), payoff-before-process, numbers as hooks, a mid-video turn, chapters with clear visual structure (Vox/Harris: maps, charts, kinetic type, highlighter emphasis). | Fireship brief; freelancer briefs of the Vox/Harris look; film pacing research (average shot length < 4 s) |
| Endings | No dead stop: the final frame should carry motion/colour/question back into frame one so a replay feels intentional (replays show as >100 % at second 1); a half-second "button" callback to the hook. | aibrify; virvid looping structure |
| Titles | Short, concrete, number- or contrast-led ("X costs 5x more. Worth it?"); the measured title data for the AI/tech niche is thin, so this stays a hypothesis to test. | general Shorts guides |

## Principles the template embodies (ranked)

1. **Payoff in the first second.** The title scene lands kicker + pictogram + headline by ~0.7 s, no intro, captions from the first word.
2. **Something changes every ≤ 1.2 s.** Every scene has continuous purposeful motion (counts, draws, marker travel, push-in + head parallax, sweeps); reveal cues are capped at 1.6 s + 1.4 s per item; `scripts/motion_audit.py` fails a render with any static stretch over 1.2 s outside the caption band.
3. **Fill the frame with one idea.** Each hero graphic takes 60–70 % of the stage; type scale floors (labels 34 px, body 48 px, captions 44 px); no two text-only scenes in a row, at least two graphic scenes per video.
4. **Captions for sound-off viewing.** 44 px, ≤ 5 words, phrase-aware chunks, the spoken word in ink, the rest muted; always inside the lower third.
5. **Icons mean something.** A curated semantic set with documented uses; the outro always uses the comment bubble; no filler or repeated icons in consecutive scenes.
6. **Length 36–42 s.** 98–110 words, pacing floor 0.88, tail ≤ 1 s.
7. **End into the loop.** The outro is an animated CTA that keeps moving to the last frame and echoes the title's kicker/pictogram so a replay reads as deliberate.
8. **Contrast and variety.** Alternating push/wipe cuts, a full-bleed accent field for the kinetic key-phrase scene, charts/diagrams/devices chosen by the beat.

## Sources

- aibrify, "YouTube Shorts retention curve playbook" — https://aibrify.com/blog/youtube-shorts-retention-curve-playbook
- gofaceless, "How to hook viewers in the first 2 seconds" — https://www.gofaceless.ai/blog/mastering-first-impressions-two-second-rule
- blitzcut, "Best YouTube Shorts hooks (2026)" — https://blitzcutai.com/blog/best-youtube-shorts-hooks-2026 and "Best caption style for YouTube Shorts (2026)" — https://blitzcutai.com/blog/best-caption-style-youtube-shorts-2026
- clipspeed, "11 caption styles ranked" — https://www.clipspeed.ai/blog/11-caption-styles-ranked-viral-performance-2026.html
- opus.pro, "Caption analytics" — https://www.opus.pro/blog/caption-styles-drive-views
- virvid, "Looping structure for Shorts retention" — https://virvid.ai/blog/looping-structure-shorts-retention-2026
- Fireship-style script brief — https://skills.sh/jarmen423/skills/fireship-video-style
- Kurzgesagt, "Our video process" — https://kurzgesagt.org/youtube/
- FH St. Pölten thesis, "Lernen mit animierten Erklärvideos" — https://phaidra.ustp.at/o:3960
- Film pacing: average shot length research summarised in https://kc.umn.ac.id/id/eprint/46444/2/BAB_I.pdf
