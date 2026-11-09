"""Desktop mode: the Django side.

The desktop launcher (runner/desktop_app.py) starts this app on 127.0.0.1 with a
random port and sets SCRIPTHUB_DESKTOP_TOKEN. Then:

* every request must carry the token (a cookie set from the launch URL), so
  other local users, other browser tabs and web pages cannot use the app;
* "Show in folder" (Explorer) is desktop-only (404 in server mode) and only takes
  a script id or run id, never a path.

The native folder picker (tkinter, part of Python; on Windows without tkinter, as in the
portable build, the Windows Forms dialogs through PowerShell) opens on the machine running
ScriptHub: it is available in desktop mode and, in server mode, to requests from
that same machine (loopback). Nothing else here is active in server mode.
"""
import hmac
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

ENV_TOKEN = "SCRIPTHUB_DESKTOP_TOKEN"
COOKIE = "sr_desktop"
_dialog_lock = threading.Lock()
# Window presence, read by the launcher: the page pings while its window is open
# and says goodbye when it goes away (navigation sends a goodbye and then a ping).
WINDOW = {"last_seen": 0.0, "bye_at": 0.0}


def token() -> str:
    return os.environ.get(ENV_TOKEN, "")


def enabled() -> bool:
    return bool(token())


class DesktopTokenMiddleware:
    """In desktop mode only the window opened by the launcher gets in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        secret = token()
        if not secret:
            return self.get_response(request)
        if hmac.compare_digest(request.COOKIES.get(COOKIE, ""), secret):
            return self.get_response(request)
        offered = request.GET.get("desktop_token", "")
        if offered and hmac.compare_digest(offered, secret):
            query = request.GET.copy()
            del query["desktop_token"]
            target = request.path + ("?" + query.urlencode() if query else "")
            resp = HttpResponseRedirect(target)
            resp.set_cookie(COOKIE, secret, httponly=True, samesite="Strict")
            return resp
        return HttpResponseForbidden(
            "ScriptHub is running in desktop mode. Open it from its window or tray icon.",
            content_type="text/plain")


def _require_desktop():
    if not enabled():
        raise Http404("Only available in desktop mode")


LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def can_pick_folder(request) -> bool:
    """The native folder dialog opens on the machine running ScriptHub, so it is offered
    in desktop mode and to a browser on that same machine, never to remote clients."""
    return enabled() or request.META.get("REMOTE_ADDR", "") in LOOPBACK


# ------------------------------------------------------------- folder picker --
def _tkinter():
    """tkinter, or None where Python has none (the portable Windows build's embedded Python)."""
    try:
        import tkinter
        from tkinter import filedialog  # noqa: F401
        return tkinter
    except ImportError:
        return None


# Windows dialogs through PowerShell (Windows Forms) when there is no tkinter. Inputs travel in
# environment variables, never inside the command text; the result comes back on stdout (UTF-8).
_PS_FOLDER = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:SH_TITLE
$d.SelectedPath = $env:SH_START
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }
"""
_PS_FILES = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = $env:SH_TITLE
$d.InitialDirectory = $env:SH_START
$d.Filter = $env:SH_FILTER
$d.Multiselect = $true
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write(($d.FileNames -join "`n")) }
"""


def _powershell_dialog(script: str, **env) -> str:
    import base64
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        capture_output=True, timeout=3600, env={**os.environ, **{f"SH_{k.upper()}": v for k, v in env.items()}},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or "PowerShell dialog failed")
    return done.stdout.decode("utf-8", "replace").strip()


def ask_directory(initial: str = "", title: str = "Choose a folder") -> str:
    """Native folder dialog (tkinter, or Windows Forms without it). Returns '' when cancelled."""
    start = initial if initial and Path(initial).is_dir() else str(Path.home())
    tkinter = _tkinter()
    if tkinter is None:
        if os.name != "nt":
            raise RuntimeError("tkinter is not installed")
        with _dialog_lock:
            return _powershell_dialog(_PS_FOLDER, title=title, start=start)
    from tkinter import filedialog
    with _dialog_lock:                       # one dialog at a time
        root = tkinter.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            return filedialog.askdirectory(parent=root, initialdir=start, title=title, mustexist=True) or ""
        finally:
            root.destroy()


def ask_file(initial: str = "", title: str = "Open scripts", extensions=()) -> list:
    """Native open-file dialog (tkinter, or Windows Forms without it) for one or more script
    files. Returns [] when cancelled."""
    start = initial if initial and Path(initial).is_dir() else (
        str(Path(initial).parent) if initial and Path(initial).is_file() else str(Path.home()))
    tkinter = _tkinter()
    if tkinter is None:
        if os.name != "nt":
            raise RuntimeError("tkinter is not installed")
        patterns = ";".join(f"*{e}" for e in sorted(extensions)) or "*.*"
        with _dialog_lock:
            out = _powershell_dialog(_PS_FILES, title=title, start=start,
                                     filter=f"Scripts ({patterns})|{patterns}|All files (*.*)|*.*")
        return [line for line in out.splitlines() if line.strip()]
    from tkinter import filedialog
    with _dialog_lock:
        root = tkinter.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            pattern = " ".join(f"*{e}" for e in sorted(extensions)) or "*"
            chosen = filedialog.askopenfilenames(parent=root, initialdir=start, title=title,
                                                 filetypes=[("Scripts", pattern), ("All files", "*")])
            return list(chosen or [])
        finally:
            root.destroy()


@require_POST
def pick_file(request):
    """Open file: the native dialog for one script (same rules as the folder picker)."""
    if not can_pick_folder(request):
        raise Http404("Only available on the machine running ScriptHub")
    from . import workspace
    try:
        chosen = ask_file(request.POST.get("initial", ""), request.POST.get("title", "") or "Open scripts",
                          workspace.script_extensions())
    except Exception as exc:                 # no tkinter, no display, ...
        return JsonResponse({"ok": False, "error": f"The file dialog is not available: {exc}"}, status=501)
    if isinstance(chosen, str):              # a single path (tests, older callers)
        chosen = [chosen] if chosen else []
    paths = [os.path.normpath(p) for p in chosen if p]
    return JsonResponse({"ok": True, "paths": paths, "path": paths[0] if paths else ""})


@require_POST
def pick_folder(request):
    if not can_pick_folder(request):
        raise Http404("Only available on the machine running ScriptHub")
    try:
        path = ask_directory(request.POST.get("initial", ""), request.POST.get("title", "") or "Choose a folder")
    except Exception as exc:                 # no tkinter, no display, ...
        return JsonResponse({"ok": False, "error": f"The folder dialog is not available: {exc}"}, status=501)
    return JsonResponse({"ok": True, "path": os.path.normpath(path) if path else ""})


# ------------------------------------------------------------ show in folder --
def _reveal(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


@require_POST
def reveal(request):
    """Show a script (by id) or a run folder (by run id) in the file manager."""
    _require_desktop()
    from . import discovery, history
    script_id, run_id = request.POST.get("script", ""), request.POST.get("run", "")
    if script_id:
        script = discovery.get_script(script_id)
        if script is None:
            raise Http404("Unknown script")
        target = Path(script.path)
    elif run_id and history.valid_run_id(run_id) and history.run_dir(run_id).is_dir():
        target = history.run_dir(run_id) / "metadata.json"
    else:
        raise Http404("Nothing to show")
    try:
        _reveal(target)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------- window presence --
@csrf_exempt            # harmless: only records a timestamp; the token gate still applies
@require_POST
def ping(request):
    _require_desktop()
    WINDOW["last_seen"] = time.monotonic()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def bye(request):
    _require_desktop()
    WINDOW["bye_at"] = time.monotonic()
    return JsonResponse({"ok": True})
