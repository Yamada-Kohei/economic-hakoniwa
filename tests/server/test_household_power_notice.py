"""家計が電気料金を払えないときの知らせ（UI-89）: 発電が売れ残っているのに家計が払えないときは
「電力が足りていません」でなく「家計が電気料金を払えない」と打てる手を出す。発電を使い切った分だけ
「電力が足りていません」。島の画面・国の要約（JSON・MCP）で同じ中身。

前は LAN の世界 8003 の島3 で、発電が余り燃料も何百か月分あるのに「発電所の燃料と、送電線で
つながっている範囲を確かめます」と出ていた（2026-11-12）。"""

from __future__ import annotations

import json

import pytest

from server.accounts import keys
from server.web import overview
from tests.server.test_ai_players import _keyed, _players, _set_turn, _tool
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db

# 島3 に似た値: 発電の 6 割が売れ残り、家計は要る電気の 56% を払えない
UNPAID = {
    "power.capacity": 1000.0,
    "power.used": 400.0,
    "power.demand": 1000.0,
    "power.shortage": 0.6,
    "power.short_supply": 0.0,
    "power.household_unaffordable": 0.56,
    "power.household.general": 0.44,
    "power.household.jobless": 0.2,
    "power.household.owners": 1.0,
}


def test_unpaid_bills_are_told_apart_from_a_power_shortage():
    texts = overview.power_alerts(UNPAID)
    assert len(texts) == 1 and "電力が足りていません" not in texts[0]
    assert texts[0].startswith("家計が電気料金を払えず、要る電気の 56.0% を使えていません")
    assert "発電は 60.0% 売れ残っています" in texts[0]
    assert "一番使えていないのは失業者で、届いたのは要る量の 20.0%" in texts[0]
    assert "公共料金の上限" in texts[0]
    planned = overview.power_alerts(UNPAID, planned=True)[0]
    assert "電気の公定価格を下げる" in planned and "公共料金の上限" not in planned
    # 発電を使い切った分は電力不足（power.shortage ではなく power.short_supply で）
    short = overview.power_alerts({**UNPAID, "power.short_supply": 0.25})
    assert short[0] == "電力が足りていません（不足率 25.0%）" and len(short) == 2
    # 知らせの線より小さい払えなさは出さない。統計のない前のターンは今までどおり power.shortage
    assert overview.power_alerts({**UNPAID, "power.household_unaffordable": 0.01}) == []
    old = overview.power_alerts({"power.shortage": 0.3})
    assert old == ["電力が足りていません（不足率 30.0%）"]


def test_summary_page_and_mcp_show_the_same_power_notice(client, world):
    (alice, _, _), (a, _, _) = _players(world)
    a.stats.all().delete()
    _stats(a, 1, UNPAID)
    _set_turn(world, 1)
    _, token = keys.issue(alice)
    data = _keyed(token).get(f"/api/islands/{a.id}/summary").json()
    told = [x for x in data["alerts"] if x.startswith("家計が電気料金を払えず")]
    assert len(told) == 1
    assert not any(x.startswith("電力が足りていません") for x in data["alerts"])
    steps = [s for s in data["next_steps"] if s["key"].startswith("power")]
    assert [s["key"] for s in steps] == ["power_bill"]
    assert steps[0]["url"].endswith("/orders/")
    text, error = _tool(_keyed(token), "summary")
    assert not error and told[0] in json.loads(text)["alerts"]
    client.force_login(alice)
    assert told[0] in client.get(f"/islands/{a.id}/home/").content.decode()
