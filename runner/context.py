def common(request):
    import config

    match = getattr(request, "resolver_match", None)
    return {
        "nav_active": (match.url_name or "").split("_")[0] if match else "",
        "polling_interval": config.load_config()["polling_interval"],
    }
