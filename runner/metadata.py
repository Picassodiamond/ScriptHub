"""Script metadata: optional sidecar JSON next to each script.

backup.py -> backup.json. If the script folder is read-only we fall back to
runtime/metadata_overrides/<script_id>.json.
"""
import os
from pathlib import Path

import config
from .fsutil import atomic_write_json, read_json


def sidecar_path(script_path: Path) -> Path:
    return Path(script_path).with_suffix(".json")


def override_path(script_id: str) -> Path:
    return config.METADATA_OVERRIDE_DIR / f"{script_id}.json"


def load_raw(script_path: Path, script_id: str):
    """Return (data_dict, source_path_or_None, error_or_None). Never raises."""
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


def _backup_if_valid(path: Path) -> None:
    """Keep the previous *valid* file as .bak (never overwrite a good backup
    with a broken file)."""
    try:
        read_json(path)
    except (OSError, ValueError):
        return
    path.with_suffix(".json.bak").write_bytes(path.read_bytes())


def save_raw(script_path: Path, script_id: str, data: dict) -> Path:
    """Atomically save metadata, keeping a .bak of the previous valid file.

    Writes the sidecar; if that location is not writable, writes the override.
    """
    target = sidecar_path(script_path)
    existing = target if target.is_file() else None
    ov = override_path(script_id)
    if existing is None and ov.is_file():
        existing = ov
        target = ov

    for attempt in (target, ov):
        try:
            if attempt.is_file():
                _backup_if_valid(attempt)
            atomic_write_json(attempt, data)
            return attempt
        except OSError:
            if attempt == ov:
                raise
    raise OSError("Could not save metadata")  # pragma: no cover


def set_enabled(script_path: Path, script_id: str, enabled: bool) -> None:
    data, _src, err = load_raw(script_path, script_id)
    if err:
        raise ValueError(err)
    data["enabled"] = bool(enabled)
    save_raw(script_path, script_id, data)
