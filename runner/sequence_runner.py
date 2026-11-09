"""Running a sequence: steps connected by arrows, each step a normal run.

Every step is submitted to the execution manager like a run started from the Run form
(it appears in History with ``"sequence": {"run": "SEQ-…", "step": n}``). Steps with no
arrow into them start first; a step starts when the arrows leading into it allow it:

  * an arrow ``success`` applies when its step succeeded, ``failure`` when it did not,
    ``always`` when it finished either way; an arrow from a skipped step never applies;
  * ``join: all`` (default): the step waits for all its arrows and runs when every one
    applies, else it is skipped; ``join: any``: it runs as soon as one applies.

Steps that are ready at the same time run side by side (the Settings limits apply). The
sequence fails when a step did not succeed and no ``failure`` arrow leads on from it.
A sequence run is recorded in

    runtime/sequence_runs/SEQ-YYYYMMDD-NNN/metadata.json

with the overall status, the arrows, and each step's position, status and run id. Like
runs, sequence runs are not resumed after a restart: they are marked Interrupted on startup.
"""
import datetime
import os
import re
import shutil
import threading
import time
from pathlib import Path

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


def _as_graph(data: dict) -> None:
    """A run record of the first version (a list of steps with on_failure) as a chain."""
    if "links" in data:
        return
    steps, links = data.get("steps", []), []
    for i, s in enumerate(steps):
        s.setdefault("id", f"s{i + 1}")
        s.setdefault("x", 2 * sequences.GRID)
        s.setdefault("y", 2 * sequences.GRID + i * (sequences.NODE_H + sequences.LAYER_GAP))
        if i:
            prev = steps[i - 1]
            links.append({"from": prev["id"], "to": s["id"],
                          "when": "always" if prev.get("on_failure") == "continue" else "success"})
    data["links"] = links


def reference_time(data: dict):
    """When a sequence run happened (for History's dates, order and retention)."""
    for key in ("finished_at", "started_at", "queued_at"):
        t = history.parse_iso(data.get(key))
        if t is not None:
            return t
    return None


def delete_run(rid: str) -> bool:
    """Delete one finished sequence run's record (its step runs stay in History). Active ones are refused."""
    data = read_run(rid)
    if data is None or data.get("status") in history.ACTIVE_STATUSES:
        return False
    shutil.rmtree(run_dir(rid), ignore_errors=True)
    return True


def delete_older_than(days: int, now=None) -> int:
    """Delete finished sequence-run records older than ``days`` days. Returns how many."""
    if days < 0:
        return 0
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(days=days)
    removed = 0
    for data in list_runs():
        when = reference_time(data)
        if data.get("status") not in history.ACTIVE_STATUSES and when is not None and when < cutoff:
            if delete_run(data.get("id", "")):
                removed += 1
    return removed


def active_runs() -> list:
    """Sequence runs that are running now (for History's Running panel)."""
    return [live_view(d) for d in list_runs() if d.get("status") in history.ACTIVE_STATUSES]


def live_view(data: dict) -> dict:
    """A sequence run for display: steps that are queued/running show their run's live status."""
    data = dict(data)
    data["steps"] = [dict(s) for s in data.get("steps", [])]
    _as_graph(data)
    steps = []
    for s in data["steps"]:
        s.setdefault("ext", Path(s.get("script_path") or "").suffix.lstrip(".").lower())
        if s.get("run_id") and s.get("status") not in history.FINAL_STATUSES + (SKIPPED,):
            meta = history.read_meta(s["run_id"]) if history.valid_run_id(s["run_id"]) else None
            if meta:
                s["status"] = meta.get("status", s["status"])
        s["status_label"] = STEP_LABELS.get(s["status"], s["status"])
        steps.append(s)
    data["steps"] = steps
    data["status_label"] = STEP_LABELS.get(data.get("status"), data.get("status"))
    data["active"] = data.get("status") in history.ACTIVE_STATUSES
    data["paused"] = bool(data["active"] and data.get("paused"))
    if data["paused"]:
        data["status_label"] = "Paused"
    holds = set(data.get("holds") or []) if data["active"] else set()
    for s in steps:
        s["hold"] = s.get("id") in holds
        s["held"] = bool(data["paused"] and s.get("id") == data.get("held_at"))
        if s["held"]:
            s["status_label"] = "Paused here"
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

    ``passwords``: {(step id, parameter name): value}. Returns (items, errors);
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
            raw[p["name"]] = passwords.get((step["id"], p["name"]), "")
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
def link_state(link: dict, statuses: dict):
    """True when the arrow lets its step run, False when it never will, None while
    the step it starts from has not finished."""
    s = statuses.get(link["from"])
    if s == SKIPPED:
        return False
    if s == history.SUCCESS:
        return link["when"] in ("success", "always")
    if s in history.FINAL_STATUSES:
        return link["when"] in ("failure", "always")
    return None


def decide(step: dict, links_in: list, statuses: dict):
    """'run', 'skip' or None (wait) for a pending step, from the arrows leading into it."""
    if not links_in:
        return "run"
    states = [link_state(k, statuses) for k in links_in]
    if step.get("join") == "any":
        if any(s is True for s in states):
            return "run"
        return "skip" if all(s is False for s in states) else None
    if any(s is False for s in states):
        return "skip"
    return "run" if all(s is True for s in states) else None


class _State:
    def __init__(self):
        self.cancel = threading.Event()
        self.pause = threading.Event()   # set: no new step starts (running ones finish)
        self.wake = threading.Event()    # resume / stop while the loop waits
        self.holds = set()               # step ids: pause the run before starting them (box ⏸)
        self.current = set()         # run ids of the steps queued or running now
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
            links = [dict(k) for k in data.get("links", [])]
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
                "links": links,
                "steps": [{
                    "index": n + 1,
                    "id": step["id"],
                    "x": step["x"],
                    "y": step["y"],
                    "join": step.get("join", "all"),
                    "script_id": script.id,
                    "script_name": script.name,
                    "script_path": str(script.path),
                    "ext": script.path.suffix.lstrip(".").lower(),
                    "host": spec.host,
                    "status": PENDING,
                    "run_id": None,
                    "error": None,
                } for n, (step, script, spec) in enumerate(items)],
            })
            state = _State()
            self._runs[rid] = state
            if token:
                self._tokens[token] = (rid, now)
            state.thread = threading.Thread(target=self._run, args=(rid, data["name"], items, links, initiator),
                                            name=f"seq-{rid}", daemon=True)
            state.thread.start()
            return rid

    def cancel(self, rid: str) -> bool:
        """Stop a running sequence: the steps running now are stopped, the rest are skipped."""
        with self._lock:
            state = self._runs.get(rid)
            if state is None:
                return False
            state.cancel.set()
            state.wake.set()
            current = list(state.current)
        for run_id in current:
            manager.cancel(run_id)
        return True

    def pause(self, rid: str) -> bool:
        """Pause between steps: the steps running now finish, no new step starts until resume()."""
        with self._lock:
            state = self._runs.get(rid)
            if state is None or state.cancel.is_set():
                return False
            state.pause.set()
            _update_run(rid, paused=True)
            return True

    def resume(self, rid: str) -> bool:
        with self._lock:
            state = self._runs.get(rid)
            if state is None or state.cancel.is_set():
                return False
            state.pause.clear()
            _update_run(rid, paused=False, held_at=None)
            state.wake.set()
            return True

    def hold(self, rid: str, step_id: str, on: bool = True) -> bool:
        """Pause the run before a step that has not started yet (or no longer)."""
        with self._lock:
            state = self._runs.get(rid)
            data = read_run(rid) if state is not None and not state.cancel.is_set() else None
            step = next((s for s in (data or {}).get("steps", []) if s.get("id") == step_id), None)
            if step is None or step.get("status") != PENDING:
                return False
            (state.holds.add if on else state.holds.discard)(step_id)
            _update_run(rid, holds=sorted(state.holds))
            return True

    def is_paused(self, rid: str) -> bool:
        with self._lock:
            state = self._runs.get(rid)
            return bool(state and state.pause.is_set())

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

    def _submit_ready(self, rid, name, state, items, index, statuses, links_in, running, initiator) -> None:
        """Start every step whose arrows allow it, skip those they never will (repeatedly:
        a skipped step can decide the next one)."""
        changed = True
        while changed:
            changed = False
            for step, _script, spec in items:
                sid = step["id"]
                if statuses[sid] != PENDING:
                    continue
                what = decide(step, links_in[sid], statuses)
                if what == "skip":
                    statuses[sid] = SKIPPED
                    _update_run(rid, steps={index[sid]: {"status": SKIPPED}})
                    changed = True
                    continue
                if what != "run" or state.pause.is_set():
                    continue
                with self._lock:
                    if sid in state.holds:           # ⏸ on this box: pause here, Resume starts it
                        state.holds.discard(sid)
                        state.pause.set()
                        _update_run(rid, paused=True, held_at=sid, holds=sorted(state.holds))
                        continue
                with self._lock:
                    if state.cancel.is_set():
                        return
                    spec.sequence = {"run": rid, "step": index[sid] + 1, "name": name}
                    try:
                        run_id = manager.submit(spec, initiator=initiator)
                    except Exception as exc:  # noqa: BLE001 - recorded on the step
                        statuses[sid] = history.FAILED
                        _update_run(rid, steps={index[sid]: {"status": history.FAILED,
                                                             "error": f"Could not start: {exc}"}})
                        changed = True
                        continue
                    state.current.add(run_id)
                running[run_id] = sid
                statuses[sid] = history.QUEUED
                _update_run(rid, steps={index[sid]: {"status": history.QUEUED, "run_id": run_id}})

    def _run(self, rid, name, items, links, initiator) -> None:
        state = self._runs[rid]
        index = {step["id"]: i for i, (step, _script, _spec) in enumerate(items)}
        statuses = {step["id"]: PENDING for step, _script, _spec in items}
        links_in = {sid: [k for k in links if k["to"] == sid] for sid in statuses}
        running = {}                                   # run id -> step id
        try:
            while True:
                if not state.cancel.is_set():
                    self._submit_ready(rid, name, state, items, index, statuses, links_in, running, initiator)
                if not running:
                    if (state.pause.is_set() and not state.cancel.is_set()
                            and any(v == PENDING for v in statuses.values())):
                        state.wake.wait(0.5)       # paused with nothing running: wait for resume / stop
                        state.wake.clear()
                        continue
                    break
                for run_id in manager.wait_any(list(running), timeout=1.0):
                    sid = running.pop(run_id)
                    meta = history.read_meta(run_id) or {}
                    status = meta.get("status") or history.FAILED
                    if status not in history.FINAL_STATUSES:
                        status = history.FAILED
                    statuses[sid] = status
                    _update_run(rid, steps={index[sid]: {"status": status, "error": meta.get("error")}})
                    with self._lock:
                        state.current.discard(run_id)
        except Exception as exc:  # never leave a sequence stuck in 'running'
            _update_run(rid, error=f"Internal error: {exc!r}")
            state.cancel.set()
            for run_id in list(running):
                manager.cancel(run_id)
        finally:
            self._finish(rid, state, links)

    def _finish(self, rid, state, links) -> None:
        data = read_run(rid) or {"steps": []}
        skipped = {i: {"status": SKIPPED} for i, s in enumerate(data["steps"])
                   if s["status"] not in history.FINAL_STATUSES + (SKIPPED,)}
        handled = {k["from"] for k in links if k["when"] == "failure"}
        failed = [s for s in data["steps"]
                  if s["status"] in history.FINAL_STATUSES and s["status"] != history.SUCCESS
                  and s.get("id") not in handled]
        n_skipped = sum(1 for s in data["steps"] if s["status"] == SKIPPED) + len(skipped)
        note = (f" {n_skipped} step{'s were' if n_skipped != 1 else ' was'} skipped.") if n_skipped else ""
        if state.cancel.is_set():
            status, error = history.CANCELLED, data.get("error") or "Stopped by user."
        elif len(failed) == 1:
            s = failed[0]
            status, error = history.FAILED, f"Step {s['index']} ({s['script_name']}) did not succeed." + note
        elif failed:
            status = history.FAILED
            error = f"{len(failed)} of {len(data['steps'])} steps did not succeed." + note
        else:
            status, error = history.SUCCESS, None
        _update_run(rid, steps=skipped, status=status, error=error, finished_at=history.now_iso(), paused=False,
                    held_at=None, holds=[])
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
