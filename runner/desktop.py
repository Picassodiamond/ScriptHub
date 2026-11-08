"""Desktop mode: the Django side.

The desktop launcher (runner/desktop_app.py) starts this app on 127.0.0.1 with a
random port and sets SCRIPTHUB_DESKTOP_TOKEN. Then:

* every request must carry the token (a cookie set from the launch URL), so
  other local users, other browser tabs and web pages cannot use the app;
* two desktop-only endpoints exist: a native folder picker (tkinter, part of
  Python) and "Show in folder" (Explorer). They answer 404 in server mode, and
  "Show in folder" only takes a script id or run id, never a path.

Nothing here is active when the app runs as a normal web server.
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


# ------------------------------------------------------------- folder picker --
def ask_directory(initial: str = "", title: str = "Choose a folder") -> str:
    """Native folder dialog (tkinter). Returns '' when cancelled or unavailable."""
    import tkinter
    from tkinter import filedialog
    with _dialog_lock:                       # one dialog at a time
        root = tkinter.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            start = initial if initial and Path(initial).is_dir() else str(Path.home())
            return filedialog.askdirectory(parent=root, initialdir=start, title=title, mustexist=True) or ""
        finally:
            root.destroy()


@require_POST
def pick_folder(request):
    _require_desktop()
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
