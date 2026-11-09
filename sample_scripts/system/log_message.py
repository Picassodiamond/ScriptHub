# --- scripthub ---
# {
#   "name": "Log a message",
#   "description": "Print a message and append it, with the time, to a log file. Use it at the end of a sequence: one after the success arrows, one after the failure arrows.",
#   "category": "System",
#   "parameters": [
#     {"name": "message", "label": "Message", "type": "text", "default": "All done.", "required": true, "max_length": 500},
#     {"name": "level", "label": "Level", "type": "select", "choices": ["info", "warning", "error"], "default": "info"},
#     {"name": "log_file", "label": "Log file", "type": "file", "help": "Empty = scripthub-messages.log in the temp folder"}
#   ]
# }
# --- end scripthub ---
"""Append '<time> <LEVEL> <message>' to a log file and print it."""
import argparse
import datetime
import os
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--message", default="All done.")
parser.add_argument("--level", choices=["info", "warning", "error"], default="info")
parser.add_argument("--log-file", default="")
args = parser.parse_args()

path = args.log_file or os.path.join(tempfile.gettempdir(), "scripthub-messages.log")
line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {args.level.upper():<7} {args.message}"
with open(path, "a", encoding="utf-8") as fh:
    fh.write(line + "\n")
print(line)
print(f"(appended to {path})")
