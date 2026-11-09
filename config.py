"""Filesystem-backed application configuration (runtime/config.json).

No Django imports here so it can be reused by non-web code.

Where the state lives (RUNTIME_DIR, called "runtime/" throughout the code and docs):
``SCRIPTHUB_HOME`` when set; else a ``runtime`` folder next to the code when one exists
(portable mode, and installs from before 2.0 keep their data); else the user's data folder:
%LOCALAPPDATA%\\ScriptHub, ~/Library/Application Support/ScriptHub or ~/.local/share/scripthub.
"""
import copy
import logging
import os
import sys
import threading
from pathlib import Path

from runner.fsutil import atomic_write_json, read_json
from runner.validators import validate_config

BASE_DIR = Path(__file__).resolve().parent
PORTABLE_DIR = BASE_DIR / "runtime"


def runtime_location(environ=None, platform=None, portable=PORTABLE_DIR) -> tuple:
    """(folder, how): how = "env" | "portable" | "user"."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = (environ.get("SCRIPTHUB_HOME") or "").strip()
    if home:
        return Path(home).expanduser().resolve(), "env"
    if Path(portable).is_dir():
        return Path(portable), "portable"
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ScriptHub", "user"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ScriptHub", "user"
    base = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "scripthub", "user"


RUNTIME_DIR, RUNTIME_MODE = runtime_location()
CONFIG_FILE = RUNTIME_DIR / "config.json"
EXECUTIONS_DIR = RUNTIME_DIR / "executions"
PRESETS_DIR = RUNTIME_DIR / "presets"
LOCKS_DIR = RUNTIME_DIR / "locks"
METADATA_OVERRIDE_DIR = RUNTIME_DIR / "metadata_overrides"
BACKUPS_DIR = RUNTIME_DIR / "backups"          # previous versions of scripts, per script id
SEQUENCES_DIR = RUNTIME_DIR / "sequences"      # <slug>.yaml: scripts connected by arrows
SEQUENCE_RUNS_DIR = RUNTIME_DIR / "sequence_runs"   # SEQ-YYYYMMDD-NNN/metadata.json

log = logging.getLogger(__name__)

# The scanned script folder is not a setting: it is opened from the UI and kept in
# runtime/workspace.json (runner/workspace.py). A "script_directories" list in an
# older config.json is still read once, to seed that state.
DEFAULTS = {
    # file extension -> interpreter command ("" never allowed). Only these
    # extensions are ever treated as scripts.
    "interpreters": {
        ".py": "python" if os.name == "nt" else "python3",
        ".sh": "bash",
    },
    # Interpreters used on remote Linux hosts (scripts are streamed over SSH).
    "remote_interpreters": {".py": "python3", ".sh": "bash"},
    "ssh_executable": "ssh",
    "ssh_connect_timeout": 10,
    "environment": {},  # extra environment variables for every run
    "enable_script_editing": True,   # editing script files from the browser
    "max_concurrent_executions": 3,
    "max_concurrent_per_host": 3,
    "default_timeout": 3600,
    "history_retention_days": 90,
    "log_retention_days": 30,
    "polling_interval": 2000,
}

if os.name == "nt":
    DEFAULTS["interpreters"][".bat"] = "cmd"

_lock = threading.RLock()


def ensure_runtime_dirs() -> None:
    for d in (RUNTIME_DIR, EXECUTIONS_DIR, PRESETS_DIR, LOCKS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Defaults merged with runtime/config.json. Never raises."""
    cfg = copy.deepcopy(DEFAULTS)
    with _lock:
        if CONFIG_FILE.exists():
            try:
                stored = read_json(CONFIG_FILE)
                if isinstance(stored, dict):
                    cfg.update(stored)
                else:
                    log.warning("config.json is not an object; using defaults")
            except (OSError, ValueError) as exc:
                log.warning("Could not read config.json (%s); using defaults", exc)
    try:
        return validate_config(cfg)
    except Exception as exc:  # corrupt values: fall back rather than crash
        log.warning("Invalid stored config (%s); using defaults", exc)
        return copy.deepcopy(DEFAULTS)


def save_config(cfg: dict) -> dict:
    """Validate then atomically save; keeps config.json.bak of the previous file.

    Raises runner.validators.ValidationError on bad input.
    """
    clean = validate_config(cfg)
    with _lock:
        ensure_runtime_dirs()
        if CONFIG_FILE.exists():
            try:
                (CONFIG_FILE.with_suffix(".json.bak")).write_bytes(CONFIG_FILE.read_bytes())
            except OSError:
                pass
        atomic_write_json(CONFIG_FILE, clean)
    return clean


def resolve_dir(value: str) -> Path:
    """Resolve a configured directory (relative paths are relative to the app)."""
    p = Path(os.path.expandvars(os.path.expanduser(value)))
    if not p.is_absolute():
        p = BASE_DIR / p
    return p.resolve()


def python_executable() -> str:
    return sys.executable
