"""JS の文言の訳を渡す仕組み（/jsi18n/、docs/i18n.md の JS の節）を確かめる。

Python 側と同じ server/locale の訳を、Django の JavaScriptCatalog で
JS の gettext / ngettext / interpolate へ渡す。自分たちの訳ファイルが
無い間は、JS 側では文言そのもの（日本語）がそのまま出る。
"""

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_javascript_catalog_serves_gettext_library():
    response = Client().get(reverse("javascript-catalog"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
    body = response.content.decode("utf-8")
    # JS 側の gettext 一式が定義され、グローバルへ出ている。
    assert "django.gettext = function" in body
    assert "django.ngettext = function" in body
    assert "django.interpolate = function" in body
    assert "globals.gettext = django.gettext" in body
    # 自分たちの訳ファイルはまだ無いので、見本の文言はカタログに無い
    # （JS 側では文言そのもの＝日本語がそのまま出る）。カタログに入っている
    # のは Django 本体の djangojs の訳だけ。
    assert "未読の公電 %(count)s" not in body


@pytest.mark.django_db
def test_base_template_loads_catalog_before_scripts():
    response = Client().get("/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    catalog_tag = '<script src="/jsi18n/"></script>'
    assert catalog_tag in body
    # gettext を使うスクリプト（module は遅れて実行される）より前に読み込む。
    assert body.index(catalog_tag) < body.index("js/theme.js")
