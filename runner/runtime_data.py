"""What ScriptHub keeps in its data folder (config.RUNTIME_DIR), how large each kind is, and clearing it
(Settings -> Data, ``manage.py clear_runtime``). Queued and running runs are never removed; the secret key,
locks and script id aliases are internal and not offered."""
import os
import shutil
from pathlib import Path

import config
from . import history, history_ops, sequence_runner


def _paths(key: str) -> list:
    r = config.RUNTIME_DIR
    return {
        "history": [config.EXECUTIONS_DIR, config.SEQUENCE_RUNS_DIR],
        "presets": [config.PRESETS_DIR],
        "sequences": [config.SEQUENCES_DIR],
        "backups": [config.BACKUPS_DIR],
        "metadata": [config.METADATA_OVERRIDE_DIR],
        "workspace": [r / "workspace.json"],
        "imports": [r / "imports"],
        "desktop": [r / "desktop-profile", r / "desktop.log"],
        "settings": [config.CONFIG_FILE, config.CONFIG_FILE.with_name(config.CONFIG_FILE.name + ".bak")],
    }[key]


KINDS = (
    ("history", "Run history and logs", "Finished script and sequence runs. Queued and running ones stay."),
    ("presets", "Saved values", "Presets and recent values of every script."),
    ("sequences", "Sequences", "Every sequence (their runs are under run history)."),
    ("backups", "Script backups", "Previous versions kept when a script is saved."),
    ("metadata", "Metadata of read-only scripts", "Metadata stored here because the script could not be written."),
    ("workspace", "Open folder, recent and favourites", "Closes the folder and opened files, forgets recent and favourites."),
    ("imports", "Uploaded project files", "Project files waiting to be opened (removed after 24 hours anyway)."),
    ("desktop", "Desktop window profile", "The app window's browser profile and the desktop log."),
    ("settings", "Settings", "Back to the defaults."),
)
KEYS = tuple(k for k, _l, _h in KINDS)


def _size(path: Path) -> tuple:
    """(bytes, files) under path."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size, 1
    total = count = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            if f == ".gitkeep":
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
                count += 1
            except OSError:
                pass
    return total, count


def overview() -> list:
    rows = []
    for key, label, help_text in KINDS:
        size = files = 0
        for p in _paths(key):
            try:
                b, n = _size(p)
            except OSError:
                continue
            size, files = size + b, files + n
        rows.append({"key": key, "label": label, "help": help_text, "bytes": size, "files": files})
    return rows


def _empty_dir(path: Path) -> int:
    """Remove what is in ``path`` (the folder stays). Returns how many entries could not be removed."""
    left = 0
    if not Path(path).is_dir():
        return 0
    for child in Path(path).iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError:
            left += 1
    return left


def _unlink(path: Path) -> int:
    try:
        Path(path).unlink(missing_ok=True)
        return 0
    except OSError:
        return 1


def clear(key: str) -> str:
    """Clear one kind of data; returns a sentence for the user. Raises ValueError for an unknown kind or
    when it cannot be cleared now."""
    if key not in KEYS:
        raise ValueError(f"Unknown kind of data: {key}")
    if key == "history":
        removed = kept = 0
        for run_id in history.list_run_ids():
            if history_ops.delete_run(run_id):
                removed += 1
            else:
                kept += 1
        for data in sequence_runner.list_runs():
            if sequence_runner.delete_run(data.get("id", "")):
                removed += 1
            else:
                kept += 1
        return f"Removed {removed} run{'s' if removed != 1 else ''}" + (f"; {kept} still running or unreadable." if kept else ".")
    if key == "sequences" and sequence_runner.active_runs():
        raise ValueError("A sequence is running: stop it or wait, then clear the sequences.")
    if key == "workspace":
        from . import discovery
        left = _unlink(_paths(key)[0])
        discovery.rescan()
    elif key == "settings":
        left = sum(_unlink(p) for p in _paths(key))
    else:
        left = 0
        for p in _paths(key):
            left += _empty_dir(p) if not Path(p).is_file() else _unlink(p)
    label = dict((k, l) for k, l, _h in KINDS)[key]
    if left:
        return f"{label}: cleared, except {left} item{'s' if left != 1 else ''} in use (close what uses them and try again)."
    return f"{label}: cleared."


def location() -> dict:
    how = {"env": "set by SCRIPTHUB_HOME", "portable": "portable: the runtime folder next to the code",
           "user": "your user data folder"}[config.RUNTIME_MODE]
    return {"path": str(config.RUNTIME_DIR), "how": how}


def human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"
