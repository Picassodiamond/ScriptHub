# --- scripthub ---
# {
#   "name": "Disk space check",
#   "description": "Show free space on a drive or mount point and fail when it is fuller than the limit. Handy as the first step of a sequence.",
#   "category": "System",
#   "parameters": [
#     {"name": "path", "label": "Drive or folder", "type": "directory", "default": ".", "required": true, "help": "Any folder on the drive, e.g. C:\\ or /var"},
#     {"name": "max_used", "label": "Fail above (% used)", "type": "number", "default": 90, "min": 1, "max": 100}
#   ]
# }
# --- end scripthub ---
"""Exit 1 when the file system holding PATH is more than MAX_USED percent full."""
import argparse
import os
import shutil
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--path", default=".")
parser.add_argument("--max-used", type=int, default=90)
args = parser.parse_args()

try:
    total, used, free = shutil.disk_usage(args.path)
except OSError as exc:
    sys.exit(f"Cannot read {args.path}: {exc}")

gb = 1024 ** 3
percent = used * 100 / total
print(f"{os.path.abspath(args.path)}")
print(f"  total {total / gb:8.1f} GB")
print(f"  used  {used / gb:8.1f} GB  ({percent:.0f}%)")
print(f"  free  {free / gb:8.1f} GB")
if percent > args.max_used:
    print(f"FULL: {percent:.0f}% used is above the {args.max_used}% limit.", file=sys.stderr)
    sys.exit(1)
print(f"OK: below the {args.max_used}% limit.")
