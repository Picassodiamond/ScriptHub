#!/usr/bin/env bash
# --- scripthub ---
# name: Largest folders (Bash)
# description: "The biggest entries in a folder (du) and the file system's free space (df). For Linux hosts over SSH."
# category: System
# platforms: [linux, macos]
# parameters:
#   - name: path
#     label: Folder
#     type: directory
#     default: /var/log
#     required: true
#   - name: top
#     label: Entries
#     type: number
#     default: 10
#     min: 1
#     max: 100
# --- end scripthub ---
set -u
path="" top=10
while [ $# -gt 0 ]; do
  case "$1" in
    --path) path="${2:-}"; shift 2 ;;
    --top)  top="${2:-}";  shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -d "$path" ] || { echo "Not a folder: $path" >&2; exit 1; }

echo "Largest $top entries in $path"
du -sk -- "$path"/* 2>/dev/null | sort -rn | head -n "$top" \
  | awk '{ size=$1; $1=""; printf "%10.1f MB %s\n", size/1024, $0 }'
echo
df -h -- "$path" | tail -n 1
