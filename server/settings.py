"""Django の設定。

環境変数で変えられるもの:
- DATABASE_URL: sqlite:///db.sqlite3（既定）または postgres://user:pass@host:5432/name
- DJANGO_SECRET_KEY: 公開サーバーでは必ず設定する
- DJANGO_DEBUG: 1 で開発モード（既定は 1。公開サーバーでは 0）
- DJANGO_ALLOWED_HOSTS: カンマ区切り
- HAKONIWA_GAME_CONFIG: game.yaml の場所（既定はリポジトリ直下）
- HAKONIWA_CLIENT_IP_HEADER: 利用者の IP を読む場所（既定 REMOTE_ADDR。リバースプロキシの後ろでは
  HTTP_X_REAL_IP など、プロキシが書き込む場所にする）
- HAKONIWA_LETTERS_PER_DAY: 1つの島が 24 時間に送れる手紙の数（既定 50。送りすぎの歯止め）
- HAKONIWA_HALL_PER_DAY: 1つの島が 24 時間に国際会議場に出せる声明の数（既定 10。出しすぎの歯止め）
- HAKONIWA_AI_CALLS_PER_MINUTE: AI 用の鍵1つで 1 分に呼べる API の回数（既定 60。0 で上限なし。
  server/accounts/keys.py）
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY を設定してください")
    SECRET_KEY = "dev-only-insecure-key"
ALLOWED_HOSTS = [
    h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "server.accounts",
    "server.world",
    "server.economy",
    "server.orders",
    "server.turns",
    "server.board",
    "server.letters",
    "server.hall",
    "server.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "server.accounts.keys.KeyMiddleware",  # AI 用の鍵（/api/ と /mcp だけ）
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "server.urls"
WSGI_APPLICATION = "server.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "server" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "server.web.context.own_island",
                "server.letters.context.unread_letters",
            ],
        },
    },
]


def database_from_url(url: str) -> dict:
    """DATABASE_URL を Django の DATABASES の形に直す（SQLite と PostgreSQL だけ）。"""
    parsed = urlparse(url)
    if parsed.scheme == "sqlite":
        name = unquote(parsed.path[1:]) if parsed.path.startswith("/") else parsed.path
        path = Path(name)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path if path.is_absolute() else BASE_DIR / path,
        }
    if parsed.scheme in ("postgres", "postgresql"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }
    raise RuntimeError(f"DATABASE_URL の形式に対応していません: {parsed.scheme}")


DATABASES = {"default": database_from_url(os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3"))}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "top"
LOGOUT_REDIRECT_URL = "top"

LANGUAGE_CODE = "ja"
LANGUAGES = [("ja", "日本語")]
LOCALE_PATHS = [BASE_DIR / "server" / "locale"]
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "server" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

HAKONIWA_GAME_CONFIG = os.environ.get("HAKONIWA_GAME_CONFIG", str(BASE_DIR / "game.yaml"))
HAKONIWA_CLIENT_IP_HEADER = os.environ.get("HAKONIWA_CLIENT_IP_HEADER", "REMOTE_ADDR")
HAKONIWA_LETTERS_PER_DAY = int(os.environ.get("HAKONIWA_LETTERS_PER_DAY", "50"))
HAKONIWA_HALL_PER_DAY = int(os.environ.get("HAKONIWA_HALL_PER_DAY", "10"))
HAKONIWA_AI_CALLS_PER_MINUTE = int(os.environ.get("HAKONIWA_AI_CALLS_PER_MINUTE", "60"))
