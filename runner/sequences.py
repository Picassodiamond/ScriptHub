"""Sequences: existing scripts connected by arrows, run in the order the arrows give.

One YAML file per sequence, under runtime/ (like saved values, not in the script folder):

    runtime/sequences/<slug>.yaml

    # Sequence for ScriptHub. Passwords are never stored.
    name: Nightly maintenance
    saved_at: 2026-11-09T21:10:00
    steps:
      - id: s1                      # step id, unique in the sequence (arrows refer to it)
        script: 3f9a1c0b7d22        # script id (stable id from the metadata block)
        path: C:\\scripts\\backup.py  # last known path: shown, and used when the id is not in the open folder
        name: Backup                # last known script name (shown when the script is missing)
        host: local                 # empty = the script's own default host
        preset: production          # values from this preset of the script ...
        x: 240                      # position on the page (top left of the box)
        y: 40
      - id: s2
        script: 81be44f0a913
        path: C:\\scripts\\cleanup.sh
        name: Cleanup
        values:                     # ... or values stored with the step (raw form values)
          days: 30
        join: any                   # several arrows in: all (default) = wait for all, any = the first
        x: 240
        y: 160
    links:
      - from: s1
        to: s2
        when: success               # success | failure | always

A step runs when the arrows leading into it allow it (see sequence_runner); steps with
no arrow into them start first. Arrows never form a loop.

The slug is fixed when the sequence is created (URLs and sequence runs refer to it);
renaming changes only ``name``. Values are re-validated against the script's current
metadata when the sequence starts; password parameters are never written (they are
asked for when the sequence starts). A file that cannot be read is listed as broken
and never overwritten. A file of the first version (a list of steps with ``on_failure:
stop|continue`` and no ``links``) is read as a top-to-bottom chain and written in this
form on the next change.
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
STEP_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
WHEN = ("success", "failure", "always")
JOIN = ("all", "any")
POS_MAX = 20000
GRID = 20
NODE_W, NODE_H, LAYER_GAP, COL_GAP = 200, 60, 60, 40     # used for the layout of migrated files
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


def _pos(value, default=0) -> int:
    """A coordinate: an int on the grid, within 0..POS_MAX."""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        v = default
    v = max(0, min(POS_MAX, v))
    return int(round(v / GRID) * GRID)


def _clean_step(raw) -> dict:
    """A step as stored, with every key present; raises SequenceError."""
    if not isinstance(raw, dict):
        raise SequenceError("a step is not a mapping")
    sid = raw.get("script")
    if not isinstance(sid, str) or not re.match(r"^[0-9a-f]{6,64}$", sid):
        raise SequenceError("a step has no valid 'script' id")
    step_id = raw.get("id")
    if not isinstance(step_id, str) or not STEP_ID_RE.match(step_id):
        raise SequenceError(f"step of '{sid}': invalid or missing 'id'")
    preset = raw.get("preset") or ""
    if preset and not (isinstance(preset, str) and SLUG_RE.match(preset)):
        raise SequenceError(f"step '{step_id}': invalid preset")
    values = raw.get("values")
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise SequenceError(f"step '{step_id}': 'values' must be a mapping")
    join = raw.get("join") or "all"
    if join not in JOIN:
        raise SequenceError(f"step '{step_id}': join must be all or any")
    return {
        "id": step_id,
        "script": sid,
        "path": str(raw.get("path") or ""),
        "name": str(raw.get("name") or ""),
        "host": str(raw.get("host") or ""),
        "preset": preset,
        "values": values,
        "join": join,
        "x": _pos(raw.get("x")),
        "y": _pos(raw.get("y")),
    }


def _clean_link(raw, step_ids) -> dict:
    if not isinstance(raw, dict):
        raise SequenceError("an arrow is not a mapping")
    a, b, when = raw.get("from"), raw.get("to"), raw.get("when") or "success"
    if a not in step_ids or b not in step_ids:
        raise SequenceError(f"the arrow {a} -> {b} refers to a step that does not exist")
    if a == b:
        raise SequenceError(f"the arrow {a} -> {b} leads to itself")
    if when not in WHEN:
        raise SequenceError(f"the arrow {a} -> {b}: when must be success, failure or always")
    return {"from": a, "to": b, "when": when}


def _legacy(steps: list) -> tuple:
    """A first-version file: steps in a list, ``on_failure`` on each. Becomes a chain from
    top to bottom; ``continue`` becomes an ``always`` arrow to the next step."""
    out, links, prev_continue = [], [], False
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise SequenceError("a step is not a mapping")
        step = dict(raw)
        on_failure = step.pop("on_failure", None) or "stop"
        if on_failure not in ("stop", "continue"):
            raise SequenceError(f"step {i + 1}: on_failure must be stop or continue")
        step.setdefault("id", f"s{i + 1}")
        step.setdefault("x", 2 * GRID)
        step.setdefault("y", 2 * GRID + i * (NODE_H + LAYER_GAP))
        if out:
            links.append({"from": out[-1]["id"], "to": step["id"], "when": "always" if prev_continue else "success"})
        out.append(step)
        prev_continue = on_failure == "continue"
    return out, links


def _clean(data) -> dict:
    if not isinstance(data, dict):
        raise SequenceError("not a sequence file")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SequenceError("the sequence has no name")
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise SequenceError("'steps' must be a list")
    links = data.get("links")
    if links is None:
        steps, links = _legacy(steps)
    if not isinstance(links, list):
        raise SequenceError("'links' must be a list")
    steps = [_clean_step(s) for s in steps]
    step_ids = [s["id"] for s in steps]
    if len(set(step_ids)) != len(step_ids):
        raise SequenceError("two steps have the same id")
    out_links, seen = [], set()
    for raw in links:
        link = _clean_link(raw, step_ids)
        key = (link["from"], link["to"])
        if key in seen:
            raise SequenceError(f"the arrow {key[0]} -> {key[1]} is there twice")
        seen.add(key)
        out_links.append(link)
    data = {
        "name": name.strip(),
        "description": str(data.get("description") or ""),
        "saved_at": str(data.get("saved_at") or ""),
        "steps": steps,
        "links": out_links,
    }
    if topo_order(data) is None:
        raise SequenceError("the arrows form a loop")
    return data


def _dump_step(step: dict) -> dict:
    """The stored form: empty keys are left out, and ``values`` only without a preset."""
    out = {"id": step["id"], "script": step["script"]}
    for key in ("path", "name", "host", "preset"):
        if step.get(key):
            out[key] = step[key]
    if not step.get("preset"):
        out["values"] = dict(step.get("values") or {})
    if step.get("join", "all") != "all":
        out["join"] = step["join"]
    out["x"] = step["x"]
    out["y"] = step["y"]
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


def dump_text(data: dict) -> str:
    """The file text of a sequence (also used inside project files)."""
    out = {"name": data["name"]}
    if data.get("description"):
        out["description"] = data["description"]
    out["saved_at"] = history.now_iso()
    out["steps"] = [_dump_step(s) for s in data["steps"]]
    out["links"] = [dict(link) for link in data.get("links", [])]
    return yamlish.dump(out, header="Sequence for ScriptHub. Passwords are never stored.")


def parse_text(text: str) -> dict:
    """A sequence from file text (checked as a stored file is); raises SequenceError."""
    try:
        return _clean(yamlish.load(text))
    except ValueError as exc:
        raise SequenceError(str(exc)) from None


def _write(slug: str, data: dict) -> None:
    path = _file(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, dump_text(data))


# ------------------------------------------------------------------ graph ---
def topo_order(data: dict):
    """Step ids so that every arrow points forward (steps keep their list order where
    the arrows allow it); None when the arrows form a loop."""
    ids_ = [s["id"] for s in data["steps"]]
    indeg = {i: 0 for i in ids_}
    out = {i: [] for i in ids_}
    for link in data.get("links", []):
        out[link["from"]].append(link["to"])
        indeg[link["to"]] += 1
    order, ready = [], [i for i in ids_ if indeg[i] == 0]
    rank = {i: n for n, i in enumerate(ids_)}
    while ready:
        ready.sort(key=rank.get)
        cur = ready.pop(0)
        order.append(cur)
        for nxt in out[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    return order if len(order) == len(ids_) else None


def incoming(data: dict, step_id: str) -> list:
    return [link for link in data.get("links", []) if link["to"] == step_id]


def outgoing(data: dict, step_id: str) -> list:
    return [link for link in data.get("links", []) if link["from"] == step_id]


def _reaches(data: dict, start: str, goal: str) -> bool:
    todo, seen = [start], set()
    while todo:
        cur = todo.pop()
        if cur == goal:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        todo.extend(link["to"] for link in outgoing(data, cur))
    return False


def find(data: dict, step_id) -> int:
    for i, s in enumerate(data["steps"]):
        if s["id"] == step_id:
            return i
    raise SequenceError("That step does not exist.")


def _new_id(data: dict) -> str:
    """One more than the highest ``s<n>`` (a removed step's id is not given to the next one)."""
    taken = {s["id"] for s in data["steps"]}
    n = 1 + max([int(t[1:]) for t in taken if re.match(r"^s\d{1,6}$", t)] or [0])
    while f"s{n}" in taken:
        n += 1
    return f"s{n}"


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
        _write(slug, {"name": name, "steps": [], "links": []})
        return slug


def add_imported(data: dict) -> str:
    """Store a sequence read with parse_text() under a new slug (its name must be free); returns the slug."""
    name = clean_name(data["name"])
    with _lock:
        if any(s["name"].lower() == name.lower() for s in list_sequences()):
            raise SequenceError(f"A sequence named '{name}' already exists.")
        base = slug = slugify(name)
        n = 2
        while _file(slug).exists():
            slug = f"{base[:60]}-{n}"
            n += 1
        _write(slug, {**data, "name": name})
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


def new_step(script, values: dict, preset: str = "", x=0, y=0) -> dict:
    """A step for ``script`` (its id is given by add_step)."""
    return {"id": "s0", "script": script.id, "path": str(script.path), "name": script.name,
            "preset": preset, "values": values, "x": x, "y": y}


def add_step(slug: str, step: dict) -> str:
    """Add a step (anywhere on the page); returns its id."""
    with _lock:
        data = load(slug)
        if len(data["steps"]) >= STEPS_MAX:
            raise SequenceError(f"A sequence has at most {STEPS_MAX} steps.")
        step = _clean_step({**step, "id": _new_id(data)})
        data["steps"].append(step)
        _write(slug, data)
        return step["id"]


def update_step(slug: str, step_id: str, **fields) -> None:
    fields.pop("id", None)
    with _lock:
        data = load(slug)
        i = find(data, step_id)
        data["steps"][i] = _clean_step({**data["steps"][i], **fields})
        _write(slug, data)


def remove_step(slug: str, step_id: str) -> None:
    """Remove a step and the arrows to and from it."""
    with _lock:
        data = load(slug)
        data["steps"].pop(find(data, step_id))
        data["links"] = [k for k in data["links"] if step_id not in (k["from"], k["to"])]
        _write(slug, data)


def move_steps(slug: str, positions: dict) -> None:
    """Place steps: {step id: (x, y)}; unknown ids raise SequenceError."""
    with _lock:
        data = load(slug)
        for step_id, (x, y) in positions.items():
            step = data["steps"][find(data, step_id)]
            step["x"], step["y"] = _pos(x), _pos(y)
        _write(slug, data)


def link(slug: str, a: str, b: str, when: str = "success") -> None:
    """Add an arrow a -> b, or change its condition when it is there already."""
    if when not in WHEN:
        raise SequenceError("Unknown condition.")
    with _lock:
        data = load(slug)
        find(data, a)
        find(data, b)
        if a == b:
            raise SequenceError("A step cannot lead to itself.")
        for k in data["links"]:
            if k["from"] == a and k["to"] == b:
                k["when"] = when
                break
        else:
            if _reaches(data, b, a):
                raise SequenceError("That arrow would make a loop: the steps would never finish.")
            data["links"].append({"from": a, "to": b, "when": when})
        _write(slug, data)


def unlink(slug: str, a: str, b: str) -> None:
    with _lock:
        data = load(slug)
        before = len(data["links"])
        data["links"] = [k for k in data["links"] if not (k["from"] == a and k["to"] == b)]
        if len(data["links"]) == before:
            raise SequenceError("That arrow does not exist.")
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
    updates = []                                     # (step id, script id it had, new fields)
    for step, script in zip(data["steps"], scripts):
        if script is None:
            continue
        new = {"path": str(script.path), "name": script.name, "script": script.id}
        if any(step[k] != v for k, v in new.items()):
            updates.append((step["id"], step["script"], new))
            step.update(new)
    if not updates:
        return
    with _lock:
        try:
            current = load(slug)
        except SequenceError:
            return
        for step_id, old_id, new in updates:          # only steps that were not changed meanwhile
            for step in current["steps"]:
                if step["id"] == step_id and step["script"] == old_id:
                    step.update(new)
        try:
            _write(slug, current)
        except OSError:
            pass
