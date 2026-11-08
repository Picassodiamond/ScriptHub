"""Step debugger: markers, local Python/Bash debugging, SSH (fake ssh), web endpoint."""
import json
import os
import shutil
import time
import unittest

from django.test import SimpleTestCase

import config
from runner import executor, history, tracing
from runner.executor import ExecutionError
from runner.test_execution import PY, EngineCase, FakeSshRemoteTests, wait_for

BASH = shutil.which("bash")


def lf(path):
    """Fixtures must use LF endings (bash and the line numbers depend on it)."""
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    return path


class DebugMixin:
    """Helpers on top of EngineCase for driving a debug run."""

    def state(self, rid):
        return self.mgr.debug_state(rid) or {}

    def wait_paused(self, rid, line=None, timeout=20):
        ok = wait_for(lambda: self.state(rid).get("state") == "paused"
                      and (line is None or self.state(rid).get("line") == line), timeout)
        self.assertTrue(ok, f"not paused at {line}: {self.state(rid)} / {self.read_log(rid, 'stderr')}")
        return self.state(rid)

    def step_to(self, rid, line):
        self.assertTrue(self.mgr.debug_command(rid, "step"))
        return self.wait_paused(rid, line)

    def finished(self, rid, timeout=30):
        self.assertTrue(wait_for(lambda: self.meta(rid)["status"] in history.FINAL_STATUSES, timeout),
                        f"run did not finish: {self.state(rid)}")
        return self.meta(rid)


class MarkerTests(SimpleTestCase):
    def run_filter(self, chunks, events=True):
        lines, ev = [], []
        f = tracing.MarkerFilter(lines.extend, (lambda k, v: ev.append((k, v))) if events else None)
        out = b"".join(f.feed(c) for c in chunks) + f.flush()
        return out, lines, ev

    def test_pause_resume_and_variables(self):
        snap = json.dumps({"scopes": [{"name": "Locals", "vars": [{"name": "x", "type": "int", "value": "1"}]}],
                           "stack": [{"fn": "f", "line": 4}]}).encode()
        data = b"a\0SRL3,4\0b\0SRP4\0\0SRV" + snap + b"\0c\0SRR\0d"
        for cut in range(1, len(data)):
            out, lines, ev = self.run_filter([data[:cut], data[cut:]])
            self.assertEqual(out, b"abcd", cut)
            self.assertEqual(lines, [3, 4])
            self.assertEqual([k for k, _ in ev], ["P", "V", "R"])
            self.assertEqual(ev[0][1], 4)
            self.assertEqual(ev[1][1]["stack"][0]["fn"], "f")

    def test_debug_markers_ignored_without_event_callback(self):
        out, lines, ev = self.run_filter([b"x\0SRP4\0y\0SRL2\0"], events=False)
        self.assertEqual(out, b"x\0SRP4\0y")
        self.assertEqual(lines, [2])

    def test_long_variables_marker_is_accepted(self):
        big = json.dumps({"scopes": [{"name": "Globals", "vars": [{"name": "s", "type": "str", "value": "z" * 50000}]}],
                          "stack": []}).encode()
        chunks = [b"\0SRV" + big[:1000], big[1000:30000], big[30000:] + b"\0tail"]
        out, _lines, ev = self.run_filter(chunks)
        self.assertEqual(out, b"tail")
        self.assertEqual(len(ev[0][1]["scopes"][0]["vars"][0]["value"]), 50000)

    def test_bad_bodies_stay_in_the_stream(self):
        out, _lines, ev = self.run_filter([b"\0SRPx\0|\0SRRy\0|\0SRV{nope\0"])
        self.assertEqual(ev, [])
        self.assertEqual(out, b"\0SRPx\0|\0SRRy\0|\0SRV{nope\0")


class CommandTests(SimpleTestCase):
    def test_batch_cannot_be_debugged(self):
        spec = executor.ExecutionSpec(script_id="x", script_name="x", script_path="x.bat", extension=".bat",
                                      interpreter="cmd", host="local", argv=[], trace={}, debug=True)
        with self.assertRaisesRegex(ExecutionError, "Step debugging"):
            executor.build_command(spec, config.load_config())
        self.assertFalse(executor.debug_supported(".bat", "cmd"))
        self.assertTrue(executor.debug_supported(".py", "python3"))

    def test_bash_prologue_is_one_line(self):
        self.assertEqual(tracing.bash_debug_prologue(1).count("\n"), 1)
        self.assertTrue(tracing.bash_debug_prologue(1).endswith("\n"))


class LocalPythonDebugTests(DebugMixin, EngineCase):
    SRC = """\
x = 10
def double(a):
    b = a * 2
    return b
y = double(x)
items = [1, 2, 3]
for i in items:
    y += i
print("y", y)
"""

    def debug_run(self, src=None, **kw):
        p = lf(self.script("dbg.py", src or self.SRC))
        return self.mgr.submit(self.spec(p, debug=True, **kw))

    def test_pauses_on_entry_and_steps_line_by_line(self):
        rid = self.debug_run()
        st = self.wait_paused(rid, 1)
        self.assertEqual(st["stack"], [{"fn": "<module>", "line": 1}])
        self.assertTrue(self.meta(rid)["debug"]["enabled"])
        self.assertTrue(self.meta(rid)["trace"]["enabled"])
        self.step_to(rid, 2)
        self.step_to(rid, 5)
        st = self.step_to(rid, 3)                       # steps into double()
        self.assertEqual([f["fn"] for f in st["stack"]], ["double", "<module>"])
        st = self.step_to(rid, 4)
        local = {v["name"]: v for v in st["scopes"][0]["vars"]}
        self.assertEqual(st["scopes"][0]["name"], "Locals")
        self.assertEqual(local["a"]["value"], "10")
        self.assertEqual(local["b"], {"name": "b", "type": "int", "value": "20"})
        glob = {v["name"] for v in st["scopes"][1]["vars"]}
        self.assertIn("x", glob)
        self.assertNotIn("double", glob)                 # functions are left out at module level
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").strip(), "y 26")
        self.assertEqual(self.read_log(rid, "stderr"), "")       # no markers leak

    def test_breakpoints_and_continue(self):
        rid = self.debug_run()
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.debug_command(rid, "breakpoints", [8]))
        self.assertEqual(self.state(rid)["breakpoints"], [8])
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        st = self.wait_paused(rid, 8)
        self.assertEqual({v["name"]: v["value"] for v in st["scopes"][0]["vars"]}["i"], "1")
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        self.assertTrue(wait_for(lambda: self.state(rid).get("state") == "paused"
                                 and {v["name"]: v["value"] for v in self.state(rid)["scopes"][0]["vars"]}.get("i") == "2", 20))
        self.assertTrue(self.mgr.debug_command(rid, "breakpoints", []))      # clear
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual(m["status"], "success", self.read_log(rid, "stderr"))

    def test_breakpoints_set_before_the_first_stop_are_kept(self):
        rid = self.debug_run()
        self.assertTrue(wait_for(lambda: self.state(rid).get("state") in ("starting", "paused"), 10))
        self.assertTrue(self.mgr.debug_command(rid, "breakpoints", [6]))
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        self.wait_paused(rid, 6)
        self.mgr.debug_command(rid, "detach")
        self.assertEqual(self.finished(rid)["status"], "success")

    def test_pause_while_running(self):
        rid = self.debug_run("import time\nfor i in range(400):\n    time.sleep(0.02)\nprint('done')\n")
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        self.assertTrue(wait_for(lambda: self.state(rid).get("state") == "running", 10))
        time.sleep(0.3)
        self.assertTrue(self.mgr.debug_command(rid, "pause"))
        st = self.wait_paused(rid)
        self.assertIn(st["line"], (2, 3))
        self.assertTrue(self.mgr.debug_command(rid, "detach"))
        self.assertEqual(self.state(rid)["state"], "detached")
        self.assertFalse(self.mgr.debug_command(rid, "step"))           # nothing to control any more
        m = self.finished(rid)
        self.assertEqual(m["status"], "success")
        self.assertTrue(m["trace"]["stopped"])
        self.assertIn("done", self.read_log(rid, "stdout"))

    def test_detach_runs_at_full_speed(self):
        rid = self.debug_run("total = 0\nfor i in range(200000):\n    total += i\nprint(total)\n")
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.stop_trace(rid))                      # stop_trace = detach for debug runs
        m = self.finished(rid, timeout=20)
        self.assertEqual(m["status"], "success")
        self.assertEqual(self.read_log(rid, "stdout").strip(), str(sum(range(200000))))

    def test_cancel_while_paused(self):
        rid = self.debug_run()
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.cancel(rid))
        m = self.finished(rid)
        self.assertEqual(m["status"], "cancelled")
        self.assertIsNone(self.mgr.debug_state(rid))
        self.assertFalse(self.mgr.debug_command(rid, "step"))

    def test_script_and_children_do_not_see_the_control_stream(self):
        rid = self.debug_run("import subprocess, sys\n"
                             "r = subprocess.run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'],"
                             " capture_output=True, text=True)\n"
                             "print('child', r.stdout.strip(), repr(sys.stdin.read()))\n")
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual(m["status"], "success", self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").strip(), "child '' ''")

    def test_uncaught_exception_while_debugging(self):
        rid = self.debug_run("def f():\n    raise ValueError('bad')\nf()\n")
        self.wait_paused(rid, 1)
        self.mgr.debug_command(rid, "continue")
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 1))
        self.assertIn("ValueError: bad", self.read_log(rid, "stderr"))
        self.assertNotIn('File "<string>"', self.read_log(rid, "stderr"))

    def test_unknown_action_and_untraced_runs_are_refused(self):
        p = self.script("plain.py", "import time\ntime.sleep(1)\n")
        rid = self.mgr.submit(self.spec(p))
        self.assertTrue(wait_for(lambda: self.meta(rid)["status"] == "running", 10))
        self.assertIsNone(self.mgr.debug_state(rid))
        self.assertFalse(self.mgr.debug_command(rid, "step"))
        self.assertIsNone(self.meta(rid)["debug"])
        self.finished(rid)
        rid2 = self.debug_run()
        self.wait_paused(rid2, 1)
        self.assertFalse(self.mgr.debug_command(rid2, "jump"))
        self.mgr.debug_command(rid2, "detach")
        self.finished(rid2)


@unittest.skipUnless(BASH, "bash is needed")
class LocalBashDebugTests(DebugMixin, EngineCase):
    SRC = """\
echo one
f() {
  echo "in f $1"
}
f x
echo "two $(echo sub)"
echo three
"""

    def test_step_and_breakpoint(self):
        p = lf(self.script("d.sh", self.SRC))
        rid = self.mgr.submit(self.spec(p, interpreter="bash", extension=".sh", debug=True))
        self.wait_paused(rid, 1)
        self.assertEqual(self.read_log(rid, "stdout"), "")
        self.step_to(rid, 5)
        self.assertTrue(wait_for(lambda: "one" in self.read_log(rid, "stdout"), 10))
        self.step_to(rid, 2)                             # entering f
        self.assertTrue(self.mgr.debug_command(rid, "breakpoints", [7]))
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        st = self.wait_paused(rid, 7)
        self.assertEqual(st["scopes"], [])               # bash: no variables
        self.assertIn("two sub", self.read_log(rid, "stdout"))
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").split("\n")[:4], ["one", "in f x", "two sub", "three"])
        self.assertNotIn("SR", self.read_log(rid, "stderr"))

    def test_exit_code_and_cancel(self):
        p = lf(self.script("e.sh", "echo a\nexit 4\n"))
        rid = self.mgr.submit(self.spec(p, interpreter="bash", extension=".sh", debug=True))
        self.wait_paused(rid, 1)
        self.mgr.debug_command(rid, "continue")
        self.assertEqual(self.finished(rid)["exit_code"], 4)
        rid = self.mgr.submit(self.spec(p, interpreter="bash", extension=".sh", debug=True))
        self.wait_paused(rid, 1)
        self.assertTrue(self.mgr.cancel(rid))
        self.assertEqual(self.finished(rid)["status"], "cancelled")


@unittest.skipUnless(BASH, "bash is needed to emulate a remote host")
class RemoteDebugTests(DebugMixin, FakeSshRemoteTests):
    def test_remote_python_step_and_continue(self):
        p = lf(self.script("r.py", "import sys\na = 5\nb = a + 1\nprint('b', b, sys.argv[1:])\nsys.exit(3)\n"))
        rid = self.mgr.submit(self.spec(p, ["x y"], host="prod-1", interpreter=PY.replace("\\", "/"), debug=True))
        self.wait_paused(rid, 1)
        self.step_to(rid, 2)
        st = self.step_to(rid, 3)
        self.assertEqual({v["name"]: v["value"] for v in st["scopes"][0]["vars"]}["a"], "5")
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 3), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").strip(), "b 6 ['x y']")
        self.assertNotIn("SR", self.read_log(rid, "stderr"))

    def test_remote_bash_step_breakpoint_and_exit_code(self):
        p = lf(self.script("r.sh", 'echo "one $1"\necho two\necho three\nexit 2\n'))
        rid = self.mgr.submit(self.spec(p, ["a b"], host="prod-1", interpreter="bash", extension=".sh", debug=True))
        self.wait_paused(rid, 1)                         # the prologue line is not counted
        self.step_to(rid, 2)
        self.assertTrue(self.mgr.debug_command(rid, "breakpoints", [4]))
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        self.wait_paused(rid, 4)
        self.assertTrue(self.mgr.debug_command(rid, "continue"))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 2), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").split(), ["one", "a", "b", "two", "three"])


from runner.process_manager import manager  # noqa: E402
from runner.test_editor import WebCase  # noqa: E402


class DebugWebTests(WebCase):
    CODE = "x = 1\ny = x + 1\nprint(y)\n"

    def post(self, mode="debug", **post):
        return self.start_script(self.CODE, ".py", mode=mode, **post)

    def debug_url(self, rid):
        return f"/executions/{rid}/debug/"

    def status(self, rid):
        return self.client.get(f"/executions/{rid}/status/").json()

    def test_debug_endpoint_and_status(self):
        rid = self.run_id_of(self.post())
        self.assertTrue(wait_for(lambda: (self.status(rid)["debug"] or {}).get("state") == "paused", 20))
        dbg = self.status(rid)["debug"]
        self.assertEqual(set(dbg), {"state", "line", "scopes", "stack", "breakpoints"})
        self.assertEqual(dbg["line"], 1)
        self.assertEqual(self.client.get(self.debug_url(rid)).status_code, 405)
        self.assertEqual(self.client.post(self.debug_url(rid), {"action": "jump"}).status_code, 400)
        self.assertEqual(self.client.post(self.debug_url(rid), {"action": "breakpoints", "lines": "a,b"}).status_code, 400)
        self.assertEqual(self.client.post(self.debug_url(rid), {"action": "breakpoints", "lines": "0"}).status_code, 400)
        r = self.client.post(self.debug_url(rid), {"action": "breakpoints", "lines": "3, 2"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["debug"]["breakpoints"], [2, 3])
        r = self.client.post(self.debug_url(rid), {"action": "step"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertTrue(wait_for(lambda: self.status(rid)["debug"]["line"] == 2
                                 and self.status(rid)["debug"]["state"] == "paused", 20))
        self.assertEqual(self.status(rid)["trace"]["line"], 2)
        self.client.post(self.debug_url(rid), {"action": "continue"})
        self.assertTrue(wait_for(lambda: self.status(rid)["debug"]["line"] == 3, 20))
        self.client.post(self.debug_url(rid), {"action": "continue"})
        meta = self.wait_final(rid)
        self.assertEqual(meta["status"], "success")
        self.assertEqual(self.status(rid)["debug"]["state"], "ended")
        self.assertEqual(self.status(rid)["debug"]["line"], 3)
        self.assertEqual(self.client.post(self.debug_url(rid), {"action": "step"}).status_code, 409)
        self.assertEqual(history.log_path(rid, "stdout").read_text().strip(), "2")

    def test_untraced_run_is_not_debuggable(self):
        rid = self.run_id_of(self.post(mode="normal"))
        self.wait_final(rid)
        self.assertIsNone(self.status(rid)["debug"])
        self.assertIsNone(history.read_meta(rid)["debug"])
        self.assertEqual(self.client.post(self.debug_url(rid), {"action": "step"}).status_code, 409)

    def test_legacy_trace_checkbox_still_means_trace(self):
        rid = self.run_id_of(self.post(mode="", trace="1"))
        meta = self.wait_final(rid)
        self.assertTrue(meta["trace"]["enabled"])
        self.assertIsNone(meta["debug"])

    def test_batch_cannot_be_debugged(self):
        r = self.start_script("@echo off\r\necho hi\r\n", ".bat", mode="debug")
        if os.name == "nt":
            self.assertContains(r, "Python and Bash scripts only", status_code=400)
