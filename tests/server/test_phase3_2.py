"""フェーズ3-2 の画面: 産業の鎖、施設の欄の作り方の変更、マスの肥沃度、建国の区画の気候帯、
手引き。"""

from __future__ import annotations

import pytest

from server.economy.models import MarketRecord, Trade
from server.game import get_game
from server.web import chain
from server.world import models as m
from tests.server.test_phase1 import _login, _own_island
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db


def _records(island: m.Island, good: str, rows: list[tuple[int, int, int, int]]) -> None:
    """(ターン, 値段, 売買の量, 買いたい量) の市場の記録。"""
    MarketRecord.objects.bulk_create(
        [
            MarketRecord(
                island=island,
                turn=t,
                good=good,
                price=p,
                volume=v,
                demand=d,
                supply=v,
            )
            for t, p, v, d in rows
        ]
    )


def test_chain_page_is_only_for_the_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    page = client.get(f"/islands/{island.id}/chain/").content.decode()
    assert "産業の鎖は、島の持ち主だけが見られます。" in page
    island = _own_island(client)
    page = client.get(f"/islands/{island.id}/chain/").content.decode()
    assert "品目ごとの鎖" in page
    assert "止まっている工場と待っているもの" in page
    island_page = client.get(f"/islands/{island.id}/").content.decode()
    assert f"/islands/{island.id}/chain/" in island_page  # 島の画面の案内に出る


def test_chain_view_marks_shortages_and_counts_imports(client, world):
    island = _own_island(client)
    _stats(island, 15, {"production.grain": 40.0, "use.grain": 12.0, "power.used": 30.0})
    _records(island, "grain", [(3, 100, 5000, 5000), (15, 120, 5000, 5000)])
    # 衣料は 3 ターン続けて半分しか買えない
    _records(
        island,
        "clothing",
        [(12, 900, 1000, 1000), (13, 900, 500, 1000), (14, 950, 500, 1000), (15, 990, 500, 1000)],
    )
    number = island.facilities.order_by("-number").first().number + 1
    trader = m.Facility.objects.create(
        island=island, number=number, kind="trading", x=-50, y=-50, built=True, owner="private"
    )
    Trade.objects.create(
        island=island,
        turn=15,
        good="clothing",
        buyer="hh:general",
        seller=f"fac:{trader.number}",
        quantity=300,
        amount=300,
    )
    Trade.objects.create(
        island=island,
        turn=15,
        good="clothing",
        buyer="hh:general",
        seller="merchant",
        quantity=200,
        amount=200,
    )
    stats = {"production.grain": 40.0, "use.grain": 12.0, "power.used": 30.0}
    view = chain.chain_view(island, stats, "¤")
    assert view["turn"] == 15
    goods = {g["good"]: g for tier in view["tiers"] for g in tier["goods"]}
    grain, clothing = goods["grain"], goods["clothing"]
    assert grain["made"] == 1000.0 and grain["used"] == 300.0 and not grain["short"]
    assert grain["change"] == "+20%"  # 12 ターン前との比べ
    assert clothing["short"] and clothing["streak"] == 3 and clothing["bought"] == 50
    assert clothing["imported"] == 12.5  # 商社と海外の商人が売った量
    assert [g["good"] for g in view["short"]] == ["clothing"]
    assert goods["electricity"]["used"] == 750.0  # 電力は使った量を送電の統計から
    tiers = [t["name"] for t in view["tiers"]]
    assert tiers == sorted(tiers, key=["原料", "中間財", "最終財"].index)
    farm = next(k for k in grain["makers"] if k["kind"] == "farm")
    assert farm["built"] >= 1


def test_chain_view_tells_goods_left_unsold_at_their_price(client, world):
    """売りも買いもあるのに売れ残った品目（売りの値段が買い手の指値より高い）は、品不足と分けて
    そう出す。"""
    island = _own_island(client)
    MarketRecord.objects.create(
        island=island, turn=15, good="luxury_crop", price=900, volume=0, demand=5000, supply=3000
    )
    MarketRecord.objects.create(
        island=island, turn=15, good="grain", price=100, volume=4000, demand=6000, supply=4000
    )
    view = chain.chain_view(island, {}, "¤")
    goods = {g["good"]: g for tier in view["tiers"] for g in tier["goods"]}
    assert goods["luxury_crop"]["unmatched"] is True and goods["luxury_crop"]["supply"] == 75.0
    assert goods["grain"]["unmatched"] is False  # 売りに出た分は売れた（ただの品不足）
    page = client.get(f"/islands/{island.id}/chain/").content.decode()
    assert "売りの値段が買い手の指値より高く、売れ残りました" in page
    assert "量は実物に近い単位です" in page


def test_recipe_choices_for_a_state_facility(client, world):
    island = _own_island(client)
    row = island.facilities.filter(kind="farm").order_by("number").first()
    assert row is not None
    row.owner = "state"
    row.save()
    climate = (island.ext or {})["climate"]
    data = client.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    choices = {c["recipe"] for c in data["facility"].get("recipe_choices", [])}
    assert row.recipe not in choices
    assert "fertilized" in choices
    climate_spec = get_game().defs.climate[climate]["outputs"]
    assert ("cotton" in choices) == (climate_spec.get("cotton", 1) > 0)
    assert data["fertility"] == pytest.approx(
        island.tiles.get(x=row.x, y=row.y).ext.get("fertility", 1000) / 1000
    )
    row.owner = "private"  # 民営は自分で見直すので、命令は出せない
    row.save()
    data = client.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    assert not data["facility"].get("recipe_choices")


def test_world_and_founding_cells_show_climate(client, world):
    data = client.get("/api/world").json()
    zones = {i["climate"] for i in data["islands"]}
    assert zones <= {"cold", "temperate", "tropical"} and zones
    _login(client)
    cells = client.get("/api/founding/cells").json()
    assert len(cells["rows"]) == data["grid"]
    assert cells["rows"][0] == "cold" and cells["rows"][-1] == "tropical"
    names = {z["zone"]: z for z in cells["zones"]}
    assert names["cold"]["rows"][0] == 0
    assert "綿は作れない" in names["cold"]["text"]
    assert "家庭の電力 1.3 倍" in names["cold"]["text"]


def test_help_explains_goods_and_climate(client, world):
    page = client.get("/help/").content.decode()
    assert "品目と自然条件" in page
    assert "寒冷" in page and "肥沃度" in page and "トラック" in page
