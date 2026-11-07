"""HTTP views. Business logic lives in the service modules."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import discovery, history, metadata, ssh_hosts
from .security import clean_directory_input
from .validators import ValidationError


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
    # Quick run: recently used first, then the rest alphabetically (enabled and valid only).
    recent_order = {}
    for r in runs:
        recent_order.setdefault(r.get("script_id"), len(recent_order))
    runnable = [s for s in scripts if s.enabled and s.meta_valid]
    runnable.sort(key=lambda s: (recent_order.get(s.id, 10_000), s.name.lower()))

    return render(request, "runner/dashboard.html", {
        "total": len(scripts),
        "enabled": sum(1 for s in scripts if s.enabled),
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
            "enabled_count": sum(1 for s in scripts if s.enabled),
            "interpreters": sorted({s.interpreter for s in scripts if s.interpreter}),
        },
    )


@require_POST
def library_refresh(request):
    scripts, problems = discovery.scan(force=True)
    messages.success(request, f"Rescanned: {len(scripts)} script(s) found.")
    for p in problems:
        messages.warning(request, p)
    return redirect("library")


@require_POST
def script_toggle(request, script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404("Unknown script")
    enable = request.POST.get("enabled") == "1"
    try:
        metadata.set_enabled(script.path, script.id, enable)
    except (OSError, ValueError) as exc:
        messages.error(request, f"Could not update {script.name}: {exc}")
    else:
        discovery.scan(force=True)
        messages.success(request, f"{script.name} {'enabled' if enable else 'disabled'}.")
    return redirect("library")


def settings_view(request):
    cfg = config.load_config()
    dirs = []
    for configured in cfg["script_directories"]:
        resolved = config.resolve_dir(configured)
        dirs.append({"configured": configured, "resolved": resolved, "exists": resolved.is_dir()})
    scripts, _ = discovery.scan()
    for d in dirs:
        d["count"] = sum(1 for s in scripts if s.root == d["resolved"])
    return render(
        request,
        "runner/settings.html",
        {
            "directories": dirs,
            "interpreters": cfg["interpreters"],
            "remote_interpreters": cfg["remote_interpreters"],
            "ssh_hosts": ssh_hosts.list_hosts(),
            "ssh_executable": cfg["ssh_executable"],
        },
    )


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


@require_POST
def settings_add_directory(request):
    try:
        value = clean_directory_input(request.POST.get("directory", ""))
        resolved = config.resolve_dir(value)
        if not resolved.is_dir():
            raise ValueError(f"Not an existing directory: {resolved}")
        cfg = config.load_config()
        existing = {config.resolve_dir(d) for d in cfg["script_directories"]}
        if resolved in existing:
            raise ValueError("That directory is already configured.")
        cfg["script_directories"].append(value)
        config.save_config(cfg)
    except ValueError as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        for e in exc.errors:
            messages.error(request, e)
    else:
        discovery.scan(force=True)
        messages.success(request, f"Added {resolved}.")
    return redirect("settings")


@require_POST
def settings_remove_directory(request):
    value = request.POST.get("directory", "")
    cfg = config.load_config()
    if value not in cfg["script_directories"]:
        messages.error(request, "That directory is not configured.")
    else:
        cfg["script_directories"].remove(value)
        try:
            config.save_config(cfg)
        except ValidationError as exc:
            for e in exc.errors:
                messages.error(request, e)
        else:
            discovery.scan(force=True)
            messages.success(request, f"Removed {value}.")
    return redirect("settings")
