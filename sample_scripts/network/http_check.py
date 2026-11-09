# --- scripthub ---
# {
#   "name": "Check a web page",
#   "description": "Request a URL and fail when the status code or the page text is not what you expect.",
#   "category": "Network",
#   "parameters": [
#     {"name": "url", "label": "URL", "type": "text", "default": "https://example.com/", "required": true, "pattern": "https?://\\S+"},
#     {"name": "status", "label": "Expected status", "type": "number", "default": 200, "min": 100, "max": 599},
#     {"name": "contains", "label": "Page contains", "type": "text", "help": "Optional text that must be on the page"},
#     {"name": "timeout", "label": "Timeout (seconds)", "type": "number", "default": 10, "min": 1, "max": 120}
#   ]
# }
# --- end scripthub ---
"""GET a URL (redirects followed); exit 1 on a different status, missing text or no answer."""
import argparse
import sys
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True)
parser.add_argument("--status", type=int, default=200)
parser.add_argument("--contains", default="")
parser.add_argument("--timeout", type=int, default=10)
args = parser.parse_args()

request = urllib.request.Request(args.url, headers={"User-Agent": "ScriptHub-sample/1.0"})
start = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=args.timeout) as resp:
        code, body = resp.status, resp.read(2_000_000)
except urllib.error.HTTPError as exc:
    code, body = exc.code, exc.read(2_000_000)
except (urllib.error.URLError, OSError) as exc:
    sys.exit(f"NO ANSWER: {args.url} ({getattr(exc, 'reason', exc)})")

ms = (time.monotonic() - start) * 1000
print(f"{code} from {args.url} in {ms:.0f} ms, {len(body)} bytes")
problems = []
if code != args.status:
    problems.append(f"status {code}, expected {args.status}")
if args.contains and args.contains not in body.decode("utf-8", "replace"):
    problems.append(f"the page does not contain {args.contains!r}")
if problems:
    print("FAILED: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
print("OK")
