"""Running a sequence: its steps one after another, each as a normal run.

Every step is submitted to the execution manager like a run started from the Run form
(it appears in History with ``"sequence": {"run": "SEQ-…", "step": n}``); the next step
starts when it has finished. A failed step stops the sequence unless the step says
``on_failure: continue``. A sequence run is recorded in

    runtime/sequence_runs/SEQ-YYYYMMDD-NNN/metadata.json

with the overall status and each step's status and run id. Like runs, sequence runs
are not resumed after a restart: they are marked Interrupted on startup.
"""
import datetime
import os
import re
import threading
import time

import config
from . import history, presets, sequences, ssh_hosts
from . import parameter_service as ps
from .executor import ExecutionError
from .fsutil import atomic_write_json, read_json
from .process_manager import manager, pid_alive

SEQ_RUN_RE = re.compile(r"^SEQ-\d{8}-\d{3,}$")
PENDING, SKIPPED = "pending", "skipped"
STEP_LABELS = {**history.STATUS_LABELS, PENDING: "Pending", SKIPPED: "Skipped"}
TOKEN_TTL = 600

_alloc_lock = threading.Lock()
_meta_lock = threading.RLock()


# --------------------------------------------------------------- records ---
def valid_run_id(rid) -> bool:
    return isinstance(rid, str) and bool(SEQ_RUN_RE.match(rid))


def run_dir(rid: str):
    if not valid_run_id(rid):
        raise ValueError("Invalid sequence run id")
    return config.SEQUENCE_RUNS_DIR / rid


def _allocate() -> str:
    config.SEQUENCE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    prefix = "SEQ-" + datetime.date.today().strftime("%Y%m%d") + "-"
    with _alloc_lock:
        highest = 0
        for p in config.SEQUENCE_RUNS_DIR.glob(prefix + "*"):
            try:
                highest = max(highest, int(p.name[len(prefix):]))
            except ValueError:
                pass
        n = highest + 1
        while True:
            rid = f"{prefix}{n:03d}"
            try:
                (config.SEQUENCE_RUNS_DIR / rid).mkdir()
                return rid
            except FileExistsError:
                n += 1


def read_run(rid: str):
    if not valid_run_id(rid):
        return None
    for attempt in range(8):
        try:
            data = read_json(run_dir(rid) / "metadata.json")
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            if not run_dir(rid).is_dir() or attempt == 7:
                return None
            time.sleep(0.02)
    return None


def _write_run(rid: str, data: dict) -> None:
    with _meta_lock:
        atomic_write_json(run_dir(rid) / "metadata.json", data)


def _update_run(rid: str, steps: dict | None = None, **fields) -> dict:
    """Update top-level fields and/or steps ({index: {field: value}})."""
    with _meta_lock:
        data = read_run(rid) or {}
        data.update(fields)
        for i, change in (steps or {}).items():
            data["steps"][i].update(change)
        _write_run(rid, data)
        return data


def list_runs(slug: str | None = None, limit: int | None = None) -> list:
    """Sequence runs, newest first (optionally of one sequence)."""
    try:
        names = sorted((p.name for p in config.SEQUENCE_RUNS_DIR.iterdir() if valid_run_id(p.name)), reverse=True)
    except OSError:
        return []
    out = []
    for rid in names:
        data = read_run(rid)
        if data is None or (slug and data.get("sequence") != slug):
            continue
        out.append(data)
        if limit and len(out) >= limit:
            break
    return out


def live_view(data: dict) -> dict:
    """A sequence run for display: steps that are queued/running show their run's live status."""
    data = dict(data)
    steps = []
    for s in data.get("steps", []):
        s = dict(s)
        if s.get("run_id") and s.get("status") not in history.FINAL_STATUSES + (SKIPPED,):
            meta = history.read_meta(s["run_id"]) if history.valid_run_id(s["run_id"]) else None
            if meta:
                s["status"] = meta.get("status", s["status"])
        s["status_label"] = STEP_LABELS.get(s["status"], s["status"])
        steps.append(s)
    data["steps"] = steps
    data["status_label"] = STEP_LABELS.get(data.get("status"), data.get("status"))
    data["active"] = data.get("status") in history.ACTIVE_STATUSES
    data["done"] = sum(1 for s in steps if s["status"] in history.FINAL_STATUSES + (SKIPPED,))
    data["started_display"] = (data.get("started_at") or "").replace("T", " ")[:16]
    started = history.parse_iso(data.get("started_at"))
    if started is not None:
        end = history.parse_iso(data.get("finished_at")) or datetime.datetime.now()
        data["duration"] = history.format_duration(max(0.0, (end - started).total_seconds()))
    else:
        data["duration"] = "—"
    return data


# --------------------------------------------------------------- prepare ---
def step_host(step, script) -> str:
    return step.get("host") or script.meta.get("host", ssh_hosts.LOCAL)


def password_params(script) -> list:
    if not script.meta_valid:
        return []
    return [p for p in script.meta.get("parameters", [])
            if p["type"] == "password" and p.get("editable_at_runtime", True)]


def step_raw(step, script):
    """(raw values, warnings, error) for a step: from its preset or its stored values."""
    params = script.meta.get("parameters", []) if script.meta_valid else []
    if step.get("preset"):
        stored = presets.get_preset(script, step["preset"])
        if stored is None:
            return ps.defaults_raw(params), [], f"the preset '{step['preset']}' was not found"
    else:
        stored = {"values": step.get("values") or {}}
    raw, warnings = presets.apply_preset(params, stored)
    if not step.get("preset"):          # a parameter not stored with the step just takes its default
        warnings = [w for w in warnings if "is new since" not in w]
    return raw, warnings, None


def prepare(data: dict, passwords: dict | None = None):
    """Resolve and validate every step before anything runs.

    ``passwords``: {(step index, parameter name): value}. Returns (items, errors);
    items = [(step, script, spec)] when there are no errors."""
    from .execution_views import _check_host, build_spec       # avoid an import cycle
    passwords = passwords or {}
    items, errors = [], []
    if not data["steps"]:
        errors.append("The sequence has no steps.")
    for i, step in enumerate(data["steps"]):
        label = f"Step {i + 1} ({step.get('name') or step['script']})"
        script = sequences.resolve(step)
        if script is None:
            errors.append(f"{label}: the script was not found (open its folder, or remove the step).")
            continue
        if not script.meta_valid:
            errors.append(f"{label}: the script's metadata is invalid ({script.meta_error}).")
            continue
        params = script.meta.get("parameters", [])
        raw, _warnings, err = step_raw(step, script)
        if err:
            errors.append(f"{label}: {err}.")
            continue
        for p in password_params(script):
            raw[p["name"]] = passwords.get((i, p["name"]), "")
        values, field_errors = ps.clean_values(params, raw)
        if field_errors:
            labels = {p["name"]: p["label"] for p in params}
            errors.extend(f"{label}: {labels.get(k, k)}: {v}" for k, v in field_errors.items())
            continue
        host = step_host(step, script)
        host_error = _check_host(script, host, ssh_hosts.allowed_hosts([script.meta.get("host", ssh_hosts.LOCAL)]))
        if host_error:
            errors.append(f"{label}: host '{host}': {host_error}")
            continue
        try:
            spec = build_spec(script, host, params, values)
        except ExecutionError as exc:
            errors.append(f"{label}: {exc}")
            continue
        items.append((step, script, spec))
    return (items if not errors else []), errors


# ---------------------------------------------------------------- runner ---
class _State:
    def __init__(self):
        self.cancel = threading.Event()
        self.current = None          # run id of the step running now
        self.thread = None


class SequenceRunner:
    def __init__(self):
        self._lock = threading.RLock()
        self._runs = {}              # sequence run id -> _State
        self._tokens = {}            # submit token -> (sequence run id, time)

    def start(self, data: dict, items: list, initiator: str = "local", token: str | None = None) -> str:
        """Start a prepared sequence (see prepare()); returns the sequence run id."""
        with self._lock:
            now = time.monotonic()
            self._tokens = {t: v for t, v in self._tokens.items() if now - v[1] < TOKEN_TTL}
            if token and token in self._tokens:
                return self._tokens[token][0]      # a double submit
            rid = _allocate()
            _write_run(rid, {
                "id": rid,
                "sequence": data["slug"],
                "name": data["name"],
                "status": history.RUNNING,
                "queued_at": history.now_iso(),
                "started_at": history.now_iso(),
                "finished_at": None,
                "initiator": initiator,
                "owner_pid": os.getpid(),
                "error": None,
                "steps": [{
                    "index": n + 1,
                    "script_id": script.id,
                    "script_name": script.name,
                    "script_path": str(script.path),
                    "host": spec.host,
                    "on_failure": step["on_failure"],
                    "status": PENDING,
                    "run_id": None,
                    "error": None,
                } for n, (step, script, spec) in enumerate(items)],
            })
            state = _State()
            self._runs[rid] = state
            if token:
                self._tokens[token] = (rid, now)
            state.thread = threading.Thread(target=self._run, args=(rid, data["name"], items, initiator),
                                            name=f"seq-{rid}", daemon=True)
            state.thread.start()
            return rid

    def cancel(self, rid: str) -> bool:
        """Stop a running sequence: the current step is stopped, the rest are skipped."""
        with self._lock:
            state = self._runs.get(rid)
            if state is None:
                return False
            state.cancel.set()
            current = state.current
        if current:
            manager.cancel(current)
        return True

    def is_active(self, rid: str) -> bool:
        with self._lock:
            return rid in self._runs

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until no sequence is running (tests)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._runs:
                    return True
            time.sleep(0.05)
        return False

    def _run(self, rid, name, items, initiator) -> None:
        state = self._runs[rid]
        failed, stopped_at = [], None
        try:
            for i, (step, script, spec) in enumerate(items):
                with self._lock:
                    if state.cancel.is_set():
                        break
                    spec.sequence = {"run": rid, "step": i + 1, "name": name}
                    run_id = manager.submit(spec, initiator=initiator)
                    state.current = run_id
                _update_run(rid, steps={i: {"status": history.QUEUED, "run_id": run_id}})
                manager.wait_finished(run_id)
                meta = history.read_meta(run_id) or {}
                status = meta.get("status") or history.FAILED
                _update_run(rid, steps={i: {"status": status, "error": meta.get("error")}})
                with self._lock:
                    state.current = None
                if status != history.SUCCESS:
                    failed.append(i)
                    if state.cancel.is_set() or step["on_failure"] != "continue":
                        stopped_at = i
                        break
        except Exception as exc:  # never leave a sequence stuck in 'running'
            _update_run(rid, error=f"Internal error: {exc!r}")
            state.cancel.set()
        finally:
            data = read_run(rid) or {"steps": []}
            skipped = {i: {"status": SKIPPED} for i, s in enumerate(data["steps"]) if s["status"] == PENDING}
            if state.cancel.is_set():
                status, error = history.CANCELLED, data.get("error") or "Stopped by user."
            elif stopped_at is not None:
                s = data["steps"][stopped_at]
                status = history.FAILED
                error = f"Step {stopped_at + 1} ({s['script_name']}) did not succeed"
                error += "; the remaining steps were skipped." if skipped else "."
            elif failed:
                status = history.FAILED
                error = f"{len(failed)} of {len(data['steps'])} steps did not succeed."
            else:
                status, error = history.SUCCESS, None
            _update_run(rid, steps=skipped, status=status, error=error, finished_at=history.now_iso())
            with self._lock:
                self._runs.pop(rid, None)

    def recover_interrupted(self) -> list:
        """Sequence runs left running by a stopped application are marked interrupted."""
        fixed = []
        me = os.getpid()
        for data in list_runs():
            rid = data.get("id")
            if data.get("status") not in history.ACTIVE_STATUSES or not valid_run_id(rid):
                continue
            if self.is_active(rid):
                continue
            owner = data.get("owner_pid")
            if owner == me or pid_alive(owner):
                continue
            steps = {i: {"status": SKIPPED} for i, s in enumerate(data.get("steps", [])) if s.get("status") == PENDING}
            _update_run(rid, steps=steps, status=history.INTERRUPTED, finished_at=history.now_iso(),
                        error="The application stopped before this sequence finished.")
            fixed.append(rid)
        return fixed


sequencer = SequenceRunner()
