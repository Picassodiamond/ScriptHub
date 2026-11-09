# --- scripthub ---
# {
#   "name": "Parameter tour",
#   "description": "One parameter of every type, passed as flags, positionals and environment variables. Run it to see exactly what a script receives.",
#   "category": "Basics",
#   "parameters": [
#     {"name": "target", "label": "Target", "type": "text", "default": "demo", "required": true, "pass_as": "positional", "position": 1, "help": "Positional argument 1"},
#     {"name": "title", "label": "Title", "type": "text", "default": "Tour", "min_length": 2, "max_length": 40},
#     {"name": "notes", "label": "Notes", "type": "textarea", "default": "Line one\nLine two"},
#     {"name": "count", "label": "Count", "type": "number", "default": 3, "min": 0, "max": 100},
#     {"name": "ratio", "label": "Ratio", "type": "decimal", "default": "0.75", "min": 0, "max": 1},
#     {"name": "verbose", "label": "Verbose", "type": "boolean", "default": true},
#     {"name": "color", "label": "Color", "type": "select", "choices": ["red", "green", "blue"], "default": "green"},
#     {"name": "features", "label": "Features", "type": "multiselect", "choices": ["logs", "metrics", "traces"], "default": ["logs", "metrics"], "help": "Repeated flag: --features logs --features metrics"},
#     {"name": "since", "label": "Since", "type": "date", "default": "2026-01-01"},
#     {"name": "run_at", "label": "Run at", "type": "datetime", "default": "2026-01-01T09:30"},
#     {"name": "input_file", "label": "Input file", "type": "file", "placeholder": "C:\\path\\to\\file.txt"},
#     {"name": "output_dir", "label": "Output folder", "type": "directory", "placeholder": "C:\\path\\to\\folder"},
#     {"name": "mode", "label": "Mode", "type": "select", "choices": ["dev", "test", "prod"], "default": "dev", "pass_as": "env", "env_name": "TOUR_MODE"},
#     {"name": "api_token", "label": "API token", "type": "password", "pass_as": "env", "env_name": "TOUR_TOKEN", "help": "Never saved; masked in logs and history"},
#     {"name": "region", "label": "Region (fixed)", "type": "text", "default": "eu-west", "editable_at_runtime": false, "help": "Locked: always the default"}
#   ]
# }
# --- end scripthub ---
"""Print everything this script received, so you can see how each parameter type arrives."""
import argparse
import os
import sys

print("argv:", sys.argv[1:])
print()

parser = argparse.ArgumentParser()
parser.add_argument("target")
parser.add_argument("--title")
parser.add_argument("--notes")
parser.add_argument("--count", type=int)
parser.add_argument("--ratio", type=float)
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--color")
parser.add_argument("--features", action="append", default=[])
parser.add_argument("--since")
parser.add_argument("--run-at")
parser.add_argument("--input-file")
parser.add_argument("--output-dir")
parser.add_argument("--region")
args = parser.parse_args()

for key, value in vars(args).items():
    print(f"{key:<12} {value!r}")
print()
print(f"{'TOUR_MODE':<12} {os.environ.get('TOUR_MODE')!r}  (environment variable)")
token = os.environ.get("TOUR_TOKEN", "")
print(f"{'TOUR_TOKEN':<12} {'*' * len(token) if token else '(not set)'}  (environment variable, password)")
