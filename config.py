"""Filesystem-backed application configuration (runtime/config.json).

No Django imports here so it can be reused by non-web code.
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
RUNTIME_DIR = BASE_DIR / "runtime"
CONFIG_FILE = RUNTIME_DIR / "config.json"
EXECUTIONS_DIR = RUNTIME_DIR / "executions"
PRESETS_DIR = RUNTIME_DIR / "presets"
LOCKS_DIR = RUNTIME_DIR / "locks"
METADATA_OVERRIDE_DIR = RUNTIME_DIR / "metadata_overrides"

log = logging.getLogger(__name__)

DEFAULTS = {
    "script_directories": ["./scripts"],
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
    "enable_adhoc": True,            # the Ad-hoc script screen
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
