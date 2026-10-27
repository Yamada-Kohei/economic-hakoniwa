import os
import runpy
from unittest.mock import patch

import pytest
from django.core.checks import run_checks
from django.test import Client, RequestFactory, override_settings

from server.settings import BASE_DIR, database_from_url


def load_settings(**env: str) -> dict:
    """実行中の Django 設定を変えず、環境変数からの読み込みを確かめる。"""
    with patch.dict(os.environ, env, clear=True):
        return {
            key: value
            for key, value in runpy.run_path(str(BASE_DIR / "server/settings.py")).items()
            if key.startswith(("SECURE_", "SESSION_COOKIE_", "CSRF_COOKIE_"))
            or key in {"DEBUG", "SECRET_KEY", "ALLOWED_HOSTS", "SILENCED_SYSTEM_CHECKS"}
        }


def production_settings(**env: str) -> dict:
    return load_settings(
        DJANGO_DEBUG="0",
        DJANGO_SECRET_KEY="settings-test-key-" + "a1b2c3d4e5" * 6,
        DJANGO_ALLOWED_HOSTS="testserver",
        DJANGO_SECURE_HSTS_SECONDS="3600",
        DJANGO_SESSION_COOKIE_SECURE="1",
        DJANGO_CSRF_COOKIE_SECURE="true",
        DJANGO_TRUST_X_FORWARDED_PROTO="1",
        **env,
    )


def test_sqlite_url():
    db = database_from_url("sqlite:///db.sqlite3")
    assert db["ENGINE"].endswith("sqlite3")
    assert db["NAME"] == BASE_DIR / "db.sqlite3"


def test_postgres_url():
    db = database_from_url("postgres://user:p%40ss@db.example:5433/hakoniwa")
    assert db == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "hakoniwa",
        "USER": "user",
        "PASSWORD": "p@ss",
        "HOST": "db.example",
        "PORT": "5433",
    }


@pytest.mark.parametrize("debug", ["0", "1"])
def test_https_defaults_preserve_http_development(debug):
    config = load_settings(DJANGO_DEBUG=debug, DJANGO_SECRET_KEY="test-secret")
    assert config["SECURE_HSTS_SECONDS"] == 0
    for name in (
        "SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "SECURE_HSTS_PRELOAD",
        "SECURE_SSL_REDIRECT",
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
    ):
        assert config[name] is False
    assert config["SECURE_PROXY_SSL_HEADER"] is None
    assert config["SILENCED_SYSTEM_CHECKS"] == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_https_boolean_environment(value):
    config = load_settings(DJANGO_SECURE_SSL_REDIRECT=value)
    assert config["SECURE_SSL_REDIRECT"] is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "off"])
def test_https_boolean_false_environment(value):
    assert load_settings(DJANGO_SECURE_SSL_REDIRECT=value)["SECURE_SSL_REDIRECT"] is False


@pytest.mark.parametrize(
    "env, error",
    [
        ({"DJANGO_SECURE_SSL_REDIRECT": "ture"}, RuntimeError),
        ({"DJANGO_SECURE_HSTS_SECONDS": "-1"}, RuntimeError),
        ({"DJANGO_SECURE_HSTS_SECONDS": "invalid"}, ValueError),
    ],
)
def test_invalid_https_environment_fails_at_startup(env, error):
    with pytest.raises(error):
        load_settings(**env)


@pytest.mark.parametrize("redirect_at_proxy", ["0", "1"])
def test_deploy_checks_only_silence_redirect_when_explicit(redirect_at_proxy):
    config = production_settings(DJANGO_HTTPS_REDIRECT_AT_PROXY=redirect_at_proxy)
    with override_settings(**config):
        issues = run_checks(tags=["security"], include_deployment_checks=True)
    # run_checks は消した警告も返すので、管理コマンドと同じく除いて比較する。
    visible = {issue.id for issue in issues if issue.id not in config["SILENCED_SYSTEM_CHECKS"]}
    # HSTS の対象拡大・preload は意図的に無効。追加の助言は隠さず残す。
    expected = {"security.W005", "security.W021"}
    if redirect_at_proxy == "0":
        expected.add("security.W008")
    assert visible == expected


def test_proxy_redirect_declaration_does_not_hide_other_missing_security_settings():
    config = load_settings(
        DJANGO_DEBUG="0",
        DJANGO_SECRET_KEY="settings-test-key-" + "a1b2c3d4e5" * 6,
        DJANGO_HTTPS_REDIRECT_AT_PROXY="1",
    )
    with override_settings(**config):
        visible = {
            issue.id
            for issue in run_checks(tags=["security"], include_deployment_checks=True)
            if issue.id not in config["SILENCED_SYSTEM_CHECKS"]
        }
    assert {"security.W004", "security.W012", "security.W016"} <= visible


@pytest.mark.django_db
def test_django_redirect_mode_needs_no_silencing():
    config = production_settings(DJANGO_SECURE_SSL_REDIRECT="1", DJANGO_HTTPS_REDIRECT_AT_PROXY="1")
    assert config["SILENCED_SYSTEM_CHECKS"] == []
    with override_settings(**config):
        assert {
            issue.id for issue in run_checks(tags=["security"], include_deployment_checks=True)
        } == {"security.W005", "security.W021"}
        client = Client(HTTP_HOST="testserver")
        assert client.get("/help/").status_code == 301
        assert client.get("/help/", HTTP_X_FORWARDED_PROTO="https").status_code == 200


def test_forwarded_proto_is_opt_in():
    factory = RequestFactory()
    for config, expected in ((load_settings(), False), (production_settings(), True)):
        with override_settings(**config):
            assert factory.get("/", HTTP_X_FORWARDED_PROTO="https").is_secure() is expected
            assert not factory.get("/", HTTP_X_FORWARDED_PROTO="http").is_secure()


@pytest.mark.django_db
def test_proxy_https_mode_keeps_deploy_pages_and_secure_login_working():
    config = production_settings(DJANGO_HTTPS_REDIRECT_AT_PROXY="1")
    with override_settings(**config):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="testserver")
        # 反映用の台本と同じく、見出しなしの内部 http で直接確かめる。
        for page in ("/", "/help/", "/world/", "/board/"):
            response = client.get(page)
            assert response.status_code == 200
            assert "Strict-Transport-Security" not in response
        headers = {"HTTP_X_FORWARDED_PROTO": "https"}
        response = client.get("/accounts/signup/", **headers)
        assert response.status_code == 200
        assert response["Strict-Transport-Security"] == "max-age=3600"
        assert response.cookies["csrftoken"]["secure"]
        data = {
            "username": "secure-signup",
            "password1": "a-test-password-7148!",
            "password2": "a-test-password-7148!",
        }
        assert client.post("/accounts/signup/", data, **headers).status_code == 403
        data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        response = client.post(
            "/accounts/signup/", data, HTTP_ORIGIN="https://testserver", **headers
        )
        assert response.status_code == 302
        assert response.cookies["sessionid"]["secure"]
        assert client.session["_auth_user_id"]
