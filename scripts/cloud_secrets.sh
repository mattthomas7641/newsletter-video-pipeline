#!/bin/bash
# Copies the pipeline's secrets from this Mac into Google Secret Manager, and lets the daily job (and only it)
# read them. Run it yourself: the values go from your files straight to Google and never appear on screen.
#
#   ./scripts/cloud_secrets.sh
#
# Safe to run again: it adds a new version of each secret instead of making a second one.
# The job is pinned to a version number per secret, so after a re-run point the job at the new versions (printed below).
set -euo pipefail
cd "$(dirname "$0")/.."

GCLOUD="${GCLOUD:-$HOME/google-cloud-sdk/bin/gcloud}"
PROJECT="${PROJECT:-project-4cfd0497-02c5-4d28-b9a}"
JOB_SA="bytesize-daily-job@${PROJECT}.iam.gserviceaccount.com"

# name in Secret Manager | where the value comes from
#   env:KEY   = a line KEY=value in .env
#   file:PATH = a whole file
SECRETS=(
  "anthropic-api-key|env:ANTHROPIC_API_KEY"
  "typesafe-api-key|env:TYPESAFE_API_KEY"
  "notify-webhook-url|env:NOTIFY_WEBHOOK_URL"
  "youtube-client-secret|file:secrets/youtube_client_secret.json"
  "youtube-token|file:secrets/youtube_token.json"
)

value_of() {  # prints the value for a source spec, nothing else
  local spec="$1"
  case "$spec" in
    env:*)  local key="${spec#env:}"; grep -E "^${key}=" .env | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' ;;
    file:*) cat "${spec#file:}" ;;
  esac
}

for entry in "${SECRETS[@]}"; do
  name="${entry%%|*}"; spec="${entry#*|}"
  if [ -z "$(value_of "$spec")" ]; then
    echo "SKIP  $name  (nothing found at $spec)"; continue
  fi
  if ! "$GCLOUD" secrets describe "$name" --project="$PROJECT" >/dev/null 2>&1; then
    "$GCLOUD" secrets create "$name" --project="$PROJECT" --replication-policy=automatic --quiet >/dev/null 2>&1
  fi
  # printf '%s' (not echo): a trailing newline in the stored value breaks API keys and URLs.
  printf '%s' "$(value_of "$spec")" | "$GCLOUD" secrets versions add "$name" --project="$PROJECT" --data-file=- --quiet >/dev/null 2>&1
  "$GCLOUD" secrets add-iam-policy-binding "$name" --project="$PROJECT" \
    --member="serviceAccount:${JOB_SA}" --role="roles/secretmanager.secretAccessor" --quiet >/dev/null 2>&1
  version="$("$GCLOUD" secrets versions list "$name" --project="$PROJECT" --limit=1 --format='value(name)' 2>/dev/null || true)"
  echo "OK    $name  (now at version ${version:-?})"
done
echo "Done. Each secret can be read only by $JOB_SA."
# The job reads a fixed version of each secret, not "latest", so a new version changes nothing until you say so.
echo "The job is pinned to version numbers. To use a new version:"
echo "  gcloud run jobs update <job> --region <region> --update-secrets ENV_NAME=<secret-name>:<version>"
