"""W-308: 商社の建て増し（扱える重さで取り逃した輸入が続き、儲けが費用に引き合えば、支店・倉庫と人を
足して規模を 1 つ上げる。扱える重さと人数が規模の分だけ増える）。"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import OWNERS, facility_entity, household
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.trade import traders, traders_of
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_trade import _context, game, run, traded  # noqa: F401


def _trader(world, game):  # noqa: F811
    island = world.islands[1]
    working = traders_of(island, game.config, world.turn)
    assert working
    return island, working[0]


def _rich_owners(island):
    """資産を持つ層に建て増しの代金を出せる預金を作る（試験のためのお金）。"""
    bank = ledger.bank_of(island, household(OWNERS))
    ledger.create(island, household(OWNERS), ledger.DEPOSIT, bank, 10**15, "test_top_up")


def _missed_every_turn(game, f, profit):  # noqa: F811
    turns = game.config.get("trade.entry_turns")
    traders.state_of(f)["missed_cap"] = [[profit, profit] for _ in range(turns)]


def test_a_bigger_trader_handles_and_hires_in_proportion_to_its_size(game, traded):  # noqa: F811
    world = copy.deepcopy(traded[0])
    _, f = _trader(world, game)
    one = traders.capacity(game.config, f)
    assert one == game.config.get("trade.trader_capacity") * QUANTITY_SCALE
    traders.state_of(f)["volume"] = [one]  # 扱える重さいっぱいに扱った
    staff = traders.workers_wanted(game.config, game.defs, f)
    f.ext[fac.SIZE] = 2
    assert traders.capacity(game.config, f) == 2 * one
    traders.state_of(f)["volume"] = [2 * one]
    assert traders.workers_wanted(game.config, game.defs, f) == 2 * staff
    traders.state_of(f)["volume"] = [one]  # 規模 2 で 1 社分しか扱わなければ、人も 1 社分
    assert traders.workers_wanted(game.config, game.defs, f) == staff


def test_a_trader_expands_when_capacity_cuts_pay_and_the_switch_is_on(game, traded, monkeypatch):  # noqa: F811
    world = copy.deepcopy(traded[0])
    island, f = _trader(world, game)
    ctx = _context(game, world)
    _rich_owners(island)
    _missed_every_turn(game, f, 10**12)  # 引き合う輸入を扱える重さで取り逃し続けた
    assert trade_setup.expand(island, ctx, False) is None  # 既定は切
    monkeypatch.setitem(game.config.raw["trade"], "trader_expand", True)
    assert trade_setup.expand(island, ctx, True) is None  # 計画経済の島では建て増さない（第 1 版）
    site = trade_setup.expand(island, ctx, False)
    assert site is not None and fac.expansion_of(site) == f.id
    assert site.ext.get("private_entry") and ledger.money(island, facility_entity(site.id)) > 0
    assert trade_setup.expand(island, ctx, False) is None  # 工事中の商社は重ねて建て増さない
    assert fac.pending_expansions(island, f) == 1


def test_a_trader_does_not_expand_on_money_cuts_or_past_the_size_limit(game, traded, monkeypatch):  # noqa: F811
    monkeypatch.setitem(game.config.raw["trade"], "trader_expand", True)
    world = copy.deepcopy(traded[0])
    island, f = _trader(world, game)
    ctx = _context(game, world)
    _rich_owners(island)
    turns = game.config.get("trade.entry_turns")
    # 取り逃しが半分のターンに届かない（お金で買えなかった分は missed_cap に入らない）
    traders.state_of(f)["missed_cap"] = [[0, 0]] * (turns - turns // 2 + 1) + [[10**12, 1]] * (
        turns // 2 - 1
    )
    for other in traders_of(island, game.config, world.turn):
        if other is not f:
            traders.state_of(other)["missed_cap"] = []
    assert trade_setup.expand(island, ctx, False) is None
    _missed_every_turn(game, f, 10**12)
    f.ext[fac.SIZE] = game.config.get("trade.trader_max_size")  # もう上限の規模
    assert trade_setup.expand(island, ctx, False) is None


def test_capacity_cuts_are_kept_apart_from_money_cuts(game, traded):  # noqa: F811
    """扱える重さで取り逃した分（建て増しの根拠）は、お金で買えなかった分を含む取り逃しの一部。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    for f in traders_of(island, game.config, world.turn):
        for item in traders.goods_of(f).values():
            if item.get("dir") == traders.IMPORT:
                item["m"] = item["m"] * 50 + 200 * QUANTITY_SCALE  # 扱える重さに収まらない量
    run(game, world, 1)
    for f in traders_of(island, game.config, world.turn):
        state = traders.state_of(f)
        assert len(state["missed_cap"]) == len(state["missed"])
        (cap_profit, cap_cost), (profit, cost) = state["missed_cap"][-1], state["missed"][-1]
        assert 0 <= cap_profit <= profit and 0 <= cap_cost <= cost
