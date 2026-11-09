"""The open script folder and any individually opened script files, plus recent and
favourite folders and files (runtime/workspace.json).

Like an editor's "Open Folder" / "Open File": at most one folder is open (scanned for
scripts), and any number of script files can be opened on their own, with or without
a folder; all of it is reopened on the next start. This is state the user changes from
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
MAX_SEQ_FAVORITES = 100
MAX_FILES = 30
_lock = threading.RLock()


def state_file() -> Path:
    # Next to config.json, so tests that redirect CONFIG_FILE get their own workspace too.
    return config.CONFIG_FILE.parent / "workspace.json"


def normalize(value: str) -> str:
    """Absolute, resolved path as stored (relative paths are relative to the app)."""
    return str(config.resolve_dir(value))


def _key(path: str) -> str:
    try:                                   # also C:\PROGRA~1 (8.3 short names) = C:\Program Files
        path = os.path.realpath(path)
    except (OSError, ValueError):
        path = os.path.normpath(path)
    return os.path.normcase(path)


def same(a: str, b: str) -> bool:
    return _key(a) == _key(b)


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
    return {"folder": folder, "files": [], "recent": _dedupe(dirs, MAX_RECENT), "favorites": [], "sequence_favorites": []}


def _slugs(values, limit):
    out = []
    for v in values:
        if isinstance(v, str) and v.strip() and v not in out:
            out.append(v)
    return out[:limit]


def load() -> dict:
    """{"folder": str|None, "files": [str], "recent": [str], "favorites": [str],
    "sequence_favorites": [slug]}. Never raises."""
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
        folder = folder if isinstance(folder, str) and folder.strip() else None
        files = _dedupe(data.get("files") or [], MAX_FILES)
        if folder and Path(folder).is_file():          # a single file stored as "folder" (2.1 betas)
            files, folder = _dedupe([folder] + files, MAX_FILES), None
        return {
            "folder": folder,
            "files": files,
            "recent": _dedupe(data.get("recent") or [], MAX_RECENT),
            "favorites": _dedupe(data.get("favorites") or [], MAX_FAVORITES),
            "sequence_favorites": _slugs(data.get("sequence_favorites") or [], MAX_SEQ_FAVORITES),
        }


def _save(state: dict) -> None:
    config.ensure_runtime_dirs()
    state_file().parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_file(), state)


def script_extensions() -> set:
    """File types ScriptHub can run (the configured interpreters)."""
    return {k.lower() for k in config.load_config()["interpreters"]}


def current() -> Path | None:
    """The open folder (None when only files, or nothing, are open)."""
    folder = load()["folder"]
    return Path(folder) if folder else None


def open_files() -> list[Path]:
    return [Path(p) for p in load()["files"]]


def current_is_file() -> bool:
    """True when script files are open but no folder (New script then has nowhere to go)."""
    state = load()
    return not state["folder"] and bool(state["files"])


def roots() -> list[Path]:
    """What to scan: the open folder, then each opened file not already inside it
    (missing ones are reported by discovery)."""
    state = load()
    folder = Path(state["folder"]) if state["folder"] else None
    out = [folder] if folder else []
    for f in state["files"]:
        p = Path(f)
        if folder is None or not _inside(p, folder):
            out.append(p)
    return out


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.normcase(str(path))).relative_to(os.path.normcase(str(folder)))
        return True
    except ValueError:
        return False


def open_folder(value: str) -> Path:
    """Open ``value``: an existing folder (replaces the open folder; opened files stay) or a
    script file with a configured extension (added to the opened files). It goes first in
    the recent list. Raises ValueError with a message for the user."""
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
        if p.is_file():
            state["files"] = _dedupe(state["files"] + [path], MAX_FILES)
        else:
            state["folder"] = path
        state["recent"] = _dedupe([path] + state["recent"], MAX_RECENT)
        _save(state)
    return Path(path)


def close_folder() -> None:
    with _lock:
        state = load()
        state["folder"] = None
        _save(state)


def close_file(value: str) -> bool:
    """Close an opened script file (not one that is part of the open folder)."""
    with _lock:
        state = load()
        rest = [p for p in state["files"] if not same(p, value)]
        if len(rest) == len(state["files"]):
            return False
        state["files"] = rest
        _save(state)
        return True


def close_all_files() -> None:
    with _lock:
        state = load()
        state["files"] = []
        _save(state)


def set_favorite(value: str, on: bool) -> str:
    path = normalize(value)
    with _lock:
        state = load()
        rest = [p for p in state["favorites"] if not same(p, path)]
        state["favorites"] = _dedupe(rest + [path], MAX_FAVORITES) if on else rest
        _save(state)
    return path


def sequence_favorites() -> list:
    """Slugs of the favourite sequences (UI state of this machine, like folder favourites)."""
    return load()["sequence_favorites"]


def set_sequence_favorite(slug: str, on: bool) -> None:
    with _lock:
        state = load()
        rest = [s for s in state["sequence_favorites"] if s != slug]
        state["sequence_favorites"] = _slugs(rest + [slug], MAX_SEQ_FAVORITES) if on else rest
        _save(state)


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
                "is_open": (bool(state["folder"]) and same(p, state["folder"])) or any(same(p, f) for f in state["files"])}
    folder = item(state["folder"]) if state["folder"] else None
    return {"folder": folder,
            "files": [item(p) for p in state["files"]],
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
