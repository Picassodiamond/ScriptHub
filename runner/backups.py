"""Previous versions of scripts, kept out of the script folders.

    runtime/backups/<script id>/20261108-214512-123456__ping_host.bat

Every save (editor, restore, metadata block) stores the version it replaces here; the
newest KEEP per script are kept. "Restore previous" puts the newest one back (and, like
any write, backs up what was there, so a restore can be undone); any listed version can
be restored. An old ``<script>.bak`` next to a script (before version 2.1) is moved in
here on the script's next save, and only removed once the copy is safely written.
"""
import datetime
import os
import re
import tempfile
import time
from pathlib import Path

import config
from . import ids
from .fsutil import _replace_with_retry

KEEP = 10
NAME_RE = re.compile(r"^(\d{8}-\d{6}-\d{6})__([^/\\]+)$")


class BackupError(Exception):
    pass


def folder(sid: str) -> Path:
    if not ids.valid(sid):
        raise BackupError("Invalid script id.")
    return config.BACKUPS_DIR / sid


def _stamp(when: float) -> str:
    return datetime.datetime.fromtimestamp(when).strftime("%Y%m%d-%H%M%S-%f")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(sid: str, script_name: str, data: bytes, when: float = None) -> Path:
    """Keep ``data`` (a version of ``script_name``) as the newest backup; prune old ones."""
    base = folder(sid)
    when = time.time() if when is None else when
    target = base / f"{_stamp(when)}__{script_name}"
    while target.exists():                       # same microsecond: move on a tick
        when += 0.000001
        target = base / f"{_stamp(when)}__{script_name}"
    _write(target, data)
    prune(sid)
    return target


def _files(sid: str):
    try:
        base = folder(sid)
    except BackupError:
        return []
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_file() and NAME_RE.match(p.name)),
                  key=lambda p: p.name, reverse=True)


def versions(sid: str) -> list:
    """Newest first: [{'name', 'when', 'size', 'file'}]."""
    out = []
    for p in _files(sid):
        stamp, original = NAME_RE.match(p.name).groups()
        when = datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f")
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({"name": p.name, "when": when.strftime("%Y-%m-%d %H:%M:%S"), "size": size, "file": original})
    return out


def read(sid: str, name: str = "") -> bytes:
    """A backup's bytes; ``name`` empty = the newest. Raises BackupError."""
    files = _files(sid)
    if not files:
        raise BackupError("There is no previous version to restore.")
    if not name:
        return files[0].read_bytes()
    for p in files:
        if p.name == name:
            return p.read_bytes()
    raise BackupError("That version no longer exists.")


def prune(sid: str) -> None:
    for p in _files(sid)[KEEP:]:
        try:
            p.unlink()
        except OSError:
            pass


def migrate_side_bak(sid: str, script_path) -> bool:
    """Move an old ``<script>.bak`` next to the script into the backups. The .bak is only
    removed when its copy was written and reads back identical. Returns True when moved."""
    bak = Path(script_path).with_name(Path(script_path).name + ".bak")
    if not bak.is_file():
        return False
    try:
        data = bak.read_bytes()
        copy = add(sid, Path(script_path).name, data, when=bak.stat().st_mtime)
        if copy.read_bytes() != data:
            return False
        bak.unlink()
        return True
    except (OSError, BackupError):
        return False
