"""Phase 4: history, retention, presets and settings.

python manage.py test runner.test_phase4
"""
import datetime
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import discovery, history, history_ops, presets
from runner import parameter_service as ps


def days_ago(n, hours=0):
    return (datetime.datetime.now() - datetime.timedelta(days=n, hours=hours)).isoformat(timespec="seconds")


class Phase4Case(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-p4-"))
        self.scripts = self.tmp / "scripts"
        self.scripts.mkdir()
        for sub in ("executions", "presets"):
            (self.tmp / sub).mkdir()
        self.cfg_file = self.tmp / "config.json"
        self.cfg_file.write_text(json.dumps({
            "script_directories": [str(self.scripts)],
            "history_retention_days": 30, "log_retention_days": 7,
        }))
        for p in (mock.patch.object(config, "CONFIG_FILE", self.cfg_file),
                  mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups"),
                  mock.patch.object(config, "SEQUENCES_DIR", self.tmp / "sequences"),
                  mock.patch.object(config, "SEQUENCE_RUNS_DIR", self.tmp / "sequence_runs")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = self.client_class(HTTP_HOST="localhost")

    def make_run(self, run_id, status="success", script_id="aaaaaaaaaaaa", name="Backup", age_days=0,
                 params=None, error=None, host="local", logs=True):
        history.run_dir(run_id).mkdir(parents=True)
        when = days_ago(age_days)
        history.write_meta(run_id, {
            "id": run_id, "script_id": script_id, "script_name": name, "host": host, "status": status,
            "queued_at": when, "started_at": when, "finished_at": None if status in history.ACTIVE_STATUSES else when,
            "exit_code": 0 if status == "success" else None, "parameters": params or {}, "error": error,
            "command": f"python {name}.py",
        })
        if logs:
            history.log_path(run_id, "stdout").write_text("out\n")
            history.log_path(run_id, "stderr").write_text("err\n")


class HistoryQueryTests(Phase4Case):
    def setUp(self):
        super().setUp()
        self.make_run("RUN-20260101-001", "success", name="Backup", params={"database": "prod"})
        self.make_run("RUN-20260101-002", "failed", name="Cleanup", script_id="bbbbbbbbbbbb", error="disk full")
        self.make_run("RUN-20260101-003", "success", name="Backup", age_days=10, params={"database": "dev"})
        self.make_run("RUN-20260101-004", "running", name="Backup")

    def ids(self, **kw):
        runs, total = history_ops.query_runs(**kw)
        self.assertEqual(len(runs), total)
        return [r["id"] for r in runs]

    def test_newest_first_and_no_filter(self):
        self.assertEqual(self.ids(), ["RUN-20260101-004", "RUN-20260101-003", "RUN-20260101-002", "RUN-20260101-001"])

    def test_filter_by_script_and_status(self):
        self.assertEqual(self.ids(script_id="bbbbbbbbbbbb"), ["RUN-20260101-002"])
        self.assertEqual(self.ids(status="success"), ["RUN-20260101-003", "RUN-20260101-001"])
        self.assertEqual(self.ids(script_id="aaaaaaaaaaaa", status="failed"), [])

    def test_search_matches_parameters_errors_and_names(self):
        self.assertEqual(self.ids(q="prod"), ["RUN-20260101-001"])
        self.assertEqual(self.ids(q="DISK FULL"), ["RUN-20260101-002"])
        self.assertEqual(self.ids(q="cleanup"), ["RUN-20260101-002"])
        self.assertEqual(self.ids(q="running"), ["RUN-20260101-004"])
        self.assertEqual(self.ids(q="nothing-matches"), [])

    def test_filter_by_date_range(self):
        today = datetime.date.today()
        self.assertEqual(self.ids(date_from=today - datetime.timedelta(days=2)),
                         ["RUN-20260101-004", "RUN-20260101-002", "RUN-20260101-001"])
        self.assertEqual(self.ids(date_to=today - datetime.timedelta(days=5)), ["RUN-20260101-003"])
        self.assertEqual(self.ids(date_from=today - datetime.timedelta(days=12), date_to=today - datetime.timedelta(days=8)),
                         ["RUN-20260101-003"])

    def test_paging(self):
        page1, total = history_ops.query_runs(page=1, per_page=3)
        page2, _ = history_ops.query_runs(page=2, per_page=3)
        self.assertEqual((len(page1), len(page2), total), (3, 1, 4))

    def test_history_page_filters_and_renders(self):
        r = self.client.get("/history/?status=failed")
        self.assertContains(r, "RUN-20260101-002")
        self.assertNotContains(r, "RUN-20260101-001")
        self.assertContains(self.client.get("/history/?q=zzz"), "No runs match these filters")
        self.assertContains(self.client.get("/history/?date_from=not-a-date"), "RUN-20260101-001")  # bad input ignored
        self.assertContains(self.client.get("/history/?page=99"), "RUN-20260101-0")


class DeleteAndRetentionTests(Phase4Case):
    def test_delete_run_refuses_active_runs(self):
        self.make_run("RUN-20260101-001", "running")
        self.make_run("RUN-20260101-002", "queued")
        self.make_run("RUN-20260101-003", "success")
        self.assertFalse(history_ops.delete_run("RUN-20260101-001"))
        self.assertFalse(history_ops.delete_run("RUN-20260101-002"))
        self.assertTrue(history_ops.delete_run("RUN-20260101-003"))
        self.assertTrue(history.run_dir("RUN-20260101-001").exists())
        self.assertFalse(history.run_dir("RUN-20260101-003").exists())
        self.assertFalse(history_ops.delete_run("../../etc"))        # not a valid run id

    def test_delete_older_than(self):
        self.make_run("RUN-20260101-001", age_days=40)
        self.make_run("RUN-20260101-002", age_days=5)
        self.make_run("RUN-20260101-003", "running", age_days=90)    # active: never removed
        self.assertEqual(history_ops.delete_older_than(30), 1)
        self.assertEqual(sorted(history.list_run_ids()), ["RUN-20260101-002", "RUN-20260101-003"])

    def test_retention_removes_logs_first_then_whole_runs(self):
        self.make_run("RUN-20260101-001", age_days=40)    # beyond history retention (30): gone
        self.make_run("RUN-20260101-002", age_days=10)    # beyond log retention (7): logs gone, record stays
        self.make_run("RUN-20260101-003", age_days=1)     # recent: untouched
        result = history_ops.apply_retention()
        self.assertEqual(result, {"runs_removed": 1, "logs_removed": 1, "sequences_removed": 0})
        self.assertFalse(history.run_dir("RUN-20260101-001").exists())
        meta = history.read_meta("RUN-20260101-002")
        self.assertTrue(meta["logs_deleted"])
        self.assertFalse(history.log_path("RUN-20260101-002", "stdout").exists())
        self.assertTrue(history.log_path("RUN-20260101-003", "stdout").exists())
        self.assertEqual(history_ops.apply_retention(), {"runs_removed": 0, "logs_removed": 0, "sequences_removed": 0})   # idempotent

    def test_delete_views(self):
        self.make_run("RUN-20260101-001", "success")
        self.make_run("RUN-20260101-002", "running")
        r = self.client.post("/history/RUN-20260101-001/delete/", follow=True)
        self.assertContains(r, "Deleted RUN-20260101-001")
        r = self.client.post("/history/RUN-20260101-002/delete/", follow=True)
        self.assertContains(r, "still queued or running")
        self.assertEqual(self.client.post("/history/RUN-20260101-999/delete/").status_code, 404)
        self.assertEqual(self.client.get("/history/RUN-20260101-001/delete/").status_code, 405)
        self.make_run("RUN-20260101-003", "success", age_days=60)
        r = self.client.post("/history/delete-old/", {"days": "30"}, follow=True)
        self.assertContains(r, "Deleted 1 execution")
        r = self.client.post("/history/delete-old/", {"days": "abc"}, follow=True)
        self.assertContains(r, "Enter a number of days")
        r = self.client.post("/history/retention/", follow=True)
        self.assertContains(r, "Retention applied")

    def test_console_survives_deleted_logs(self):
        self.make_run("RUN-20260101-001", age_days=10)
        history_ops.apply_retention()
        page = self.client.get("/executions/RUN-20260101-001/")
        self.assertContains(page, "removed by the retention policy")
        self.assertEqual(self.client.get("/executions/RUN-20260101-001/log/stdout/").status_code, 404)
        self.assertEqual(self.client.get("/executions/RUN-20260101-001/status/").json()["stdout"], "")


class PresetStorageTests(Phase4Case):
    SID = "0123456789ab"

    def setUp(self):
        super().setUp()
        self.script_file = self.scripts / "tool.py"
        self.script_file.write_bytes(b"print(1)\n")
        self.s = SimpleNamespace(id=self.SID, path=self.script_file, meta={})
        self.dir = config.PRESETS_DIR / self.SID                 # saved values live in runtime/presets/<id>/
        self.old_file = self.scripts / "tool.params.json"        # the layout before version 2.1

    def params(self, extra=None):
        base = [
            {"name": "db", "label": "Database", "type": "text", "default": "mydb", "required": True},
            {"name": "days", "label": "Days", "type": "number", "default": 30, "min": 1, "max": 365},
            {"name": "mode", "label": "Mode", "type": "select", "choices": ["fast", "full"], "default": "fast"},
            {"name": "zip", "label": "Zip", "type": "boolean", "default": True},
            {"name": "secret", "label": "Secret", "type": "password"},
            {"name": "fixed", "label": "Fixed", "type": "text", "default": "F", "editable_at_runtime": False},
        ] + (extra or [])
        return ps.validate_parameters(base)[0]

    RAW = {"db": "prod", "days": "7", "mode": "full", "zip": False, "secret": "hunter2", "fixed": "hacked"}

    def test_save_load_roundtrip_one_yaml_file_without_passwords_or_fixed(self):
        params = self.params()
        slug = presets.save_preset(self.s, "Production Backup", params, self.RAW, "prod-1")
        self.assertEqual(slug, "production-backup")
        stored_text = (self.dir / "production-backup.yaml").read_text()
        self.assertTrue(stored_text.startswith("# Preset for "))
        self.assertIn("name: Production Backup\n", stored_text)
        self.assertIn("\nvalues:\n  db: prod\n", stored_text)
        self.assertNotIn("hunter2", stored_text)
        self.assertNotIn("hacked", stored_text)
        self.assertEqual([p.name for p in self.scripts.iterdir()], ["tool.py"])     # nothing next to the script
        data = presets.get_preset(self.s, slug)
        self.assertEqual(data["host"], "prod-1")
        raw, warnings = presets.apply_preset(params, data)
        self.assertEqual((raw["db"], raw["days"], raw["mode"], raw["zip"]), ("prod", "7", "full", False))
        self.assertEqual(raw["fixed"], "F")                         # locked values never come from a preset
        self.assertEqual(warnings, [])

    def test_list_rename_delete_and_name_rules(self):
        params = self.params()
        presets.save_preset(self.s, "Weekly", params, self.RAW, "local")
        presets.save_preset(self.s, "Daily", params, self.RAW, "local")
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Daily", "Weekly"])
        presets.rename_preset(self.s, "weekly", "Weekly Full")
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Daily", "Weekly Full"])
        with self.assertRaises(presets.PresetError):
            presets.rename_preset(self.s, "daily", "weekly full")      # duplicate (case-insensitive)
        with self.assertRaises(presets.PresetError):
            presets.save_preset(self.s, "   ", params, self.RAW, "local")
        with self.assertRaises(presets.PresetError):
            presets.save_preset(self.s, "x" * 61, params, self.RAW, "local")
        self.assertTrue(presets.delete_preset(self.s, "daily"))
        self.assertFalse(presets.delete_preset(self.s, "daily"))
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Weekly Full"])

    def test_saving_same_name_updates_and_different_names_get_unique_slugs(self):
        params = self.params()
        a = presets.save_preset(self.s, "Nightly!", params, self.RAW, "local")
        b = presets.save_preset(self.s, "Nightly?", params, self.RAW, "local")      # same slug base
        self.assertNotEqual(a, b)
        again = presets.save_preset(self.s, "nightly!", params, {**self.RAW, "db": "other"}, "local")
        self.assertEqual(again, a)
        self.assertEqual(presets.get_preset(self.s, a)["values"]["db"], "other")
        self.assertEqual(len(presets.list_presets(self.s)), 2)

    def test_bad_ids_and_slugs_are_refused(self):
        params = self.params()
        for bad_sid in ("../x", "..", "a/b", ""):
            bad = SimpleNamespace(id=bad_sid, path=self.script_file, meta={})
            with self.assertRaises(presets.PresetError):
                presets.save_preset(bad, "n", params, self.RAW, "local")
            self.assertEqual(presets.list_presets(bad), [])
        self.assertIsNone(presets.get_preset(self.s, "../../config"))
        with self.assertRaises(presets.PresetError):
            presets.delete_preset(self.s, "../evil")
        slug = presets.save_preset(self.s, "../../evil name", params, self.RAW, "local")
        self.assertEqual(slug, "evil-name")
        self.assertEqual(sorted(p.name for p in self.scripts.iterdir()), ["tool.py"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["evil-name.yaml"])
        self.assertEqual(sorted(p.name for p in config.PRESETS_DIR.iterdir()), [self.SID])

    def test_removed_renamed_and_changed_parameters_are_handled(self):
        old = self.params()
        slug = presets.save_preset(self.s, "Old", old, self.RAW, "local")
        data = presets.get_preset(self.s, slug)
        # metadata changed: 'db' removed, 'days' range tightened, 'mode' choices changed, new parameter added
        new, definition_errors = ps.validate_parameters([
            {"name": "days", "label": "Days", "type": "number", "default": 3, "min": 1, "max": 5},
            {"name": "mode", "label": "Mode", "type": "select", "choices": ["quick", "deep"], "default": "quick"},
            {"name": "zip", "label": "Zip", "type": "boolean", "default": True},
            {"name": "region", "label": "Region", "type": "text", "default": "eu"},
        ])
        self.assertEqual(definition_errors, [])
        raw, warnings = presets.apply_preset(new, data)
        text = " ".join(warnings)
        self.assertIn("'db' no longer exists", text)
        self.assertIn("'Days'", text)                      # 7 > new max 5
        self.assertIn("'Mode'", text)                      # 'full' not a choice any more
        self.assertIn("'Region' is new", text)
        self.assertEqual((raw["days"], raw["mode"], raw["region"], raw["zip"]), ("3", "quick", "eu", False))
        values, errors = ps.clean_values(new, raw)         # the merged result is always valid
        self.assertEqual(errors, {})

    def test_a_broken_file_is_skipped_and_never_overwritten(self):
        params = self.params()
        self.dir.mkdir(parents=True)
        (self.dir / "good.yaml").write_text("values: [1, 2")
        self.assertEqual(presets.list_presets(self.s), [])
        self.assertEqual(presets.save_preset(self.s, "Good", params, self.RAW, "local"), "good-2")   # its own file
        self.assertEqual((self.dir / "good.yaml").read_text(), "values: [1, 2")
        presets.record_run(self.s, params, self.RAW, "local", "RUN-20260101-001")
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Good"])
        self.assertEqual(len(presets.recent_runs(self.s)), 1)

    def test_recent_runs_are_remembered_newest_first_and_deduplicated(self):
        params = self.params()
        presets.record_run(self.s, params, self.RAW, "local", "RUN-20260101-001")
        presets.record_run(self.s, params, {**self.RAW, "db": "b"}, "local", "RUN-20260101-002")
        presets.record_run(self.s, params, self.RAW, "local", "RUN-20260101-003")     # same values as the first
        runs = presets.recent_runs(self.s)
        self.assertEqual([r["run_id"] for r in runs], ["RUN-20260101-003", "RUN-20260101-002"])
        self.assertEqual((runs[0]["key"], runs[0]["label"]), ("@0", "Last run"))
        files = sorted((self.dir / "recent").iterdir())
        self.assertEqual([f.name for f in files], ["RUN-20260101-002.yaml", "RUN-20260101-003.yaml"])
        self.assertNotIn("hunter2", "".join(f.read_text() for f in files))
        for i in range(15):
            presets.record_run(self.s, params, {**self.RAW, "days": str(i + 1)}, "local", f"RUN-20260102-{i:03d}")
        self.assertEqual(len(presets.recent_runs(self.s)), presets.RECENT_MAX)
        self.assertEqual(len(list((self.dir / "recent").iterdir())), presets.RECENT_MAX)   # older files removed
        raw, _w = presets.apply_preset(params, presets.get_recent(self.s, 0))
        self.assertEqual(raw["days"], "15")

    def test_grid_compares_presets_and_recent_runs(self):
        params = self.params()
        presets.save_preset(self.s, "Prod", params, self.RAW, "prod-1")
        presets.record_run(self.s, params, {**self.RAW, "db": "mydb"}, "local", "RUN-20260101-001")
        g = presets.grid(self.s, params)
        self.assertEqual([c["name"] for c in g["columns"]], ["Prod", "Last run"])
        rows = {r["name"]: r for r in g["rows"]}
        self.assertEqual([c["text"] for c in rows["db"]["cells"]], ["prod", "mydb"])
        self.assertTrue(rows["db"]["varies"])
        self.assertEqual([c.get("changed") for c in rows["db"]["cells"]], [True, False])   # mydb is the default
        self.assertEqual(rows["secret"]["cells"][0]["text"], "not saved")
        self.assertTrue(rows["host"]["varies"])

    def test_older_layouts_are_migrated_to_yaml_files(self):
        self.dir.mkdir(parents=True)
        (self.dir / "weekly.json").write_text(json.dumps({"name": "Weekly", "script_id": self.SID, "host": "local",
                                                          "values": {"db": "w"}, "saved_at": "2026-01-01T00:00:00"}))
        self.old_file.write_text(json.dumps({
            "presets": {"daily": {"name": "Daily", "host": "local", "values": {"db": "d"}}},
            "recent": [{"host": "local", "values": {"db": "r"}, "saved_at": "2026-01-02T00:00:00",
                        "run_id": "RUN-20260102-001"}]}))
        (config.PRESETS_DIR / f"{self.SID}.json").write_text(json.dumps(
            {"presets": {"ro": {"name": "Ro", "values": {"db": "ro"}}}, "recent": []}))
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Daily", "Ro", "Weekly"])
        self.assertEqual(presets.get_recent(self.s, 0)["values"], {"db": "r"})
        self.assertFalse(self.old_file.exists())
        self.assertFalse((config.PRESETS_DIR / f"{self.SID}.json").exists())
        self.assertFalse((self.dir / "weekly.json").exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["daily.yaml", "recent", "ro.yaml", "weekly.yaml"])

    def test_a_read_only_script_folder_does_not_matter(self):
        params = self.params()
        self.scripts.chmod(0o555)                                  # nothing is written next to the script any more
        self.addCleanup(self.scripts.chmod, 0o755)
        presets.save_preset(self.s, "Ro", params, self.RAW, "local")
        self.assertEqual([p.name for p in self.scripts.iterdir()], ["tool.py"])
        self.assertEqual([p["name"] for p in presets.list_presets(self.s)], ["Ro"])

class PresetWebTests(Phase4Case):
    def setUp(self):
        super().setUp()
        (self.scripts / "tool.py").write_text("print('x')\n")
        self.meta_file = self.scripts / "tool.json"
        self.write_meta([
            {"name": "db", "label": "Database", "type": "text", "default": "mydb", "required": True},
            {"name": "days", "label": "Days", "type": "number", "default": 30, "min": 1, "max": 365, "required": True},
            {"name": "password", "label": "Password", "type": "password"},
        ])
        self.sid = discovery.scan(force=True)[0][0].id
        self.addCleanup(discovery.scan, True)

    def write_meta(self, params):
        self.meta_file.write_text(json.dumps({"name": "Tool", "parameters": params}))
        discovery.scan(force=True)

    def url(self, name):
        return f"/scripts/{self.sid}/{name}/"

    def start(self, db, days, token):
        with mock.patch("runner.execution_views.manager.submit", return_value="RUN-20260101-009") as submit:
            r = self.client.post(self.url("start"), {"host": "local", "p_db": db, "p_days": days,
                                                     "p_password": "pw", "submit_token": token})
        self.assertEqual(r.status_code, 302)
        return submit

    def test_started_runs_remember_values_and_prefill_the_form(self):
        self.start("lastdb", "5", "t1")
        stored = "".join(f.read_text() for f in (config.PRESETS_DIR / self.sid / "recent").iterdir())
        self.assertIn("lastdb", stored)
        self.assertNotIn('"pw"', stored)                                   # passwords are never saved
        page = self.client.get(self.url("run"))
        self.assertContains(page, 'value="lastdb"')                       # opens with the last run's values
        self.assertContains(page, "Last run")
        self.assertContains(self.client.get(self.url("run") + "?preset=-"), 'value="mydb"')   # defaults on request

    def run_saved(self, key):
        with mock.patch("runner.execution_views.manager.submit", return_value="RUN-20260101-010") as submit:
            r = self.client.post(self.url("start"), {"from_preset": key})
        return r, submit

    def test_run_with_saved_values_starts_straight_away(self):
        self.client.post(self.url("presets/save"), {"host": "local", "p_db": "prod", "p_days": "7", "preset_name": "Prod"})
        r, submit = self.run_saved("prod")
        self.assertEqual(r["Location"], self.url("run") + "?open=RUN-20260101-010")    # opens as a run tab
        self.assertIn("--db", submit.call_args[0][0].argv)
        self.assertIn("prod", submit.call_args[0][0].argv)
        self.start("other", "9", "t2")
        r, submit = self.run_saved("@0")                                                 # @0 = last run
        self.assertIn("other", submit.call_args[0][0].argv)
        r, submit = self.run_saved("nope")                                               # unknown: the form, nothing runs
        self.assertContains(r, "not found")
        submit.assert_not_called()
        grid = self.client.get(self.url("run") + "?tab=params")
        self.assertContains(grid, 'id="saved-panel" aria-label="Saved values" data-force-open')   # opens the bottom panel
        self.assertContains(grid, 'name="from_preset" value="prod"')
        self.assertContains(self.client.get(f"/scripts/{self.sid}/parameters/"), 'name="from_preset" value="prod"')

    def test_save_load_in_run_form(self):
        r = self.client.post(self.url("presets/save"), {"host": "local", "p_db": "prod", "p_days": "7",
                                                        "p_password": "pw", "preset_name": "Prod"}, follow=True)
        self.assertContains(r, "Preset &#x27;Prod&#x27; saved")
        self.assertContains(r, 'value="prod"')                    # loaded straight away
        self.assertContains(r, 'value="7"')
        self.assertNotContains(r, 'value="pw"')                   # password not stored
        listing = self.client.get(self.url("run"))
        self.assertContains(listing, '<option value="prod"')
        loaded = self.client.get(self.url("run") + "?preset=prod")
        self.assertContains(loaded, 'value="prod"')
        self.assertContains(self.client.get(self.url("run") + "?preset=nope"), "That preset was not found")

    def test_modify_before_running_does_not_change_preset(self):
        self.client.post(self.url("presets/save"), {"host": "local", "p_db": "prod", "p_days": "7", "preset_name": "Prod"})
        with mock.patch("runner.execution_views.manager.submit", return_value="RUN-20260101-011"):
            r = self.client.post(self.url("start"), {"host": "local", "p_db": "changed", "p_days": "9", "submit_token": "t-mod"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(self.url("run") + "?preset=prod"), 'value="prod"')

    def test_rename_and_delete_in_ui(self):
        self.client.post(self.url("presets/save"), {"host": "local", "p_db": "a", "p_days": "1", "preset_name": "One"})
        r = self.client.post(self.url("presets/rename"), {"slug": "one", "new_name": "Uno"}, follow=True)
        self.assertContains(r, "Preset renamed")
        self.assertContains(r, "Uno")
        r = self.client.post(self.url("presets/delete"), {"slug": "one"}, follow=True)
        self.assertContains(r, "Preset deleted")
        self.assertEqual(presets.list_presets(self.sid), [])

    def test_empty_name_keeps_the_values_on_screen(self):
        r = self.client.post(self.url("presets/save"), {"host": "local", "p_db": "keepme", "p_days": "3", "preset_name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Enter a name for the preset", status_code=400)
        self.assertContains(r, 'value="keepme"', status_code=400)

    def test_metadata_change_shows_warnings_instead_of_failing(self):
        self.client.post(self.url("presets/save"), {"host": "local", "p_db": "prod", "p_days": "200", "preset_name": "Old"})
        self.write_meta([{"name": "days", "label": "Days", "type": "number", "default": 5, "min": 1, "max": 10}])
        r = self.client.get(self.url("run") + "?preset=old")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no longer exists")
        self.assertContains(r, "reset to its default")
        self.assertContains(r, 'value="5"')

    def test_invalid_preset_slug_is_harmless(self):
        for slug in ("../x", "A B", "..%2f..%2fconfig"):
            self.assertEqual(self.client.get(self.url("run") + "?preset=" + slug).status_code, 200)
        r = self.client.post(self.url("presets/delete"), {"slug": "../x"}, follow=True)
        self.assertEqual(r.status_code, 200)


class SettingsSaveTests(Phase4Case):
    def form(self, **over):
        base = {
            "max_concurrent_executions": "4", "max_concurrent_per_host": "2", "default_timeout": "600",
            "polling_interval": "1500", "history_retention_days": "45", "log_retention_days": "14",
            "ssh_connect_timeout": "8", "ssh_executable": "ssh",
            "environment": "A=1\nB=two", "interpreters": ".py=python\n.sh=bash", "remote_interpreters": ".py=python3\n.sh=bash",
        }
        base.update(over)
        return base

    def test_save_applies_and_persists(self):
        r = self.client.post("/settings/save/", self.form(), follow=True)
        self.assertContains(r, "Settings saved")
        saved = json.loads(self.cfg_file.read_text())
        self.assertEqual((saved["max_concurrent_executions"], saved["history_retention_days"], saved["ssh_connect_timeout"]),
                         (4, 45, 8))
        self.assertEqual(saved["environment"], {"A": "1", "B": "two"})
        self.assertEqual(saved["script_directories"], [str(self.scripts)])   # untouched by this form
        self.assertTrue(self.cfg_file.with_suffix(".json.bak").exists())      # previous version kept
        self.assertContains(self.client.get("/settings/"), 'value="45"')

    def test_invalid_values_are_rejected_without_saving(self):
        before = self.cfg_file.read_text()
        r = self.client.post("/settings/save/", self.form(max_concurrent_executions="0", default_timeout="soon",
                                                         environment="BAD LINE", interpreters=".py"), follow=True)
        self.assertEqual(r.status_code, 400)
        for text in ("Max concurrent executions must be between", "Default timeout must be a whole number",
                     "must look like NAME=value", "use the form key=value"):
            self.assertContains(r, text, status_code=400)
        self.assertContains(r, 'value="soon"', status_code=400)                # what you typed is kept
        self.assertEqual(self.cfg_file.read_text(), before)

    def test_bad_extension_and_empty_interpreter_list(self):
        r = self.client.post("/settings/save/", self.form(interpreters="py=python"))
        self.assertContains(r, "Invalid file extension", status_code=400)
        r = self.client.post("/settings/save/", self.form(interpreters=""))
        self.assertContains(r, "at least one file extension", status_code=400)

    def test_new_limits_are_used_by_the_manager(self):
        self.client.post("/settings/save/", self.form(max_concurrent_executions="1"))
        self.assertEqual(config.load_config()["max_concurrent_executions"], 1)

    def test_pages_render(self):
        for url in ("/history/", "/settings/", "/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
