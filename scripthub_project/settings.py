"""Django settings for ScriptHub.

There is deliberately NO database, NO sessions and NO auth: this is a
personal tool and all state lives in JSON files under config.RUNTIME_DIR.
"""
import os
import secrets
from pathlib import Path

import config

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = config.RUNTIME_DIR
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Persist a random key on disk so CSRF cookies survive restarts."""
    key_file = RUNTIME_DIR / "secret_key.txt"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_urlsafe(50)
        key_file.write_text(key, encoding="utf-8")
        return key


SECRET_KEY = _secret_key()
def _env(name: str, default: str = "") -> str:
    """SCRIPTHUB_<name>; the pre-rename SCRIPT_RUNNER_<name> is still accepted."""
    return os.environ.get(f"SCRIPTHUB_{name}", os.environ.get(f"SCRIPT_RUNNER_{name}", default))


DEBUG = _env("DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"] + [
    h for h in _env("ALLOWED_HOSTS").split(",") if h
]

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "runner.apps.RunnerConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "runner.desktop.DesktopTokenMiddleware",   # only active in desktop mode (see runner/desktop.py)
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# Flash messages live in a cookie (no session/database needed).
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

ROOT_URLCONF = "scripthub_project.urls"
WSGI_APPLICATION = "scripthub_project.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "runner.context.common",
            ],
        },
    },
]

# No database at all.
DATABASES = {}

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

USE_TZ = False
TIME_ZONE = "UTC"
USE_I18N = False
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
