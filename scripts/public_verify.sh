#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/public_verify.sh <base-url> [digest-date] [artifact-id]

Examples:
  scripts/public_verify.sh http://127.0.0.1:8310 2026-07-30
  scripts/public_verify.sh https://example.localhost.run 2026-07-30 art_abc123

Environment:
  PUBLIC_VERIFY_API_KEY  Optional API key used for a no-op authenticated write smoke.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi

BASE_URL="${1%/}"
DIGEST_DATE="${2:-}"
ARTIFACT_ID="${3:-}"
PUBLIC_VERIFY_API_KEY="${PUBLIC_VERIFY_API_KEY:-}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch() {
  local path="$1"
  local output="$2"
  curl --fail --silent --show-error --location --max-time 20 "$BASE_URL$path" --output "$output"
}

status_request() {
  local method="$1"
  local path="$2"
  local body="$3"
  local output="$4"
  shift 4
  curl --silent --show-error --location --max-time 20 \
    --request "$method" \
    --header "Content-Type: application/json" \
    "$@" \
    --data "$body" \
    --output "$output" \
    --write-out "%{http_code}" \
    "$BASE_URL$path"
}

contains() {
  local file="$1"
  local pattern="$2"
  if ! grep -Fq "$pattern" "$file"; then
    echo "Expected '$pattern' in $file" >&2
    return 1
  fi
}

echo "Verifying $BASE_URL"

fetch "/health" "$TMP_DIR/health.json"
contains "$TMP_DIR/health.json" '"status":"ok"'

fetch "/api/v1/stats" "$TMP_DIR/stats.json"
contains "$TMP_DIR/stats.json" '"papers"'

fetch "/" "$TMP_DIR/index.html"
contains "$TMP_DIR/index.html" "AI Infra Research Hub"
contains "$TMP_DIR/index.html" "论文研读与专利转化"

fetch "/static/styles.css" "$TMP_DIR/styles.css"
contains "$TMP_DIR/styles.css" ":root"

fetch "/static/app.js" "$TMP_DIR/app.js"
contains "$TMP_DIR/app.js" "DOMContentLoaded"

anonymous_write_status="$(status_request "PATCH" "/api/v1/topics/aif-01" "{}" "$TMP_DIR/anonymous-write.json")"
if [[ "$anonymous_write_status" != "401" ]]; then
  echo "Expected anonymous PATCH /api/v1/topics/aif-01 to return 401, got $anonymous_write_status" >&2
  echo "Public deployments must set RESEARCH_HUB_API_KEY so anonymous writes fail closed." >&2
  exit 1
fi

if [[ -n "$PUBLIC_VERIFY_API_KEY" ]]; then
  keyed_write_status="$(
    status_request \
      "PATCH" \
      "/api/v1/topics/aif-01" \
      "{}" \
      "$TMP_DIR/keyed-write.json" \
      --header "X-API-Key: $PUBLIC_VERIFY_API_KEY"
  )"
  if [[ "$keyed_write_status" != "200" ]]; then
    echo "Expected keyed no-op PATCH /api/v1/topics/aif-01 to return 200, got $keyed_write_status" >&2
    exit 1
  fi
fi

if [[ -n "$DIGEST_DATE" ]]; then
  fetch "/api/v1/daily-digests/$DIGEST_DATE" "$TMP_DIR/digest.json"
  contains "$TMP_DIR/digest.json" '"date"'
fi

if [[ -n "$ARTIFACT_ID" ]]; then
  fetch "/api/v1/artifacts/$ARTIFACT_ID/download" "$TMP_DIR/artifact.bin"
  if [[ ! -s "$TMP_DIR/artifact.bin" ]]; then
    echo "Artifact download is empty: $ARTIFACT_ID" >&2
    exit 1
  fi
fi

echo "OK: public frontend, health, API, static assets, anonymous write protection, and requested optional checks passed."
