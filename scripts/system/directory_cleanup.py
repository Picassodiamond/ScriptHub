# --- scripthub ---
# {
#   "name": "Directory Cleanup",
#   "description": "Delete files older than N days. Runs as a dry run (list only) until you type DELETE in the confirmation field.",
#   "category": "Maintenance",
#   "parameters": [
#     {"name": "target", "label": "Target directory", "type": "directory", "required": true, "placeholder": "/var/tmp/uploads", "help": "Files are only removed inside this directory. Filesystem roots and home folders are refused."},
#     {"name": "days", "label": "Older than (days)", "type": "number", "default": 30, "required": true, "min": 1, "max": 3650, "help": "Files not modified for at least this many days."},
#     {"name": "pattern", "label": "File pattern", "type": "text", "default": "*", "help": "Glob pattern, e.g. *.log or backup-*.zip"},
#     {"name": "recursive", "label": "Include sub-directories", "type": "boolean", "default": false},
#     {"name": "confirm", "label": "Confirmation", "type": "text", "placeholder": "Type DELETE to really delete", "pattern": "DELETE", "help": "Leave empty for a dry run that only lists the files. Type DELETE (capitals) to delete them."}
#   ]
# }
# --- end scripthub ---
"""Delete old files from a directory - safely.

Without --confirm DELETE this is a DRY RUN: it only lists what would be
removed. Only regular files are touched (never directories or symlinks), and
filesystem roots / home directories are refused.
"""
import argparse
import fnmatch
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser(description="Delete files older than N days (dry run unless confirmed).")
parser.add_argument("--target", required=True, help="Directory to clean")
parser.add_argument("--days", type=int, required=True, help="Delete files not modified for this many days")
parser.add_argument("--pattern", default="*", help="Only files matching this glob, e.g. *.log")
parser.add_argument("--recursive", action="store_true", help="Include sub-directories")
parser.add_argument("--confirm", default="", help="Type DELETE to really delete")
args = parser.parse_args()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def refuse(message: str):
    print(f"Refusing: {message}", file=sys.stderr)
    sys.exit(2)


target = Path(args.target).expanduser()
if not target.is_dir():
    refuse(f"not a directory: {target}")
target = target.resolve()
if target.parent == target:
    refuse("the target is a filesystem root")
if target == Path.home().resolve():
    refuse("the target is your home directory")
if len(target.parts) < 3:
    refuse(f"{target} is too close to the filesystem root")
if args.days < 1:
    refuse("--days must be at least 1")
if args.confirm and args.confirm != "DELETE":
    refuse('the confirmation must be exactly "DELETE" (or left empty for a dry run)')

really_delete = args.confirm == "DELETE"
cutoff = time.time() - args.days * 86400
print(f"Target:   {target}")
print(f"Rule:     files matching {args.pattern!r}, not modified for {args.days} day(s)"
      f"{', including sub-directories' if args.recursive else ''}")
print(f"Mode:     {'DELETE' if really_delete else 'DRY RUN (nothing will be deleted)'}")
print("-" * 60)

candidates = []
for folder, dirs, files in os.walk(target, followlinks=False):
    for name in files:
        if not fnmatch.fnmatch(name, args.pattern):
            continue
        path = Path(folder) / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            st = path.stat()
        except OSError as exc:
            print(f"skip   {path}  ({exc})")
            continue
        if st.st_mtime < cutoff:
            candidates.append((path, st.st_size, (time.time() - st.st_mtime) / 86400))
    if not args.recursive:
        break

total = sum(size for _p, size, _a in candidates)
failed = 0
for path, size, age in sorted(candidates):
    label = "delete" if really_delete else "would delete"
    if really_delete:
        try:
            path.unlink()
        except OSError as exc:
            failed += 1
            print(f"FAILED {path}  ({exc})", file=sys.stderr)
            continue
    print(f"{label:<12} {path}  [{human(size)}, {age:.0f} days old]")

print("-" * 60)
if not candidates:
    print("Nothing to clean up.")
elif really_delete:
    print(f"Deleted {len(candidates) - failed} file(s), {human(total)} freed.")
    if failed:
        print(f"{failed} file(s) could not be deleted.", file=sys.stderr)
        sys.exit(1)
else:
    print(f"{len(candidates)} file(s), {human(total)} would be deleted.")
    print('DRY RUN: nothing was deleted. Set Confirmation to DELETE to remove them.')
