"""企業の一覧（UI-54）: 他国の施設の画面・国の要約・統計の順位の画面の世界の企業。お金の数字と
国有化・民営化の命令は持ち主の国だけ。"""

from __future__ import annotations

from server.world import models as m
from tests.server.test_phase1 import _login

MONEY = ("cash", "profit", "tax", "dividend", "losses", "order")


def _two_islands(client, world):
    from server.turns.services import run_turn

    run_turn(world.id)
    islands = list(m.Island.objects.filter(world=world).order_by("id"))
    mine = islands[0]
    mine.owner = _login(client)
    mine.save()
    other = next(i for i in islands[1:] if i.facilities.filter(economy__has_key="firm").exists())
    return mine, other


def test_other_country_firms_without_money(client, world):
    _, other = _two_islands(client, world)
    res = client.get(f"/islands/{other.id}/facilities/")
    firms = res.context["firms"]
    assert firms and "table" not in res.context
    assert all(not any(k in r for k in MONEY) for r in firms)
    assert all(r["name"] and r["count"] >= 0 and r["workers"] >= 0 for r in firms)
    html = res.content.decode()
    assert 'class="firm-action"' not in html and "世界の企業" in html

    data = client.get(f"/api/islands/{other.id}/facilities").json()
    assert [r["name"] for r in data["firms"]] == [r["name"] for r in firms]
    summary = client.get(f"/api/islands/{other.id}/summary").json()
    assert summary["firms"]["list"]
    assert all(not any(k in r for k in MONEY) for r in summary["firms"]["list"])


def test_world_firms_on_the_rankings_page(client, world):
    _two_islands(client, world)
    res = client.get("/rankings/")
    firms = res.context["data"]["firms"]
    assert firms and [r["rank"] for r in firms] == list(range(1, len(firms) + 1))
    keys = [(-r["count"], -r["workers"]) for r in firms]
    assert keys == sorted(keys)
    assert all(not any(k in r for k in MONEY) for r in firms)
    assert 'id="firms"' in res.content.decode()
    api = client.get("/api/rankings").json()
    assert [r["name"] for r in api["firms"]] == [r["name"] for r in firms]
