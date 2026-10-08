#!/bin/bash
# Entry point for the launchd daily job — launchd doesn't source shell rc
# files, so this resolves everything by absolute path.
set -euo pipefail
cd "/Users/matthewthomas/TLDR Brainrot"
exec ./venv/bin/python -m newsletter_video.run_pipeline
