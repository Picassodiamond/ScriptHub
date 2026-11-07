"""SSH helpers. SSH itself is configured outside the app (~/.ssh/config, keys,
agent); we only list host aliases and shell out to the system ``ssh``."""
import glob
import os
import re
import shlex
import subprocess
from pathlib import Path

import config

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,254}$")
LOCAL = "local"


def _ssh_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".ssh"


def _parse_file(path: Path, hosts: set, depth: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if not parts:
            continue
        key = parts[0].lower().rstrip("=")
        if key == "host":
            for alias in parts[1:]:
                if not any(c in alias for c in "*?!") and HOST_RE.match(alias):
                    hosts.add(alias)
        elif key == "include" and depth < 3:
            for pattern in parts[1:]:
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = str(_ssh_dir() / pattern)
                for match in sorted(glob.glob(pattern)):
                    _parse_file(Path(match), hosts, depth + 1)


def list_hosts() -> list:
    """Concrete ``Host`` aliases from ~/.ssh/config (wildcards are skipped)."""
    hosts: set = set()
    _parse_file(_ssh_dir() / "config", hosts, 0)
    return sorted(hosts, key=str.lower)


def is_valid_host(host: str) -> bool:
    return host == LOCAL or bool(isinstance(host, str) and HOST_RE.match(host))


def allowed_hosts(extra=()) -> list:
    """'local' plus configured aliases (plus any alias a script names itself)."""
    out = [LOCAL] + list_hosts()
    for h in extra:
        if h and h not in out and is_valid_host(h):
            out.append(h)
    return out


def split_command(command: str) -> list:
    """Split a command line into arguments (Windows-path friendly)."""
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":  # non-POSIX mode keeps the quotes; drop one surrounding pair
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def ssh_base(cfg=None) -> list:
    """The ssh command with non-interactive options. Never prompts."""
    cfg = cfg or config.load_config()
    exe = split_command(cfg["ssh_executable"])
    return exe + [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={int(cfg['ssh_connect_timeout'])}",
    ]


def run_remote(host: str, remote_command: str, timeout: int = 30, cfg=None):
    """Run one short command on ``host``. Returns CompletedProcess (text)."""
    if not is_valid_host(host) or host == LOCAL:
        raise ValueError("Invalid SSH host")
    args = ssh_base(cfg) + ["-T", "--", host, remote_command]
    return subprocess.run(
        args, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def test_connection(host: str, cfg=None):
    """(ok, message) after a quick non-interactive login."""
    try:
        done = run_remote(host, "uname -sr 2>/dev/null || echo connected", timeout=40, cfg=cfg)
    except FileNotFoundError:
        return False, "The ssh program was not found. Install OpenSSH or set ssh_executable."
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for the host."
    except ValueError as exc:
        return False, str(exc)
    if done.returncode == 0:
        return True, (done.stdout.strip().splitlines() or ["connected"])[0]
    err = (done.stderr.strip().splitlines() or ["unknown error"])[-1]
    return False, f"ssh exited with {done.returncode}: {err}"
