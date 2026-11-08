# --- scripthub ---
# {
#   "name": "Pip: Download Wheelhouse",
#   "description": "Download a requirements file and all dependencies into a folder (a wheelhouse) for installing offline later. Can target another OS/Python, writes a SHA-256 manifest, and can zip it for transfer.",
#   "category": "Python packages",
#   "parameters": [
#     {"name": "requirements", "label": "Requirements file", "type": "file", "required": true, "placeholder": "/srv/app/requirements.txt", "help": "Path to requirements.txt on the machine that runs this script."},
#     {"name": "dest", "label": "Wheelhouse folder", "type": "directory", "required": true, "default": "./wheelhouse", "help": "Created if missing."},
#     {"name": "platform", "label": "Target platform", "type": "select", "choices": [{"value": "manylinux2014_x86_64", "label": "Linux x86-64"}, {"value": "manylinux2014_aarch64", "label": "Linux ARM64"}, {"value": "win_amd64", "label": "Windows 64-bit"}, {"value": "macosx_11_0_arm64", "label": "macOS Apple Silicon"}, {"value": "macosx_10_9_x86_64", "label": "macOS Intel"}], "help": "Leave “none” to download for this machine. Pick the OS of the offline machine if it differs; only binary wheels can be fetched for another platform."},
#     {"name": "python_version", "label": "Target Python version", "type": "text", "placeholder": "3.11", "pattern": "\\d+(\\.\\d+){0,2}", "help": "Python on the offline machine, e.g. 3.11. Leave empty to use this interpreter's version."},
#     {"name": "index_url", "label": "Package index URL", "type": "text", "placeholder": "https://pypi.org/simple", "pattern": "https?://\\S+", "help": "Optional: an internal mirror."},
#     {"name": "only_binary", "label": "Binary wheels only", "type": "boolean", "default": false, "help": "Refuse source packages (they would need compilers offline)."},
#     {"name": "bundle", "label": "Also create a .zip for transfer", "type": "boolean", "default": true}
#   ]
# }
# --- end scripthub ---
"""Download every package in a requirements file (with its dependencies) into a
"wheelhouse" folder so it can be installed later on a machine WITHOUT internet.

Run this where internet is available. To prepare for a different target (for
example a Linux server while you are on Windows) choose the target platform and
Python version; pip then fetches matching binary wheels only.

Writes wheelhouse-manifest.txt (SHA-256 of every file) which
pip_install_offline.py verifies before installing, and can zip the folder for
transfer.
"""
import argparse
import datetime
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

MANIFEST = "wheelhouse-manifest.txt"
TARGET_PLATFORMS = (
    "manylinux2014_x86_64", "manylinux2014_aarch64", "win_amd64", "macosx_11_0_arm64", "macosx_10_9_x86_64",
)


def build_command(args) -> list:
    """The pip command line (a list; never a shell string)."""
    cmd = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
           "-r", str(args.requirements), "-d", str(args.dest)]
    if args.platform:
        cmd += ["--platform", args.platform]
    if args.python_version:
        cmd += ["--python-version", args.python_version]
    # pip only allows a foreign platform / Python version together with binary wheels.
    if args.platform or args.python_version or args.only_binary:
        cmd += ["--only-binary=:all:"]
    if args.index_url:
        cmd += ["--index-url", args.index_url]
    return cmd


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(dest: Path, args) -> int:
    files = sorted(p for p in dest.iterdir() if p.is_file() and p.name != MANIFEST)
    lines = [
        f"# created {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"# python {sys.version.split()[0]} on {sys.platform}; target platform: {args.platform or 'this machine'};"
        f" target python: {args.python_version or 'this interpreter'}",
        f"# requirements: {args.requirements}",
    ]
    lines += [f"{sha256(p)}  {p.name}" for p in files]
    (dest / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files)


def make_bundle(dest: Path) -> Path:
    bundle = dest.with_suffix(".zip") if dest.suffix == "" else dest.parent / (dest.name + ".zip")
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as zf:  # wheels are already compressed
        for p in sorted(dest.iterdir()):
            if p.is_file():
                zf.write(p, f"{dest.name}/{p.name}")
    return bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Download packages into a wheelhouse for offline installation.")
    parser.add_argument("--requirements", required=True, help="requirements.txt to download")
    parser.add_argument("--dest", required=True, help="Folder to put the wheelhouse in")
    parser.add_argument("--platform", default="", choices=("",) + TARGET_PLATFORMS,
                        help="Target platform when it differs from this machine")
    parser.add_argument("--python-version", default="", help="Target Python version, e.g. 3.11")
    parser.add_argument("--only-binary", action="store_true", help="Refuse source distributions")
    parser.add_argument("--index-url", default="", help="Alternative package index")
    parser.add_argument("--bundle", action="store_true", help="Also create a .zip for transfer")
    args = parser.parse_args(argv)

    req = Path(args.requirements)
    if not req.is_file():
        print(f"Requirements file not found: {req}", file=sys.stderr)
        return 2
    if args.python_version and not all(part.isdigit() for part in args.python_version.split(".")):
        print("The Python version must look like 3.11 or 3.11.4", file=sys.stderr)
        return 2
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Requirements: {req}")
    print(f"Destination:  {dest.resolve()}")
    print(f"Target:       {args.platform or 'this machine'}, Python {args.python_version or sys.version.split()[0]}")
    print("-" * 60, flush=True)
    code = subprocess.call(build_command(args))
    if code != 0:
        print(f"\npip download failed (exit {code}). Nothing was installed.", file=sys.stderr)
        if args.platform and code != 0:
            print("Tip: with a target platform only binary wheels can be fetched; a package that "
                  "ships only source cannot be prepared this way.", file=sys.stderr)
        return code

    count = write_manifest(dest, args)
    size = sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
    print("-" * 60)
    print(f"Wheelhouse ready: {count} file(s), {size / 1048576:.1f} MB")
    print(f"Manifest:         {dest / MANIFEST}")
    if args.bundle:
        bundle = make_bundle(dest)
        print(f"Bundle:           {bundle} ({bundle.stat().st_size / 1048576:.1f} MB)")
    print("Copy the folder (or the zip) to the offline machine, then run 'Offline install'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
