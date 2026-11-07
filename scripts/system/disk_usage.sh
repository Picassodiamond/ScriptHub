#!/usr/bin/env bash
# Show the largest entries under a directory, plus free space.
#
# Usage: disk_usage.sh --path DIR [--top N]
#
# Parameters arrive as normal command-line options (see disk_usage.json:
# "pass_as": "flag"), so a shell script can parse them with a simple loop.
set -u

path=""
top=10

while [ $# -gt 0 ]; do
  case "$1" in
    --path) path="${2:-}"; shift 2 ;;
    --top)  top="${2:-}";  shift 2 ;;
    -h|--help) echo "Usage: $0 --path DIR [--top N]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$path" ] || { echo "Error: --path is required" >&2; exit 2; }
[ -d "$path" ] || { echo "Error: not a directory: $path" >&2; exit 1; }
case "$top" in
  ''|*[!0-9]*) echo "Error: --top must be a positive whole number" >&2; exit 2 ;;
esac

echo "Largest $top entries under $path"
echo "----------------------------------------"
du -sk -- "$path"/* "$path"/.[!.]* 2>/dev/null \
  | sort -rn \
  | head -n "$top" \
  | awk '{ size=$1; $1=""; sub(/^ /, ""); printf "%10.1f MB  %s\n", size/1024, $0 }'

echo
echo "Filesystem usage:"
df -h -- "$path" | tail -n 1
