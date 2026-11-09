"""The sample scripts (sample_scripts/) and sample projects (sample_projects/*.scripthub).

python manage.py test runner.test_sample_scripts
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest import mock, skipUnless

from django.test import SimpleTestCase

import config
from runner import discovery, parameter_service as ps, project_files, sequences, workspace

SAMPLES = config.BASE_DIR / "sample_scripts"
PROJECTS = config.BASE_DIR / "sample_projects"


def run(rel, *args, cwd=None, env=None):
    """Run a Python sample with arguments; returns CompletedProcess (text)."""
    path = SAMPLES / rel
    return subprocess.run([sys.executable, str(path), *args], cwd=cwd or path.parent, capture_output=True,
                          text=True, timeout=60, env={**os.environ, **(env or {})})


def default_argv(rel, **overrides):
    """argv and env the way ScriptHub builds them from the defaults (+ overrides, raw form values)."""
    script = discovery.load_file(str(SAMPLES / rel))
    params = script.meta["parameters"]
    raw = {**ps.defaults_raw(params), **overrides}
    values, errors = ps.clean_values(params, raw)
    assert not errors, errors
    return ps.build_arguments(params, values)


class TempCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-samples-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


class MetadataTests(SimpleTestCase):
    def scripts(self):
        return [p for p in sorted(SAMPLES.rglob("*")) if p.suffix in (".py", ".sh", ".bat")]

    def test_every_sample_has_valid_metadata_a_category_and_a_description(self):
        self.assertGreaterEqual(len(self.scripts()), 15)
        cfg = config.load_config()           # .bat counts as a script on Linux too (its metadata is checked)
        cfg = {**cfg, "interpreters": {**cfg["interpreters"], ".py": "python", ".sh": "bash", ".bat": "cmd"}}
        patcher = mock.patch.object(config, "load_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        for path in self.scripts():
            with self.subTest(script=path.relative_to(SAMPLES).as_posix()):
                s = discovery.load_file(str(path))
                self.assertTrue(s is not None and s.meta_valid, getattr(s, "meta_error", "not loaded"))
                self.assertTrue(s.meta.get("description") and s.meta.get("category"))
                self.assertNotIn("id", s.meta)          # ids are given on first save, per copy

    def test_folders_stay_within_discovery_depth_and_line_endings_are_right(self):
        for path in self.scripts():
            self.assertLessEqual(len(path.relative_to(SAMPLES).parts) - 1, discovery.MAX_DEPTH)
            data = path.read_bytes()
            if path.suffix == ".bat":
                self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), path.name)
            else:
                self.assertNotIn(b"\r\n", data, path.name)

    def test_the_parameter_tour_uses_every_type_and_way_of_passing(self):
        s = discovery.load_file(str(SAMPLES / "basics" / "parameter_tour.py"))
        params = s.meta["parameters"]
        self.assertEqual({p["type"] for p in params}, set(ps.TYPES))
        self.assertEqual({p.get("pass_as", "flag") for p in params}, set(ps.PASS_AS))


class PythonSampleTests(TempCase):
    def test_every_python_sample_with_defaults_or_simple_values(self):
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "a.txt").write_text("hello")
        needs = {
            "files/clean_old_files.py": {"folder": str(self.tmp / "src")},
            "files/backup_folder.py": {"source": str(self.tmp / "src"), "destination": str(self.tmp / "bak")},
            "files/file_checksum.py": {"file": str(self.tmp / "src" / "a.txt")},
            "system/disk_space.py": {"max_used": "100"},
            "system/log_message.py": {"log_file": str(self.tmp / "messages.log")},
            "basics/outcome.py": {"seconds": "0"},
        }
        skip = {"network/check_port.py", "network/http_check.py"}          # tested offline below
        for path in sorted(SAMPLES.rglob("*.py")):
            rel = path.relative_to(SAMPLES).as_posix()
            if rel in skip:
                continue
            with self.subTest(script=rel):
                argv, env = default_argv(rel, **needs.get(rel, {}))
                r = run(rel, *argv, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(r.stdout.strip())

    def test_parameter_tour_shows_what_arrives(self):
        argv, env = default_argv("basics/parameter_tour.py", api_token="s3cret")
        r = run("basics/parameter_tour.py", *argv, env=env)
        self.assertIn("'demo'", r.stdout)
        self.assertIn("['logs', 'metrics']", r.stdout)
        self.assertIn("'dev'", r.stdout)
        self.assertIn("******", r.stdout)
        self.assertNotIn("s3cret", r.stdout)

    def test_outcome_failure_uses_the_exit_code(self):
        r = run("basics/outcome.py", "--outcome", "failure", "--seconds", "0", "--exit-code", "7")
        self.assertEqual(r.returncode, 7)

    def test_clean_old_files_lists_by_default_and_deletes_only_old_matches(self):
        old, new, other = self.tmp / "old.log", self.tmp / "new.log", self.tmp / "old.txt"
        for p in (old, new, other):
            p.write_text("x")
        past = time.time() - 40 * 86400
        os.utime(old, (past, past))
        os.utime(other, (past, past))
        r = run("files/clean_old_files.py", "--folder", str(self.tmp), "--days", "30")
        self.assertIn("old.log", r.stdout)
        self.assertTrue(old.exists())
        r = run("files/clean_old_files.py", "--folder", str(self.tmp), "--days", "30", "--delete")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((old.exists(), new.exists(), other.exists()), (False, True, True))

    def test_clean_old_files_refuses_a_root(self):
        root = Path(os.path.abspath(os.sep))
        self.assertNotEqual(run("files/clean_old_files.py", "--folder", str(root)).returncode, 0)

    def test_backup_keeps_the_newest_and_refuses_a_target_inside_the_source(self):
        src = self.tmp / "notes"
        src.mkdir()
        (src / "a.txt").write_text("a")
        dest = self.tmp / "bak"
        dest.mkdir()
        for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
            (dest / f"notes-{stamp}.zip").write_bytes(b"old")
        r = run("files/backup_folder.py", "--source", str(src), "--destination", str(dest), "--keep", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(p.name for p in dest.iterdir())
        self.assertEqual(len(left), 2)
        self.assertIn("notes-20200103-000000.zip", left)
        with zipfile.ZipFile(dest / left[-1]) as z:
            self.assertEqual(z.read("a.txt"), b"a")
        r = run("files/backup_folder.py", "--source", str(src), "--destination", str(src / "inner"))
        self.assertNotEqual(r.returncode, 0)

    def test_checksum_mismatch_fails(self):
        f = self.tmp / "f.bin"
        f.write_bytes(b"abc")
        ok = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertEqual(run("files/file_checksum.py", "--file", str(f), "--expected", ok).returncode, 0)
        self.assertEqual(run("files/file_checksum.py", "--file", str(f), "--expected", "00").returncode, 1)

    def test_data_samples_read_their_files_next_to_them(self):
        r = run("data/csv_summary.py", "--file", "sales.csv", "--group-by", "product")
        self.assertIn("Laptop", r.stdout)
        r = run("data/log_search.py", "--file", "app.log", "--pattern", "CRITICAL", "--fail-if-found")
        self.assertEqual(r.returncode, 1)
        self.assertIn("database connection lost", r.stdout)
        r = run("data/csv_summary.py", "--file", "sales.csv", "--group-by", "nope")
        self.assertNotEqual(r.returncode, 0)

    def test_network_checks_against_a_local_server(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<h1>ScriptHub test page</h1>"
                self.send_response(200 if self.path == "/" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = str(server.server_address[1])
        url = f"http://127.0.0.1:{port}/"
        self.assertEqual(run("network/check_port.py", "--host", "127.0.0.1", "--port", port).returncode, 0)
        self.assertEqual(run("network/http_check.py", "--url", url, "--contains", "test page").returncode, 0)
        self.assertEqual(run("network/http_check.py", "--url", url, "--contains", "missing").returncode, 1)
        self.assertEqual(run("network/http_check.py", "--url", url + "x").returncode, 1)
        with socket.socket() as s:                      # a port that is surely closed
            s.bind(("127.0.0.1", 0))
            closed = str(s.getsockname()[1])
        r = run("network/check_port.py", "--host", "127.0.0.1", "--port", closed, "--timeout", "2")
        self.assertEqual(r.returncode, 1)


@skipUnless(os.name == "nt", "batch files run on Windows only")
class BatchSampleTests(TempCase):
    def bat(self, rel, *args, env=None):
        return subprocess.run(["cmd", "/c", str(SAMPLES / rel), *args], capture_output=True, text=True,
                              timeout=60, env={**os.environ, **(env or {})})

    def test_batch_samples(self):
        self.assertIn("Hello, Ada!", self.bat("basics/hello.bat", "Ada").stdout)
        r = self.bat("system/list_directory.bat", str(SAMPLES / "system"), env={"LIST_MODE": "brief"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("list_directory.bat", r.stdout)
        self.assertNotEqual(self.bat("system/list_directory.bat", str(self.tmp / "nope")).returncode, 0)


class SampleProjectTests(TempCase):
    def setUp(self):
        super().setUp()
        runtime = self.tmp / "runtime"
        runtime.mkdir()
        cfg = runtime / "config.json"
        cfg.write_text(json.dumps({"default_timeout": 60}))
        for p in (mock.patch.object(config, "RUNTIME_DIR", runtime),
                  mock.patch.object(config, "CONFIG_FILE", cfg),
                  mock.patch.object(config, "EXECUTIONS_DIR", runtime / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", runtime / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", runtime / "backups"),
                  mock.patch.object(config, "METADATA_OVERRIDE_DIR", runtime / "overrides"),
                  mock.patch.object(config, "SEQUENCES_DIR", runtime / "sequences"),
                  mock.patch.object(config, "SEQUENCE_RUNS_DIR", runtime / "sequence_runs")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: discovery.scan(force=True))

    def projects(self):
        found = sorted(PROJECTS.glob("*" + project_files.EXT))
        self.assertGreaterEqual(len(found), 3)
        return found

    def test_projects_hold_the_current_sample_scripts(self):
        """Fails when a sample script changed: run python tools/build_sample_projects.py"""
        for path in self.projects():
            with zipfile.ZipFile(path) as z:
                manifest = json.loads(z.read("manifest.json"))
                for f in manifest["files"]:
                    with self.subTest(project=path.name, file=f["path"]):
                        self.assertEqual(z.read("files/" + f["path"]), (SAMPLES / f["path"]).read_bytes(),
                                         "outdated: run python tools/build_sample_projects.py")
                self.assertTrue(manifest["sequences"] and manifest["presets"])

    def test_every_project_opens_and_its_sequences_find_their_scripts(self):
        from runner import sequence_runner
        for n, path in enumerate(self.projects()):
            with self.subTest(project=path.name):
                preview = project_files.inspect(path)
                self.assertEqual(preview["warnings"], [])
                result = project_files.install(path, str(self.tmp / f"p{n}"))
                workspace.open_folder(result["folder"])
                discovery.scan(force=True)
                for slug in result["sequences"]:
                    _items, errors = sequence_runner.prepare(sequences.load(slug))
                    self.assertEqual(errors, [])
                self.assertGreater(result["presets"], 0)
        workspace.close_folder()

    def test_home_opens_the_sample_folder_and_sample_projects_without_choosing_a_file(self):
        home = self.client.get("/")
        self.assertContains(home, str(SAMPLES))
        for path in self.projects():
            self.assertContains(home, f'name="name" value="{path.stem}"')
        self.client.post("/folder/open/", {"path": str(SAMPLES)})
        self.assertEqual(workspace.current(), SAMPLES)

        page = self.client.post("/project/sample/", {"name": "Getting started"})
        self.assertContains(page, "Try a sequence")                       # the preview, nothing written yet
        suggested = Path(page.context["destination"])
        self.assertFalse(project_files._inside(suggested, config.BASE_DIR))   # never inside the code folder
        self.assertEqual(suggested.parent, Path.home() / "ScriptHub projects")
        self.assertContains(page, 'data-pick-into="Getting started"')       # Browse… picks where it goes
        token = page.context["token"]
        dest = self.tmp / "Getting started"
        r = self.client.post("/project/open/install/", {"token": token, "destination": str(dest)})
        self.assertEqual(r.status_code, 302)
        self.assertTrue((dest / "basics" / "hello.py").is_file())
        self.assertEqual(workspace.current(), dest)
        self.assertEqual([s["name"] for s in sequences.list_sequences()], ["Try a sequence"])

        for bad in ("../README", "nope", ""):
            r = self.client.post("/project/sample/", {"name": bad})
            self.assertEqual(r["Location"], "/")
        self.assertEqual(self.client.get("/project/sample/").status_code, 405)
        workspace.close_folder()
