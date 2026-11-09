"""Template context shared by every page of the workbench UI.

Expensive values (the explorer tree, the side run list, the command palette
data) are lazy: they are only computed when a template actually uses them.
"""
from django.utils.functional import SimpleLazyObject

APP_VERSION = "2.0"


def _tree():
    from . import discovery, history

    scripts, _problems = discovery.scan()
    last = history.last_run_by_script(scan_limit=200)
    groups, by_cat = [], {}
    for s in scripts:
        run = last.get(s.id) or {}
        status = run.get("status")
        item = {
            "id": s.id,
            "name": s.name,
            "ext": s.extension[1:],
            "relative": s.relative,
            "category": s.category,
            "runnable": s.meta_valid,
            "status": status,
            "status_label": history.STATUS_LABELS.get(status, status) if status else "",
        }
        if s.category not in by_cat:
            by_cat[s.category] = {"name": s.category, "scripts": []}
            groups.append(by_cat[s.category])
        by_cat[s.category]["scripts"].append(item)
    return groups


def _runs():
    from . import history
    from .execution_views import _decorate

    return [_decorate(m) for m in history.list_runs(limit=40)]


def _palette(tree):
    return [
        {"id": s["id"], "name": s["name"], "cat": s["category"], "ext": s["ext"],
         "path": s["relative"], "run": s["runnable"]}
        for g in tree for s in g["scripts"]
    ]


def _sequences():
    """Sequences for the centre tab bar (open sequences are tabs next to the scripts)."""
    from . import sequences
    return [{"slug": x["slug"], "name": x["name"]} for x in sequences.list_sequences() if not x["error"]]


def _sequence_list():
    """The Sequences section of the side panel: favourites first, then by name; broken files too."""
    from . import sequences, workspace
    favs = workspace.sequence_favorites()
    out = [dict(s, favorite=s["slug"] in favs) for s in sequences.list_sequences()]
    return sorted(out, key=lambda s: (not s["favorite"], s["name"].lower()))


def _folder():
    from . import workspace
    return workspace.overview()


def _folder_palette(folder):
    return [{"path": x["path"], "name": x["name"], "favorite": x["favorite"]}
            for x in folder["favorites"] + folder["recent"] if x["exists"] and not x["is_open"]]


def common(request):
    import config
    from . import desktop
    from .process_manager import manager

    from . import discovery

    match = getattr(request, "resolver_match", None)
    counts = manager.counts()
    cfg = config.load_config()
    tree = SimpleLazyObject(_tree)
    folder = SimpleLazyObject(_folder)
    return {
        "app_version": APP_VERSION,
        "desktop": desktop.enabled(),
        "folder_picker": desktop.can_pick_folder(request),   # "Open folder…" = system dialog
        "editing_enabled": cfg["enable_script_editing"],
        "nav_active": (match.url_name or "").split("_")[0] if match else "",
        "url_name": (match.url_name or "") if match else "",
        "polling_interval": cfg["polling_interval"],
        "active_runs": counts["running"] + counts["queued"],
        "live_running": counts["running"],
        "live_queued": counts["queued"],
        "wb_tree": tree,
        "wb_runs": SimpleLazyObject(_runs),
        "wb_folder": folder,                         # open script folder, favourites, recent
        "wb_folder_palette": lambda: _folder_palette(folder),
        # Background folder scan progress; read late in the page (after the tree started it).
        "wb_scan": SimpleLazyObject(discovery.status),
        # A plain callable: templates call it, and json_script then gets a real list.
        "wb_palette": lambda: _palette(tree),
        "wb_sequences": _sequences,
        "wb_sequence_list": _sequence_list,
    }
