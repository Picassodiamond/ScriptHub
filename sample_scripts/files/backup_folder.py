# --- scripthub ---
# {
#   "name": "Back up a folder",
#   "description": "Pack a folder into a dated .zip or .tar.gz and keep only the newest backups.",
#   "category": "Files",
#   "parameters": [
#     {"name": "source", "label": "Folder to back up", "type": "directory", "required": true, "placeholder": "C:\\Users\\me\\Documents\\notes"},
#     {"name": "destination", "label": "Backup folder", "type": "directory", "required": true, "placeholder": "D:\\Backups"},
#     {"name": "format", "label": "Format", "type": "select", "choices": [{"value": "zip", "label": ".zip"}, {"value": "gztar", "label": ".tar.gz"}], "default": "zip"},
#     {"name": "keep", "label": "Backups to keep", "type": "number", "default": 5, "min": 1, "max": 365}
#   ]
# }
# --- end scripthub ---
"""Archive SOURCE into DESTINATION/<name>-YYYYMMDD-HHMMSS.<ext>, then delete the oldest beyond --keep."""
import argparse
import datetime
import os
import shutil
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True)
parser.add_argument("--destination", required=True)
parser.add_argument("--format", choices=["zip", "gztar"], default="zip")
parser.add_argument("--keep", type=int, default=5)
args = parser.parse_args()

source, dest = os.path.abspath(args.source), os.path.abspath(args.destination)
if not os.path.isdir(source):
    sys.exit(f"Not a folder: {source}")
try:
    inside = os.path.commonpath([source, dest]) == source
except ValueError:                      # different drives
    inside = False
if inside:
    sys.exit("The backup folder must not be inside the folder being backed up.")
os.makedirs(dest, exist_ok=True)

name = os.path.basename(source.rstrip("\\/")) or "backup"
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
print(f"Packing {source} ...", flush=True)
archive = shutil.make_archive(os.path.join(dest, f"{name}-{stamp}"), args.format, root_dir=source)
print(f"Created {archive} ({os.path.getsize(archive) / 1024 / 1024:.1f} MB)")

suffix = ".zip" if args.format == "zip" else ".tar.gz"
ours = sorted(f for f in os.listdir(dest) if f.startswith(name + "-") and f.endswith(suffix))
for old in ours[: max(0, len(ours) - args.keep)]:
    os.remove(os.path.join(dest, old))
    print(f"Removed old backup {old}")
print(f"{min(len(ours), args.keep)} backup(s) kept.")
