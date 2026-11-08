#!/usr/bin/env bash
# --- scripthub ---
# {
#   "name": "Django: Deploy",
#   "description": "Update a Django project on the server: git pull, install requirements (online or offline), check, migrate, collectstatic and restart the service. Stops at the first failing step.",
#   "category": "Django",
#   "parameters": [
#     {"name": "project_dir", "label": "Project directory", "type": "directory", "required": true, "placeholder": "/srv/myapp", "help": "The folder that contains manage.py."},
#     {"name": "venv", "label": "Virtual environment", "type": "directory", "required": true, "placeholder": "/srv/myapp/venv"},
#     {"name": "git_pull", "label": "Pull the latest code (git)", "type": "boolean", "default": true},
#     {"name": "branch", "label": "Branch", "type": "text", "placeholder": "main", "pattern": "[A-Za-z0-9][A-Za-z0-9._/-]*", "help": "Optional. Fetch and switch to this branch before pulling."},
#     {"name": "requirements", "label": "Requirements file", "type": "text", "default": "requirements.txt", "help": "Relative to the project directory, or a full path."},
#     {"name": "wheelhouse", "label": "Offline wheelhouse", "type": "directory", "placeholder": "/srv/wheelhouse", "help": "Optional. If set, packages are installed from this folder with no internet."},
#     {"name": "migrate", "label": "Apply migrations", "type": "boolean", "default": true},
#     {"name": "collectstatic", "label": "Collect static files", "type": "boolean", "default": true},
#     {"name": "settings", "label": "Settings module", "type": "text", "placeholder": "config.settings.production", "pattern": "[A-Za-z0-9_.]+", "help": "Optional DJANGO_SETTINGS_MODULE."},
#     {"name": "service", "label": "systemd service to restart", "type": "text", "placeholder": "gunicorn-myapp", "pattern": "[A-Za-z0-9][A-Za-z0-9@._-]*", "help": "Optional. Needs root or passwordless sudo on the server."}
#   ]
# }
# --- end scripthub ---
# Deploy a Django project that is already on this server.
#
# Steps (each optional step is a flag):
#   git pull -> install requirements (online, or offline from a wheelhouse)
#   -> manage.py check -> migrate -> collectstatic -> restart the service
#
# Stops at the first failing step, so a broken migration never reaches the
# service restart. Run it through ScriptHub over SSH, or directly.
#
# Usage: django_deploy.sh --project-dir DIR --venv DIR [--requirements FILE]
#          [--wheelhouse DIR] [--git-pull] [--branch NAME] [--migrate]
#          [--collectstatic] [--settings MODULE] [--service NAME]
set -euo pipefail

PROJECT_DIR="" VENV="" REQ="requirements.txt" WHEELHOUSE="" BRANCH="" SETTINGS="" SERVICE=""
GIT_PULL=0 MIGRATE=0 COLLECTSTATIC=0

die()  { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }
need() { [ $# -ge 2 ] || die "option $1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir)   need "$@"; PROJECT_DIR="$2"; shift 2 ;;
    --venv)          need "$@"; VENV="$2"; shift 2 ;;
    --requirements)  need "$@"; REQ="$2"; shift 2 ;;
    --wheelhouse)    need "$@"; WHEELHOUSE="$2"; shift 2 ;;
    --branch)        need "$@"; BRANCH="$2"; shift 2 ;;
    --settings)      need "$@"; SETTINGS="$2"; shift 2 ;;
    --service)       need "$@"; SERVICE="$2"; shift 2 ;;
    --git-pull)      GIT_PULL=1; shift ;;
    --migrate)       MIGRATE=1; shift ;;
    --collectstatic) COLLECTSTATIC=1; shift ;;
    -h|--help)       sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$PROJECT_DIR" ] || die "--project-dir is required"
[ -n "$VENV" ]        || die "--venv is required"
[ -d "$PROJECT_DIR" ] || die "project directory not found: $PROJECT_DIR"
[ -f "$PROJECT_DIR/manage.py" ] || die "manage.py not found in $PROJECT_DIR"
case "$BRANCH"  in ""|-*|*[!A-Za-z0-9._/-]*) [ -z "$BRANCH" ]  || die "invalid branch name: $BRANCH" ;; esac
case "$SERVICE" in ""|-*|*[!A-Za-z0-9@._-]*) [ -z "$SERVICE" ] || die "invalid service name: $SERVICE" ;; esac
case "$SETTINGS" in *[!A-Za-z0-9._]*) die "invalid settings module: $SETTINGS" ;; esac

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
[ -x "$PY" ] || die "no Python found in the virtual environment: $VENV"
[ -z "$SETTINGS" ] || export DJANGO_SETTINGS_MODULE="$SETTINGS"

run_systemctl() {
  if [ "$(id -u)" -eq 0 ]; then systemctl "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo -n systemctl "$@"
  else die "restarting a service needs root or passwordless sudo"
  fi
}

cd "$PROJECT_DIR"
echo "Project: $PROJECT_DIR"
echo "Python:  $("$PY" --version 2>&1)"

if [ "$GIT_PULL" -eq 1 ]; then
  step "Updating the code (git)"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$PROJECT_DIR is not a git repository"
  before="$(git rev-parse --short HEAD)"
  if [ -n "$BRANCH" ]; then
    git fetch origin "$BRANCH"
    git checkout "$BRANCH"
  fi
  git pull --ff-only
  after="$(git rev-parse --short HEAD)"
  echo "HEAD: $before -> $after"
fi

if [ -f "$REQ" ]; then
  if [ -n "$WHEELHOUSE" ]; then
    step "Installing requirements OFFLINE from $WHEELHOUSE"
    [ -d "$WHEELHOUSE" ] || die "wheelhouse not found: $WHEELHOUSE"
    "$PY" -m pip install --disable-pip-version-check --no-index --find-links "$WHEELHOUSE" -r "$REQ"
  else
    step "Installing requirements from the package index"
    "$PY" -m pip install --disable-pip-version-check -r "$REQ"
  fi
else
  echo; echo "(no $REQ found - skipping package installation)"
fi

step "Checking the project (manage.py check)"
"$PY" manage.py check

if [ "$MIGRATE" -eq 1 ]; then
  step "Applying database migrations"
  "$PY" manage.py migrate --noinput
fi

if [ "$COLLECTSTATIC" -eq 1 ]; then
  step "Collecting static files"
  "$PY" manage.py collectstatic --noinput
fi

if [ -n "$SERVICE" ]; then
  step "Restarting service $SERVICE"
  run_systemctl restart "$SERVICE"
  sleep 2
  if run_systemctl is-active --quiet "$SERVICE"; then
    echo "$SERVICE is active"
  else
    run_systemctl status --no-pager --lines=15 "$SERVICE" || true
    die "$SERVICE is not running after the restart"
  fi
fi

echo; echo "Deployment finished OK"
