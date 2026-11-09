# --- scripthub ---
# name: Check a TCP port
# description: Try to connect to host:port and fail when it is closed or does not answer in time.
# category: Network
# parameters:
#   - name: host
#     label: Host
#     type: text
#     default: github.com
#     required: true
#     pattern: "[A-Za-z0-9.:-]+"
#   - name: port
#     label: Port
#     type: number
#     default: 443
#     min: 1
#     max: 65535
#   - name: timeout
#     label: Timeout (seconds)
#     type: number
#     default: 5
#     min: 1
#     max: 120
# --- end scripthub ---
"""Exit 0 when a TCP connection to HOST:PORT succeeds, 1 otherwise."""
import argparse
import socket
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, default=443)
parser.add_argument("--timeout", type=int, default=5)
args = parser.parse_args()

start = time.monotonic()
try:
    with socket.create_connection((args.host, args.port), timeout=args.timeout):
        pass
except OSError as exc:
    print(f"CLOSED: {args.host}:{args.port} ({exc})", file=sys.stderr)
    sys.exit(1)
print(f"OPEN: {args.host}:{args.port} answered in {(time.monotonic() - start) * 1000:.0f} ms")
