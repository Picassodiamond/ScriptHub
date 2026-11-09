# --- scripthub ---
# name: Succeed, fail or take a while
# description: Prints a line per second, then ends the way you choose. Use it to try the live console, Stop, Trace and Debug, and the success / failure arrows of a sequence.
# category: Basics
# parameters:
#   - name: outcome
#     label: Outcome
#     type: select
#     choices: [success, failure]
#     default: success
#   - name: seconds
#     label: Seconds
#     type: number
#     default: 3
#     min: 0
#     max: 600
#   - name: exit_code
#     label: Exit code on failure
#     type: number
#     default: 1
#     min: 1
#     max: 255
# --- end scripthub ---
"""Count for a while, then exit 0 or the chosen exit code."""
import argparse
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--outcome", choices=["success", "failure"], default="success")
parser.add_argument("--seconds", type=int, default=3)
parser.add_argument("--exit-code", type=int, default=1)
args = parser.parse_args()

for n in range(1, args.seconds + 1):
    print(f"step {n} of {args.seconds}", flush=True)
    time.sleep(1)

if args.outcome == "failure":
    print(f"Failing on purpose with exit code {args.exit_code}.", file=sys.stderr)
    sys.exit(args.exit_code)
print("Done.")
