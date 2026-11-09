"""Sequences: existing scripts run one after another.

One YAML file per sequence, under runtime/ (like saved values, not in the script folder):

    runtime/sequences/<slug>.yaml

    # Sequence for ScriptHub. Passwords are never stored.
    name: Nightly maintenance
    saved_at: 2026-11-08T21:10:00
    steps:
      - script: 3f9a1c0b7d22        # script id (stable id from the metadata block)
        path: C:\\scripts\\backup.py  # last known path: shown, and used when the id is not in the open folder
        name: Backup                # last known script name (shown when the script is missing)
        host: local                 # empty = the script's own default host
        preset: production          # values from this preset of the script ...
        on_failure: stop            # stop | continue
      - script: 81be44f0a913
        path: C:\\scripts\\cleanup.sh
        name: Cleanup
        values:                     # ... or values stored with the step (raw form values)
          days: 30
        on_failure: continue

The slug is fixed when the sequence is created (URLs and sequence runs refer to it);
renaming changes only ``name``. Values are re-validated against the script's current
metadata when the sequence starts; password parameters are never written (they are
asked for when the sequence starts). A file that cannot be read is listed as broken
and never overwritten.
"""
import re
import threading
from pathlib import Path

import config
from . import discovery, history, ids, yamlish
from .fsutil import atomic_write_text

EXT = ".yaml"
NAME_MAX = 60
STEPS_MAX = 50
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ON_FAILURE = ("stop", "continue")
_lock = threading.RLock()


class SequenceError(Exception):
    pass


# ------------------------------------------------------------- helpers ---
def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "sequence"


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise SequenceError("Enter a name for the sequence.")
    if len(name) > NAME_MAX:
        raise SequenceError(f"The name must be at most {NAME_MAX} characters.")
    if any(ord(c) < 32 for c in name):
        raise SequenceError("The name contains invalid characters.")
    return name


def valid_slug(slug) -> bool:
    return isinstance(slug, str) and bool(SLUG_RE.match(slug))


def folder() -> Path:
    return config.SEQUENCES_DIR


def _file(slug: str) -> Path:
    if not valid_slug(slug):
        raise SequenceError("Invalid sequence.")
    return folder() / f"{slug}{EXT}"


def _clean_step(raw) -> dict:
    """A step as stored, with every key present; raises SequenceError."""
    if not isinstance(raw, dict):
        raise SequenceError("a step is not a mapping")
    sid = raw.get("script")
    if not isinstance(sid, str) or not re.match(r"^[0-9a-f]{6,64}$", sid):
        raise SequenceError("a step has no valid 'script' id")
    preset = raw.get("preset") or ""
    if preset and not (isinstance(preset, str) and SLUG_RE.match(preset)):
        raise SequenceError(f"step '{sid}': invalid preset")
    values = raw.get("values")
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise SequenceError(f"step '{sid}': 'values' must be a mapping")
    on_failure = raw.get("on_failure") or "stop"
    if on_failure not in ON_FAILURE:
        raise SequenceError(f"step '{sid}': on_failure must be stop or continue")
    host = raw.get("host") or ""
    return {
        "script": sid,
        "path": str(raw.get("path") or ""),
        "name": str(raw.get("name") or ""),
        "host": str(host),
        "preset": preset,
        "values": values,
        "on_failure": on_failure,
    }


def _clean(data) -> dict:
    if not isinstance(data, dict):
        raise SequenceError("not a sequence file")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SequenceError("the sequence has no name")
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise SequenceError("'steps' must be a list")
    return {
        "name": name.strip(),
        "description": str(data.get("description") or ""),
        "saved_at": str(data.get("saved_at") or ""),
        "steps": [_clean_step(s) for s in steps],
    }


def _dump_step(step: dict) -> dict:
    """The stored form: empty keys are left out, and ``values`` only without a preset."""
    out = {"script": step["script"]}
    for key in ("path", "name", "host", "preset"):
        if step.get(key):
            out[key] = step[key]
    if not step.get("preset"):
        out["values"] = dict(step.get("values") or {})
    out["on_failure"] = step.get("on_failure") or "stop"
    return out


def _read(slug: str, strict: bool = False):
    """The sequence, None when missing (or unreadable unless ``strict``, then SequenceError)."""
    path = _file(slug)
    try:
        data = _clean(yamlish.load(path.read_text(encoding="utf-8-sig")))
    except FileNotFoundError:
        if strict:
            raise SequenceError("That sequence does not exist.") from None
        return None
    except (OSError, ValueError, SequenceError) as exc:
        if strict:
            raise SequenceError(f"{path.name} could not be read ({exc}); fix or delete it first.") from exc
        return None
    data["slug"] = slug
    return data


def _write(slug: str, data: dict) -> None:
    out = {"name": data["name"]}
    if data.get("description"):
        out["description"] = data["description"]
    out["saved_at"] = history.now_iso()
    out["steps"] = [_dump_step(s) for s in data["steps"]]
    path = _file(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, yamlish.dump(out, header="Sequence for ScriptHub. Passwords are never stored."))


# ---------------------------------------------------------------- public ---
def list_sequences() -> list:
    """[{'slug', 'name', 'steps', 'saved_at', 'error'}] by name; a broken file has 'error'."""
    out = []
    try:
        files = sorted(folder().glob(f"*{EXT}"))
    except OSError:
        return []
    for f in files:
        slug = f.stem
        if not valid_slug(slug):
            continue
        try:
            data = _read(slug, strict=True)
            out.append({"slug": slug, "name": data["name"], "steps": len(data["steps"]),
                        "saved_at": data["saved_at"], "saved_display": data["saved_at"].replace("T", " ")[:16],
                        "error": ""})
        except SequenceError as exc:
            out.append({"slug": slug, "name": slug, "steps": 0, "saved_at": "", "saved_display": "",
                        "error": str(exc)})
    return sorted(out, key=lambda s: s["name"].lower())


def get(slug: str):
    """The sequence (with 'slug'), or None when missing or unreadable."""
    if not valid_slug(slug):
        return None
    return _read(slug)


def load(slug: str) -> dict:
    """Like get() but raises SequenceError with the reason."""
    return _read(slug, strict=True)


def create(name: str) -> str:
    name = clean_name(name)
    with _lock:
        taken = {s["name"].lower() for s in list_sequences()}
        if name.lower() in taken:
            raise SequenceError(f"A sequence named '{name}' already exists.")
        base = slug = slugify(name)
        n = 2
        while _file(slug).exists():
            slug = f"{base[:60]}-{n}"
            n += 1
        _write(slug, {"name": name, "steps": []})
        return slug


def rename(slug: str, name: str) -> None:
    name = clean_name(name)
    with _lock:
        data = load(slug)
        if any(s["name"].lower() == name.lower() and s["slug"] != slug for s in list_sequences()):
            raise SequenceError(f"A sequence named '{name}' already exists.")
        data["name"] = name
        _write(slug, data)


def delete(slug: str) -> bool:
    with _lock:
        try:
            _file(slug).unlink()
            return True
        except FileNotFoundError:
            return False


def _step_index(data, index) -> int:
    if not isinstance(index, int) or not 0 <= index < len(data["steps"]):
        raise SequenceError("That step does not exist.")
    return index


def new_step(script, values: dict, preset: str = "") -> dict:
    return _clean_step({"script": script.id, "path": str(script.path), "name": script.name,
                        "preset": preset, "values": values})


def add_step(slug: str, step: dict) -> int:
    """Append a step; returns its index."""
    with _lock:
        data = load(slug)
        if len(data["steps"]) >= STEPS_MAX:
            raise SequenceError(f"A sequence has at most {STEPS_MAX} steps.")
        data["steps"].append(_clean_step(step))
        _write(slug, data)
        return len(data["steps"]) - 1


def update_step(slug: str, index: int, **fields) -> None:
    with _lock:
        data = load(slug)
        i = _step_index(data, index)
        data["steps"][i] = _clean_step({**data["steps"][i], **fields})
        _write(slug, data)


def move_step(slug: str, index: int, delta: int) -> int:
    """Move a step up (-1) or down (+1); returns its new index."""
    with _lock:
        data = load(slug)
        i = _step_index(data, index)
        j = max(0, min(len(data["steps"]) - 1, i + delta))
        if i != j:
            steps = data["steps"]
            steps.insert(j, steps.pop(i))
            _write(slug, data)
        return j


def remove_step(slug: str, index: int) -> None:
    with _lock:
        data = load(slug)
        data["steps"].pop(_step_index(data, index))
        _write(slug, data)


def resolve(step: dict):
    """The step's script: by id in the open folder, else from its last known path
    (when that file still has the same id). None when it cannot be found."""
    sid = step["script"]
    script = discovery.get_script(sid)
    if script is not None:
        return script
    if not step.get("path"):
        return None
    script = discovery.load_file(step["path"])
    if script is None:
        return None
    if sid in (script.id, script.path_id) or ids.canonical(sid) == script.id:
        return script
    return None


def refresh(slug: str, data: dict, scripts: list) -> None:
    """Remember where each step's script is now (path and name), so a step still finds
    a moved script when its folder is not open. ``scripts`` lines up with the steps."""
    updates = []                                     # (index, id it had, new fields)
    for i, (step, script) in enumerate(zip(data["steps"], scripts)):
        if script is None:
            continue
        new = {"path": str(script.path), "name": script.name, "script": script.id}
        if any(step[k] != v for k, v in new.items()):
            updates.append((i, step["script"], new))
            step.update(new)
    if not updates:
        return
    with _lock:
        try:
            current = load(slug)
        except SequenceError:
            return
        for i, old_id, new in updates:               # only steps that were not changed meanwhile
            if i < len(current["steps"]) and current["steps"][i]["script"] == old_id:
                current["steps"][i].update(new)
        try:
            _write(slug, current)
        except OSError:
            pass
