"""Script editing (service and web).

python manage.py test runner.test_editor
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import unittest
from django.test import SimpleTestCase

import config
from runner import discovery, history, script_editor
from runner.process_manager import manager
from runner.script_editor import ConflictError, EditError

HAS_BASH = shutil.which("bash") is not None


class TempCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-edit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "scripts"
        self.root.mkdir()


# ------------------------------------------------------------ editing service
class EditorServiceTests(TempCase):
    def make(self, name, data):
        p = self.root / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def test_read_normalises_newlines_and_reports_style(self):
        p = self.make("a.py", b"x = 1\r\ny = 2\r\n")
        info = script_editor.read_for_edit(p, self.root)
        self.assertEqual(info["text"], "x = 1\ny = 2\n")
        self.assertEqual(info["newline"], "crlf")
        self.assertEqual(script_editor.read_for_edit(self.make("b.py", b"x\ny\n"), self.root)["newline"], "lf")

    def test_unsuitable_files_are_refused(self):
        with self.assertRaises(EditError):
            script_editor.read_for_edit(self.make("bin.py", b"abc\x00def"), self.root)
        with self.assertRaises(EditError) as cm:
            script_editor.read_for_edit(self.make("latin.py", "caf\xe9".encode("latin-1")), self.root)
        self.assertIn("UTF-8", str(cm.exception))
        with self.assertRaises(EditError) as cm:
            script_editor.read_for_edit(self.make("big.py", b"#" * (script_editor.MAX_BYTES + 1)), self.root)
        self.assertIn("512 KB", str(cm.exception))
        with self.assertRaises(EditError):
            script_editor.read_for_edit(self.root / "missing.py", self.root)

    def test_files_outside_the_root_are_never_touched(self):
        outside = self.tmp / "outside.py"
        outside.write_text("print(1)")
        with self.assertRaises(EditError) as cm:
            script_editor.read_for_edit(outside, self.root)
        self.assertIn("outside", str(cm.exception))
        with self.assertRaises(EditError):
            script_editor.save_script(outside, self.root, "print(2)", "x", force=True)
        sneaky = self.root / ".." / "outside.py"
        with self.assertRaises(EditError):
            script_editor.save_script(sneaky, self.root, "print(2)", "x", force=True)
        self.assertEqual(outside.read_text(), "print(1)")

    def test_save_writes_file_and_keeps_a_backup(self):
        p = self.make("a.py", "print('old')\n")
        info = script_editor.read_for_edit(p, self.root)
        result = script_editor.save_script(p, self.root, "print('new')\n", info["hash"])
        self.assertTrue(result["changed"])
        self.assertEqual(p.read_text(), "print('new')\n")
        self.assertEqual(script_editor.backup_path(p).read_text(), "print('old')\n")
        self.assertEqual([x.name for x in self.root.iterdir() if x.suffix == ".tmp"], [])   # no temp files left

    def test_unchanged_save_does_not_touch_the_backup(self):
        p = self.make("a.py", "print(1)\n")
        script_editor.save_script(p, self.root, "print(2)\n", script_editor.read_for_edit(p, self.root)["hash"])
        before = script_editor.backup_path(p).read_text()
        info = script_editor.read_for_edit(p, self.root)
        result = script_editor.save_script(p, self.root, info["text"], info["hash"])
        self.assertFalse(result["changed"])
        self.assertEqual(script_editor.backup_path(p).read_text(), before)

    def test_conflict_when_the_file_changed_after_opening(self):
        p = self.make("a.py", "v1\n")
        stale = script_editor.read_for_edit(p, self.root)["hash"]
        p.write_text("someone else edited this\n")
        with self.assertRaises(ConflictError):
            script_editor.save_script(p, self.root, "mine\n", stale)
        self.assertEqual(p.read_text(), "someone else edited this\n")
        script_editor.save_script(p, self.root, "mine\n", stale, force=True)       # explicit overwrite
        self.assertEqual(p.read_text(), "mine\n")
        self.assertEqual(script_editor.backup_path(p).read_text(), "someone else edited this\n")

    def test_line_ending_policy(self):
        browser_text = "line1\r\nline2\r\n"                                        # what a browser posts
        sh = self.make("run.sh", "echo a\n")
        script_editor.save_script(sh, self.root, browser_text, script_editor.read_for_edit(sh, self.root)["hash"])
        self.assertNotIn(b"\r", sh.read_bytes())                                   # shell scripts must stay LF
        bat = self.make("run.bat", "@echo off\r\n")
        script_editor.save_script(bat, self.root, "@echo off\necho hi\n", script_editor.read_for_edit(bat, self.root)["hash"])
        self.assertEqual(bat.read_bytes(), b"@echo off\r\necho hi\r\n")           # batch files must be CRLF
        py_crlf = self.make("w.py", b"a = 1\r\n")
        script_editor.save_script(py_crlf, self.root, "a = 2\nb = 3\n", script_editor.read_for_edit(py_crlf, self.root)["hash"])
        self.assertEqual(py_crlf.read_bytes(), b"a = 2\r\nb = 3\r\n")             # other files keep their style
        py_lf = self.make("l.py", b"a = 1\n")
        script_editor.save_script(py_lf, self.root, "a = 2\r\n", script_editor.read_for_edit(py_lf, self.root)["hash"])
        self.assertEqual(py_lf.read_bytes(), b"a = 2\n")

    def test_utf8_and_bom_are_preserved(self):
        p = self.make("u.py", b"\xef\xbb\xbfprint('caf\xc3\xa9')\n")
        info = script_editor.read_for_edit(p, self.root)
        self.assertTrue(info["bom"])
        self.assertIn("café", info["text"])
        script_editor.save_script(p, self.root, "print('naïve ✓')\n", info["hash"])
        raw = p.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw[3:].decode("utf-8"), "print('naïve ✓')\n")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_executable_bit_is_kept(self):
        p = self.make("tool.sh", "echo a\n")
        p.chmod(0o755)
        script_editor.save_script(p, self.root, "echo b\n", script_editor.read_for_edit(p, self.root)["hash"])
        self.assertTrue(p.stat().st_mode & stat.S_IXUSR)

    def test_read_only_file_is_not_overwritten(self):
        p = self.make("ro.py", "print(1)\n")
        h = script_editor.read_for_edit(p, self.root)["hash"]
        p.chmod(stat.S_IREAD)
        self.addCleanup(p.chmod, stat.S_IREAD | stat.S_IWRITE)
        with self.assertRaises(EditError) as cm:
            script_editor.save_script(p, self.root, "print(2)\n", h)
        self.assertIn("read-only", str(cm.exception))
        self.assertEqual(p.read_text(), "print(1)\n")

    def test_bad_content_is_rejected(self):
        p = self.make("a.py", "x\n")
        h = script_editor.read_for_edit(p, self.root)["hash"]
        with self.assertRaises(EditError):
            script_editor.save_script(p, self.root, "a\x00b", h)
        with self.assertRaises(EditError):
            script_editor.save_script(p, self.root, "#" * (script_editor.MAX_BYTES + 10), h)
        self.assertEqual(p.read_text(), "x\n")

    def test_syntax_warnings_do_not_block_saving_and_never_execute(self):
        marker = self.tmp / "executed.txt"
        p = self.make("a.py", "x = 1\n")
        h = script_editor.read_for_edit(p, self.root)["hash"]
        result = script_editor.save_script(p, self.root, f"open({str(marker)!r}, 'w').write('x')\nif True print(1)\n", h)
        self.assertTrue(result["changed"])                                          # saved anyway
        self.assertIn("Python syntax error at line 2", result["warning"])
        self.assertFalse(marker.exists())                                           # compile() only, nothing ran
        ok = script_editor.save_script(p, self.root, "print(1)\n", script_editor.read_for_edit(p, self.root)["hash"])
        self.assertIsNone(ok["warning"])

    @unittest.skipUnless(HAS_BASH, "bash needed")
    def test_bash_syntax_warning(self):
        p = self.make("a.sh", "echo ok\n")
        h = script_editor.read_for_edit(p, self.root)["hash"]
        result = script_editor.save_script(p, self.root, 'if true; then\necho "unterminated\n', h)
        self.assertIn("Bash reports a syntax problem", result["warning"])

    def test_restore_swaps_with_the_backup(self):
        p = self.make("a.py", "v1\n")
        script_editor.save_script(p, self.root, "v2\n", script_editor.read_for_edit(p, self.root)["hash"])
        script_editor.restore_backup(p, self.root)
        self.assertEqual(p.read_text(), "v1\n")
        script_editor.restore_backup(p, self.root)                                  # restoring is itself undoable
        self.assertEqual(p.read_text(), "v2\n")
        with self.assertRaises(EditError):
            script_editor.restore_backup(self.make("fresh.py", "x\n"), self.root)


# --------------------------------------------------------------- web: editing
class WebCase(TempCase):
    def setUp(self):
        super().setUp()
        for sub in ("executions", "presets"):
            (self.tmp / sub).mkdir()
        self.cfg_file = self.tmp / "config.json"
        self.write_config()
        for p in (mock.patch.object(config, "CONFIG_FILE", self.cfg_file),
                  mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets")):
            p.start()
            self.addCleanup(p.stop)
        self.client = self.client_class(HTTP_HOST="localhost")
        self.addCleanup(self._cleanup)

    def write_config(self, **over):
        cfg = {"script_directories": [str(self.root)],
               "interpreters": {".py": "python", ".sh": "bash", ".bat": "cmd"},
               "default_timeout": 60, "max_concurrent_executions": 3}
        cfg.update(over)
        self.cfg_file.write_text(json.dumps(cfg))

    def _cleanup(self):
        for rid in list(manager._active):
            manager.cancel(rid)
        manager.wait_idle(20)
        discovery.scan(force=True)

    def wait_final(self, run_id, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            meta = history.read_meta(run_id)
            if meta and meta["status"] in history.FINAL_STATUSES:
                return meta
            time.sleep(0.1)
        self.fail("run did not finish")

    @staticmethod
    def run_id_of(response):
        return response["Location"].rstrip("/").split("/")[-1]

    _n = 0

    def start_script(self, code, ext=".py", mode="normal", **post):
        """Write ``code`` as a new script in the scanned folder and start it like the Run
        form does (Review -> Run now). Returns the response of the start request."""
        WebCase._n += 1
        path = self.root / f"t{WebCase._n}{ext}"
        path.write_bytes(code.encode("utf-8"))            # bytes: no newline conversion
        (self.root / f"t{WebCase._n}.json").write_text(json.dumps({"name": f"T{WebCase._n}", "parameters": []}))
        sid = discovery.script_id(path.resolve())
        discovery.scan(force=True)
        form = {"host": "local", "mode": mode, "submit_token": f"tok-{time.monotonic_ns()}", **post}
        return self.client.post(f"/scripts/{sid}/start/", form)


class WebEditTests(WebCase):
    def setUp(self):
        super().setUp()
        self.script = self.root / "tool.py"
        self.script.write_bytes(b"print('v1')\n")         # bytes: write_text would use CRLF on Windows
        (self.root / "tool.json").write_text(json.dumps({"name": "Tool", "parameters": []}))
        self.sid = discovery.scan(force=True)[0][0].id

    def url(self, tail=""):
        return f"/scripts/{self.sid}/edit/{tail}"

    def hash_on_page(self):
        page = self.client.get(self.url()).content.decode()
        return page.split('name="base_hash" value="')[1].split('"')[0]

    def test_edit_page_shows_the_source_and_editor(self):
        r = self.client.get(self.url())
        self.assertContains(r, "print(&#x27;v1&#x27;)")
        self.assertContains(r, 'class="editor"')
        self.assertContains(r, 'data-lang="python"')
        self.assertContains(r, "line endings LF")

    def test_save_updates_the_file_and_creates_a_backup(self):
        r = self.client.post(self.url(), {"content": "print('v2')\r\n", "base_hash": self.hash_on_page(), "action": "save"}, follow=True)
        self.assertContains(r, "Saved tool.py")
        self.assertEqual(self.script.read_text(), "print('v2')\n")
        self.assertEqual((self.root / "tool.py.bak").read_text(), "print('v1')\n")
        self.assertContains(r, "Restore previous")

    def test_syntax_error_is_a_warning_not_a_failure(self):
        r = self.client.post(self.url(), {"content": "def (:\n", "base_hash": self.hash_on_page()}, follow=True)
        self.assertContains(r, "Python syntax error")
        self.assertEqual(self.script.read_text(), "def (:\n")

    def test_conflict_keeps_my_text_and_offers_overwrite(self):
        stale = self.hash_on_page()
        self.script.write_text("print('changed elsewhere')\n")
        r = self.client.post(self.url(), {"content": "print('mine')\n", "base_hash": stale})
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "changed", status_code=409)
        self.assertContains(r, "print(&#x27;mine&#x27;)", status_code=409)          # my text is still in the editor
        self.assertContains(r, "Overwrite the file with my version", status_code=409)
        self.assertEqual(self.script.read_text(), "print('changed elsewhere')\n")
        r = self.client.post(self.url(), {"content": "print('mine')\n", "base_hash": stale, "force": "1"}, follow=True)
        self.assertEqual(self.script.read_text(), "print('mine')\n")

    def test_save_and_run_redirects_to_the_run_form(self):
        r = self.client.post(self.url(), {"content": "print('x')\n", "base_hash": self.hash_on_page(), "action": "save_run"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/scripts/{self.sid}/run/")

    def test_script_tab_merges_run_form_and_editor(self):
        page = self.client.get(f"/scripts/{self.sid}/run/")
        self.assertContains(page, 'id="edit-form"')                      # editable script in the body
        self.assertContains(page, 'id="run-form"')                       # run configuration on the right
        self.assertContains(page, "print(&#x27;v1&#x27;)")
        old_hash = page.content.decode().split('name="base_hash" value="')[1].split('"')[0]
        r = self.client.post(self.url(), {"content": "print('v3')\n", "base_hash": old_hash, "action": "save"}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], f"/scripts/{self.sid}/run/")   # saving lands back on the Script tab
        new_hash = r.content.decode().split('name="base_hash" value="')[1].split('"')[0]
        self.assertNotEqual(new_hash, old_hash)                           # the page carries the new hash for the next save
        self.write_config(enable_script_editing=False)
        page = self.client.get(f"/scripts/{self.sid}/run/")
        self.assertNotContains(page, 'id="edit-form"')                   # read-only when editing is off
        self.assertContains(page, 'id="doc-src"')

    def test_restore_view(self):
        self.client.post(self.url(), {"content": "print('v2')\n", "base_hash": self.hash_on_page()})
        r = self.client.post(self.url("restore/"), follow=True)
        self.assertContains(r, "Restored the previous version")
        self.assertEqual(self.script.read_text(), "print('v1')\n")
        self.assertEqual(self.client.get(self.url("restore/")).status_code, 405)

    def test_unknown_script_and_no_way_to_name_a_path(self):
        self.assertEqual(self.client.get("/scripts/doesnotexist/edit/").status_code, 404)
        outside = self.tmp / "victim.txt"
        outside.write_text("keep")
        self.client.post(self.url(), {"content": "x", "base_hash": "h", "force": "1", "path": str(outside), "file": str(outside)})
        self.assertEqual(outside.read_text(), "keep")                               # a posted path is simply ignored

    def test_unsuitable_file_shows_a_message(self):
        self.script.write_bytes(b"binary\x00data")
        discovery.scan(force=True)
        r = self.client.get(self.url())
        self.assertContains(r, "looks binary", status_code=400)

    def test_edit_links_appear_where_expected(self):
        for page in ("/scripts/", f"/scripts/{self.sid}/", f"/scripts/{self.sid}/source/"):
            self.assertContains(self.client.get(page), f"/scripts/{self.sid}/edit/")

    def test_editing_can_be_switched_off(self):
        self.write_config(enable_script_editing=False)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        r = self.client.post(self.url(), {"content": "print('hacked')\n", "base_hash": "x", "force": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post(self.url("restore/")).status_code, 403)
        self.assertEqual(self.script.read_text(), "print('v1')\n")
        self.assertNotContains(self.client.get("/scripts/"), f"/scripts/{self.sid}/edit/")

    def test_settings_page_controls_the_switches(self):
        form = {"max_concurrent_executions": "3", "max_concurrent_per_host": "3", "default_timeout": "60",
                "polling_interval": "2000", "history_retention_days": "90", "log_retention_days": "30",
                "ssh_connect_timeout": "10", "ssh_executable": "ssh", "environment": "",
                "interpreters": ".py=python", "remote_interpreters": ".py=python3"}
        self.client.post("/settings/save/", form)                                   # box unticked
        self.assertFalse(config.load_config()["enable_script_editing"])
        self.client.post("/settings/save/", {**form, "enable_script_editing": "on"})
        cfg = config.load_config()
        self.assertTrue(cfg["enable_script_editing"])
        self.assertNotIn("enable_adhoc", cfg)                                       # removed feature
