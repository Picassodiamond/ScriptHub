"""Validation of application configuration (and, later, metadata/parameters)."""
import re

EXT_RE = re.compile(r"^\.[A-Za-z0-9_]{1,10}$")


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _int(data, key, lo, hi, errors, label):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be a whole number.")
        return None
    if not lo <= value <= hi:
        errors.append(f"{label} must be between {lo} and {hi}.")
        return None
    return value


def validate_config(data: dict) -> dict:
    """Return a cleaned copy of ``data`` or raise ValidationError."""
    errors = []
    clean = dict(data)

    dirs = data.get("script_directories")
    if not isinstance(dirs, list):
        errors.append("script_directories must be a list.")
    else:
        seen, out = set(), []
        for d in dirs:
            if not isinstance(d, str) or not d.strip():
                errors.append("Every script directory must be a non-empty path.")
                continue
            d = d.strip()
            if d not in seen:
                seen.add(d)
                out.append(d)
        clean["script_directories"] = out

    interp = data.get("interpreters")
    if not isinstance(interp, dict) or not interp:
        errors.append("interpreters must map at least one file extension to an interpreter.")
    else:
        out = {}
        for ext, cmd in interp.items():
            ext_n = ext.lower() if isinstance(ext, str) else ext
            if not isinstance(ext_n, str) or not EXT_RE.match(ext_n):
                errors.append(f"Invalid file extension: {ext!r} (use e.g. .py).")
            elif not isinstance(cmd, str) or not cmd.strip():
                errors.append(f"Interpreter for {ext_n} must be a non-empty command or path.")
            else:
                out[ext_n] = cmd.strip()
        clean["interpreters"] = out

    for key, lo, hi, label in (
        ("max_concurrent_executions", 1, 64, "Max concurrent executions"),
        ("default_timeout", 1, 7 * 86400, "Default timeout (seconds)"),
        ("history_retention_days", 1, 36500, "History retention (days)"),
        ("log_retention_days", 1, 36500, "Log retention (days)"),
        ("polling_interval", 500, 60000, "Polling interval (ms)"),
    ):
        clean[key] = _int(data, key, lo, hi, errors, label)

    if errors:
        raise ValidationError(errors)
    return clean
