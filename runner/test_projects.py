"""Project files (.scripthub): save a folder with its sequences and presets, open it elsewhere, and refuse unsafe files.

python manage.py test runner.test_projects
"""
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import discovery, presets, project_files, sequences, workspace
from runner.project_files import ProjectError

TOOL = b"import sys\nprint('hello', sys.argv[1:])\n"
WITH_ID = (b"# --- scripthub ---\n# {\"id\": \"a1b2c3d4e5f6\", \"name\": \"Alpha\", \"parameters\": "
           b"[{\"name\": \"tag\", \"type\": \"text\", \"default\": \"x\"}]}\n# --- end scripthub ---\n") + TOOL


class ProjectCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-proj-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        runtime = self.tmp / "runtime"
        runtime.mkdir()
        cfg = runtime / "config.json"
        cfg.write_text(json.dumps({"interpreters": {".py": "python", ".sh": "bash"}, "default_timeout": 60}))
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
        # the folder to save: two scripts (one with a stable id, one with an old JSON sidecar), helpers, secrets
        self.src = self.tmp / "ops"
        (self.src / "deploy").mkdir(parents=True)
        (self.src / "data").mkdir()
        (self.src / "__pycache__").mkdir()
        (self.src / ".git").mkdir()
        (self.src / "alpha.py").write_bytes(WITH_ID)
        (self.src / "deploy" / "beta.sh").write_bytes(b"#!/bin/sh\necho beta\n")
        (self.src / "deploy" / "beta.json").write_text(json.dumps({"name": "Beta", "parameters": []}))
        (self.src / "data" / "hosts.csv").write_bytes(b"name,ip\nweb,10.0.0.1\n")
        (self.src / "README.md").write_bytes(b"# ops\n")
        (self.src / ".env").write_bytes(b"PASSWORD=secret\n")
        (self.src / "server.pem").write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
        (self.src / "__pycache__" / "alpha.cpython-312.pyc").write_bytes(b"\0")
        (self.src / ".git" / "HEAD").write_bytes(b"ref\n")
        workspace.open_folder(str(self.src))
        discovery.scan(force=True)

    def script(self, name):
        return next(s for s in discovery.scan()[0] if s.path.name == name)

    def make_sequence(self, name="Release"):
        slug = sequences.create(name)
        a, b = self.script("alpha.py"), self.script("beta.sh")
        sequences.add_step(slug, sequences.new_step(a, {"tag": "prod"}))
        sequences.add_step(slug, sequences.new_step(b, {}))
        sequences.link(slug, "s1", "s2", "success")
        presets.save_preset(a, "Nightly", a.meta["parameters"], {"tag": "night"}, "local")
        return slug

    def save(self, slugs, files=None):
        included, _left = project_files.folder_files(self.src)
        chosen = files if files is not None else [f[0] for f in included]
        fh, report = project_files.build(self.src, "Ops project", chosen, slugs)
        out = self.tmp / "ops.scripthub"
        out.write_bytes(fh.read())
        fh.close()
        return out, report


class SaveAndOpenTests(ProjectCase):
    def test_folder_files_leave_out_secrets_and_tool_folders(self):
        included, left_out = project_files.folder_files(self.src)
        names = {f[0] for f in included}
        self.assertEqual(names, {"alpha.py", "deploy/beta.sh", "deploy/beta.json", "data/hosts.csv", "README.md"})
        self.assertEqual({f[0] for f in included if f[2]}, {"alpha.py", "deploy/beta.sh"})
        reasons = dict(left_out)
        self.assertEqual(reasons[".env"], "may hold secrets")
        self.assertEqual(reasons["server.pem"], "may hold secrets")
        self.assertIn("__pycache__/", reasons)
        self.assertIn(".git/", reasons)

    def test_round_trip_to_a_new_folder(self):
        slug = self.make_sequence()
        pfile, report = self.save([slug])
        self.assertEqual((report["files"], report["scripts"], report["sequences"], report["presets"]), (5, 2, 1, 1))
        with zipfile.ZipFile(pfile) as z:
            names = set(z.namelist())
            manifest = json.loads(z.read("manifest.json"))
            seq_text = z.read(f"sequences/{slug}.yaml").decode()
        self.assertIn("files/alpha.py", names)
        self.assertFalse(any(".env" in n or ".pem" in n or "__pycache__" in n for n in names))
        self.assertEqual(manifest["format"], "scripthub-project")
        self.assertIn("path: files/deploy/beta.sh", seq_text)                 # paths inside the project

        # "another computer": nothing of the sequence or presets left, the files open elsewhere
        sequences.delete(slug)
        shutil.rmtree(config.PRESETS_DIR)
        old_beta_id = self.script("beta.sh").id
        preview = project_files.inspect(pfile)
        self.assertEqual((preview["scripts"], preview["files"], preview["presets"]), (2, 5, 1))
        self.assertEqual(preview["sequences"], [{"name": "Release", "as": "Release", "steps": 2}])
        dest = self.tmp / "elsewhere" / "Ops"
        result = project_files.install(pfile, str(dest))
        self.assertEqual((result["files"], len(result["sequences"]), result["presets"]), (5, 1, 1))
        self.assertEqual((dest / "data" / "hosts.csv").read_bytes(), b"name,ip\nweb,10.0.0.1\n")
        self.assertFalse((dest / ".env").exists())

        workspace.open_folder(str(dest))
        discovery.scan(force=True)
        data = sequences.load(result["sequences"][0])
        found = [sequences.resolve(s) for s in data["steps"]]
        self.assertEqual([s.path for s in found], [(dest / "alpha.py").resolve(), (dest / "deploy" / "beta.sh").resolve()])
        self.assertEqual(data["steps"][0]["script"], "a1b2c3d4e5f6")          # the stable id travels
        self.assertNotEqual(data["steps"][1]["script"], old_beta_id)          # a path id follows the new path
        self.assertEqual(data["steps"][1]["script"], found[1].id)
        self.assertEqual(data["steps"][0]["values"], {"tag": "prod"})
        self.assertEqual(data["links"], [{"from": "s1", "to": "s2", "when": "success"}])
        self.assertEqual([p["name"] for p in presets.list_presets(found[0])], ["Nightly"])

        # opened twice: the name is taken, the destination must be new or empty
        with self.assertRaises(ProjectError):
            project_files.install(pfile, str(dest))
        again = project_files.install(pfile, str(self.tmp / "second"))
        self.assertEqual(sequences.load(again["sequences"][0])["name"], "Release (2)")
        self.assertIn("exists already", " ".join(again["kept"]))             # the preset there is kept

    def test_scripts_a_sequence_needs_are_always_included(self):
        slug = self.make_sequence()
        outside = self.tmp / "elsewhere-scripts"
        outside.mkdir()
        (outside / "gamma.py").write_bytes(TOOL)
        gamma = discovery.load_file(str(outside / "gamma.py"))
        sequences.add_step(slug, sequences.new_step(gamma, {}))
        pfile, report = self.save([slug], files=["README.md"])                  # scripts not ticked
        with zipfile.ZipFile(pfile) as z:
            names = set(z.namelist())
        self.assertTrue({"files/alpha.py", "files/deploy/beta.sh", "files/_outside/gamma.py"} <= names)
        self.assertEqual(report["outside"], [str((outside / "gamma.py").resolve())])

    def test_preview_warns_about_hosts_and_paths_of_this_computer(self):
        slug = self.make_sequence()
        sequences.update_step(slug, "s1", values={"tag": "C:\\nowhere\\at\\all"})
        data = sequences.load(slug)
        data["steps"][1]["host"] = "web-9"
        sequences._write(slug, data)
        pfile, _report = self.save([slug])
        warnings = " | ".join(project_files.inspect(pfile)["warnings"])
        self.assertIn("host 'web-9' is not in this ScriptHub's settings", warnings)
        self.assertIn("tag = C:\\nowhere\\at\\all does not exist on this computer", warnings)


class UnsafeFileTests(ProjectCase):
    def zip_with(self, entries, manifest=None, symlink=None):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
            if symlink:
                info = zipfile.ZipInfo(symlink)
                info.external_attr = (0o120777 << 16)
                z.writestr(info, "/etc/passwd")
            if manifest is not False:
                z.writestr("manifest.json", json.dumps(manifest or {
                    "format": "scripthub-project", "version": 1, "files": [], "sequences": [], "presets": []}))
        path = self.tmp / "bad.scripthub"
        path.write_bytes(buf.getvalue())
        return path

    def test_unsafe_or_damaged_files_are_refused(self):
        cases = {
            "parent": self.zip_with({"files/../evil.py": b"x"}),
            "absolute": self.zip_with({"/files/evil.py": b"x"}),
            "drive": self.zip_with({"C:/files/evil.py": b"x"}),
            "backslash": self.zip_with({"files\\..\\evil.py": b"x"}),
            "unexpected": self.zip_with({"other/evil.py": b"x"}),
            "link": self.zip_with({}, symlink="files/link.py"),
            "no manifest": self.zip_with({"files/a.py": b"x"}, manifest=False),
            "other format": self.zip_with({}, manifest={"format": "zip", "version": 1, "files": [], "sequences": [], "presets": []}),
            "newer": self.zip_with({}, manifest={"format": "scripthub-project", "version": 99, "files": [], "sequences": [], "presets": []}),
            "changed": self.zip_with({"files/a.py": b"changed"}, manifest={
                "format": "scripthub-project", "version": 1, "sequences": [], "presets": [],
                "files": [{"path": "a.py", "size": 1, "sha256": "0" * 64}]}),
            "manifest path": self.zip_with({}, manifest={
                "format": "scripthub-project", "version": 1, "sequences": [], "presets": [],
                "files": [{"path": "../../evil.py"}]}),
        }
        for label, path in cases.items():
            with self.subTest(label):
                with self.assertRaises(ProjectError):
                    project_files.inspect(path)
        (self.tmp / "notzip.scripthub").write_bytes(b"hello")
        with self.assertRaises(ProjectError):
            project_files.inspect(self.tmp / "notzip.scripthub")

    def test_destination_must_be_new_or_empty_and_outside_runtime(self):
        (self.tmp / "full").mkdir()
        (self.tmp / "full" / "x.txt").write_bytes(b"x")
        for bad in (str(self.tmp / "full"), "relative/folder", str(config.RUNTIME_DIR / "here"), ""):
            with self.subTest(bad):
                with self.assertRaises(ProjectError):
                    project_files.check_destination(bad)
        (self.tmp / "empty").mkdir()
        self.assertEqual(project_files.check_destination(str(self.tmp / "empty")), self.tmp / "empty")


class WebTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.client = self.client_class(HTTP_HOST="localhost")

    def test_save_open_and_install_through_the_pages(self):
        slug = self.make_sequence()
        r = self.client.get("/project/save/?name=Ops project.scripthub")      # what the Save As dialog fetches
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="Ops project.scripthub"', r["Content-Disposition"])
        self.assertEqual(r["X-ScriptHub-Report"], "5 files (2 scripts), 1 sequences, 1 presets")
        body = b"".join(r.streaming_content)
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = set(z.namelist())
            self.assertEqual(json.loads(z.read("manifest.json"))["name"], "Ops project")
        self.assertIn(f"sequences/{slug}.yaml", names)                   # sequences using the folder
        self.assertFalse(any(".env" in n or ".pem" in n for n in names))
        self.assertIn('filename="ops.scripthub"', self.client.get("/project/save/")["Content-Disposition"])   # folder name
        upload = io.BytesIO(body)
        upload.name = "Ops project.scripthub"
        preview = self.client.post("/project/open/", {"project": upload})
        self.assertContains(preview, "Ops project")
        self.assertContains(preview, "added as “Release (2)”")
        token = preview.context["token"]
        dest = self.tmp / "unpacked"
        r = self.client.post("/project/open/install/", {"token": token, "destination": str(dest)})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/sequences/"))
        self.assertTrue((dest / "alpha.py").is_file())
        self.assertEqual(workspace.load()["folder"], str(dest))               # the project folder is open
        self.assertFalse(project_files.imports_dir().joinpath(f"{token}.scripthub").exists())

        bad = io.BytesIO(b"not a zip")
        bad.name = "x.scripthub"
        self.assertContains(self.client.post("/project/open/", {"project": bad}, follow=True), "Not a ScriptHub project file")
        r = self.client.post("/project/open/install/", {"token": "0" * 24, "destination": str(self.tmp / "z")}, follow=True)
        self.assertContains(r, "no longer available")

    def test_cancel_forgets_the_upload(self):
        self.make_sequence()
        pfile, _report = self.save([])
        upload = io.BytesIO(pfile.read_bytes())
        upload.name = "ops.scripthub"
        token = self.client.post("/project/open/", {"project": upload}).context["token"]
        self.client.post("/project/open/install/", {"token": token, "cancel": "1"})
        self.assertFalse(project_files.imports_dir().joinpath(f"{token}.scripthub").exists())

    def test_save_needs_an_open_folder(self):
        workspace.close_folder()
        r = self.client.get("/project/save/")
        self.assertEqual(r.status_code, 302)
