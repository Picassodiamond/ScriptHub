"""Edit a catalogued script file from the browser."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import config
from . import discovery, script_editor
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
        "has_backup": script_editor.has_backup(script.path),
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
            "has_backup": script_editor.has_backup(script.path)}


def _back_to_script(script):
    """After saving or restoring: the merged Script page when the script can run, else the editor."""
    if script.enabled and script.meta_valid:
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
        result = script_editor.save_script(script.path, script.root, posted, base_hash, force=force)
    except ConflictError as exc:
        # Keep what the user typed; the hash is refreshed only if they choose to overwrite.
        ctx = _context(script, info, text=posted.replace("\r\n", "\n"), error=str(exc), conflict=True)
        ctx["base_hash"] = base_hash
        return render(request, "runner/edit.html", ctx, status=409)
    except EditError as exc:
        ctx = _context(script, info, text=posted.replace("\r\n", "\n"), error=str(exc))
        ctx["base_hash"] = base_hash
        return render(request, "runner/edit.html", ctx, status=400)

    discovery.scan(force=True)
    if result["changed"]:
        messages.success(request, f"Saved {script.path.name}. The previous version is kept as {script.path.name}.bak.")
    else:
        messages.info(request, "No changes to save.")
    if result["warning"]:
        messages.warning(request, result["warning"])
    return _back_to_script(script)


@require_POST
def script_restore(request, script_id):
    script = _get_script_or_404(script_id)
    blocked = _disabled(request, script)
    if blocked:
        return blocked
    try:
        script_editor.restore_backup(script.path, script.root)
    except EditError as exc:
        messages.error(request, str(exc))
    else:
        discovery.scan(force=True)
        messages.success(request, "Restored the previous version. The version you replaced is now the backup, so you can switch back.")
    return _back_to_script(script)
