"""Line tracing: report which source line of a Python or Bash script is running.

The script is not modified. Python runs through a small launcher (below) that
reports line numbers; Bash gets a DEBUG trap. Both write markers of the form
NUL "SRL" <n>[,<n>...] NUL to stderr. ``MarkerFilter`` removes them from the
stream again, so stderr.log stays clean, and hands the numbers to the manager.

Tracing can be stopped for the rest of a run (the script then runs at full
speed): locally through a stop file, on SSH hosts with SIGUSR1.
"""
import re

SUPPORTED = (".py", ".sh")
START = b"\0SRL"
_BODY = re.compile(rb"[0-9,]*")

# Run with `python -c LAUNCHER <script path | -> args...`; "-" reads the script from stdin.
PY_LAUNCHER = r'''
import os, signal, sys, threading, time, traceback
def _main():
    target = sys.argv[1]
    stopfile = os.environ.pop("SR_TRACE_STOPFILE", "")
    if target == "-":
        fn = "<stdin>"
        src = sys.stdin.buffer.read()
        sys.argv = ["-"] + sys.argv[2:]
    else:
        fn = os.path.abspath(target)
        with open(fn, "rb") as fh:
            src = fh.read()
        sys.argv = [fn] + sys.argv[2:]
        sys.path[0] = os.path.dirname(fn)
    code = compile(src, fn, "exec")
    buf, stop = [], threading.Event()
    def emit():
        items = buf[:]
        del buf[:len(items)]
        if items:
            if len(items) > 300:
                items = items[:100] + items[-200:]
            try:
                os.write(2, b"\0SRL" + ",".join(map(str, items)).encode() + b"\0")
            except OSError:
                pass
    mon = getattr(sys, "monitoring", None)
    if mon is not None:
        tool = 3
        mon.use_tool_id(tool, "script-runner")
        def on_line(c, line):
            if c.co_filename == fn:
                buf.append(line)
            else:
                return mon.DISABLE
        mon.register_callback(tool, mon.events.LINE, on_line)
        mon.set_events(tool, mon.events.LINE)
        def off():
            mon.set_events(tool, 0)
    else:
        def tracer(frame, event, arg):
            if stop.is_set():
                sys.settrace(None)
                return None
            if frame.f_code.co_filename != fn:
                return None
            def local(frame, event, arg):
                if stop.is_set():
                    frame.f_trace = None
                    sys.settrace(None)
                    return None
                if event == "line":
                    buf.append(frame.f_lineno)
                return local
            return local
        sys.settrace(tracer)
        threading.settrace(tracer)
        def off():
            pass
    def stop_now(*_):
        stop.set()
        off()
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, stop_now)
    done = threading.Event()
    def watch():
        while not done.wait(0.2):
            if stopfile and os.path.exists(stopfile) and not stop.is_set():
                stop_now()
            emit()
    threading.Thread(target=watch, daemon=True).start()
    g = {"__name__": "__main__", "__builtins__": __builtins__, "__doc__": None}
    if target != "-":
        g["__file__"] = fn
    try:
        exec(code, g)
    except SystemExit:
        raise
    except BaseException as exc:
        stop_now()
        done.set()
        emit()
        traceback.print_exception(type(exc), exc, exc.__traceback__.tb_next)
        sys.exit(1)
    finally:
        done.set()
        stop_now()
        emit()
_main()
'''

_MARK = r'printf "\0SRL%s\0" "$LINENO" >&2'


def bash_prologue_file(stopfile: str) -> str:
    """BASH_ENV file for local runs (the script file itself stays untouched)."""
    return (
        "unset BASH_ENV\n"
        '__sr_stop="$SR_TRACE_STOPFILE"; unset SR_TRACE_STOPFILE\n'
        "set -T\n"
        "trap '[ -e \"$__sr_stop\" ] && { trap - DEBUG; true; } || " + _MARK + "' DEBUG\n"
    )


def bash_prologue_line() -> bytes:
    """One line put in front of a script streamed to a remote bash (shifts lines by 1)."""
    mark = r'printf "\0SRL%s\0" "$((LINENO-1))" >&2'
    return ("unset BASH_ENV; set -T; trap 'trap - DEBUG' USR1; trap '" + mark + "' DEBUG\n").encode()


def kind(extension: str, interpreter_stem: str):
    """'python', 'bash' or None when this script cannot be traced."""
    if extension == ".py" and interpreter_stem.startswith("python"):
        return "python"
    if extension == ".sh" and interpreter_stem == "bash":
        return "bash"
    return None


class MarkerFilter:
    """Strips trace markers out of a byte stream; chunk boundaries are handled."""

    def __init__(self, on_trace):
        self.on_trace = on_trace
        self.carry = b""

    def feed(self, data: bytes) -> bytes:
        data = self.carry + data
        self.carry = b""
        out = []
        while True:
            i = data.find(START)
            if i < 0:
                keep = 0                                  # a marker may start at the very end
                for n in range(min(len(START) - 1, len(data)), 0, -1):
                    if data.endswith(START[:n]):
                        keep = n
                        break
                out.append(data[:len(data) - keep])
                self.carry = data[len(data) - keep:]
                break
            out.append(data[:i])
            j = data.find(b"\0", i + len(START))
            if j < 0:
                if len(data) - i > 4096:                  # far too long to be a marker
                    out.append(data[i:i + 1])
                    data = data[i + 1:]
                    continue
                self.carry = data[i:]
                break
            body = data[i + len(START):j]
            if _BODY.fullmatch(body):
                nums = [int(x) for x in body.split(b",") if x]
                if nums:
                    self.on_trace(nums)
                data = data[j + 1:]
            else:
                out.append(data[i:i + 1])
                data = data[i + 1:]
        return b"".join(out)

    def flush(self) -> bytes:
        rest, self.carry = self.carry, b""
        return rest
