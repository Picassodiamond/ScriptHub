# --- scripthub ---
# {
#   "name": "Pip: Freeze Requirements",
#   "description": "List the exact installed package versions of a Python environment and optionally save them as a requirements file.",
#   "category": "Python packages",
#   "parameters": [
#     {"name": "python", "label": "Python interpreter", "type": "file", "placeholder": "/srv/app/venv/bin/python", "help": "The environment to read. Leave empty for the interpreter that runs this script."},
#     {"name": "output", "label": "Save to file", "type": "file", "placeholder": "/srv/app/requirements.lock.txt", "help": "Optional. Leave empty to only print the list."},
#     {"name": "include_editable", "label": "Include editable installs", "type": "boolean", "default": false}
#   ]
# }
# --- end scripthub ---
"""Write the exact package versions of a Python environment to a requirements file.

Use this on a working installation, then feed the file to
"Pip: Download Wheelhouse" to prepare an offline copy of the same environment.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="pip freeze into a requirements file.")
    parser.add_argument("--python", default="", help="Interpreter of the environment (default: this one)")
    parser.add_argument("--output", default="", help="File to write (default: print only)")
    parser.add_argument("--include-editable", action="store_true", help="Keep editable (-e) installs")
    args = parser.parse_args(argv)

    python = args.python or sys.executable
    cmd = [python, "-m", "pip", "freeze", "--disable-pip-version-check"]
    if not args.include_editable:
        cmd.append("--exclude-editable")
    try:
        done = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        print(f"Could not run {python}: {exc}", file=sys.stderr)
        return 2
    if done.returncode != 0:
        print(done.stderr.strip() or "pip freeze failed", file=sys.stderr)
        return done.returncode

    lines = [l for l in done.stdout.splitlines() if l.strip()]
    print(f"Interpreter: {python}")
    print(f"{len(lines)} package(s):")
    print("\n".join(lines))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
