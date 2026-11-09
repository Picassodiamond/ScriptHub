"""Sequences: storage (runtime/sequences/<slug>.yaml, steps + arrows), running steps in the
order the arrows give (side by side where they allow), and the web pages.

python manage.py test runner.test_sequences
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
                                   "max_concurrent_executions": 4, "polling_interval": 500}))
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
        self.third = self.make("third.py", "Third")
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

    def sequence(self, *steps, links=(), name="Nightly"):
        """Steps (script path, values) get the ids s1, s2, ...; ``links`` = (from, to, when)."""
        slug = sequences.create(name)
        for n, (path, values) in enumerate(steps):
            sequences.add_step(slug, sequences.new_step(self.script(path), values, x=0, y=n * 120))
        for a, b, when in links:
            sequences.link(slug, a, b, when)
        return slug

    def run_sequence(self, slug, passwords=None):
        data = sequences.load(slug)
        items, errors = sequence_runner.prepare(data, passwords)
        self.assertEqual(errors, [])
        rid = sequencer.start(data, items)
        self.assertTrue(sequencer.wait_idle(60))
        return sequence_runner.read_run(rid)

    def statuses(self, run):
        return {s["id"]: s["status"] for s in run["steps"]}

    def lines(self):
        return self.out.read_text().splitlines() if self.out.exists() else []


# ------------------------------------------------------------------ storage ---
class StorageTests(SequenceCase):
    def test_steps_arrows_and_file_format(self):
        slug = self.sequence((self.first, self.values(tag="a")), (self.second, self.values(tag="b")),
                             links=[("s1", "s2", "success")], name="Nightly maintenance")
        self.assertEqual(slug, "nightly-maintenance")
        with self.assertRaises(sequences.SequenceError):
            sequences.create("nightly MAINTENANCE")                  # names are unique, any case
        text = (config.SEQUENCES_DIR / f"{slug}.yaml").read_text(encoding="utf-8")
        a = self.script(self.first)
        self.assertTrue(text.startswith("# Sequence for ScriptHub. Passwords are never stored.\n"))
        self.assertIn(f"steps:\n  - id: s1\n    script: {a.id}\n    path: {a.path}\n    name: First\n    values:\n", text)
        self.assertIn("    x: 0\n    y: 120\n", text)
        self.assertIn("links:\n  - from: s1\n    to: s2\n    when: success\n", text)

        sequences.link(slug, "s1", "s2", "always")                    # the same arrow: its condition changes
        sequences.move_steps(slug, {"s2": (333, 41)})
        sequences.rename(slug, "Renamed")
        data = sequences.load(slug)
        self.assertEqual(data["slug"], slug)                          # the slug never changes
        self.assertEqual(data["name"], "Renamed")
        self.assertEqual(data["links"], [{"from": "s1", "to": "s2", "when": "always"}])
        self.assertEqual((data["steps"][1]["x"], data["steps"][1]["y"]), (340, 40))   # on the grid

        sid = sequences.add_step(slug, sequences.new_step(self.script(self.third), {}))
        self.assertEqual(sid, "s3")
        sequences.remove_step(slug, "s1")                             # its arrows go with it
        data = sequences.load(slug)
        self.assertEqual([s["id"] for s in data["steps"]], ["s2", "s3"])
        self.assertEqual(data["links"], [])
        self.assertEqual(sequences.add_step(slug, sequences.new_step(self.script(self.first), {})), "s4")
        with self.assertRaises(sequences.SequenceError):
            sequences.remove_step(slug, "s9")
        self.assertTrue(sequences.delete(slug))
        self.assertEqual(sequences.list_sequences(), [])

    def test_arrows_never_make_a_loop(self):
        slug = self.sequence((self.first, {}), (self.second, {}), (self.third, {}),
                             links=[("s1", "s2", "success"), ("s2", "s3", "success")])
        for a, b in (("s3", "s1"), ("s2", "s1"), ("s1", "s1")):
            with self.assertRaises(sequences.SequenceError):
                sequences.link(slug, a, b)
        with self.assertRaises(sequences.SequenceError):
            sequences.link(slug, "s1", "s3", "sometimes")
        with self.assertRaises(sequences.SequenceError):
            sequences.unlink(slug, "s1", "s3")
        sequences.link(slug, "s1", "s3", "failure")                    # a second way down is fine
        sequences.unlink(slug, "s1", "s2")
        self.assertEqual([(k["from"], k["to"]) for k in sequences.load(slug)["links"]], [("s2", "s3"), ("s1", "s3")])

    def test_preset_step_stores_no_values(self):
        slug = self.sequence((self.first, self.values()))
        sequences.update_step(slug, "s1", preset="prod", values={})
        text = (config.SEQUENCES_DIR / f"{slug}.yaml").read_text(encoding="utf-8")
        self.assertIn("    preset: prod\n", text)
        self.assertNotIn("values:", text)

    def test_first_version_file_is_read_as_a_chain(self):
        a, b, c = self.script(self.first), self.script(self.second), self.script(self.third)
        config.SEQUENCES_DIR.mkdir(parents=True)
        (config.SEQUENCES_DIR / "old.yaml").write_text(
            f"name: Old\nsteps:\n  - script: {a.id}\n    on_failure: continue\n"
            f"  - script: {b.id}\n    values:\n      tag: b\n    on_failure: stop\n  - script: {c.id}\n")
        data = sequences.load("old")
        self.assertEqual([s["id"] for s in data["steps"]], ["s1", "s2", "s3"])
        self.assertEqual(data["links"], [{"from": "s1", "to": "s2", "when": "always"},
                                         {"from": "s2", "to": "s3", "when": "success"}])
        self.assertEqual([s["y"] for s in data["steps"]], [40, 160, 280])
        sequences.rename("old", "Old one")                            # the next change writes the new form
        text = (config.SEQUENCES_DIR / "old.yaml").read_text(encoding="utf-8")
        self.assertNotIn("on_failure", text)
        self.assertIn("    when: always\n", text)

    def test_unreadable_file_is_listed_and_never_overwritten(self):
        config.SEQUENCES_DIR.mkdir(parents=True)
        broken = config.SEQUENCES_DIR / "broken.yaml"
        broken.write_text("name: x\nsteps: [1, 2]\n")
        a = self.script(self.first)
        loop = config.SEQUENCES_DIR / "loop.yaml"
        loop.write_text(f"name: Loop\nsteps:\n  - id: s1\n    script: {a.id}\n  - id: s2\n    script: {a.id}\n"
                        "links:\n  - from: s1\n    to: s2\n  - from: s2\n    to: s1\n")
        entries = {e["slug"]: e for e in sequences.list_sequences()}
        self.assertIn("could not be read", entries["broken"]["error"])
        self.assertIn("loop", entries["loop"]["error"])
        self.assertIsNone(sequences.get("broken"))
        with self.assertRaises(sequences.SequenceError):
            sequences.add_step("broken", sequences.new_step(a, {}))
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
    def test_a_chain_runs_one_after_another(self):
        slug = self.sequence((self.first, self.values(tag="a", sleep="0.6")), (self.second, self.values(tag="b")),
                             links=[("s1", "s2", "success")])
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.SUCCESS)
        self.assertEqual(self.lines(), ["start a 0", "end a", "start b 0", "end b"])
        self.assertEqual(self.statuses(run), {"s1": history.SUCCESS, "s2": history.SUCCESS})
        self.assertEqual(run["links"], [{"from": "s1", "to": "s2", "when": "success"}])
        for n, step in enumerate(run["steps"], start=1):
            meta = history.read_meta(step["run_id"])
            self.assertEqual(meta["sequence"], {"run": run["id"], "step": n, "name": "Nightly"})

    def test_a_failed_step_skips_what_needs_its_success(self):
        slug = self.sequence((self.first, self.values(tag="a", fail=True)), (self.second, self.values(tag="b")),
                             (self.third, self.values(tag="c")),
                             links=[("s1", "s2", "success"), ("s2", "s3", "always")])
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.FAILED)
        self.assertEqual(run["error"], "Step 1 (First) did not succeed. 2 steps were skipped.")
        self.assertEqual(self.statuses(run), {"s1": history.FAILED, "s2": sequence_runner.SKIPPED,
                                              "s3": sequence_runner.SKIPPED})      # skipped spreads, even over 'always'
        self.assertIsNone(run["steps"][1]["run_id"])
        self.assertEqual(self.lines(), ["start a 0", "end a"])

    def test_always_runs_the_next_step_after_a_failure(self):
        slug = self.sequence((self.first, self.values(tag="a", fail=True)), (self.second, self.values(tag="b")),
                             links=[("s1", "s2", "always")])
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.FAILED)
        self.assertEqual(run["error"], "Step 1 (First) did not succeed.")
        self.assertEqual(self.statuses(run), {"s1": history.FAILED, "s2": history.SUCCESS})

    def test_a_failure_arrow_handles_the_failure(self):
        slug = self.sequence((self.first, self.values(tag="a", fail=True)), (self.second, self.values(tag="ok")),
                             (self.third, self.values(tag="alert")),
                             links=[("s1", "s2", "success"), ("s1", "s3", "failure")])
        run = self.run_sequence(slug)
        self.assertEqual(self.statuses(run), {"s1": history.FAILED, "s2": sequence_runner.SKIPPED,
                                              "s3": history.SUCCESS})
        self.assertEqual(run["status"], history.SUCCESS)               # handled: a failure arrow leads on
        self.assertIsNone(run["error"])
        self.assertEqual(self.lines(), ["start a 0", "end a", "start alert 0", "end alert"])

    def test_branches_run_side_by_side(self):
        slug = self.sequence((self.first, self.values(tag="a")), (self.second, self.values(tag="b", sleep="1.5")),
                             (self.third, self.values(tag="c", sleep="1.5")),
                             links=[("s1", "s2", "success"), ("s1", "s3", "success")])
        run = self.run_sequence(slug)
        self.assertEqual(run["status"], history.SUCCESS)
        lines = self.lines()
        self.assertEqual(lines[:2], ["start a 0", "end a"])
        self.assertEqual(sorted(lines[2:4]), ["start b 0", "start c 0"])   # both started before either ended
        self.assertEqual(sorted(lines[4:]), ["end b", "end c"])

    def test_join_all_waits_for_every_arrow_and_any_for_the_first(self):
        for join, expect in (("all", sequence_runner.SKIPPED), ("any", history.SUCCESS)):
            with self.subTest(join=join):
                self.out.unlink(missing_ok=True)
                slug = self.sequence((self.first, self.values(tag="a", fail=True)), (self.second, self.values(tag="b")),
                                     (self.third, self.values(tag="c")),
                                     links=[("s1", "s3", "success"), ("s2", "s3", "success")], name=f"Join {join}")
                sequences.update_step(slug, "s3", join=join)
                run = self.run_sequence(slug)
                self.assertEqual(self.statuses(run)["s3"], expect)
                self.assertEqual(run["status"], history.FAILED)

    def test_stop_cancels_the_running_steps_and_skips_the_rest(self):
        slug = self.sequence((self.first, self.values(tag="a", sleep="20")), (self.second, self.values(tag="b")),
                             links=[("s1", "s2", "always")])
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
        self.assertEqual(self.statuses(run), {"s1": history.CANCELLED, "s2": sequence_runner.SKIPPED})
        self.assertFalse(sequencer.cancel(rid))

    def test_every_step_is_checked_before_anything_runs(self):
        slug = self.sequence((self.first, self.values(out="")), (self.second, self.values()))
        data = sequences.load(slug)
        data["steps"].append({**data["steps"][1], "id": "s3", "script": "0123456789ab", "path": "", "name": "Gone"})
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

    def test_first_version_run_records_show_as_a_chain(self):
        view = sequence_runner.live_view({"id": "SEQ-20260101-001", "status": history.FAILED, "steps": [
            {"index": 1, "status": history.FAILED, "on_failure": "continue", "script_path": "C:/a.py"},
            {"index": 2, "status": history.SUCCESS, "on_failure": "stop"}]})
        self.assertEqual([s["id"] for s in view["steps"]], ["s1", "s2"])
        self.assertEqual(view["links"], [{"from": "s1", "to": "s2", "when": "always"}])
        self.assertEqual(view["steps"][0]["ext"], "py")


# ---------------------------------------------------------------------- web ---
class WebTests(SequenceCase):
    def graph(self, slug, **body):
        r = self.client.post(f"/sequences/{slug}/graph/", data=json.dumps(body), content_type="application/json")
        return r, r.json()

    def post_step(self, slug, step_id, **form):
        base = {"source": "values", "host": "", "p_out": str(self.out), "p_tag": "w", "p_sleep": "0"}
        return self.client.post(f"/sequences/{slug}/steps/{step_id}/", {**base, **form})

    def test_build_configure_and_run(self):
        self.assertEqual(self.client.get("/sequences/").status_code, 200)
        r = self.client.post("/sequences/", {"name": "Web flow"})
        self.assertRedirects(r, "/sequences/web-flow/", fetch_redirect_response=False)
        a, b = self.script(self.first), self.script(self.second)
        page = self.client.get("/sequences/web-flow/")
        self.assertContains(page, f'data-id="{a.id}"')                 # the explorer offers scripts to drag
        self.assertContains(page, 'id="seq-graph"')
        self.assertContains(page, 'id="seq-tabs"')                     # the centre tab bar
        self.assertContains(page, f'data-ws="{self.root}"')            # open tabs are kept per open folder
        # every page knows the sequences, so an open sequence is a tab next to the scripts
        script_page = self.client.get(f"/scripts/{a.id}/")
        self.assertContains(script_page, 'id="wb-sequences"')
        self.assertContains(script_page, '"slug": "web-flow"')

        r, d = self.graph("web-flow", op="add", script=a.id, x=100, y=40)
        self.assertEqual((r.status_code, d["id"]), (200, "s1"))
        self.assertEqual(d["graph"]["steps"][0]["script"], a.id)       # a double-click opens it
        r, d = self.graph("web-flow", op="add", script=b.id, x=100, y=200)
        self.assertEqual(d["id"], "s2")
        self.assertEqual([p["input"] for p in d["graph"]["passwords"]], ["pw_s1_password", "pw_s2_password"])
        r, d = self.graph("web-flow", op="link", **{"from": "s1", "to": "s2", "when": "success"})
        self.assertEqual(d["graph"]["links"], [{"from": "s1", "to": "s2", "when": "success"}])
        r, d = self.graph("web-flow", op="link", **{"from": "s2", "to": "s1"})
        self.assertEqual(r.status_code, 400)                             # a loop
        self.assertIn("loop", d["error"])
        self.assertEqual(len(d["graph"]["links"]), 1)
        r, d = self.graph("web-flow", op="move", positions={"s2": [300, 200]})
        self.assertEqual([(s["x"], s["y"]) for s in d["graph"]["steps"]], [(100, 40), (300, 200)])
        self.assertEqual(self.graph("web-flow", op="add", script="0123456789ab")[0].status_code, 400)
        self.assertEqual(self.graph("web-flow", op="explode")[0].status_code, 400)
        self.assertEqual(self.client.get("/sequences/web-flow/graph/").json()["graph"]["steps"][0]["name"], "First")

        embed = self.client.get("/sequences/web-flow/steps/s1/?embed=1")
        self.assertContains(embed, 'name="p_out"')
        self.assertContains(embed, 'name="embed" value="1"')
        self.assertNotContains(embed, 'class="activity"')                # no shell inside the right-hand column
        r = self.post_step("web-flow", "s1", p_tag="a", p_password="never-saved", embed="1")
        self.assertRedirects(r, "/sequences/web-flow/steps/s1/?embed=1&saved=1", fetch_redirect_response=False)
        self.assertContains(self.client.get(r["Location"]), "seq-step-saved")
        self.assertEqual(self.post_step("web-flow", "s2", p_tag="b").status_code, 302)
        self.assertEqual(self.post_step("web-flow", "s2", p_out="").status_code, 400)    # required value missing
        text = (config.SEQUENCES_DIR / "web-flow.yaml").read_text(encoding="utf-8")
        self.assertNotIn("never-saved", text)
        self.assertNotIn("password", text)

        token = uuid.uuid4().hex
        r = self.client.post("/sequences/web-flow/start/", {"submit_token": token, "pw_s1_password": "s3cret"})
        self.assertEqual(r.status_code, 302)
        rid = r["Location"].rstrip("/").rsplit("/", 1)[1]
        self.assertTrue(sequence_runner.valid_run_id(rid), r["Location"])
        again = self.client.post("/sequences/web-flow/start/", {"submit_token": token, "pw_s1_password": "s3cret"})
        self.assertEqual(again["Location"], r["Location"])                # a double submit starts nothing new
        self.assertTrue(sequencer.wait_idle(60))
        self.assertEqual(self.lines(), ["start a 6", "end a", "start b 0", "end b"])
        first_run = sequence_runner.read_run(rid)["steps"][0]["run_id"]
        self.assertContains(self.client.get(f"/executions/{first_run}/"), f"/sequence-runs/{rid}/")

        page = self.client.get(f"/sequence-runs/{rid}/")
        self.assertContains(page, "Web flow")
        self.assertContains(page, 'id="seq-graph"')
        status = self.client.get(f"/sequence-runs/{rid}/status/").json()
        self.assertEqual(status["status"], history.SUCCESS)
        self.assertEqual([s["status"] for s in status["graph"]["steps"]], [history.SUCCESS, history.SUCCESS])
        self.assertEqual(status["graph"]["links"], [{"from": "s1", "to": "s2", "when": "success"}])
        self.assertContains(self.client.get("/sequences/"), rid)
        self.assertNotIn("s3cret", json.dumps(sequence_runner.read_run(rid)))
        self.assertNotIn("s3cret", json.dumps(history.read_meta(first_run)))

        r, d = self.graph("web-flow", op="unlink", **{"from": "s1", "to": "s2"})
        self.assertEqual(d["graph"]["links"], [])
        r, d = self.graph("web-flow", op="remove", id="s1")
        self.assertEqual([s["id"] for s in d["graph"]["steps"]], ["s2"])

    def test_history_lists_sequence_runs_with_their_steps(self):
        slug = self.sequence((self.first, self.values(tag="a")), (self.second, self.values(tag="b")),
                             links=[("s1", "s2", "success")], name="Hist flow")
        run = self.run_sequence(slug)
        rid, steps = run["id"], [s["run_id"] for s in run["steps"]]
        page = self.client.get("/history/")
        self.assertContains(page, f"/sequence-runs/{rid}/")
        self.assertContains(page, "2/2 steps")
        self.assertContains(page, "↳ Hist flow · step 1")                 # a step run links to its sequence run
        for step_run in steps:
            self.assertContains(page, step_run)
        only = self.client.get("/history/?kind=sequences")
        self.assertContains(only, rid)
        self.assertNotContains(only, steps[0])
        scripts_only = self.client.get("/history/?kind=scripts")
        self.assertNotContains(scripts_only, 'class="hist-seq"')
        self.assertContains(scripts_only, steps[0])
        picked = self.client.get(f"/history/?script=seq:{slug}")         # a sequence: its runs and step runs
        self.assertContains(picked, rid)
        self.assertContains(picked, steps[1])
        self.assertContains(self.client.get("/history/?q=hist+flow&kind=sequences"), rid)
        self.assertNotContains(self.client.get("/history/?status=failed&kind=sequences"), f"/sequence-runs/{rid}/")

        r = self.client.post(f"/sequence-runs/{rid}/delete/", follow=True)
        self.assertContains(r, f"Deleted {rid}")
        self.assertIsNone(sequence_runner.read_run(rid))
        self.assertIsNotNone(history.read_meta(steps[0]))                # step runs stay
        self.assertEqual(self.client.post(f"/sequence-runs/{rid}/delete/").status_code, 404)

    def test_old_sequence_runs_follow_history_retention(self):
        rid = sequence_runner._allocate()
        sequence_runner._write_run(rid, {"id": rid, "sequence": "x", "name": "Old", "status": history.SUCCESS,
                                         "started_at": "2020-01-01T10:00:00", "finished_at": "2020-01-01T10:01:00",
                                         "steps": []})
        active = sequence_runner._allocate()
        sequence_runner._write_run(active, {"id": active, "sequence": "x", "name": "Busy", "status": history.RUNNING,
                                            "started_at": "2020-01-01T10:00:00", "steps": []})
        self.assertEqual(sequence_runner.delete_older_than(30), 1)
        self.assertIsNone(sequence_runner.read_run(rid))
        self.assertIsNotNone(sequence_runner.read_run(active))           # running: never removed
        self.assertEqual([r["id"] for r in sequence_runner.active_runs()], [active])

    def test_join_choice_only_with_several_arrows_in(self):
        slug = self.sequence((self.first, self.values()), (self.second, self.values()), (self.third, self.values()),
                             links=[("s1", "s3", "success")], name="Join")
        self.assertNotContains(self.client.get(f"/sequences/{slug}/steps/s3/?embed=1"), 'value="any"')
        sequences.link(slug, "s2", "s3", "always")
        self.assertContains(self.client.get(f"/sequences/{slug}/steps/s3/?embed=1"), 'value="any"')
        self.assertEqual(self.post_step(slug, "s3", join="any").status_code, 302)
        self.assertEqual(sequences.load(slug)["steps"][2]["join"], "any")

    def test_step_using_a_preset(self):
        slug = self.sequence((self.first, self.values()), name="Preset flow")
        script = self.script(self.first)
        presets.save_preset(script, "Prod", script.meta["parameters"], self.values(tag="from-preset"), "local")
        r = self.post_step(slug, "s1", source="prod")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sequences.load(slug)["steps"][0]["preset"], "prod")
        self.assertEqual(self.client.get(f"/sequences/{slug}/graph/").json()["graph"]["steps"][0]["source"],
                         "Preset: Prod")
        r = self.client.post(f"/sequences/{slug}/start/", {"submit_token": uuid.uuid4().hex})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(sequencer.wait_idle(60))
        self.assertEqual(self.lines(), ["start from-preset 0", "end from-preset"])

    def test_problems_are_shown_and_nothing_starts(self):
        slug = self.sequence((self.first, self.values(out="")), name="Broken")
        r = self.client.post(f"/sequences/{slug}/start/", {"submit_token": uuid.uuid4().hex}, follow=True)
        self.assertContains(r, "Step 1 (First): Out:")
        self.assertEqual(sequence_runner.list_runs(), [])
        self.assertEqual(list(config.EXECUTIONS_DIR.iterdir()), [])

    def test_side_panel_lists_favourites_first(self):
        from runner import workspace
        a = self.sequence((self.first, self.values()), name="Alpha")
        z = self.sequence((self.first, self.values()), name="Zulu")
        page = self.client.get(f"/sequences/{a}/")
        self.assertContains(page, 'id="seq-side"')
        self.assertContains(page, 'id="tree"')                            # the scripts to drag, below
        html = page.content.decode()
        self.assertLess(html.index(">Alpha<"), html.index(">Zulu<"))      # by name

        r = self.client.post(f"/sequences/{z}/favorite/", {"on": "1", "next": f"/sequences/{a}/"})
        self.assertRedirects(r, f"/sequences/{a}/", fetch_redirect_response=False)
        self.assertEqual(workspace.sequence_favorites(), [z])
        html = self.client.get(f"/sequences/{a}/").content.decode()
        self.assertLess(html.index(">Zulu<"), html.index(">Alpha<"))      # favourites first
        self.assertContains(self.client.get("/sequences/"), "seq-card")    # and on the start page
        self.assertNotIn("favorite", (config.SEQUENCES_DIR / f"{z}.yaml").read_text(encoding="utf-8"))
        self.client.post(f"/sequences/{z}/favorite/", {"on": "0", "next": "https://evil.example/"})
        self.assertEqual(workspace.sequence_favorites(), [])
        self.assertEqual(self.client.post("/sequences/nope/favorite/", {"on": "1"}).status_code, 404)

        r = self.client.post(f"/sequences/{a}/rename/", {"name": "Alpha two", "next": "/history/"})
        self.assertRedirects(r, "/history/", fetch_redirect_response=False)
        r = self.client.post(f"/sequences/{a}/rename/", {"name": "Alpha 3", "next": "//evil.example/"})
        self.assertRedirects(r, f"/sequences/{a}/", fetch_redirect_response=False)    # not a local path

        self.client.post(f"/sequences/{z}/favorite/", {"on": "1"})
        r = self.client.post(f"/sequences/{z}/delete/", {"next": f"/sequences/{z}/"})     # its own page is gone
        self.assertRedirects(r, "/sequences/", fetch_redirect_response=False)
        self.assertEqual(workspace.sequence_favorites(), [])              # no favourite left behind
        r = self.client.post(f"/sequences/{a}/delete/", {"next": "/history/"})
        self.assertRedirects(r, "/history/", fetch_redirect_response=False)

    def test_delete(self):
        slug = self.sequence((self.first, self.values()), name="Gone soon")
        r = self.client.post(f"/sequences/{slug}/delete/")
        self.assertRedirects(r, "/sequences/", fetch_redirect_response=False)
        self.assertFalse((config.SEQUENCES_DIR / f"{slug}.yaml").exists())

    def test_unknown_ids_are_404(self):
        slug = self.sequence((self.first, self.values()), name="Known")
        for url in ("/sequences/nope/", "/sequences/BAD!/", "/sequence-runs/RUN-20260101-001/",
                    "/sequence-runs/SEQ-20260101-001/", "/sequences/nope/steps/s1/", f"/sequences/{slug}/steps/s9/",
                    "/sequences/nope/graph/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post("/sequences/nope/start/").status_code, 404)
