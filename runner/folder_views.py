"""Open folder page: open / close the script folder, recent and favourite folders,
and an in-page folder browser (the native dialog is only there in desktop mode)."""
from django.contrib import messages
from django.http import JsonResponse
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
    """Open a folder, or one or more script files (several ``path`` values)."""
    opened, errors = [], []
    for value in request.POST.getlist("path") or [""]:
        try:
            opened.append(workspace.open_folder(value))
        except ValueError as exc:
            errors.append(str(exc))
    for e in errors:
        messages.error(request, e)
    if not opened:
        return redirect("folder")
    scripts, problems, finished = discovery.rescan()
    what = str(opened[0]) if len(opened) == 1 else f"{len(opened)} scripts"
    if finished:
        messages.success(request, f"Opened {what} — {len(scripts)} script{'' if len(scripts) == 1 else 's'} open.")
        for p in problems:
            messages.warning(request, p)
    else:
        messages.info(request, f"Opened {what}. It is still being scanned for scripts; "
                               "the progress is shown at the bottom and the list fills in as they are found.")
    files = [p for p in opened if p.is_file()]
    if len(files) == len(opened):               # only files: show the (first) script, as a tab
        found = discovery.get_script(discovery.script_id(files[0].resolve()))
        if found is not None:
            return redirect("script_detail", script_id=found.id)
    return _next(request)


@require_POST
def folder_close(request):
    workspace.close_folder()
    discovery.rescan()
    messages.success(request, "Folder closed.")
    return redirect("folder")


@require_POST
def folder_close_file(request):
    """Close one opened script file, or all of them (``all=1``)."""
    if request.POST.get("all") == "1":
        workspace.close_all_files()
        messages.success(request, "Closed the opened files.")
    elif workspace.close_file(request.POST.get("path", "")):
        messages.success(request, "File closed.")
    discovery.rescan()
    return redirect("library")


@require_POST
def folder_favorite(request):
    path = request.POST.get("path", "")
    if path:
        on = request.POST.get("on") == "1"
        workspace.set_favorite(path, on)
        messages.success(request, "Added to favourites." if on else "Removed from favourites.")
    return _next(request, "folder")


def folder_scan(request):
    """Progress of the background folder scan (polled by the status bar while it runs)."""
    return JsonResponse(discovery.status())


@require_POST
def folder_forget(request):
    path = request.POST.get("path", "")
    if path:
        workspace.forget(path)
    return _next(request, "folder")
