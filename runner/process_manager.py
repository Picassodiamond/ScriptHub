"""In-process execution manager: queue, concurrency limits, worker threads,
cancel and timeout. No database, no task queue; runs are threads inside the
Django process and everything durable lives in runtime/executions/.

If the application stops, running scripts are not supervised any more. On the
next start such runs are marked 'interrupted'.
"""
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import config
from . import executor, history
from .executor import ExecutionError, ExecutionSpec

TRACE_LOG_MAX = 1_000_000   # bytes of trace.log kept per run
TOKEN_TTL = 600  # seconds a submit token is remembered (double-click guard)
DEBUG_ACTIONS = ("step", "continue", "pause", "detach", "breakpoints")
MAX_BREAKPOINTS = 1000


def _debug_blank(state="starting"):
    return {"state": state, "line": None, "scopes": [], "stack": [], "breakpoints": []}


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _Active:
    def __init__(self, run_id, spec):
        self.run_id = run_id
        self.spec = spec
        self.proc = None
        self.remote_pid = None
        self.cancel = threading.Event()
        self.reason = None          # None | 'cancelled' | 'timed_out'
        self.thread = None
        # Step debugger: state shown in the console, and the control channel (stdin).
        self.dbg = _debug_blank() if spec.debug else None
        self.wlock = threading.Lock()   # one writer on stdin at a time
        self.pending = []               # control lines sent before the debuggee first paused
        self.stdin_open = False


class ExecutionManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._queue = deque()
        self._specs = {}     # queued run_id -> spec
        self._active = {}    # running run_id -> _Active
        self._tokens = {}    # submit token -> (run_id, time)

    # ------------------------------------------------------------ public ---
    def submit(self, spec: ExecutionSpec, initiator: str = "local") -> str:
        with self._lock:
            now = time.monotonic()
            self._tokens = {t: v for t, v in self._tokens.items() if now - v[1] < TOKEN_TTL}
            if spec.token and spec.token in self._tokens:
                return self._tokens[spec.token][0]  # duplicate submission
            if spec.debug and spec.trace is None:
                spec.trace = {}            # debugging always reports the running line too
            run_id, run_dir = history.allocate_run_dir()
            if spec.trace is not None:
                # What the console shows next to the output: the source as it was when the run started.
                try:
                    (run_dir / "trace_source").write_bytes(Path(spec.script_path).read_bytes()[:512 * 1024])
                except OSError:
                    pass
            history.write_meta(run_id, {
                "id": run_id,
                "script_id": spec.script_id,
                "script_name": spec.script_name,
                "script_path": spec.script_path,
                "host": spec.host,
                "interpreter": spec.interpreter,
                "command": spec.display_command,
                "parameters": spec.parameters,
                "status": history.QUEUED,
                "queued_at": history.now_iso(),
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "exit_code": None,
                "pid": None,
                "remote_pid": None,
                "timeout": spec.timeout,
                "initiator": initiator,
                "trace": {"enabled": True, "stopped": False} if spec.trace is not None else None,
                "debug": {"enabled": True} if spec.debug else None,
                "owner_pid": os.getpid(),
                "error": None,
            })
            self._specs[run_id] = spec
            self._queue.append(run_id)
            if spec.token:
                self._tokens[spec.token] = (run_id, now)
            self._dispatch()
            return run_id

    def cancel(self, run_id: str) -> bool:
        """Cancel a queued or running execution. Returns False if not active."""
        with self._lock:
            if run_id in self._specs:  # still queued
                self._queue.remove(run_id)
                del self._specs[run_id]
                now = history.now_iso()
                history.update_meta(run_id, status=history.CANCELLED, finished_at=now,
                                    error="Cancelled while queued.")
                return True
            active = self._active.get(run_id)
            if active is None:
                return False
            active.reason = active.reason or "cancelled"
            active.cancel.set()
            return True

    def stop_trace(self, run_id: str) -> bool:
        """Stop line tracing for the rest of a running script (it then runs at full speed)."""
        with self._lock:
            active = self._active.get(run_id)
        if active is None or active.spec.trace is None:
            return False
        if active.spec.debug:
            return self.debug_command(run_id, "detach")
        spec = active.spec
        if spec.host == executor.LOCAL:
            stopfile = spec.trace.get("stopfile")
            if not stopfile:
                return False
            try:
                Path(stopfile).write_bytes(b"stop")
            except OSError:
                return False
        else:
            pid = active.remote_pid
            if not isinstance(pid, int) or pid <= 1:
                return False
            cfg = config.load_config()
            threading.Thread(target=self._remote_signal, args=(spec.host, pid, cfg), daemon=True).start()
        history.update_meta(run_id, trace={"enabled": True, "stopped": True})
        return True

    @staticmethod
    def _remote_signal(host, pid, cfg) -> None:
        try:
            executor.ssh_hosts.run_remote(host, f"kill -USR1 {int(pid)} 2>/dev/null; true", timeout=30, cfg=cfg)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    # ------------------------------------------------------------- debugger ---
    def debug_state(self, run_id: str):
        """What the step debugger of a run is doing, or None if it is not a debug run
        managed here (finished runs included)."""
        with self._lock:
            active = self._active.get(run_id)
            if active is not None and active.dbg is not None:
                d = active.dbg
                return {"state": d["state"], "line": d["line"], "scopes": list(d["scopes"]),
                        "stack": list(d["stack"]), "breakpoints": list(d["breakpoints"])}
            spec = self._specs.get(run_id)
            if spec is not None and spec.debug:
                return _debug_blank()
        return None

    def debug_command(self, run_id: str, action: str, lines=None) -> bool:
        """Send step / continue / pause / detach / breakpoints to a running debug run."""
        if action not in DEBUG_ACTIONS:
            return False
        with self._lock:
            active = self._active.get(run_id)
        if active is None or active.dbg is None:
            return False
        bps = []
        if action == "breakpoints":
            bps = sorted({int(n) for n in (lines or []) if int(n) >= 1})[:MAX_BREAKPOINTS]
            text = "bp " + ",".join(map(str, bps))
        else:
            text = action
        with active.wlock:
            with self._lock:
                d = active.dbg
                if d["state"] == "detached" or active.reason:
                    return False
                queued = d["state"] == "starting"     # sent when the debuggee first stops
                if action == "breakpoints":
                    d["breakpoints"] = bps
                elif action == "detach":
                    d["state"] = "detached"
                elif action in ("step", "continue") and d["state"] == "paused":
                    d["state"] = "running"           # set before writing: the next 'P' must win
                if queued:
                    active.pending.append(text)
            ok = queued or self._write_control(active, [text])
        if ok and action == "detach":
            history.update_meta(run_id, trace={"enabled": True, "stopped": True})
        return ok

    @staticmethod
    def _write_control(active, lines) -> bool:
        """Caller holds active.wlock."""
        proc = active.proc
        if proc is None or not active.stdin_open or proc.stdin is None:
            return False
        try:
            proc.stdin.write("".join(t + "\n" for t in lines).encode("ascii", "replace"))
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _debug_event(self, active):
        """Marker callback (stderr pump thread): P paused, R resumed, V variables."""
        def on_event(kind, value):
            with active.wlock:
                pending = []
                with self._lock:
                    d = active.dbg
                    if kind == "P":
                        if d["state"] != "detached":
                            d["state"] = "paused"
                        d.update(line=value, scopes=[], stack=[])
                        pending, active.pending = active.pending, []
                    elif kind == "R":
                        if d["state"] == "paused":
                            d["state"] = "running"
                    elif kind == "V":
                        scopes, stack = value.get("scopes"), value.get("stack")
                        d["scopes"] = scopes if isinstance(scopes, list) else []
                        d["stack"] = stack if isinstance(stack, list) else []
                if pending:
                    self._write_control(active, pending)
        return on_event

    def _open_stdin(self, active, data: bytes) -> None:
        """Debug runs: write the initial bytes and keep stdin open for control lines."""
        with active.wlock:
            try:
                if data:
                    active.proc.stdin.write(data)
                active.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def _close_stdin(self, active) -> None:
        with active.wlock:
            active.stdin_open = False
            try:
                if active.proc is not None and active.proc.stdin is not None:
                    active.proc.stdin.close()
            except (OSError, ValueError):
                pass

    def queue_position(self, run_id: str):
        with self._lock:
            try:
                return list(self._queue).index(run_id) + 1
            except ValueError:
                return None

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active or run_id in self._specs

    def counts(self) -> dict:
        with self._lock:
            return {"running": len(self._active), "queued": len(self._queue)}

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until nothing is running or queued (used by tests)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._active and not self._queue:
                    return True
            time.sleep(0.05)
        return False

    def recover_interrupted(self) -> list:
        """Mark runs left 'queued'/'running' by a stopped app as 'interrupted'."""
        fixed = []
        me = os.getpid()
        for meta in history.list_runs(statuses=history.ACTIVE_STATUSES):
            run_id = meta["id"]
            owner = meta.get("owner_pid")
            with self._lock:
                if run_id in self._active or run_id in self._specs:
                    continue
            if owner == me or pid_alive(owner):
                continue  # owned by a live application instance
            note = "The application stopped before this run finished."
            pid = meta.get("pid")
            if meta.get("status") == history.RUNNING and meta.get("host") == "local" and pid_alive(pid):
                note += f" The script process (PID {pid}) may still be running."
            history.update_meta(run_id, status=history.INTERRUPTED,
                                finished_at=history.now_iso(), error=note)
            fixed.append(run_id)
        return fixed

    # ---------------------------------------------------------- internals ---
    def _can_start(self, spec: ExecutionSpec, cfg: dict) -> bool:
        if len(self._active) >= cfg["max_concurrent_executions"]:
            return False
        same_host = sum(1 for a in self._active.values() if a.spec.host == spec.host)
        if same_host >= cfg["max_concurrent_per_host"]:
            return False
        if not spec.allow_concurrent and any(a.spec.script_id == spec.script_id for a in self._active.values()):
            return False
        return True

    def _dispatch(self) -> None:
        with self._lock:
            cfg = config.load_config()
            for run_id in list(self._queue):
                spec = self._specs[run_id]
                if not self._can_start(spec, cfg):
                    continue  # blocked runs are skipped, not waited on
                self._queue.remove(run_id)
                del self._specs[run_id]
                active = _Active(run_id, spec)
                self._active[run_id] = active
                active.thread = threading.Thread(target=self._run, args=(active,), name=f"run-{run_id}", daemon=True)
                active.thread.start()

    def _finish(self, active: _Active, status, exit_code, error=None) -> None:
        now = history.now_iso()
        meta = history.read_meta(active.run_id) or {}
        started = history.parse_iso(meta.get("started_at"))
        duration = None
        if started is not None:
            duration = round(max(0.0, (history.parse_iso(now) - started).total_seconds()), 1)
        history.update_meta(active.run_id, status=status, exit_code=exit_code,
                            finished_at=now, duration_seconds=duration, error=error)

    def _run(self, active: _Active) -> None:
        spec, run_id = active.spec, active.run_id
        cfg = config.load_config()
        try:
            history.update_meta(run_id, status=history.RUNNING, started_at=history.now_iso())
            if spec.trace is not None:
                spec.trace["stopfile"] = str(history.run_dir(run_id) / "trace.stop")
            try:
                cmd = executor.build_command(spec, cfg)
            except ExecutionError as exc:
                self._write_stderr(run_id, f"[scripthub] {exc}\n")
                self._finish(active, history.FAILED, None, str(exc))
                return
            try:
                proc = subprocess.Popen(
                    cmd.args, shell=False,
                    stdin=subprocess.PIPE if cmd.stdin is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=cmd.cwd, env=cmd.env, **executor.popen_flags(),
                )
            except OSError as exc:
                msg = f"Could not start the process: {exc}"
                self._write_stderr(run_id, f"[scripthub] {msg}\n")
                self._finish(active, history.FAILED, None, msg)
                return
            active.proc = proc
            active.stdin_open = cmd.keep_stdin_open
            history.update_meta(run_id, pid=proc.pid)

            def on_marker(pid):
                active.remote_pid = pid
                history.update_meta(run_id, remote_pid=pid)

            out_t = threading.Thread(target=executor.pump, args=(proc.stdout, history.log_path(run_id, "stdout"),
                                     on_marker if cmd.remote else None), daemon=True)
            on_trace = self._trace_writer(run_id) if spec.trace is not None else None
            on_event = self._debug_event(active) if active.dbg is not None else None
            err_t = threading.Thread(target=executor.pump, args=(proc.stderr, history.log_path(run_id, "stderr"),
                                     None, on_trace, on_event), daemon=True)
            out_t.start()
            err_t.start()
            if cmd.keep_stdin_open:
                threading.Thread(target=self._open_stdin, args=(active, cmd.stdin or b""), daemon=True).start()
            elif cmd.stdin is not None:
                threading.Thread(target=executor.feed_stdin, args=(proc, cmd.stdin), daemon=True).start()

            deadline = time.monotonic() + spec.timeout
            while True:
                try:
                    proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if active.cancel.is_set() or time.monotonic() >= deadline:
                    if not active.cancel.is_set():
                        active.reason = "timed_out"
                    executor.stop(spec, proc, active.remote_pid, cfg)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    break

            if cmd.keep_stdin_open:
                self._close_stdin(active)
            for t in (out_t, err_t):
                t.join(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass

            code = proc.returncode
            if active.reason == "cancelled":
                self._finish(active, history.CANCELLED, code, "Stopped by user.")
            elif active.reason == "timed_out":
                self._finish(active, history.TIMED_OUT, code, f"Exceeded the {spec.timeout}s timeout and was stopped.")
            elif cmd.remote and code == 255:
                self._finish(active, history.FAILED, code,
                             "SSH connection failed (see stderr). Check that the host is reachable and key-based login works.")
            elif code == 0:
                self._finish(active, history.SUCCESS, 0)
            else:
                self._finish(active, history.FAILED, code, f"Exited with code {code}.")
        except Exception as exc:  # never leave a run stuck in 'running'
            self._write_stderr(run_id, f"[scripthub] internal error: {exc!r}\n")
            self._finish(active, history.FAILED, None, f"Internal error: {exc!r}")
        finally:
            with self._lock:
                self._active.pop(run_id, None)
            self._dispatch()

    @staticmethod
    def _trace_writer(run_id):
        folder = history.run_dir(run_id)

        def write(nums):
            try:
                log = folder / "trace.log"
                if not log.exists() or log.stat().st_size < TRACE_LOG_MAX:
                    with open(log, "ab") as fh:
                        fh.write((chr(10).join(map(str, nums)) + chr(10)).encode())
                (folder / "trace.last").write_text(str(nums[-1]))
            except OSError:
                pass
        return write

    @staticmethod
    def _write_stderr(run_id, text) -> None:
        try:
            with open(history.log_path(run_id, "stderr"), "ab") as fh:
                fh.write(text.encode("utf-8", errors="replace"))
        except OSError:
            pass


manager = ExecutionManager()
