#!/usr/bin/env bash
# --- scripthub ---
# {
#   "name": "Django: Health Check",
#   "description": "Run manage.py check --deploy, confirm no migrations are pending, and optionally check the systemd service and an HTTP URL. Prints a pass/fail summary.",
#   "category": "Django",
#   "parameters": [
#     {"name": "project_dir", "label": "Project directory", "type": "directory", "required": true, "placeholder": "/srv/myapp"},
#     {"name": "venv", "label": "Virtual environment", "type": "directory", "required": true, "placeholder": "/srv/myapp/venv"},
#     {"name": "settings", "label": "Settings module", "type": "text", "placeholder": "config.settings.production", "pattern": "[A-Za-z0-9_.]+"},
#     {"name": "service", "label": "systemd service", "type": "text", "placeholder": "gunicorn-myapp", "pattern": "[A-Za-z0-9][A-Za-z0-9@._-]*"},
#     {"name": "url", "label": "URL to probe", "type": "text", "placeholder": "https://example.com/health/", "pattern": "https?://\\S+", "help": "Optional. A 2xx or 3xx answer counts as healthy."}
#   ]
# }
# --- end scripthub ---
# Health check for a deployed Django project. Runs every check and prints a
# summary; exits non-zero if any check failed.
#
#   * manage.py check --deploy   (warnings are shown, errors fail)
#   * manage.py migrate --check  (fails when migrations are pending)
#   * systemd service is active  (optional)
#   * HTTP probe of a URL        (optional)
#
# Usage: django_check.sh --project-dir DIR --venv DIR [--settings MODULE]
#          [--service NAME] [--url URL]
set -u

PROJECT_DIR="" VENV="" SETTINGS="" SERVICE="" URL=""

die()  { echo "ERROR: $*" >&2; exit 2; }
need() { [ $# -ge 2 ] || die "option $1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir) need "$@"; PROJECT_DIR="$2"; shift 2 ;;
    --venv)        need "$@"; VENV="$2"; shift 2 ;;
    --settings)    need "$@"; SETTINGS="$2"; shift 2 ;;
    --service)     need "$@"; SERVICE="$2"; shift 2 ;;
    --url)         need "$@"; URL="$2"; shift 2 ;;
    -h|--help)     sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$PROJECT_DIR" ] && [ -n "$VENV" ] || die "--project-dir and --venv are required"
[ -f "$PROJECT_DIR/manage.py" ] || die "manage.py not found in $PROJECT_DIR"
case "$SERVICE" in ""|-*|*[!A-Za-z0-9@._-]*) [ -z "$SERVICE" ] || die "invalid service name: $SERVICE" ;; esac
case "$SETTINGS" in *[!A-Za-z0-9._]*) die "invalid settings module: $SETTINGS" ;; esac
case "$URL" in ""|http://*|https://*) ;; *) die "the URL must start with http:// or https://" ;; esac

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
[ -x "$PY" ] || die "no Python found in the virtual environment: $VENV"
[ -z "$SETTINGS" ] || export DJANGO_SETTINGS_MODULE="$SETTINGS"
cd "$PROJECT_DIR" || die "cannot enter $PROJECT_DIR"

failures=0
report() { # name status detail
  if [ "$2" = ok ]; then printf '  [ OK ] %s %s\n' "$1" "$3"; else printf '  [FAIL] %s %s\n' "$1" "$3"; failures=$((failures + 1)); fi
}

echo "Project: $PROJECT_DIR"
echo "Python:  $("$PY" --version 2>&1)"
echo; echo "==> manage.py check --deploy"
if "$PY" manage.py check --deploy; then dep=ok; else dep=fail; fi

echo; echo "==> manage.py migrate --check"
if "$PY" manage.py migrate --check; then mig=ok; else mig=fail; fi

svc=skip
if [ -n "$SERVICE" ]; then
  echo; echo "==> service $SERVICE"
  if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then svc=ok; echo "$SERVICE is active"
  else svc=fail; systemctl status --no-pager --lines=10 "$SERVICE" 2>&1 | sed 's/^/    /' || true; fi
fi

web=skip
if [ -n "$URL" ]; then
  echo; echo "==> HTTP probe $URL"
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -- "$URL")" || code=000
    echo "HTTP status: $code"
    case "$code" in 2??|3??) web=ok ;; *) web=fail ;; esac
  else
    echo "curl is not installed"; web=fail
  fi
fi

echo; echo "Summary"
report "Django system check (--deploy)" "$dep" ""
report "Database migrations up to date" "$mig" ""
[ "$svc" = skip ] || report "Service $SERVICE" "$svc" ""
[ "$web" = skip ] || report "HTTP $URL" "$web" ""
if [ "$failures" -eq 0 ]; then echo; echo "All checks passed."; exit 0; fi
echo; echo "$failures check(s) failed." >&2
exit 1
