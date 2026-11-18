[Back to the README](../README.md)

# Configuration (`.env`)

Copy `.env.example` to `.env`. Everything is optional:

| Setting | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Claude writes the scripts (much more engaging than the rule-based fallback) |
| `WRITER_EFFORT` | How hard Claude reasons (default `medium`: same script quality as the model default at about 4x fewer output tokens) |
| `VIDEOS_PER_DAY` | Videos per run (default 3; "~6 uploads/day" is a commonly cited quota estimate, not a confirmed or enforced limit: 16 uploads succeeded in one day during testing with no quota errors) |
| `TYPESAFE_API_KEY` | Jev screens and ranks stories before Claude sees them (see [Story selection with Jev](story-selection.md)) |
| `TTS_PROVIDER` | `kokoro` (default), `voicebox` (local cloned voice), `elevenlabs` (cloud cloned voice) or `say` (macOS) |
| `KOKORO_VOICE`, `KOKORO_SPEED` | Kokoro voice and pace (`npx hyperframes tts --list` for voices) |
| `VOICEBOX_PROFILE_ID`, `VOICEBOX_ENGINE`, `VOICEBOX_MODEL_SIZE`, `VOICEBOX_INSTRUCT` | Voicebox settings (see [Custom voice](customizing.md#custom-voice)) |
| `VOICE_EFFECT`, `CARTOON_PITCH` | `cartoon` pitches any voice up (1.3 subtle to 1.6 squeaky); `none` (default) keeps it natural |
| `VIDEO_STYLE` | `explainer` (default), a clean animated explainer with no avatar or footage; `avatar`, the original look; or `avx`, the avatar explainer the cloud job runs (see [Video styles](video-styles.md)) |
| `SOURCE` | Where stories come from: `tldr` (default, the only source so far; see `newsletter_video/sources/`) |
| `VIDEO_THEME` | Explainer style only: `light` (default), `dark` or `cinematic` |
| `BACKGROUND_STYLE` | Avatar style only: `gameplay` (default) or `none` for the plain graph-paper background |
| `DAILY_MODE` | What the scheduled run does: `full` (default) plans, renders and uploads in one go; `plan` only writes the day's plan and notifies you (see [Approval mode](scheduling.md#approval-mode)) |
| `YOUTUBE_UPLOAD` | `1` uploads each day's videos after rendering |
| `YOUTUBE_PRIVACY`, `YOUTUBE_SCHEDULE` | Visibility and timed publishing (once your API project is audited) |
| `CHARACTER` | The narrator to use, a folder name under `assets/characters/` (see [Customizing](customizing.md#the-narrator-character)) |
| `CLAUDE_RUN_BUDGET_USD` | Stops a run's Claude calls once it has spent this much (default 4.0, 0 = off; see [Cost](cost.md)) |
| `AVX_DRAFT_MODEL`, `AVX_SHORTEN_MODEL`, `FACTCHECK_MODEL` | Models for avx drafting, for shortening on-screen text, and for the fact check (default: the writer's model) |
| `AVX_EDITOR` | `0` switches off the avx editor pass (default on) |
| `WHISPER_BACKEND` | Which Whisper backend aligns words to audio (default `auto`) |
| `STREAM_UPLOADS` | `0` uploads all videos at the end of the run instead of as each one finishes |
| `UPLOAD_MAX_AGE_DAYS` | Leftover videos older than this are not uploaded (default 2; `off` removes the limit) |
| `LOOK_PRESET`, `LOOK_BG`, `LOOK_ACCENT`, `LOOK_ACCENT_ROTATE` | The look used when there is no saved lineup (see [Control panel](control-panel.md)) |
| `FALLBACK_UPLOAD` | What to do with rule-based fallback scripts when uploads are on: `skip` (default: make none, notify), `private` or `public` |
| `NOTIFY_WEBHOOK_URL`, `NOTIFY_WEBHOOK_FORMAT`, `NOTIFY_MACOS` | Where run notifications go (see [When things fail](failure-handling.md)) |
| `RUN_DEADLINE_MINUTES`, `RENDER_TIMEOUT_SECONDS`, `VOICEBOX_GENERATION_TIMEOUT` | Wall-clock limits: whole run (default 240), one render attempt (1200), one Voicebox clip (420) |
| `FACTCHECK`, `FACTCHECK_AVX`, `FACTCHECK_EXPLAINER`, `FACTCHECK_AVATAR` | The fact check (see [Accuracy](accuracy.md)): `FACTCHECK=0` switches it off everywhere; the per-format switches override the defaults |
| `FACTCHECK_FETCH_ARTICLES`, `FACTCHECK_REFILL_ROUNDS`, `FACTCHECK_BATCH`, `FACTCHECK_TIMEOUT_SECONDS`, `FACTCHECK_EFFORT` | Give the checker the linked article too (default off), extra writer rounds to replace dropped videos (2), videos per checker call (5), the checker's time budget per round (480 s), its effort (the writer's) |
| `STYLE_LIBRARY`, `STYLE_VISION*`, `STYLE_RECREATE_ROUNDS`, `STYLE_PLANNER_*` | The style library folder (`off` to disable) and the model/budget settings for analysing references and planning styles (see [Style library](video-styles.md#style-library-teach-the-look-by-example)) |

Videos land between 40 and 45 seconds: Claude aims for 100–110 words, and if the voiceover
still runs long or short it's re-voiced slightly faster or slower. Prices and units are
rewritten into spoken words before voicing ("$0.10" → "10 cents"), and coined words the voice
gets wrong can be respelled in `PRONUNCIATIONS` in `pronounce.py`.
