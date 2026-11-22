"""Create a default script or save a copy into a script folder."""
import re
from pathlib import Path

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

import config
from . import discovery, metadata, script_editor, workspace
from .security import is_within

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,60}$")

STARTERS = {
    ".py": 'import sys\n\nprint("Hello from ScriptHub")\nprint("arguments:", sys.argv[1:])\n',
    ".sh": '#!/usr/bin/env bash\nset -euo pipefail\n\necho "Hello from $(hostname)"\necho "arguments: $*"\n',
    ".bat": "@echo off\r\necho Hello from ScriptHub\r\necho arguments: %*\r\n",
}


def languages(cfg):
    """[(extension, label)] for every script type that can run somewhere."""
    exts = sorted(set(cfg["interpreters"]) | set(cfg["remote_interpreters"]))
    return [(e, discovery.language_of(e)[1]) for e in exts]


def _suggestions(scripts):
    """Existing sub-folders and categories of the open folder (for the datalists)."""
    folders = sorted({Path(s.relative).parent.as_posix() for s in scripts} - {"."}, key=str.lower)
    categories = sorted({s.category for s in scripts if s.category}, key=str.lower)
    return folders, categories


def _target(root: Path, ext: str, filename: str, subfolder: str):
    ext = ext.lower()
    filename = filename.strip()
    if filename.lower().endswith(ext):
        filename = filename[: -len(ext)]
    if not NAME_RE.match(filename):
        raise ValueError("The file name may contain letters, digits, dots, hyphens and underscores, "
                         "and must start with a letter or digit.")
    raw_sub = subfolder.strip()
    parts = [p for p in re.split(r"[\\/]+", raw_sub) if p]
    if raw_sub[:1] in ("/", "\\") or re.match(r"^[A-Za-z]:", raw_sub):
        raise ValueError("Give the sub-folder relative to the script folder (no leading slash or drive).")
    if any(not FOLDER_RE.match(p) or p in (".", "..") for p in parts):
        raise ValueError("The sub-folder may only contain letters, digits, spaces, dots, hyphens and underscores.")
    if len(parts) > discovery.MAX_DEPTH:
        raise ValueError(f"The sub-folder may be at most {discovery.MAX_DEPTH} levels deep "
                         "(deeper folders are not scanned for scripts).")
    root = Path(root).resolve()
    folder = root.joinpath(*parts)
    target = folder / f"{filename}{ext}"
    if not root.is_dir() or not is_within(folder, root):
        raise ValueError("That location is outside the script folder.")
    if target.exists():
        raise ValueError(f"{target.name} already exists there; pick another name. Existing files are never overwritten.")
    return ext, target


def _available_filename(root: Path, ext: str, subfolder: str, stem: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", subfolder.strip()) if p]
    parent = Path(root).joinpath(*parts)
    index = 1
    while True:
        filename = stem if index == 1 else f"{stem}_{index}"
        if not (parent / f"{filename}{ext}").exists():
            return filename
        index += 1


def _render(request, cfg, form, errors=None, status=200):
    scripts, _ = discovery.scan()
    folders, categories = _suggestions(scripts)
    return render(request, "runner/new_script.html", {
        "form": form, "errors": errors or [], "languages": languages(cfg),
        "folder": workspace.current(), "sub_folders": folders, "categories": categories,
        "max_depth": discovery.MAX_DEPTH,
    }, status=status)


def create(root: Path, ext: str, filename: str, subfolder: str, name: str = "", category: str = "") -> Path:
    """Write a new script and its metadata under ``root``. Raises ValueError (message for
    the user). Never overwrites: the file is opened with "xb"."""
    ext, target = _target(root, ext, filename, subfolder)
    display_name = filename.strip()
    if display_name.lower().endswith(ext):
        display_name = display_name[: -len(ext)]
    meta = {"name": (name.strip() or display_name.replace("_", " ").replace("-", " ").title())[:120],
            "description": "", "parameters": []}
    if category.strip():
        meta["category"] = category.strip()[:60]
    meta = metadata.with_id(meta)                    # a new script gets its stable id right away
    newline = "\r\n" if ext in script_editor.CRLF_EXTENSIONS else "\n"
    text = metadata.with_block(script_editor.normalise_text(STARTERS.get(ext, ""), ext), meta, target, newline)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as fh:              # one write: starter + metadata block, never overwrites
            fh.write(text.encode("utf-8"))
    except FileExistsError:
        raise ValueError(f"{target.name} already exists there; pick another name.") from None
    except OSError as exc:
        raise ValueError(f"Could not create the script: {exc}") from None
    return target


def copy_as(script, filename: str, subfolder: str) -> Path:
    """Create an independent file copy with a fresh stable id; never alter the source."""
    root = Path(script.root).resolve()
    if not is_within(script.path, root):
        raise ValueError("The script is outside its configured script folder.")
    ext, target = _target(root, script.extension, filename, subfolder)
    try:
        source = script_editor.read_for_edit(script.path, root)
    except script_editor.EditError as exc:
        raise ValueError(str(exc)) from None
    data, _source_path, error = metadata.load_raw(script.path, script.id)
    if error:
        raise ValueError(f"Fix the script's metadata before saving a copy: {error}")
    if not data:
        data = {"name": script.name, "description": script.description,
                "parameters": [], "category": script.category}
    data = metadata.with_id(data, conflict=True)
    newline = "\r\n" if ext in script_editor.CRLF_EXTENSIONS or source["newline"] == "crlf" else "\n"
    text = metadata.with_block(source["text"], data, target, newline)
    payload = (script_editor.BOM if source["bom"] else b"") + text.encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as fh:
            fh.write(payload)
    except FileExistsError:
        raise ValueError(f"{target.name} already exists there; pick another name. Existing files are never overwritten.") from None
    except OSError as exc:
        raise ValueError(f"Could not save the copy: {exc}") from None
    return target


def _open_created(request, target: Path):
    discovery.update_file(target)
    created = discovery.get_script(discovery.script_id(target.resolve()))
    sid = created.id if created else discovery.script_id(target.resolve())
    messages.success(request, f"Created {target.name}. Write it here (Ctrl+S saves); add parameters on the Parameters tab.")
    return redirect("script_run", script_id=sid)


def _default_script(root: Path, cfg: dict, base=None) -> Path:
    exts = [ext for ext, _label in languages(cfg)]
    if not exts:
        raise ValueError("Configure at least one script interpreter before creating a script.")
    ext = base.extension if base and base.extension in exts else ".py" if ".py" in exts else exts[0]
    subfolder = ""
    category = "General"
    if base:
        category = base.category or category
        if Path(base.root).resolve() == Path(root).resolve():
            parent = Path(base.relative).parent.as_posix()
            subfolder = "" if parent == "." else parent
    filename = _available_filename(root, ext, subfolder, "new_script")
    return create(root, ext, filename, subfolder, "New Script", category)


def script_new(request):
    cfg = config.load_config()
    root = workspace.current()
    if root is None or not root.is_dir():
        messages.info(request, "Only script files are open; open a folder to create new scripts in it."
                      if workspace.current_is_file() else "Open a script folder first; new scripts are saved in it.")
        return redirect("folder")

    if request.method == "GET" or request.POST.get("quick") == "1":
        from_id = request.POST.get("from", "") if request.method == "POST" else request.GET.get("from", "")
        try:
            target = _default_script(root, cfg, discovery.get_script(from_id))
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("library")
        return _open_created(request, target)

    form = {k: request.POST.get(k, "") for k in ("language", "filename", "subfolder", "name", "category")}
    if form["language"].lower() not in {e for e, _l in languages(cfg)}:
        return _render(request, cfg, form, ["Choose a language from the list."], status=400)
    try:
        target = create(root, form["language"], form["filename"], form["subfolder"], form["name"], form["category"])
    except ValueError as exc:
        return _render(request, cfg, form, [str(exc)], status=400)
    return _open_created(request, target)


def _save_as_page(request, script, form, errors=None, status=200):
    scripts, _ = discovery.scan()
    related = [s for s in scripts if Path(s.root).resolve() == Path(script.root).resolve()]
    folders, _categories = _suggestions(related)
    return render(request, "runner/script_save_as.html", {
        "script": script, "form": form, "errors": errors or [],
        "sub_folders": folders, "max_depth": discovery.MAX_DEPTH,
    }, status=status)


def script_save_as(request, script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404
    parent = Path(script.relative).parent.as_posix()
    subfolder = "" if parent == "." else parent
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", script.path.stem).strip("._-") or "script"
    default_name = _available_filename(script.root, script.extension, subfolder, f"{stem[:76]}_copy")
    form = {"filename": default_name, "subfolder": subfolder}
    if request.method != "POST":
        return _save_as_page(request, script, form)
    form = {key: request.POST.get(key, "") for key in ("filename", "subfolder")}
    try:
        target = copy_as(script, form["filename"], form["subfolder"])
    except ValueError as exc:
        return _save_as_page(request, script, form, [str(exc)], status=400)
    return _open_created(request, target)
