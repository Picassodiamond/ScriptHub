"""Builds and supervises the OS process for one execution.

Rules that matter for safety:
* Processes are always started from an argument LIST with shell=False.
* For remote runs the only string handed to a shell is the remote command,
  and every element of it passes through shlex.quote.
* Batch files get a strict character check (cmd.exe re-parses arguments).
"""
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ssh_hosts, tracing

LOCAL = ssh_hosts.LOCAL
BATCH_EXTENSIONS = (".bat", ".cmd")
# Characters cmd.exe treats specially; never allowed in batch arguments.
BATCH_FORBIDDEN = set('&|<>^%!"`\r\n\x00')
PID_MARKER = b"__SR_PID__"
# Reads the script from stdin; first the interpreter's "read program from stdin" flags.
STDIN_FLAGS = {"bash": ["-s", "--"], "sh": ["-s", "--"], "zsh": ["-s", "--"], "dash": ["-s", "--"]}
# Runs on the remote host: report our PID, change directory, then exec the interpreter.
REMOTE_WRAPPER = 'printf "__SR_PID__%s\\n" "$$"; cd -- "$1" || exit 126; shift; exec env "$@"'


class ExecutionError(Exception):
    """A run could not be prepared (shown to the user)."""


@dataclass
class ExecutionSpec:
    script_id: str
    script_name: str
    script_path: str
    extension: str
    interpreter: str
    host: str
    argv: list
    env: dict = field(default_factory=dict)
    cwd: str | None = None
    timeout: int = 3600
    # A script runs one at a time (further runs wait in the queue); tests may lift it.
    allow_concurrent: bool = False
    parameters: dict = field(default_factory=dict)   # redacted copy for history
    display_command: str = ""
    token: str | None = None
    # Line tracing: {} when requested (the manager adds "stopfile"), None when off.
    trace: dict | None = None
    # Step debugging (implies tracing): the process reads control lines from stdin.
    debug: bool = False


@dataclass
class Command:
    args: list
    stdin: bytes | None
    env: dict
    cwd: str | None
    remote: bool
    keep_stdin_open: bool = False    # debugger: stdin stays open as the control channel


def _exe_stem(command: str) -> str:
    base = os.path.basename(command.strip().strip('"')).lower()
    return base.rsplit(".", 1)[0] if "." in base else base


def resolve_interpreter(meta_interpreter, extension: str, host: str, cfg: dict) -> str:
    """Interpreter command for this script on this host."""
    if meta_interpreter:
        return meta_interpreter
    table = cfg["interpreters"] if host == LOCAL else cfg["remote_interpreters"]
    if extension.lower() not in table:
        where = "locally" if host == LOCAL else f"on remote hosts"
        raise ExecutionError(f"No interpreter is configured for {extension} files {where}.")
    return table[extension.lower()]


def check_batch_args(argv) -> None:
    for a in argv:
        bad = sorted(c for c in set(a) if c in BATCH_FORBIDDEN)
        if bad:
            shown = " ".join(repr(c)[1:-1] for c in bad)
            raise ExecutionError(
                f"Value {a!r} contains characters that are not allowed for batch files ({shown}). "
                "cmd.exe would interpret them as commands."
            )


def trace_supported(extension: str, interpreter: str) -> bool:
    parts = ssh_hosts.split_command(interpreter or "")
    return bool(parts) and tracing.kind(extension, _exe_stem(parts[0])) is not None


def debug_supported(extension: str, interpreter: str) -> bool:
    return trace_supported(extension, interpreter)


DEBUG_UNSUPPORTED = "Step debugging is available for Python and Bash scripts only."


def _split(command: str):
    return ssh_hosts.split_command(command)


def build_command(spec: ExecutionSpec, cfg: dict) -> Command:
    path = Path(spec.script_path)
    env_extra = dict(spec.env)
    interp = _split(spec.interpreter)
    if not interp:
        raise ExecutionError("The interpreter is empty.")
    stem = _exe_stem(interp[0])
    is_python = stem.startswith("python")
    if is_python:
        env_extra.setdefault("PYTHONUNBUFFERED", "1")
        env_extra.setdefault("PYTHONUTF8", "1")

    traced = spec.trace is not None or spec.debug
    trace_kind = tracing.kind(spec.extension, stem) if traced else None
    if spec.debug and trace_kind is None:
        raise ExecutionError(DEBUG_UNSUPPORTED)
    if traced and trace_kind is None:
        raise ExecutionError("Line tracing is available for Python and Bash scripts only.")

    if spec.host == LOCAL:
        if spec.debug:
            return _local_debug(spec, path, interp, stem, trace_kind, env_extra)
        if trace_kind:
            return _local_traced(spec, path, interp, stem, trace_kind, env_extra)
        if spec.extension in BATCH_EXTENSIONS:
            if os.name != "nt":
                raise ExecutionError("Batch files can only run on Windows.")
            check_batch_args(spec.argv)
            if stem == "cmd":
                args = ["cmd.exe", "/d", "/c", str(path), *spec.argv]
            else:
                args = [*interp, str(path), *spec.argv]
        elif stem in ("python", "python3"):
            args = [sys.executable, str(path), *spec.argv]
        elif os.name == "nt" and stem in ("bash", "sh", "zsh"):
            args = [*interp, path.as_posix(), *spec.argv]
        else:
            args = [*interp, str(path), *spec.argv]
        env = {**os.environ, **env_extra}
        return Command(args, None, env, spec.cwd or str(path.parent), remote=False)

    # ---- remote (SSH) ----
    if spec.extension in BATCH_EXTENSIONS:
        raise ExecutionError("Batch files can only run locally.")
    if not ssh_hosts.is_valid_host(spec.host):
        raise ExecutionError(f"Invalid SSH host: {spec.host!r}")
    try:
        script_bytes = path.read_bytes()
    except OSError as exc:
        raise ExecutionError(f"Could not read the script: {exc}") from exc
    flags = STDIN_FLAGS.get(stem, ["-"])
    keep_open = False
    if spec.debug:
        # stdin = "<N>\n" + N bytes of script, then the debugger's control lines.
        keep_open = True
        if trace_kind == "python":
            flags = ["-c", tracing.PY_DEBUG_LAUNCHER, "-"]
        else:
            script_bytes = tracing.bash_debug_prologue(1).encode() + script_bytes
            flags = ["-c", tracing.BASH_DEBUG_LOADER, "sr-debug"]
        script_bytes = str(len(script_bytes)).encode() + b"\n" + script_bytes
    elif trace_kind == "python":
        flags = ["-c", tracing.PY_LAUNCHER, "-"]
    elif trace_kind == "bash":
        script_bytes = tracing.bash_prologue_line() + script_bytes
    remote_env = [f"{k}={v}" for k, v in env_extra.items()]
    parts = ["sh", "-c", REMOTE_WRAPPER, "sh", spec.cwd or ".", *remote_env, *interp, *flags, *spec.argv]
    remote_command = " ".join(shlex.quote(p) for p in parts)
    args = ssh_hosts.ssh_base(cfg) + ["-T", "--", spec.host, remote_command]
    return Command(args, script_bytes, dict(os.environ), None, remote=True, keep_stdin_open=keep_open)


def _local_debug(spec, path, interp, stem, trace_kind, env_extra) -> Command:
    stopfile = (spec.trace or {}).get("stopfile") or ""
    cwd = spec.cwd or str(path.parent)
    if trace_kind == "python":
        exe = [sys.executable] if stem in ("python", "python3") else interp
        args = [*exe, "-c", tracing.PY_DEBUG_LAUNCHER, str(path), *spec.argv]
    else:
        folder = Path(stopfile).parent if stopfile else path.parent
        prologue = folder / "debug_prologue.sh"
        prologue.write_bytes(tracing.bash_debug_prologue_file().encode())
        env_extra["BASH_ENV"] = prologue.as_posix() if os.name == "nt" else str(prologue)
        script_arg = path.as_posix() if os.name == "nt" else str(path)
        args = [*interp, script_arg, *spec.argv]
    return Command(args, b"", {**os.environ, **env_extra}, cwd, remote=False, keep_stdin_open=True)


def _local_traced(spec, path, interp, stem, trace_kind, env_extra) -> Command:
    stopfile = (spec.trace or {}).get("stopfile") or ""
    env_extra["SR_TRACE_STOPFILE"] = stopfile
    cwd = spec.cwd or str(path.parent)
    if trace_kind == "python":
        exe = [sys.executable] if stem in ("python", "python3") else interp
        args = [*exe, "-c", tracing.PY_LAUNCHER, str(path), *spec.argv]
        env = {**os.environ, **env_extra}
    else:
        prologue = Path(stopfile).with_name("trace_prologue.sh") if stopfile else path.with_suffix(".trace.sh")
        prologue.write_bytes(tracing.bash_prologue_file(stopfile).encode())
        shown = lambda p: (Path(p).as_posix() if os.name == "nt" else str(p)) if p else ""
        env_extra["SR_TRACE_STOPFILE"] = shown(stopfile)
        env_extra["BASH_ENV"] = shown(prologue)
        script_arg = path.as_posix() if os.name == "nt" else str(path)
        args = [*interp, script_arg, *spec.argv]
        env = {**os.environ, **env_extra}
    return Command(args, None, env, cwd, remote=False)


def popen_flags() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Stop a local process and its children."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def remote_stop(spec: ExecutionSpec, remote_pid, cfg: dict) -> None:
    """Ask the remote host to stop the script (best effort)."""
    if not isinstance(remote_pid, int) or remote_pid <= 1:
        return
    n = remote_pid
    cmd = (
        f"pkill -TERM -P {n} 2>/dev/null; kill -TERM {n} 2>/dev/null; i=0; "
        f"while kill -0 {n} 2>/dev/null && [ $i -lt 5 ]; do sleep 1; i=$((i+1)); done; "
        f"pkill -KILL -P {n} 2>/dev/null; kill -KILL {n} 2>/dev/null; true"
    )
    try:
        ssh_hosts.run_remote(spec.host, cmd, timeout=30, cfg=cfg)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass


def stop(spec: ExecutionSpec, proc: subprocess.Popen, remote_pid, cfg: dict) -> None:
    if spec.host != LOCAL:
        remote_stop(spec, remote_pid, cfg)
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    else:
        kill_tree(proc)


def pump(stream, path: Path, on_marker=None, on_trace=None, on_event=None) -> None:
    """Copy ``stream`` into ``path`` as data arrives (unbuffered, so the
    browser sees output live). ``on_marker(pid)`` is called if the very first
    line is the remote PID marker; that line is not written to the log."""
    reader = getattr(stream, "read1", stream.read)
    checking = on_marker is not None
    pending = b""
    filt = tracing.MarkerFilter(on_trace, on_event) if (on_trace or on_event) else None
    with open(path, "ab", buffering=0) as fh:
        while True:
            try:
                chunk = reader(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            if filt:
                chunk = filt.feed(chunk)
                if not chunk:
                    continue
            if checking:
                pending += chunk
                if b"\n" not in pending and len(pending) < 256:
                    continue
                checking = False
                first, sep, rest = pending.partition(b"\n")
                pending = b""
                if first.startswith(PID_MARKER):
                    try:
                        on_marker(int(first[len(PID_MARKER):].strip()))
                    except ValueError:
                        pass
                    chunk = rest
                else:
                    chunk = first + sep + rest
                if not chunk:
                    continue
            fh.write(chunk)
        if checking and pending:
            fh.write(pending)
        if filt:
            fh.write(filt.flush())


def feed_stdin(proc: subprocess.Popen, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        proc.stdin.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
