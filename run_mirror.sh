#!/bin/bash
# Entry point for a second channel's daily job (launchd): remakes the first channel's best videos with this
# channel's narrator and voice (newsletter_video/mirror_channel.py). No Claude calls. Run from this channel's own
# project folder (its own .env, secrets/, output/ and assets/characters/).
set -euo pipefail
cd "$(dirname "$0")"
exec caffeinate -is ./venv/bin/python -m newsletter_video.mirror_channel --render
