#!/usr/bin/env bash
# Run ONE of a fixed list of Django management commands in a project.
#
# Only the commands below are allowed (no arbitrary shell, no dbshell), and the
# extra arguments are limited to simple tokens such as --plan or myapp.
#
# Usage: django_manage.sh --project-dir DIR --venv DIR --command NAME
#          [--args "TOKENS"] [--settings MODULE]
set -euo pipefail

PROJECT_DIR="" VENV="" COMMAND="" EXTRA="" SETTINGS=""

die()  { echo "ERROR: $*" >&2; exit 2; }
need() { [ $# -ge 2 ] || die "option $1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir) need "$@"; PROJECT_DIR="$2"; shift 2 ;;
    --venv)        need "$@"; VENV="$2"; shift 2 ;;
    --command)     need "$@"; COMMAND="$2"; shift 2 ;;
    --args)        need "$@"; EXTRA="$2"; shift 2 ;;
    --settings)    need "$@"; SETTINGS="$2"; shift 2 ;;
    -h|--help)     sed -n '2,10p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$PROJECT_DIR" ] && [ -n "$VENV" ] && [ -n "$COMMAND" ] || die "--project-dir, --venv and --command are required"
[ -f "$PROJECT_DIR/manage.py" ] || die "manage.py not found in $PROJECT_DIR"
case "$SETTINGS" in *[!A-Za-z0-9._]*) die "invalid settings module: $SETTINGS" ;; esac

fixed=()
case "$COMMAND" in
  check|showmigrations|createcachetable|clearsessions) ;;
  migrate|collectstatic) fixed=(--noinput) ;;
  *) die "command not allowed: $COMMAND (allowed: check, showmigrations, migrate, collectstatic, createcachetable, clearsessions)" ;;
esac

# Extra arguments: split on spaces, no globbing, every token must be "simple".
extra=()
set -f
for token in $EXTRA; do
  case "$token" in
    *[!A-Za-z0-9_.=,:/-]*) die "argument not allowed: $token" ;;   # any other character, anywhere
  esac
  extra+=("$token")
done
set +f

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
[ -x "$PY" ] || die "no Python found in the virtual environment: $VENV"
[ -z "$SETTINGS" ] || export DJANGO_SETTINGS_MODULE="$SETTINGS"

cd "$PROJECT_DIR"
echo "Project: $PROJECT_DIR"
echo "Running: manage.py $COMMAND ${fixed[*]:-} ${extra[*]:-}"
echo "----------------------------------------"
exec "$PY" manage.py "$COMMAND" "${fixed[@]}" "${extra[@]}"
