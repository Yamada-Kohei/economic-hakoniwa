"""手引きの入門（/help/start/）と、画面ごとの「この画面の手引き」のリンク。"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from server.web import display
from server.web.context import SCREEN_HELP
from server.web.views import STATUS_HELP


@pytest.mark.django_db
def test_start_guide_links_both_ways_and_names_the_statuses(client, world):
    """入門と詳しい仕組みは互いにリンクし、施設の状態の言葉は画面と同じ名前で出る。"""
    start = client.get("/help/start/").content.decode()
    general = client.get("/help/").content.decode()
    assert 'href="/help/"' in start and 'href="/help/start/"' in general
    for key, _text in STATUS_HELP:
        assert display.message(f"status_{key}") in start
    assert "工業団地・オフィス街の指定" in start  # 市場・混合経済の最初の命令
    assert 'href="/help/start/"' in client.get("/").content.decode()  # 見出しの「手引き」は入門へ


@pytest.mark.django_db
def test_start_guide_links_point_at_real_sections(client, world):
    """入門の「詳しく」と、画面ごとの手引きのリンクは、手引きにある節を指す。"""
    pages = {name: client.get(reverse(name)).content.decode() for name in ("help", "help_start")}
    targets = re.findall(r'href="/help/#([a-z-]+)"', pages["help_start"])
    targets = [("help", a) for a in targets] + [t for t in SCREEN_HELP.values() if t[1]]
    for page, anchor in targets:
        assert f'id="{anchor}"' in pages[page], (page, anchor)


@pytest.mark.django_db
def test_screens_show_their_help_link(client, world):
    """画面の上に、その画面の節へのリンクが出る（手引きの画面には出さない）。"""
    assert (
        'class="screen-help"><a href="/help/#statistics"'
        in client.get("/rankings/").content.decode()
    )
    assert "screen-help" not in client.get("/help/").content.decode()


@pytest.mark.django_db
def test_mcp_guide_reads_the_start_guide(client, world):
    from server.web import mcp

    assert "start" in mcp.TOOLS_BY_NAME["guide"].properties["which"]["enum"]
