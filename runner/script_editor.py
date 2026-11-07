"""Safe editing of catalogued script files.

* Only a path the catalogue gave us (inside its configured directory) is written.
* Saves are atomic; the previous version is kept as ``<file>.bak``.
* A save is refused when the file changed on disk since it was opened
  (``base_hash``), unless the caller explicitly forces it.
* Line endings are kept sensible: ``.bat`` -> CRLF, ``.sh`` -> LF, others keep
  whatever the file used. (A browser submits textareas with CRLF; a CRLF
  shell script would break on Linux, so we normalise.)
* Only UTF-8 text up to 512 KB is editable.
* After saving, a quick syntax check (never executing the script) produces
  warnings for Python and Bash.
"""
import hashlib
import shutil
import subprocess
from pathlib import Path

from .fsutil import _replace_with_retry
from .security import is_within

import os
import tempfile

MAX_BYTES = 512 * 1024
BOM = b"\xef\xbb\xbf"
CRLF_EXTENSIONS = (".bat", ".cmd")
LF_EXTENSIONS = (".sh", ".bash")


class EditError(Exception):
    """The file cannot be edited or saved (message is shown to the user)."""


class ConflictError(EditError):
    """The file changed on disk after it was opened."""


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def has_backup(path: Path) -> bool:
    return backup_path(path).is_file()


def _check_path(path: Path, root: Path) -> None:
    if not is_within(path, root):
        raise EditError("This file is outside its configured script directory and cannot be edited.")
    if not Path(path).is_file():
        raise EditError("The script file no longer exists.")


def newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lone_lf = data.count(b"\n") - crlf
    return "crlf" if crlf > lone_lf else "lf"


def read_for_edit(path: Path, root: Path) -> dict:
    """Load a script for the editor. Raises EditError if it cannot be edited."""
    _check_path(path, root)
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise EditError(f"Could not read the file: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise EditError("This file is larger than 512 KB; open it in a real editor.")
    if b"\x00" in data:
        raise EditError("This file looks binary and cannot be edited here.")
    bom = data.startswith(BOM)
    try:
        text = (data[len(BOM):] if bom else data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditError("This file is not valid UTF-8, so it cannot be edited safely here.") from exc
    return {
        "text": text.replace("\r\n", "\n").replace("\r", "\n"),
        "hash": file_hash(data),
        "newline": newline_style(data),
        "bom": bom,
        "size": len(data),
        "writable": os.access(path, os.W_OK),
    }


def normalise_text(text: str, ext: str, original_style: str = "lf") -> str:
    """Apply the line-ending policy to editor text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    ext = ext.lower()
    style = "crlf" if ext in CRLF_EXTENSIONS else "lf" if ext in LF_EXTENSIONS else original_style
    return text.replace("\n", "\r\n") if style == "crlf" else text


def syntax_warning(ext: str, text: str):
    """A human-readable syntax problem, or None. Never runs the script."""
    ext = ext.lower()
    if ext == ".py":
        try:
            compile(text, "<script>", "exec")
        except SyntaxError as exc:
            return f"Python syntax error at line {exc.lineno}: {exc.msg}"
        except ValueError as exc:  # e.g. null bytes
            return f"Python could not parse this file: {exc}"
    elif ext in LF_EXTENSIONS and shutil.which("bash"):
        try:
            done = subprocess.run(["bash", "-n"], input=text.encode("utf-8"), capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            first = (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["syntax error"])[0]
            return f"Bash reports a syntax problem: {first.replace('bash: ', '', 1)}"
    return None


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            shutil.copymode(path, tmp)           # keep the executable bit
        except OSError:
            pass
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_with_backup(path: Path, new_bytes: bytes, current: bytes) -> None:
    try:
        backup_path(path).write_bytes(current)
        _atomic_write(path, new_bytes)
    except PermissionError as exc:
        raise EditError("The file or its folder is read-only, so it cannot be saved.") from exc
    except OSError as exc:
        raise EditError(f"Could not save: {exc}") from exc


def save_script(path: Path, root: Path, text: str, base_hash: str, force: bool = False) -> dict:
    """Save editor text. Returns {'changed': bool, 'warning': str|None}."""
    _check_path(path, root)
    if not os.access(path, os.W_OK):       # a rename would succeed even for a 0444 file in a writable folder
        raise EditError("The file is read-only, so it cannot be saved.")
    try:
        current = Path(path).read_bytes()
    except OSError as exc:
        raise EditError(f"Could not read the file: {exc}") from exc
    if not force and file_hash(current) != base_hash:
        raise ConflictError("The file was changed on disk after you opened it. "
                            "Reload to see the new version, or overwrite it with yours.")
    if "\x00" in text:
        raise EditError("The text contains a null character and cannot be saved.")
    ext = Path(path).suffix
    body = normalise_text(text, ext, newline_style(current)).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise EditError("The script would be larger than 512 KB; it was not saved.")
    new_bytes = (BOM if current.startswith(BOM) else b"") + body
    warning = syntax_warning(ext, body.decode("utf-8"))
    if new_bytes == current:
        return {"changed": False, "warning": warning}
    _write_with_backup(Path(path), new_bytes, current)
    return {"changed": True, "warning": warning}


def restore_backup(path: Path, root: Path) -> None:
    """Swap the file with its .bak (so the restore itself can be undone)."""
    _check_path(path, root)
    if not os.access(path, os.W_OK):
        raise EditError("The file is read-only, so it cannot be restored.")
    bak = backup_path(path)
    if not bak.is_file():
        raise EditError("There is no previous version to restore.")
    try:
        previous, current = bak.read_bytes(), Path(path).read_bytes()
    except OSError as exc:
        raise EditError(f"Could not read the files: {exc}") from exc
    _write_with_backup(Path(path), previous, current)
