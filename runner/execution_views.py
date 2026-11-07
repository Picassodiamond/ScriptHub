"""Views for running scripts: run form, start, live console, status polling,
stop, execution list and log download."""
import datetime
import shlex
import uuid

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import discovery, executor, history, presets, ssh_hosts
from . import parameter_service as ps
from .executor import ExecutionError, ExecutionSpec
from .process_manager import manager


def client_address(request) -> str:
    """Who started a run (the connecting address; there is no login).
    X-Forwarded-For is deliberately not trusted: any client can forge it."""
    return request.META.get("REMOTE_ADDR") or "unknown"


def _get_script_or_404(script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404("Unknown script")
    return script


def _params_of(script):
    return script.meta.get("parameters", []) if script.meta_valid else []


# --------------------------------------------------------------- spec build ---
def build_spec(script, host, params, values, token=None) -> ExecutionSpec:
    """Turn validated values into an ExecutionSpec. Raises ExecutionError."""
    cfg = config.load_config()
    interpreter = executor.resolve_interpreter(script.meta.get("interpreter"), script.extension, host, cfg)
    argv, env_from_params = ps.build_arguments(params, values)
    masked_argv, _ = ps.build_arguments(params, values, mask_secrets=True)
    env = {**cfg["environment"], **script.meta.get("environment", {}), **env_from_params}
    if host == ssh_hosts.LOCAL:
        shown = shlex.join([interpreter, str(script.path), *masked_argv])
    else:
        shown = f"ssh {host}: {interpreter} - {shlex.join(masked_argv)}".rstrip()
    return ExecutionSpec(
        script_id=script.id,
        script_name=script.name,
        script_path=str(script.path),
        extension=script.extension,
        interpreter=interpreter,
        host=host,
        argv=argv,
        env=env,
        cwd=script.meta.get("working_directory") or None,
        timeout=script.meta.get("timeout") or cfg["default_timeout"],
        allow_concurrent=bool(script.meta.get("allow_concurrent", False)),
        parameters=ps.redact(params, values),
        display_command=shown,
        token=token,
    )


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


def _check_host(script, host, hosts):
    if host not in hosts:
        return "Choose one of the listed hosts."
    if script.extension in executor.BATCH_EXTENSIONS and host != ssh_hosts.LOCAL:
        return "Batch files can only run on this computer (local)."
    return None


def _form_context(script, params, raw, errors, hosts, host, host_error):
    return {
        "script": script,
        "fields": ps.build_fields(params, raw, errors),
        "has_errors": bool(errors) or bool(host_error),
        "hosts": hosts,
        "host": host,
        "host_error": host_error,
    }


# ----------------------------------------------------------------- run form ---
def script_run(request, script_id):
    script = _get_script_or_404(script_id)
    if not script.enabled:
        return render(request, "runner/run.html", {
            "script": script,
            "blocked": "This script is disabled. Enable it from the Script Library to run it."})
    if not script.meta_valid:
        return render(request, "runner/run.html", {"script": script, "blocked": script.meta_error})

    params = _params_of(script)
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    hosts = ssh_hosts.allowed_hosts([default_host])
    errors, host_error, host = {}, None, default_host
    current_preset, preset_warnings = None, []

    if request.method == "POST":
        host = request.POST.get("host", default_host)
        raw = ps.raw_from_post(params, request.POST)
        if not request.POST.get("edit"):          # "edit" = back from the confirmation page
            host_error = _check_host(script, host, hosts)
            values, errors = ps.clean_values(params, raw)
            if not errors and not host_error:
                try:
                    spec = build_spec(script, host, params, values)
                except ExecutionError as exc:
                    host_error = str(exc)
                else:
                    _, env_masked = ps.build_arguments(params, values, mask_secrets=True)
                    return render(request, "runner/run_confirm.html", {
                        "script": script,
                        "host": host,
                        "rows": [{"label": p["label"], "value": _display_value(p, values.get(p["name"])),
                                  "locked": not p.get("editable_at_runtime", True)} for p in params],
                        "command": spec.display_command,
                        "env_rows": sorted(env_masked.items()),
                        "hidden": ps.hidden_pairs(params, raw),
                        "token": uuid.uuid4().hex,
                        "timeout": spec.timeout,
                    })
    else:
        raw = ps.defaults_raw(params)
        slug = request.GET.get("preset", "")
        if slug:
            preset = presets.get_preset(script.id, slug)
            if preset is None:
                preset_warnings.append("That preset was not found; showing the defaults.")
            else:
                raw, preset_warnings = presets.apply_preset(params, preset)
                current_preset = {"slug": slug, "name": preset.get("name", slug)}
                if preset.get("host") in hosts:
                    host = preset["host"]

    ctx = _form_context(script, params, raw, errors, hosts, host, host_error)
    ctx.update(presets=presets.list_presets(script.id), current_preset=current_preset,
               preset_warnings=preset_warnings, has_password=any(p["type"] == "password" for p in params))
    return render(request, "runner/run.html", ctx, status=400 if ctx["has_errors"] else 200)


@require_POST
def script_start(request, script_id):
    """Second step: validate again and hand the run to the manager."""
    script = _get_script_or_404(script_id)
    if not script.enabled or not script.meta_valid:
        messages.error(request, "This script cannot be run right now.")
        return redirect("script_detail", script_id=script.id)
    params = _params_of(script)
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    hosts = ssh_hosts.allowed_hosts([default_host])
    host = request.POST.get("host", default_host)
    raw = ps.raw_from_post(params, request.POST)
    values, errors = ps.clean_values(params, raw)
    host_error = _check_host(script, host, hosts)
    spec = None
    if not errors and not host_error:
        try:
            spec = build_spec(script, host, params, values, token=request.POST.get("submit_token") or None)
        except ExecutionError as exc:
            host_error = str(exc)
    if spec is None:
        ctx = _form_context(script, params, raw, errors, hosts, host, host_error)
        return render(request, "runner/run.html", ctx, status=400)
    run_id = manager.submit(spec, initiator=client_address(request))
    return redirect("execution_console", run_id=run_id)


# ------------------------------------------------------------------ console ---
def _get_meta_or_404(run_id):
    if not history.valid_run_id(run_id):
        raise Http404("Unknown execution")
    meta = history.read_meta(run_id)
    if meta is None:
        raise Http404("Unknown execution")
    meta.setdefault("id", run_id)
    return meta


def _is_active(meta):
    return meta.get("status") in history.ACTIVE_STATUSES


def execution_console(request, run_id):
    meta = _get_meta_or_404(run_id)
    params = meta.get("parameters") or {}
    adhoc_source = None
    if meta.get("adhoc"):                      # show exactly the code that ran
        try:
            with open(meta.get("script_path", ""), "rb") as fh:
                adhoc_source = fh.read(200 * 1024).decode("utf-8", errors="replace")
        except OSError:
            adhoc_source = None
    return render(request, "runner/console.html", {
        "run": meta,
        "adhoc_source": adhoc_source,
        "adhoc_args": (meta.get("adhoc") or {}).get("arguments") or [],
        "status_label": history.STATUS_LABELS.get(meta.get("status"), meta.get("status")),
        "param_rows": [(k, "—" if v in (None, "", []) else (", ".join(v) if isinstance(v, list) else v))
                       for k, v in params.items()],
        "active": _is_active(meta),
    })


def execution_status(request, run_id):
    meta = _get_meta_or_404(run_id)

    def offset(name):
        try:
            return max(0, int(request.GET.get(name, 0)))
        except ValueError:
            return 0

    out, out_next = history.read_chunk(history.log_path(run_id, "stdout"), offset("out"))
    err, err_next = history.read_chunk(history.log_path(run_id, "stderr"), offset("err"))
    seconds = history.duration_seconds(meta)
    if meta.get("duration_seconds") is not None:
        seconds = meta["duration_seconds"]
    status = meta.get("status")
    return JsonResponse({
        "status": status,
        "status_label": history.STATUS_LABELS.get(status, status),
        "active": _is_active(meta),
        "exit_code": meta.get("exit_code"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "queued_at": meta.get("queued_at"),
        "duration": history.format_duration(seconds),
        "duration_seconds": seconds,
        "queue_position": manager.queue_position(run_id),
        "error": meta.get("error"),
        "pid": meta.get("pid"),
        "stdout": out, "stdout_next": out_next,
        "stderr": err, "stderr_next": err_next,
    })


@require_POST
def execution_stop(request, run_id):
    meta = _get_meta_or_404(run_id)
    if manager.cancel(run_id):
        messages.info(request, "Stop requested.")
    elif _is_active(meta):
        messages.warning(request, "This run is not managed by this application instance, so it cannot be stopped from here.")
    else:
        messages.info(request, "This run has already finished.")
    return redirect("execution_console", run_id=run_id)


def execution_log(request, run_id, stream):
    _get_meta_or_404(run_id)
    if stream not in ("stdout", "stderr"):
        raise Http404("Unknown log")
    path = history.log_path(run_id, stream)
    if not path.is_file():
        raise Http404("No output captured")
    return FileResponse(open(path, "rb"), as_attachment=True, content_type="text/plain; charset=utf-8",
                        filename=f"{run_id}-{stream}.log")


# --------------------------------------------------------------- executions ---
def _ago(iso):
    when = history.parse_iso(iso)
    if when is None:
        return ""
    secs = int((datetime.datetime.now() - when).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def _decorate(meta):
    meta = dict(meta)
    meta["status_label"] = history.STATUS_LABELS.get(meta.get("status"), meta.get("status"))
    seconds = meta.get("duration_seconds")
    meta["duration"] = history.format_duration(seconds if seconds is not None else history.duration_seconds(meta))
    meta["ago"] = _ago(meta.get("finished_at") or meta.get("started_at") or meta.get("queued_at"))
    meta["position"] = manager.queue_position(meta["id"])
    return meta


def executions_list(request):
    runs = history.list_runs(limit=60)
    active = [_decorate(m) for m in runs if _is_active(m)]
    active.sort(key=lambda m: (m["status"] != history.RUNNING, m["id"]))
    recent = [_decorate(m) for m in runs if not _is_active(m)][:25]
    template = "runner/_runs_table.html" if request.GET.get("partial") else "runner/executions.html"
    return render(request, template, {"active": active, "recent": recent})
