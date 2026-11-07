"""Install packages from a local wheelhouse folder - with NO internet access.

Pairs with "Pip: Download Wheelhouse". Before installing it verifies the
SHA-256 manifest (if present), optionally creates a virtual environment, then
runs pip with --no-index so nothing can be fetched from the network, and
finishes with 'pip check'.
"""
import argparse
import hashlib
import subprocess
import sys
import venv
from pathlib import Path

MANIFEST = "wheelhouse-manifest.txt"


def venv_python(venv_dir: Path) -> Path:
    for candidate in (venv_dir / "bin" / "python", venv_dir / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return venv_dir / "bin" / "python"


def build_install_command(python, wheelhouse, requirements, dry_run=False) -> list:
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-index", "--find-links", str(wheelhouse), "-r", str(requirements)]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def verify_manifest(wheelhouse: Path):
    """Returns (checked_count, problems). No manifest -> (0, [])."""
    manifest = wheelhouse / MANIFEST
    if not manifest.is_file():
        return 0, []
    problems, checked = [], 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, _sep, name = line.partition("  ")
        path = wheelhouse / name.strip()
        if not path.is_file():
            problems.append(f"missing: {name.strip()}")
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        checked += 1
        if h.hexdigest() != digest.strip():
            problems.append(f"changed or corrupted: {name.strip()}")
    return checked, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install from a wheelhouse without internet.")
    parser.add_argument("--wheelhouse", required=True, help="Folder with the downloaded wheels")
    parser.add_argument("--requirements", required=True, help="requirements.txt to install")
    parser.add_argument("--venv", default="", help="Virtual environment to use (created if missing)")
    parser.add_argument("--dry-run", action="store_true", help="Only check that everything can be installed")
    parser.add_argument("--skip-verify", action="store_true", help="Do not verify the manifest checksums")
    args = parser.parse_args(argv)

    wheelhouse, req = Path(args.wheelhouse), Path(args.requirements)
    if not wheelhouse.is_dir():
        print(f"Wheelhouse folder not found: {wheelhouse}", file=sys.stderr)
        return 2
    if not req.is_file():
        print(f"Requirements file not found: {req}", file=sys.stderr)
        return 2

    print(f"Wheelhouse:   {wheelhouse.resolve()}")
    print(f"Requirements: {req}")
    if not args.skip_verify:
        checked, problems = verify_manifest(wheelhouse)
        if problems:
            print("Wheelhouse verification FAILED:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("Re-copy the wheelhouse, or use --skip-verify if you changed it on purpose.", file=sys.stderr)
            return 3
        print(f"Checksums:    {checked} file(s) verified" if checked else "Checksums:    no manifest found (skipped)")

    python = Path(sys.executable)
    if args.venv:
        venv_dir = Path(args.venv)
        if not venv_python(venv_dir).exists():
            print(f"Creating virtual environment: {venv_dir}", flush=True)
            venv.EnvBuilder(with_pip=True).create(str(venv_dir))
        python = venv_python(venv_dir)
        print(f"Virtual env:  {venv_dir.resolve()}")
    print(f"Python:       {python}")
    print("-" * 60, flush=True)

    code = subprocess.call(build_install_command(python, wheelhouse, req, args.dry_run))
    if code != 0:
        print(f"\npip install failed (exit {code}). A package is probably missing from the wheelhouse "
              "or built for another platform / Python version.", file=sys.stderr)
        return code
    if args.dry_run:
        print("-" * 60)
        print("Dry run OK: every requirement can be installed from this wheelhouse.")
        return 0
    print("-" * 60, flush=True)
    check = subprocess.call([str(python), "-m", "pip", "check", "--disable-pip-version-check"])
    print("Installed. 'pip check' found no broken requirements." if check == 0
          else "Installed, but 'pip check' reported problems (see above).")
    return 0 if check == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
