"""市場と貿易の画面の直し（W-226・W-225・W-222・W-171・W-70）の受け入れの条件。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.trade import contracts as contract_states
from server.game import get_game
from server.web import display, reports, trade
from server.world.models import Island

pytestmark = pytest.mark.django_db


def _contract(cid: int, seller: int, buyer: int, good: str, delivered: int) -> dict:
    return {
        "id": cid,
        "seller": seller,
        "buyer": buyer,
        "proposer": seller,
        "good": good,
        "qty": 300_000,
        "price": 100,
        "currency": seller,
        "state": contract_states.ENDED,
        "start": 10,
        "end": 13,
        "delivered": delivered,
        "paid": 0,
        "short": 0,
        "turns": 3,
    }


def test_contract_rows_show_shipped_and_cargo_at_sea():
    """W-226: 契約の一覧は「送り出した量」と、まだ海の上の量と着く見込みのターンを出す。"""
    good = next(iter(get_game().defs.goods))
    islands = {
        1: {"name": "甲", "currency": "甲円", "symbol": "¥"},
        2: {"name": "乙", "currency": "乙ドル", "symbol": "$"},
    }
    shipments = [
        {"id": 1, "kind": "contract", "ref": 5, "batches": [[11, 100_000]], "arrive": 15},
        {"id": 2, "kind": "contract", "ref": 5, "batches": [[12, 200_000]], "arrive": 16},
        {"id": 3, "kind": "bloc", "ref": 5, "batches": [[12, 999_000]], "arrive": 14},
    ]
    contracts = [_contract(5, 1, 2, good, 300_000), _contract(6, 1, 2, good, 300_000)]
    rows = {r["id"]: r for r in trade.contract_rows(contracts, 2, 14, islands, shipments)}
    assert "delivered" not in rows[5]
    assert rows[5]["shipped"] == trade._amount(good, 300_000)
    assert rows[5]["at_sea"] == trade._amount(good, 300_000)
    assert (rows[5]["arrive_first"], rows[5]["arrive_last"]) == (15, 16)
    # 海の上の荷のない契約は 0 で、着くターンはない（圏の荷は契約の荷に数えない）
    assert rows[5]["port_wait"] is False
    # 着く予定を過ぎた荷は、港の空き待ちとして次のターンに着く見込みにする
    late = {r["id"]: r for r in trade.contract_rows(contracts, 2, 16, islands, shipments)}
    assert (late[5]["arrive_first"], late[5]["arrive_last"], late[5]["port_wait"]) == (17, 17, True)
    assert rows[6]["at_sea"] == 0
    assert rows[6]["arrive_first"] is None


def test_price_kinds_separate_trade_quote_carry_and_none():
    """W-171: 値段の種類で、約定・気配・持ち越し・未観測を分ける。"""
    record = SimpleNamespace(volume=0, demand=0, supply=0)
    assert reports.price_kind(None) == "none"
    assert reports.price_kind(record) == "carry"
    record.demand = 5
    assert reports.price_kind(record) == "carry"  # 片側だけの注文は前の値段のまま
    record.supply = 5
    assert reports.price_kind(record) == "quote"
    # 商人の安い買いと島の売りだけで折り合わない気配は、記録の買い注文が 0 でも値段が前と変わる
    lone = SimpleNamespace(volume=0, demand=0, supply=5, price=90)
    assert reports.price_kind(lone, SimpleNamespace(price=100)) == "quote"
    assert reports.price_kind(lone, SimpleNamespace(price=90)) == "carry"
    record.volume = 1
    assert reports.price_kind(record) == "trade"
    assert set(reports.PRICE_KINDS) == {"trade", "quote", "carry", "none"}
    # W-227: 市場の段が残した種類があれば、それを使う（推し量らない）
    kept = SimpleNamespace(volume=0, demand=5, supply=5, price=90, kind="carry")
    assert reports.price_kind(kept, SimpleNamespace(price=100)) == "carry"


def test_market_table_has_price_kind_and_to_state_only_for_owner(world):
    """W-171・W-225: 市場の表に値段の種類と最後に約定したターン、持ち主にだけ倉庫から国営の施設に
    渡した量を出す。"""
    island = Island.objects.filter(world=world).order_by("id").first()
    good = next(iter(get_game().defs.goods))
    stats = {f"stock.{good}": 10, f"warehouse.to_state.{good}": 2}
    mine = {r["good"]: r for r in reports.market_table(island, stats, "¥", mine=True)}
    other = {r["good"]: r for r in reports.market_table(island, stats, "¥")}
    assert mine[good]["price_kind"] == "none"
    assert mine[good]["price_kind_name"] == "未観測"
    assert mine[good]["observed_turn"] is None
    assert mine[good]["to_state"] == display.shown(good, 2)
    assert "to_state" not in other[good]


def test_withdrawn_goods_stats_are_owner_only():
    """W-225: 品目ごとの倉庫からの引き出しは、ほかの国には見せない（国の計画の中身）。"""
    good = next(iter(get_game().defs.goods))
    name = f"warehouse.to_state.{good}"
    assert reports.visible(name, mine=True)
    assert not reports.visible(name, mine=False)
    assert reports.visible("warehouse.to_state", mine=False)  # 国全体の額はこれまでどおり
    assert reports.stat_label(name)[0] != name


def test_reserve_rotation_note_on_grain_trades():
    """W-222: 穀物の約定で、政府どうしの組は備蓄の入れ替えと分かる説明を付ける。"""
    grain = get_game().config.get("food_reserve")["good"]
    gov = ledger.GOV
    assert reports.trade_note(grain, grain, gov, gov).startswith("備蓄の入れ替え")
    assert "備蓄" in reports.trade_note(grain, grain, "hh:general", gov)
    assert "備蓄" in reports.trade_note(grain, grain, gov, "fac:3")
    assert reports.trade_note(grain, grain, "hh:general", "fac:3") == ""
    other = next(g for g in get_game().defs.goods if g != grain)
    assert reports.trade_note(other, grain, gov, gov) == ""


def test_fx_amount_stats_carry_currency_symbol(world):
    """W-70: 為替の売買量などの統計の単位に、その通貨の記号を付ける（グラフの縦軸に出る）。"""
    island = Island.objects.filter(world=world).order_by("id").first()
    symbol = (island.names or {}).get("currency_symbol", "")
    _, unit = reports.stat_label(f"fx.volume.{island.id}")
    assert unit == (f"money:{symbol}" if symbol else "")
    _, rate_unit = reports.stat_label(f"fx.{island.id}")
    assert rate_unit == "fx"
    assert display.stat_text("fx.volume.1", 1234, "money:$") == display.money_text(1234, "$")
