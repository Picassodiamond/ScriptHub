# --- scripthub ---
# {
#   "name": "Clean up old files",
#   "description": "Find files older than N days that match a pattern, and delete them only when asked. A dry run is the default.",
#   "category": "Files",
#   "parameters": [
#     {"name": "folder", "label": "Folder", "type": "directory", "required": true, "placeholder": "C:\\Temp\\logs"},
#     {"name": "pattern", "label": "File pattern", "type": "text", "default": "*.log", "required": true},
#     {"name": "days", "label": "Older than (days)", "type": "number", "default": 30, "min": 0, "max": 36500},
#     {"name": "recursive", "label": "Include sub-folders", "type": "boolean", "default": false},
#     {"name": "delete", "label": "Delete them", "type": "boolean", "default": false, "help": "Off = only list what would be deleted"}
#   ]
# }
# --- end scripthub ---
"""Delete (or with a dry run, list) old files. Links are never followed or deleted."""
import argparse
import fnmatch
import os
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--folder", required=True)
parser.add_argument("--pattern", default="*.log")
parser.add_argument("--days", type=int, default=30)
parser.add_argument("--recursive", action="store_true")
parser.add_argument("--delete", action="store_true")
args = parser.parse_args()

folder = os.path.abspath(args.folder)
if not os.path.isdir(folder):
    sys.exit(f"Not a folder: {folder}")
if os.path.dirname(folder) == folder:
    sys.exit(f"Refusing to clean a drive or file system root: {folder}")

cutoff = time.time() - args.days * 86400
matches = []
for dirpath, dirnames, filenames in os.walk(folder):
    if not args.recursive:
        dirnames[:] = []
    for name in filenames:
        path = os.path.join(dirpath, name)
        if os.path.islink(path) or not fnmatch.fnmatch(name.lower(), args.pattern.lower()):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                matches.append(path)
        except OSError:
            pass

verb = "Deleting" if args.delete else "Would delete (dry run)"
print(f"{verb}: {len(matches)} file(s) matching {args.pattern} older than {args.days} days in {folder}")
failed = 0
for path in matches:
    print(f"  {path}")
    if args.delete:
        try:
            os.remove(path)
        except OSError as exc:
            failed += 1
            print(f"    not deleted: {exc}", file=sys.stderr)
if not args.delete and matches:
    print("Tick 'Delete them' to delete these files.")
sys.exit(1 if failed else 0)
