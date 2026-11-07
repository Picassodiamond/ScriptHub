"""HTTP views. Business logic lives in the service modules."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import discovery, metadata
from .security import clean_directory_input
from .validators import ValidationError


def library(request):
    scripts, problems = discovery.scan()
    categories = sorted({s.category for s in scripts}, key=str.lower)
    return render(
        request,
        "runner/library.html",
        {
            "scripts": scripts,
            "categories": categories,
            "problems": problems,
            "enabled_count": sum(1 for s in scripts if s.enabled),
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
        {"directories": dirs, "interpreters": cfg["interpreters"]},
    )


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
