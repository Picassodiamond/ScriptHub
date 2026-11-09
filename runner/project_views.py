"""Project files (.shp): save the open folder with its sequences and presets as one file, and open such a
file on another computer (preview first, then unpack into a new folder). Logic in runner/project_files.py."""
from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import discovery, project_files, workspace
from .project_files import EXT, ProjectError


def project_save(request):
    """The project file of the open folder, with the default contents: every file of the folder (secrets and
    tool folders left out), the sequences that use its scripts (and the scripts they need), the presets.
    ``?name=`` = the project name (the menu's Save As dialog passes the chosen file name)."""
    root = workspace.current()
    if root is None:
        messages.error(request, "Open a folder first: a project file holds the open folder's scripts and files.")
        return redirect("folder")
    name = " ".join(request.GET.get("name", "").split())[:80]
    if name.lower().endswith(EXT):
        name = name[: -len(EXT)]
    name = name or root.name
    included, _left_out = project_files.folder_files(root)
    slugs = [s["slug"] for s in project_files.sequences_for(root) if s["uses_folder"]]
    try:
        fh, report = project_files.build(root, name, [f[0] for f in included], slugs, with_presets=True)
    except (ProjectError, OSError) as exc:
        messages.error(request, f"The project file was not made: {exc}")
        return redirect("library")
    resp = FileResponse(fh, as_attachment=True, filename=project_files.slug_name(name) + EXT,
                        content_type="application/zip")
    resp["X-ScriptHub-Report"] = (f"{report['files']} files ({report['scripts']} scripts), "
                                  f"{report['sequences']} sequences, {report['presets']} presets")
    return resp


def project_open(request):
    """GET: choose a project file. POST (upload): check it and show what opening it does."""
    if request.method == "POST":
        upload = request.FILES.get("project")
        if upload is None:
            messages.error(request, f"Choose a ScriptHub project file ({EXT}).")
            return redirect("project_open")
        try:
            token = project_files.save_upload(upload)
            preview = project_files.inspect(project_files.upload_path(token))
        except ProjectError as exc:
            messages.error(request, str(exc))
            return redirect("project_open")
        return render(request, "runner/project_open.html", {
            "token": token, "preview": preview, "file_name": upload.name, "ext": EXT,
            "destination": project_files.default_destination(preview["name"]),
        })
    return render(request, "runner/project_open.html", {"ext": EXT})


@require_POST
def project_install(request):
    """Unpack a checked upload into the chosen folder, add its sequences and presets, open the folder."""
    token = request.POST.get("token", "")
    if request.POST.get("cancel"):
        project_files.forget_upload(token)
        messages.success(request, "Nothing was opened.")
        return redirect("project_open")
    try:
        path = project_files.upload_path(token)
        result = project_files.install(path, request.POST.get("destination", ""))
    except ProjectError as exc:
        messages.error(request, str(exc))
        try:
            preview = project_files.inspect(project_files.upload_path(token))
        except ProjectError:
            return redirect("project_open")
        return render(request, "runner/project_open.html", {
            "token": token, "preview": preview, "ext": EXT,
            "destination": request.POST.get("destination", ""),
        }, status=400)
    except OSError as exc:
        messages.error(request, f"The project could not be unpacked: {exc}")
        return redirect("project_open")
    project_files.forget_upload(token)
    try:
        workspace.open_folder(result["folder"])
        discovery.rescan()
    except ValueError as exc:
        messages.error(request, str(exc))
    n = len(result["sequences"])
    messages.success(request, f"Opened the project in {result['folder']}: {result['files']} files, "
                              f"{n} sequence{'s' if n != 1 else ''}, {result['presets']} presets.")
    for note in result["kept"][:5]:
        messages.warning(request, note)
    for warning in result["warnings"][:8]:
        messages.warning(request, warning)
    if result["sequences"]:
        return redirect("sequence_detail", slug=result["sequences"][0])
    return redirect("library")



