"""Sequences: storage (runtime/sequences/<slug>.yaml), running steps one after another,
and the web pages.

python manage.py test runner.test_sequences
"""
import json
import os
import shutil
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import discovery, history, presets, sequence_runner, sequences
from runner.process_manager import manager
from runner.sequence_runner import sequencer

TOOL = textwrap.dedent("""
    import argparse, sys, time
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--tag", default="x")
    p.add_argument("--fail", action="store_true")
    p.add_argument("--sleep", type=float, default=0)
    p.add_argument("--password", default="")
    a = p.parse_args()
    with open(a.out, "a") as f:
        f.write("start %s %d\\n" % (a.tag, len(a.password)))
    time.sleep(a.sleep)
    with open(a.out, "a") as f:
        f.write("end %s\\n" % a.tag)
    sys.exit(1 if a.fail else 0)
""")

PARAMS = [
    {"name": "out", "type": "text", "required": True},
    {"name": "tag", "type": "text", "default": "x"},
    {"name": "fail", "type": "boolean", "default": False},
    {"name": "sleep", "type": "decimal", "default": "0"},
    {"name": "password", "type": "password"},
]


class SequenceCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-seq-"))
        self.root = self.tmp / "scripts"
        self.root.mkdir()
        self.out = self.tmp / "out.txt"
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"script_directories": [str(self.root)],
                                   "interpreters": {".py": "python"}, "default_timeout": 60,
                                   "max_concurrent_executions": 3, "polling_interval": 500}))
        for p in (mock.patch.object(config, "CONFIG_FILE", cfg),
                  mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions"),
                  mock.patch.object(config, "PRESETS_DIR", self.tmp / "presets"),
                  mock.patch.object(config, "BACKUPS_DIR", self.tmp / "backups"),
                  mock.patch.object(config, "METADATA_OVERRIDE_DIR", self.tmp / "overrides"),
                  mock.patch.object(config, "SEQUENCES_DIR", self.tmp / "sequences"),
                  mock.patch.object(config, "SEQUENCE_RUNS_DIR", self.tmp / "sequence_runs")):
            p.start()
            self.addCleanup(p.stop)
        (self.tmp / "executions").mkdir()
        self.first = self.make("first.py", "First")
        self.second = self.make("second.py", "Second")
        discovery.scan(force=True)
        self.client = self.client_class(HTTP_HOST="localhost")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for rid in list(sequencer._runs):
            sequencer.cancel(rid)
        sequencer.wait_idle(20)
        for rid in list(manager._active):
            manager.cancel(rid)
        manager.wait_idle(20)
        discovery.scan(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, title, folder=None):
        folder = folder or self.root
        path = folder / name
        path.write_bytes(TOOL.encode("utf-8"))            # bytes: no newline conversion
        (folder / (path.stem + ".json")).write_text(json.dumps({"name": title, "parameters": PARAMS}))
        return path

    def script(self, path):
        return next(s for s in discovery.scan()[0] if s.path == path.resolve())

    def values(self, **over):
        return {"out": str(self.out), "tag": "x", "fail": False, "sleep": "0", **over}

    def sequence(self, *steps, name="Nightly"):
        """A sequence of (script path, values, on_failure) steps; returns its slug."""
        slug = sequences.create(name)
        for path, values, on_failure in steps:
            i = sequences.add_step(slug, sequences.new_step(self.script(path), values))
            sequences.update_step(slug, i, on_failure=on_failure)
        return slug

    def run_sequence(self, slug, passwords=None):
        data = sequences.load(slug)
        items, errors = sequence_runner.prepare(data, passwords)
        self.assertEqual(errors, [])
        rid = sequencer.start(data, items)
        self.assertTrue(sequencer.wait_idle(60))
        return sequence_runner.read_run(rid)

    def lines(self):
        return self.out.read_text().splitlines() if self.out.exists() else []


# ------------------------------------------------------------------ storage ---
class StorageTests(SequenceCase):
    def test_create_edit_and_file_format(self):
        slug = self.sequence((self.first, self.values(tag="a"), "stop"),
                             (self.second, self.values(tag="b"), "continue"), name="Nightly maintenance")
        self.assertEqual(slug, "nightly-maintenance")
        with self.assertRaises(sequences.SequenceError):
            sequences.create("nightly MAINTENANCE")                  # names are unique, any case
        text = (config.SEQUENCES_DIR / f"{slug}.yaml").read_text(encoding="utf-8")
        a, b = self.script(self.first), self.script(self.second)
        self.assertTrue(text.startswith("# Sequence for ScriptHub. Passwords are never stored.\n"))
        self.assertIn(f"steps:\n  - script: {a.id}\n    path: {a.path}\n    name: First\n    values:\n", text)
        self.assertIn("    on_failure: continue\n", text)

        sequences.move_step(slug, 1, -1)
        sequences.rename(slug, "Renamed")
        data = sequences.load(slug)
        self.assertEqual(data["slug"], slug)                          # the slug never changes
        self.assertEqual(data["name"], "Renamed")
        self.assertEqual([s["script"] for s in data["steps"]], [b.id, a.id])
        sequences.remove_step(slug, 0)
        self.assertEqual([s["values"]["tag"] for s in sequences.load(slug)["steps"]], ["a"])
        with self.assertRaises(sequences.SequenceError):
            sequences.remove_step(slug, 5)
        self.assertTrue(sequences.delete(slug))
        self.assertEqual(sequences.list_sequences(), [])

    def test_preset_step_stores_no_values(self):
        slug = self.sequence((self.first, self.values(), "stop"))
        sequences.update_step(slug, 0, preset="prod", values={})
        text = (config.SEQUENCES_DIR / f"{slug}.yaml").read_text(encoding="utf-8")
        self.assertIn("    preset: prod\n", text)
        self.assertNotIn("values:", text)

    def test_unreadable_file_is_listed_and_never_overwritten(self):
        config.SEQUENCES_DIR.mkdir(parents=True)
        broken = config.SEQUENCES_DIR / "broken.yaml"
        broken.write_text("name: x\nsteps: [1, 2]\n")
        [entry] = sequences.list_sequences()
        self.assertEqual(entry["slug"], "broken")
        self.assertIn("could not be read", entry["error"])
        self.assertIsNone(sequences.get("broken"))
        with self.assertRaises(sequences.SequenceError):
            sequences.add_step("broken", sequences.new_step(self.script(self.first), {}))
        self.assertEqual(broken.read_text(), "name: x\nsteps: [1, 2]\n")

    def test_step_finds_its_script_by_path_when_the_folder_is_not_open(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        path = self.make("far.py", "Far", folder=other)
        sid = discovery.script_id(path.resolve())
        found = sequences.resolve({"script": sid, "path": str(path)})
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "Far")
        self.assertIsNone(sequences.resolve({"script": "0123456789ab", "path": str(path)}))   # another file now
        self.assertIsNone(sequences.resolve({"script": sid, "path": ""}))


# ------------------------------------------------------------------- runner ---
class RunnerTests(SequenceCase):
    def test_steps_run_one_after_another(self):
        slug = self.sequence((self.first, self.values(tag="a", sleep="0.6"), "stop"),
                             (self.second, self.values(tag="b"), "stop"))
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.SUCCESS)
        self.assertEqual(self.lines(), ["start a 0", "end a", "start b 0", "end b"])
        self.assertEqual([s["status"] for s in run["steps"]], [history.SUCCESS, history.SUCCESS])
        for n, step in enumerate(run["steps"], start=1):
            meta = history.read_meta(step["run_id"])
            self.assertEqual(meta["sequence"], {"run": run["id"], "step": n, "name": "Nightly"})
            self.assertEqual(meta["status"], history.SUCCESS)

    def test_a_failed_step_stops_the_sequence_by_default(self):
        slug = self.sequence((self.first, self.values(tag="a", fail=True), "stop"),
                             (self.second, self.values(tag="b"), "stop"))
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.FAILED)
        self.assertIn("Step 1 (First) did not succeed; the remaining steps were skipped.", run["error"])
        self.assertEqual([s["status"] for s in run["steps"]], [history.FAILED, sequence_runner.SKIPPED])
        self.assertIsNone(run["steps"][1]["run_id"])
        self.assertEqual(self.lines(), ["start a 0", "end a"])

    def test_continue_on_failure_runs_the_next_step(self):
        slug = self.sequence((self.first, self.values(tag="a", fail=True), "continue"),
                             (self.second, self.values(tag="b"), "stop"))
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.FAILED)
        self.assertEqual(run["error"], "1 of 2 steps did not succeed.")
        self.assertEqual([s["status"] for s in run["steps"]], [history.FAILED, history.SUCCESS])
        self.assertEqual(self.lines()[-1], "end b")

    def test_stop_cancels_the_running_step_and_skips_the_rest(self):
        slug = self.sequence((self.first, self.values(tag="a", sleep="20"), "continue"),
                             (self.second, self.values(tag="b"), "stop"))
        data = sequences.load(slug)
        items, errors = sequence_runner.prepare(data)
        self.assertEqual(errors, [])
        rid = sequencer.start(data, items)
        end = time.monotonic() + 20
        while "start a 0" not in self.lines() and time.monotonic() < end:
            time.sleep(0.05)
        self.assertTrue(sequencer.cancel(rid))
        self.assertTrue(sequencer.wait_idle(30))
        run = sequence_runner.read_run(rid)
        self.assertEqual(run["status"], history.CANCELLED)
        self.assertEqual([s["status"] for s in run["steps"]], [history.CANCELLED, sequence_runner.SKIPPED])
        self.assertFalse(sequencer.cancel(rid))

    def test_every_step_is_checked_before_anything_runs(self):
        slug = self.sequence((self.first, self.values(out=""), "stop"),
                             (self.second, self.values(), "stop"))
        data = sequences.load(slug)
        data["steps"].append({**data["steps"][1], "script": "0123456789ab", "path": "", "name": "Gone"})
        items, errors = sequence_runner.prepare(data)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].startswith("Step 1 (First): Out:"), errors)
        self.assertIn("Step 3 (Gone): the script was not found", errors[1])
        self.assertEqual(sequence_runner.prepare({"steps": []})[1], ["The sequence has no steps."])

    def test_interrupted_sequence_runs_are_marked_on_startup(self):
        rid = sequence_runner._allocate()
        sequence_runner._write_run(rid, {"id": rid, "sequence": "x", "name": "X", "status": history.RUNNING,
                                         "owner_pid": 2 ** 22 + 12345, "steps": [
                                             {"index": 1, "status": history.SUCCESS, "run_id": None},
                                             {"index": 2, "status": sequence_runner.PENDING, "run_id": None}]})
        with mock.patch.object(sequence_runner, "pid_alive", return_value=False):
            self.assertEqual(sequencer.recover_interrupted(), [rid])
        run = sequence_runner.read_run(rid)
        self.assertEqual(run["status"], history.INTERRUPTED)
        self.assertEqual([s["status"] for s in run["steps"]], [history.SUCCESS, sequence_runner.SKIPPED])


# ---------------------------------------------------------------------- web ---
class WebTests(SequenceCase):
    def post_step(self, slug, index, **form):
        base = {"source": "values", "host": "", "on_failure": "stop", "p_out": str(self.out), "p_tag": "w",
                "p_sleep": "0"}
        return self.client.post(f"/sequences/{slug}/steps/{index}/", {**base, **form})

    def test_create_add_configure_and_run(self):
        self.assertEqual(self.client.get("/sequences/").status_code, 200)
        r = self.client.post("/sequences/", {"name": "Web flow"})
        self.assertRedirects(r, "/sequences/web-flow/", fetch_redirect_response=False)
        a, b = self.script(self.first), self.script(self.second)
        for s in (a, b):
            r = self.client.post("/sequences/web-flow/steps/", {"op": "add", "script_id": s.id})
            self.assertEqual(r.status_code, 302)
        page = self.client.get("/sequences/web-flow/")
        self.assertContains(page, "First")
        self.assertContains(page, 'name="pw_0_password"')               # passwords are asked at run time
        self.assertContains(self.client.get("/sequences/web-flow/steps/1/"), 'name="p_out"')

        self.assertEqual(self.post_step("web-flow", 0, p_tag="a", p_password="never-saved").status_code, 302)
        self.assertEqual(self.post_step("web-flow", 1, p_tag="b", on_failure="continue").status_code, 302)
        r = self.post_step("web-flow", 1, p_out="")                       # required value missing
        self.assertEqual(r.status_code, 400)
        text = (config.SEQUENCES_DIR / "web-flow.yaml").read_text(encoding="utf-8")
        self.assertNotIn("never-saved", text)
        self.assertNotIn("password", text)
        self.assertEqual([s["on_failure"] for s in sequences.load("web-flow")["steps"]], ["stop", "continue"])

        token = uuid.uuid4().hex
        r = self.client.post("/sequences/web-flow/start/", {"submit_token": token, "pw_0_password": "s3cret"})
        self.assertEqual(r.status_code, 302)
        rid = r["Location"].rstrip("/").rsplit("/", 1)[1]
        self.assertTrue(sequence_runner.valid_run_id(rid), r["Location"])
        again = self.client.post("/sequences/web-flow/start/", {"submit_token": token, "pw_0_password": "s3cret"})
        self.assertEqual(again["Location"], r["Location"])                # a double submit starts nothing new
        self.assertTrue(sequencer.wait_idle(60))
        self.assertEqual(self.lines(), ["start a 6", "end a", "start b 0", "end b"])
        first_run = sequence_runner.read_run(rid)["steps"][0]["run_id"]
        self.assertContains(self.client.get(f"/executions/{first_run}/"), f"/sequence-runs/{rid}/")

        page = self.client.get(f"/sequence-runs/{rid}/")
        self.assertContains(page, "Web flow")
        status = self.client.get(f"/sequence-runs/{rid}/status/").json()
        self.assertEqual(status["status"], history.SUCCESS)
        self.assertEqual([s["status"] for s in status["steps"]], [history.SUCCESS, history.SUCCESS])
        self.assertContains(self.client.get("/sequences/"), rid)
        self.assertNotIn("s3cret", json.dumps(sequence_runner.read_run(rid)))
        self.assertNotIn("s3cret", json.dumps(history.read_meta(status["steps"][0]["run_id"])))

    def test_step_using_a_preset(self):
        slug = self.sequence((self.first, self.values(), "stop"), name="Preset flow")
        script = self.script(self.first)
        params = script.meta["parameters"]
        presets.save_preset(script, "Prod", params, {**self.values(tag="from-preset")}, "local")
        r = self.post_step(slug, 0, source="prod")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sequences.load(slug)["steps"][0]["preset"], "prod")
        self.assertContains(self.client.get(f"/sequences/{slug}/"), "Preset: Prod")
        r = self.client.post(f"/sequences/{slug}/start/", {"submit_token": uuid.uuid4().hex})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(sequencer.wait_idle(60))
        self.assertEqual(self.lines(), ["start from-preset 0", "end from-preset"])

    def test_problems_are_shown_and_nothing_starts(self):
        slug = self.sequence((self.first, self.values(out=""), "stop"), name="Broken")
        r = self.client.post(f"/sequences/{slug}/start/", {"submit_token": uuid.uuid4().hex}, follow=True)
        self.assertContains(r, "Step 1 (First): Out:")
        self.assertEqual(sequence_runner.list_runs(), [])
        self.assertEqual(list(config.EXECUTIONS_DIR.iterdir()), [])

    def test_reorder_and_remove_steps(self):
        slug = self.sequence((self.first, self.values(), "stop"), (self.second, self.values(), "stop"), name="Order")
        self.client.post(f"/sequences/{slug}/steps/", {"op": "down", "index": "0"})
        self.assertEqual([s["name"] for s in sequences.load(slug)["steps"]], ["Second", "First"])
        self.client.post(f"/sequences/{slug}/steps/", {"op": "remove", "index": "0"})
        self.assertEqual([s["name"] for s in sequences.load(slug)["steps"]], ["First"])
        self.assertEqual(self.client.post(f"/sequences/{slug}/steps/", {"op": "up", "index": "7"}).status_code, 404)
        r = self.client.post(f"/sequences/{slug}/delete/")
        self.assertRedirects(r, "/sequences/", fetch_redirect_response=False)
        self.assertFalse((config.SEQUENCES_DIR / f"{slug}.yaml").exists())

    def test_unknown_ids_are_404(self):
        for url in ("/sequences/nope/", "/sequences/BAD!/", "/sequence-runs/RUN-20260101-001/",
                    "/sequence-runs/SEQ-20260101-001/", "/sequences/nope/steps/0/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post("/sequences/nope/start/").status_code, 404)
