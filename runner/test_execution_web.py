"""Web flow: run form -> confirm -> start -> console polling -> stop -> logs.

python manage.py test runner.test_execution_web
"""
import json
import shutil
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import discovery, history
from runner.process_manager import manager


class ExecutionWebTests(SimpleTestCase):
    def setUp(self):
        self.manager = manager
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-web-"))
        self.scripts = self.tmp / "scripts"
        self.scripts.mkdir()
        (self.tmp / "executions").mkdir()
        (self.scripts / "tool.py").write_text(textwrap.dedent("""
            import argparse, sys, time
            p = argparse.ArgumentParser()
            p.add_argument("--db", required=True)
            p.add_argument("--days", type=int, required=True)
            p.add_argument("--password", default="")
            p.add_argument("--zip", action="store_true")
            p.add_argument("--sleep", type=float, default=0)
            a = p.parse_args()
            print("db=%s days=%d zip=%s pwlen=%d" % (a.db, a.days, a.zip, len(a.password)), flush=True)
            print("warn", file=sys.stderr, flush=True)
            time.sleep(a.sleep)
        """))
        (self.scripts / "tool.json").write_text(json.dumps({
            "name": "Tool", "category": "Test", "interpreter": "python",
            "parameters": [
                {"name": "db", "type": "text", "default": "mydb", "required": True},
                {"name": "days", "type": "number", "default": 30, "min": 1, "max": 365, "required": True},
                {"name": "password", "type": "password"},
                {"name": "zip", "type": "boolean", "default": True},
                {"name": "sleep", "type": "decimal", "default": "0"},
            ]}))
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"script_directories": [str(self.scripts)], "polling_interval": 500}))
        for p in (mock.patch.object(config, "CONFIG_FILE", cfg),
                  mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups")):
            p.start()
            self.addCleanup(p.stop)
        discovery.scan(force=True)
        self.addCleanup(discovery.scan, True)
        self.sid = discovery.scan()[0][0].id
        self.client = self.client_class(HTTP_HOST="localhost")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for rid in list(self.manager._active):
            self.manager.cancel(rid)
        self.manager.wait_idle(20)
        shutil.rmtree(self.tmp, ignore_errors=True)

    FORM = {"host": "local", "p_db": "prod", "p_days": "7", "p_password": "s3cr3t-pw", "p_zip": "on", "p_sleep": "0"}

    def url(self, name):
        return f"/scripts/{self.sid}/{name}/"

    def start(self, token=None, **over):
        token = token or uuid.uuid4().hex   # unique per call: the manager remembers tokens
        return self.client.post(self.url("start"), {**self.FORM, "submit_token": token, **over})

    @staticmethod
    def run_id_of(response):
        # Starting a run goes back to the script page, which opens the run as a tab (?open=RUN-…).
        return response["Location"].split("open=", 1)[1].split("&")[0]

    def poll(self, run_id, out=0, err=0):
        r = self.client.get(f"/executions/{run_id}/status/?out={out}&err={err}")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def wait_final(self, run_id, timeout=25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            d = self.poll(run_id)
            if not d["active"]:
                return d
            time.sleep(0.1)
        self.fail("run did not finish")

    def test_run_form_offers_host_selector(self):
        r = self.client.get(self.url("run"))
        self.assertContains(r, 'name="host"')
        self.assertContains(r, '<option value="local" selected>local</option>')

    def test_full_flow_start_poll_logs(self):
        page = self.client.get(self.url("run"))
        self.assertContains(page, 'name="submit_token"')                        # Run starts directly (no review step)
        r = self.start()
        self.assertEqual(r.status_code, 302)
        run_id = self.run_id_of(r)
        self.assertTrue(history.valid_run_id(run_id))
        self.assertEqual(r["Location"], f"/scripts/{self.sid}/run/?open={run_id}")   # stays on the script page
        page = self.client.get(r["Location"])
        self.assertContains(page, f'data-script="{self.sid}"')                     # the run tab is added by designer.js
        embed = self.client.get(f"/executions/{run_id}/?embed=1")
        self.assertContains(embed, "Standard output")
        self.assertContains(embed, '<base target="_top">')
        self.assertNotContains(embed, 'class="activity"')                        # no shell around it
        self.assertNotContains(embed, "Run again")

        d = self.wait_final(run_id)
        self.assertEqual((d["status"], d["exit_code"]), ("success", 0))
        self.assertIn("db=prod days=7 zip=True pwlen=9", d["stdout"])
        self.assertIn("warn", d["stderr"])
        self.assertNotIn("warn", d["stdout"])                  # streams are separate

        # incremental polling: continuing from the returned offsets yields nothing new
        again = self.poll(run_id, d["stdout_next"], d["stderr_next"])
        self.assertEqual((again["stdout"], again["stderr"]), ("", ""))

        # secrets are redacted everywhere that is stored
        self.assertNotIn("s3cr3t-pw", history.meta_path(run_id).read_text())
        self.assertEqual(history.read_meta(run_id)["parameters"]["password"], "••••••")

        dl = self.client.get(f"/executions/{run_id}/log/stdout/")
        self.assertEqual(dl.status_code, 200)
        self.assertIn("attachment", dl["Content-Disposition"])
        self.assertIn(b"db=prod", b"".join(dl.streaming_content))
        self.assertEqual(self.client.get(f"/executions/{run_id}/log/other/").status_code, 404)

    def test_double_submit_with_same_token_runs_once(self):
        tok = uuid.uuid4().hex
        a = self.start(token=tok)
        b = self.start(token=tok)
        self.assertEqual(a["Location"], b["Location"])
        self.assertEqual(len(history.list_run_ids()), 1)
        self.manager.wait_idle()

    def test_start_rejects_invalid_values_and_creates_no_run(self):
        r = self.start(p_days="9999")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "at most 365", status_code=400)
        self.assertEqual(history.list_run_ids(), [])

    def test_unknown_host_rejected(self):
        r = self.start(host="not-in-ssh-config")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Choose one of the listed hosts", status_code=400)
        self.assertEqual(history.list_run_ids(), [])

    def test_stop_from_the_ui(self):
        run_id = self.run_id_of(self.start(p_sleep="30"))
        end = time.monotonic() + 15
        while time.monotonic() < end and "db=prod" not in self.poll(run_id)["stdout"]:
            time.sleep(0.1)
        d = self.poll(run_id)
        self.assertEqual((d["status"], d["active"]), ("running", True))
        self.assertContains(self.client.get(f"/executions/{run_id}/"), "Stop")
        self.assertEqual(self.client.post(f"/executions/{run_id}/stop/").status_code, 302)
        d = self.wait_final(run_id)
        self.assertEqual(d["status"], "cancelled")
        self.assertIn("Stopped", d["error"])
        # stopping a finished run is harmless; from a run tab it stays in the tab
        self.assertEqual(self.client.post(f"/executions/{run_id}/stop/").status_code, 302)
        r = self.client.post(f"/executions/{run_id}/stop/", {"embed": "1"})
        self.assertEqual(r["Location"], f"/executions/{run_id}/?embed=1")

    def test_runs_page_and_library_last_run(self):
        run_id = self.run_id_of(self.start(p_sleep="1.5"))         # still running for a moment
        running = self.client.get("/history/")                     # Jobs merged into History: Running on top
        self.assertContains(running, "Running")
        self.assertContains(self.client.get("/executions/?partial=1"), run_id)
        self.wait_final(run_id)
        self.assertRedirects(self.client.get("/executions/"), "/history/")
        page = self.client.get("/history/")
        self.assertContains(page, run_id)
        self.assertContains(page, "Success")
        self.assertNotContains(page, '<section class="panel" data-active-count')   # nothing running: no Running panel
        self.assertNotContains(self.client.get("/executions/?partial=1"), run_id)
        lib = self.client.get("/scripts/")
        self.assertContains(lib, "Last run")
        self.assertContains(lib, "st-success")

    def test_bad_run_ids_are_404(self):
        for bad in ("RUN-20200101-999", "..", "RUN-1", "nope"):
            self.assertEqual(self.client.get(f"/executions/{bad}/").status_code, 404, bad)
            self.assertEqual(self.client.get(f"/executions/{bad}/status/").status_code, 404, bad)

    def test_settings_rejects_unknown_ssh_host_test(self):
        r = self.client.post("/settings/hosts/test/", {"host": "definitely-not-configured"}, follow=True)
        self.assertContains(r, "Unknown SSH host")
