#!/usr/bin/env bash
# --- scripthub ---
# {
#   "name": "Service: Status / Restart",
#   "description": "Check, start, stop, restart or reload a systemd service (gunicorn, nginx, celery...) and read its recent log.",
#   "category": "Deployment",
#   "parameters": [
#     {"name": "service1", "label": "Service name", "type": "text", "required": true, "editable_at_runtime": true, "help": "As used with systemctl, e.g. nginx or gunicorn-myapp.", "placeholder": "gunicorn-myapp", "pattern": "[A-Za-z0-9][A-Za-z0-9@._-]*", "pass_as": "flag"},
#     {"name": "action", "label": "Action", "type": "select", "default": "status", "required": true, "editable_at_runtime": true, "choices": [{"value": "status", "label": "Status"}, {"value": "logs", "label": "Show recent log"}, {"value": "restart", "label": "Restart"}, {"value": "reload", "label": "Reload (graceful)"}, {"value": "start", "label": "Start"}, {"value": "stop", "label": "Stop"}], "pass_as": "flag"},
#     {"name": "lines", "label": "Log lines", "type": "number", "default": 50, "required": false, "editable_at_runtime": true, "help": "Used by “Show recent log”.", "min": 1, "max": 5000, "pass_as": "flag"}
#   ]
# }
# --- end scripthub ---
# Status / start / stop / restart / reload a systemd service, or show its log.
#
# Usage: service_control.sh --service NAME --action ACTION [--lines N]
#   ACTION: status | start | stop | restart | reload | logs
#
# start/stop/restart/reload need root or passwordless sudo on the server.
set -u

SERVICE="" ACTION="" LINES=50

die()  { echo "ERROR: $*" >&2; exit 2; }
need() { [ $# -ge 2 ] || die "option $1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --service) need "$@"; SERVICE="$2"; shift 2 ;;
    --action)  need "$@"; ACTION="$2"; shift 2 ;;
    --lines)   need "$@"; LINES="$2"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$SERVICE" ] && [ -n "$ACTION" ] || die "--service and --action are required"
case "$SERVICE" in -*|*[!A-Za-z0-9@._-]*) die "invalid service name: $SERVICE" ;; esac
case "$LINES" in ''|*[!0-9]*) die "--lines must be a number" ;; esac
[ "$LINES" -ge 1 ] && [ "$LINES" -le 5000 ] || die "--lines must be between 1 and 5000"
command -v systemctl >/dev/null 2>&1 || die "systemctl is not available on this host"

priv() {
  if [ "$(id -u)" -eq 0 ]; then "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo -n "$@"
  else echo "ERROR: this action needs root or passwordless sudo" >&2; return 77
  fi
}

case "$ACTION" in
  status)
    systemctl status --no-pager --lines=15 "$SERVICE"
    code=$?
    # systemctl status exits 3 for a stopped service: that is information, not an error.
    [ "$code" -eq 3 ] && { echo; echo "(service is not running)"; exit 0; }
    exit "$code" ;;
  start|stop|restart|reload)
    echo "==> systemctl $ACTION $SERVICE"
    priv systemctl "$ACTION" "$SERVICE" || exit $?
    sleep 1
    systemctl is-active "$SERVICE" || true
    echo
    systemctl status --no-pager --lines=8 "$SERVICE" || true
    if [ "$ACTION" = stop ]; then exit 0; fi
    systemctl is-active --quiet "$SERVICE" || { echo "ERROR: $SERVICE is not active" >&2; exit 1; } ;;
  logs)
    command -v journalctl >/dev/null 2>&1 || die "journalctl is not available"
    journalctl -u "$SERVICE" -n "$LINES" --no-pager ;;
  *) die "unknown action: $ACTION (use status, start, stop, restart, reload or logs)" ;;
esac
