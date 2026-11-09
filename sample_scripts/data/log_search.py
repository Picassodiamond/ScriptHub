# --- scripthub ---
# name: Search a log file
# description: Show the lines of a log that match a pattern, with context, and a count per level. Try it on app.log next to it.
# category: Data
# parameters:
#   - name: file
#     label: Log file
#     type: file
#     default: app.log
#     required: true
#     help: "Relative paths start in this script's folder"
#   - name: pattern
#     label: Pattern (regular expression)
#     type: text
#     default: "ERROR|WARN"
#     required: true
#   - name: ignore_case
#     label: Ignore case
#     type: boolean
#     default: true
#   - name: context
#     label: Lines of context
#     type: number
#     default: 0
#     min: 0
#     max: 20
#   - name: fail_if_found
#     label: Fail when found
#     type: boolean
#     default: false
#     help: Useful in a sequence
# --- end scripthub ---
"""grep-like search that works the same on Windows and Linux."""
import argparse
import re
import sys
from collections import Counter, deque

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--file", required=True)
parser.add_argument("--pattern", required=True)
parser.add_argument("--ignore-case", action="store_true")
parser.add_argument("--context", type=int, default=0)
parser.add_argument("--fail-if-found", action="store_true")
args = parser.parse_args()

try:
    rx = re.compile(args.pattern, re.I if args.ignore_case else 0)
except re.error as exc:
    sys.exit(f"Bad pattern: {exc}")

levels = Counter()
before, after, hits, last_printed = deque(maxlen=args.context), 0, 0, 0
try:
    with open(args.file, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, start=1):
            line = line.rstrip("\n")
            m = re.search(r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\b", line)
            if m:
                levels[m.group(1)] += 1
            if rx.search(line):
                hits += 1
                if last_printed and n - len(before) > last_printed + 1:
                    print("--")
                for k, b in enumerate(before):
                    print(f"{n - len(before) + k:>6}  {b}")
                before.clear()
                print(f"{n:>6}> {line}")
                last_printed, after = n, args.context
            elif after:
                print(f"{n:>6}  {line}")
                last_printed, after = n, after - 1
            else:
                before.append(line)
except OSError as exc:
    sys.exit(f"Cannot read {args.file}: {exc}")

print()
print(f"{hits} matching line(s). Levels: " + (", ".join(f"{k} {v}" for k, v in levels.most_common()) or "none"))
sys.exit(1 if args.fail_if_found and hits else 0)
