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

_lock = threading.Lock()
_cache = {"time": 0.0, "scripts": [], "problems": []}

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
    enabled: bool
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
        enabled=data.get("enabled", True) is not False,
        meta_source=source,
        meta_error=err,
        parameter_count=len(params) if isinstance(params, list) else 0,
        meta=data,
        meta_valid=meta_valid,
        language=lang_key,
        language_label=lang_label,
        platforms=[p for p in ALL_PLATFORMS if p in platforms],
    )


def _scan_now():
    cfg = config.load_config()
    interpreters = {k.lower(): v for k, v in cfg["interpreters"].items()}
    scripts: dict[str, Script] = {}
    problems: list[str] = []
    from . import workspace
    for root in workspace.roots():
        root = root.resolve()
        if not root.is_dir():
            problems.append(f"Folder not found: {root}")
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
            )
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
                    problems.append(f"Skipped (outside its directory): {path}")
                    continue
                if not real.is_file():
                    continue
                sid = script_id(real)
                if sid in scripts:  # duplicate/overlapping roots
                    continue
                scripts[sid] = _build(real, root, interpreters)
    ordered = sorted(scripts.values(), key=lambda s: (s.category.lower(), s.name.lower()))
    return ordered, problems


def scan(force: bool = False):
    """Return (scripts, problems). Cached briefly; ``force`` rescans now."""
    with _lock:
        if force or time.monotonic() - _cache["time"] > CACHE_TTL:
            scripts, problems = _scan_now()
            _cache.update(time=time.monotonic(), scripts=scripts, problems=problems)
        return list(_cache["scripts"]), list(_cache["problems"])


def get_script(sid: str, force: bool = False) -> Script | None:
    scripts, _ = scan(force=force)
    for s in scripts:
        if s.id == sid:
            return s
    return None
