"""Save, rename and delete parameter presets (loading happens in the run form)."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import presets, ssh_hosts
from . import parameter_service as ps
from .execution_views import _form_context, _get_script_or_404, _params_of


def _run_url(script_id, slug=None):
    url = reverse("script_run", args=[script_id])
    return f"{url}?preset={slug}" if slug else url


@require_POST
def preset_save(request, script_id):
    script = _get_script_or_404(script_id)
    params = _params_of(script)
    default_host = script.meta.get("host", ssh_hosts.LOCAL)
    hosts = ssh_hosts.allowed_hosts([default_host])
    host = request.POST.get("host", default_host)
    if host not in hosts:
        host = default_host
    raw = ps.raw_from_post(params, request.POST)
    name = request.POST.get("preset_name", "")
    existed = any(p["name"].lower() == " ".join(name.split()).lower() for p in presets.list_presets(script.id))
    try:
        slug = presets.save_preset(script.id, name, params, raw, host)
    except presets.PresetError as exc:
        messages.error(request, str(exc))
        ctx = _form_context(script, params, raw, {}, hosts, host, None)
        ctx.update(presets=presets.list_presets(script.id), preset_name_draft=name)
        return render(request, "runner/run.html", ctx, status=400)
    messages.success(request, f"Preset '{' '.join(name.split())}' {'updated' if existed else 'saved'}.")
    return redirect(_run_url(script.id, slug))


@require_POST
def preset_rename(request, script_id):
    script = _get_script_or_404(script_id)
    slug = request.POST.get("slug", "")
    try:
        presets.rename_preset(script.id, slug, request.POST.get("new_name", ""))
    except presets.PresetError as exc:
        messages.error(request, str(exc))
        return redirect(_run_url(script.id, slug if presets.get_preset(script.id, slug) else None))
    messages.success(request, "Preset renamed.")
    return redirect(_run_url(script.id, slug))


@require_POST
def preset_delete(request, script_id):
    script = _get_script_or_404(script_id)
    try:
        removed = presets.delete_preset(script.id, request.POST.get("slug", ""))
    except presets.PresetError as exc:
        messages.error(request, str(exc))
        removed = False
    else:
        (messages.success if removed else messages.info)(
            request, "Preset deleted." if removed else "That preset was already gone.")
    return redirect(_run_url(script.id))
