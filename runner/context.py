def common(request):
    import config
    from .process_manager import manager

    match = getattr(request, "resolver_match", None)
    counts = manager.counts()
    cfg = config.load_config()
    return {
        "adhoc_enabled": cfg["enable_adhoc"],
        "editing_enabled": cfg["enable_script_editing"],
        "nav_active": (match.url_name or "").split("_")[0] if match else "",
        "polling_interval": config.load_config()["polling_interval"],
        "active_runs": counts["running"] + counts["queued"],
    }
