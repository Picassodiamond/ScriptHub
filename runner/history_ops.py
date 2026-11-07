"""History queries and housekeeping on top of runner.history.

* query_runs   - search + filters + paging
* delete_run / delete_older_than - manual clean-up (never touches active runs)
* apply_retention - history_retention_days removes whole runs,
                    log_retention_days removes only the log files
"""
import datetime
import json
import shutil
import threading
import time

import config
from . import history

_retention_lock = threading.Lock()
_last_retention = {"at": 0.0}
RETENTION_INTERVAL = 3600  # seconds between automatic sweeps


def _reference_time(meta):
    for key in ("finished_at", "started_at", "queued_at"):
        t = history.parse_iso(meta.get(key))
        if t is not None:
            return t
    return None


def _haystack(meta) -> str:
    parts = [
        meta.get("id", ""), meta.get("script_name", ""), meta.get("host", ""),
        meta.get("status", ""), history.STATUS_LABELS.get(meta.get("status"), ""),
        meta.get("command", "") or "", meta.get("error", "") or "",
        json.dumps(meta.get("parameters") or {}, ensure_ascii=False),
    ]
    return " ".join(str(p) for p in parts).lower()


def parse_date(value):
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def query_runs(q="", script_id="", status="", date_from=None, date_to=None, page=1, per_page=20):
    """Return (runs_on_page, total_matches). Newest first."""
    q = (q or "").strip().lower()
    matches = []
    for run_id in history.list_run_ids():
        meta = history.read_meta(run_id)
        if meta is None:
            continue
        meta.setdefault("id", run_id)
        if script_id and meta.get("script_id") != script_id:
            continue
        if status and meta.get("status") != status:
            continue
        if date_from or date_to:
            when = _reference_time(meta)
            if when is None:
                continue
            if date_from and when.date() < date_from:
                continue
            if date_to and when.date() > date_to:
                continue
        if q and q not in _haystack(meta):
            continue
        matches.append(meta)
    total = len(matches)
    page = max(1, page)
    start = (page - 1) * per_page
    return matches[start:start + per_page], total


def script_choices():
    """[(script_id, script_name)] for runs present in history (names as recorded)."""
    seen = {}
    for run_id in history.list_run_ids():
        meta = history.read_meta(run_id)
        if meta and meta.get("script_id") and meta["script_id"] not in seen:
            seen[meta["script_id"]] = meta.get("script_name") or meta["script_id"]
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def is_active(meta) -> bool:
    return meta.get("status") in history.ACTIVE_STATUSES


def delete_run(run_id: str) -> bool:
    """Delete one finished run. Active runs are refused."""
    if not history.valid_run_id(run_id):
        return False
    meta = history.read_meta(run_id)
    if meta is None or is_active(meta):
        return False
    shutil.rmtree(history.run_dir(run_id), ignore_errors=True)
    return True


def delete_older_than(days: int, now=None) -> int:
    """Delete finished runs older than ``days`` days. Returns how many."""
    if days < 0:
        return 0
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(days=days)
    removed = 0
    for run_id in history.list_run_ids():
        meta = history.read_meta(run_id)
        if meta is None or is_active(meta):
            continue
        when = _reference_time(meta)
        if when is not None and when < cutoff:
            shutil.rmtree(history.run_dir(run_id), ignore_errors=True)
            removed += 1
    return removed


def delete_logs_older_than(days: int, now=None) -> int:
    """Remove stdout/stderr logs of finished runs older than ``days`` days but
    keep the metadata record."""
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(days=days)
    cleaned = 0
    for run_id in history.list_run_ids():
        meta = history.read_meta(run_id)
        if meta is None or is_active(meta) or meta.get("logs_deleted"):
            continue
        when = _reference_time(meta)
        if when is None or when >= cutoff:
            continue
        for stream in ("stdout", "stderr"):
            try:
                history.log_path(run_id, stream).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                continue
        history.update_meta(run_id, logs_deleted=True)
        cleaned += 1
    return cleaned


def apply_retention(cfg=None, now=None) -> dict:
    """Apply the configured retention. Whole runs go first; logs after that."""
    cfg = cfg or config.load_config()
    runs = delete_older_than(cfg["history_retention_days"], now)
    logs = delete_logs_older_than(cfg["log_retention_days"], now)
    return {"runs_removed": runs, "logs_removed": logs}


def maybe_apply_retention() -> None:
    """Rate-limited automatic sweep (startup and when History is opened)."""
    with _retention_lock:
        if time.monotonic() - _last_retention["at"] < RETENTION_INTERVAL and _last_retention["at"]:
            return
        _last_retention["at"] = time.monotonic()
    try:
        apply_retention()
    except Exception:  # housekeeping must never break a page
        pass
