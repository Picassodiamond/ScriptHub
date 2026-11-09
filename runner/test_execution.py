"""Execution engine tests: real subprocesses, no mocks of the process layer.

python manage.py test runner.test_execution
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from runner import executor, history
from runner.executor import ExecutionSpec
from runner.process_manager import ExecutionManager, pid_alive

PY = sys.executable


def wait_for(predicate, timeout=15.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


class EngineCase(SimpleTestCase):
    max_concurrent = 3
    per_host = 3

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-test-"))
        self.scripts = self.tmp / "scripts dir"      # a space on purpose
        self.scripts.mkdir()
        (self.tmp / "executions").mkdir()
        cfg_file = self.tmp / "config.json"
        cfg_file.write_text(json.dumps({
            "script_directories": [str(self.scripts)],
            "max_concurrent_executions": self.max_concurrent,
            "max_concurrent_per_host": self.per_host,
        }))
        for p in (mock.patch.object(config, "CONFIG_FILE", cfg_file),
                  mock.patch.object(config, "EXECUTIONS_DIR", self.tmp / "executions")):
            p.start()
            self.addCleanup(p.stop)
        self.mgr = ExecutionManager()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for rid in list(self.mgr._active):
            self.mgr.cancel(rid)
        self.mgr.wait_idle(20)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def script(self, name, body):
        p = self.scripts / name
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def spec(self, path, argv=(), **kw):
        defaults = dict(
            script_id=kw.pop("script_id", path.stem), script_name=path.stem, script_path=str(path),
            extension=path.suffix, interpreter="python", host="local", argv=list(argv),
            timeout=30, allow_concurrent=True,
        )
        defaults.update(kw)
        return ExecutionSpec(**defaults)

    def meta(self, run_id):
        return history.read_meta(run_id)

    def read_log(self, run_id, stream):
        try:
            return history.log_path(run_id, stream).read_text(encoding="utf-8")
        except FileNotFoundError:   # not created yet
            return ""

    def finished(self, run_id, timeout=20):
        assert wait_for(lambda: self.meta(run_id)["status"] in history.FINAL_STATUSES, timeout), self.meta(run_id)
        return self.meta(run_id)


class BasicExecutionTests(EngineCase):
    def test_success_passes_arguments_and_separates_streams(self):
        p = self.script("echo.py", """
            import sys
            print("args:", sys.argv[1:])
            print("to-stderr", file=sys.stderr)
        """)
        rid = self.mgr.submit(self.spec(p, ["--name", "a b; rm -rf /", "x"]))
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0))
        self.assertIn("['--name', 'a b; rm -rf /', 'x']", self.read_log(rid, "stdout"))  # no shell parsing
        self.assertNotIn("to-stderr", self.read_log(rid, "stdout"))
        self.assertIn("to-stderr", self.read_log(rid, "stderr"))
        self.assertIsNotNone(m["duration_seconds"])
        self.assertTrue(m["id"].startswith("RUN-"))

    def test_failure_reports_exit_code(self):
        p = self.script("fail.py", "import sys; print('boom', file=sys.stderr); sys.exit(3)")
        m = self.finished(self.mgr.submit(self.spec(p)))
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 3))
        self.assertIn("code 3", m["error"])

    def test_environment_and_working_directory(self):
        p = self.script("env.py", "import os; print(os.environ['MY_VAR'], os.getcwd())")
        work = self.tmp / "work"
        work.mkdir()
        rid = self.mgr.submit(self.spec(p, env={"MY_VAR": "hello"}, cwd=str(work)))
        self.finished(rid)
        out = self.read_log(rid, "stdout").split()
        self.assertEqual(out[0], "hello")
        self.assertEqual(os.path.realpath(" ".join(out[1:])), os.path.realpath(work))

    def test_output_is_live_not_buffered_until_exit(self):
        p = self.script("live.py", """
            import time
            print("first", flush=True)
            time.sleep(3)
            print("second")
        """)
        rid = self.mgr.submit(self.spec(p))
        self.assertTrue(wait_for(lambda: "first" in self.read_log(rid, "stdout"), 2.5))
        self.assertEqual(self.meta(rid)["status"], "running")   # still running while output is visible
        self.finished(rid)

    def test_missing_interpreter_fails_cleanly(self):
        p = self.script("x.py", "print(1)")
        m = self.finished(self.mgr.submit(self.spec(p, interpreter="definitely-not-installed-xyz")))
        self.assertEqual(m["status"], "failed")
        self.assertIn("Could not start", m["error"])
        self.assertIn("Could not start", self.read_log(m["id"], "stderr"))

    def test_run_ids_are_unique_and_dated(self):
        p = self.script("n.py", "print(1)")
        ids = [self.mgr.submit(self.spec(p)) for _ in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(history.valid_run_id(i) for i in ids))
        self.mgr.wait_idle()


class StopAndTimeoutTests(EngineCase):
    SLEEPER = """
        import time
        print("started", flush=True)
        time.sleep(60)
    """

    def test_timeout_stops_the_process(self):
        p = self.script("slow.py", self.SLEEPER)
        t0 = time.monotonic()
        rid = self.mgr.submit(self.spec(p, timeout=2))
        m = self.finished(rid)
        self.assertEqual(m["status"], "timed_out")
        self.assertLess(time.monotonic() - t0, 15)
        self.assertFalse(pid_alive(m["pid"]))

    def test_cancel_running_process(self):
        p = self.script("slow.py", self.SLEEPER)
        rid = self.mgr.submit(self.spec(p))
        self.assertTrue(wait_for(lambda: "started" in self.read_log(rid, "stdout")))
        pid = self.meta(rid)["pid"]
        self.assertTrue(self.mgr.cancel(rid))
        m = self.finished(rid)
        self.assertEqual(m["status"], "cancelled")
        self.assertFalse(pid_alive(pid))

    def test_cancel_kills_child_processes_too(self):
        p = self.script("parent.py", f"""
            import subprocess, sys, time
            child = subprocess.Popen([{PY!r}, "-c", "import time; time.sleep(60)"])
            print(child.pid, flush=True)
            time.sleep(60)
        """)
        rid = self.mgr.submit(self.spec(p))
        self.assertTrue(wait_for(lambda: self.read_log(rid, "stdout").strip().isdigit()))
        child_pid = int(self.read_log(rid, "stdout").strip())
        self.assertTrue(pid_alive(child_pid))
        self.mgr.cancel(rid)
        self.finished(rid)
        self.assertTrue(wait_for(lambda: not pid_alive(child_pid), 10))

    def test_cancel_unknown_run(self):
        self.assertFalse(self.mgr.cancel("RUN-20200101-001"))

    def test_success_is_not_reported_before_exit(self):
        p = self.script("slow.py", self.SLEEPER)
        rid = self.mgr.submit(self.spec(p))
        wait_for(lambda: self.meta(rid)["status"] == "running")
        self.assertNotEqual(self.meta(rid)["status"], "success")
        self.assertIsNone(self.meta(rid)["exit_code"])


class ConcurrencyTests(EngineCase):
    max_concurrent = 1

    def test_second_run_is_queued_then_started(self):
        p = self.script("short.py", "import time; time.sleep(1.5); print('done')")
        first = self.mgr.submit(self.spec(p, script_id="a"))
        second = self.mgr.submit(self.spec(p, script_id="b"))
        self.assertEqual(self.meta(second)["status"], "queued")
        self.assertEqual(self.mgr.queue_position(second), 1)
        self.assertEqual(self.finished(first)["status"], "success")
        self.assertEqual(self.finished(second)["status"], "success")
        # strictly sequential
        self.assertLessEqual(self.meta(first)["finished_at"], self.meta(second)["started_at"])

    def test_cancel_queued_run(self):
        p = self.script("short.py", "import time; time.sleep(2)")
        self.mgr.submit(self.spec(p, script_id="a"))
        queued = self.mgr.submit(self.spec(p, script_id="b"))
        self.assertTrue(self.mgr.cancel(queued))
        m = self.meta(queued)
        self.assertEqual(m["status"], "cancelled")
        self.assertIsNone(m["started_at"])
        self.assertIsNone(self.mgr.queue_position(queued))


class PerScriptConcurrencyTests(EngineCase):
    def test_non_concurrent_script_waits_but_others_do_not(self):
        p = self.script("work.py", "import time; time.sleep(2)")
        other = self.script("other.py", "print('quick')")
        a1 = self.mgr.submit(self.spec(p, script_id="solo", allow_concurrent=False))
        a2 = self.mgr.submit(self.spec(p, script_id="solo", allow_concurrent=False))
        b = self.mgr.submit(self.spec(other, script_id="other"))
        self.assertEqual(self.meta(a2)["status"], "queued")       # blocked by a1...
        self.assertEqual(self.finished(b)["status"], "success")   # ...but does not block others
        self.assertEqual(self.meta(a2)["status"], "queued")
        self.assertEqual(self.finished(a1)["status"], "success")
        self.assertEqual(self.finished(a2)["status"], "success")

    def test_concurrent_script_runs_in_parallel(self):
        p = self.script("work.py", "import time; time.sleep(2)")
        a = self.mgr.submit(self.spec(p, script_id="par", allow_concurrent=True))
        b = self.mgr.submit(self.spec(p, script_id="par", allow_concurrent=True))
        self.assertTrue(wait_for(lambda: self.meta(a)["status"] == "running" and self.meta(b)["status"] == "running", 5))
        self.mgr.wait_idle()


class PerHostConcurrencyTests(EngineCase):
    per_host = 1

    def test_host_limit_applies_per_host(self):
        p = self.script("work.py", "import time; time.sleep(1.5)")
        first = self.mgr.submit(self.spec(p, script_id="a"))
        second = self.mgr.submit(self.spec(p, script_id="b"))
        self.assertEqual(self.meta(second)["status"], "queued")
        self.finished(first)
        self.finished(second)


class DuplicateAndRecoveryTests(EngineCase):
    def test_duplicate_submit_token_returns_same_run(self):
        p = self.script("n.py", "print(1)")
        a = self.mgr.submit(self.spec(p, token="tok-1"))
        b = self.mgr.submit(self.spec(p, token="tok-1"))
        self.assertEqual(a, b)
        self.assertEqual(len(history.list_run_ids()), 1)
        self.mgr.wait_idle()

    def test_recovery_marks_orphaned_runs_interrupted(self):
        for rid, owner, status in (("RUN-20260101-001", 99999999, "running"),
                                   ("RUN-20260101-002", 99999999, "queued"),
                                   ("RUN-20260101-003", os.getpid(), "running"),
                                   ("RUN-20260101-004", 99999999, "success")):
            history.run_dir(rid).mkdir(parents=True)
            history.write_meta(rid, {"id": rid, "status": status, "owner_pid": owner,
                                     "host": "local", "started_at": history.now_iso(), "pid": None})
        fixed = self.mgr.recover_interrupted()
        self.assertEqual(sorted(fixed), ["RUN-20260101-001", "RUN-20260101-002"])
        self.assertEqual(self.meta("RUN-20260101-001")["status"], "interrupted")
        self.assertEqual(self.meta("RUN-20260101-003")["status"], "running")   # live owner untouched
        self.assertEqual(self.meta("RUN-20260101-004")["status"], "success")
        self.assertIsNotNone(self.meta("RUN-20260101-001")["finished_at"])

    def test_history_survives_a_new_manager(self):
        p = self.script("n.py", "print('persisted')")
        rid = self.mgr.submit(self.spec(p))
        self.finished(rid)
        fresh = ExecutionManager()          # simulates an application restart
        fresh.recover_interrupted()
        self.assertEqual(history.read_meta(rid)["status"], "success")
        self.assertIn("persisted", self.read_log(rid, "stdout"))


class LogChunkTests(SimpleTestCase):
    def test_chunk_never_splits_multibyte_characters(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "out.log"
        data = ("héllo ✓ wörld " * 20).encode("utf-8")
        f.write_bytes(data)
        offset, text = 0, ""
        while True:
            part, new = history.read_chunk(f, offset, limit=7)
            if new == offset:
                break
            text += part
            offset = new
        self.assertEqual(text, data.decode("utf-8"))
        self.assertNotIn("�", text)


@unittest.skipUnless(os.name == "nt", "batch files are Windows-only")
class BatchExecutionTests(EngineCase):
    def test_batch_file_runs_with_positional_argument(self):
        bat = self.script("hello.bat", "@echo off\r\necho Hello %~1\r\nexit /b 0\r\n")
        rid = self.mgr.submit(self.spec(bat, ["World"], interpreter="cmd", extension=".bat"))
        m = self.finished(rid)
        self.assertEqual(m["status"], "success")
        self.assertIn("Hello World", self.read_log(rid, "stdout"))

    def test_batch_exit_code_is_reported(self):
        bat = self.script("bad.bat", "@echo off\r\nexit /b 7\r\n")
        m = self.finished(self.mgr.submit(self.spec(bat, interpreter="cmd", extension=".bat")))
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 7))

    def test_batch_rejects_command_injection_characters(self):
        marker = self.tmp / "pwned.txt"
        bat = self.script("echo.bat", "@echo off\r\necho %~1\r\n")
        for evil in ("a & echo x > " + str(marker), "a | dir", "%PATH%", 'a" & dir', "a^b", "!x!"):
            with self.subTest(value=evil):
                m = self.finished(self.mgr.submit(self.spec(bat, [evil], interpreter="cmd", extension=".bat")))
                self.assertEqual(m["status"], "failed")
                self.assertIn("not allowed for batch files", m["error"])
        self.assertFalse(marker.exists())

    def test_sample_batch_scripts(self):
        base = config.BASE_DIR / "sample_scripts" / "system"
        m = self.finished(self.mgr.submit(self.spec(base.parent / "network" / "ping_host.bat", ["127.0.0.1", "1"],
                                                    interpreter="cmd", extension=".bat")))
        self.assertEqual(m["status"], "success", self.read_log(m["id"], "stderr"))
        m = self.finished(self.mgr.submit(self.spec(base / "list_directory.bat", [str(base)],
                                                    interpreter="cmd", extension=".bat",
                                                    env={"LIST_MODE": "brief"})))
        self.assertEqual(m["status"], "success")
        self.assertIn("list_directory.bat", self.read_log(m["id"], "stdout"))
        m = self.finished(self.mgr.submit(self.spec(base / "list_directory.bat", [str(base / "nope")],
                                                    interpreter="cmd", extension=".bat")))
        self.assertEqual(m["status"], "failed")


class CommandBuildingTests(SimpleTestCase):
    CFG = {"interpreters": {".py": "python"}, "remote_interpreters": {".py": "python3", ".sh": "bash"},
           "ssh_executable": "ssh", "ssh_connect_timeout": 10}

    def spec(self, **kw):
        base = dict(script_id="s", script_name="s", script_path="/tmp/x.py", extension=".py",
                    interpreter="python3", host="local", argv=[])
        base.update(kw)
        return ExecutionSpec(**base)

    def test_remote_command_is_fully_quoted_and_streams_script(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.py"
            path.write_text("print('hi')")
            nasty = "$(touch /tmp/pwned); `id` 'q' \"d\""
            spec = self.spec(script_path=str(path), host="prod-1", argv=["--name", nasty],
                             env={"A": "b c"}, cwd="/srv/app dir")
            cmd = executor.build_command(spec, self.CFG)
        self.assertTrue(cmd.remote)
        self.assertEqual(cmd.stdin, b"print('hi')")
        self.assertEqual(cmd.args[:7], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-T", "--"])
        self.assertEqual(cmd.args[7], "prod-1")
        import shlex
        remote_argv = shlex.split(cmd.args[8])          # what the remote shell will see
        self.assertEqual(remote_argv[:2], ["sh", "-c"])
        tail = remote_argv[4:]
        self.assertEqual(tail[0], "/srv/app dir")
        self.assertIn("A=b c", tail)
        self.assertIn("PYTHONUNBUFFERED=1", tail)
        self.assertEqual(tail[-3:], ["-", "--name", nasty])  # hostile text survives as ONE argument

    def test_bash_scripts_use_stdin_flags_remotely(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.sh"
            path.write_text("echo hi")
            cmd = executor.build_command(self.spec(script_path=str(path), extension=".sh", interpreter="bash",
                                                   host="h1", argv=["a"]), self.CFG)
        import shlex
        self.assertEqual(shlex.split(cmd.args[8])[-4:], ["bash", "-s", "--", "a"])

    def test_invalid_hosts_and_batch_remote_rejected(self):
        for host in ("-oProxyCommand=evil", "a b", "x;y", ""):
            with self.subTest(host=host):
                with self.assertRaises(executor.ExecutionError):
                    executor.build_command(self.spec(host=host), self.CFG)
        with self.assertRaises(executor.ExecutionError):
            executor.build_command(self.spec(host="h1", extension=".bat", script_path="/tmp/x.bat"), self.CFG)

    def test_resolve_interpreter_per_target(self):
        self.assertEqual(executor.resolve_interpreter(None, ".py", "local", self.CFG), "python")
        self.assertEqual(executor.resolve_interpreter(None, ".py", "h1", self.CFG), "python3")
        self.assertEqual(executor.resolve_interpreter("custom", ".py", "h1", self.CFG), "custom")
        with self.assertRaises(executor.ExecutionError):
            executor.resolve_interpreter(None, ".bat", "h1", self.CFG)


@unittest.skipUnless(shutil.which("bash"), "bash is needed to emulate a remote host")
class FakeSshRemoteTests(EngineCase):
    """End-to-end remote execution against a fake `ssh` that runs the remote
    command in a local bash. Proves streaming, PID capture and quoting."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_ssh.py"
        self.log = self.tmp / "ssh_calls.log"
        self.fake.write_text(textwrap.dedent(f"""
            import subprocess, sys
            args = sys.argv[1:]
            i = args.index("--")
            host, command = args[i + 1], " ".join(args[i + 2:])
            with open({str(self.log)!r}, "a") as fh:
                fh.write(host + " :: " + command + "\\n")
            if host == "down":
                sys.stderr.write("ssh: connect to host down port 22: Connection refused\\n")
                sys.exit(255)
            sys.exit(subprocess.call(["bash", "-c", command]))
        """))
        cfg = config.load_config()
        cfg["ssh_executable"] = f'"{PY}" "{self.fake}"'
        cfg["remote_interpreters"] = {".py": PY.replace("\\", "/"), ".sh": "bash"}
        patcher = mock.patch.object(config, "load_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_remote_python_script_streams_over_stdin_with_args(self):
        p = self.script("remote.py", """
            import sys, os
            print("argv", sys.argv[1:])
            print("who", os.environ.get("REMOTE_VAR"))
            print("err", file=sys.stderr)
        """)
        spec = self.spec(p, ["--x", "one two", "$HOME"], host="prod-1", interpreter=PY.replace("\\", "/"),
                         env={"REMOTE_VAR": "yes"})
        rid = self.mgr.submit(spec)
        m = self.finished(rid)
        self.assertEqual((m["status"], m["exit_code"]), ("success", 0), self.read_log(rid, "stderr"))
        out = self.read_log(rid, "stdout")
        self.assertIn("argv ['--x', 'one two', '$HOME']", out)   # not expanded by the remote shell
        self.assertIn("who yes", out)
        self.assertNotIn("__SR_PID__", out)                      # marker line is stripped
        self.assertIn("err", self.read_log(rid, "stderr"))
        self.assertIsInstance(m["remote_pid"], int)

    def test_remote_bash_script(self):
        p = self.script("remote.sh", 'echo "n=$#"; echo "first=$1"\n')
        rid = self.mgr.submit(self.spec(p, ["a b", "c"], host="prod-1", interpreter="bash", extension=".sh"))
        m = self.finished(rid)
        self.assertEqual(m["status"], "success", self.read_log(rid, "stderr"))
        self.assertIn("n=2", self.read_log(rid, "stdout"))
        self.assertIn("first=a b", self.read_log(rid, "stdout"))

    def test_connection_failure_is_reported_as_ssh_failure(self):
        p = self.script("remote.py", "print(1)")
        m = self.finished(self.mgr.submit(self.spec(p, host="down", interpreter=PY.replace("\\", "/"))))
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 255))
        self.assertIn("SSH connection failed", m["error"])
        self.assertIn("Connection refused", self.read_log(m["id"], "stderr"))

    def test_remote_failure_exit_code(self):
        p = self.script("remote.py", "import sys; sys.exit(4)")
        m = self.finished(self.mgr.submit(self.spec(p, host="prod-1", interpreter=PY.replace("\\", "/"))))
        self.assertEqual((m["status"], m["exit_code"]), ("failed", 4))

    def test_cancel_sends_remote_stop_command(self):
        p = self.script("remote.py", "import time; print('up', flush=True); time.sleep(20)")
        rid = self.mgr.submit(self.spec(p, host="prod-1", interpreter=PY.replace("\\", "/")))
        self.assertTrue(wait_for(lambda: "up" in self.read_log(rid, "stdout"), 15))
        remote_pid = self.meta(rid)["remote_pid"]
        self.assertTrue(wait_for(lambda: isinstance(self.meta(rid)["remote_pid"], int), 5))
        t0 = time.monotonic()
        self.mgr.cancel(rid)
        ok = wait_for(lambda: self.meta(rid)["status"] in history.FINAL_STATUSES, 60)
        calls = self.log.read_text()
        self.assertTrue(ok, f"still running after {time.monotonic() - t0:.0f}s\nssh calls:\n{calls}\nmeta: {self.meta(rid)}")
        self.assertEqual(self.meta(rid)["status"], "cancelled")
        self.assertIn(f"kill -TERM {self.meta(rid)['remote_pid']}", calls)
