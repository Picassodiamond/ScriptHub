"""Saved parameters: named presets and the values of recent runs.

One YAML file per saved set, under runtime/ (not in the script folder):

    runtime/presets/<script id>/<preset slug>.yaml        a named preset
    runtime/presets/<script id>/recent/<run id>.yaml      the values of a recent run (last 10 distinct)

    # Preset for C:\\scripts\\system\\directory_backup.py (ScriptHub). Passwords are never stored.
    name: Production backup
    host: local
    saved_at: 2026-11-08T20:44:13
    values:
      source: /data/project
      compress: true

The YAML is a small subset (runner/yamlish.py) so ScriptHub keeps no dependency
besides Django. Saved values belong to the script's id (its path): moving or
renaming the script starts with none. Older layouts are migrated on first use and
then removed: <stem>.params.json next to the script, runtime/presets/<id>.json and
runtime/presets/<id>/<slug>.json.

Values are raw form values, re-validated against the *current* metadata every
time they are loaded, so removed or renamed parameters are handled gracefully.
Password parameters are never written; parameters that are fixed at run time
are skipped too. A file that cannot be read is never overwritten.
"""
import json
import re
import threading
import time
from pathlib import Path

import config
from . import history, yamlish
from . import parameter_service as ps
from .fsutil import atomic_write_text, read_json

NAME_MAX = 60
RECENT_MAX = 10
EXT = ".yaml"
LEGACY_SUFFIX = ".params.json"
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


def folder(script) -> Path:
    """runtime/presets/<script id>/: one YAML file per preset, recent/ for recent runs."""
    return config.PRESETS_DIR / script.id


def _preset_file(script, slug) -> Path:
    return folder(script) / f"{slug}{EXT}"


def _recent_dir(script) -> Path:
    return folder(script) / "recent"


def _header(script, what) -> str:
    return f"{what} for {script.path} (ScriptHub). Passwords are never stored."


def _read_set(path: Path, strict: bool = False):
    """One saved set (a dict with a ``values`` mapping), None when missing or unusable.
    With ``strict`` (before overwriting it) an unreadable file raises PresetError."""
    try:
        data = yamlish.load(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        if strict:
            raise PresetError(f"{path.name} could not be read ({exc}); fix or delete it first.") from exc
        return None
    if not isinstance(data.get("values", {}), dict):
        if strict:
            raise PresetError(f"{path.name} is not a saved-values file; fix or delete it first.")
        return None
    data.setdefault("values", {})
    return data


def _write_set(script, path: Path, data: dict, what: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, yamlish.dump(data, header=_header(script, what)))


# ------------------------------------------------------------ migration ---
def file_next_to(script) -> Path:
    """Old location (before version 2.1): <folder>/<stem>.params.json next to the script."""
    path = Path(script.path)
    return path.with_name(path.stem + LEGACY_SUFFIX)


def _read_old(path: Path):
    """An old JSON file ({"presets": {...}, "recent": [...]}); None when missing or unreadable."""
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    presets = {s: p for s, p in (data.get("presets") or {}).items()
               if SLUG_RE.match(str(s)) and isinstance(p, dict) and isinstance(p.get("name"), str)
               and isinstance(p.get("values", {}), dict)} if isinstance(data.get("presets"), dict) else {}
    recent = [r for r in data.get("recent") or [] if isinstance(r, dict) and isinstance(r.get("values", {}), dict)] \
        if isinstance(data.get("recent"), list) else []
    return {"presets": presets, "recent": recent}


def _migrate(script) -> None:
    """Move saved values from the older layouts into the YAML files (then remove the old files)."""
    old_files = [file_next_to(script), config.PRESETS_DIR / f"{script.id}.json"]
    v1 = sorted(folder(script).glob("*.json")) if folder(script).is_dir() else []
    if not any(p.is_file() for p in old_files) and not v1:
        return
    done = []
    for path in old_files:
        old = _read_old(path) if path.is_file() else None
        if old is None:
            continue
        for slug, p in old["presets"].items():
            target = _preset_file(script, slug)
            if not target.exists():
                _write_set(script, target, {k: p[k] for k in ("name", "host", "saved_at", "values") if k in p}, "Preset")
        for i, r in enumerate(old["recent"][:RECENT_MAX]):
            rid = r.get("run_id") if history.valid_run_id(str(r.get("run_id", ""))) else f"migrated-{i:02d}"
            target = _recent_dir(script) / f"{rid}{EXT}"
            if not target.exists():
                _write_set(script, target, {k: r[k] for k in ("host", "saved_at", "run_id", "initiator", "values") if k in r},
                           "Values of a recent run")
        done.append(path)
    for f in v1:                                   # version 1: one JSON file per preset
        try:
            p = read_json(f)
        except (OSError, ValueError):
            continue
        if isinstance(p, dict) and isinstance(p.get("name"), str) and SLUG_RE.match(f.stem):
            if not _preset_file(script, f.stem).exists():
                _write_set(script, _preset_file(script, f.stem),
                           {k: p[k] for k in ("name", "host", "saved_at", "values") if k in p}, "Preset")
            done.append(f)
    for path in done:
        try:
            path.unlink()
        except OSError:
            pass


def _load(script) -> dict:
    """{"presets": {slug: set}, "recent": [set, ...] newest first}. Unreadable files are skipped."""
    _check(script)
    with _lock:
        try:
            _migrate(script)
        except OSError:
            pass
        presets, recent = {}, []
        base = folder(script)
        if base.is_dir():
            for f in sorted(base.glob(f"*{EXT}")):
                p = _read_set(f)
                if SLUG_RE.match(f.stem) and p is not None and isinstance(p.get("name"), str):
                    presets[f.stem] = p
        rdir = _recent_dir(script)
        if rdir.is_dir():
            for f in rdir.glob(f"*{EXT}"):
                r = _read_set(f)
                if r is not None:
                    r["_file"] = f
                    try:
                        r["_mtime"] = f.stat().st_mtime_ns
                    except OSError:
                        r["_mtime"] = 0
                    recent.append(r)
        # newest first: saved_at has seconds only; "order" (ns, written by record_run) and then
        # the file time break ties within a second
        recent.sort(key=lambda r: (str(r.get("saved_at") or ""), r.get("order") if isinstance(r.get("order"), int) else 0,
                                   r["_mtime"]), reverse=True)
        return {"presets": presets, "recent": recent}


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


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


# -------------------------------------------------------------- presets ---
def list_presets(script) -> list:
    """[{'slug', 'name', 'saved_at', 'host'}] sorted by name."""
    try:
        data = _load(script)
    except PresetError:
        return []
    out = [{"slug": s, "name": p["name"], "saved_at": p.get("saved_at", ""), "host": p.get("host", "")}
           for s, p in data["presets"].items()]
    return sorted(out, key=lambda p: p["name"].lower())


def get_preset(script, slug: str):
    try:
        _check_slug(slug)
        p = _load(script)["presets"].get(slug)
    except PresetError:
        return None
    return _public(p) if p else None


def save_preset(script, name: str, params, raw: dict, host: str, overwrite: bool = True) -> str:
    """Create or overwrite a preset (its own file); returns its slug."""
    name = clean_name(name)
    with _lock:
        data = _load(script)
        existing = {p["name"].lower(): s for s, p in data["presets"].items()}
        if not overwrite and name.lower() in existing:
            raise PresetError(f"A preset named '{name}' already exists.")
        slug = existing.get(name.lower())
        if slug is None:                    # a new name: a free slug (also not taken by an unreadable file)
            base = slug = slugify(name)
            n = 2
            while slug in data["presets"] or _preset_file(script, slug).exists():
                slug = f"{base}-{n}"
                n += 1
        target = _preset_file(script, slug)
        if target.exists():
            _read_set(target, strict=True)   # never overwrite a file that cannot be read
        _write_set(script, target, {"name": name, "host": host, "saved_at": history.now_iso(),
                                    "values": savable_values(params, raw)}, "Preset")
    return slug


def rename_preset(script, slug: str, new_name: str) -> str:
    new_name = clean_name(new_name)
    _check_slug(slug)
    with _lock:
        data = _load(script)
        target = _preset_file(script, slug)
        p = _read_set(target, strict=True)
        if p is None:
            raise PresetError("That preset no longer exists.")
        for s, other in data["presets"].items():
            if s != slug and other["name"].lower() == new_name.lower():
                raise PresetError(f"A preset named '{new_name}' already exists.")
        p["name"] = new_name
        _write_set(script, target, p, "Preset")
    return slug


def delete_preset(script, slug: str) -> bool:
    _check_slug(slug)
    with _lock:
        _load(script)                        # migrates old files first
        try:
            _preset_file(script, slug).unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PresetError(f"Could not delete the preset: {exc}") from exc
    return True


# --------------------------------------------------------- recent runs ---
def _same(a: dict, host, values) -> bool:
    return json.dumps([a.get("host"), a.get("values", {})], sort_keys=True) == json.dumps([host, values], sort_keys=True)


def record_run(script, params, raw: dict, host: str, run_id: str, initiator: str = "") -> None:
    """Remember the values a run was started with: one file per run, the newest
    RECENT_MAX distinct sets are kept (an identical earlier set is replaced)."""
    values = savable_values(params, raw)
    name = run_id if history.valid_run_id(run_id or "") else "run-" + history.now_iso().replace(":", "")
    with _lock:
        data = _load(script)
        for r in data["recent"]:
            if _same(r, host, values):
                r["_file"].unlink(missing_ok=True)
        _write_set(script, _recent_dir(script) / f"{name}{EXT}",
                   {"host": host, "saved_at": history.now_iso(), "order": time.time_ns(), "run_id": run_id,
                    "initiator": initiator, "values": values}, "Values of a recent run")
        for r in _load(script)["recent"][RECENT_MAX:]:
            r["_file"].unlink(missing_ok=True)


def recent_runs(script) -> list:
    """Newest first; each entry also carries ``key`` ("@<n>", for ?preset=), ``label`` and ``when``."""
    try:
        runs = _load(script)["recent"][:RECENT_MAX]
    except PresetError:
        return []
    out = []
    for i, r in enumerate(runs):
        d = _public(r)
        d["key"] = f"@{i}"
        d["label"] = "Last run" if i == 0 else (r.get("run_id") or f"Run {i + 1}")
        d["when"] = str(r.get("saved_at") or "").replace("T", " ")[:16]
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
        data = _load(script)
    except PresetError:
        return {"columns": [], "rows": []}
    cols = [{"kind": "preset", "key": s, "name": p["name"], "host": p.get("host", ""),
             "saved_at": str(p.get("saved_at") or "").replace("T", " "), "values": p.get("values") or {}}
            for s, p in sorted(data["presets"].items(), key=lambda kv: kv[1]["name"].lower())]
    for i, r in enumerate(data["recent"][:RECENT_MAX]):
        cols.append({"kind": "recent", "key": f"@{i}", "name": "Last run" if i == 0 else (r.get("run_id") or f"Run {i + 1}"),
                     "run_id": r.get("run_id", ""), "host": r.get("host", ""),
                     "saved_at": str(r.get("saved_at") or "").replace("T", " "), "values": r.get("values") or {}})
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
    """Where the saved values of a script live (shown as a tooltip)."""
    try:
        _check(script)
    except PresetError:
        return {}
    return {"path": str(folder(script)), "name": "saved values"}


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
