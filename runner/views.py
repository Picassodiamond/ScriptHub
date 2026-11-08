"""HTTP views. Business logic lives in the service modules."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import discovery, history, ssh_hosts
from .validators import ValidationError, validate_config


def dashboard(request):
    from collections import Counter
    from .execution_views import _decorate
    from .process_manager import manager

    scripts, _problems = discovery.scan()
    by_id = {s.id: s for s in scripts}
    runs = history.list_runs(limit=500)
    counts = Counter(r.get("status") for r in runs)
    live = manager.counts()

    usage = Counter(r.get("script_id") for r in runs if r.get("script_id") in by_id)
    frequent = [{"script": by_id[sid], "runs": n} for sid, n in usage.most_common(5)]
    # Quick run: recently used first, then the rest alphabetically (valid metadata only).
    recent_order = {}
    for r in runs:
        recent_order.setdefault(r.get("script_id"), len(recent_order))
    runnable = [s for s in scripts if s.meta_valid]
    runnable.sort(key=lambda s: (recent_order.get(s.id, 10_000), s.name.lower()))

    return render(request, "runner/dashboard.html", {
        "total": len(scripts),
        "invalid": sum(1 for s in scripts if not s.meta_valid),
        "running": live["running"],
        "queued": live["queued"],
        "success": counts.get(history.SUCCESS, 0),
        "failed": counts.get(history.FAILED, 0) + counts.get(history.TIMED_OUT, 0),
        "recent": [_decorate(m) for m in runs[:8]],
        "frequent": frequent,
        "quick": runnable[:6],
        "has_runs": bool(runs),
    })


def library(request):
    scripts, problems = discovery.scan()
    from .execution_views import _ago
    last_runs = history.last_run_by_script()
    for s in scripts:
        run = last_runs.get(s.id)
        s.last_run = None if run is None else {
            "id": run["id"], "status": run.get("status"),
            "label": history.STATUS_LABELS.get(run.get("status"), run.get("status")),
            "ago": _ago(run.get("finished_at") or run.get("started_at") or run.get("queued_at")),
        }
    categories = sorted({s.category for s in scripts}, key=str.lower)
    languages = sorted({(s.language, s.language_label) for s in scripts}, key=lambda x: x[1].lower())
    platforms = [
        (key, discovery.PLATFORM_LABELS[key])
        for key in discovery.ALL_PLATFORMS
        if any(key in s.platforms for s in scripts)
    ]
    return render(
        request,
        "runner/library.html",
        {
            "scripts": scripts,
            "categories": categories,
            "languages": languages,
            "platforms": platforms,
            "problems": problems,
            "interpreters": sorted({s.interpreter for s in scripts if s.interpreter}),
        },
    )


@require_POST
def library_refresh(request):
    scripts, problems, finished = discovery.rescan()
    if not finished:
        messages.info(request, "Rescanning in the background; the list updates when it is done.")
        return redirect("library")
    messages.success(request, f"Rescanned: {len(scripts)} script(s) found.")
    for p in problems:
        messages.warning(request, p)
    return redirect("library")


INT_SETTINGS = (
    ("max_concurrent_executions", "Max concurrent executions"),
    ("max_concurrent_per_host", "Max concurrent executions per host"),
    ("default_timeout", "Default timeout"),
    ("polling_interval", "Polling interval"),
    ("history_retention_days", "History retention"),
    ("log_retention_days", "Log retention"),
    ("ssh_connect_timeout", "SSH connect timeout"),
)


def _map_to_text(mapping) -> str:
    return "\n".join(f"{k}={v}" for k, v in mapping.items())


def _text_to_map(text, what):
    """'key=value' lines -> (dict, errors)."""
    out, errors = {}, []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip() or not value.strip():
            errors.append(f"{what}, line {n}: use the form key=value.")
        else:
            out[key.strip()] = value.strip()
    return out, errors


def _settings_form(cfg):
    form = {k: cfg[k] for k, _ in INT_SETTINGS}
    form.update(
        enable_script_editing=cfg["enable_script_editing"],
        ssh_executable=cfg["ssh_executable"],
        environment=_map_to_text(cfg["environment"]),
        interpreters=_map_to_text(cfg["interpreters"]),
        remote_interpreters=_map_to_text(cfg["remote_interpreters"]),
    )
    return form


def _render_settings(request, form=None, errors=None, status=200):
    cfg = config.load_config()
    return render(
        request,
        "runner/settings.html",
        {
            "form": form if form is not None else _settings_form(cfg),
            "errors": errors or [],
            "ssh_hosts": ssh_hosts.list_hosts(),
        },
        status=status,
    )


def settings_view(request):
    return _render_settings(request)


@require_POST
def settings_save(request):
    """Validate and save the editable settings (limits, retention, interpreters,
    SSH, environment). The script folder is not a setting (see folder_views)."""
    from .script_views import _text_to_env

    cfg = config.load_config()
    form = {k: request.POST.get(k, "").strip() for k, _ in INT_SETTINGS}
    form["ssh_executable"] = request.POST.get("ssh_executable", "").strip()
    for key in ("environment", "interpreters", "remote_interpreters"):
        form[key] = request.POST.get(key, "")
    form["enable_script_editing"] = request.POST.get("enable_script_editing") == "on"   # checkbox: absent = off
    errors, new = [], dict(cfg)
    new["enable_script_editing"] = form["enable_script_editing"]

    for key, label in INT_SETTINGS:
        try:
            new[key] = int(form[key])
        except ValueError:
            errors.append(f"{label} must be a whole number.")   # keep the old value so other checks still run
    new["ssh_executable"] = form["ssh_executable"]
    new["environment"], env_errors = _text_to_env(form["environment"])
    new["interpreters"], i_errors = _text_to_map(form["interpreters"], "Interpreters")
    new["remote_interpreters"], r_errors = _text_to_map(form["remote_interpreters"], "Remote interpreters")
    errors += env_errors + i_errors + r_errors

    # Always validate so every problem is reported at once, not one at a time.
    try:
        validate_config(new)
    except ValidationError as exc:
        errors += exc.errors
    if not errors:
        config.save_config(new)
    if errors:
        return _render_settings(request, form=form, errors=errors, status=400)
    discovery.rescan(wait=1.0)                       # interpreters may have changed
    messages.success(request, "Settings saved. They apply to the next execution.")
    return redirect("settings")


@require_POST
def settings_test_host(request):
    host = request.POST.get("host", "")
    if host == ssh_hosts.LOCAL or host not in ssh_hosts.list_hosts():
        messages.error(request, "Unknown SSH host.")
    else:
        ok, detail = ssh_hosts.test_connection(host)
        (messages.success if ok else messages.error)(
            request, f"{host}: connected ({detail})" if ok else f"{host}: {detail}")
    return redirect("settings")
