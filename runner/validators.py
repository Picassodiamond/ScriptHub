"""Validation of application configuration (and, later, metadata/parameters)."""
import re

EXT_RE = re.compile(r"^\.[A-Za-z0-9_]{1,10}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


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

    dirs = data.get("script_directories", [])     # legacy, only seeds runner/workspace.py
    if not isinstance(dirs, list):
        errors.append("script_directories must be a list.")
    elif "script_directories" in data:
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

    remote = data.get("remote_interpreters")
    if not isinstance(remote, dict):
        errors.append("remote_interpreters must map file extensions to interpreters.")
    else:
        out = {}
        for ext, cmd in remote.items():
            ext_n = ext.lower() if isinstance(ext, str) else ext
            if not isinstance(ext_n, str) or not EXT_RE.match(ext_n):
                errors.append(f"Invalid remote file extension: {ext!r}.")
            elif not isinstance(cmd, str) or not cmd.strip():
                errors.append(f"Remote interpreter for {ext_n} must be non-empty.")
            else:
                out[ext_n] = cmd.strip()
        clean["remote_interpreters"] = out

    ssh = data.get("ssh_executable")
    if not isinstance(ssh, str) or not ssh.strip():
        errors.append("ssh_executable must be a command or path.")
    else:
        clean["ssh_executable"] = ssh.strip()

    env = data.get("environment")
    if not isinstance(env, dict):
        errors.append("environment must be an object of NAME: value pairs.")
    else:
        env_out = {}
        for k, v in env.items():
            if not isinstance(k, str) or not ENV_NAME_RE.match(k):
                errors.append(f"Invalid environment variable name: {k!r}.")
            elif not isinstance(v, str) or "\x00" in v:
                errors.append(f"Environment variable {k} must have a text value.")
            else:
                env_out[k] = v
        clean["environment"] = env_out

    clean.pop("enable_adhoc", None)          # the Ad-hoc screen was removed
    if not isinstance(data.get("enable_script_editing"), bool):
        errors.append("enable_script_editing must be true or false.")

    for key, lo, hi, label in (
        ("max_concurrent_executions", 1, 64, "Max concurrent executions"),
        ("max_concurrent_per_host", 1, 64, "Max concurrent executions per host"),
        ("ssh_connect_timeout", 1, 300, "SSH connect timeout (seconds)"),
        ("default_timeout", 1, 7 * 86400, "Default timeout (seconds)"),
        ("history_retention_days", 1, 36500, "History retention (days)"),
        ("log_retention_days", 1, 36500, "Log retention (days)"),
        ("polling_interval", 500, 60000, "Polling interval (ms)"),
    ):
        clean[key] = _int(data, key, lo, hi, errors, label)

    if errors:
        raise ValidationError(errors)
    return clean
