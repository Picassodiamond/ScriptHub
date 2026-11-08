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
            ps.validate_metadata({"timeout": 0, "enabled": "yes", "parameters": [{"name": "!"}]})
        self.assertGreaterEqual(len(cm.exception.errors), 3)


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


class BundledExamplesTests(SimpleTestCase):
    def test_all_bundled_metadata_is_valid(self):
        files = sorted((config.BASE_DIR / "scripts").rglob("*.json"))
        self.assertGreaterEqual(len(files), 5)
        for f in files:
            with self.subTest(file=f.name):
                data = json.loads(f.read_text(encoding="utf-8"))
                ps.validate_metadata(data)  # raises on problems

    def test_example_argument_styles(self):
        base = config.BASE_DIR / "scripts"
        health = ps.validate_metadata(json.loads((base / "deployment/health_check.json").read_text()))["parameters"]
        argv, env = ps.build_arguments(health, {"url": "https://x.test/h", "timeout_secs": 7})
        self.assertEqual(argv, ["https://x.test/h"])        # positional
        self.assertEqual(env, {"TIMEOUT_SECS": "7"})        # environment
        ping = ps.validate_metadata(json.loads((base / "system/ping_host.json").read_text()))["parameters"]
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
        (self.scripts / "tool.py").write_text("print('hi')\n")
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
        self.assertContains(self.client.get(f"/scripts/{self.sid}/"), "Database")
        r = self.client.get(self.url("run"))
        self.assertContains(r, 'value="mydb"')
        self.assertContains(r, 'value="30"')
        self.assertContains(r, "Fixed")

    def test_run_validation_errors(self):
        r = self.client.post(self.url("run"), {"p_db": "", "p_days": "999"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "required", status_code=400)
        self.assertContains(r, "at most 365", status_code=400)

    def test_run_confirmation_masks_password(self):
        r = self.client.post(self.url("run"), {"p_db": "prod", "p_days": "7", "p_secret": "topsecret", "p_zip": "on"})
        self.assertContains(r, "Confirm")
        self.assertContains(r, "--db prod")
        self.assertContains(r, "--zip")
        self.assertNotContains(r, "<td>topsecret")
        # raw value is carried for the next step only inside a hidden field
        self.assertContains(r, 'name="p_secret"')

    def test_configure_save_and_backup(self):
        params = [{"name": "only", "type": "select", "choices": ["x|X"], "pass_as": "flag"}]
        params = [{"name": "only", "label": "Only", "type": "select", "choices": [{"value": "x", "label": "X"}]}]
        r = self.client.post(self.url("configure"), {
            "name": "Renamed", "category": "Test", "enabled": "on", "timeout": "120",
            "parameters_json": json.dumps(params)})
        self.assertEqual(r.status_code, 302)
        saved = json.loads((self.scripts / "tool.json").read_text())
        self.assertEqual(saved["name"], "Renamed")
        self.assertEqual(saved["timeout"], 120)
        self.assertEqual([p["name"] for p in saved["parameters"]], ["only"])
        bak = json.loads((self.scripts / "tool.json.bak").read_text())
        self.assertEqual(bak["name"], "Tool")  # previous valid version kept

    def test_configure_rejects_invalid(self):
        before = (self.scripts / "tool.json").read_text()
        r = self.client.post(self.url("configure"), {
            "name": "X", "enabled": "on",
            "parameters_json": json.dumps([{"name": "a", "type": "number", "min": 9, "max": 1}])})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "min is greater than max", status_code=400)
        self.assertEqual((self.scripts / "tool.json").read_text(), before)

    def test_raw_json_invalid_and_valid(self):
        before = (self.scripts / "tool.json").read_text()
        r = self.client.post(self.url("configure/raw"), {"raw_json": "{not json"})
        self.assertContains(r, "Invalid JSON", status_code=400)
        self.assertEqual((self.scripts / "tool.json").read_text(), before)
        r = self.client.post(self.url("configure/raw"), {"raw_json": json.dumps({"name": "Via raw", "parameters": []})})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(json.loads((self.scripts / "tool.json").read_text())["name"], "Via raw")

    def test_script_pages_share_the_centre_and_put_tabs_on_the_right(self):
        for page in ("", "run", "configure"):
            html = self.client.get(self.url(page) if page else f"/scripts/{self.sid}/").content.decode()
            for panel in ('id="doc-code-panel"', 'id="doc-meta-panel"', 'id="doc-params-panel"', 'name="raw_json"'):
                self.assertIn(panel, html, f"{page or 'overview'}: {panel}")
            centre, side = html.split('class="split-side"', 1)
            self.assertNotIn('class="wb-tabs', centre, page)               # no tab strip above the centre
            self.assertIn('class="wb-tabs side-tabs"', side, page)          # it heads the right column

    def test_raw_json_save_returns_to_the_page_it_came_from(self):
        body = {"raw_json": json.dumps({"name": "Back", "parameters": []})}
        r = self.client.post(self.url("configure/raw"), {**body, "back": "script_detail"})
        self.assertEqual(r["Location"], f"/scripts/{self.sid}/")
        r = self.client.post(self.url("configure/raw"), {**body, "back": "https://evil.example/"})
        self.assertEqual(r["Location"], self.url("configure"))

    def test_broken_metadata_blocks_run_and_can_be_repaired(self):
        (self.scripts / "tool.json").write_text("{ broken")
        discovery.scan(force=True)
        self.assertContains(self.client.get(self.url("run")), "Invalid metadata file")
        self.assertContains(self.client.get(self.url("configure")), "{ broken")
        r = self.client.post(self.url("configure/raw"), {"raw_json": '{"name": "Fixed", "parameters": []}'})
        self.assertEqual(r.status_code, 302)
        self.assertNotContains(self.client.get(self.url("run")), "Invalid metadata")

    def test_disabled_script_cannot_run(self):
        metadata.set_enabled(self.scripts / "tool.py", self.sid, False)
        discovery.scan(force=True)
        self.assertContains(self.client.get(self.url("run")), "disabled")
        r = self.client.post(self.url("run"), {"p_db": "x", "p_days": "1"})
        self.assertNotContains(r, "Command preview")

    def test_source_view_download_and_escaping(self):
        (self.scripts / "tool.py").write_text("print('<b>hi</b>')\n# second line\n")
        r = self.client.get(self.url("source"))
        self.assertContains(r, "&lt;b&gt;hi&lt;/b&gt;")      # HTML-escaped, never executed as markup
        self.assertContains(r, "second line")
        self.assertContains(r, "tool.json")                    # metadata tab
        d = self.client.get(self.url("source") + "?download=1")
        self.assertEqual(d["Content-Type"].split(";")[0], "text/plain")
        self.assertIn("attachment", d["Content-Disposition"])

    def test_source_view_unknown_script_and_binary(self):
        self.assertEqual(self.client.get("/scripts/doesnotexist/source/").status_code, 404)
        (self.scripts / "tool.py").write_bytes(b"\x00\x01\x02binary")
        self.assertContains(self.client.get(self.url("source")), "looks binary")

    def test_library_has_cards_table_and_switches(self):
        r = self.client.get("/scripts/")
        self.assertContains(r, 'id="view-cards"')
        self.assertContains(r, 'id="view-table"')
        self.assertContains(r, 'role="switch"')
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
        base = {"name": "Tool", "enabled": "on", "parameters_json": "[]"}
        self.client.post(self.url("configure"), {**base, "platforms": ["linux"]})
        self.assertEqual(json.loads((self.scripts / "tool.json").read_text())["platforms"], ["linux"])
        self.assertEqual(discovery.scan(force=True)[0][0].platforms, ["linux"])
        self.client.post(self.url("configure"), {**base, "platforms": ["windows", "linux", "macos"]})
        self.assertNotIn("platforms", json.loads((self.scripts / "tool.json").read_text()))

    def test_configure_saves_host_working_directory_and_environment(self):
        base = {"name": "Tool", "enabled": "on", "parameters_json": "[]"}
        r = self.client.post(self.url("configure"), {**base, "host": "prod-1", "working_directory": "/srv/app",
                                                       "environment": "A=1\nB=two words\n# comment"})
        self.assertEqual(r.status_code, 302)
        saved = json.loads((self.scripts / "tool.json").read_text())
        self.assertEqual((saved["host"], saved["working_directory"]), ("prod-1", "/srv/app"))
        self.assertEqual(saved["environment"], {"A": "1", "B": "two words"})
        bad = self.client.post(self.url("configure"), {**base, "environment": "NOEQUALS"})
        self.assertContains(bad, "must look like NAME=value", status_code=400)
        bad = self.client.post(self.url("configure"), {**base, "host": "-oProxyCommand=x"})
        self.assertEqual(bad.status_code, 400)

