"""Open folder: the scanned script folder, recent and favourite folders (runner/workspace.py,
runner/folder_views.py)."""
import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from . import discovery, metadata, workspace


class FolderCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sh-folders-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = self.make_folder("alpha", "one.py")
        self.b = self.make_folder("beta", "two.py")
        self.cfg_file = self.tmp / "config.json"
        self.cfg_file.write_text(json.dumps({}))
        for p in (mock.patch.object(config, "CONFIG_FILE", self.cfg_file),
                  mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups"),
                  mock.patch.object(config, "SEQUENCES_DIR", self.tmp / "sequences"),
                  mock.patch.object(config, "SEQUENCE_RUNS_DIR", self.tmp / "sequence_runs"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets")):
            p.start()
            self.addCleanup(p.stop)
        discovery.scan(force=True)
        self.addCleanup(discovery.scan, True)
        self.client = self.client_class(HTTP_HOST="localhost")

    def make_folder(self, name, script):
        d = self.tmp / name
        d.mkdir()
        (d / script).write_bytes(b"print('x')\n")
        return d

    def names(self):
        return [s.path.name for s in discovery.scan(force=True)[0]]

    def state(self):
        return json.loads(workspace.state_file().read_text())


class WorkspaceTests(FolderCase):
    def test_nothing_open_on_a_fresh_install(self):
        self.assertIsNone(workspace.current())
        self.assertEqual(self.names(), [])
        self.assertFalse(workspace.state_file().exists())

    def test_old_script_directories_setting_seeds_the_state(self):
        missing = str(self.tmp / "gone")
        self.cfg_file.write_text(json.dumps({"script_directories": [missing, str(self.a), str(self.b)]}))
        self.assertTrue(workspace.same(str(workspace.current()), str(self.a)))   # first existing one
        self.assertEqual(len(workspace.load()["recent"]), 3)
        self.assertEqual(self.names(), ["one.py"])
        self.assertFalse(workspace.state_file().exists())                       # nothing written until a change

    def test_open_keeps_a_deduplicated_recent_list(self):
        workspace.open_folder(str(self.a))
        workspace.open_folder(str(self.b))
        workspace.open_folder(str(self.a).upper() if workspace.same("A", "a") else str(self.a))
        st = self.state()
        self.assertTrue(workspace.same(st["folder"], str(self.a)))
        self.assertEqual(len(st["recent"]), 2)
        self.assertTrue(workspace.same(st["recent"][0], str(self.a)))
        self.assertEqual(self.names(), ["one.py"])

    def test_recent_list_is_capped(self):
        for i in range(workspace.MAX_RECENT + 3):
            workspace.open_folder(str(self.make_folder(f"f{i}", "s.py")))
        self.assertEqual(len(self.state()["recent"]), workspace.MAX_RECENT)

    def test_open_rejects_missing_paths_and_non_scripts(self):
        workspace.open_folder(str(self.a))
        (self.a / "notes.txt").write_bytes(b"x")
        for bad in (str(self.tmp / "nope"), str(self.a / "notes.txt"), "", "  "):
            with self.assertRaises(ValueError):
                workspace.open_folder(bad)
        self.assertTrue(workspace.same(self.state()["folder"], str(self.a)))

    def test_a_single_script_file_can_be_opened(self):
        (self.b / "other.py").write_bytes(b"print(2)\n")
        workspace.open_folder(str(self.b / "two.py"))
        self.assertTrue(workspace.current_is_file())
        self.assertEqual(self.names(), ["two.py"])                       # just that script, not its folder
        script = discovery.scan()[0][0]
        self.assertTrue(workspace.same(str(script.root), str(self.b)))   # its folder is its root
        self.assertTrue(self.state()["recent"][0].endswith("two.py"))
        page = self.client.get("/")
        self.assertContains(page, "Close opened files")                  # File menu
        self.assertContains(page, "Open files")                          # explorer title
        self.assertNotContains(page, "data-new-script")                  # no folder to create scripts in
        self.assertEqual(self.client.get("/scripts/new/")["Location"], "/folder/")
        sid = discovery.scan()[0][0].id
        self.assertContains(self.client.get(f"/scripts/{sid}/"), f"Close two.py")   # File > Close <this file>

    def test_files_open_next_to_a_folder_and_close_on_their_own(self):
        workspace.open_folder(str(self.a))
        workspace.open_folder(str(self.b / "two.py"))
        self.assertEqual(sorted(self.names()), ["one.py", "two.py"])     # the folder and the extra file
        self.assertFalse(workspace.current_is_file())
        workspace.open_folder(str(self.make_folder("gamma", "three.py")))    # another folder: files stay
        self.assertEqual(sorted(self.names()), ["three.py", "two.py"])
        self.assertTrue(workspace.close_file(str(self.b / "two.py")))
        self.assertEqual(self.names(), ["three.py"])
        self.assertFalse(workspace.close_file(str(self.b / "two.py")))
        workspace.open_folder(str(self.b / "two.py"))
        workspace.close_all_files()
        self.assertEqual(self.state()["files"], [])

    def test_several_files_open_at_once_and_show_the_first(self):
        (self.b / "other.py").write_bytes(b"print(2)\n")
        r = self.client.post("/folder/open/", {"path": [str(self.b / "two.py"), str(self.b / "other.py")]})
        self.assertEqual(sorted(self.names()), ["other.py", "two.py"])
        first = next(s for s in discovery.scan()[0] if s.path.name == "two.py")
        self.assertEqual(r["Location"], f"/scripts/{first.id}/")          # opens as a tab
        page = self.client.get(r["Location"])
        self.assertContains(page, 'data-ws="files"')                      # the open-tabs list for "just files"
        self.assertContains(page, f'data-script="{first.id}"')
        self.client.post("/folder/close-file/", {"path": str(self.b / "two.py")})
        self.assertEqual(self.names(), ["other.py"])
        self.client.post("/folder/close-file/", {"all": "1"})
        self.assertEqual(self.names(), [])

    def test_a_file_stored_as_the_folder_is_read_as_an_opened_file(self):
        workspace.state_file().write_text(json.dumps({"folder": str(self.b / "two.py"), "recent": []}))
        st = workspace.load()
        self.assertEqual((st["folder"], len(st["files"])), (None, 1))
        self.assertEqual(self.names(), ["two.py"])

    def test_browse_lists_script_files_too(self):
        (self.a / "notes.txt").write_bytes(b"x")
        files = [f["name"] for f in workspace.browse(str(self.a))["files"]]
        self.assertEqual(files, ["one.py"])

    def test_favourites_are_listed_apart_from_recent(self):
        workspace.open_folder(str(self.a))
        workspace.open_folder(str(self.b))
        workspace.set_favorite(str(self.a), True)
        ov = workspace.overview()
        self.assertEqual([x["name"] for x in ov["favorites"]], ["alpha"])
        self.assertEqual([x["name"] for x in ov["recent"]], ["beta"])
        self.assertTrue(ov["folder"]["is_open"] and ov["folder"]["name"] == "beta")
        workspace.set_favorite(str(self.a), False)
        self.assertEqual(workspace.overview()["favorites"], [])

    def test_close_and_forget(self):
        workspace.open_folder(str(self.a))
        workspace.close_folder()
        self.assertIsNone(workspace.current())
        self.assertEqual(self.names(), [])
        workspace.forget(str(self.a))
        self.assertEqual(self.state()["recent"], [])

    def test_a_broken_state_file_means_nothing_open(self):
        workspace.state_file().write_text("{ broken")
        self.assertIsNone(workspace.current())
        workspace.open_folder(str(self.b))                     # and it is replaced on the next change
        self.assertTrue(workspace.same(self.state()["folder"], str(self.b)))

    def test_scan_stops_at_the_depth_limit(self):
        deepest = self.a.joinpath(*"bcdefghij"[:discovery.MAX_DEPTH])   # MAX_DEPTH levels below the open folder
        deepest.mkdir(parents=True)
        (deepest / "deepest.py").write_bytes(b"print(5)\n")
        (deepest / "z").mkdir()
        (deepest / "z" / "too_deep.py").write_bytes(b"print(6)\n")
        workspace.open_folder(str(self.a))
        scripts, problems = discovery.scan(force=True)
        self.assertEqual(sorted(s.path.name for s in scripts), ["deepest.py", "one.py"])
        msg = f"more than {discovery.MAX_DEPTH} folder levels deep"
        self.assertEqual(len(problems), 1)
        self.assertIn(msg, problems[0])
        self.assertContains(self.client.get("/scripts/"), msg)

    def test_browse_lists_sub_folders_without_hidden_ones(self):
        (self.a / "sub").mkdir()
        (self.a / ".git").mkdir()
        b = workspace.browse(str(self.a))
        self.assertEqual([d["name"] for d in b["dirs"]], ["sub"])
        self.assertTrue(workspace.same(b["parent"], str(self.tmp)))
        self.assertTrue(b["places"])
        self.assertEqual(workspace.browse(str(self.tmp / "nope"))["path"], "")


class FolderPageTests(FolderCase):
    def test_fresh_install_asks_for_a_folder(self):
        page = self.client.get("/")
        self.assertContains(page, "Open a script folder to start")
        self.assertContains(page, "No folder open")
        self.assertContains(self.client.get("/folder/"), 'id="folder-path"')

    def test_open_from_the_page(self):
        r = self.client.post("/folder/open/", {"path": str(self.a)})
        self.assertEqual(r["Location"], "/scripts/")
        self.assertContains(self.client.get("/scripts/"), "one.py")
        page = self.client.get("/folder/")
        self.assertContains(page, "alpha")
        self.assertContains(page, "Close folder")

    def test_open_errors_are_shown_and_nothing_changes(self):
        r = self.client.post("/folder/open/", {"path": str(self.tmp / "nope")}, follow=True)
        self.assertContains(r, "Not an existing folder")
        self.assertIsNone(workspace.current())

    def test_favourite_close_and_forget_views(self):
        self.client.post("/folder/open/", {"path": str(self.a)})
        self.client.post("/folder/favorite/", {"path": str(self.a), "on": "1"})
        self.assertEqual(len(self.state()["favorites"]), 1)
        self.assertContains(self.client.get("/"), "Remove from favourites")       # explorer menu
        self.client.post("/folder/close/")
        self.assertIsNone(self.state()["folder"])
        self.client.post("/folder/forget/", {"path": str(self.a)})
        self.assertEqual(self.state()["recent"], [])
        self.assertEqual(self.client.get("/folder/close/").status_code, 405)

    def test_next_must_stay_on_this_site(self):
        r = self.client.post("/folder/open/", {"path": str(self.a), "next": "https://evil.example/"})
        self.assertEqual(r["Location"], "/scripts/")
        r = self.client.post("/folder/open/", {"path": str(self.b), "next": "/history/"})
        self.assertEqual(r["Location"], "/history/")

    def test_the_folder_is_no_longer_a_setting(self):
        page = self.client.get("/settings/")
        self.assertNotContains(page, "Script directories")
        self.assertEqual(self.client.post("/settings/directories/add/", {"directory": str(self.a)}).status_code, 404)

    def test_adhoc_is_gone(self):
        self.assertEqual(self.client.get("/adhoc/").status_code, 404)
        self.assertNotContains(self.client.get("/"), "Ad-hoc")


class BackgroundScanTests(FolderCase):
    """A big folder is scanned in a thread: pages do not wait for it and show progress."""

    def slow(self, seconds=0.25):
        build = discovery._build

        def slow_build(*a, **kw):
            time.sleep(seconds)
            return build(*a, **kw)
        p = mock.patch.object(discovery, "_build", slow_build)
        p.start()
        self.addCleanup(p.stop)

    def finish(self):
        job = discovery._job
        if job is not None:
            job.done.wait(10)

    def test_pages_do_not_wait_and_show_progress(self):
        for i in range(4):
            (self.a / f"s{i}.py").write_bytes(b"print(1)\n")
        self.slow(0.4)                                                   # 5 scripts: about 2 s
        for name, value in (("RESCAN_WAIT", 0.2), ("AUTO_WAIT", 0.1)):
            p = mock.patch.object(discovery, name, value)
            p.start()
            self.addCleanup(p.stop)
        t = time.monotonic()
        r = self.client.post("/folder/open/", {"path": str(self.a)}, follow=True)
        self.assertLess(time.monotonic() - t, 1.5)
        self.assertContains(r, "still being scanned")
        self.assertContains(r, 'id="sb-scan"')
        st = self.client.get("/folder/scan/").json()
        self.assertTrue(st["scanning"] and st["first"])
        self.assertLess(st["found"], 5)
        t = time.monotonic()
        partial, _ = discovery.scan(wait=0.05)                           # the first scan's results so far
        self.assertLess(time.monotonic() - t, 1)
        self.assertLess(len(partial), 5)
        self.finish()
        st = self.client.get("/folder/scan/").json()
        self.assertFalse(st["scanning"])
        self.assertEqual(st["count"], 5)
        self.assertEqual(len(discovery.scan()[0]), 5)
        self.assertNotContains(self.client.get("/scripts/"), 'id="sb-scan"')

    def test_opening_another_folder_cancels_the_running_scan(self):
        for i in range(6):
            (self.a / f"s{i}.py").write_bytes(b"print(1)\n")
        self.slow()
        workspace.open_folder(str(self.a))
        discovery.rescan(wait=0.1)
        first = discovery._job
        workspace.open_folder(str(self.b))
        discovery.rescan(wait=5)
        self.assertTrue(first.cancelled)
        first.done.wait(10)
        self.assertEqual(self.names(), ["two.py"])

    def test_one_file_is_refreshed_without_a_rescan(self):
        workspace.open_folder(str(self.a))
        discovery.scan(force=True)
        new = self.a / "later.py"
        new.write_bytes(b"print(2)\n")
        with mock.patch.object(discovery, "_start") as start:
            discovery.update_file(new)
            self.assertIn("later.py", [s.path.name for s in discovery.scan()[0]])
            start.assert_not_called()
        new.unlink()
        discovery.update_file(new)
        self.assertNotIn("later.py", [s.path.name for s in discovery.scan()[0]])

    def test_system_folders_are_skipped(self):
        (self.a / "$Recycle.Bin").mkdir()
        (self.a / "$Recycle.Bin" / "old.py").write_bytes(b"print(0)\n")
        workspace.open_folder(str(self.a))
        self.assertEqual(self.names(), ["one.py"])


class NewScriptTests(FolderCase):
    def form(self, **over):
        return {"language": ".py", "filename": "cleanup_logs", "subfolder": "", "name": "", "category": "", **over}

    def test_needs_an_open_folder(self):
        r = self.client.get("/scripts/new/")
        self.assertEqual(r["Location"], "/folder/")
        self.assertEqual(self.client.post("/scripts/new/", self.form())["Location"], "/folder/")
        self.assertNotContains(self.client.get("/"), "data-new-script")       # no "+" in the explorer

    def test_creates_the_script_with_metadata_and_opens_it_in_the_editor(self):
        workspace.open_folder(str(self.a))
        self.assertContains(self.client.get("/"), "data-new-script")
        r = self.client.post("/scripts/new/", self.form(subfolder="maint/daily", category="Maintenance"))
        target = self.a / "maint" / "daily" / "cleanup_logs.py"
        self.assertTrue(target.is_file())
        self.assertIn(b"Hello from ScriptHub", target.read_bytes())
        meta, _src, err = metadata.load_raw(target, "0" * 12)                 # the block at the top of the script
        self.assertIsNone(err)
        self.assertTrue(target.read_text().splitlines()[0].startswith("# --- scripthub ---"))
        self.assertFalse(target.with_suffix(".json").exists())                    # no sidecar
        self.assertEqual((meta["name"], meta["category"], meta["parameters"]), ("Cleanup Logs", "Maintenance", []))
        from runner import ids
        self.assertTrue(ids.valid(meta["id"]))                                    # a new script has its stable id
        self.assertEqual(r["Location"], f"/scripts/{meta['id']}/run/")
        self.assertFalse(config.BACKUPS_DIR.exists())                            # nothing to back up yet
        page = self.client.get(r["Location"])
        self.assertContains(page, 'id="edit-form"')                          # the editor, ready to write
        self.assertContains(page, "Created cleanup_logs.py")

    def test_never_overwrites_and_rejects_bad_names_and_paths(self):
        workspace.open_folder(str(self.a))
        before = (self.a / "one.py").read_bytes()
        r = self.client.post("/scripts/new/", self.form(filename="one"))
        self.assertContains(r, "already exists", status_code=400)
        self.assertEqual((self.a / "one.py").read_bytes(), before)
        for bad in ({"filename": "../evil"}, {"filename": "a b"}, {"filename": ""},
                    {"subfolder": "../out"}, {"subfolder": "/abs"}, {"subfolder": "C:/x"}, {"language": ".exe"}):
            r = self.client.post("/scripts/new/", self.form(**bad))
            self.assertEqual(r.status_code, 400, bad)
        self.assertFalse((self.tmp / "out").exists())
        self.assertEqual(self.names(), ["one.py"])

    def test_sub_folder_within_the_depth_limit(self):
        workspace.open_folder(str(self.a))
        levels = list("bcdefghij")
        r = self.client.post("/scripts/new/", self.form(subfolder="/".join(levels[:discovery.MAX_DEPTH + 1])))
        self.assertContains(r, f"at most {discovery.MAX_DEPTH} levels deep", status_code=400)
        self.assertFalse((self.a / "b").exists())
        r = self.client.post("/scripts/new/", self.form(subfolder="/".join(levels[:discovery.MAX_DEPTH])))
        self.assertEqual(r.status_code, 302)
        self.assertIn("cleanup_logs.py", self.names())

    def test_line_endings_and_extension_handling(self):
        workspace.open_folder(str(self.a))
        self.client.post("/scripts/new/", self.form(language=".sh", filename="deploy.sh"))
        self.assertNotIn(b"\r\n", (self.a / "deploy.sh").read_bytes())                 # .sh: LF, extension added once
        self.client.post("/scripts/new/", self.form(language=".bat", filename="win"))
        if (self.a / "win.bat").exists():                                                # .bat is configured on Windows
            self.assertIn(b"\r\n", (self.a / "win.bat").read_bytes())

    def test_plus_next_to_a_script_prefills_its_place(self):
        workspace.open_folder(str(self.tmp))
        sid = next(s.id for s in discovery.scan(force=True)[0] if s.path.name == "two.py")
        page = self.client.get(f"/scripts/new/?from={sid}")
        self.assertContains(page, 'name="subfolder" value="beta"')
        self.assertContains(page, '<option value="beta">')                              # existing sub-folders offered

    def test_switched_off_with_editing(self):
        workspace.open_folder(str(self.a))
        self.cfg_file.write_text(json.dumps({"enable_script_editing": False}))
        self.assertEqual(self.client.get("/scripts/new/").status_code, 403)
        self.assertEqual(self.client.post("/scripts/new/", self.form()).status_code, 403)
        self.assertFalse((self.a / "cleanup_logs.py").exists())
