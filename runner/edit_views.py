"""Edit a catalogued script file from the browser."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import backups, discovery, metadata, script_editor
from .script_editor import ConflictError, EditError
from .script_views import LANGUAGES


def _get_script_or_404(script_id):
    script = discovery.get_script(script_id)
    if script is None:
        raise Http404("Unknown script")
    return script


def _disabled(request, script):
    if config.load_config()["enable_script_editing"]:
        return None
    return render(request, "runner/edit.html", {
        "script": script,
        "blocked": "Editing scripts from the browser is turned off in Settings."}, status=403)


def _context(script, info, **extra):
    ctx = {
        "script": script,
        "text": info["text"],
        "base_hash": info["hash"],
        "newline": info["newline"].upper(),
        "writable": info["writable"],
        "size": info["size"],
        "language": LANGUAGES.get(script.extension, ""),
        "backup_versions": backups.versions(script.id),
    }
    ctx.update(extra)
    return ctx


def editor_context(script) -> dict:
    """Editor data for the merged Script page (run form + editable script).
    Empty when editing is off or the file cannot be edited (the page then shows it read-only)."""
    if not config.load_config()["enable_script_editing"]:
        return {}
    try:
        info = script_editor.read_for_edit(script.path, script.root)
    except EditError as exc:
        return {"ed_error": str(exc)}
    return {"ed": {"text": info["text"], "hash": info["hash"], "newline": info["newline"].upper(),
                   "writable": info["writable"], "size": info["size"]},
            "backup_versions": backups.versions(script.id)}


def _back_to_script(script):
    """After saving or restoring: the merged Script page when the script can run, else the editor."""
    if script.meta_valid:
        return redirect("script_run", script_id=script.id)
    return redirect("script_edit", script_id=script.id)


def script_edit(request, script_id):
    script = _get_script_or_404(script_id)
    blocked = _disabled(request, script)
    if blocked:
        return blocked
    try:
        info = script_editor.read_for_edit(script.path, script.root)
    except EditError as exc:
        return render(request, "runner/edit.html", {"script": script, "blocked": str(exc)}, status=400)

    if request.method != "POST":
        return render(request, "runner/edit.html", _context(script, info))

    posted = request.POST.get("content", "")
    base_hash = request.POST.get("base_hash", "")
    force = request.POST.get("force") == "1"
    try:
        result = script_editor.save_script(script.path, script.root, posted, base_hash, force=force, sid=script.id)
    except ConflictError as exc:
        # Keep what the user typed; the hash is refreshed only if they choose to overwrite.
        ctx = _context(script, info, text=posted.replace("\r\n", "\n"), error=str(exc), conflict=True)
        ctx["base_hash"] = base_hash
        return render(request, "runner/edit.html", ctx, status=409)
    except EditError as exc:
        ctx = _context(script, info, text=posted.replace("\r\n", "\n"), error=str(exc))
        ctx["base_hash"] = base_hash
        return render(request, "runner/edit.html", ctx, status=400)

    script = _renew_copied_id(script)
    discovery.update_file(script.path)
    script = discovery.get_script(script.path_id) or script
    if result["changed"]:
        messages.success(request, f"Saved {script.path.name}. The previous version is kept (Restore previous).")
    else:
        messages.info(request, "No changes to save.")
    if result["warning"]:
        messages.warning(request, result["warning"])
    return _back_to_script(script)


def _renew_copied_id(script):
    """A copy of another script (same stable id) gets a new id on its next save."""
    if not script.id_conflict:
        return script
    data, _src, err = metadata.load_raw(script.path, script.id)
    if err:
        return script
    try:
        metadata.save_raw(script.path, script.id, data, conflict=True)
    except OSError:
        return script
    discovery.update_file(script.path)
    return discovery.get_script(script.path_id) or script


@require_POST
def script_restore(request, script_id):
    script = _get_script_or_404(script_id)
    blocked = _disabled(request, script)
    if blocked:
        return blocked
    version = "" if request.POST.get("previous") else request.POST.get("version", "")
    try:
        script_editor.restore_backup(script.path, script.root, sid=script.id, version=version)
    except EditError as exc:
        messages.error(request, str(exc))
    else:
        discovery.update_file(script.path)
        script = discovery.get_script(script.path_id) or script
        messages.success(request, "Restored. The version you replaced is kept too, so you can switch back (Restore previous).")
    return _back_to_script(script)
