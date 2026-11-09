"""Where ScriptHub keeps its data (config.runtime_location), Settings -> Data (clearing it), manage.py
clear_runtime, and File -> Close project.

python manage.py test runner.test_runtime_data
"""
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

import config
from runner import history, runtime_data, sequences, workspace


class LocationTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-loc-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.portable = self.tmp / "code" / "runtime"

    def test_env_wins_then_portable_then_the_user_folder(self):
        env = {"SCRIPTHUB_HOME": str(self.tmp / "home"), "LOCALAPPDATA": str(self.tmp / "local")}
        self.portable.mkdir(parents=True)
        where, how = config.runtime_location(env, "win32", self.portable)
        self.assertEqual((where, how), ((self.tmp / "home").resolve(), "env"))
        del env["SCRIPTHUB_HOME"]
        self.assertEqual(config.runtime_location(env, "win32", self.portable), (self.portable, "portable"))
        self.portable.rmdir()
        self.assertEqual(config.runtime_location(env, "win32", self.portable),
                         (self.tmp / "local" / "ScriptHub", "user"))

    def test_user_folder_per_platform(self):
        env = {"XDG_DATA_HOME": str(self.tmp / "xdg")}
        self.assertEqual(config.runtime_location(env, "linux", self.portable), (self.tmp / "xdg" / "scripthub", "user"))
        where, _how = config.runtime_location({}, "darwin", self.portable)
        self.assertEqual(where.parts[-3:], ("Library", "Application Support", "ScriptHub"))
        where, _how = config.runtime_location({}, "linux", self.portable)
        self.assertEqual(where.parts[-3:], (".local", "share", "scripthub"))


class DataCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-data-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        r = self.rt = self.tmp / "runtime"
        r.mkdir()
        self.cfg = r / "config.json"
        self.cfg.write_text(json.dumps({"default_timeout": 77}))
        for p in (mock.patch.object(config, "RUNTIME_DIR", r),
                  mock.patch.object(config, "CONFIG_FILE", self.cfg),
                  mock.patch.object(config, "EXECUTIONS_DIR", r / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", r / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", r / "backups"),
                  mock.patch.object(config, "METADATA_OVERRIDE_DIR", r / "overrides"),
                  mock.patch.object(config, "SEQUENCES_DIR", r / "sequences"),
                  mock.patch.object(config, "SEQUENCE_RUNS_DIR", r / "sequence_runs")):
            p.start()
            self.addCleanup(p.stop)
        self.run_dir("RUN-20260101-001", "success")
        self.run_dir("RUN-20260101-002", "running")
        (r / "presets" / "abc123abc123").mkdir(parents=True)
        (r / "presets" / "abc123abc123" / "nightly.yaml").write_text("name: Nightly\n")
        (r / "backups" / "abc123abc123").mkdir(parents=True)
        (r / "backups" / "abc123abc123" / "old.py").write_text("print(1)\n")
        sequences.create("Release")
        self.folder = self.tmp / "scripts"
        self.folder.mkdir()
        workspace.open_folder(str(self.folder))

    def run_dir(self, run_id, status):
        d = config.EXECUTIONS_DIR / run_id
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps({"id": run_id, "status": status, "script_id": "x"}))
        (d / "stdout.log").write_text("out\n")

    def row(self, key):
        return next(r for r in runtime_data.overview() if r["key"] == key)


class ClearTests(DataCase):
    def test_overview_counts_files_per_kind(self):
        self.assertEqual(self.row("history")["files"], 4)
        self.assertEqual(self.row("presets")["files"], 1)
        self.assertEqual(self.row("sequences")["files"], 1)
        self.assertEqual(self.row("imports")["files"], 0)

    def test_history_keeps_active_runs(self):
        msg = runtime_data.clear("history")
        self.assertIn("Removed 1 run", msg)
        self.assertEqual(history.list_run_ids(), ["RUN-20260101-002"])

    def test_each_kind_and_settings_back_to_defaults(self):
        for key in ("presets", "backups", "sequences"):
            runtime_data.clear(key)
            self.assertEqual(self.row(key)["files"], 0, key)
        self.assertEqual(config.load_config()["default_timeout"], 77)
        runtime_data.clear("settings")
        self.assertEqual(config.load_config()["default_timeout"], config.DEFAULTS["default_timeout"])
        self.assertIsNotNone(workspace.current())
        runtime_data.clear("workspace")
        self.assertIsNone(workspace.current())
        self.assertTrue(self.folder.is_dir())                    # the scripts themselves are never touched

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            runtime_data.clear("secret_key")


class WebTests(DataCase):
    def test_settings_shows_the_location_and_clears_one_kind_or_all(self):
        page = self.client.get("/settings/")
        self.assertContains(page, str(self.rt))
        self.assertContains(page, 'name="kind" value="presets"')
        r = self.client.post("/settings/data/clear/", {"kind": "presets"})
        self.assertEqual(r["Location"], "/settings/#data")
        self.assertEqual(self.row("presets")["files"], 0)
        self.assertEqual(self.row("backups")["files"], 1)
        self.client.post("/settings/data/clear/", {"kind": "all"})
        self.assertEqual([r["key"] for r in runtime_data.overview() if r["files"]], ["history"])   # the running one
        self.assertEqual(self.client.get("/settings/data/clear/").status_code, 405)

    def test_close_project_closes_the_folder_and_the_opened_files(self):
        other = self.tmp / "loose.py"
        other.write_bytes(b"print(1)\n")
        workspace.open_folder(str(other))
        self.assertContains(self.client.get("/scripts/"), "Close project")
        r = self.client.post("/project/close/")
        self.assertEqual(r["Location"], "/folder/")
        self.assertEqual((workspace.current(), workspace.open_files()), (None, []))
        self.assertTrue(self.folder.is_dir() and other.is_file())


class CommandTests(DataCase):
    def test_lists_and_clears(self):
        out = io.StringIO()
        call_command("clear_runtime", stdout=out)
        self.assertIn(str(self.rt), out.getvalue())
        self.assertIn("presets", out.getvalue())
        out = io.StringIO()
        call_command("clear_runtime", "presets", "backups", stdout=out)
        self.assertIn("Saved values: cleared.", out.getvalue())
        self.assertEqual(self.row("backups")["files"], 0)
        with self.assertRaises(Exception):
            call_command("clear_runtime", "nope", stdout=io.StringIO())
