# --- scripthub ---
# {
#   "name": "Directory Backup",
#   "description": "Create a ZIP backup of a source directory.",
#   "category": "Backup",
#   "parameters": [
#     {"name": "source", "label": "Source directory", "type": "directory", "required": true, "editable_at_runtime": true, "help": "Directory to back up", "placeholder": "/data/project", "pass_as": "flag"},
#     {"name": "destination", "label": "Destination directory", "type": "directory", "default": "./backups", "required": true, "editable_at_runtime": true, "help": "Where the ZIP file is written", "pass_as": "flag"},
#     {"name": "compress", "label": "Compress archive", "type": "boolean", "default": true, "editable_at_runtime": true, "pass_as": "flag", "required": false}
#   ]
# }
# --- end scripthub ---
"""Create a ZIP backup of a directory."""
import argparse
import datetime
import os
import sys
import zipfile

parser = argparse.ArgumentParser(description="Back up a directory into a ZIP file.")
parser.add_argument("--source", required=True, help="Directory to back up")
parser.add_argument("--destination", required=True, help="Directory to write the ZIP into")
parser.add_argument("--compress", action="store_true", help="Compress the archive")
args = parser.parse_args()

source = os.path.abspath(args.source)
if not os.path.isdir(source):
    sys.exit(f"Source is not a directory: {source}")
os.makedirs(args.destination, exist_ok=True)

stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
target = os.path.join(os.path.abspath(args.destination), f"{os.path.basename(source) or 'backup'}-{stamp}.zip")
mode = zipfile.ZIP_DEFLATED if args.compress else zipfile.ZIP_STORED

count = 0
with zipfile.ZipFile(target, "w", mode) as zf:
    for folder, _dirs, files in os.walk(source):
        for name in files:
            full = os.path.join(folder, name)
            if os.path.abspath(full) == target:
                continue
            zf.write(full, os.path.relpath(full, source))
            count += 1
            print(f"Added {os.path.relpath(full, source)}")

print(f"Backed up {count} file(s) from {source}")
print(f"Archive: {target} ({os.path.getsize(target)} bytes, {'compressed' if args.compress else 'stored'})")
