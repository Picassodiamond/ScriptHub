"""Filesystem execution records.

runtime/executions/RUN-YYYYMMDD-NNN/
    metadata.json   status, timings, parameters (passwords redacted), ...
    stdout.log
    stderr.log
"""
import datetime
import re
import threading
import time
from pathlib import Path

import config
from .fsutil import atomic_write_json, read_json

RUN_ID_RE = re.compile(r"^RUN-\d{8}-\d{3,}$")

QUEUED, RUNNING = "queued", "running"
SUCCESS, FAILED, TIMED_OUT, CANCELLED, INTERRUPTED = "success", "failed", "timed_out", "cancelled", "interrupted"
ACTIVE_STATUSES = (QUEUED, RUNNING)
FINAL_STATUSES = (SUCCESS, FAILED, TIMED_OUT, CANCELLED, INTERRUPTED)
ALL_STATUSES = ACTIVE_STATUSES + FINAL_STATUSES
STATUS_LABELS = {
    QUEUED: "Queued", RUNNING: "Running", SUCCESS: "Success", FAILED: "Failed",
    TIMED_OUT: "Timed out", CANCELLED: "Cancelled", INTERRUPTED: "Interrupted",
}

_alloc_lock = threading.Lock()
_meta_lock = threading.Lock()
MAX_CHUNK = 256 * 1024


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def valid_run_id(run_id: str) -> bool:
    return bool(isinstance(run_id, str) and RUN_ID_RE.match(run_id))


def run_dir(run_id: str) -> Path:
    if not valid_run_id(run_id):
        raise ValueError("Invalid run id")
    return config.EXECUTIONS_DIR / run_id


def meta_path(run_id: str) -> Path:
    return run_dir(run_id) / "metadata.json"


def log_path(run_id: str, stream: str) -> Path:
    if stream not in ("stdout", "stderr"):
        raise ValueError("Unknown stream")
    return run_dir(run_id) / f"{stream}.log"


def allocate_run_dir():
    """Create the next RUN-YYYYMMDD-NNN folder (safe against concurrent callers)."""
    config.ensure_runtime_dirs()
    prefix = "RUN-" + datetime.date.today().strftime("%Y%m%d") + "-"
    with _alloc_lock:
        highest = 0
        for p in config.EXECUTIONS_DIR.glob(prefix + "*"):
            try:
                highest = max(highest, int(p.name[len(prefix):]))
            except ValueError:
                pass
        n = highest + 1
        while True:
            run_id = f"{prefix}{n:03d}"
            try:
                (config.EXECUTIONS_DIR / run_id).mkdir()
                return run_id, config.EXECUTIONS_DIR / run_id
            except FileExistsError:
                n += 1


def read_meta(run_id: str):
    """Metadata for a run, or None. A brief failure while another thread is
    swapping the file in (atomic write) is retried when the run exists."""
    exists = run_dir(run_id).is_dir()
    for attempt in range(8):
        try:
            data = read_json(meta_path(run_id))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            if not exists or attempt == 7:
                return None
            time.sleep(0.02)
    return None


def write_meta(run_id: str, data: dict) -> None:
    with _meta_lock:
        atomic_write_json(meta_path(run_id), data)


def update_meta(run_id: str, **fields) -> dict:
    with _meta_lock:
        try:
            data = read_json(meta_path(run_id))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data.update(fields)
        atomic_write_json(meta_path(run_id), data)
        return data


def list_run_ids(newest_first: bool = True):
    try:
        ids = [p.name for p in config.EXECUTIONS_DIR.iterdir() if p.is_dir() and valid_run_id(p.name)]
    except OSError:
        return []
    return sorted(ids, reverse=newest_first)


def list_runs(limit=None, statuses=None, script_id=None):
    out = []
    for run_id in list_run_ids():
        meta = read_meta(run_id)
        if meta is None:
            continue
        meta.setdefault("id", run_id)
        if statuses and meta.get("status") not in statuses:
            continue
        if script_id and meta.get("script_id") != script_id:
            continue
        out.append(meta)
        if limit and len(out) >= limit:
            break
    return out


def last_run_by_script(scan_limit=400):
    """{script_id: newest metadata} from the most recent runs."""
    latest = {}
    for meta in list_runs(limit=scan_limit):
        latest.setdefault(meta.get("script_id"), meta)
    return latest


def duration_seconds(meta: dict):
    start = parse_iso(meta.get("started_at"))
    if start is None:
        return None
    end = parse_iso(meta.get("finished_at"))
    if end is None:
        if meta.get("status") != RUNNING:
            return None
        end = datetime.datetime.now()
    return max(0.0, (end - start).total_seconds())


def format_duration(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def read_chunk(path: Path, offset: int = 0, limit: int = MAX_CHUNK):
    """Return (text, new_offset), never splitting a UTF-8 character."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, offset))
            blob = fh.read(limit)
    except OSError:
        return "", offset
    if not blob:
        return "", offset
    end = len(blob)
    # Don't cut a multi-byte sequence in half at the end of the chunk.
    for back in range(1, 4):
        if end - back < 0:
            break
        b = blob[end - back]
        if b & 0xC0 == 0x80:      # continuation byte, keep looking
            continue
        need = 4 if b >= 0xF0 else 3 if b >= 0xE0 else 2 if b >= 0xC0 else 1
        if need > back:           # sequence incomplete: leave it for next poll
            end -= back
        break
    return blob[:end].decode("utf-8", errors="replace"), offset + end
