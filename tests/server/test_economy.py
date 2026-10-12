"""サーバーでのお金の経済: 保存と読み込み、フェーズ1の島の移し替え、記録の表。"""

from __future__ import annotations

import pytest

from server.economy.models import FacilityBook, JournalEntry, MarketRecord
from server.game import get_game
from server.turns.services import rollback, run_turn
from server.world.models import Facility, Island, Tile
from tests.server.test_phase1 import _own_island

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
