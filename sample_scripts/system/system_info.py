# --- scripthub ---
# {
#   "name": "System information",
#   "description": "Host name, operating system, Python, CPUs, uptime and the current user. Works locally and over SSH.",
#   "category": "System",
#   "parameters": [
#     {"name": "json", "label": "As JSON", "type": "boolean", "default": false}
#   ]
# }
# --- end scripthub ---
"""Print basic facts about the machine the script runs on."""
import argparse
import datetime
import getpass
import json
import os
import platform
import socket
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--json", action="store_true")
args = parser.parse_args()


def uptime():
    try:
        with open("/proc/uptime") as fh:
            return str(datetime.timedelta(seconds=int(float(fh.read().split()[0]))))
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
        ms = ctypes.windll.kernel32.GetTickCount64()
        return str(datetime.timedelta(seconds=ms // 1000))
    return "unknown"


info = {
    "host": socket.gethostname(),
    "os": f"{platform.system()} {platform.release()}",
    "version": platform.version(),
    "machine": platform.machine(),
    "cpus": os.cpu_count(),
    "python": sys.version.split()[0],
    "user": getpass.getuser(),
    "uptime": uptime(),
    "time": datetime.datetime.now().isoformat(timespec="seconds"),
}
if args.json:
    print(json.dumps(info, indent=2))
else:
    for key, value in info.items():
        print(f"{key:<8} {value}")
