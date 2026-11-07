"""Named parameter presets, stored as JSON files:

    runtime/presets/<script_id>/<slug>.json

A preset holds raw form values plus the host. It is kept separate from the
script's metadata and is re-validated against the *current* metadata every
time it is loaded, so removed or renamed parameters are handled gracefully.

Password parameters are never written to a preset (enter them at run time);
parameters that are fixed at run time are skipped too.
"""
import re
import shutil

import config
from . import history
from . import parameter_service as ps
from .fsutil import atomic_write_json, read_json

NAME_MAX = 60
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SID_RE = re.compile(r"^[0-9a-f]{6,64}$")


class PresetError(Exception):
    pass


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


def _dir(script_id: str):
    if not SID_RE.match(script_id or ""):
        raise PresetError("Invalid script.")
    return config.PRESETS_DIR / script_id


def _path(script_id: str, slug: str):
    if not SLUG_RE.match(slug or ""):
        raise PresetError("Invalid preset.")
    return _dir(script_id) / f"{slug}.json"


def list_presets(script_id: str) -> list:
    """[{'slug', 'name', 'saved_at'}] sorted by name. Unreadable files are skipped."""
    try:
        folder = _dir(script_id)
    except PresetError:
        return []
    out = []
    if folder.is_dir():
        for f in folder.glob("*.json"):
            try:
                data = read_json(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("name"), str) and SLUG_RE.match(f.stem):
                out.append({"slug": f.stem, "name": data["name"], "saved_at": data.get("saved_at", "")})
    return sorted(out, key=lambda p: p["name"].lower())


def get_preset(script_id: str, slug: str):
    try:
        data = read_json(_path(script_id, slug))
    except (OSError, ValueError, PresetError):
        return None
    return data if isinstance(data, dict) else None


def savable_values(params, raw: dict) -> dict:
    """The part of a submitted form that belongs in a preset."""
    out = {}
    for p in params:
        if p["type"] == "password" or not p.get("editable_at_runtime", True):
            continue
        if p["name"] in raw:
            v = raw[p["name"]]
            out[p["name"]] = list(v) if isinstance(v, (list, tuple)) else v
    return out


def save_preset(script_id: str, name: str, params, raw: dict, host: str, overwrite: bool = True) -> str:
    """Create or overwrite a preset; returns its slug."""
    name = clean_name(name)
    existing = {p["name"].lower(): p["slug"] for p in list_presets(script_id)}
    slug = existing.get(name.lower()) or slugify(name)
    if not overwrite and name.lower() in existing:
        raise PresetError(f"A preset named '{name}' already exists.")
    if name.lower() not in existing:       # keep slugs unique for different names
        base, n = slug, 2
        taken = set(existing.values())
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
    atomic_write_json(_path(script_id, slug), {
        "name": name,
        "script_id": script_id,
        "host": host,
        "values": savable_values(params, raw),
        "saved_at": history.now_iso(),
    })
    return slug


def rename_preset(script_id: str, slug: str, new_name: str) -> str:
    new_name = clean_name(new_name)
    data = get_preset(script_id, slug)
    if data is None:
        raise PresetError("That preset no longer exists.")
    for p in list_presets(script_id):
        if p["slug"] != slug and p["name"].lower() == new_name.lower():
            raise PresetError(f"A preset named '{new_name}' already exists.")
    data["name"] = new_name
    atomic_write_json(_path(script_id, slug), data)
    return slug


def delete_preset(script_id: str, slug: str) -> bool:
    path = _path(script_id, slug)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    folder = _dir(script_id)
    try:
        folder.rmdir()  # tidy up when empty
    except OSError:
        pass
    return True


def delete_all_for_script(script_id: str) -> None:
    shutil.rmtree(_dir(script_id), ignore_errors=True)


def apply_preset(params, preset: dict):
    """Merge a stored preset into the *current* parameter definitions.

    Returns ``(raw_values, warnings)``. Parameters the preset does not know get
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
            warnings.append(f"'{p['label']}' is new since this preset was saved; its default was used.")
            continue
        candidate = stored[name]
        _value, err = ps.parse_value(p, candidate)
        if err:
            warnings.append(f"'{p['label']}' (saved value is no longer valid: {err.rstrip('.')}) was reset to its default.")
        else:
            raw[name] = candidate if p["type"] != "boolean" else bool(candidate)
    return raw, warnings
