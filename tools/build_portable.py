"""Build the portable Windows download: dist/ScriptHub-<version>-windows-portable.zip

    python tools/build_portable.py [--version 2.0] [--check]

The zip holds one folder, ScriptHub/:

    ScriptHub.exe   launcher (packaging/windows/ScriptHub.cs, compiled with the C# compiler of .NET
                    Framework 4, which every Windows 10/11 has) -> python\\pythonw.exe desktop.pyw
    python/         the official "embeddable" Python from python.org (pinned version and SHA-256)
                    with Django installed into Lib/site-packages; its ._pth file is removed so it
                    behaves like a normal Python (a script's own folder is on sys.path)
    runtime/        empty: its presence switches ScriptHub to portable mode (data stays here)
    the code        manage.py, desktop.pyw, config.py, runner/, ... (no tests)

--check starts the built app (with a throwaway data folder) and fetches a page, and runs a sample
script on the bundled Python. Windows only (the bundled Python and the launcher are Windows
programs). Nothing here is needed to run ScriptHub from source.
"""
import argparse
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "dist"
PY_VERSION = "3.12.10"
PY_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
CODE = ["manage.py", "desktop.pyw", "config.py", "requirements.txt", "LICENSE", "README.md", "SECURITY.md",
        "runner", "scripthub_project", "templates", "static", "sample_scripts", "sample_projects"]
PACKAGING = BASE / "packaging" / "windows"


def log(msg):
    print(msg, flush=True)


def app_version() -> str:
    text = (BASE / "runner" / "context.py").read_text(encoding="utf-8")
    return re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text).group(1)


def fetch_python() -> Path:
    cache = DIST / "cache" / Path(PY_URL).name
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        log(f"download {PY_URL}")
        with urllib.request.urlopen(PY_URL, timeout=300) as r:
            data = r.read()
        cache.write_bytes(data)
    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    if digest != PY_SHA256:
        cache.unlink()
        sys.exit(f"SHA-256 of {cache.name} is {digest}, expected {PY_SHA256}: not using it")
    return cache


def _ignore(folder, names):
    skip = {n for n in names if n in ("__pycache__", ".pytest_cache") or n.endswith((".pyc", ".pyo"))}
    if Path(folder).name == "runner":
        skip |= {n for n in names if n == "tests.py" or (n.startswith("test") and n.endswith(".py"))}
    return skip


def stage(version: str) -> Path:
    root = DIST / "ScriptHub"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    with zipfile.ZipFile(fetch_python()) as z:
        z.extractall(root / "python")
    for pth in (root / "python").glob("*._pth"):          # behave like a normal Python (see the docstring)
        pth.unlink()
    site = root / "python" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    log("install Django into the bundled Python")
    major_minor = ".".join(PY_VERSION.split(".")[:2])
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                    "--target", str(site), "--platform", "win_amd64", "--python-version", major_minor,
                    "--implementation", "cp", "--only-binary=:all:", "-r", str(BASE / "requirements.txt")],
                   check=True)
    for name in CODE:
        src = BASE / name
        if src.is_dir():
            shutil.copytree(src, root / name, ignore=_ignore)
        else:
            shutil.copy2(src, root / name)
    (root / "runtime").mkdir()
    readme = (PACKAGING / "README.txt").read_text(encoding="utf-8")
    (root / "README.txt").write_text(readme.format(version=version, python_version=PY_VERSION),
                                     encoding="utf-8", newline="\r\n")
    build_launcher(root / "ScriptHub.exe")
    return root


def csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        exe = windir / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if exe.is_file():
            return exe
    sys.exit("The C# compiler of .NET Framework 4 (csc.exe) was not found: build on Windows")


def build_launcher(target: Path) -> None:
    log("compile ScriptHub.exe")
    subprocess.run([str(csc()), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
                    f"/win32icon:{BASE / 'static' / 'img' / 'scripthub.ico'}", f"/out:{target}",
                    "/reference:System.Windows.Forms.dll", str(PACKAGING / "ScriptHub.cs")], check=True)


def check(root: Path) -> None:
    """The built app starts and answers; a sample script runs on the bundled Python."""
    python = root / "python" / "python.exe"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""}
    caches = set(root.rglob("__pycache__"))                  # pip precompiles Django: those stay
    hello = subprocess.run([str(python), str(root / "sample_scripts" / "basics" / "hello.py"), "--name", "portable"],
                           capture_output=True, text=True, env=env, timeout=60)
    assert hello.returncode == 0 and "Hello, portable!" in hello.stdout, hello.stdout + hello.stderr
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryDirectory() as home:
        server = subprocess.Popen([str(python), "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                                  cwd=root, env={**env, "SCRIPTHUB_HOME": home},
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                        assert r.status == 200 and b"ScriptHub" in r.read()
                    break
                except OSError:
                    if server.poll() is not None:
                        sys.exit("the bundled app exited:\n" + server.stdout.read().decode("utf-8", "replace"))
                    time.sleep(1)
            else:
                sys.exit("the bundled app did not answer")
        finally:
            server.terminate()
            server.wait(30)
    assert set(root.rglob("__pycache__")) == caches, "the check left __pycache__ folders"
    assert not any((root / "runtime").iterdir()), "the check wrote into runtime/"
    log("check: the app starts and a sample script runs on the bundled Python")


def make_zip(root: Path, version: str) -> Path:
    out = DIST / f"ScriptHub-{version}-windows-portable.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(root.rglob("*")):
            arc = Path("ScriptHub") / path.relative_to(root)
            if path.is_dir():
                if not any(path.iterdir()):              # keep empty folders (runtime/)
                    z.writestr(arc.as_posix() + "/", "")
            else:
                z.write(path, arc.as_posix())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", help="version in the file name (default: APP_VERSION; a leading v is dropped)")
    ap.add_argument("--check", action="store_true", help="start the built app once and run a sample script")
    args = ap.parse_args()
    if os.name != "nt":
        sys.exit("Build the Windows portable download on Windows.")
    version = (args.version or app_version()).lstrip("v")
    root = stage(version)
    if args.check:
        check(root)
    out = make_zip(root, version)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    log(f"{out.relative_to(BASE)}  {out.stat().st_size / 1e6:.1f} MB  sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
