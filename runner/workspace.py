"""The open script folder (or a single script file), plus recent and favourite
folders and files (runtime/workspace.json).

Like an editor's "Open Folder" / "Open File": one folder (scanned for scripts) or
one script file is open at a time; it is reopened on the next start. This is state the user changes from
the UI (Open folder page, explorer header), not a setting.

Installs from before this file existed used the ``script_directories`` setting;
until the first change, the state is derived from it (first existing directory =
open folder, the rest = recent), so nothing has to be migrated by hand.
"""
import os
import threading
from pathlib import Path

import config
from .fsutil import atomic_write_json, read_json

MAX_RECENT = 10
MAX_FAVORITES = 50
_lock = threading.RLock()


def state_file() -> Path:
    # Next to config.json, so tests that redirect CONFIG_FILE get their own workspace too.
    return config.CONFIG_FILE.parent / "workspace.json"


def normalize(value: str) -> str:
    """Absolute, resolved path as stored (relative paths are relative to the app)."""
    return str(config.resolve_dir(value))


def same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _dedupe(paths, limit):
    out = []
    for p in paths:
        if isinstance(p, str) and p.strip() and not any(same(p, q) for q in out):
            out.append(p)
    return out[:limit]


def _legacy() -> dict:
    dirs = [normalize(d) for d in config.load_config().get("script_directories", [])]
    existing = [d for d in dirs if Path(d).is_dir()]
    folder = existing[0] if existing else None
    return {"folder": folder, "recent": _dedupe(dirs, MAX_RECENT), "favorites": []}


def load() -> dict:
    """{"folder": str|None, "recent": [str], "favorites": [str]}. Never raises."""
    with _lock:
        f = state_file()
        if not f.exists():
            return _legacy()
        try:
            data = read_json(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        folder = data.get("folder")
        return {
            "folder": folder if isinstance(folder, str) and folder.strip() else None,
            "recent": _dedupe(data.get("recent") or [], MAX_RECENT),
            "favorites": _dedupe(data.get("favorites") or [], MAX_FAVORITES),
        }


def _save(state: dict) -> None:
    config.ensure_runtime_dirs()
    state_file().parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_file(), state)


def script_extensions() -> set:
    """File types ScriptHub can run (the configured interpreters)."""
    return {k.lower() for k in config.load_config()["interpreters"]}


def current() -> Path | None:
    folder = load()["folder"]
    return Path(folder) if folder else None


def current_is_file() -> bool:
    """True when a single script file is open (not a folder)."""
    path = current()
    return bool(path) and path.is_file()


def roots() -> list[Path]:
    """What to scan: the open folder or the open script file (missing ones are reported by discovery)."""
    folder = current()
    return [folder] if folder else []


def open_folder(value: str) -> Path:
    """Open ``value``, an existing folder or a script file (a configured extension), and put
    it first in the recent list. Raises ValueError with a message for the user."""
    from .security import clean_directory_input
    path = normalize(clean_directory_input(value))
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() not in script_extensions():
            raise ValueError(f"Not a script type ScriptHub can run (see Settings, Interpreters): {p.name}")
    elif not p.is_dir():
        raise ValueError(f"Not an existing folder or script file: {path}")
    with _lock:
        state = load()
        state["folder"] = path
        state["recent"] = _dedupe([path] + state["recent"], MAX_RECENT)
        _save(state)
    return Path(path)


def close_folder() -> None:
    with _lock:
        state = load()
        state["folder"] = None
        _save(state)


def set_favorite(value: str, on: bool) -> str:
    path = normalize(value)
    with _lock:
        state = load()
        rest = [p for p in state["favorites"] if not same(p, path)]
        state["favorites"] = _dedupe(rest + [path], MAX_FAVORITES) if on else rest
        _save(state)
    return path


def forget(value: str) -> None:
    """Remove a folder from the recent list (not from favourites, not the open one)."""
    with _lock:
        state = load()
        state["recent"] = [p for p in state["recent"] if not same(p, value)]
        _save(state)


def overview() -> dict:
    """Template data: the open folder and the lists, with existence and favourite flags."""
    state = load()
    favs = state["favorites"]

    def item(p):
        return {"path": p, "name": Path(p).name or p, "exists": Path(p).exists(), "is_file": Path(p).is_file(),
                "favorite": any(same(p, f) for f in favs),
                "is_open": bool(state["folder"]) and same(p, state["folder"])}
    folder = item(state["folder"]) if state["folder"] else None
    return {"folder": folder,
            "favorites": [item(p) for p in favs],
            "recent": [item(p) for p in state["recent"] if not any(same(p, f) for f in favs)]}


# ------------------------------------------------------------- folder browser --
def browse(value: str) -> dict:
    """Sub-folders and script files of ``value`` for the in-page browser (works without the
    desktop dialog). Empty/unknown value: the drives (Windows) or "/" plus the home folder."""
    places = [{"path": str(Path.home()), "name": "Home"}]
    if os.name == "nt":
        import string
        places += [{"path": f"{d}:\\", "name": f"{d}:"} for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    else:
        places.append({"path": "/", "name": "/"})
    path = None
    if value:
        try:
            p = config.resolve_dir(value)
            if p.is_dir():
                path = p
        except (OSError, ValueError):
            path = None
    if path is None:
        return {"path": "", "parent": "", "dirs": [], "files": [], "places": places, "crumbs": [], "error": ""}
    dirs, files, error = [], [], ""
    exts = script_extensions()
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.name.startswith((".", "$")):
                        continue
                    if e.is_dir():
                        dirs.append({"path": str(path / e.name), "name": e.name})
                    elif Path(e.name).suffix.lower() in exts:
                        files.append({"path": str(path / e.name), "name": e.name})
                except OSError:
                    continue
    except OSError as exc:
        error = f"Cannot list this folder: {exc.strerror or exc}"
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda d: d["name"].lower())
    crumbs = [{"path": str(p), "name": p.name or str(p)} for p in reversed([path, *path.parents])]
    parent = str(path.parent) if path.parent != path else ""
    return {"path": str(path), "parent": parent, "dirs": dirs[:500], "files": files[:500], "more": len(dirs) > 500,
            "places": places, "crumbs": crumbs, "error": error}
