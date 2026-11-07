"""Views for a single script: details, configuration (metadata + parameter
editor) and the dynamic run form."""
import json
import shlex

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import discovery, metadata
from . import parameter_service as ps
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
        rows.append({"p": p, "default": d})
    return render(request, "runner/script_detail.html", {"script": script, "rows": rows})


# -------------------------------------------------------------- configure ---
def _type_info():
    return {k: {"label": t.label, "allowed": sorted(t.allowed)} for k, t in ps.TYPES.items()}


def _info_form(data):
    timeout = data.get("timeout")
    return {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "category": data.get("category", ""),
        "interpreter": data.get("interpreter", ""),
        "timeout": "" if timeout is None else timeout,
        "allow_concurrent": data.get("allow_concurrent", False),
        "enabled": data.get("enabled", True),
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
            request, script, form=_info_form(data), params=_param_list(data),
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
    }
    errors, params = [], []
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
            return redirect("script_configure", script_id=script.id)
    cur, _src, _err = metadata.load_raw(script.path, script.id)
    return _configure_response(
        request, script, form=_info_form(cur), params=_param_list(cur),
        raw_text=text, raw_errors=errors, status=400,
    )


# -------------------------------------------------------------------- run ---
def _display_value(p, value):
    if value is None or value == "" or value == []:
        return "—"
    if p["type"] == "password":
        return ps.SECRET_MASK
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def script_run(request, script_id):
    script = _get_script_or_404(script_id)
    ctx = {"script": script}
    if not script.enabled:
        ctx["blocked"] = "This script is disabled. Enable it from the Script Library to run it."
        return render(request, "runner/run.html", ctx)
    if not script.meta_valid:
        ctx["blocked"] = script.meta_error
        return render(request, "runner/run.html", ctx)

    params = _params_of(script)
    errors = {}
    if request.method == "POST":
        raw = ps.raw_from_post(params, request.POST)
        if not request.POST.get("edit"):  # "edit" = back from confirmation
            values, errors = ps.clean_values(params, raw)
            if not errors:
                argv, env = ps.build_arguments(params, values, mask_secrets=True)
                ctx.update(
                    confirm=True,
                    rows=[
                        {
                            "label": p["label"],
                            "value": _display_value(p, values.get(p["name"])),
                            "locked": not p.get("editable_at_runtime", True),
                        }
                        for p in params
                    ],
                    command=shlex.join([script.interpreter or "python", str(script.path), *argv]),
                    env_rows=sorted(env.items()),
                    hidden=ps.hidden_pairs(params, raw),
                )
                return render(request, "runner/run_confirm.html", ctx)
    else:
        raw = ps.defaults_raw(params)

    ctx["fields"] = ps.build_fields(params, raw, errors)
    ctx["has_errors"] = bool(errors)
    return render(request, "runner/run.html", ctx, status=400 if errors else 200)
