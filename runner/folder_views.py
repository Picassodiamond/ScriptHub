"""Open folder page: open / close the script folder, recent and favourite folders,
and an in-page folder browser (the native dialog is only there in desktop mode)."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import discovery, workspace


def _next(request, default="library"):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)


def folder_page(request):
    ws = workspace.overview()
    start = request.GET.get("browse")
    if start is None:
        start = ws["folder"]["path"] if ws["folder"] else ""
    return render(request, "runner/folder.html", {"ws": ws, "browser": workspace.browse(start)})


@require_POST
def folder_open(request):
    try:
        path = workspace.open_folder(request.POST.get("path", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("folder")
    scripts, problems = discovery.scan(force=True)
    messages.success(request, f"Opened {path} — {len(scripts)} script{'' if len(scripts) == 1 else 's'} found.")
    for p in problems:
        messages.warning(request, p)
    return _next(request)


@require_POST
def folder_close(request):
    workspace.close_folder()
    discovery.scan(force=True)
    messages.success(request, "Folder closed.")
    return redirect("folder")


@require_POST
def folder_favorite(request):
    path = request.POST.get("path", "")
    if path:
        on = request.POST.get("on") == "1"
        workspace.set_favorite(path, on)
        messages.success(request, "Added to favourites." if on else "Removed from favourites.")
    return _next(request, "folder")


@require_POST
def folder_forget(request):
    path = request.POST.get("path", "")
    if path:
        workspace.forget(path)
    return _next(request, "folder")
