"""Desktop mode: token gate, desktop-only endpoints, launcher helpers, tray icon file."""
import http.cookiejar
import os
import struct
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from runner import desktop, desktop_app, discovery, history
from runner.test_editor import WebCase

TOKEN = "test-token-123"


def desktop_on():
    return mock.patch.dict(os.environ, {desktop.ENV_TOKEN: TOKEN})


class DesktopCase(WebCase):
    def setUp(self):
        super().setUp()
        (self.root / "tool.py").write_bytes(b"print('hi')\n")
        self.sid = discovery.scan(force=True)[0][0].id

    def login(self):
        r = self.client.get("/?desktop_token=" + TOKEN)
        self.assertEqual(r.status_code, 302)


class TokenGateTests(DesktopCase):
    def test_server_mode_is_unchanged(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertNotContains(self.client.get("/"), 'data-desktop="1"')
        for url in ("/desktop/pick-folder/", "/desktop/reveal/", "/desktop/ping/", "/desktop/bye/"):
            self.assertEqual(self.client.post(url).status_code, 404, url)       # desktop-only endpoints do not exist

    def test_every_request_needs_the_launch_token(self):
        with desktop_on():
            self.assertEqual(self.client.get("/").status_code, 403)
            self.assertEqual(self.client.get("/scripts/").status_code, 403)
            self.assertEqual(self.client.get("/?desktop_token=wrong").status_code, 403)
            r = self.client.get(f"/scripts/?desktop_token={TOKEN}&x=1")
            self.assertEqual((r.status_code, r["Location"]), (302, "/scripts/?x=1"))      # token leaves the URL
            cookie = r.cookies[desktop.COOKIE]
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "Strict")
            page = self.client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, 'data-desktop="1"')
            self.assertContains(self.client.get(f"/scripts/{self.sid}/"), f'data-reveal-script="{self.sid}"')
            self.assertContains(self.client.get("/folder/"), 'data-pick-folder="#folder-path"')


class DesktopEndpointTests(DesktopCase):
    def test_folder_picker(self):
        with desktop_on():
            self.login()
            with mock.patch.object(desktop, "ask_directory", return_value="C:/picked/dir") as ask:
                r = self.client.post("/desktop/pick-folder/", {"initial": "C:/start", "title": "Pick"})
            self.assertEqual(r.json(), {"ok": True, "path": os.path.normpath("C:/picked/dir")})
            ask.assert_called_once_with("C:/start", "Pick")
            with mock.patch.object(desktop, "ask_directory", return_value=""):
                self.assertEqual(self.client.post("/desktop/pick-folder/").json()["path"], "")      # cancelled
            with mock.patch.object(desktop, "ask_directory", side_effect=ImportError("no tkinter")):
                r = self.client.post("/desktop/pick-folder/")
            self.assertEqual(r.status_code, 501)
            self.assertIn("no tkinter", r.json()["error"])
            self.assertEqual(self.client.get("/desktop/pick-folder/").status_code, 405)

    def test_show_in_folder_takes_ids_never_paths(self):
        with desktop_on():
            self.login()
            with mock.patch.object(desktop, "_reveal") as reveal:
                self.assertEqual(self.client.post("/desktop/reveal/", {"script": self.sid}).status_code, 200)
                self.assertEqual(Path(reveal.call_args[0][0]), self.root / "tool.py")
                reveal.reset_mock()
                self.assertEqual(self.client.post("/desktop/reveal/", {"script": "nope"}).status_code, 404)
                self.assertEqual(self.client.post("/desktop/reveal/", {"path": "C:/Windows"}).status_code, 404)
                self.assertEqual(self.client.post("/desktop/reveal/", {"run": "../../x"}).status_code, 404)
                reveal.assert_not_called()
                history.run_dir("RUN-20260101-001").mkdir(parents=True)
                self.assertEqual(self.client.post("/desktop/reveal/", {"run": "RUN-20260101-001"}).status_code, 200)
                self.assertEqual(Path(reveal.call_args[0][0]).parent.name, "RUN-20260101-001")

    def test_ping_and_bye_record_the_window(self):
        with desktop_on():
            self.login()
            desktop.WINDOW.update(last_seen=0.0, bye_at=0.0)
            self.assertEqual(self.client.post("/desktop/ping/").status_code, 200)
            self.assertGreater(desktop.WINDOW["last_seen"], 0)
            self.assertEqual(self.client.post("/desktop/bye/").status_code, 200)
            self.assertGreaterEqual(desktop.WINDOW["bye_at"], desktop.WINDOW["last_seen"])
        desktop.WINDOW.update(last_seen=0.0, bye_at=0.0)


class LauncherTests(WebCase):
    def tearDown(self):
        desktop.WINDOW.update(last_seen=0.0, bye_at=0.0)

    def test_window_presence_rules(self):
        app = desktop_app.DesktopApp()
        self.assertFalse(app.window_open())                         # never opened
        now = time.monotonic()
        app.window_opened_at = now
        self.assertTrue(app.window_open())                          # still loading
        desktop.WINDOW.update(last_seen=now + 1, bye_at=0.0)
        self.assertTrue(app.window_open())
        desktop.WINDOW.update(last_seen=now - desktop_app.HEARTBEAT_TIMEOUT - 1)
        app.window_opened_at = now - desktop_app.HEARTBEAT_TIMEOUT - 5
        self.assertFalse(app.window_open())                         # no ping for too long
        desktop.WINDOW.update(last_seen=now - 10, bye_at=now - desktop_app.BYE_GRACE - 1)
        self.assertFalse(app.window_open())                         # said goodbye, did not come back
        desktop.WINDOW.update(bye_at=now - 20)
        self.assertTrue(app.window_open())                          # goodbye then ping again = navigation

    def test_finished_runs_are_detected(self):
        runs = [{"id": "RUN-1", "status": "success"}, {"id": "RUN-2", "status": "running"},
                {"id": "RUN-3", "status": "failed"}]
        self.assertEqual([m["id"] for m in desktop_app.finished_runs({"RUN-1", "RUN-2"}, runs)], ["RUN-1"])

    def test_start_with_windows_writes_a_cmd_in_the_startup_folder(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.tmp)}):
            desktop_app.set_startup(True)
            target = desktop_app.startup_file()
            self.assertTrue(str(target).startswith(str(self.tmp)))
            text = target.read_text()
            self.assertIn("desktop.pyw", text)
            self.assertIn("--background", text)
            if os.name == "nt":
                self.assertTrue(desktop_app.startup_enabled())
            desktop_app.set_startup(False)
            self.assertFalse(target.exists())
            desktop_app.set_startup(False)                          # idempotent

    def test_find_browser_prefers_edge_then_chrome(self):
        edge = self.tmp / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        edge.parent.mkdir(parents=True)
        edge.write_bytes(b"")
        with mock.patch.dict(os.environ, {"PROGRAMFILES(X86)": str(self.tmp), "PROGRAMFILES": "", "LOCALAPPDATA": ""}):
            self.assertEqual(desktop_app.find_browser(), str(edge))

    def test_pid_alive(self):
        self.assertTrue(desktop_app.pid_alive(os.getpid()))
        self.assertFalse(desktop_app.pid_alive(0))
        self.assertFalse(desktop_app.pid_alive("x"))

    def test_in_process_server_requires_the_token(self):
        app = desktop_app.DesktopApp()
        state_file = self.tmp / "desktop.json"
        with mock.patch.object(desktop_app, "STATE_FILE", state_file), mock.patch.dict(os.environ):
            app.start_server()                              # sets the token in os.environ (restored afterwards)
            try:
                base = f"http://127.0.0.1:{app.port}"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + "/", timeout=10)
                self.assertEqual(ctx.exception.code, 403)
                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                with opener.open(app.url("/"), timeout=10) as resp:                    # follows the redirect
                    self.assertEqual(resp.status, 200)
                    self.assertIn(b'data-desktop="1"', resp.read())
                with opener.open(base + "/static/css/workbench.css", timeout=10) as resp:
                    self.assertEqual(resp.status, 200)                               # static files are served
                state = desktop_app.read_state()
                self.assertEqual((state["pid"], state["port"], state["token"]), (os.getpid(), app.port, app.token))
                self.assertTrue(desktop_app.instance_responds(state))               # a second launch would reuse it
            finally:
                app.shutdown()
            self.assertFalse(state_file.exists())                                   # cleaned up on exit


@unittest.skipUnless(os.name == "nt", "the tray icon is Windows only")
class TrayIconTests(unittest.TestCase):
    def test_generated_icon_is_a_valid_ico(self):
        from runner.desktop_tray import make_icon_bytes
        data = make_icon_bytes(32)
        self.assertEqual(struct.unpack("<HHH", data[:6]), (0, 1, 1))
        width, height, _c, _r, planes, bpp, size, offset = struct.unpack("<BBBBHHII", data[6:22])
        self.assertEqual((width, height, planes, bpp, offset), (32, 32, 1, 32, 22))
        self.assertEqual(len(data), 22 + size)
        self.assertEqual(size, 40 + 32 * 32 * 4 + 32 * 4)
