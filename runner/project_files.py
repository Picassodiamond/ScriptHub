"""ScriptHub project files (.scripthub): a folder's scripts and files, its sequences and presets in one zip.

A project file is a zip archive with its own extension::

    manifest.json              format, version, name, date, ScriptHub version; every file with its
                               size and SHA-256; the sequences; the presets (which script each belongs to)
    files/<path>               the open folder's files (scripts with their metadata, helpers, data, docs)
    sequences/<slug>.yaml      the sequences; a step's ``path`` is ``files/<path>`` of its script
    presets/<n>/<slug>.yaml    saved presets (named values) of the scripts; recent values are not taken

Never in it: passwords (never stored anyway), run history and logs, Settings, SSH keys, files that look like
secrets (.env, keys, keystores) and tool folders (.git, .venv, node_modules, __pycache__).

Saving (``build``) walks the open folder as discovery does (``discovery.MAX_DEPTH`` levels). Opening is two
steps: ``inspect`` checks the archive and returns a preview (nothing written), ``install`` unpacks it into an
empty destination folder, adds the sequences (a taken name becomes "Name (2)") and the presets (an existing
preset is kept), and points every step at its unpacked script. Nothing runs. The archive is untrusted: names
that leave the archive (absolute, drive, ``..``, links), oversized or over-compressed members and a manifest
that does not match the contents are refused.
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import config
from . import discovery, presets, sequences, ssh_hosts, yamlish

EXT = ".scripthub"
SAMPLES_DIR = config.BASE_DIR / "sample_projects"
FORMAT = "scripthub-project"
VERSION = 1
MAX_FILES = 5000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_RATIO = 200                      # uncompressed / compressed, for members above 1 MB
UPLOAD_TTL = 24 * 3600
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".venv", "env", ".git", ".svn", ".hg", ".idea", ".vscode"}
SECRET_RE = re.compile(r"(^\.env($|\.)|\.(pem|key|pfx|p12|jks|keystore|kdbx|ppk)$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"
                       r"|^secrets?\.(txt|json|ya?ml|ini)$)", re.I)
SKIP_FILE_RE = re.compile(r"\.(pyc|pyo)$", re.I)
TOP = ("manifest.json", "files/", "sequences/", "presets/")


class ProjectError(Exception):
    pass


def imports_dir() -> Path:
    return config.RUNTIME_DIR / "imports"


def slug_name(name: str) -> str:
    """A file-name-safe version of the project name."""
    s = re.sub(r"[^A-Za-z0-9._ -]+", "", name or "").strip(" .")
    return re.sub(r"\s+", " ", s)[:60] or "project"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


# ------------------------------------------------------------------- save ---
def folder_files(root: Path) -> tuple:
    """(included, left_out) for the folder: included = [(relative posix path, size, is_script)],
    left_out = [(relative path, reason)]."""
    exts = {e.lower() for e in config.load_config()["interpreters"]}
    included, left_out = [], []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        keep = []
        for d in sorted(dirnames):
            if d.startswith((".", "$")) or d in SKIP_DIRS:
                left_out.append(((rel_dir / d).as_posix() + "/", "tool or hidden folder"))
            elif len(rel_dir.parts) >= discovery.MAX_DEPTH:
                left_out.append(((rel_dir / d).as_posix() + "/", f"more than {discovery.MAX_DEPTH} folder levels deep"))
            elif (Path(dirpath) / d).is_symlink():
                left_out.append(((rel_dir / d).as_posix() + "/", "a link"))
            else:
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            p = Path(dirpath) / f
            rel = (rel_dir / f).as_posix()
            if SECRET_RE.search(f):
                left_out.append((rel, "may hold secrets"))
            elif f.startswith(".") or SKIP_FILE_RE.search(f):
                left_out.append((rel, "hidden or compiled file"))
            elif p.is_symlink():
                left_out.append((rel, "a link"))
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    left_out.append((rel, "cannot be read"))
                    continue
                if size > MAX_FILE_BYTES:
                    left_out.append((rel, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"))
                else:
                    included.append((rel, size, p.suffix.lower() in exts))
    return included, left_out


def sequences_for(root: Path) -> list:
    """Every readable sequence, with ``uses_folder``: a step's script is in ``root`` (those are offered
    ticked when saving)."""
    out = []
    for entry in sequences.list_sequences():
        if entry["error"]:
            continue
        data = sequences.get(entry["slug"])
        if data is None:
            continue
        uses = False
        for step in data["steps"]:
            script = sequences.resolve(step)
            if script is not None and _inside(script.path, root):
                uses = True
                break
        out.append({"slug": entry["slug"], "name": data["name"], "steps": len(data["steps"]), "uses_folder": uses})
    return out


def _unique(name: str, taken: set) -> str:
    """``name``, or name_2.ext, name_3.ext ... when taken (case-insensitive); records the result."""
    stem, suffix = Path(name).stem, Path(name).suffix
    out, n = name, 2
    while out.lower() in taken:
        out = f"{stem[:50]}_{n}{suffix}"
        n += 1
    taken.add(out.lower())
    return out


def build(root: Path, name: str, files: list, seq_slugs: list, with_presets: bool = True) -> tuple:
    """Write the project file to a temporary file. ``files`` = relative paths chosen from folder_files().
    Returns (open binary file positioned at 0, report dict). Raises ProjectError."""
    root = Path(root)
    included = {rel: (size, is_script) for rel, size, is_script in folder_files(root)[0]}
    chosen = [f for f in files if f in included]
    report = {"files": 0, "scripts": 0, "sequences": 0, "presets": 0, "missing": [], "outside": []}

    # scripts the chosen sequences use: from the folder (always included) or from elsewhere (files/_outside/)
    seqs, extra = [], {}                      # extra: archive path -> real path of a script outside the folder
    taken = {f.lower() for f in chosen}
    by_real = {}
    for slug in seq_slugs:
        data = sequences.get(slug)
        if data is None:
            continue
        for step in data["steps"]:
            script = sequences.resolve(step)
            if script is None:
                report["missing"].append(f"{data['name']}: {step.get('name') or step['script']}")
                continue
            real = Path(script.path).resolve()
            if _inside(real, root):
                rel = real.relative_to(root.resolve()).as_posix()
                if rel not in chosen and rel in included:
                    chosen.append(rel)
                    taken.add(rel.lower())
                step["path"] = "files/" + rel
            else:
                key = str(real).lower()
                if key not in by_real:
                    rel = "_outside/" + _unique(real.name, taken)
                    by_real[key] = rel
                    extra[rel] = real
                    report["outside"].append(str(real))
                    sidecar = real.with_suffix(".json")       # an old metadata sidecar travels with it
                    if sidecar.is_file():
                        srel = rel.rsplit(".", 1)[0] + ".json"
                        extra[srel] = sidecar
                step["path"] = "files/" + by_real[key]
            step["script"] = script.id
        seqs.append(data)

    if len(chosen) + len(extra) > MAX_FILES:
        raise ProjectError(f"Too many files (at most {MAX_FILES}).")
    total = sum(included[f][0] for f in chosen) + sum(p.stat().st_size for p in extra.values())
    if total > MAX_TOTAL_BYTES:
        raise ProjectError(f"The files are larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB together.")

    manifest = {"format": FORMAT, "version": VERSION, "name": name, "created_at": _now(),
                "scripthub": _app_version(), "folder": root.name, "files": [], "sequences": [], "presets": []}
    out = tempfile.TemporaryFile()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        sources = [(rel, root / rel) for rel in sorted(chosen)] + sorted(extra.items())
        script_exts = {e.lower() for e in config.load_config()["interpreters"]}
        for rel, real in sources:
            data = real.read_bytes()
            z.writestr("files/" + rel, data)
            is_script = Path(rel).suffix.lower() in script_exts
            manifest["files"].append({"path": rel, "size": len(data), "sha256": _sha(data), "script": is_script})
            report["files"] += 1
            report["scripts"] += int(is_script)
        used = set()
        for data in seqs:
            slug = data["slug"]
            body = sequences.dump_text(data)
            z.writestr(f"sequences/{slug}.yaml", body)
            manifest["sequences"].append({"file": f"sequences/{slug}.yaml", "name": data["name"],
                                          "steps": len(data["steps"]), "sha256": _sha(body.encode("utf-8"))})
            report["sequences"] += 1
        if with_presets:
            n = 0
            for rel, real in sources:
                if Path(rel).suffix.lower() not in script_exts:
                    continue
                script = discovery.load_file(str(real))
                if script is None or script.id in used:
                    continue
                used.add(script.id)
                pfolder = presets.folder(script)
                if not pfolder.is_dir():
                    continue
                for pf in sorted(pfolder.glob("*.yaml")):
                    body = pf.read_bytes()
                    n += 1
                    arc = f"presets/{n}/{pf.name}"
                    z.writestr(arc, body)
                    manifest["presets"].append({"file": arc, "script": rel, "sha256": _sha(body)})
                    report["presets"] += 1
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    out.seek(0)
    return out, report


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _app_version() -> str:
    from .context import APP_VERSION
    return APP_VERSION


# ------------------------------------------------------------------- open ---
def _check_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        raise ProjectError(f"Unsafe name in the project file: {name!r}")
    p = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in p.parts) or ":" in p.parts[0]:
        raise ProjectError(f"Unsafe name in the project file: {name!r}")
    if not (name == "manifest.json" or name.startswith(TOP[1:])):
        raise ProjectError(f"Unexpected entry in the project file: {name!r}")
    return p


def _open_zip(path: Path) -> tuple:
    """(ZipFile, manifest, {name: ZipInfo}) after every structural check. Raises ProjectError."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ProjectError(f"Not a ScriptHub project file ({exc}).") from None
    infos, seen = {}, set()
    total = 0
    for info in z.infolist():
        if info.is_dir():
            continue
        _check_name(info.filename)
        if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
            raise ProjectError(f"The project file holds a link: {info.filename}")
        key = info.filename.lower()
        if key in seen:
            raise ProjectError(f"A name is in the project file twice: {info.filename}")
        seen.add(key)
        if info.file_size > MAX_FILE_BYTES:
            raise ProjectError(f"A file is too large: {info.filename}")
        if info.file_size > 1024 * 1024 and info.file_size > MAX_RATIO * max(1, info.compress_size):
            raise ProjectError(f"A file is compressed suspiciously well: {info.filename}")
        total += info.file_size
        infos[info.filename] = info
    if len(infos) > MAX_FILES + 2000:
        raise ProjectError("The project file holds too many files.")
    if total > MAX_TOTAL_BYTES:
        raise ProjectError("The project file is too large when unpacked.")
    if "manifest.json" not in infos:
        raise ProjectError("Not a ScriptHub project file (no manifest.json).")
    try:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProjectError("The project's manifest.json cannot be read.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ProjectError("Not a ScriptHub project file (unknown format).")
    if not isinstance(manifest.get("version"), int) or manifest["version"] > VERSION:
        raise ProjectError("This project file was made by a newer ScriptHub: update ScriptHub to open it.")
    for key in ("files", "sequences", "presets"):
        if not isinstance(manifest.get(key), list):
            raise ProjectError(f"The project's manifest has no '{key}' list.")
    return z, manifest, infos


def _read_checked(z, infos, name, sha) -> bytes:
    if name not in infos:
        raise ProjectError(f"The project file lacks {name}.")
    with z.open(infos[name]) as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ProjectError(f"A file is too large: {name}")
    if sha and _sha(data) != sha:
        raise ProjectError(f"{name} does not match the manifest: the project file is damaged or was changed.")
    return data


def _entries(manifest) -> tuple:
    """Validated (files, sequences, presets) lists of the manifest."""
    files = []
    for f in manifest["files"]:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str):
            raise ProjectError("The project's manifest lists a file without a path.")
        _check_name("files/" + f["path"])
        files.append(f)
    seqs = [s for s in manifest["sequences"] if isinstance(s, dict) and isinstance(s.get("file"), str)]
    pres = [p for p in manifest["presets"] if isinstance(p, dict) and isinstance(p.get("file"), str)]
    for item in seqs + pres:
        _check_name(item["file"])
    return files, seqs, pres


_ABS_RE = re.compile(r"^([A-Za-z]:[\\/]|\\\\|/[A-Za-z0-9_.-]+/)")


def inspect(path: Path) -> dict:
    """Check a project file and describe what opening it would do. Writes nothing."""
    z, manifest, infos = _open_zip(path)
    with z:
        files, seqs, pres = _entries(manifest)
        for f in files:
            _read_checked(z, infos, "files/" + f["path"], f.get("sha256"))
        taken = {s["name"].lower() for s in sequences.list_sequences()}
        hosts = set(ssh_hosts.allowed_hosts())
        seq_rows, warnings = [], []
        for s in seqs:
            body = _read_checked(z, infos, s["file"], s.get("sha256")).decode("utf-8")
            try:
                data = sequences.parse_text(body)
            except sequences.SequenceError as exc:
                raise ProjectError(f"{s['file']} cannot be read: {exc}") from None
            name = _free_name(data["name"], taken)
            seq_rows.append({"name": data["name"], "as": name, "steps": len(data["steps"])})
            for n, step in enumerate(data["steps"], start=1):
                label = f"{data['name']}, step {n} ({step.get('name') or step['script']})"
                if not str(step.get("path", "")).startswith("files/"):
                    warnings.append(f"{label}: its script is not in the project; it needs to be on this computer.")
                if step.get("host") and step["host"] not in hosts:
                    warnings.append(f"{label}: host '{step['host']}' is not in this ScriptHub's settings.")
                for key, value in (step.get("values") or {}).items():
                    if isinstance(value, str) and _ABS_RE.match(value) and not Path(value).exists():
                        warnings.append(f"{label}: {key} = {value} does not exist on this computer.")
        for p in pres:
            _read_checked(z, infos, p["file"], p.get("sha256"))
    return {
        "name": str(manifest.get("name") or Path(path).stem),
        "created_at": str(manifest.get("created_at") or "").replace("T", " "),
        "scripthub": str(manifest.get("scripthub") or ""),
        "folder": str(manifest.get("folder") or ""),
        "files": len(files),
        "scripts": sum(1 for f in files if f.get("script")),
        "file_list": [f["path"] for f in files],
        "sequences": seq_rows,
        "presets": len(pres),
        "warnings": warnings,
    }


def _free_name(name: str, taken: set) -> str:
    out, n = name, 2
    while out.lower() in taken:
        suffix = f" ({n})"
        out = name[: sequences.NAME_MAX - len(suffix)] + suffix
        n += 1
    taken.add(out.lower())
    return out


def check_destination(value: str) -> Path:
    """The folder to unpack into: absolute, and missing or empty. Raises ProjectError."""
    from .security import clean_directory_input
    try:
        dest = Path(clean_directory_input(value or ""))
    except ValueError as exc:
        raise ProjectError(str(exc)) from None
    if not dest.is_absolute():
        raise ProjectError("Choose a full folder path, e.g. C:\\ScriptHub\\My project.")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ProjectError(f"{dest} is not empty: choose a new or empty folder (nothing is overwritten).")
    if _inside(dest, config.RUNTIME_DIR):
        raise ProjectError("Choose a folder outside ScriptHub's runtime folder.")
    return dest


def install(path: Path, destination: str) -> dict:
    """Unpack the project into ``destination`` and add its sequences and presets.
    Returns {"folder", "files", "sequences": [slug], "presets", "kept": [..], "warnings": [..]}."""
    dest = check_destination(destination)
    preview = inspect(path)                    # every check again, right before writing
    z, manifest, infos = _open_zip(path)
    result = {"folder": str(dest), "files": 0, "sequences": [], "presets": 0, "kept": [],
              "warnings": preview["warnings"]}
    with z:
        files, seqs, pres = _entries(manifest)
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = dest.joinpath(*PurePosixPath(f["path"]).parts)
            if not _inside(target, dest):
                raise ProjectError(f"Unsafe name in the project file: {f['path']!r}")
            data = _read_checked(z, infos, "files/" + f["path"], f.get("sha256"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as fh:           # never overwrites
                fh.write(data)
            result["files"] += 1

        def script_at(rel):
            return discovery.load_file(str(dest.joinpath(*PurePosixPath(rel).parts)))

        taken = {s["name"].lower() for s in sequences.list_sequences()}
        for s in seqs:
            data = sequences.parse_text(_read_checked(z, infos, s["file"], s.get("sha256")).decode("utf-8"))
            for step in data["steps"]:
                p = str(step.get("path", ""))
                if p.startswith("files/"):
                    script = script_at(p[len("files/"):])
                    if script is not None:
                        step.update(script=script.id, path=str(script.path), name=script.name)
                    else:
                        step["path"] = str(dest.joinpath(*PurePosixPath(p[len("files/"):]).parts))
            data["name"] = _free_name(data["name"], taken)
            result["sequences"].append(sequences.add_imported(data))

        for p in pres:
            script = script_at(str(p.get("script", "")))
            if script is None:
                continue
            body = _read_checked(z, infos, p["file"], p.get("sha256"))
            try:
                yamlish.load(body.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                result["kept"].append(f"{p['file']}: not readable, skipped")
                continue
            target = presets.folder(script) / PurePosixPath(p["file"]).name
            if target.exists():
                result["kept"].append(f"{script.name}: preset {target.stem} exists already, yours is kept")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            result["presets"] += 1
    return result


# ----------------------------------------------------------------- uploads ---
def _imports() -> Path:
    """The uploads folder, without uploads older than UPLOAD_TTL."""
    folder = imports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob(f"*{EXT}"):
        try:
            if now - old.stat().st_mtime > UPLOAD_TTL:
                old.unlink()
        except OSError:
            pass
    return folder


def save_upload(fileobj) -> str:
    """Keep an uploaded project file for its preview and install; returns a token."""
    folder = _imports()
    token = secrets.token_hex(12)
    size = 0
    with open(folder / f"{token}{EXT}", "wb") as out:
        for chunk in fileobj.chunks():
            size += len(chunk)
            if size > MAX_TOTAL_BYTES:
                out.close()
                (folder / f"{token}{EXT}").unlink(missing_ok=True)
                raise ProjectError("The project file is too large.")
            out.write(chunk)
    return token


def sample_projects() -> list:
    """The project files that come with ScriptHub (sample_projects/): [{"name"}]."""
    return [{"name": p.stem} for p in sorted(SAMPLES_DIR.glob(f"*{EXT}")) if p.is_file()]


def import_sample(name: str) -> str:
    """Treat a sample project like an upload (preview, then install); returns a token. Only names of
    files in sample_projects/ are accepted, never a path."""
    match = next((p for p in SAMPLES_DIR.glob(f"*{EXT}") if p.is_file() and p.stem == name), None)
    if match is None:
        raise ProjectError("That sample project does not exist.")
    token = secrets.token_hex(12)
    shutil.copyfile(match, _imports() / f"{token}{EXT}")
    return token


def upload_path(token: str) -> Path:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{24}", token):
        raise ProjectError("That upload is no longer available: choose the project file again.")
    p = imports_dir() / f"{token}{EXT}"
    if not p.is_file():
        raise ProjectError("That upload is no longer available: choose the project file again.")
    return p


def forget_upload(token: str) -> None:
    try:
        upload_path(token).unlink()
    except (ProjectError, OSError):
        pass


def default_destination(name: str) -> str:
    """A new folder named after the project, next to the open folder; in ~/ScriptHub projects when nothing is
    open or the open folder is part of ScriptHub's own code (e.g. sample_scripts)."""
    from . import workspace
    base = workspace.current()
    parent = base.parent if base else None
    if parent is None or _inside(parent, config.BASE_DIR):
        parent = Path.home() / "ScriptHub projects"
    candidate = parent / slug_name(name)
    n = 2
    while candidate.exists():
        candidate = parent / f"{slug_name(name)} ({n})"
        n += 1
    return str(candidate)


