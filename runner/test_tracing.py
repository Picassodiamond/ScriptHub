"""Line tracing: markers, local Python/Bash runs, stopping a trace mid-run, SSH, web."""
import os
import shutil
import sys
import textwrap
import time
import unittest
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import executor, history, tracing
from runner.executor import ExecutionError
from runner.process_manager import manager
from runner.test_execution import PY, EngineCase, FakeSshRemoteTests, wait_for

BASH = shutil.which("bash")


def trace_numbers(case, rid):
    try:
        text = (history.run_dir(rid) / "trace.log").read_text()
    except FileNotFoundError:
        return []
    return [int(x) for x in text.split()]


class MarkerFilterTests(SimpleTestCase):
    def run_filter(self, chunks):
        seen = []
        f = tracing.MarkerFilter(seen.extend)
        out = b"".join(f.feed(c) for c in chunks) + f.flush()
        return out, seen

    def test_markers_removed_and_numbers_reported(self):
        out, seen = self.run_filter([b"hello\n\0SRL3\0world\n\0SRL4,5,6\0"])
        self.assertEqual(out, b"hello\nworld\n")
        self.assertEqual(seen, [3, 4, 5, 6])

    def test_marker_split_across_chunks(self):
        data = b"a\0SRL12,13\0b"
        for cut in range(1, len(data)):
            out, seen = self.run_filter([data[:cut], data[cut:]])
            self.assertEqual((out, seen), (b"ab", [12, 13]), cut)

    def test_not_a_marker_is_kept(self):
        out, seen = self.run_filter([b"x\0SRLabc\0y", b"\0SR"])
        self.assertEqual(seen, [])
        self.assertEqual(out, b"x\0SRLabc\0y\0SR")


class CommandTests(SimpleTestCase):
    def test_only_python_and_bash(self):
        self.assertTrue(executor.trace_supported(".py", "python3"))
        self.assertTrue(executor.trace_supported(".py", '"C:/Python312/python.exe"'))
        self.assertTrue(executor.trace_supported(".sh", "bash"))
        self.assertFalse(executor.trace_supported(".sh", "sh"))
        self.assertFalse(executor.trace_supported(".bat", "cmd"))

    def test_unsupported_script_is_refused(self):
        spec = executor.ExecutionSpec(script_id="x", script_name="x", script_path="x.bat", extension=".bat",
                                      interpreter="cmd", host="local", argv=[], trace={})
        with self.assertRaises(ExecutionError):
            executor.build_command(spec, config.load_config())


class LocalPythonTraceTests(EngineCase):
    def test_reports_lines_keeps_output_and_stderr_clean(self):
        p = self.script("t.py", """
            import sys
            print("one")
            x = 1
            print("two", sys.argv[1:], __name__, file=sys.stderr)
        """)
        rid = self.mgr.submit(self.spec(p, ["a b"], trace={}))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").strip(), "one")
        self.assertEqual(self.read_log(rid, "stderr").strip(), "two ['a b'] __main__")   # no markers
        nums = trace_numbers(self, rid)
        self.assertEqual(nums[0], 2)          # line 1 is blank (dedent keeps the leading newline)
        self.assertEqual(nums[-1], 5)
        self.assertEqual(sorted(set(nums)), [2, 3, 4, 5])
        self.assertTrue((history.run_dir(rid) / "trace_source").exists())
        self.assertTrue(m["trace"]["enabled"])

    def test_uncaught_exception_traceback_and_exit_code(self):
        p = self.script("boom.py", """
            def f():
                raise ValueError("bad")
            f()
        """)
        rid = self.mgr.submit(self.spec(p, trace={}))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 1))
        err = self.read_log(rid, "stderr")
        self.assertIn("ValueError: bad", err)
        self.assertIn("boom.py", err)
        self.assertNotIn('File "<string>"', err)       # the launcher frame is hidden
        self.assertEqual(trace_numbers(self, rid)[-1], 3)

    def test_sys_exit_code_and_imports_next_to_script(self):
        (self.scripts / "helper_mod.py").write_text("VALUE = 7\n")
        p = self.script("ex.py", """
            import helper_mod, sys
            print(helper_mod.VALUE)
            sys.exit(3)
        """)
        m = self.finished(self.mgr.submit(self.spec(p, trace={})))
        self.assertEqual(m["exit_code"], 3)

    def test_stop_tracing_mid_run_keeps_script_running(self):
        p = self.script("loop.py", """
            import time
            for i in range(60):
                time.sleep(0.1)
            print("done")
        """)
        rid = self.mgr.submit(self.spec(p, trace={}))
        self.assertTrue(wait_for(lambda: len(trace_numbers(self, rid)) > 3, 15), "no trace appeared")
        self.assertTrue(self.mgr.stop_trace(rid))
        self.assertTrue(self.meta(rid)["trace"]["stopped"])
        time.sleep(1.0)                                  # let the launcher notice and flush
        size = (history.run_dir(rid) / "trace.log").stat().st_size
        self.assertEqual(self.meta(rid)["status"], "running")
        time.sleep(1.0)
        self.assertEqual((history.run_dir(rid) / "trace.log").stat().st_size, size)   # nothing new
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0))
        self.assertIn("done", self.read_log(rid, "stdout"))

    def test_stop_trace_on_untraced_or_finished_run_is_refused(self):
        p = self.script("q.py", "print(1)")
        rid = self.mgr.submit(self.spec(p))
        self.finished(rid)
        self.assertFalse(self.mgr.stop_trace(rid))
        self.assertIsNone(self.meta(rid)["trace"])


@unittest.skipUnless(BASH, "bash is needed")
class LocalBashTraceTests(EngineCase):
    def test_bash_lines_reported_and_script_unchanged(self):
        p = self.script("t.sh", """
            #!/usr/bin/env bash
            echo "start"
            f() {
              echo "in f"
            }
            f
            echo "end $0" >&2
        """)
        # fixtures must use LF endings
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        rid = self.mgr.submit(self.spec(p, interpreter="bash", extension=".sh", trace={}))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        self.assertIn("start", self.read_log(rid, "stdout"))
        err = self.read_log(rid, "stderr")
        self.assertIn("end ", err)
        self.assertNotIn("SRL", err)
        self.assertNotIn("\0", err)
        nums = trace_numbers(self, rid)
        self.assertEqual(set(nums), {3, 4, 5, 7, 8})     # echo, f() definition, echo in f, call f, final echo
        self.assertEqual(nums[-1], 8)
        self.assertIn("t.sh", err)                       # $0 is still the real script

    def test_bash_trace_stops_on_request(self):
        p = self.script("loop.sh", """
            for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
              sleep 0.1
            done
            echo finished
        """)
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        rid = self.mgr.submit(self.spec(p, interpreter="bash", extension=".sh", trace={}))
        self.assertTrue(wait_for(lambda: len(trace_numbers(self, rid)) > 3, 15))
        self.assertTrue(self.mgr.stop_trace(rid))
        time.sleep(0.6)
        size = (history.run_dir(rid) / "trace.log").stat().st_size
        time.sleep(0.8)
        self.assertEqual((history.run_dir(rid) / "trace.log").stat().st_size, size)
        m = self.finished(rid)
        self.assertEqual(m["status"], "success")
        self.assertIn("finished", self.read_log(rid, "stdout"))


@unittest.skipUnless(BASH, "bash is needed to emulate a remote host")
class RemoteTraceTests(FakeSshRemoteTests):
    def test_remote_python_trace(self):
        p = self.script("r.py", """
            print("hi")
            y = 2
        """)
        rid = self.mgr.submit(self.spec(p, host="prod-1", interpreter=PY.replace("\\", "/"), trace={}))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").strip(), "hi")
        self.assertNotIn("SRL", self.read_log(rid, "stderr"))
        self.assertEqual(sorted(set(trace_numbers(self, rid))), [2, 3])

    def test_remote_bash_trace_lines_match_the_file(self):
        p = self.script("r.sh", """
            echo one
            echo two
        """)
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        rid = self.mgr.submit(self.spec(p, host="prod-1", interpreter="bash", extension=".sh", trace={}))
        m = self.finished(rid)
        self.assertEqual(m["status"], "success", self.read_log(rid, "stderr"))
        self.assertEqual(self.read_log(rid, "stdout").split(), ["one", "two"])
        self.assertEqual(sorted(set(trace_numbers(self, rid))), [2, 3])   # prologue line is not counted

    def test_remote_bash_trace_stop_sends_usr1(self):
        p = self.script("r2.sh", """
            for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
              sleep 0.1
            done
            echo finished
        """)
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        rid = self.mgr.submit(self.spec(p, host="prod-1", interpreter="bash", extension=".sh", trace={}))
        self.assertTrue(wait_for(lambda: len(trace_numbers(self, rid)) > 3 and isinstance(self.meta(rid)["remote_pid"], int), 15))
        self.assertTrue(self.mgr.stop_trace(rid))
        self.assertTrue(wait_for(lambda: "kill -USR1" in self.log.read_text(), 10))
        time.sleep(0.8)
        size = (history.run_dir(rid) / "trace.log").stat().st_size
        time.sleep(0.8)
        self.assertEqual((history.run_dir(rid) / "trace.log").stat().st_size, size)
        m = self.finished(rid)
        self.assertEqual(m["status"], "success", self.read_log(rid, "stderr"))
        self.assertIn("finished", self.read_log(rid, "stdout"))


from runner.test_editor_adhoc import WebCase  # noqa: E402


class TraceWebTests(WebCase):
    LOOP = "import time\nfor i in range(60):\n    time.sleep(0.1)\nprint('done')\n"

    def post(self, **over):
        form = {"language": ".py", "code": "print('hi')\nx = 2\n", "host": "local", "timeout": "60",
                "arguments": "", "cwd": "", "trace": "1"}
        form.update(over)
        return self.client.post("/adhoc/", form)

    def test_adhoc_form_offers_the_option(self):
        self.assertContains(self.client.get("/adhoc/"), 'name="trace"')

    def test_traced_adhoc_run_shows_source_and_reports_the_line(self):
        run_id = self.run_id_of(self.post())
        meta = self.wait_final(run_id)
        self.assertTrue(meta["trace"]["enabled"])
        page = self.client.get(f"/executions/{run_id}/")
        self.assertContains(page, 'id="trace-src"')
        self.assertContains(page, 'data-lang="python"')
        self.assertContains(page, "trace/stop/")
        data = self.client.get(f"/executions/{run_id}/status/").json()
        self.assertEqual(data["trace"]["line"], 2)
        self.assertFalse(data["trace"]["stopped"])
        self.assertEqual(history.log_path(run_id, "stderr").read_text(), "")

    def test_untraced_run_has_no_trace_panel(self):
        run_id = self.run_id_of(self.post(trace=""))
        self.wait_final(run_id)
        self.assertNotContains(self.client.get(f"/executions/{run_id}/"), 'id="trace-src"')
        self.assertIsNone(self.client.get(f"/executions/{run_id}/status/").json()["trace"])

    def test_bat_cannot_be_traced(self):
        r = self.post(language=".bat", code="@echo off\r\necho hi\r\n")
        if os.name == "nt":
            self.assertEqual(r.status_code, 400)
            self.assertContains(r, "Python and Bash", status_code=400)

    def test_stop_endpoint_stops_the_trace_while_running(self):
        run_id = self.run_id_of(self.post(code=self.LOOP))
        self.assertTrue(wait_for(lambda: (history.run_dir(run_id) / "trace.last").exists(), 15))
        r = self.client.post(f"/executions/{run_id}/trace/stop/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get(f"/executions/{run_id}/status/").json()["trace"]["stopped"])
        manager.cancel(run_id)
        self.wait_final(run_id)
        self.assertEqual(self.client.post(f"/executions/{run_id}/trace/stop/").status_code, 409)   # not running now
        self.assertEqual(self.client.get(f"/executions/{run_id}/trace/stop/").status_code, 405)   # POST only
