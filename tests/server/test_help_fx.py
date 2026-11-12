"""為替の効き方の手引き（/help/fx/、W-252）と、入門の「軽く動かさないほうがいい数値」（W-255）。"""

from __future__ import annotations

import pytest

from server.game import get_game
from server.web.context import SCREEN_HELP


@pytest.mark.django_db
def test_fx_guide_reads_the_numbers_from_the_settings(client, world):
    """為替の手引きは値幅・融通の上限などを設定から出し、レートの向きと海外の商人の限りを書く。"""
    config = get_game().config
    page = client.get("/help/fx/").content.decode()
    assert f"{config.get('fx.band') * 100:g}%" in page
    assert f"{config.get('fx.swap_quota') * 100:g}%" in page
    assert f"{config.get('fx.reserve_share') * 100:g}%" in page
    assert "自国の通貨が安くなった" in page  # レートが上がる＝自国通貨安
    assert "海外の商人" in page and "為替とつながっていません" in page  # W-203 が残っていること
    for anchor in ("when", "read", "effects", "levers", "not-yet"):
        assert f'id="{anchor}"' in page


@pytest.mark.django_db
def test_fx_guide_is_linked_from_the_guides_and_the_fx_screen(client, world):
    """手引きの一覧・詳しい仕組みの「貿易と為替」・為替の画面の手引きのリンクから行ける。"""
    assert 'href="/help/fx/"' in client.get("/help/").content.decode()
    assert 'href="/help/fx/"' in client.get("/help/start/").content.decode()
    assert SCREEN_HELP["island_fx"] == ("help_fx", "")


@pytest.mark.django_db
def test_mcp_guide_reads_the_fx_guide(client, world):
    from server.web import mcp

    assert "fx" in mcp.TOOLS_BY_NAME["guide"].properties["which"]["enum"]
    assert mcp.SCREENS["help_fx"] == ("guide",)


@pytest.mark.django_db
def test_start_guide_lists_the_numbers_not_to_move_lightly(client, world):
    """入門に、戻しても戻らない数値の表があり、最低賃金と国債の支払い停止の数を設定から出す。"""
    config = get_game().config
    page = client.get("/help/start/").content.decode()
    assert 'id="careful"' in page and 'href="#careful"' in page
    assert "最低賃金" in page and "上がった賃金は下がりません" in page
    assert f"{config.get('bonds.default_decay_turns')} ターンかけて消え" in page
    assert f"{config.get('central_bank.switch_lock_turns')} ターンは戻せません" in page
    assert 'href="/help/fx/#levers"' in page
