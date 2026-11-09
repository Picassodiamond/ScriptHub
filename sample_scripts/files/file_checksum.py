# --- scripthub ---
# {
#   "name": "File checksum",
#   "description": "Print a file's hash, or check it against the expected one (fails when it differs).",
#   "category": "Files",
#   "parameters": [
#     {"name": "file", "label": "File", "type": "file", "required": true, "placeholder": "C:\\Downloads\\setup.exe"},
#     {"name": "algorithm", "label": "Algorithm", "type": "select", "choices": ["sha256", "sha1", "md5"], "default": "sha256"},
#     {"name": "expected", "label": "Expected hash", "type": "text", "pattern": "[0-9a-fA-F]*", "help": "Optional"}
#   ]
# }
# --- end scripthub ---
"""Hash a file in chunks; exit 1 when --expected is given and does not match."""
import argparse
import hashlib
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--file", required=True)
parser.add_argument("--algorithm", choices=["sha256", "sha1", "md5"], default="sha256")
parser.add_argument("--expected", default="")
args = parser.parse_args()

h = hashlib.new(args.algorithm)
try:
    with open(args.file, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
except OSError as exc:
    sys.exit(f"Cannot read {args.file}: {exc}")

digest = h.hexdigest()
print(f"{args.algorithm}  {digest}  {args.file}")
if args.expected:
    if digest.lower() == args.expected.strip().lower():
        print("OK: matches the expected hash.")
    else:
        print(f"MISMATCH: expected {args.expected}", file=sys.stderr)
        sys.exit(1)
