"""Script ids.

A script is known by a **stable id** (12 hex characters) stored in its metadata block
(``"id": "…"``), written the first time ScriptHub writes the block. It travels with the
file, so a moved or renamed script keeps its saved values (runtime/presets/<id>/),
backups (runtime/backups/<id>/) and run history. Until then a script is known by its
**path id**: sha1 of its normalised path, cut to 12 characters.

When a stable id replaces a path id, the old id is recorded as an alias in
``script_ids.json`` (next to config.json, so tests that patch CONFIG_FILE stay isolated):
runs recorded under the path id still belong to the script, and old links keep working.
"""
import hashlib
import os
import re
import secrets
import shutil
import threading
from pathlib import Path

import config
from .fsutil import atomic_write_json, read_json

ID_RE = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.RLock()
_cache = {"mtime": None, "aliases": {}}


def path_id(path) -> str:
    key = os.path.normcase(str(path))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def new_id() -> str:
    return secrets.token_hex(6)


def valid(value) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


# -------------------------------------------------------------- aliases ---
def alias_file() -> Path:
    return config.CONFIG_FILE.parent / "script_ids.json"


def aliases() -> dict:
    """{old id: stable id}. Cached until the file changes; never raises."""
    f = alias_file()
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                data = read_json(f)
            except (OSError, ValueError):
                data = {}
            _cache["aliases"] = {k: v for k, v in (data.get("aliases") or {}).items()
                                 if valid(k) and valid(v)} if isinstance(data, dict) else {}
            _cache["mtime"] = mtime
        return dict(_cache["aliases"])


def canonical(sid):
    """The current id for ``sid`` (an old path id resolves to its stable id)."""
    if not sid:
        return sid
    seen = set()
    table = aliases()
    while sid in table and sid not in seen:     # a script may have been re-identified more than once
        seen.add(sid)
        sid = table[sid]
    return sid


def add_alias(old: str, new: str) -> None:
    if not (valid(old) and valid(new)) or old == new:
        return
    with _lock:
        table = aliases()
        table[old] = new
        f = alias_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(f, {"_comment": "Old script ids -> stable ids (ScriptHub).", "aliases": table})
        _cache["mtime"] = None


# ------------------------------------------------------------- adoption ---
def _move_dir(src: Path, dst: Path) -> None:
    """Move src's files into dst (renaming src when dst does not exist; never overwrites)."""
    if not src.is_dir():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return
    for item in src.rglob("*"):
        if item.is_file():
            target = dst / item.relative_to(src)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(target))
    shutil.rmtree(src, ignore_errors=True)


def adopt(old: str, new: str) -> None:
    """A script known as ``old`` is now ``new``: its saved values and backups follow, and
    the old id becomes an alias (history, links)."""
    if not (valid(old) and valid(new)) or old == new:
        return
    for base in (config.PRESETS_DIR, config.BACKUPS_DIR):
        try:
            _move_dir(base / old, base / new)
        except OSError:
            pass
    add_alias(old, new)
