"""The container's entry point: the daily run, wrapped in "load state, run, save state".

    python -m newsletter_video.cloud_run [YYYY-MM-DD]
    python -m newsletter_video.cloud_run render-plan YYYY-MM-DD --final   # render (and upload) a saved plan

1. Secrets that arrive as environment variables (Secret Manager -> env) are written to the files the
   pipeline expects under secrets/.
2. If STATE_BUCKET is set, the bucket's state is copied down (cloud_state.py).
3. The normal supervised daily run (run_pipeline.main) happens, unchanged.
4. State is copied back up every few minutes during the run and once more at the end, whatever the outcome.

Nothing here runs on the Mac: run_daily.sh still calls run_pipeline directly.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("newsletter_video")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = PROJECT_ROOT / "secrets"
# env var -> file under secrets/
SECRET_FILES = {
    "YOUTUBE_CLIENT_SECRET_JSON": "youtube_client_secret.json",
    "YOUTUBE_TOKEN_JSON": "youtube_token.json",
}


# Secrets that arrive as environment variables. A stray newline (easy to add when a value is piped into Secret
# Manager) makes an HTTP header invalid, which the Anthropic client reports as a bare "connection error".
CLEAN_ENV = ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "NOTIFY_WEBHOOK_URL")


def clean_env(env=None) -> list[str]:
    """Strips whitespace and one pair of surrounding quotes from the secret env vars, in place. Returns the
    names that changed."""
    env = os.environ if env is None else env
    changed = []
    for name in CLEAN_ENV:
        raw = env.get(name)
        if raw is None:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        if value != raw:
            env[name] = value
            changed.append(name)
    return changed


def materialize_secrets(env=None, secrets_dir: Path = SECRETS_DIR) -> list[str]:
    """Writes each secret held in an environment variable to its file (0600). Returns the file names written."""
    env = os.environ if env is None else env
    written = []
    for var, filename in SECRET_FILES.items():
        value = (env.get(var) or "").strip()
        if not value:
            continue
        secrets_dir.mkdir(parents=True, exist_ok=True)
        path = secrets_dir / filename
        path.write_text(value)
        path.chmod(0o600)
        written.append(filename)
    return written


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fixed = clean_env()
    if fixed:
        log.warning("Cleaned stray whitespace from: %s (fix the stored secret too)", ", ".join(fixed))
    log.info("Secret files written: %s", ", ".join(materialize_secrets()) or "none (using the files already there)")

    from . import cloud_state, run_pipeline
    from .notify import notify

    state = None
    try:
        state = cloud_state.from_env()
    except Exception as exc:
        notify("Cloud run not started", f"Could not reach the state bucket: {exc}")
        return 1
    stop = None
    if state is not None:
        try:
            state.pull()
        except Exception as exc:
            notify("Cloud run not started", f"Could not load saved state, so a rerun could repeat or skip "
                                            f"videos: {exc}")
            return 1
        stop = state.start_periodic_push()
    try:
        if argv and argv[0] == "render-plan":  # render a plan already in the bucket (e.g. catch-up videos)
            from . import render_plan

            return render_plan.main(argv[1:])
        return run_pipeline.main(argv)
    finally:
        if stop is not None:
            stop.set()
        if state is not None:
            result = state.push()
            if result["failed"]:
                notify("Cloud state not fully saved", f"{result['failed']} files could not be copied to the "
                                                      "bucket; the next run may repeat work.")


if __name__ == "__main__":
    sys.exit(main())
