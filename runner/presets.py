"""Saved parameters: named presets and the values of recent runs.

They live next to the script, in one file per script:

    scripts/system/directory_backup.py
    scripts/system/directory_backup.json          <- metadata
    scripts/system/directory_backup.params.json   <- saved parameters (this module)

    {
      "presets": {"<slug>": {"name", "host", "values", "saved_at"}},
      "recent":  [{"host", "values", "saved_at", "run_id", "initiator"}, ...]   newest first
    }

So the values travel with the script (moving it, or committing it to git, keeps
them). If the script folder cannot be written, the file goes to
runtime/presets/<script_id>.json instead and is moved next to the script by the
next save that can write there. Presets from version 1
(runtime/presets/<script_id>/<slug>.json) are migrated on first use.

Values are raw form values, re-validated against the *current* metadata every
time they are loaded, so removed or renamed parameters are handled gracefully.
Password parameters are never written; parameters that are fixed at run time
are skipped too.
"""
import json
import re
import shutil
import threading
from pathlib import Path

import config
from . import history
from . import parameter_service as ps
from .fsutil import atomic_write_json, read_json

NAME_MAX = 60
RECENT_MAX = 10
SUFFIX = ".params.json"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SID_RE = re.compile(r"^[0-9a-f]{6,64}$")
_lock = threading.RLock()


class PresetError(Exception):
    pass


# ------------------------------------------------------------- helpers ---
def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "preset"


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise PresetError("Enter a name for the preset.")
    if len(name) > NAME_MAX:
        raise PresetError(f"The name must be at most {NAME_MAX} characters.")
    if any(ord(c) < 32 for c in name):
        raise PresetError("The name contains invalid characters.")
    return name


def _check(script):
    if script is None or not SID_RE.match(getattr(script, "id", "") or ""):
        raise PresetError("Invalid script.")


def _check_slug(slug):
    if not SLUG_RE.match(slug or ""):
        raise PresetError("Invalid preset.")


def file_next_to(script) -> Path:
    """<folder>/<stem>.params.json next to the script."""
    path = Path(script.path)
    return path.with_name(path.stem + SUFFIX)


def _fallback(script) -> Path:
    return config.PRESETS_DIR / f"{script.id}.json"


def _legacy_dir(script) -> Path:
    return config.PRESETS_DIR / script.id


def _empty():
    return {"presets": {}, "recent": []}


def _read(path: Path, strict: bool = False):
    """Parsed file, None if missing. A broken file reads as empty, but with ``strict``
    (before writing) it raises, so a hand-edited file is never silently overwritten."""
    try:
        data = read_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        if strict:
            raise PresetError(f"{path.name} could not be read ({exc}); fix or delete it first.") from exc
        return _empty()
    if not isinstance(data, dict):
        if strict:
            raise PresetError(f"{path.name} is not a saved-parameters file; fix or delete it first.")
        return _empty()
    out = _empty()
    for slug, p in (data.get("presets") or {}).items() if isinstance(data.get("presets"), dict) else []:
        if SLUG_RE.match(str(slug)) and isinstance(p, dict) and isinstance(p.get("name"), str) \
                and isinstance(p.get("values", {}), dict):
            out["presets"][slug] = p
    for r in data.get("recent") or [] if isinstance(data.get("recent"), list) else []:
        if isinstance(r, dict) and isinstance(r.get("values", {}), dict):
            out["recent"].append(r)
    return out


def _load(script, strict: bool = False):
    """The current saved parameters (merged from every place they may be). Returns (data, dirty).
    ``strict`` is used before writing: an unreadable file raises PresetError."""
    _check(script)
    data = _read(file_next_to(script), strict)
    dirty = False
    if data is None:
        data = _read(_fallback(script), strict)
        dirty = data is not None          # move it next to the script on the next save
        data = data or _empty()
    else:
        extra = _read(_fallback(script))
        if extra is not None:             # left over from a time the folder was read-only
            for slug, p in extra["presets"].items():
                data["presets"].setdefault(slug, p)
            dirty = True
    legacy = _legacy_dir(script)
    if legacy.is_dir():                   # version 1: one file per preset
        for f in legacy.glob("*.json"):
            try:
                p = read_json(f)
            except (OSError, ValueError):
                continue
            if isinstance(p, dict) and isinstance(p.get("name"), str) and SLUG_RE.match(f.stem):
                p.pop("script_id", None)
                data["presets"].setdefault(f.stem, p)
        dirty = True
    return data, dirty


def _save(script, data) -> Path:
    """Write next to the script; fall back to runtime/ when the folder is read-only."""
    payload = {
        "_comment": f"Saved parameters for {Path(script.path).name} (ScriptHub). Passwords are never stored.",
        "presets": data["presets"],
        "recent": data["recent"][:RECENT_MAX],
    }
    target = file_next_to(script)
    try:
        atomic_write_json(target, payload)
    except OSError:
        target = _fallback(script)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(target, payload)
    else:
        try:
            _fallback(script).unlink()
        except OSError:
            pass
    shutil.rmtree(_legacy_dir(script), ignore_errors=True)
    return target


def _loaded(script):
    data, dirty = _load(script)
    if dirty:                              # migrate / move next to the script, but never over a broken file
        try:
            _save(script, _load(script, strict=True)[0])
        except (OSError, PresetError):
            pass
    return data


def savable_values(params, raw: dict) -> dict:
    """The part of a submitted form that may be stored (no passwords, no fixed values)."""
    out = {}
    for p in params:
        if p["type"] == "password" or not p.get("editable_at_runtime", True):
            continue
        if p["name"] in raw:
            v = raw[p["name"]]
            out[p["name"]] = list(v) if isinstance(v, (list, tuple)) else v
    return out


# -------------------------------------------------------------- presets ---
def list_presets(script) -> list:
    """[{'slug', 'name', 'saved_at', 'host'}] sorted by name."""
    try:
        with _lock:
            data = _loaded(script)
    except PresetError:
        return []
    out = [{"slug": s, "name": p["name"], "saved_at": p.get("saved_at", ""), "host": p.get("host", "")}
           for s, p in data["presets"].items()]
    return sorted(out, key=lambda p: p["name"].lower())


def get_preset(script, slug: str):
    try:
        _check_slug(slug)
        with _lock:
            p = _loaded(script)["presets"].get(slug)
    except PresetError:
        return None
    return dict(p) if p else None


def save_preset(script, name: str, params, raw: dict, host: str, overwrite: bool = True) -> str:
    """Create or overwrite a preset; returns its slug."""
    name = clean_name(name)
    with _lock:
        data, _dirty = _load(script, strict=True)
        existing = {p["name"].lower(): s for s, p in data["presets"].items()}
        if not overwrite and name.lower() in existing:
            raise PresetError(f"A preset named '{name}' already exists.")
        slug = existing.get(name.lower())
        if slug is None:                    # keep slugs unique for different names
            base = slug = slugify(name)
            n = 2
            while slug in data["presets"]:
                slug = f"{base}-{n}"
                n += 1
        data["presets"][slug] = {"name": name, "host": host, "values": savable_values(params, raw),
                                 "saved_at": history.now_iso()}
        _save(script, data)
    return slug


def rename_preset(script, slug: str, new_name: str) -> str:
    new_name = clean_name(new_name)
    _check_slug(slug)
    with _lock:
        data, _dirty = _load(script, strict=True)
        if slug not in data["presets"]:
            raise PresetError("That preset no longer exists.")
        for s, p in data["presets"].items():
            if s != slug and p["name"].lower() == new_name.lower():
                raise PresetError(f"A preset named '{new_name}' already exists.")
        data["presets"][slug]["name"] = new_name
        _save(script, data)
    return slug


def delete_preset(script, slug: str) -> bool:
    _check_slug(slug)
    with _lock:
        data, dirty = _load(script, strict=True)
        if data["presets"].pop(slug, None) is None:
            if dirty:
                _save(script, data)
            return False
        _save(script, data)
    return True


# --------------------------------------------------------- recent runs ---
def record_run(script, params, raw: dict, host: str, run_id: str, initiator: str = "") -> None:
    """Remember the values a run was started with (newest first, duplicates collapse)."""
    values = savable_values(params, raw)
    key = json.dumps([host, values], sort_keys=True)
    with _lock:
        data, _dirty = _load(script, strict=True)
        data["recent"] = [r for r in data["recent"]
                          if json.dumps([r.get("host"), r.get("values", {})], sort_keys=True) != key]
        data["recent"].insert(0, {"host": host, "values": values, "saved_at": history.now_iso(),
                                  "run_id": run_id, "initiator": initiator})
        _save(script, data)


def recent_runs(script) -> list:
    """Newest first; each entry also carries ``key`` ("@<n>", for ?preset=), ``label`` and ``when``."""
    try:
        with _lock:
            runs = _loaded(script)["recent"][:RECENT_MAX]
    except PresetError:
        return []
    out = []
    for i, r in enumerate(runs):
        d = dict(r)
        d["key"] = f"@{i}"
        d["label"] = "Last run" if i == 0 else (r.get("run_id") or f"Run {i + 1}")
        d["when"] = (r.get("saved_at") or "").replace("T", " ")[:16]
        out.append(d)
    return out


def get_recent(script, index: int):
    runs = recent_runs(script)
    return dict(runs[index]) if 0 <= index < len(runs) else None


# --------------------------------------------------------------- views ---
def _fmt(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or "—"
    return "—" if v in (None, "") else str(v)


def grid(script, params) -> dict:
    """Comparison of all saved parameter sets: rows = parameters, columns = presets + recent runs."""
    try:
        with _lock:
            data = _loaded(script)
    except PresetError:
        return {"columns": [], "rows": []}
    cols = [{"kind": "preset", "key": s, "name": p["name"], "host": p.get("host", ""),
             "saved_at": (p.get("saved_at") or "").replace("T", " "), "values": p.get("values") or {}}
            for s, p in sorted(data["presets"].items(), key=lambda kv: kv[1]["name"].lower())]
    for i, r in enumerate(data["recent"][:RECENT_MAX]):
        cols.append({"kind": "recent", "key": f"@{i}", "name": "Last run" if i == 0 else (r.get("run_id") or f"Run {i + 1}"),
                     "run_id": r.get("run_id", ""), "host": r.get("host", ""),
                     "saved_at": (r.get("saved_at") or "").replace("T", " "), "values": r.get("values") or {}})
    defaults = ps.defaults_raw(params)
    default_host = (getattr(script, "meta", None) or {}).get("host", "local")
    rows = []
    host_cells = [{"text": c["host"] or "—", "changed": (c["host"] or "local") != default_host} for c in cols]
    rows.append({"label": "Target host", "name": "host", "cells": host_cells,
                 "varies": len({c["text"] for c in host_cells}) > 1})
    for p in params:
        name = p["name"]
        if p["type"] == "password":
            cells = [{"text": "not saved", "muted": True} for _ in cols]
        elif not p.get("editable_at_runtime", True):
            cells = [{"text": "fixed", "muted": True} for _ in cols]
        else:
            cells = []
            for c in cols:
                if name in c["values"]:
                    t = _fmt(c["values"][name])
                    cells.append({"text": t, "changed": t != _fmt(defaults.get(name))})
                else:
                    cells.append({"text": "default", "muted": True})
        rows.append({"label": p["label"], "name": name, "cells": cells,
                     "varies": len({c["text"] for c in cells}) > 1})
    return {"columns": cols, "rows": rows}


def file_info(script) -> dict:
    """Where the saved parameters live, and the file's text (for the raw view)."""
    try:
        _check(script)
    except PresetError:
        return {}
    for path, where in ((file_next_to(script), "next to the script"), (_fallback(script), "in runtime/ (script folder is read-only)")):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                text = ""
            return {"path": str(path), "name": path.name, "where": where, "text": text}
    return {"path": str(file_next_to(script)), "name": file_next_to(script).name, "where": "not created yet", "text": ""}


def apply_preset(params, preset: dict):
    """Merge stored values (a preset or a recent run) into the *current* parameter definitions.

    Returns ``(raw_values, warnings)``. Parameters the stored set does not know get
    their defaults; values that no longer validate (changed type, range or
    choices) fall back to the default; stored values for parameters that no
    longer exist are ignored. Each of those is reported in ``warnings``.
    """
    stored = preset.get("values") or {}
    raw = ps.defaults_raw(params)
    warnings = []
    current = {p["name"]: p for p in params}
    for name in stored:
        if name not in current:
            warnings.append(f"'{name}' no longer exists in this script and was ignored.")
    for p in params:
        name = p["name"]
        if p["type"] == "password" or not p.get("editable_at_runtime", True):
            continue
        if name not in stored:
            warnings.append(f"'{p['label']}' is new since these values were saved; its default was used.")
            continue
        candidate = stored[name]
        _value, err = ps.parse_value(p, candidate)
        if err:
            warnings.append(f"'{p['label']}' (saved value is no longer valid: {err.rstrip('.')}) was reset to its default.")
        else:
            raw[name] = candidate if p["type"] != "boolean" else bool(candidate)
    return raw, warnings
