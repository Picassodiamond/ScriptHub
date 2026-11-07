"""Small filesystem helpers: atomic JSON writes and tolerant reads."""
import json
import os
import tempfile
import time
from pathlib import Path


def _replace_with_retry(src, dst, attempts: int = 60, delay: float = 0.025) -> None:
    """os.replace, retrying briefly: on Windows it is refused while another
    thread or process (e.g. a polling request) has the target open."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path):
    """Return parsed JSON; raises ValueError (bad JSON) or OSError.

    A brief PermissionError (file being swapped by an atomic write on
    Windows) is retried."""
    for attempt in range(40):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025)
