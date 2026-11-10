#!/bin/bash
# Starts the control panel and opens it in your browser.
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python -m newsletter_video.ui "$@"
