"""Script metadata: a JSON block in a comment at the top of the script itself.

    #!/usr/bin/env python3
    # --- scripthub ---
    # {
    #   "name": "Directory Backup",
    #   "category": "Backup",
    #   "parameters": [
    #     {"name": "source", "type": "directory", "required": true, "arg": "--source"}
    #   ]
    # }
    # --- end scripthub ---

Batch files use ``::`` instead of ``#``. The block goes after a ``#!`` line, a Python
encoding line or ``@echo off``. Saving rewrites only the block (atomically, keeping
the previous file as ``<name>.bak``, the line endings and a BOM).

Older scripts may still have a sidecar ``<stem>.json``; it is read until the next
save, which moves it into the script. When the script cannot be written, the
metadata goes to runtime/metadata_overrides/<script_id>.json instead.
"""
import json
import os
import re
import tempfile
from pathlib import Path

import config
from .fsutil import _replace_with_retry, atomic_write_json, read_json

START, END = "--- scripthub ---", "--- end scripthub ---"
BATCH = (".bat", ".cmd")
MAX_SCAN = 256 * 1024                     # the block is near the top; never read more than this
_MARK = re.compile(r"^\s*(?:#|::|rem\b)\s*(" + re.escape(START) + "|" + re.escape(END) + r")\s*$", re.I)
_PREFIX = re.compile(r"^\s*(?:#|::|rem\b) ?", re.I)
_KEEP_FIRST = re.compile(r"^(#!|#.*coding[:=]|@echo\s+off\b)", re.I)


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
    try:
        data = json.loads(body or "{}")
    except ValueError as exc:
        return {}, f"Invalid metadata block (lines {span[0] + 1}-{span[1] + 1}): {exc}"
    if not isinstance(data, dict):
        return {}, "The metadata block must contain a JSON object {…}."
    return data, None


def format_block(data: dict, prefix: str = "#") -> list:
    """The block's lines: one key per line, one parameter per line (compact and diff-friendly)."""
    body = ["{"]
    items = list(data.items())
    for n, (key, value) in enumerate(items):
        comma = "," if n < len(items) - 1 else ""
        if key == "parameters" and isinstance(value, list) and value:
            body.append(f'  "parameters": [')
            for i, p in enumerate(value):
                body.append("    " + json.dumps(p, ensure_ascii=False) + ("," if i < len(value) - 1 else ""))
            body.append("  ]" + comma)
        else:
            body.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    body.append("}")
    return [f"{prefix} {START}"] + [f"{prefix} {line}" for line in body] + [f"{prefix} {END}"]


def _read_script(script_path: Path):
    """(text, bom, newline) of the start of the script; raises OSError."""
    with open(script_path, "rb") as fh:
        blob = fh.read(MAX_SCAN + 1)
    bom = blob.startswith(b"\xef\xbb\xbf")
    text = blob[3 if bom else 0:MAX_SCAN].decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in text else ("\r\n" if Path(script_path).suffix.lower() in BATCH and "\n" not in text else "\n")
    return text, bom, newline


# ----------------------------------------------------------------- load ---
def load_raw(script_path: Path, script_id: str):
    """Return (data_dict, source_path_or_None, error_or_None). Never raises.
    ``source`` is the script itself when it has a block."""
    try:
        text, _bom, _nl = _read_script(script_path)
    except OSError:
        text = ""
    data, err = parse_block(text)
    if data is not None:
        return data, Path(script_path), err
    for candidate in (sidecar_path(script_path), override_path(script_id)):
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


def _write_script(script_path: Path, data: dict) -> None:
    script_path = Path(script_path)
    raw = script_path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    content = raw[3 if bom else 0:].decode("utf-8")       # UnicodeDecodeError -> not ours to rewrite
    newline = "\r\n" if "\r\n" in content else ("\r\n" if script_path.suffix.lower() in BATCH and "\n" not in content else "\n")
    new = with_block(content, data, script_path, newline)
    payload = (b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8")
    if payload == raw:
        return
    script_path.with_name(script_path.name + ".bak").write_bytes(raw)   # "Restore previous" in the editor
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


def save_raw(script_path: Path, script_id: str, data: dict) -> Path:
    """Write the metadata into the script (moving an old sidecar in); falls back to
    runtime/metadata_overrides/ when the script cannot be written. Returns where it went."""
    try:
        _write_script(script_path, data)
    except (OSError, UnicodeDecodeError):
        target = override_path(script_id)
        atomic_write_json(target, data)
        return target
    for old in (sidecar_path(script_path), override_path(script_id)):   # now inside the script
        try:
            if isinstance(read_json(old), dict):     # only a metadata object, never an unrelated file
                old.unlink()
        except (OSError, ValueError):
            pass
    return Path(script_path)
