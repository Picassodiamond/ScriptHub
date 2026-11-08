"""New script: create a file (from a short starter, with its metadata block at the top) in
the open script folder, then open it on its Run tab where the editor is."""
import re
from pathlib import Path

from django.contrib import messages
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


def _blocked(request, reason):
    return render(request, "runner/new_script.html", {"blocked": reason}, status=403)


def _suggestions(scripts):
    """Existing sub-folders and categories of the open folder (for the datalists)."""
    folders = sorted({Path(s.relative).parent.as_posix() for s in scripts} - {"."}, key=str.lower)
    categories = sorted({s.category for s in scripts if s.category}, key=str.lower)
    return folders, categories


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
    root = root.resolve()
    folder = root.joinpath(*parts) if parts else root
    target = folder / f"{filename}{ext}"
    if not root.is_dir() or not is_within(folder, root):
        raise ValueError("That location is outside the script folder.")
    if target.exists():
        raise ValueError(f"{target.name} already exists there; pick another name. Existing files are never overwritten.")
    meta = {"name": (name.strip() or filename.replace("_", " ").replace("-", " ").title())[:120],
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


def script_new(request):
    cfg = config.load_config()
    if not cfg["enable_script_editing"]:
        return _blocked(request, "Creating and editing scripts from the browser is turned off in Settings.")
    root = workspace.current()
    if root is None or not root.is_dir():
        messages.info(request, "A single script is open; open a folder to create new scripts in it." if root and root.is_file()
                      else "Open a script folder first; new scripts are saved in it.")
        return redirect("folder")

    if request.method != "POST":
        form = {"language": ".py", "filename": "", "subfolder": "", "name": "", "category": ""}
        base = discovery.get_script(request.GET.get("from", ""))       # "+" next to a script: same place
        if base is not None:
            sub = Path(base.relative).parent.as_posix()
            form.update(language=base.extension, subfolder="" if sub == "." else sub,
                        category=base.meta.get("category", "") if base.meta_valid else "")
        return _render(request, cfg, form)

    form = {k: request.POST.get(k, "") for k in ("language", "filename", "subfolder", "name", "category")}
    if form["language"].lower() not in {e for e, _l in languages(cfg)}:
        return _render(request, cfg, form, ["Choose a language from the list."], status=400)
    try:
        target = create(root, form["language"], form["filename"], form["subfolder"], form["name"], form["category"])
    except ValueError as exc:
        return _render(request, cfg, form, [str(exc)], status=400)
    discovery.update_file(target)
    created = discovery.get_script(discovery.script_id(target.resolve()))
    sid = created.id if created else discovery.script_id(target.resolve())
    messages.success(request, f"Created {target.name}. Write it here (Ctrl+S saves); add parameters on the Parameters tab.")
    return redirect("script_run", script_id=sid)
