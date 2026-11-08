"""Script discovery: builds the approved-script catalogue.

Only files whose extension is explicitly mapped to an interpreter in the
configuration are catalogued. Nothing is ever executed during discovery.
"""
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from . import metadata
from .parameter_service import validate_metadata
from .validators import ValidationError

SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".venv", "site-packages", "env"}
CACHE_TTL = 2.0  # seconds; new scripts appear without a restart
MAX_DEPTH = 3    # sub-folder levels below the open folder that are scanned (root = 0)
AUTO_WAIT = 1.0  # a page waits this long for a scan it needs, then shows what is there
RESCAN_WAIT = 1.5  # opening a folder / Rescan waits this long before answering
SLOW_FACTOR = 10  # a scan that took t seconds is repeated automatically after at most every SLOW_FACTOR*t

_lock = threading.Lock()
# Result of the last finished scan. "key" = the scanned roots; "version" changes when the
# list of scripts changes (pages scanned in the meantime reload or offer to).
_state = {"key": None, "scripts": {}, "problems": [], "time": 0.0, "duration": 0.0, "version": 0}
_job = None       # the running _Job, if any

# Language of a script, from its file extension.
LANGUAGES = {
    ".py": ("python", "Python"),
    ".sh": ("shell", "Shell"), ".bash": ("shell", "Shell"),
    ".bat": ("batch", "Batch"), ".cmd": ("batch", "Batch"),
    ".ps1": ("powershell", "PowerShell"),
}
PLATFORM_LABELS = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
ALL_PLATFORMS = ["windows", "linux", "macos"]
# Where a script of this type normally runs. A script can override this with a
# "platforms" list in its metadata.
DEFAULT_PLATFORMS = {
    ".sh": ["linux", "macos"], ".bash": ["linux", "macos"],
    ".bat": ["windows"], ".cmd": ["windows"],
}


def language_of(ext: str):
    """(key, label) for a file extension; unknown types use the extension."""
    ext = ext.lower()
    if ext in LANGUAGES:
        return LANGUAGES[ext]
    word = ext.lstrip(".") or "other"
    return word, word.upper()


def default_platforms(ext: str) -> list:
    return list(DEFAULT_PLATFORMS.get(ext.lower(), ALL_PLATFORMS))


@dataclass
class Script:
    id: str
    path: Path
    root: Path
    name: str
    description: str
    category: str
    interpreter: str
    extension: str
    meta_source: Path | None = None
    meta_error: str | None = None
    parameter_count: int = 0
    meta: dict = field(default_factory=dict)
    meta_valid: bool = True
    language: str = ""
    language_label: str = ""
    platforms: list = field(default_factory=list)

    @property
    def platform_labels(self) -> list:
        return [PLATFORM_LABELS.get(p, p) for p in self.platforms]

    @property
    def relative(self) -> str:
        try:
            return self.path.relative_to(self.root).as_posix()
        except ValueError:
            return self.path.name

    @property
    def location(self) -> str:
        return str(self.path.parent)


def script_id(path: Path) -> str:
    key = os.path.normcase(str(path))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _pretty(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title() or stem


def _build(path: Path, root: Path, interpreters: dict) -> Script:
    sid = script_id(path)
    ext = path.suffix.lower()
    data, source, err = metadata.load_raw(path, sid)
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        name = _pretty(path.stem)
    else:
        name = data["name"].strip()
    category = data.get("category")
    if not isinstance(category, str) or not category.strip():
        rel_parent = path.parent.relative_to(root) if path.parent != root else None
        category = _pretty(rel_parent.parts[0]) if rel_parent and rel_parent.parts else "General"
    interp = data.get("interpreter")
    if not isinstance(interp, str) or not interp.strip():
        interp = interpreters.get(ext, "")
    meta_valid = True
    if not err and source is not None:
        try:
            data = validate_metadata(data)
        except ValidationError as exc:
            meta_valid, err = False, "Invalid metadata: " + "; ".join(exc.errors)
    elif err:
        meta_valid = False
    params = data.get("parameters")
    lang_key, lang_label = language_of(ext)
    platforms = data.get("platforms")
    if not (isinstance(platforms, list) and platforms and all(p in PLATFORM_LABELS for p in platforms)):
        platforms = default_platforms(ext)
    return Script(
        id=sid,
        path=path,
        root=root,
        name=name,
        description=data.get("description", "") if isinstance(data.get("description"), str) else "",
        category=category.strip(),
        interpreter=interp,
        extension=ext,
        meta_source=source,
        meta_error=err,
        parameter_count=len(params) if isinstance(params, list) else 0,
        meta=data,
        meta_valid=meta_valid,
        language=lang_key,
        language_label=lang_label,
        platforms=[p for p in ALL_PLATFORMS if p in platforms],
    )


def _skip_dir(name: str) -> bool:
    # Hidden folders, system folders such as $Recycle.Bin, and dependency/cache folders.
    return name.startswith((".", "$")) or name in SKIP_DIRS


class _Job:
    """One scan of the open folder, in a background thread. Progress (folders walked,
    scripts found so far) is readable while it runs; a newer scan cancels it."""

    def __init__(self, key, roots):
        self.key, self.roots = key, roots
        self.scripts: dict[str, Script] = {}
        self.problems: list[str] = []
        self.dirs = 0
        self.current = ""
        self.started = time.monotonic()
        self.cancelled = False
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="scripthub-scan", daemon=True)

    def _run(self):
        try:
            self._walk()
        except Exception as exc:  # never leave the job "running" forever
            self.problems.append(f"Scan failed: {exc}")
        finally:
            global _job
            with _lock:
                if not self.cancelled:
                    now = time.monotonic()
                    changed = _state["key"] != self.key or set(_state["scripts"]) != set(self.scripts)
                    _state.update(key=self.key, scripts=dict(self.scripts), problems=list(self.problems),
                                  time=now, duration=now - self.started,
                                  version=_state["version"] + (1 if changed else 0))
                if _job is self:
                    _job = None
            self.done.set()

    def _walk(self):
        cfg = config.load_config()
        interpreters = {k.lower(): v for k, v in cfg["interpreters"].items()}
        for root in self.roots:
            if not root.is_dir():
                self.problems.append(f"Folder not found: {root}")
                continue
            too_deep: list[str] = []
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                if self.cancelled:
                    return
                self.dirs += 1
                self.current = dirpath
                dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
                if dirnames and len(Path(dirpath).relative_to(root).parts) >= MAX_DEPTH:
                    too_deep.extend(os.path.join(dirpath, d) for d in dirnames)
                    dirnames[:] = []           # do not descend more than MAX_DEPTH levels
                for fn in sorted(filenames):
                    if fn.startswith("."):
                        continue
                    path = Path(dirpath) / fn
                    if path.suffix.lower() not in interpreters:
                        continue
                    try:
                        real = path.resolve()
                        # Reject symlinks that escape the configured root.
                        real.relative_to(root)
                    except (OSError, ValueError):
                        self.problems.append(f"Skipped (outside its directory): {path}")
                        continue
                    if not real.is_file():
                        continue
                    sid = script_id(real)
                    if sid in self.scripts:  # duplicate/overlapping roots
                        continue
                    script = _build(real, root, interpreters)
                    with _lock:
                        self.scripts[sid] = script
            if too_deep:
                more = f" (and {len(too_deep) - 1} more)" if len(too_deep) > 1 else ""
                self.problems.append(f"Not scanned, more than {MAX_DEPTH} folder levels deep: {too_deep[0]}{more}")


def _roots():
    from . import workspace
    roots = [r.resolve() for r in workspace.roots()]
    return tuple(str(r) for r in roots), roots


def _start(key, roots):
    """Start a scan (caller holds _lock); a running one is cancelled."""
    global _job
    if _job is not None:
        _job.cancelled = True
    _job = _Job(key, roots)
    _job.thread.start()
    return _job


def _ordered(scripts: dict) -> list:
    return sorted(scripts.values(), key=lambda s: (s.category.lower(), s.name.lower()))


def _result(key):
    """(scripts, problems) for ``key``: the last finished scan, or while the first scan of
    a newly opened folder runs, what it has found so far."""
    with _lock:
        if _state["key"] == key:
            return _ordered(_state["scripts"]), list(_state["problems"])
        if _job is not None and _job.key == key:
            return _ordered(dict(_job.scripts)), list(_job.problems)
        return [], []


def scan(force: bool = False, wait: float | None = None):
    """Return (scripts, problems).

    Scanning runs in a background thread so a big folder never blocks the app: a page
    waits up to ``wait`` seconds for a scan it needs, then shows the last results (or,
    for a newly opened folder, what has been found so far). Results are reused for
    CACHE_TTL seconds, longer for folders that are slow to scan. ``force`` rescans now
    and waits for the result (tests; use rescan() in views)."""
    key, roots = _roots()
    with _lock:
        job = _job if _job is not None and _job.key == key else None
        started = False
        if force or (job is None and (_state["key"] != key or _stale())):
            job, started = _start(key, roots), True
    if job is not None:
        if force:
            job.done.wait()
        elif started or _state["key"] != key:
            # Only the first moments of a scan are worth waiting for (small folders finish
            # in time); after that pages answer at once with what has been found so far.
            budget = AUTO_WAIT if wait is None else wait
            job.done.wait(max(0.0, budget - (time.monotonic() - job.started)))
    return _result(key)


def _stale() -> bool:
    age = time.monotonic() - _state["time"]
    return age > max(CACHE_TTL, SLOW_FACTOR * _state["duration"])


def rescan(wait: float | None = None):
    """Scan the open folder again (Rescan, after opening a folder or changing settings)
    without blocking for long. Returns (scripts, problems, finished)."""
    key, roots = _roots()
    with _lock:
        job = _job if _job is not None and _job.key == key else _start(key, roots)
    finished = job.done.wait(RESCAN_WAIT if wait is None else wait)
    scripts, problems = _result(key)
    return scripts, problems, finished


def update_file(path) -> None:
    """Refresh one script after it was created, edited or reconfigured, without
    rescanning the whole folder."""
    from .security import is_within
    key, roots = _roots()
    path = Path(path).resolve()
    interpreters = {k.lower(): v for k, v in config.load_config()["interpreters"].items()}
    root = next((r for r in roots if is_within(path, r)), None)
    sid = script_id(path)
    script = None
    if root is not None and path.suffix.lower() in interpreters and path.is_file():
        script = _build(path, root, interpreters)
    with _lock:
        targets = []
        if _state["key"] == key:
            targets.append(_state["scripts"])
        if _job is not None and _job.key == key:
            targets.append(_job.scripts)
        for scripts in targets:
            if script is None:
                scripts.pop(sid, None)
            else:
                if sid not in scripts and scripts is _state["scripts"]:
                    _state["version"] += 1
                scripts[sid] = script


def status() -> dict:
    """Scan progress for the status bar (also served as JSON while a scan runs)."""
    key, _roots_ = _roots()
    with _lock:
        job = _job
        out = {"scanning": job is not None, "version": _state["version"],
               "count": len(_state["scripts"]) if _state["key"] == key else 0}
        if job is not None:
            out.update(folder=", ".join(job.key), dirs=job.dirs, found=len(job.scripts),
                       current=job.current, elapsed=int(time.monotonic() - job.started),
                       first=_state["key"] != job.key)
        return out


def get_script(sid: str, force: bool = False) -> Script | None:
    scripts, _ = scan(force=force)
    for s in scripts:
        if s.id == sid:
            return s
    return None
