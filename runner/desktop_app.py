"""ScriptHub as a desktop application, built only from what is already
installed: Python (standard library), Django and the system browser.

    pythonw desktop.pyw            (double-click; no console window)
    python manage.py desktop       (same, with a console)
    ... --background               (tray only, no window; used by "Start with Windows")

What it does:
* runs the Django app inside this process on 127.0.0.1 with a random port and a
  per-launch secret (runner/desktop.py rejects every request without it);
* opens the UI in its own window: Edge or Chrome in "app" mode (no tabs or
  address bar), with a separate profile under runtime/; any browser as a fallback;
* Windows: a tray icon (running count in the tooltip, Open, Start with Windows,
  Quit) and a notification when a run finishes; clicking it opens that run;
* closing the window while runs are active keeps the app in the tray until they
  finish; with nothing running the app ends with its window;
* a second launch just opens another window on the running instance.

Like `runserver`, runs live inside this process: quitting stops them (the tray
asks first).
"""
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import config

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME = config.RUNTIME_DIR
STATE_FILE = RUNTIME / "desktop.json"
PROFILE_DIR = RUNTIME / "desktop-profile"
LOG_FILE = RUNTIME / "desktop.log"
ICON_FILE = RUNTIME / "desktop.ico"
STARTUP_NAME = "ScriptHub.cmd"
HEARTBEAT_TIMEOUT = 90       # seconds without a ping before the window counts as closed (minimised pages ping slowly)
BYE_GRACE = 6                # seconds after a goodbye without a new ping (navigation pings again at once)
FIRST_PING_GRACE = 45        # the window has this long to load before it counts as closed

log = logging.getLogger("runner.desktop")


# ----------------------------------------------------------------- helpers --
def find_browser():
    """Edge or Chrome (they support --app windows), else None."""
    names = [("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe")]
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        for parts in names if root else ():
            path = Path(root, *parts)
            if path.is_file():
                return str(path)
    for cmd in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def pid_alive(pid) -> bool:
    """Is that process still running? (Standalone: used before Django is set up.)"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def instance_url(state, path="/"):
    return f"http://127.0.0.1:{state['port']}{path}"


def instance_responds(state) -> bool:
    """Is a ScriptHub with this token listening there? (It answers the token URL with a redirect.)"""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(state["port"]), timeout=3)
        try:
            conn.request("GET", "/?desktop_token=" + urllib.parse.quote(str(state["token"])))
            resp = conn.getresponse()
            return resp.status == 302 and "sr_desktop" in (resp.getheader("Set-Cookie") or "")
        finally:
            conn.close()
    except Exception:
        return False


def startup_file() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME


def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.is_file() else exe)


def startup_enabled() -> bool:
    return os.name == "nt" and startup_file().is_file()


def set_startup(on: bool) -> None:
    """Start with Windows: a small .cmd in the user's Startup folder (no installer, no registry)."""
    target = startup_file()
    if on:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'@echo off\r\nstart "" "{pythonw()}" "{BASE_DIR / "desktop.pyw"}" --background\r\n', encoding="utf-8")
    else:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def finished_runs(previous_active: set, runs: list):
    """Runs that were active at the last check and have finished now: [(meta), ...]."""
    from . import history
    return [m for m in runs if m.get("id") in previous_active and m.get("status") in history.FINAL_STATUSES]


# ---------------------------------------------------------------- the app --
class DesktopApp:
    def __init__(self, background=False):
        self.background = background
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        self.port = None
        self.tray = None
        self.browser = find_browser()
        self.window_proc = None
        self.window_opened_at = None
        self.window_seen = False
        self.stopping = threading.Event()
        self.kept_alive_notice = False

    # server
    def start_server(self):
        os.environ["SCRIPTHUB_DESKTOP_TOKEN"] = self.token
        os.environ.setdefault("SCRIPTHUB_DEBUG", "0")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scripthub_project.settings")
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.servers.basehttp import ThreadedWSGIServer, WSGIRequestHandler
        from django.core.wsgi import get_wsgi_application

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, format, *args):           # only errors go to the log
                if len(args) > 1 and str(args[1])[:1] in ("4", "5"):
                    super().log_message(format, *args)

        app = StaticFilesHandler(get_wsgi_application())
        self.httpd = ThreadedWSGIServer(("127.0.0.1", 0), QuietHandler, ipv6=False)
        self.httpd.set_app(app)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="desktop-http", daemon=True).start()
        RUNTIME.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "port": self.port, "token": self.token}), encoding="utf-8")
        log.info("Serving on 127.0.0.1:%s", self.port)

    def url(self, path="/"):
        sep = "&" if "?" in path else "?"
        return f"http://127.0.0.1:{self.port}{path}{sep}desktop_token={self.token}"

    # window
    def open_window(self, path="/"):
        url = self.url(path)
        if self.browser:
            args = [self.browser, f"--app={url}", f"--user-data-dir={PROFILE_DIR}", "--window-size=1440,900",
                    "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self.window_proc is None or self.window_proc.poll() is not None:
                self.window_proc = proc
        else:
            webbrowser.open(url)
        self.window_opened_at = time.monotonic()

    def window_open(self) -> bool:
        from . import desktop
        now = time.monotonic()
        seen, bye = desktop.WINDOW["last_seen"], desktop.WINDOW["bye_at"]
        if seen:
            self.window_seen = True
        if self.window_opened_at is None:
            return False
        if not seen or seen < self.window_opened_at:
            return now - self.window_opened_at < FIRST_PING_GRACE          # still loading
        if bye and bye >= seen and now - bye > BYE_GRACE:
            return False                                                    # said goodbye and did not come back
        return now - seen < HEARTBEAT_TIMEOUT

    # tray actions
    def on_open(self):
        self.open_window("/")

    def on_quit(self):
        from .process_manager import manager
        counts = manager.counts()
        active = counts["running"] + counts["queued"]
        if active and self.tray and not self.tray.confirm(
                f"{active} execution(s) still running or queued.\n\nQuitting stops them. Quit ScriptHub?"):
            return
        self.quit()

    def quit(self):
        from . import history
        from .process_manager import manager
        for meta in history.list_runs(statuses=history.ACTIVE_STATUSES):
            manager.cancel(meta["id"])
        manager.wait_idle(20)
        self.stopping.set()
        if self.tray:
            self.tray.stop()

    # background watch: tooltip, notifications, closing with the window
    def watch(self):
        from . import history
        from .process_manager import manager
        active = {m["id"] for m in history.list_runs(statuses=history.ACTIVE_STATUSES)}
        while not self.stopping.wait(2.0):
            try:
                counts = manager.counts()
                busy = counts["running"] + counts["queued"]
                if self.tray:
                    self.tray.set_tooltip(f"ScriptHub — {counts['running']} running, {counts['queued']} queued"
                                          if busy else "ScriptHub — idle")
                runs = history.list_runs(limit=30)
                for meta in finished_runs(active, runs):
                    self.notify_finished(meta)
                active = {m["id"] for m in runs if m.get("status") in history.ACTIVE_STATUSES}
                if self.background:
                    continue                                    # started with Windows: lives in the tray until Quit
                if not self.window_open():
                    if busy:
                        if not self.kept_alive_notice and self.tray:
                            self.tray.notify("ScriptHub is still running",
                                             f"{busy} execution(s) in progress. It stays in the tray until they finish.")
                            self.kept_alive_notice = True
                        continue
                    log.info("Window closed and nothing running: exiting")
                    self.stopping.set()
                    if self.tray:
                        self.tray.stop()
                else:
                    self.kept_alive_notice = False
            except Exception:
                log.exception("Desktop watch failed")

    def notify_finished(self, meta):
        if not self.tray:
            return
        from . import history
        status = meta.get("status")
        label = history.STATUS_LABELS.get(status, status)
        took = history.format_duration(meta.get("duration_seconds"))
        text = f"{label} in {took}" + (f" (exit {meta['exit_code']})" if meta.get("exit_code") not in (None, 0) else "")
        level = "info" if status == history.SUCCESS else ("warning" if status == history.CANCELLED else "error")
        run_id = meta["id"]
        self.tray.notify(f"{meta.get('script_name', 'Script')}", f"{text}\n{run_id}", level,
                         on_click=lambda: self.open_window(f"/executions/{run_id}/"))

    # main
    def run(self):
        self.start_server()
        if not self.background:
            self.open_window("/")
        watcher = threading.Thread(target=self.watch, name="desktop-watch", daemon=True)
        watcher.start()
        if os.name == "nt":
            from .desktop_tray import Tray
            self.tray = Tray("ScriptHub", ICON_FILE, self.on_open, self.on_quit, startup_enabled, set_startup)
            self.tray.run()                      # message loop until stop()
        else:
            print(f"ScriptHub is running at {self.url('/')}  (Ctrl+C to quit)")
            try:
                while not self.stopping.wait(1.0):
                    pass
            except KeyboardInterrupt:
                self.quit()
        self.shutdown()

    def shutdown(self):
        self.stopping.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        state = read_state()
        if state and state.get("pid") == os.getpid():
            try:
                STATE_FILE.unlink()
            except OSError:
                pass


def _setup_logging():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:            # pythonw: no console
        stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stderr)])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    background = "--background" in argv
    sys.path.insert(0, str(BASE_DIR))
    os.chdir(BASE_DIR)
    _setup_logging()
    state = read_state()
    if state and pid_alive(state.get("pid")) and instance_responds(state):
        if not background:                       # already running: just open another window
            app = DesktopApp()
            app.port, app.token = state["port"], state["token"]
            app.open_window("/")
        return 0
    DesktopApp(background=background).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
