"""Tests (no database needed): python manage.py test runner"""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.http import QueryDict
from django.test import SimpleTestCase, override_settings

import config
from runner import discovery, metadata
from runner import parameter_service as ps
from runner.validators import ValidationError


def P(**kw):
    kw.setdefault("name", "x")
    kw.setdefault("type", "text")
    clean, errors = ps.validate_parameters([kw])
    assert not errors, errors
    return clean[0]


class ParameterValueTests(SimpleTestCase):
    def test_required_text(self):
        p = P(required=True)
        self.assertEqual(ps.parse_value(p, "")[1], "This field is required.")
        self.assertEqual(ps.parse_value(p, "hi"), ("hi", None))

    def test_optional_empty_is_none(self):
        self.assertEqual(ps.parse_value(P(), "  "), (None, None))

    def test_number_range_and_type(self):
        p = P(type="number", min=1, max=365)
        self.assertEqual(ps.parse_value(p, "30"), (30, None))
        self.assertIn("at least", ps.parse_value(p, "0")[1])
        self.assertIn("at most", ps.parse_value(p, "366")[1])
        self.assertIn("whole number", ps.parse_value(p, "3.5")[1])
        self.assertIn("whole number", ps.parse_value(p, "abc")[1])

    def test_decimal(self):
        p = P(type="decimal", min=0, max=1)
        self.assertEqual(ps.parse_value(p, "0.25"), ("0.25", None))
        self.assertIn("valid decimal", ps.parse_value(p, "NaN")[1])
        self.assertIn("at most", ps.parse_value(p, "1.5")[1])

    def test_boolean(self):
        p = P(type="boolean")
        self.assertEqual(ps.parse_value(p, True), (True, None))
        self.assertEqual(ps.parse_value(p, False), (False, None))

    def test_select_and_multiselect(self):
        p = P(type="select", choices=["a", {"value": "b", "label": "Bee"}])
        self.assertEqual(ps.parse_value(p, "b"), ("b", None))
        self.assertIn("one of", ps.parse_value(p, "z")[1])
        m = P(type="multiselect", choices=["a", "b"])
        self.assertEqual(ps.parse_value(m, ["a", "b"]), (["a", "b"], None))
        self.assertIn("not an available", ps.parse_value(m, ["a", "q"])[1])

    def test_date_datetime(self):
        self.assertEqual(ps.parse_value(P(type="date"), "2026-11-07"), ("2026-11-07", None))
        self.assertIn("valid date", ps.parse_value(P(type="date"), "07/11/2026")[1])
        self.assertEqual(ps.parse_value(P(type="datetime"), "2026-11-07T10:30"), ("2026-11-07T10:30:00", None))

    def test_pattern_and_length(self):
        p = P(pattern=r"[a-z]+", max_length=5)
        self.assertEqual(ps.parse_value(p, "abc"), ("abc", None))
        self.assertIn("format", ps.parse_value(p, "ABC")[1])
        self.assertIn("at most", ps.parse_value(p, "abcdefg")[1])

    def test_path_rejects_control_chars(self):
        self.assertIn("invalid", ps.parse_value(P(type="directory"), "a\nb")[1])


class DefinitionValidationTests(SimpleTestCase):
    def check(self, params):
        return ps.validate_parameters(params)[1]

    def test_bad_names_and_duplicates(self):
        self.assertTrue(self.check([{"name": "1bad"}]))
        self.assertTrue(self.check([{"name": "a"}, {"name": "a"}]))

    def test_unknown_type(self):
        self.assertTrue(any("unknown type" in e for e in self.check([{"name": "a", "type": "color"}])))

    def test_select_needs_choices(self):
        self.assertTrue(self.check([{"name": "a", "type": "select"}]))

    def test_incompatible_attribute(self):
        self.assertTrue(any("not valid" in e for e in self.check([{"name": "a", "type": "text", "choices": ["x"]}])))

    def test_min_greater_than_max_and_bad_default(self):
        self.assertTrue(any("greater" in e for e in self.check([{"name": "a", "type": "number", "min": 5, "max": 1}])))
        self.assertTrue(self.check([{"name": "a", "type": "number", "max": 5, "default": 9}]))

    def test_bad_pattern(self):
        self.assertTrue(any("pattern" in e for e in self.check([{"name": "a", "pattern": "("}])))

    def test_duplicate_flag_and_position(self):
        self.assertTrue(self.check([{"name": "a", "arg": "--z"}, {"name": "b", "arg": "--z"}]))
        self.assertTrue(self.check([{"name": "a", "pass_as": "positional", "position": 1},
                                    {"name": "b", "pass_as": "positional", "position": 1}]))

    def test_forbidden_env(self):
        self.assertTrue(self.check([{"name": "a", "pass_as": "env", "env_name": "PATH"}]))

    def test_unknown_keys_preserved(self):
        clean, errors = ps.validate_parameters([{"name": "a", "future_option": 42}])
        self.assertFalse(errors)
        self.assertEqual(clean[0]["future_option"], 42)

    def test_metadata_collects_errors(self):
        with self.assertRaises(ValidationError) as cm:
            ps.validate_metadata({"platforms": "x", "environment": 5, "parameters": [{"name": "!"}]})
        self.assertGreaterEqual(len(cm.exception.errors), 3)

    def test_removed_per_script_options_are_dropped(self):
        clean = ps.validate_metadata({"name": "x", "enabled": False, "timeout": "soon", "allow_concurrent": 1})
        self.assertEqual(clean, {"name": "x", "parameters": []})


class ArgumentTests(SimpleTestCase):
    PARAMS = ps.validate_parameters([
        {"name": "database", "type": "text", "default": "mydb", "required": True},
        {"name": "backup_path", "type": "directory", "arg": "--backup-path"},
        {"name": "compress", "type": "boolean"},
        {"name": "tags", "type": "multiselect", "choices": ["a", "b"]},
        {"name": "token", "type": "password"},
        {"name": "target", "type": "text", "pass_as": "positional", "position": 2},
        {"name": "first", "type": "text", "pass_as": "positional", "position": 1},
        {"name": "region", "type": "text", "pass_as": "env", "env_name": "APP_REGION"},
    ])[0]

    def test_build(self):
        values = {"database": "d b; rm -rf /", "backup_path": "/b", "compress": True, "tags": ["a", "b"],
                  "token": "s3cret", "target": "T", "first": "F", "region": "eu"}
        argv, env = ps.build_arguments(self.PARAMS, values)
        self.assertEqual(argv, ["--database", "d b; rm -rf /", "--backup-path", "/b", "--compress",
                                "--tags", "a", "--tags", "b", "--token", "s3cret", "F", "T"])
        self.assertEqual(env, {"APP_REGION": "eu"})

    def test_false_boolean_and_empty_omitted(self):
        argv, _ = ps.build_arguments(self.PARAMS, {"compress": False, "database": "x", "backup_path": None})
        self.assertEqual(argv, ["--database", "x"])

    def test_mask_and_redact(self):
        argv, _ = ps.build_arguments(self.PARAMS, {"token": "s3cret"}, mask_secrets=True)
        self.assertNotIn("s3cret", argv)
        self.assertEqual(ps.redact(self.PARAMS, {"token": "s3cret", "database": "x"})["token"], ps.SECRET_MASK)

    def test_post_roundtrip_and_locked(self):
        params = ps.validate_parameters([
            {"name": "a", "type": "text", "default": "D"},
            {"name": "b", "type": "text", "default": "fixed", "editable_at_runtime": False},
            {"name": "c", "type": "boolean", "default": True},
        ])[0]
        post = QueryDict("p_a=hello&p_b=hacked")  # c unchecked
        raw = ps.raw_from_post(params, post)
        self.assertEqual(raw, {"a": "hello", "b": "fixed", "c": False})
        values, errors = ps.clean_values(params, raw)
        self.assertFalse(errors)
        self.assertEqual(values["b"], "fixed")


def meta_of(script_path):
    """The metadata of a script (its comment block, or an old sidecar)."""
    data, _source, err = metadata.load_raw(Path(script_path), "0" * 12)
    assert err is None, err
    return data


class SampleArgumentTests(SimpleTestCase):
    """Argument styles of the sample scripts (the scripts themselves: runner/test_sample_scripts.py)."""

    def test_example_argument_styles(self):
        base = config.BASE_DIR / "sample_scripts"
        hello = ps.validate_metadata(meta_of(base / "basics/hello.sh"))["parameters"]
        argv, env = ps.build_arguments(hello, {"name": "Ada", "greeting": "Hi"})
        self.assertEqual(argv, ["Ada"])                     # positional
        self.assertEqual(env, {"GREETING": "Hi"})           # environment
        ping = ps.validate_metadata(meta_of(base / "network/ping_host.bat"))["parameters"]
        argv, _ = ps.build_arguments(ping, {"host": "10.0.0.1", "count": 3})
        self.assertEqual(argv, ["10.0.0.1", "3"])           # ordered positionals
        _, errs = ps.clean_values(ping, {"host": "a;b", "count": "3"})
        self.assertIn("host", errs)                         # metacharacters rejected by pattern


class PageTests(SimpleTestCase):
    """Drive the real views against a temp scripts directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.scripts = self.tmp / "scripts"
        self.scripts.mkdir()
        (self.scripts / "tool.py").write_bytes(b"print('hi')\n")
        self.original = (self.scripts / "tool.py").read_bytes()
        (self.scripts / "tool.json").write_text(json.dumps({
            "name": "Tool", "category": "Test",
            "parameters": [
                {"name": "db", "label": "Database", "type": "text", "default": "mydb", "required": True},
                {"name": "days", "type": "number", "default": 30, "min": 1, "max": 365, "required": True},
                {"name": "zip", "type": "boolean", "default": True},
                {"name": "secret", "type": "password"},
                {"name": "fixed", "type": "text", "default": "F", "editable_at_runtime": False},
            ]}))
        cfg_file = self.tmp / "config.json"
        cfg_file.write_text(json.dumps({"script_directories": [str(self.scripts)]}))
        patches = [
            mock.patch.object(config, "CONFIG_FILE", cfg_file),
            mock.patch.object(config, "METADATA_OVERRIDE_DIR", self.tmp / "ov"),
            mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets"),       # saved values live in runtime/
            mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups"),       # previous versions too
            mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        discovery.scan(force=True)
        self.addCleanup(discovery.scan, True)
        self.sid = discovery.scan()[0][0].id
        self.client = self.client_class(HTTP_HOST="localhost")

    def url(self, name):
        return f"/scripts/{self.sid}/{name}/"

    def test_detail_and_run_defaults(self):
        self.assertContains(self.client.get(self.url("parameters")), "Database")      # parameter definitions
        r = self.client.get(self.url("run"))
        self.assertContains(r, 'value="mydb"')
        self.assertContains(r, 'value="30"')
        self.assertContains(r, "Fixed")

    def test_run_validation_errors(self):
        r = self.client.post(self.url("run"), {"p_db": "", "p_days": "999"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "required", status_code=400)
        self.assertContains(r, "at most 365", status_code=400)

    def test_run_form_starts_directly_without_a_review_step(self):
        page = self.client.get(self.url("run"))
        self.assertContains(page, f'action="{self.url("start")}"')
        self.assertContains(page, 'name="submit_token"')
        self.assertNotContains(page, "Review")
        # Invalid values: the form again with the errors, nothing started.
        with mock.patch("runner.execution_views.manager.submit") as submit:
            r = self.client.post(self.url("start"), {"p_db": "", "p_days": "999", "submit_token": "x1"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "at most 365", status_code=400)
        submit.assert_not_called()

    def test_parameters_tab_and_overview_save_separately(self):
        page = self.client.get(self.url("parameters"))
        self.assertContains(page, 'id="param-list"')
        self.assertContains(page, "Saved values")
        self.assertNotContains(self.client.get(f"/scripts/{self.sid}/"), 'id="param-list"')   # not on Overview
        before = [p["name"] for p in meta_of(self.scripts / "tool.py")["parameters"]]
        # Overview saves the settings and keeps the parameter definitions.
        r = self.client.post(f"/scripts/{self.sid}/", {"name": "Settings only", "host": "local"})
        saved = meta_of(self.scripts / "tool.py")
        self.assertEqual(r["Location"], f"/scripts/{saved['id']}/")                 # the stable id from now on
        self.sid = saved["id"]
        self.assertEqual((saved["name"], [p["name"] for p in saved["parameters"]]), ("Settings only", before))
        # Parameters saves the definitions and keeps the settings.
        r = self.client.post(self.url("parameters"), {"parameters_json": json.dumps([{"name": "only", "type": "text"}])})
        self.assertEqual(r["Location"], self.url("parameters"))
        saved = meta_of(self.scripts / "tool.py")
        self.assertEqual((saved["name"], [p["name"] for p in saved["parameters"]]), ("Settings only", ["only"]))
        bad = self.client.post(self.url("parameters"), {"parameters_json": json.dumps([{"name": "!"}])})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual([p["name"] for p in meta_of(self.scripts / "tool.py")["parameters"]], ["only"])

    def test_configure_save_and_backup(self):
        params = [{"name": "only", "type": "select", "choices": ["x|X"], "pass_as": "flag"}]
        params = [{"name": "only", "label": "Only", "type": "select", "choices": [{"value": "x", "label": "X"}]}]
        r = self.client.post(self.url("configure"), {
            "name": "Renamed", "category": "Test", "timeout": "120", "enabled": "on",   # no longer saved
            "parameters_json": json.dumps(params)})
        self.assertEqual(r.status_code, 302)
        saved = meta_of(self.scripts / "tool.py")
        self.assertEqual(saved["name"], "Renamed")
        self.assertFalse({"timeout", "enabled", "allow_concurrent"} & set(saved))    # options no longer exist
        self.assertEqual([p["name"] for p in saved["parameters"]], ["only"])
        self.assertIn("# --- scripthub ---", (self.scripts / "tool.py").read_text())   # inside the script now
        self.assertFalse((self.scripts / "tool.json").exists())                        # the old sidecar moved in
        self.assertFalse((self.scripts / "tool.py.bak").exists())                     # nothing next to the script
        from runner import backups
        self.assertEqual(backups.read(saved["id"]), self.original)                     # runtime/backups/<id>/

    def test_first_save_writes_a_stable_id_and_old_links_and_data_follow(self):
        from runner import ids, presets
        old = self.sid
        presets.save_preset(discovery.get_script(old), "Prod", ps.validate_parameters([])[0], {}, "local")
        r = self.client.post(f"/scripts/{old}/", {"name": "Tool", "host": "local"})
        new = meta_of(self.scripts / "tool.py")["id"]
        self.assertTrue(ids.valid(new))
        self.assertNotEqual(new, old)
        self.assertEqual(r["Location"], f"/scripts/{new}/")
        self.assertEqual(ids.canonical(old), new)                                   # the old id is an alias
        self.assertEqual(self.client.get(f"/scripts/{old}/run/").status_code, 200)  # old links still work
        self.assertTrue((config.PRESETS_DIR / new / "prod.yaml").is_file())         # saved values moved over
        self.assertFalse((config.PRESETS_DIR / old).exists())
        self.assertEqual([p["name"] for p in presets.list_presets(discovery.get_script(new))], ["Prod"])
        second = (self.scripts / "tool.py").read_text()
        self.client.post(f"/scripts/{new}/", {"name": "Tool 2", "host": "local"})
        self.assertEqual(meta_of(self.scripts / "tool.py")["id"], new)            # written once, then kept
        self.assertNotEqual(second, (self.scripts / "tool.py").read_text())

    def test_a_moved_script_keeps_its_id_saved_values_backups_and_history(self):
        from runner import backups, history, presets
        self.client.post(f"/scripts/{self.sid}/", {"name": "Tool", "host": "local"})      # writes the stable id
        sid = meta_of(self.scripts / "tool.py")["id"]
        presets.save_preset(discovery.get_script(sid), "Prod", ps.validate_parameters([])[0], {}, "local")
        run_dir = config.EXECUTIONS_DIR / "RUN-20260101-001"
        run_dir.mkdir(parents=True)
        (run_dir / "metadata.json").write_text(json.dumps({"id": "RUN-20260101-001", "script_id": sid,
                                                           "script_name": "Tool", "status": "success"}))
        (self.scripts / "sub").mkdir()
        (self.scripts / "tool.py").rename(self.scripts / "sub" / "renamed.py")
        moved = discovery.get_script(sid, force=True)
        self.assertEqual(moved.path.name, "renamed.py")
        self.assertEqual([p["name"] for p in presets.list_presets(moved)], ["Prod"])
        self.assertTrue(backups.versions(sid))
        self.assertEqual([m["id"] for m in history.list_runs(script_id=sid)], ["RUN-20260101-001"])
        self.assertContains(self.client.get(f"/scripts/{sid}/"), "RUN-20260101-001")    # recent runs on Overview

    def test_a_copied_script_gets_a_new_id_on_its_next_save(self):
        self.client.post(f"/scripts/{self.sid}/", {"name": "Tool", "host": "local"})
        sid = meta_of(self.scripts / "tool.py")["id"]
        (self.scripts / "copy.py").write_bytes((self.scripts / "tool.py").read_bytes())
        scripts, problems = discovery.scan(force=True)
        copy = next(s for s in scripts if s.path.name == "copy.py")
        self.assertEqual(next(s for s in scripts if s.path.name == "tool.py").id, sid)   # the original keeps it
        self.assertNotEqual(copy.id, sid)
        self.assertTrue(copy.id_conflict.endswith("tool.py"))
        self.assertTrue(any("copy.py has the same id" in p for p in problems))
        self.assertContains(self.client.get("/scripts/"), "has the same id")            # the warning is shown
        self.client.post(f"/scripts/{copy.id}/", {"name": "Copy", "host": "local"})
        new = meta_of(self.scripts / "copy.py")["id"]
        self.assertNotEqual(new, sid)
        self.assertEqual(meta_of(self.scripts / "tool.py")["id"], sid)
        scripts, problems = discovery.scan(force=True)
        self.assertFalse(any("same id" in p for p in problems))
        self.assertEqual(len({s.id for s in scripts}), len(scripts))

    def test_configure_rejects_invalid(self):
        before = (self.scripts / "tool.json").read_text()
        script_before = (self.scripts / "tool.py").read_bytes()
        r = self.client.post(self.url("configure"), {
            "name": "X",
            "parameters_json": json.dumps([{"name": "a", "type": "number", "min": 9, "max": 1}])})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "min is greater than max", status_code=400)
        self.assertEqual((self.scripts / "tool.json").read_text(), before)
        self.assertEqual((self.scripts / "tool.py").read_bytes(), script_before)

    def test_editing_the_block_in_the_script_changes_the_metadata(self):
        self.client.post(f"/scripts/{self.sid}/", {"name": "Tool", "host": "local"})     # moves it into tool.py
        text = (self.scripts / "tool.py").read_text()
        self.assertIn("\n# name: Tool\n", text)                     # YAML, not JSON
        page = self.client.get(self.url("edit"))
        base_hash = page.content.decode().split('name="base_hash" value="', 1)[1].split('"', 1)[0]
        r = self.client.post(self.url("edit"), {"content": text.replace("# name: Tool\n", "# name: Edited in code\n"),
                                                "base_hash": base_hash, "action": "save"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(discovery.get_script(self.sid, force=True).name, "Edited in code")

    def test_script_pages_share_the_centre_and_put_tabs_on_the_right(self):
        self.assertRedirects(self.client.get(self.url("configure")), f"/scripts/{self.sid}/")   # merged into Overview
        for page in ("", "run"):
            html = self.client.get(self.url(page) if page else f"/scripts/{self.sid}/").content.decode()
            for panel in ('id="doc-code-panel"', 'id="saved-panel"'):
                self.assertIn(panel, html, f"{page or 'overview'}: {panel}")
            self.assertNotIn('id="doc-meta-panel"', html)                 # metadata is in the script now
            centre, side = html.split('class="split-side"', 1)
            self.assertNotIn('class="wb-tabs', centre, page)               # no tab strip above the centre
            self.assertIn('class="wb-tabs side-tabs"', side, page)          # it heads the right column


    def test_broken_metadata_blocks_run_and_can_be_repaired(self):
        (self.scripts / "tool.json").write_text("{ broken")
        discovery.scan(force=True)
        self.assertContains(self.client.get(self.url("run")), "Invalid metadata file")
        # A block in the script wins over an old sidecar: writing one repairs the script.
        (self.scripts / "tool.py").write_bytes(b'# --- scripthub ---\n# {"name": "Fixed", "parameters": []}\n# --- end scripthub ---\nprint(1)\n')
        discovery.scan(force=True)
        self.assertNotContains(self.client.get(self.url("run")), "Invalid metadata")
        self.assertEqual(discovery.get_script(self.sid).name, "Fixed")

    def test_old_enabled_and_timeout_keys_are_ignored(self):
        meta = meta_of(self.scripts / "tool.py")
        (self.scripts / "tool.json").write_text(json.dumps({**meta, "enabled": False, "timeout": 5, "allow_concurrent": True}))
        discovery.scan(force=True)
        page = self.client.get(self.url("run"))
        self.assertNotContains(page, "is disabled")
        with mock.patch("runner.execution_views.manager.submit", return_value="RUN-20260101-001") as submit:
            r = self.client.post(self.url("start"), {"p_db": "x", "p_days": "1", "submit_token": "t-old-keys"})
        self.assertEqual(r.status_code, 302)                                          # runs although "enabled": false
        self.assertEqual(submit.call_args[0][0].timeout, config.load_config()["default_timeout"])   # Settings default
        self.assertEqual(self.client.post(f"/scripts/{self.sid}/toggle/", {"enabled": "0"}).status_code, 404)

    def test_source_view_download_and_escaping(self):
        (self.scripts / "tool.py").write_text("print('<b>hi</b>')\n# second line\n")
        r = self.client.get(self.url("source"))
        self.assertContains(r, "&lt;b&gt;hi&lt;/b&gt;")      # HTML-escaped, never executed as markup
        self.assertContains(r, "second line")
        self.assertContains(r, "tool.json")                    # an old sidecar still shows as a tab
        d = self.client.get(self.url("source") + "?download=1")
        self.assertEqual(d["Content-Type"].split(";")[0], "text/plain")
        self.assertIn("attachment", d["Content-Disposition"])

    def test_source_view_unknown_script_and_binary(self):
        self.assertEqual(self.client.get("/scripts/doesnotexist/source/").status_code, 404)
        (self.scripts / "tool.py").write_bytes(b"\x00\x01\x02binary")
        self.assertContains(self.client.get(self.url("source")), "looks binary")

    def test_library_has_cards_and_table_without_switches(self):
        r = self.client.get("/scripts/")
        self.assertContains(r, 'id="view-cards"')
        self.assertContains(r, 'id="view-table"')
        self.assertNotContains(r, 'role="switch"')                  # scripts can no longer be disabled
        self.assertContains(r, "lang-py")

    def test_source_page_loads_highlighter_with_language(self):
        r = self.client.get(self.url("source"))
        self.assertContains(r, 'data-lang="python"')
        self.assertContains(r, "js/highlight.js")

    def test_language_and_platform_defaults_by_file_type(self):
        (self.scripts / "a.sh").write_text("echo hi\n")
        (self.scripts / "b.bat").write_text("@echo off\n")
        config_file = config.CONFIG_FILE
        cfg = json.loads(config_file.read_text())
        cfg["interpreters"] = {".py": "python", ".sh": "bash", ".bat": "cmd"}
        config_file.write_text(json.dumps(cfg))
        by_name = {s.path.name: s for s in discovery.scan(force=True)[0]}
        self.assertEqual((by_name["tool.py"].language, by_name["tool.py"].platforms), ("python", ["windows", "linux", "macos"]))
        self.assertEqual((by_name["a.sh"].language, by_name["a.sh"].platforms), ("shell", ["linux", "macos"]))
        self.assertEqual((by_name["b.bat"].language, by_name["b.bat"].platforms), ("batch", ["windows"]))

    def test_library_renders_filter_rows_with_present_options_only(self):
        (self.scripts / "a.sh").write_text("echo hi\n")
        cfg = json.loads(config.CONFIG_FILE.read_text())
        cfg["interpreters"] = {".py": "python", ".sh": "bash"}
        config.CONFIG_FILE.write_text(json.dumps(cfg))
        discovery.scan(force=True)
        r = self.client.get("/scripts/")
        for key in ("category", "platform", "language"):
            self.assertContains(r, f'data-filter="{key}"')
        self.assertContains(r, '<option value="python">')
        self.assertContains(r, '<option value="shell">')
        self.assertNotContains(r, '<option value="batch">')   # no batch scripts present
        self.assertContains(r, 'data-platform="linux macos"')  # the .sh card

    def test_platform_override_is_saved_only_when_different_from_default(self):
        base = {"name": "Tool", "parameters_json": "[]"}
        self.client.post(self.url("configure"), {**base, "platforms": ["linux"]})
        self.assertEqual(meta_of(self.scripts / "tool.py")["platforms"], ["linux"])
        self.assertEqual(discovery.scan(force=True)[0][0].platforms, ["linux"])
        self.client.post(self.url("configure"), {**base, "platforms": ["windows", "linux", "macos"]})
        self.assertNotIn("platforms", meta_of(self.scripts / "tool.py"))

    def test_configure_saves_host_working_directory_and_environment(self):
        base = {"name": "Tool", "parameters_json": "[]"}
        r = self.client.post(self.url("configure"), {**base, "host": "prod-1", "working_directory": "/srv/app",
                                                       "environment": "A=1\nB=two words\n# comment"})
        self.assertEqual(r.status_code, 302)
        saved = meta_of(self.scripts / "tool.py")
        self.assertEqual((saved["host"], saved["working_directory"]), ("prod-1", "/srv/app"))
        self.assertEqual(saved["environment"], {"A": "1", "B": "two words"})
        bad = self.client.post(self.url("configure"), {**base, "environment": "NOEQUALS"})
        self.assertContains(bad, "must look like NAME=value", status_code=400)
        bad = self.client.post(self.url("configure"), {**base, "host": "-oProxyCommand=x"})
        self.assertEqual(bad.status_code, 400)

