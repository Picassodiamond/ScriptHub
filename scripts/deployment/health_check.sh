#!/usr/bin/env bash
# Check that a URL answers with a successful HTTP status.
#
# Usage:   TIMEOUT_SECS=5 health_check.sh URL
#
# Shows two other ways to receive parameters (see health_check.json):
#   * URL          - a POSITIONAL argument ("pass_as": "positional")
#   * TIMEOUT_SECS - an ENVIRONMENT VARIABLE ("pass_as": "env")
#
# Exits non-zero when the check fails, so the run is shown as Failed.
set -u

url="${1:-}"
timeout="${TIMEOUT_SECS:-5}"

[ -n "$url" ] || { echo "Usage: $0 URL" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "Error: curl is not installed" >&2; exit 127; }

echo "Checking $url (timeout ${timeout}s)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$timeout" -- "$url")
status=$?

if [ "$status" -ne 0 ]; then
  echo "FAILED: could not connect (curl exit code $status)" >&2
  exit 1
fi

echo "HTTP status: $code"
case "$code" in
  2??|3??) echo "OK" ;;
  *) echo "FAILED: unexpected status $code" >&2; exit 1 ;;
esac
