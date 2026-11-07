"""Script editing and ad-hoc scripts.

python manage.py test runner.test_editor_adhoc
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
        self.client.post("/settings/save/", form)                                   # both boxes unticked
        cfg = config.load_config()
        self.assertEqual((cfg["enable_adhoc"], cfg["enable_script_editing"]), (False, False))
        self.client.post("/settings/save/", {**form, "enable_adhoc": "on", "enable_script_editing": "on"})
        cfg = config.load_config()
        self.assertEqual((cfg["enable_adhoc"], cfg["enable_script_editing"]), (True, True))


# ---------------------------------------------------------------- web: ad-hoc
class AdhocTests(WebCase):
    def post(self, **over):
        form = {"language": ".py", "code": "print('hi')\n", "host": "local", "timeout": "60", "arguments": "", "cwd": ""}
        form.update(over)
        return self.client.post("/adhoc/", form)

    def test_page_offers_starter_code_languages_and_hosts(self):
        r = self.client.get("/adhoc/")
        self.assertContains(r, "Hello from Script Runner")
        self.assertContains(r, "Python (.py)")
        self.assertContains(r, "This computer (local)")
        self.assertContains(r, 'data-lang="python"')
        self.assertContains(r, "runs exactly what you enter")                       # the warning is shown
        self.assertContains(self.client.get("/"), "Ad-hoc")                         # nav link

    def test_run_python_with_arguments_end_to_end(self):
        code = "import sys, os\nprint('args=', sys.argv[1:])\nprint('cwd=', os.getcwd())\n"
        r = self.post(code=code, arguments="--flag\nvalue with spaces\n\n  \n$HOME; rm -rf /")
        self.assertEqual(r.status_code, 302, r.content[:400])
        run_id = self.run_id_of(r)
        meta = self.wait_final(run_id)
        self.assertEqual((meta["status"], meta["exit_code"]), ("success", 0), history.log_path(run_id, "stderr").read_text())
        out = history.log_path(run_id, "stdout").read_text()
        self.assertIn("['--flag', 'value with spaces', '$HOME; rm -rf /']", out)     # exact arguments, no shell
        self.assertIn(str(Path.home()), out)                                        # default cwd is the home folder
        self.assertTrue(meta["adhoc"]["language"] == ".py" and len(meta["adhoc"]["arguments"]) == 3)
        self.assertEqual(meta["script_id"], "adhoc")
        self.assertEqual(meta["initiator"], "127.0.0.1")                            # who started it

    def test_the_code_that_ran_is_kept_with_the_run(self):
        run_id = self.run_id_of(self.post(code="print('keep me')\n"))
        meta = self.wait_final(run_id)
        stored = Path(meta["script_path"])
        self.assertEqual(stored.parent, history.run_dir(run_id))
        self.assertEqual(stored.read_text(), "print('keep me')\n")
        page = self.client.get(f"/executions/{run_id}/")
        self.assertContains(page, "print(&#x27;keep me&#x27;)")
        self.assertContains(page, "Edit &amp; run again")
        self.assertNotContains(page, f"/scripts/adhoc/run/")
        shutil.rmtree(history.run_dir(run_id))                                      # retention / delete removes it too
        self.assertFalse(stored.exists())

    def test_edit_and_run_again_prefills_the_screen(self):
        run_id = self.run_id_of(self.post(code="print('again')\n", arguments="x\ny", cwd=str(self.tmp)))
        self.wait_final(run_id)
        r = self.client.get(f"/adhoc/?from={run_id}")
        self.assertContains(r, "print(&#x27;again&#x27;)")
        self.assertContains(r, ">x\ny</textarea>")
        self.assertContains(r, f'value="{self.tmp}"')
        self.assertContains(self.client.get("/adhoc/?from=RUN-19990101-001"), "Hello from Script Runner")   # unknown id: starter
        self.assertContains(self.client.get("/adhoc/?from=../../etc"), "Hello from Script Runner")

    def test_failure_and_stderr_are_reported(self):
        run_id = self.run_id_of(self.post(code="import sys\nprint('boom', file=sys.stderr)\nsys.exit(5)\n"))
        meta = self.wait_final(run_id)
        self.assertEqual((meta["status"], meta["exit_code"]), ("failed", 5))
        self.assertIn("boom", history.log_path(run_id, "stderr").read_text())

    def test_timeout_applies(self):
        run_id = self.run_id_of(self.post(code="import time\ntime.sleep(60)\n", timeout="2"))
        self.assertEqual(self.wait_final(run_id)["status"], "timed_out")

    @unittest.skipUnless(HAS_BASH, "bash needed")
    def test_shell_scripts_are_stored_with_unix_line_endings(self):
        run_id = self.run_id_of(self.post(language=".sh", code="echo one\r\necho two\r\n"))
        meta = self.wait_final(run_id)
        self.assertEqual(meta["status"], "success", history.log_path(run_id, "stderr").read_text())
        self.assertNotIn(b"\r", Path(meta["script_path"]).read_bytes())
        self.assertEqual(history.log_path(run_id, "stdout").read_text().split(), ["one", "two"])

    @unittest.skipUnless(os.name == "nt", "batch files are Windows-only")
    def test_batch_runs_and_rejects_dangerous_arguments(self):
        run_id = self.run_id_of(self.post(language=".bat", code="@echo off\necho Hello %~1\n", arguments="World"))
        meta = self.wait_final(run_id)
        self.assertEqual(meta["status"], "success", history.log_path(run_id, "stderr").read_text())
        self.assertIn("Hello World", history.log_path(run_id, "stdout").read_text())
        before = len(history.list_run_ids())
        for bad in ("a & calc", "x | dir", "%PATH%", 'q"w'):
            r = self.post(language=".bat", code="@echo off\n", arguments=bad)
            self.assertEqual(r.status_code, 400)
            self.assertContains(r, "not allowed for batch files", status_code=400)
        self.assertEqual(len(history.list_run_ids()), before)

    def test_validation_errors_start_nothing_and_keep_the_code(self):
        before = len(history.list_run_ids())
        cases = [
            ({"code": "   \n"}, "Enter the script to run"),
            ({"language": ".exe"}, "Choose a language"),
            ({"host": "not-a-configured-host"}, "Choose one of the listed hosts"),
            ({"timeout": "soon"}, "Timeout must be"),
            ({"timeout": "0"}, "Timeout must be"),
            ({"arguments": "\n".join(["a"] * 60)}, "at most 50 arguments"),
            ({"cwd": "bad\x00dir"}, "invalid characters"),
            ({"code": "print(1)\x00"}, "null character"),
            ({"code": "x" * (200 * 1024 + 10)}, "larger than 200 KB"),
        ]
        for over, message in cases:
            with self.subTest(message=message):
                r = self.post(**over)
                self.assertEqual(r.status_code, 400)
                self.assertContains(r, message, status_code=400)
        r = self.post(code="my precious code\n", host="nope")
        self.assertContains(r, "my precious code", status_code=400)                 # nothing typed is lost
        self.assertEqual(len(history.list_run_ids()), before)

    def test_batch_cannot_run_remotely(self):
        from runner import ssh_hosts
        with mock.patch.object(ssh_hosts, "list_hosts", return_value=["srv1"]):
            r = self.post(language=".bat", code="@echo off\n", host="srv1")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Batch files can only run", status_code=400)

    def test_language_must_be_configured_for_the_chosen_host(self):
        from runner import ssh_hosts
        self.write_config(interpreters={".py": "python"}, remote_interpreters={".sh": "bash"})
        with mock.patch.object(ssh_hosts, "list_hosts", return_value=["srv1"]):
            r = self.post(language=".py", host="srv1")                              # no remote python configured
        self.assertContains(r, "No interpreter is configured", status_code=400)

    def test_can_be_switched_off(self):
        self.write_config(enable_adhoc=False)
        self.assertEqual(self.client.get("/adhoc/").status_code, 403)
        before = len(history.list_run_ids())
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.post("/adhoc/save/", {"language": ".py", "code": "x"}).status_code, 403)
        self.assertEqual(len(history.list_run_ids()), before)
        self.assertNotContains(self.client.get("/"), 'href="/adhoc/"')

    def test_recent_runs_are_listed(self):
        run_id = self.run_id_of(self.post(code="print('listed')\n"))
        self.wait_final(run_id)
        r = self.client.get("/adhoc/")
        self.assertContains(r, "Recent ad-hoc runs")
        self.assertContains(r, f"/adhoc/?from={run_id}")
        self.assertContains(self.client.get("/history/?q=adhoc"), run_id)           # searchable in History


class AdhocSaveToLibraryTests(WebCase):
    def save(self, **over):
        form = {"language": ".py", "code": "print('saved')\n", "lib_dir": str(self.root), "subfolder": "",
                "filename": "my_tool", "display_name": "My Tool", "category": "Maintenance"}
        form.update(over)
        return self.client.post("/adhoc/save/", form)

    def test_saves_script_and_metadata_and_it_appears_in_the_library(self):
        r = self.save(subfolder="ops/nightly")
        self.assertEqual(r.status_code, 302, r.content[:300])
        script = self.root / "ops" / "nightly" / "my_tool.py"
        self.assertEqual(script.read_text(), "print('saved')\n")
        meta = json.loads((self.root / "ops" / "nightly" / "my_tool.json").read_text())
        self.assertEqual((meta["name"], meta["category"], meta["parameters"]), ("My Tool", "Maintenance", []))
        found = [s for s in discovery.scan(force=True)[0] if s.path.name == "my_tool.py"]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].name, found[0].category), ("My Tool", "Maintenance"))
        self.assertTrue(found[0].meta_valid)
        self.assertIn(found[0].id, r["Location"])
        self.assertContains(self.client.get(r["Location"]), "My Tool")

    def test_existing_files_are_never_overwritten(self):
        (self.root / "my_tool.py").write_text("ORIGINAL")
        r = self.save()
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "already exists", status_code=400)
        self.assertEqual((self.root / "my_tool.py").read_text(), "ORIGINAL")
        (self.root / "other.json").write_text("{}")
        self.assertEqual(self.save(filename="other").status_code, 400)             # its metadata file would be clobbered

    def test_path_traversal_and_bad_names_are_rejected(self):
        outside_before = set(p.name for p in self.tmp.iterdir())
        cases = [
            {"subfolder": "../escape"}, {"subfolder": "a/../../escape"}, {"subfolder": "..\\escape"},
            {"subfolder": "/etc"}, {"subfolder": "C:\\Windows"}, {"subfolder": "bad|name"},
            {"filename": "../evil"}, {"filename": "..\\evil"}, {"filename": "/abs/evil"}, {"filename": ".hidden"},
            {"filename": "bad name"}, {"filename": ""}, {"filename": "x" * 200},
            {"lib_dir": str(self.tmp)}, {"lib_dir": "/not/configured"}, {"lib_dir": ""},
        ]
        for over in cases:
            with self.subTest(over=over):
                r = self.save(**over)
                self.assertEqual(r.status_code, 400, r.content[:200])
        self.assertEqual(set(p.name for p in self.tmp.iterdir()), outside_before)   # nothing created outside scripts/
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_empty_script_and_unknown_language_are_rejected(self):
        self.assertEqual(self.save(code="  ").status_code, 400)
        self.assertEqual(self.save(language=".exe").status_code, 400)
        self.assertEqual(self.save(code="a\x00b").status_code, 400)

    def test_batch_scripts_are_saved_with_crlf_and_shell_with_lf(self):
        self.save(language=".bat", filename="b", code="@echo off\necho hi\n")
        self.assertEqual((self.root / "b.bat").read_bytes(), b"@echo off\r\necho hi\r\n")
        self.save(language=".sh", filename="s", code="echo a\r\necho b\r\n")
        self.assertEqual((self.root / "s.sh").read_bytes(), b"echo a\necho b\n")

    def test_extension_is_added_once(self):
        self.save(filename="report.py")
        self.assertTrue((self.root / "report.py").is_file())
        self.assertFalse((self.root / "report.py.py").exists())

    def test_form_keeps_what_was_typed_when_saving_fails(self):
        r = self.save(filename="../bad", code="print('do not lose me')\n")
        self.assertContains(r, "do not lose me", status_code=400)

    def test_get_on_the_save_url_just_redirects(self):
        self.assertEqual(self.client.get("/adhoc/save/").status_code, 302)
