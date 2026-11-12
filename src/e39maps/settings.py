"""
Django settings for e39maps project.

Configuration is driven by environment variables so the same code runs in
development and on the (Yocto-built) Raspberry Pi image without editing files
at build time. See README.md for the deployment notes.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from pathlib import Path
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core ---------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", default=True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
secret_key_file = os.environ.get("DJANGO_SECRET_KEY_FILE")
if not SECRET_KEY and secret_key_file:
    try:
        SECRET_KEY = Path(secret_key_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Could not read DJANGO_SECRET_KEY_FILE ({secret_key_file}): {exc}"
        ) from exc
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-only-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG=0")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

# A development admin account "user:password", created after migrate if it
# doesn't exist yet (core/dev_admin.py). Development defaults to
# admin:e39rpi; production gets none unless set; "" turns it off.
DEV_ADMIN = os.environ.get("DJANGO_DEV_ADMIN", "admin:e39rpi" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Trust X-Forwarded-Proto when running behind a reverse proxy.
if env_bool("DJANGO_BEHIND_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Enable when the site is served over HTTPS (directly or via a proxy).
if env_bool("DJANGO_HTTPS", default=False):
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# --- Applications -------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "mapdata",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Serves collected static files straight from the app process. Cheap on a
    # Raspberry Pi: no nginx (or extra process) required. In development,
    # Django's own staticfiles handler takes care of it.
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "e39maps.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "e39maps.wsgi.application"


# --- Databases ----------------------------------------------------------
# Two SQLite databases (see e39maps/routers.py):
# - "default": writable state (users, admin, sessions, user-made POIs).
#   Survives map updates. WAL etc. are set per connection in e39maps/sqlite.py:
#   Django 5.0, the image's version, has no OPTIONS["init_command"].
# - "mapdata": the read-only map database built by the map toolkit, opened
#   in place and immutable (no writes, no -wal/-shm files next to it), so a
#   map update just replaces that one file.
#
# DJANGO_DB_PATH is the old name of DJANGO_STATE_DB_PATH, still accepted.

STATE_DB_PATH = os.environ.get(
    "DJANGO_STATE_DB_PATH", os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"))
MAP_DB_PATH = Path(os.environ.get(
    "DJANGO_MAP_DB_PATH", BASE_DIR.parent / "output" / "db" / "e39maps-osm.sqlite"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": STATE_DB_PATH,
        "OPTIONS": {"timeout": 20},
    },
    "mapdata": {
        "ENGINE": "django.db.backends.sqlite3",
        # Django opens SQLite with uri=True, so a file: URI works here.
        "NAME": f"file:{quote(str(MAP_DB_PATH))}?mode=ro&immutable=1",
    },
}

DATABASE_ROUTERS = ["e39maps.routers.MapDataRouter"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Authentication -----------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization ----------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static files -------------------------------------------------------
# `manage.py collectstatic` writes here; WhiteNoise serves it in production.
# Point this at a writable location on the device if the app dir is read-only
# (e.g. /var/lib/e39maps/static).

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}
