"""Views for a single script: the Overview (configuration form: metadata + parameter
editor), raw metadata JSON, and the source view."""
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


# ------------------------------------------- overview / parameters tabs ---
# Overview: the script's settings (name, execution) and recent runs; the old /configure/
# URL redirects here. Parameters: the parameter definitions and the saved values.
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
    plats = data.get("platforms")
    if not (isinstance(plats, list) and plats):
        plats = discovery.default_platforms(script.extension)
    return {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "category": data.get("category", ""),
        "interpreter": data.get("interpreter", ""),
        "platforms": plats,
        "host": data.get("host", "local"),
        "working_directory": data.get("working_directory", ""),
        "environment": _env_to_text(data.get("environment")),
    }


def _configure_response(request, script, *, form, params, errors=None, status=200,
                        template="runner/script_detail.html"):
    """Overview (settings) and Parameters (definitions + saved values) share this context."""
    from .execution_views import _decorate
    saved, recent = presets.list_presets(script), presets.recent_runs(script)
    return render(
        request,
        template,
        {
            "script": script,
            "runs": [_decorate(m) for m in history.list_runs(limit=8, script_id=script.id)],
            "presets": saved,
            "recent": recent,
            "saved_count": len(saved) + len(recent),
            "form": form,
            "params": params,
            "type_info": _type_info(),
            "errors": errors or [],
            "platform_choices": list(discovery.PLATFORM_LABELS.items()),
            "hosts": ssh_hosts.allowed_hosts([form.get("host", "")]),
            **doc_context(script),
        },
        status=status,
    )


def _param_list(data):
    p = data.get("parameters")
    return p if isinstance(p, list) else []


def script_configure(request, script_id):
    """Old Configure URL: the form now lives on the Overview page."""
    if request.method != "POST":
        return redirect("script_detail", script_id=script_id)
    return script_detail(request, script_id)


def script_detail(request, script_id):
    script = _get_script_or_404(script_id)
    data, _src, load_err = metadata.load_raw(script.path, script.id)

    if request.method != "POST":
        return _configure_response(
            request, script, form=_info_form(data, script), params=_param_list(data),
            errors=[load_err] if load_err else [],
        )

    form = {
        "name": request.POST.get("name", "").strip(),
        "description": request.POST.get("description", "").strip(),
        "category": request.POST.get("category", "").strip(),
        "interpreter": request.POST.get("interpreter", "").strip(),
        "platforms": request.POST.getlist("platforms"),
        "host": request.POST.get("host", "local").strip() or "local",
        "working_directory": request.POST.get("working_directory", "").strip(),
        "environment": request.POST.get("environment", ""),
    }
    environment, env_errors = _text_to_env(form["environment"])
    errors = list(env_errors)
    if load_err:
        errors.append(load_err + " Fix the metadata block at the top of the script first.")
    params = _param_list(data)                     # definitions are edited on the Parameters tab
    if "parameters_json" in request.POST:          # (an old combined Configure form may still send them)
        params, perr = _posted_params(request)
        errors += perr

    if not errors:
        new = dict(data)
        for key in ("name", "description", "category", "interpreter"):
            if form[key]:
                new[key] = form[key]
            else:
                new.pop(key, None)
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
        errors += _save(script, new)
        if not errors:
            messages.success(request, "Script settings saved.")
            return redirect("script_detail", script_id=_current_id(script))

    return _configure_response(
        request, script, form=form, params=params if isinstance(params, list) else [],
        errors=errors, status=400,
    )


def _posted_params(request):
    """The parameter editor's JSON -> (list, errors)."""
    try:
        params = json.loads(request.POST.get("parameters_json", "[]"))
    except ValueError as exc:
        return [], [f"Parameter data is not valid JSON: {exc}"]
    if not isinstance(params, list):
        return [], ["Parameter data must be a list."]
    return params, []


def _save(script, new) -> list:
    """Validate and write the metadata; returns the errors (empty = saved)."""
    try:
        clean = ps.validate_metadata(new)
        # the first save writes a stable id; a copy of another script gets a new one
        metadata.save_raw(script.path, script.id, clean, conflict=bool(script.id_conflict))
    except ValidationError as exc:
        return list(exc.errors)
    except OSError as exc:
        return [f"Could not save metadata: {exc}"]
    discovery.update_file(script.path)
    return []


def _current_id(script) -> str:
    """The script's id after a save (a stable id may just have been written)."""
    fresh = discovery.get_script(script.path_id)
    return fresh.id if fresh else script.id


def script_parameters(request, script_id):
    """Parameters tab: the parameter definitions (the run form is built from them) and the
    saved values (presets and recent runs)."""
    script = _get_script_or_404(script_id)
    data, _src, load_err = metadata.load_raw(script.path, script.id)
    page = {"form": _info_form(data, script), "template": "runner/script_parameters.html"}
    if request.method != "POST":
        return _configure_response(request, script, params=_param_list(data),
                                   errors=[load_err] if load_err else [], **page)
    params, errors = _posted_params(request)
    if load_err:
        errors.append(load_err + " Fix the metadata block at the top of the script first.")
    if not errors:
        errors = _save(script, {**data, "parameters": params})
        if not errors:
            messages.success(request, "Parameters saved.")
            return redirect("script_parameters", script_id=_current_id(script))
    return _configure_response(request, script, params=params, errors=errors, status=400, **page)


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
    if script.meta_source is not None and not metadata.embedded(script):   # an old sidecar file
        meta_text, _t, _e = _read_text_limited(script.meta_source)
    return {"src_lines": text.splitlines() if not error else [], "src_error": error,
            "src_truncated": truncated, "src_lang": LANGUAGES.get(script.extension, ""),
            "meta_lines": meta_text.splitlines()}


def doc_context(script):
    """The shared centre of the script pages (Overview, Parameters, Run): the script (editable
    when editing is on; its metadata block is at the top) and its saved values."""
    from .edit_views import editor_context
    return {**source_context(script), **editor_context(script),
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
    if script.meta_source is not None and not metadata.embedded(script):   # an old sidecar file
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
