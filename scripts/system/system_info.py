# --- scripthub ---
# {
#   "name": "System Information",
#   "description": "Show hostname, operating system, Python version and current time.",
#   "category": "System",
#   "parameters": []
# }
# --- end scripthub ---
"""Print basic information about the machine the script runs on."""
import datetime
import platform
import socket
import sys

print(f"Hostname:       {socket.gethostname()}")
print(f"Operating sys.: {platform.system()} {platform.release()} ({platform.version()})")
print(f"Python version: {sys.version.split()[0]}")
print(f"Current time:   {datetime.datetime.now().isoformat(timespec='seconds')}")
