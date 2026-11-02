"""AI で遊ぶ人向けの手引き（/help/ai/）。"""

from __future__ import annotations

import pytest
from django.conf import settings

from server.accounts import keys
from server.web import mcp


@pytest.mark.django_db
def test_ai_guide_shows_the_endpoint_and_the_limits(client, world):
    """つなぎ先の URL・鍵の形・上限は、このサーバーと設定の値で出る。"""
    page = client.get("/help/ai/").content.decode()
    assert "http://testserver/mcp" in page
    assert "Bearer hk_xxxx" in page
    assert f"1 分に {settings.HAKONIWA_AI_CALLS_PER_MINUTE} 回まで" in page
    assert f"{keys.MARK_TURNS} ターンは印が残ります" in page
    for anchor in ("key", "connect", "claude-app", "claude-desktop", "claude-code", "chatgpt"):
        assert f'id="{anchor}"' in page
    assert "{%" not in page and "{{" not in page  # テンプレートの書き損じがない


@pytest.mark.django_db
def test_ai_guide_code_can_be_copied_as_is(client, world):
    """コードの枠に「コピー」ボタンの JS が付き、枠の中に Windows などの書き添えを混ぜない。"""
    import re

    page = client.get("/help/ai/").content.decode()
    assert "js/copy-code.js" in page
    blocks = re.findall(r'<pre class="(?:code|prompt)">(.*?)</pre>', page, flags=re.S)
    assert len(blocks) >= 10
    for block in blocks:
        assert "(Windows)" not in block and "(Mac" not in block


@pytest.mark.django_db
def test_ai_guide_is_linked_from_the_other_guides_and_the_key_screen(
    client, world, django_user_model
):
    for path in ("/help/", "/help/start/"):
        assert 'href="/help/ai/"' in client.get(path).content.decode()
    user = django_user_model.objects.create_user("aiguide", password="pw-aiguide-1")
    client.force_login(user)
    keys_page = client.get("/accounts/keys/").content.decode()
    assert 'href="/help/ai/"' in keys_page


@pytest.mark.django_db
def test_mcp_guide_reads_the_ai_guide(client, world):
    assert "ai" in mcp.TOOLS_BY_NAME["guide"].properties["which"]["enum"]
    assert mcp.SCREENS["help_ai"] == ("guide",)
