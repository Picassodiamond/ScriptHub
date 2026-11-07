def common(request):
    import config
    from .process_manager import manager

    match = getattr(request, "resolver_match", None)
    counts = manager.counts()
    return {
        "nav_active": (match.url_name or "").split("_")[0] if match else "",
        "polling_interval": config.load_config()["polling_interval"],
        "active_runs": counts["running"] + counts["queued"],
    }
