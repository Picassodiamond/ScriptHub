"""Views for running scripts: run form, start, live console, status polling,
stop, execution list and log download."""
import datetime
from pathlib import Path
import shlex
import uuid

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

import config
from . import discovery, executor, history, presets, ssh_hosts
from . import parameter_service as ps
from .executor import ExecutionError, ExecutionSpec
from .process_manager import manager
from .script_views import LANGUAGES, doc_context


def client_address(request) -> str:
    """Who started a run (the connecting address; there is no login).
    X-Forwarded-For is deliberately not trusted: any client can forge it."""
    return request.META.get("REMOTE_ADDR") or "unknown"


def _get_script_or_404(script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404("Unknown script")
    return script


RUN_MODES = ("normal", "trace", "debug")


def run_mode(post) -> str:
    """normal | trace | debug from a submitted form (the old ``trace=1`` checkbox still works)."""
    mode = post.get("mode", "")
    if mode in RUN_MODES:
        return mode
    return "trace" if post.get("trace") == "1" else "normal"


def _params_of(script):
    return script.meta.get("parameters", []) if script.meta_valid else []


# --------------------------------------------------------------- spec build ---
def build_spec(script, host, params, values, token=None, trace=False, debug=False) -> ExecutionSpec:
    """Turn validated values into an ExecutionSpec. Raises ExecutionError."""
    cfg = config.load_config()
    interpreter = executor.resolve_interpreter(script.meta.get("interpreter"), script.extension, host, cfg)
    argv, env_from_params = ps.build_arguments(params, values)
    masked_argv, _ = ps.build_arguments(params, values, mask_secrets=True)
    env = {**cfg["environment"], **script.meta.get("environment", {}), **env_from_params}
    if debug and not executor.debug_supported(script.extension, interpreter):
        raise ExecutionError(executor.DEBUG_UNSUPPORTED)
    trace = trace or debug
    if trace and not executor.trace_supported(script.extension, interpreter):
        raise ExecutionError("Line tracing is available for Python and Bash scripts only.")
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
        timeout=cfg["default_timeout"],
        parameters=ps.redact(params, values),
        display_command=shown,
        token=token,
        trace={} if trace else None,
        debug=bool(debug),
    )


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
        **doc_context(script),
    }


# ----------------------------------------------------------------- run form ---
def _load_saved(script, params, key, hosts, default_host):
    """Values to put in the run form. ``key``: "" = automatic (last run, else defaults),
    "-" = defaults, "@<n>" = n-th recent run, otherwise a preset slug.
    Returns (raw, host, source, warnings); source describes what was loaded."""
    raw, host, warnings = ps.defaults_raw(params), default_host, []
    stored, source = None, {"kind": "defaults", "key": "-", "label": "Defaults"}
    if key == "" or key.startswith("@"):
        index = 0
        if key:
            try:
                index = int(key[1:])
            except ValueError:
                index = -1
        stored = presets.get_recent(script, index)
        if stored is not None:
            when = (stored.get("saved_at") or "").replace("T", " ")[:16]
            name = "Last run" if index == 0 else (stored.get("run_id") or f"Run {index + 1}")
            source = {"kind": "recent", "key": f"@{index}", "label": name + (f" · {when}" if when else ""),
                      "run_id": stored.get("run_id", "")}
        elif key:
            warnings.append("Those saved values were not found; showing the defaults.")
    elif key != "-":
        stored = presets.get_preset(script, key)
        if stored is None:
            warnings.append("That preset was not found; showing the defaults.")
        else:
            source = {"kind": "preset", "key": key, "label": stored.get("name", key), "slug": key}
    if stored is not None:
        raw, warnings = presets.apply_preset(params, stored)
        if stored.get("host") in hosts:
            host = stored["host"]
    return raw, host, source, warnings


def _modes_supported(script) -> bool:
    """Trace / Debug are offered for Python and Bash scripts (with their default interpreter)."""
    try:
        interp = executor.resolve_interpreter(script.meta.get("interpreter"), script.extension,
                                              script.meta.get("host", ssh_hosts.LOCAL), config.load_config())
    except ExecutionError:
        return False
    return executor.trace_supported(script.extension, interp)


def _run_page(request, script, params, raw, host, hosts, *, errors=None, host_error=None, source=None,
              warnings=(), status=200):
    ctx = _form_context(script, params, raw, errors or {}, hosts, host, host_error)
    current = {"slug": source["slug"], "name": source["label"]} if source and source["kind"] == "preset" else None
    ctx.update(presets=presets.list_presets(script), recent=presets.recent_runs(script), source=source,
               current_preset=current, preset_warnings=list(warnings),
               has_password=any(p["type"] == "password" for p in params),
               token=uuid.uuid4().hex, modes_supported=_modes_supported(script), mode=run_mode(request.POST))
    return render(request, "runner/run.html", ctx, status=400 if ctx["has_errors"] else status)


def script_run(request, script_id):
    """The Run tab: the run form (values from the last run, a preset or the defaults).
    It posts to script_start, which runs the script straight away."""
    script = _get_script_or_404(script_id)
    if not script.meta_valid:
        return render(request, "runner/run.html", {"script": script, "blocked": script.meta_error})
    if request.method == "POST":                     # older forms posted here first (Review step)
        return script_start(request, script_id)
    params = _params_of(script)
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    hosts = ssh_hosts.allowed_hosts([default_host])
    raw, host, source, warnings = _load_saved(script, params, request.GET.get("preset", ""), hosts, default_host)
    return _run_page(request, script, params, raw, host, hosts, source=source, warnings=warnings)


@require_POST
def script_start(request, script_id):
    """Validate and hand the run to the manager; back to the script page, where the run
    opens as a tab. ``from_preset`` (a preset slug or "@<n>") runs saved values as they are."""
    script = _get_script_or_404(script_id)
    if not script.meta_valid:
        messages.error(request, "This script cannot be run until its metadata is fixed.")
        return redirect("script_detail", script_id=script.id)
    params = _params_of(script)
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    hosts = ssh_hosts.allowed_hosts([default_host])
    source, warnings = None, []
    saved = request.POST.get("from_preset", "")
    if saved:
        raw, host, source, warnings = _load_saved(script, params, saved, hosts, default_host)
        if warnings:                                 # not found: show the form instead of running defaults
            return _run_page(request, script, params, raw, host, hosts, source=source, warnings=warnings)
    else:
        host = request.POST.get("host", default_host)
        raw = ps.raw_from_post(params, request.POST)
    values, errors = ps.clean_values(params, raw)
    host_error = _check_host(script, host, hosts)
    spec = None
    mode = run_mode(request.POST)
    if not errors and not host_error:
        try:
            spec = build_spec(script, host, params, values, token=request.POST.get("submit_token") or None,
                              trace=mode == "trace", debug=mode == "debug")
        except ExecutionError as exc:
            host_error = str(exc)
    if spec is None:
        return _run_page(request, script, params, raw, host, hosts, errors=errors, host_error=host_error,
                         source=source, warnings=warnings)
    run_id = manager.submit(spec, initiator=client_address(request))
    try:                                   # remember the values: "run again with these"
        presets.record_run(script, params, raw, host, run_id, client_address(request))
    except (OSError, presets.PresetError):
        pass
    # Back to the script page; the run opens as a tab next to the script (designer.js).
    return redirect(reverse("script_run", args=[script.id]) + f"?open={run_id}")


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
    meta = history.canonical_script(_get_meta_or_404(run_id))     # the script's id as it is known now
    params = meta.get("parameters") or {}
    adhoc_source = None
    if meta.get("adhoc"):                      # show exactly the code that ran
        try:
            with open(meta.get("script_path", ""), "rb") as fh:
                adhoc_source = fh.read(200 * 1024).decode("utf-8", errors="replace")
        except OSError:
            adhoc_source = None
    trace = meta.get("trace") or None
    trace_lines = None
    if trace:
        try:
            with open(history.run_dir(run_id) / "trace_source", "rb") as fh:
                trace_lines = fh.read().decode("utf-8", errors="replace").splitlines()
            if trace_lines and trace_lines[-1] == "":
                trace_lines.pop()
        except OSError:
            trace_lines = None
    ext = Path(meta.get("script_path", "")).suffix.lower()
    embed = request.GET.get("embed") == "1"       # shown as a run tab inside a script page
    return render(request, "runner/console.html", {
        "embed": embed,
        "base_template": "base_embed.html" if embed else "base.html",
        "trace": trace,
        "debug": meta.get("debug") or None,
        "trace_lines": trace_lines,
        "trace_lang": LANGUAGES.get(ext, ""),
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
        "trace": _trace_state(run_id, meta),
        "debug": _debug_state(run_id, meta),
    })


def _trace_state(run_id, meta):
    trace = meta.get("trace")
    if not trace:
        return None
    line = None
    try:
        line = int((history.run_dir(run_id) / "trace.last").read_text().strip())
    except (OSError, ValueError):
        pass
    return {"line": line, "stopped": bool(trace.get("stopped"))}


def _last_line(run_id):
    try:
        return int((history.run_dir(run_id) / "trace.last").read_text().strip())
    except (OSError, ValueError):
        return None


def _debug_state(run_id, meta):
    """Step-debugger state for the console; None when the run is not a debug run."""
    if not meta.get("debug"):
        return None
    if _is_active(meta):
        state = manager.debug_state(run_id)
        if state is not None:
            return state
    return {"state": "ended", "line": _last_line(run_id), "scopes": [], "stack": [], "breakpoints": []}


@require_POST
def execution_debug(request, run_id):
    """Debugger control: action = step | continue | pause | detach | breakpoints (with lines="3,7")."""
    _get_meta_or_404(run_id)
    action = request.POST.get("action", "")
    if action not in ("step", "continue", "pause", "detach", "breakpoints"):
        return JsonResponse({"ok": False, "error": "Unknown debugger action."}, status=400)
    lines = None
    if action == "breakpoints":
        raw = request.POST.get("lines", "").replace(" ", "")
        try:
            lines = sorted({int(x) for x in raw.split(",") if x})
        except ValueError:
            lines = None
        if lines is None or any(n < 1 or n > 10_000_000 for n in lines) or len(lines) > 1000:
            return JsonResponse({"ok": False, "error": "Breakpoints must be line numbers, e.g. 3,7,12."}, status=400)
    if manager.debug_state(run_id) is None:
        return JsonResponse({"ok": False, "error": "This execution is not being debugged, or it has finished.",
                             "debug": None}, status=409)
    ok = manager.debug_command(run_id, action, lines)
    body = {"ok": ok, "debug": manager.debug_state(run_id)}
    if not ok:
        body["error"] = "The debugger did not accept the command (the run may be queued, detached or ending)."
    return JsonResponse(body, status=200 if ok else 409)


@require_POST
def execution_trace_stop(request, run_id):
    _get_meta_or_404(run_id)
    ok = manager.stop_trace(run_id)
    return JsonResponse({"ok": ok}, status=200 if ok else 409)


@require_POST
def execution_stop(request, run_id):
    meta = _get_meta_or_404(run_id)
    if manager.cancel(run_id):
        messages.info(request, "Stop requested.")
    elif _is_active(meta):
        messages.warning(request, "This run is not managed by this application instance, so it cannot be stopped from here.")
    else:
        messages.info(request, "This run has already finished.")
    if request.POST.get("embed") == "1":            # stopped from a run tab: stay in the tab
        return redirect(reverse("execution_console", args=[run_id]) + "?embed=1")
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


def active_runs():
    """Running and queued executions, running first."""
    active = [_decorate(m) for m in history.list_runs(limit=60) if _is_active(m)]
    active.sort(key=lambda m: (m["status"] != history.RUNNING, m["id"]))
    return active


def executions_list(request):
    """Jobs is part of History now (the Running panel on top); ?partial=1 is that panel,
    which History polls while something runs."""
    if request.GET.get("partial"):
        from .history_views import running_panel
        return render(request, "runner/_active_runs.html", running_panel())
    return redirect("history")
