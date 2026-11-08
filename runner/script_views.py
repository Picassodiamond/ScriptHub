"""Views for a single script: details, configuration (metadata + parameter
editor) and the dynamic run form."""
import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import discovery, history, metadata, presets, ssh_hosts
from . import parameter_service as ps
from .security import is_within
from .validators import ValidationError


def _get_script_or_404(script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404("Unknown script")
    return script


def _params_of(script):
    return script.meta.get("parameters", []) if script.meta_valid else []


# ---------------------------------------------------------------- details ---
def script_detail(request, script_id):
    script = _get_script_or_404(script_id)
    rows = []
    for p in _params_of(script):
        d = ps.default_raw(p)
        if p["type"] == "password":
            d = ""
        elif isinstance(d, list):
            d = ", ".join(d)
        elif isinstance(d, bool):
            d = "Yes" if d else "No"
        mode = p.get("pass_as", "flag")
        if mode == "flag":
            passed = p.get("arg") or "--" + p["name"].replace("_", "-")
        elif mode == "env":
            passed = "env " + (p.get("env_name") or p["name"].upper())
        else:
            passed = "positional" + (f" {p['position']}" if p.get("position") else "")
        rows.append({"p": p, "default": d, "passed": passed})
    from .execution_views import _decorate
    runs = [_decorate(m) for m in history.list_runs(limit=8, script_id=script.id)]
    return render(request, "runner/script_detail.html", {"script": script, "rows": rows, "runs": runs,
                                                         "presets": presets.list_presets(script),
                                                         "recent": presets.recent_runs(script)[:5],
                                                         **doc_context(script)})


# -------------------------------------------------------------- configure ---
def _type_info():
    return {k: {"label": t.label, "allowed": sorted(t.allowed)} for k, t in ps.TYPES.items()}


def _env_to_text(env):
    return "\n".join(f"{k}={v}" for k, v in env.items()) if isinstance(env, dict) else ""


def _text_to_env(text):
    """'KEY=VALUE' lines -> (dict, errors)."""
    env, errors = {}, []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            errors.append(f"Environment line {n} must look like NAME=value.")
        else:
            env[key.strip()] = value
    return env, errors


def _info_form(data, script):
    timeout = data.get("timeout")
    plats = data.get("platforms")
    if not (isinstance(plats, list) and plats):
        plats = discovery.default_platforms(script.extension)
    return {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "category": data.get("category", ""),
        "interpreter": data.get("interpreter", ""),
        "timeout": "" if timeout is None else timeout,
        "allow_concurrent": data.get("allow_concurrent", False),
        "enabled": data.get("enabled", True),
        "platforms": plats,
        "host": data.get("host", "local"),
        "working_directory": data.get("working_directory", ""),
        "environment": _env_to_text(data.get("environment")),
    }


def _raw_text(script, data):
    src = script.meta_source
    if src is not None and src.is_file():
        try:
            return src.read_text(encoding="utf-8-sig")
        except OSError:
            pass
    return json.dumps(data or {"parameters": []}, indent=2, ensure_ascii=False)


def _configure_response(request, script, *, form, params, raw_text, errors=None, raw_errors=None, status=200):
    return render(
        request,
        "runner/configure.html",
        {
            "script": script,
            "form": form,
            "params": params,
            "type_info": _type_info(),
            "raw_text": raw_text,
            "errors": errors or [],
            "raw_errors": raw_errors or [],
            "platform_choices": list(discovery.PLATFORM_LABELS.items()),
            "default_platform_labels": ", ".join(
                discovery.PLATFORM_LABELS[p] for p in discovery.default_platforms(script.extension)),
            "hosts": ssh_hosts.allowed_hosts([form.get("host", "")]),
            **doc_context(script, raw_text=raw_text),
        },
        status=status,
    )


def _param_list(data):
    p = data.get("parameters")
    return p if isinstance(p, list) else []


def script_configure(request, script_id):
    script = _get_script_or_404(script_id)
    data, _src, load_err = metadata.load_raw(script.path, script.id)

    if request.method != "POST":
        return _configure_response(
            request, script, form=_info_form(data, script), params=_param_list(data),
            raw_text=_raw_text(script, data), errors=[load_err] if load_err else [],
        )

    form = {
        "name": request.POST.get("name", "").strip(),
        "description": request.POST.get("description", "").strip(),
        "category": request.POST.get("category", "").strip(),
        "interpreter": request.POST.get("interpreter", "").strip(),
        "timeout": request.POST.get("timeout", "").strip(),
        "allow_concurrent": request.POST.get("allow_concurrent") == "on",
        "enabled": request.POST.get("enabled") == "on",
        "platforms": request.POST.getlist("platforms"),
        "host": request.POST.get("host", "local").strip() or "local",
        "working_directory": request.POST.get("working_directory", "").strip(),
        "environment": request.POST.get("environment", ""),
    }
    environment, env_errors = _text_to_env(form["environment"])
    errors, params = list(env_errors), []
    if load_err:
        errors.append(load_err + " Fix it in the Raw JSON editor first.")
    try:
        params = json.loads(request.POST.get("parameters_json", "[]"))
    except ValueError as exc:
        errors.append(f"Parameter data is not valid JSON: {exc}")
    timeout = None
    if form["timeout"]:
        try:
            timeout = int(form["timeout"])
        except ValueError:
            errors.append("Timeout must be a whole number of seconds.")

    if not errors:
        new = dict(data)
        for key in ("name", "description", "category", "interpreter"):
            if form[key]:
                new[key] = form[key]
            else:
                new.pop(key, None)
        if timeout is None:
            new.pop("timeout", None)
        else:
            new["timeout"] = timeout
        new["allow_concurrent"] = form["allow_concurrent"]
        new["enabled"] = form["enabled"]
        new["parameters"] = params
        # Platforms: only stored when they differ from the file-type default.
        chosen = [p for p in discovery.ALL_PLATFORMS if p in form["platforms"]]
        if chosen and chosen != discovery.default_platforms(script.extension):
            new["platforms"] = chosen
        else:
            new.pop("platforms", None)
        if form["host"] != "local":
            new["host"] = form["host"]
        else:
            new.pop("host", None)
        if form["working_directory"]:
            new["working_directory"] = form["working_directory"]
        else:
            new.pop("working_directory", None)
        if environment:
            new["environment"] = environment
        else:
            new.pop("environment", None)
        try:
            clean = ps.validate_metadata(new)
            metadata.save_raw(script.path, script.id, clean)
        except ValidationError as exc:
            errors.extend(exc.errors)
        except OSError as exc:
            errors.append(f"Could not save metadata: {exc}")
        else:
            discovery.scan(force=True)
            messages.success(request, "Script configuration saved.")
            return redirect("script_configure", script_id=script.id)

    return _configure_response(
        request, script, form=form, params=params if isinstance(params, list) else [],
        raw_text=_raw_text(script, data), errors=errors, status=400,
    )


RAW_BACK_PAGES = ("script_detail", "script_run", "script_configure")   # where a raw JSON save may return to


@require_POST
def script_configure_raw(request, script_id):
    script = _get_script_or_404(script_id)
    text = request.POST.get("raw_json", "")
    errors = []
    try:
        clean = ps.validate_metadata(json.loads(text))
    except ValueError as exc:
        errors.append(f"Invalid JSON: {exc}")
    except ValidationError as exc:
        errors.extend(exc.errors)
    else:
        try:
            metadata.save_raw(script.path, script.id, clean)
        except OSError as exc:
            errors.append(f"Could not save metadata: {exc}")
        else:
            discovery.scan(force=True)
            messages.success(request, "Metadata saved from raw JSON.")
            back = request.POST.get("back")
            if back not in RAW_BACK_PAGES:
                back = "script_configure"
            return redirect(back, script_id=script.id)
    cur, _src, _err = metadata.load_raw(script.path, script.id)
    return _configure_response(
        request, script, form=_info_form(cur, script), params=_param_list(cur),
        raw_text=text, raw_errors=errors, status=400,
    )


# ----------------------------------------------------------------- source ---
MAX_SOURCE_BYTES = 512 * 1024
LANGUAGES = {".py": "python", ".sh": "bash", ".bash": "bash", ".bat": "batch", ".cmd": "batch", ".json": "json"}


def _read_text_limited(path):
    """Return (text, truncated, error). Refuses binary content."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        return "", False, f"Could not read file: {exc}"
    truncated = len(blob) > MAX_SOURCE_BYTES
    blob = blob[:MAX_SOURCE_BYTES]
    if b"\x00" in blob:
        return "", False, "This file looks binary and cannot be displayed."
    return blob.decode("utf-8-sig", errors="replace"), truncated, None


def source_context(script):
    """Source lines for the designer-style pages (Run, Configure): script in the body."""
    if not is_within(script.path, script.root):
        return {"src_lines": [], "src_error": "Script is outside its configured directory.", "src_lang": ""}
    text, truncated, error = _read_text_limited(script.path)
    meta_text = ""
    if script.meta_source is not None:
        meta_text, _t, _e = _read_text_limited(script.meta_source)
    return {"src_lines": text.splitlines() if not error else [], "src_error": error,
            "src_truncated": truncated, "src_lang": LANGUAGES.get(script.extension, ""),
            "meta_lines": meta_text.splitlines()}


def doc_context(script, raw_text=None):
    """The shared centre of the script pages (Overview, Script, Configure): the script
    (editable when editing is on), its metadata JSON (editable) and its saved parameters."""
    from .edit_views import editor_context
    if raw_text is None:
        data, _src, _err = metadata.load_raw(script.path, script.id)
        raw_text = _raw_text(script, data)
    return {**source_context(script), **editor_context(script), "raw_text": raw_text,
            "pgrid": presets.grid(script, _params_of(script)), "pfile": presets.file_info(script)}


def script_source(request, script_id):
    """Read-only view of a catalogued script (and its metadata file)."""
    from django.http import HttpResponse

    script = _get_script_or_404(script_id)
    # Re-check at read time: the file must still resolve inside its root.
    if not is_within(script.path, script.root):
        raise Http404("Script is outside its configured directory")

    text, truncated, error = _read_text_limited(script.path)
    if request.GET.get("download") == "1" and error is None:
        resp = HttpResponse(text, content_type="text/plain; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{script.path.name}"'
        return resp

    meta_text = ""
    if script.meta_source is not None:
        meta_text, _t, _e = _read_text_limited(script.meta_source)
    try:
        size = script.path.stat().st_size
    except OSError:
        size = 0
    return render(
        request,
        "runner/source.html",
        {
            "script": script,
            "lines": text.splitlines() or ([""] if not error else []),
            "error": error,
            "truncated": truncated,
            "size": size,
            "language": LANGUAGES.get(script.extension, ""),
            "meta_lines": meta_text.splitlines(),
        },
    )
