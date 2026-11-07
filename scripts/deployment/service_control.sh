#!/usr/bin/env bash
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
