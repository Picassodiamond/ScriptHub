"""Line tracing: report which source line of a Python or Bash script is running.

The script is not modified. Python runs through a small launcher (below) that
reports line numbers; Bash gets a DEBUG trap. Both write markers of the form
NUL "SRL" <n>[,<n>...] NUL to stderr. ``MarkerFilter`` removes them from the
stream again, so stderr.log stays clean, and hands the numbers to the manager.

Tracing can be stopped for the rest of a run (the script then runs at full
speed): locally through a stop file, on SSH hosts with SIGUSR1.

Step debugging builds on the same markers. The debuggee reads control lines
(``step``, ``continue``, ``pause``, ``detach``, ``bp 3,7``) from its stdin,
which the manager keeps open for the whole run, and reports on stderr:
``P<line>`` paused, ``R`` resumed, ``V<json>`` variables and call stack.
"""
import json
import re

SUPPORTED = (".py", ".sh")
START = b"\0SR"                     # followed by the kind: L lines, P paused, R resumed, V variables
_BODY = re.compile(rb"[0-9,]*")
_LINE = re.compile(rb"[0-9]{1,9}")
_LIMIT = {b"L": 4096, b"P": 64, b"R": 8, b"V": 400_000}   # longest body worth waiting for
V_MAX = 256 * 1024                  # the debuggee keeps a variables snapshot below this

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
        mon.use_tool_id(tool, "scripthub")
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


# Step debugger for Python: `python -c PY_DEBUG_LAUNCHER <script path | -> args...`.
# Control lines arrive on stdin (fd 0 is swapped for the null device first, so
# the script and its children never read them). With "-", stdin starts with
# "<N>\n" and the N bytes of the script.
PY_DEBUG_LAUNCHER = r'''
import collections, json, os, reprlib, signal, sys, threading, traceback, types
def _main():
    target = sys.argv[1]
    cfd = os.dup(0)
    try:
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.close(nul)
    except OSError:
        pass
    ctl = os.fdopen(cfd, "rb")
    if target == "-":
        fn = "<stdin>"
        n = int(ctl.readline().strip() or b"0")
        src = ctl.read(n)
        sys.argv = ["-"] + sys.argv[2:]
    else:
        fn = os.path.abspath(target)
        with open(fn, "rb") as fh:
            src = fh.read()
        sys.argv = [fn] + sys.argv[2:]
        sys.path[0] = os.path.dirname(fn)
    code = compile(src, fn, "exec")

    wl = threading.RLock()
    buf = []
    def out(b):
        with wl:
            try:
                while b:
                    b = b[os.write(2, b):]
            except OSError:
                pass
    def emit():
        with wl:
            items = buf[:]
            del buf[:len(items)]
            if items:
                if len(items) > 300:
                    items = items[:100] + items[-200:]
                out(b"\0SRL" + ",".join(map(str, items)).encode() + b"\0")

    cmds = collections.deque()
    cond = threading.Condition()
    st = {"mode": "step", "bps": set(), "owner": None}
    def reader():
        try:
            for raw in ctl:
                cmds.append(raw.decode("ascii", "replace").strip())
                with cond:
                    cond.notify_all()
        except (OSError, ValueError):
            pass
        cmds.append("detach")
        with cond:
            cond.notify_all()
    threading.Thread(target=reader, daemon=True).start()
    def parse_bps(c):
        return {int(x) for x in c[2:].replace(",", " ").split() if x.isdigit()}
    def running_cmd(c):
        if c in ("pause", "step"):
            st["mode"] = "step"
        elif c == "continue":
            st["mode"] = "run"
        elif c == "detach":
            st["mode"] = "off"
        elif c.startswith("bp"):
            st["bps"] = parse_bps(c)

    rp = reprlib.Repr()
    rp.maxstring = rp.maxother = 160
    rp.maxlist = rp.maxtuple = rp.maxset = rp.maxfrozenset = rp.maxdeque = rp.maxarray = rp.maxdict = 12
    rp.maxlevel = 3
    rp.maxlong = 60
    def scope(d, top):
        res = []
        for k, v in list(d.items()):
            k = str(k)
            if (k.startswith("__") and k.endswith("__")) or isinstance(v, types.ModuleType):
                continue
            if top and isinstance(v, (types.FunctionType, types.BuiltinFunctionType, type)):
                continue
            try:
                r = rp.repr(v)
            except Exception as e:
                r = "<repr failed: %%s>" %% type(e).__name__
            res.append({"name": k, "type": type(v).__name__, "value": r[:400]})
            if len(res) >= 200:
                break
        return res
    def snapshot(frame):
        try:
            scopes = []
            if frame.f_locals is not frame.f_globals:
                scopes.append({"name": "Locals", "vars": scope(frame.f_locals, False)})
            scopes.append({"name": "Globals", "vars": scope(frame.f_globals, True)})
            stack, f = [], frame
            while f is not None and len(stack) < 50:
                if f.f_code.co_filename == fn:
                    stack.append({"fn": f.f_code.co_name, "line": f.f_lineno})
                f = f.f_back
            snap = {"scopes": scopes, "stack": stack}
            data = json.dumps(snap, ensure_ascii=True).encode()
            for keep in (40, 10, 0):
                if len(data) <= %(vmax)d:
                    break
                for s in scopes:
                    s["vars"] = s["vars"][:keep]
                data = json.dumps(snap, ensure_ascii=True).encode()
            return data
        except Exception:
            return b'{"scopes": [], "stack": []}'

    def at_line(frame, line):
        """Called for every line of the script; False = debugging is over."""
        if st["mode"] == "off":
            return False
        buf.append(line)
        if not cmds and st["owner"] is None and st["mode"] == "run" and line not in st["bps"]:
            return True
        me = threading.get_ident()
        with cond:
            while st["owner"] is not None and st["owner"] != me:
                cond.wait(0.5)
            while cmds:
                running_cmd(cmds.popleft())
            if st["mode"] == "off":
                return False
            if st["mode"] != "step" and line not in st["bps"]:
                return True
            st["owner"] = me
            with wl:
                emit()
                out(b"\0SRP%%d\0" %% line)
                out(b"\0SRV" + snapshot(frame) + b"\0")
            while True:
                while not cmds:
                    cond.wait(0.5)
                c = cmds.popleft()
                if c in ("step", "continue", "detach"):
                    running_cmd(c)
                    break
                if c.startswith("bp"):
                    st["bps"] = parse_bps(c)
            st["owner"] = None
            cond.notify_all()
            out(b"\0SRR\0")
            return st["mode"] != "off"

    mon = getattr(sys, "monitoring", None)
    if mon is not None:
        tool = 3
        mon.use_tool_id(tool, "scripthub")
        def on_line(c, line):
            if c.co_filename != fn:
                return mon.DISABLE
            if not at_line(sys._getframe(1), line):
                mon.set_events(tool, 0)
        mon.register_callback(tool, mon.events.LINE, on_line)
        mon.set_events(tool, mon.events.LINE)
        def off():
            mon.set_events(tool, 0)
    else:
        def tracer(frame, event, arg):
            if st["mode"] == "off":
                sys.settrace(None)
                return None
            if frame.f_code.co_filename != fn:
                return None
            def local(frame, event, arg):
                if event == "line" and not at_line(frame, frame.f_lineno):
                    frame.f_trace = None
                    sys.settrace(None)
                    return None
                return local
            return local
        sys.settrace(tracer)
        threading.settrace(tracer)
        def off():
            sys.settrace(None)
    def stop_now(*_):
        cmds.append("detach")
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, stop_now)
    done = threading.Event()
    def watch():
        while not done.wait(0.2):
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
        st["mode"] = "off"
        off()
        done.set()
        emit()
        traceback.print_exception(type(exc), exc, exc.__traceback__.tb_next)
        sys.exit(1)
    finally:
        done.set()
        st["mode"] = "off"
        off()
        emit()
_main()
''' % {"vmax": V_MAX}


def bash_debug_prologue(offset: int = 0) -> str:
    """Step-debugger prologue for bash, as ONE line (it is prepended to the
    script for SSH runs; ``offset`` is then 1). Control lines are read from
    fd 9; the caller makes fd 9 the control stream and stdin /dev/null."""
    parts = [
        "unset BASH_ENV",
        '__sr_main="$0"',
        "__sr_pid=$BASHPID",
        "__sr_m=step",
        "__sr_bp=' '",
        "set -T",
        # one command: step/pause pause at the next line, continue runs, detach ends, bp sets breakpoints
        "__sr_set() { case \"$1\" in step|pause) __sr_m=step;; continue) __sr_m=run;; detach) __sr_m=off;;"
        " bp*) __sr_bp=\" ${1#bp}\"; __sr_bp=\"${__sr_bp//,/ } \";; esac; }",
        "__sr_dbg() { [ \"$__sr_m\" = off ] && return 0;"
        " [ \"$2\" = \"$__sr_main\" ] && [ \"$BASHPID\" = \"$__sr_pid\" ] || return 0;"
        f" local n=$(( $1 - {int(offset)} )) c;"
        " [ \"$n\" -ge 1 ] || return 0;"
        " printf '\\0SRL%s\\0' \"$n\" >&2;"
        " while read -t 0 -u 9 2>/dev/null; do IFS= read -r -u 9 c || { __sr_m=off; return 0; }; __sr_set \"$c\"; done;"
        " [ \"$__sr_m\" = off ] && return 0;"
        " if [ \"$__sr_m\" = run ]; then case \"$__sr_bp\" in *\" $n \"*) ;; *) return 0;; esac; fi;"
        " printf '\\0SRP%s\\0' \"$n\" >&2;"
        " while :; do IFS= read -r -u 9 c || { __sr_m=off; break; };"
        " case \"$c\" in step|continue|detach) __sr_set \"$c\"; break;; bp*) __sr_set \"$c\";; esac; done;"
        " printf '\\0SRR\\0' >&2; }",
        "trap '__sr_m=off' USR1",
        "trap '__sr_dbg \"$LINENO\" \"${BASH_SOURCE[0]}\"' DEBUG",
    ]
    return "; ".join(parts) + "\n"


def bash_debug_prologue_file() -> str:
    """BASH_ENV file for local debug runs: fd 9 becomes the control stream."""
    return "exec 9<&0 0</dev/null\n" + bash_debug_prologue(0)


# Runs on the SSH host as `bash -c BASH_DEBUG_LOADER sr-debug args...`. stdin is
# "<N>\n" + N bytes (prologue line + script), then the control lines. The script
# goes to a temporary file because the debugger needs stdin for itself.
BASH_DEBUG_LOADER = (
    'IFS= read -r __n || exit 125; case "$__n" in ""|*[!0-9]*) exit 125;; esac; '
    '__f=$(mktemp) || exit 125; head -c "$__n" > "$__f"; '
    'exec 9<&0 0</dev/null; "$BASH" "$__f" "$@"; __rc=$?; rm -f "$__f"; exit $__rc'
)


def kind(extension: str, interpreter_stem: str):
    """'python', 'bash' or None when this script cannot be traced."""
    if extension == ".py" and interpreter_stem.startswith("python"):
        return "python"
    if extension == ".sh" and interpreter_stem == "bash":
        return "bash"
    return None


class MarkerFilter:
    """Strips markers out of a byte stream; chunk boundaries are handled.

    ``on_trace(numbers)`` receives line markers. With ``on_event(kind, value)``
    the debugger markers are recognised too: ("P", line), ("R", None) and
    ("V", dict). Without it they are left in the stream untouched."""

    def __init__(self, on_trace, on_event=None):
        self.on_trace = on_trace
        self.on_event = on_event
        self.kinds = (b"L", b"P", b"R", b"V") if on_event else (b"L",)
        self.carry = b""

    def _dispatch(self, kind, body) -> bool:
        if kind == b"L":
            if not _BODY.fullmatch(body):
                return False
            nums = [int(x) for x in body.split(b",") if x]
            if nums and self.on_trace:
                self.on_trace(nums)
            return True
        if kind == b"P":
            if not _LINE.fullmatch(body):
                return False
            self.on_event("P", int(body))
            return True
        if kind == b"R":
            if body:
                return False
            self.on_event("R", None)
            return True
        try:                                   # V: a JSON snapshot
            value = json.loads(body.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return False
        if isinstance(value, dict):
            self.on_event("V", value)
        return True

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
            head = i + len(START)
            if len(data) <= head:                         # the kind byte has not arrived yet
                self.carry = data[i:]
                break
            kind = data[head:head + 1]
            if kind not in self.kinds:
                out.append(data[i:i + 1])
                data = data[i + 1:]
                continue
            j = data.find(b"\0", head + 1)
            if j < 0:
                if len(data) - i > _LIMIT[kind]:          # far too long to be a marker
                    out.append(data[i:i + 1])
                    data = data[i + 1:]
                    continue
                self.carry = data[i:]
                break
            if self._dispatch(kind, data[head + 1:j]):
                data = data[j + 1:]
            else:
                out.append(data[i:i + 1])
                data = data[i + 1:]
        return b"".join(out)

    def flush(self) -> bytes:
        rest, self.carry = self.carry, b""
        return rest
