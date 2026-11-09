"""Script metadata: a YAML block in a comment at the top of the script itself.

    #!/usr/bin/env python3
    # --- scripthub ---
    # name: Directory Backup
    # category: Backup
    # parameters:
    #   - name: source
    #     type: directory
    #     required: true
    #     arg: --source
    #   - name: mode
    #     type: select
    #     choices: [full, incremental]
    # --- end scripthub ---

The YAML is the subset of runner/yamlish.py (no third-party parser). Blocks written as
JSON (before version 2.2: the body starts with ``{``) are still read and become YAML on
the next save. Batch files use ``::`` instead of ``#``. The block goes after a ``#!`` line, a Python
encoding line or ``@echo off``. Saving rewrites only the block (atomically, keeping the
line endings and a BOM; the previous version goes to runtime/backups/<id>/).

The first save writes a stable ``"id"`` (runner/ids.py) so the script keeps its saved
values, backups and history when it is moved or renamed; a copy found with the same id
as another script gets a new one on its next save (``conflict``).

Older scripts may still have a sidecar ``<stem>.json``; it is read until the next
save, which moves it into the script. When the script cannot be written, the
metadata (with its id) goes to runtime/metadata_overrides/<path id>.json instead.
"""
import json
import os
import re
import tempfile
from pathlib import Path

import config
from . import backups, ids, yamlish
from .fsutil import _replace_with_retry, atomic_write_json, read_json

START, END = "--- scripthub ---", "--- end scripthub ---"
BATCH = (".bat", ".cmd")
MAX_SCAN = 256 * 1024                     # the block is near the top; never read more than this
_MARK = re.compile(r"^\s*(?:#|::|rem\b)\s*(" + re.escape(START) + "|" + re.escape(END) + r")\s*$", re.I)
_PREFIX = re.compile(r"^\s*(?:#|::|rem\b) ?", re.I)
_KEEP_FIRST = re.compile(r"^(#!|#.*coding[:=]|@echo\s+off\b)", re.I)
_YAML_LINE = re.compile(r"\bline (\d+)\b")


def prefix_for(script_path: Path) -> str:
    return "::" if Path(script_path).suffix.lower() in BATCH else "#"


def sidecar_path(script_path: Path) -> Path:
    """Old location (before version 2.1): <stem>.json next to the script."""
    return Path(script_path).with_suffix(".json")


def override_path(script_id: str) -> Path:
    return config.METADATA_OVERRIDE_DIR / f"{script_id}.json"


# ---------------------------------------------------------------- block ---
def find_block(lines):
    """(start, end) line indexes of the block's marker lines, or None."""
    start = None
    for i, line in enumerate(lines):
        m = _MARK.match(line)
        if not m:
            continue
        if m.group(1).lower() == START and start is None:
            start = i
        elif m.group(1).lower() == END and start is not None:
            return start, i
    return None


def parse_block(text: str):
    """(data, error): data is None when the script has no block."""
    lines = text.splitlines()
    span = find_block(lines)
    if span is None:
        return None, None
    body = "\n".join(_PREFIX.sub("", line, count=1) for line in lines[span[0] + 1:span[1]])
    where = f"lines {span[0] + 1}-{span[1] + 1}"
    if body.lstrip().startswith("{"):                 # written as JSON (before 2.2)
        try:
            data = json.loads(body)
        except ValueError as exc:
            return {}, f"Invalid metadata block ({where}, JSON): {exc}"
        if not isinstance(data, dict):
            return {}, "The metadata block must contain a JSON object {…}."
        return data, None
    try:
        data = yamlish.load(body)
    except yamlish.YamlError as exc:                 # "line n" of the block -> line of the script
        msg = _YAML_LINE.sub(lambda m: f"line {int(m.group(1)) + span[0] + 1}", str(exc), count=1)
        return {}, f"Invalid metadata block ({where}): {msg}"
    return data, None


def format_block(data: dict, prefix: str = "#") -> list:
    """The block's lines: YAML, one key per line, short lists such as ``choices`` on one line."""
    body = yamlish.dump(data, flow=True).splitlines() if data else []
    return [f"{prefix} {START}"] + [f"{prefix} {line}".rstrip() for line in body] + [f"{prefix} {END}"]


def _read_script(script_path: Path):
    """(text, bom, newline) of the start of the script; raises OSError."""
    with open(script_path, "rb") as fh:
        blob = fh.read(MAX_SCAN + 1)
    bom = blob.startswith(b"\xef\xbb\xbf")
    text = blob[3 if bom else 0:MAX_SCAN].decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in text else ("\r\n" if Path(script_path).suffix.lower() in BATCH and "\n" not in text else "\n")
    return text, bom, newline


# ----------------------------------------------------------------- load ---
def _override_for(script_path) -> Path:
    """The override file is found by the script's path (it cannot carry an id in the file)."""
    return override_path(ids.path_id(Path(script_path).resolve()))


def load_raw(script_path: Path, script_id: str = ""):
    """Return (data_dict, source_path_or_None, error_or_None). Never raises.
    ``source`` is the script itself when it has a block."""
    try:
        text, _bom, _nl = _read_script(script_path)
    except OSError:
        text = ""
    data, err = parse_block(text)
    if data is not None:
        return data, Path(script_path), err
    for candidate in (sidecar_path(script_path), _override_for(script_path)):
        if candidate.is_file():
            try:
                data = read_json(candidate)
            except (OSError, ValueError) as exc:
                return {}, candidate, f"Invalid metadata file {candidate.name}: {exc}"
            if not isinstance(data, dict):
                return {}, candidate, f"{candidate.name} must contain a JSON object."
            return data, candidate, None
    return {}, None, None


def embedded(script) -> bool:
    """True when the script's metadata lives in the script file."""
    return script.meta_source is not None and Path(script.meta_source) == Path(script.path)


# ----------------------------------------------------------------- save ---
def with_block(content: str, data: dict, script_path: Path, newline: str) -> str:
    """``content`` (the whole script) with its metadata block replaced or inserted."""
    lines = content.splitlines()
    block = format_block(data, prefix_for(script_path))
    span = find_block(lines)
    if span is not None:
        lines[span[0]:span[1] + 1] = block
    else:
        at = 0
        while at < len(lines) and at < 2 and _KEEP_FIRST.match(lines[at]):
            at += 1
        lines[at:at] = block
    trailing = content.endswith(("\n", "\r"))
    return newline.join(lines) + (newline if trailing or not content else "")


def _write_script(script_path: Path, data: dict, sid: str) -> None:
    script_path = Path(script_path)
    raw = script_path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    content = raw[3 if bom else 0:].decode("utf-8")       # UnicodeDecodeError -> not ours to rewrite
    newline = "\r\n" if "\r\n" in content else ("\r\n" if script_path.suffix.lower() in BATCH and "\n" not in content else "\n")
    new = with_block(content, data, script_path, newline)
    payload = (b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8")
    if payload == raw:
        return
    backups.migrate_side_bak(sid, script_path)          # an old <script>.bak moves to runtime/backups/
    backups.add(sid, script_path.name, raw)             # "Restore previous" in the editor
    fd, tmp = tempfile.mkstemp(dir=str(script_path.parent), prefix=script_path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, script_path.stat().st_mode)
        except OSError:
            pass
        _replace_with_retry(tmp, script_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def with_id(data: dict, conflict: bool = False) -> dict:
    """``data`` with a stable id first: kept when valid, new when missing or a duplicate."""
    current = data.get("id")
    sid = ids.new_id() if conflict or not ids.valid(current) else current
    return {"id": sid, **{k: v for k, v in data.items() if k != "id"}}


def save_raw(script_path: Path, script_id: str, data: dict, conflict: bool = False) -> Path:
    """Write the metadata into the script (moving an old sidecar in); falls back to
    runtime/metadata_overrides/ when the script cannot be written. Returns where it went.

    ``script_id`` is what the script is known as now; when the stable id written here
    differs (first save, or a duplicate given a new id), the script's saved values and
    backups move to it and the old id becomes an alias (runner/ids.py)."""
    script_path = Path(script_path)
    pid = ids.path_id(script_path.resolve())
    data = with_id(data, conflict)
    sid = data["id"]
    previous = script_id if ids.valid(script_id) else pid
    if previous != sid:
        ids.adopt(previous, sid)
    try:
        _write_script(script_path, data, sid)
    except (OSError, UnicodeDecodeError, backups.BackupError):
        target = override_path(pid)
        atomic_write_json(target, data)
        return target
    for old in (sidecar_path(script_path), override_path(pid)):   # now inside the script
        try:
            if isinstance(read_json(old), dict):     # only a metadata object, never an unrelated file
                old.unlink()
        except (OSError, ValueError):
            pass
    return Path(script_path)
