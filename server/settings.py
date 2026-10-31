"""Django の設定。

環境変数で変えられるもの:
- DATABASE_URL: sqlite:///db.sqlite3（既定）または postgres://user:pass@host:5432/name
- DJANGO_SECRET_KEY: 公開サーバーでは必ず設定する
- DJANGO_DEBUG: 1 で開発モード（既定は 1。公開サーバーでは 0）
- DJANGO_ALLOWED_HOSTS: カンマ区切り
- DJANGO_SECURE_HSTS_SECONDS: HSTS の秒数（既定 0。本番はまず 3600 など短い期間から）
- DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS / DJANGO_SECURE_HSTS_PRELOAD: 既定はともに 0
- DJANGO_SECURE_SSL_REDIRECT: Django で https へ転送する（既定 0）
- DJANGO_SESSION_COOKIE_SECURE / DJANGO_CSRF_COOKIE_SECURE: cookie を https だけにする（既定 0）
- DJANGO_TRUST_X_FORWARDED_PROTO: プロキシが上書きする X-Forwarded-Proto を信じる（既定 0）。
  Django へ直接外から届かず、信頼するプロキシが見出しを必ず上書きする構成だけで 1 にする
- DJANGO_HTTPS_REDIRECT_AT_PROXY: 外側のプロキシで http → https を転送済み（既定 0）。
  1 かつ Django の転送が無効なときだけ、理由をこの宣言に限定して security.W008 を消す。
  Cloudflare 等で転送が実際に有効であることを確認してから設定する
  （上記の真偽値は 1/0、true/false、yes/no、on/off。大文字小文字は問わない）
- HAKONIWA_GAME_CONFIG: game.yaml の場所（既定はリポジトリ直下）
- HAKONIWA_CLIENT_IP_HEADER: 利用者の IP を読む場所（既定 REMOTE_ADDR。リバースプロキシの後ろでは
  HTTP_CF_CONNECTING_IP・HTTP_X_REAL_IP・HTTP_X_FORWARDED_FOR など、プロキシが書き込む場所にする）
- HAKONIWA_TRUSTED_PROXIES: 上の見出しを信じてよい中継元の IP / CIDR（カンマ区切り。既定は同じ機械
  の 127.0.0.1, ::1）。ここにない相手から来た見出しは使わず、つないできた相手の IP（REMOTE_ADDR）を
  接続元にする（利用者が見出しを偽って回数制限をすり抜けるのを防ぐ。server/accounts/clientip.py）
- HAKONIWA_LOGIN_MAX_FAILURES: 同じ接続元から同じアカウントへのログインに失敗できる回数（既定 5）。
  この回数に達したら HAKONIWA_LOGIN_LOCK_MINUTES 分の間、その組み合わせのログインを断る
- HAKONIWA_LOGIN_LOCK_MINUTES: 上の停止の長さ（分。既定 15）
- HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES: 失敗を数える期間（分。最初の失敗からこの間に回数に達する
  と停止。既定 15）
- HAKONIWA_LOGIN_POSTS_PER_WINDOW: 同じ接続元がログインの画面（通常・管理画面）に送れる回数
  （アカウント名を変えても合算。既定 30）
- HAKONIWA_LOGIN_POST_WINDOW_SECONDS: 上の回数を数える区切りの秒（既定 60）
- HAKONIWA_THROTTLE_KEEP_HOURS: 回数の記録（失敗と区切りごとの数）を消すまでの時間（既定 24）
- HAKONIWA_LETTERS_PER_DAY: 1つの島が 24 時間に送れる手紙の数（既定 50。送りすぎの歯止め）
- HAKONIWA_HALL_PER_DAY: 1つの島が 24 時間に国際会議場に出せる声明の数（既定 10。出しすぎの歯止め）
- HAKONIWA_BUG_REPORTS_PER_DAY: 1人が 24 時間に送れる不具合の報告の数（既定 10。送りすぎの歯止め）
- HAKONIWA_VERSION: ゲームの版（既定は git のコミットの頭 12 字）。不具合の報告に添え、
  静的ファイルの URL（/static/v-<版>/…）にも入れる。版が変わると URL が変わり、
  ブラウザや中継が古い CSS・JS を使わない
- HAKONIWA_BOARD_NEWCOMER_MINUTES: 登録してから掲示板に書けるまでの分（既定 1。0 で待たない）
- HAKONIWA_BOARD_INTERVAL_SECONDS: 掲示板の連投の間隔（秒。既定 10）
- HAKONIWA_BOARD_POSTS_PER_DAY: 1 人が 24 時間に掲示板に書ける数（既定 200）
- HAKONIWA_BOARD_THREADS_PER_DAY: 1 人が 24 時間に立てられるスレッドの数（既定 5）
- HAKONIWA_FLAG_MAX_KB: 国旗の PNG の大きさの上限（KB。既定 100）
- HAKONIWA_FLAG_MAX_SIDE: 国旗の PNG の縦横の上限（ピクセル。既定 512）
- HAKONIWA_COUNTRY_NOTES_PER_DAY: 1つの国が 24 時間に他国のページに残せる一言の数（既定 10）
- HAKONIWA_UPDATE_STALE_MINUTES: 鎖の取れない DB（SQLite）で、更新中の印がこの分より古ければ
  強制終了などで残ったものとみなして外す（既定 30。ターンの計算がそれより長くかかる世界では
  大きくする。PostgreSQL では鎖で見分けるので使わない。server/turns/services.py）
- HAKONIWA_AI_CALLS_PER_MINUTE: AI 用の鍵1つで 1 分に使える処理の数（既定 60。0 で上限なし。
  /api/ の呼び出しは 1 回に 1、/mcp は含まれるメッセージの数。server/accounts/keys.py）
- HAKONIWA_MCP_MAX_BATCH: /mcp に 1 回の送信でまとめて送れるメッセージの数（既定 10。超えたら 1 件も
  実行せず断る。上の 0 でも適用）
"""

from __future__ import annotations

import os
import re
import subprocess
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


def env_bool(name: str, default: bool = False) -> bool:
    """真偽値の設定ミスを、無言で無効にせず起動時に知らせる。"""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} は 1/0、true/false、yes/no、on/off で設定してください")


# DEBUG=0 でも既定は開発・LAN の http と同じ。本番で明示して有効にする。
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
if SECURE_HSTS_SECONDS < 0:
    raise RuntimeError("DJANGO_SECURE_HSTS_SECONDS は 0 以上で設定してください")
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT")
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE")
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE")
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO") else None
)
# 外側での転送を確認した構成だけに限定する。他の警告は隠さない。
SILENCED_SYSTEM_CHECKS = (
    ["security.W008"]
    if env_bool("DJANGO_HTTPS_REDIRECT_AT_PROXY") and not SECURE_SSL_REDIRECT
    else []
)

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
    "server.feedback",
    "server.country",
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
    "server.accounts.loginguard.LoginGuardMiddleware",  # ログインの試しすぎの上限（通常・管理画面）
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
                "server.web.context.screen_help",
                "server.letters.context.unread_letters",
                "server.board.context.unread_replies",
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


def _game_version() -> str:
    """ゲームの版。HAKONIWA_VERSION、なければ git のコミットの頭 12 字。URL に使えない字なら空。"""
    version = os.environ.get("HAKONIWA_VERSION", "").strip()
    if not version:
        try:
            version = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return version if re.fullmatch(r"[0-9A-Za-z._-]{1,40}", version) else ""


# 静的ファイルの URL に版を入れる（/static/v-<版>/css/site.css）。ファイルの置き場所は版で変わらず、
# Caddy が版の段を外して返し、版の付いた URL は長く覚えさせる（tools/deploy/prod/caddy/Caddyfile）。
# JS の import は相対なので、1つの画面が読むファイルはみな同じ版にそろう。
HAKONIWA_VERSION = _game_version()
STATIC_URL = f"static/v-{HAKONIWA_VERSION}/" if HAKONIWA_VERSION else "static/"
STATICFILES_DIRS = [BASE_DIR / "server" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

HAKONIWA_GAME_CONFIG = os.environ.get("HAKONIWA_GAME_CONFIG", str(BASE_DIR / "game.yaml"))
HAKONIWA_CLIENT_IP_HEADER = os.environ.get("HAKONIWA_CLIENT_IP_HEADER", "REMOTE_ADDR")
HAKONIWA_TRUSTED_PROXIES = [
    p.strip()
    for p in os.environ.get("HAKONIWA_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if p.strip()
]
HAKONIWA_LOGIN_MAX_FAILURES = int(os.environ.get("HAKONIWA_LOGIN_MAX_FAILURES", "5"))
HAKONIWA_LOGIN_LOCK_MINUTES = int(os.environ.get("HAKONIWA_LOGIN_LOCK_MINUTES", "15"))
HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES = int(
    os.environ.get("HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES", "15")
)
HAKONIWA_LOGIN_POSTS_PER_WINDOW = int(os.environ.get("HAKONIWA_LOGIN_POSTS_PER_WINDOW", "30"))
HAKONIWA_LOGIN_POST_WINDOW_SECONDS = int(os.environ.get("HAKONIWA_LOGIN_POST_WINDOW_SECONDS", "60"))
HAKONIWA_THROTTLE_KEEP_HOURS = int(os.environ.get("HAKONIWA_THROTTLE_KEEP_HOURS", "24"))
HAKONIWA_LETTERS_PER_DAY = int(os.environ.get("HAKONIWA_LETTERS_PER_DAY", "50"))
HAKONIWA_HALL_PER_DAY = int(os.environ.get("HAKONIWA_HALL_PER_DAY", "10"))
HAKONIWA_BUG_REPORTS_PER_DAY = int(os.environ.get("HAKONIWA_BUG_REPORTS_PER_DAY", "10"))
HAKONIWA_BOARD_NEWCOMER_MINUTES = int(os.environ.get("HAKONIWA_BOARD_NEWCOMER_MINUTES", "1"))
HAKONIWA_BOARD_INTERVAL_SECONDS = int(os.environ.get("HAKONIWA_BOARD_INTERVAL_SECONDS", "10"))
HAKONIWA_BOARD_POSTS_PER_DAY = int(os.environ.get("HAKONIWA_BOARD_POSTS_PER_DAY", "200"))
HAKONIWA_BOARD_THREADS_PER_DAY = int(os.environ.get("HAKONIWA_BOARD_THREADS_PER_DAY", "5"))
HAKONIWA_FLAG_MAX_KB = int(os.environ.get("HAKONIWA_FLAG_MAX_KB", "100"))
HAKONIWA_FLAG_MAX_SIDE = int(os.environ.get("HAKONIWA_FLAG_MAX_SIDE", "512"))
HAKONIWA_COUNTRY_NOTES_PER_DAY = int(os.environ.get("HAKONIWA_COUNTRY_NOTES_PER_DAY", "10"))
HAKONIWA_UPDATE_STALE_MINUTES = int(os.environ.get("HAKONIWA_UPDATE_STALE_MINUTES", "30"))
HAKONIWA_AI_CALLS_PER_MINUTE = int(os.environ.get("HAKONIWA_AI_CALLS_PER_MINUTE", "60"))
HAKONIWA_MCP_MAX_BATCH = int(os.environ.get("HAKONIWA_MCP_MAX_BATCH", "10"))
