# --- scripthub ---
# name: Find large files
# description: List the largest files under a folder.
# category: Files
# parameters:
#   - name: folder
#     label: Folder
#     type: directory
#     default: .
#     required: true
#   - name: min_mb
#     label: At least (MB)
#     type: number
#     default: 10
#     min: 0
#     max: 1000000
#   - name: top
#     label: Show
#     type: number
#     default: 20
#     min: 1
#     max: 1000
#   - name: pattern
#     label: File pattern
#     type: text
#     default: "*"
#     help: "e.g. *.log"
# --- end scripthub ---
"""Walk a folder and print the largest files, biggest first. Folders it cannot read are skipped."""
import argparse
import fnmatch
import heapq
import os
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--folder", default=".")
parser.add_argument("--min-mb", type=int, default=10)
parser.add_argument("--top", type=int, default=20)
parser.add_argument("--pattern", default="*")
args = parser.parse_args()

if not os.path.isdir(args.folder):
    sys.exit(f"Not a folder: {args.folder}")

limit = args.min_mb * 1024 * 1024
found, scanned = [], 0
for dirpath, dirnames, filenames in os.walk(args.folder, onerror=lambda e: print(f"skipped: {e}", file=sys.stderr)):
    dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    for name in filenames:
        if not fnmatch.fnmatch(name.lower(), args.pattern.lower()):
            continue
        path = os.path.join(dirpath, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        scanned += 1
        if size >= limit:
            found.append((size, path))

print(f"{scanned} files checked under {os.path.abspath(args.folder)}")
if not found:
    print(f"None of them is {args.min_mb} MB or larger.")
for size, path in heapq.nlargest(args.top, found):
    print(f"{size / 1024 / 1024:10.1f} MB  {path}")
