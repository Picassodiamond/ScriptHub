"""Ad-hoc scripts: type or paste a script, run it once, optionally save it
to the library. The code that ran is kept inside the run's own history folder."""
import json
import re
import shlex
from pathlib import Path

from django.contrib import messages
from django.shortcuts import redirect, render

import config
from . import discovery, executor, history, metadata, script_editor, ssh_hosts
from .execution_views import RUN_MODES, _ago, client_address, run_mode
from .executor import ExecutionError, ExecutionSpec
from .process_manager import manager
from .script_views import LANGUAGES
from .security import is_within

MAX_CODE = 200 * 1024
MAX_ARG = 2000
MAX_ARGS = 50
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,60}$")

TEMPLATES = {
    ".py": 'import sys\n\nprint("Hello from ScriptHub")\nprint("arguments:", sys.argv[1:])\n',
    ".sh": '#!/usr/bin/env bash\nset -euo pipefail\n\necho "Hello from $(hostname)"\necho "arguments: $*"\n',
    ".bat": "@echo off\r\necho Hello from ScriptHub\r\necho arguments: %*\r\n",
}


def _languages(cfg):
    """[(extension, label)] for every type that can run somewhere."""
    exts = sorted(set(cfg["interpreters"]) | set(cfg["remote_interpreters"]))
    return [(e, discovery.language_of(e)[1]) for e in exts]


def _enabled(request):
    if config.load_config()["enable_adhoc"]:
        return None
    return render(request, "runner/adhoc.html", {
        "blocked": "Ad-hoc scripts are turned off in Settings."}, status=403)


def _recent():
    out = []
    for meta in history.list_runs(limit=80):
        if meta.get("adhoc"):
            out.append({
                "id": meta["id"], "name": meta.get("script_name", "Ad-hoc script"),
                "status": meta.get("status"), "label": history.STATUS_LABELS.get(meta.get("status"), ""),
                "ago": _ago(meta.get("finished_at") or meta.get("started_at") or meta.get("queued_at")),
                "host": meta.get("host", "local"),
            })
        if len(out) >= 6:
            break
    return out


def _title(ext, code):
    label = discovery.language_of(ext)[1]
    for line in code.splitlines():
        line = line.strip().lstrip("#:@").strip()
        if line and not line.startswith(("!", "/usr/bin/env")) and not line.lower().startswith(("echo off", "set -")):
            return f"Ad-hoc {label}: {line[:40]}"
    return f"Ad-hoc {label}"


def _default_form(cfg, ext=".py"):
    return {"language": ext, "code": TEMPLATES.get(ext, ""), "host": "local", "timeout": str(cfg["default_timeout"]),
            "arguments": "", "cwd": "", "trace": False, "mode": "normal"}


def _form_from_run(run_id):
    """Pre-fill the screen from an earlier ad-hoc run ('edit and run again')."""
    meta = history.read_meta(run_id) if history.valid_run_id(run_id) else None
    if not meta or not meta.get("adhoc"):
        return None
    try:
        code = Path(meta["script_path"]).read_bytes()[:MAX_CODE].decode("utf-8", errors="replace")
    except OSError:
        return None
    info = meta["adhoc"]
    mode = "debug" if meta.get("debug") else ("trace" if meta.get("trace") else "normal")
    return {"language": info.get("language", ".py"), "code": code.replace("\r\n", "\n"), "host": meta.get("host", "local"),
            "timeout": str(meta.get("timeout") or ""), "arguments": "\n".join(info.get("arguments") or []),
            "cwd": info.get("cwd") or "", "trace": mode == "trace", "mode": mode}


def validate(form, cfg):
    """Return (spec, errors). The spec is ready for ExecutionManager.submit."""
    errors = []
    mode = form.get("mode") if form.get("mode") in RUN_MODES else ("trace" if form.get("trace") == "1" else "normal")
    ext = (form.get("language") or "").lower()
    host = form.get("host") or "local"
    code = form.get("code", "")
    allowed_langs = {e for e, _l in _languages(cfg)}
    hosts = ssh_hosts.allowed_hosts()

    if ext not in allowed_langs:
        errors.append("Choose a language from the list.")
    if host not in hosts:
        errors.append("Choose one of the listed hosts.")
    if not code.strip():
        errors.append("Enter the script to run.")
    if len(code.encode("utf-8", errors="replace")) > MAX_CODE:
        errors.append("The script is larger than 200 KB.")
    if "\x00" in code:
        errors.append("The script contains a null character.")

    try:
        timeout = int(form.get("timeout") or cfg["default_timeout"])
        if not 1 <= timeout <= 7 * 86400:
            raise ValueError
    except ValueError:
        timeout = cfg["default_timeout"]
        errors.append("Timeout must be a whole number of seconds between 1 and 604800.")

    args = [a.rstrip("\r") for a in (form.get("arguments") or "").split("\n") if a.strip("\r").strip()]
    if len(args) > MAX_ARGS or any(len(a) > MAX_ARG or "\x00" in a for a in args):
        errors.append(f"Use at most {MAX_ARGS} arguments, each under {MAX_ARG} characters.")

    cwd = (form.get("cwd") or "").strip()
    if "\x00" in cwd or "\n" in cwd:
        errors.append("The working directory contains invalid characters.")

    interpreter = None
    if not errors:
        try:
            if host != "local" and ext in executor.BATCH_EXTENSIONS:
                raise ExecutionError("Batch files can only run on this computer (local).")
            interpreter = executor.resolve_interpreter(None, ext, host, cfg)
            if ext in executor.BATCH_EXTENSIONS:
                executor.check_batch_args(args)
            if mode == "debug" and not executor.debug_supported(ext, interpreter):
                raise ExecutionError(executor.DEBUG_UNSUPPORTED)
            if mode == "trace" and not executor.trace_supported(ext, interpreter):
                raise ExecutionError("Line tracing is available for Python and Bash scripts only.")
        except ExecutionError as exc:
            errors.append(str(exc))
    if errors:
        return None, errors

    text = script_editor.normalise_text(code, ext)
    typed_cwd = cwd                      # what the user typed (the default below is not recorded)
    if host == "local" and not cwd:
        cwd = str(Path.home())          # never the run folder: scripts must not litter the logs
    shown_args = shlex.join(args) if args else ""
    spec = ExecutionSpec(
        script_id="adhoc",
        script_name=_title(ext, code),
        script_path="",                  # set by the manager once the code is written
        extension=ext,
        interpreter=interpreter,
        host=host,
        argv=args,
        env=dict(cfg["environment"]),
        cwd=cwd or None,
        timeout=timeout,
        allow_concurrent=True,
        parameters={},
        display_command=f"{'ssh ' + host + ': ' if host != 'local' else ''}{interpreter} <ad-hoc script> {shown_args}".rstrip(),
        inline_source=text.encode("utf-8"),
        adhoc={"language": ext, "arguments": args, "cwd": typed_cwd},
        trace={} if mode in ("trace", "debug") else None,
        debug=mode == "debug",
    )
    return spec, []


def _context(cfg, form, errors=None, **extra):
    ctx = {
        "form": form,
        "errors": errors or [],
        "languages": _languages(cfg),
        "hosts": ssh_hosts.allowed_hosts(),
        "recent": _recent(),
        "libraries": cfg["script_directories"],
        "editor_lang": LANGUAGES.get(form["language"], ""),
        "templates_json": json.dumps({k: v.replace("\r\n", "\n") for k, v in TEMPLATES.items()}),
    }
    ctx.update(extra)
    return ctx


def adhoc(request):
    blocked = _enabled(request)
    if blocked:
        return blocked
    cfg = config.load_config()
    if request.method == "POST":
        form = {k: request.POST.get(k, "") for k in ("language", "code", "host", "timeout", "arguments", "cwd", "trace")}
        form["mode"] = run_mode(request.POST)
        spec, errors = validate(form, cfg)
        if errors:
            return render(request, "runner/adhoc.html", _context(cfg, form, errors), status=400)
        run_id = manager.submit(spec, initiator=client_address(request))
        return redirect("execution_console", run_id=run_id)

    form = _form_from_run(request.GET.get("from", "")) if request.GET.get("from") else None
    if form is None:
        ext = request.GET.get("lang", ".py")
        form = _default_form(cfg, ext if ext in TEMPLATES else ".py")
    return render(request, "runner/adhoc.html", _context(cfg, form))


def adhoc_save(request):
    """Save the typed script into a configured script directory."""
    if request.method != "POST":
        return redirect("adhoc")
    blocked = _enabled(request)
    if blocked:
        return blocked
    cfg = config.load_config()
    form = {k: request.POST.get(k, "") for k in ("language", "code", "host", "timeout", "arguments", "cwd")}
    errors = []

    ext = form["language"].lower()
    if ext not in {e for e, _l in _languages(cfg)}:
        errors.append("Choose a language from the list.")
    if not form["code"].strip():
        errors.append("There is no script to save.")
    if len(form["code"].encode("utf-8", errors="replace")) > MAX_CODE or "\x00" in form["code"]:
        errors.append("The script is too large or contains a null character.")

    configured = request.POST.get("lib_dir", "")
    if configured not in cfg["script_directories"]:
        errors.append("Choose one of the configured script directories.")
    filename = request.POST.get("filename", "").strip()
    if not NAME_RE.match(filename):
        errors.append("The file name may contain letters, digits, dots, hyphens and underscores, and must start with a letter or digit.")
    if filename.lower().endswith(ext):
        filename = filename[: -len(ext)]
    raw_sub = request.POST.get("subfolder", "").strip()
    folder_parts = [p for p in re.split(r"[\\/]+", raw_sub) if p]
    if raw_sub[:1] in ("/", "\\"):
        errors.append("Give the sub-folder relative to the script directory (no leading slash).")
    elif any(not FOLDER_RE.match(p) or p in (".", "..") for p in folder_parts):
        errors.append("The sub-folder may only contain letters, digits, spaces, dots, hyphens and underscores.")

    target = None
    if not errors:
        root = config.resolve_dir(configured)
        folder = root.joinpath(*folder_parts) if folder_parts else root
        target = folder / f"{filename}{ext}"
        if not root.is_dir() or not is_within(folder, root):
            errors.append("That location is outside the script directory.")
        elif target.exists() or metadata.sidecar_path(target).exists():
            errors.append(f"{target.name} already exists there; pick another name. Existing scripts are never overwritten.")
    if errors:
        return render(request, "runner/adhoc.html", _context(cfg, form, errors, save_open=True), status=400)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = script_editor.normalise_text(form["code"], ext).encode("utf-8")
        with open(target, "xb") as fh:           # 'x': fail rather than overwrite
            fh.write(data)
        name = request.POST.get("display_name", "").strip() or filename.replace("_", " ").replace("-", " ").title()
        category = request.POST.get("category", "").strip()
        meta = {"name": name[:120], "description": "Saved from an ad-hoc script.", "parameters": []}
        if category:
            meta["category"] = category[:60]
        metadata.save_raw(target, discovery.script_id(target.resolve()), meta)
    except OSError as exc:
        return render(request, "runner/adhoc.html",
                      _context(cfg, form, [f"Could not save: {exc}"], save_open=True), status=400)

    discovery.scan(force=True)
    messages.success(request, f"Saved to the library as {target.name}. Describe its parameters in Configure.")
    return redirect("script_detail", script_id=discovery.script_id(target.resolve()))
