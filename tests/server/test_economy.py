"""サーバーでのお金の経済: 保存と読み込み、フェーズ1の島の移し替え、記録の表。"""

from __future__ import annotations

import pytest

from server.economy.models import FacilityBook, JournalEntry, MarketRecord
from server.game import get_game
from server.orders.models import Order
from server.turns.services import rollback, run_turn
from server.world.models import Facility, Island, Stock, Tile
from tests.server.test_phase1 import _login, _own_island

pytestmark = pytest.mark.django_db


def test_sample_world_starts_with_money(world):
    for island in world.islands.all():
        assert island.economy.get("started") == 0
        assert island.accounts.filter(name="gov/treasury/cb").exists()
        assert sum(island.accounts.values_list("balance", flat=True)) == 0  # 持ち物ごとに合計 0


def test_turn_writes_books_and_market_records(world):
    run_turn(force=True)
    island = Island.objects.order_by("id").first()
    assert JournalEntry.objects.filter(island=island, turn=1).exists()
    assert MarketRecord.objects.filter(island=island, turn=1).exists()
    assert FacilityBook.objects.filter(island=island, turn=1).exists()
    assert sum(island.accounts.values_list("balance", flat=True)) == 0


def _make_phase1(island: Island) -> None:
    """フェーズ1の島の形に戻す（経済の欄・お金・銀行と運送業・オフィス街なし）。"""
    config = get_game().config
    kinds = [config.get("banking.facility"), config.get("freight.facility")]
    island.facilities.filter(kind__in=kinds).delete()
    island.accounts.all().delete()
    island.bonds.all().delete()
    island.facilities.update(economy={})
    Tile.objects.filter(island=island, zone="office").update(zone="")
    Island.objects.filter(pk=island.pk).update(economy={}, policy={})


def test_phase1_island_is_migrated_on_next_turn(world):
    island = Island.objects.order_by("id").first()
    _make_phase1(island)
    run_turn(force=True)
    island.refresh_from_db()
    assert island.economy.get("started") == 0
    assert island.policy
    config = get_game().config
    assert island.facilities.filter(kind=config.get("banking.facility")).count() == 1
    assert island.facilities.filter(kind=config.get("freight.facility"), x=-1).count() == 1
    assert island.accounts.filter(name="gov/treasury/cb").exists()
    built = Facility.objects.filter(island=island, built=True).exclude(economy={})
    assert built.exists()
    assert JournalEntry.objects.filter(island=island, turn=1).exists()


def test_island_without_capital_is_migrated(world):
    """フェーズ0の世界から残っている、首都も施設もない島も移し替えられる。"""
    island = Island.objects.order_by("id").first()
    _make_phase1(island)
    island.facilities.all().delete()
    Island.objects.filter(pk=island.pk).update(capital_x=None, capital_y=None)
    run_turn(force=True)
    island.refresh_from_db()
    assert island.economy.get("started") == 0
    assert island.facilities.filter(kind=get_game().config.get("banking.facility")).count() == 1
    assert sum(island.accounts.values_list("balance", flat=True)) == 0


def test_empty_island_is_migrated_and_keeps_running(world):
    """住民も首都も施設もない島（フェーズ0のサンプル島）も、移し替えた後のターンが止まらない。"""
    island = Island.objects.order_by("id").first()
    _make_phase1(island)
    island.facilities.all().delete()
    island.tiles.update(population=0)
    Island.objects.filter(pk=island.pk).update(capital_x=None, capital_y=None)
    for _ in range(3):
        run_turn(force=True)
    island.refresh_from_db()
    assert island.economy.get("started") == 0
    assert sum(island.accounts.values_list("balance", flat=True)) == 0


def test_rollback_removes_later_records(world):
    run_turn(force=True)
    run_turn(force=True)
    rollback(1)
    assert not JournalEntry.objects.filter(turn=2).exists()
    assert not MarketRecord.objects.filter(turn=2).exists()
    assert MarketRecord.objects.filter(turn=1).exists()


def test_policy_orders_from_the_island_page(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    policy = client.get(url).json()["policy"]
    assert policy["income_tax"] == 10 and "construction" in policy["budget"]
    body = {
        "orders": [
            {"kind": "set_taxes", "payload": {"income_tax": 15, "consumption_tax": 8}},
            {"kind": "set_budget", "payload": {"construction": 5_000_000}},
            {"kind": "set_labor_policy", "payload": {"unemployment_benefit": 30}},
            {"kind": "warehouse_trade", "payload": {"good": "wood", "side": "sell", "quantity": 1}},
        ]
    }
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    texts = [o["text"] for o in res.json()["orders"]]
    assert texts[0] == "税率の変更: 所得税 15%・消費税 8%"
    assert texts[1] == "予算の変更: 公共事業費 5,000,000"
    assert "木材を売る" in texts[3]
    run_turn(force=True)
    island.refresh_from_db()
    assert island.policy["income_tax"] == 1500 and island.policy["consumption_tax"] == 800


def test_warehouse_sale_check_reads_the_warehouse(client, world):
    """倉庫の財を売る命令の確認は、国の倉庫の在庫を読んで判断する（Codex の試し遊び、ターン50）。"""
    island = _own_island(client)
    Stock.objects.filter(island=island, facility__isnull=True).delete()
    Stock.objects.create(island=island, facility=None, good="wood", made_turn=0, quantity=5_000)
    url = f"/api/islands/{island.id}/orders/check"

    def sell(good):
        payload = {"good": good, "side": "sell", "quantity": 1}
        body = {"kind": "warehouse_trade", "payload": payload}
        return client.post(url, body, content_type="application/json").json()

    assert sell("wood")["ok"] is True
    data = sell("steel")
    assert data["ok"] is False and "倉庫" in data["text"]


def test_accounting_order_from_the_island_page(client, world):
    """国営施設のマスには会計方式の変更を出し（今と違う方だけ）、その命令を並べて実行できる。"""
    user = _login(client)
    island = Island.objects.order_by("id")[1]  # 混合経済の島: 発電所が国営・独立採算
    island.owner = user
    island.save()
    plant = island.facilities.get(kind="thermal_power_plant", owner="state")
    tile_url = f"/api/islands/{island.id}/tiles/{plant.x},{plant.y}"
    info = client.get(tile_url).json()["facility"]
    assert info["accounting_name"] == "独立採算"
    assert info["accounting_target"] == "treasury"
    assert info["accounting_target_name"] == "国庫直轄"

    # 民営・公務の施設・銀行（国営と民間の2つ）・町では出さない
    config = get_game().config
    others = [
        island.facilities.filter(owner="private", x__gte=0).first(),
        island.facilities.get(kind="government_office"),
        *island.facilities.filter(kind=config.get("banking.facility")),
        island.facilities.filter(kind="housing").first(),
    ]
    for f in others:
        data = client.get(f"/api/islands/{island.id}/tiles/{f.x},{f.y}").json()["facility"]
        assert "accounting_target" not in data, f.kind

    url = f"/api/islands/{island.id}/orders"
    payload = {"x": plant.x, "y": plant.y, "accounting": "treasury"}
    body = {"orders": [{"kind": "set_accounting", "payload": payload}]}
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    assert res.json()["warnings"] == []
    text = res.json()["orders"][0]["text"]
    assert text == f"国営施設の会計方式の変更: ({plant.x}, {plant.y}) → 国庫直轄"
    order = Order.objects.get(island=island)
    assert (order.kind, order.payload) == ("set_accounting", payload)

    run_turn(force=True)
    plant.refresh_from_db()
    assert plant.accounting == "treasury"
    # 変えた後は、元に戻す向きを出す
    assert client.get(tile_url).json()["facility"]["accounting_target"] == "self_supporting"


def test_accounting_order_is_not_offered_before_the_money_economy(client, world):
    island = Island.objects.order_by("id")[1]
    _make_phase1(island)
    plant = island.facilities.get(kind="thermal_power_plant", owner="state")
    info = client.get(f"/api/islands/{island.id}/tiles/{plant.x},{plant.y}").json()["facility"]
    assert "accounting_target" not in info
