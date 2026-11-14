"""組の参入の値段と採算（FM-72。W-26 の自動車の値段の外れ）: 原料が島で作られていない組み立て工場
（自動車工場）を、原料の工場（部品工場）とひと組で見る。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめる（tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy
import math

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import OWNERS, banks, household
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.prices import construction_cost
from hakoniwa_core.modules import firms
from hakoniwa_core.modules.firms import (
    GROUP_FAILED,
    GROUP_FOR,
    EntryExit,
    entry_price,
    group_cooling,
    group_return,
    inner_cost,
)
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.units import round_money
from tests.core.test_balance_round7 import market_island, private_members
from tests.core.test_finance import context
from tests.core.test_phase3_3 import run_checked

BASE_TURNS = 24
AUTO, PARTS = "automobile_plant", "auto_parts_plant"


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, BASE_TURNS)
    return world


def no_auto_island(game, world, monkeypatch):
    """自動車工場も部品工場もなく、部品と自動車のほかは島で手に入る市場経済の島（試験のため、
    手に入る財と工事中の財を差し替える）。自動車は品切れ続き。"""
    island = market_island(world)
    island.facilities = [f for f in island.facilities if f.kind not in (AUTO, PARTS)]
    every = set(game.defs.goods) - {"auto_parts", "automobile"}
    monkeypatch.setattr(EntryExit, "_supplied", staticmethod(lambda _i, _c: set(every)))
    monkeypatch.setattr(EntryExit, "_pipeline", staticmethod(lambda _i, _c: set()))
    monkeypatch.setattr(EntryExit, "_coming", staticmethod(lambda _i, _c: set()))
    settings = game.config.get("firms")
    out = game.defs.facilities[AUTO]["recipes"]["default"]["outputs"]["automobile"]
    finance = island.economy.finance
    finance.setdefault("shortages", {})["automobile"] = [out * 2] * settings["price_turns"]
    return island


def test_group_entry_price_is_solved_from_the_pair(game, base, monkeypatch):
    """部品工場も自動車工場もない島で、自動車の参入が見合う値段は None でなく、組（自動車工場と
    部品工場）の見込みで解いた値。部品工場の原料（鋼鉄）が島にない島では今どおり None。"""
    world = copy.deepcopy(base)
    island = no_auto_island(game, world, monkeypatch)
    ctx = context(game, world)
    price = entry_price(island, ctx, "automobile")
    assert price is not None and price > 0
    # 解いた値段で、組の見込み × 売れる割合が要る利益率にほぼ届く
    prices = firms.average_prices(island, game.config.get("firms.price_turns"))
    makers = EntryExit._makers(island, ctx, ["auto_parts"], EntryExit._supplied(island, ctx), set())
    pos, freight = EntryExit()._site(island, ctx, AUTO, "default")
    wage = island.economy.average_wage
    capital = round_money(construction_cost(game.defs, AUTO, prices, wage))
    if firms.needs_site_work(island, game.defs, game.config, AUTO, pos):
        capital += round_money(firms.site_cost(game.defs, game.config, AUTO, prices, wage))
    r = group_return(
        island, ctx, AUTO, "default", makers, {**prices, "automobile": price}, freight, capital, pos
    )
    fill = EntryExit._fill(
        game.defs.facilities[AUTO]["recipes"]["default"],
        island.economy.finance["shortages"],
        island.economy.finance.get("demand", {}),
        EntryExit._capacity(island, ctx),
        prices,
        firms.unit_cost(
            game.defs,
            game.config,
            AUTO,
            game.defs.facilities[AUTO]["recipes"]["default"],
            prices,
            wage,
        ),
        game.config.get("firms"),
    )
    threshold = EntryExit._threshold(island, ctx)
    assert r * fill == pytest.approx(threshold, rel=0.02)
    # 部品工場の原料が島にない
    without = set(game.defs.goods) - {"auto_parts", "automobile", "steel"}
    monkeypatch.setattr(EntryExit, "_supplied", staticmethod(lambda _i, _c: set(without)))
    assert entry_price(island, ctx, "automobile") is None
    # 組が期限までに揃わずに解けた島は、しばらく組で見ない
    monkeypatch.setattr(EntryExit, "_supplied", staticmethod(lambda _i, _c: set(game.defs.goods)))
    island.economy.finance[GROUP_FAILED] = {AUTO: ctx.turn}
    assert group_cooling(island, ctx, AUTO)
    monkeypatch.setattr(
        EntryExit,
        "_supplied",
        staticmethod(lambda _i, _c: set(game.defs.goods) - {"auto_parts", "automobile"}),
    )
    assert entry_price(island, ctx, "automobile") is None


@pytest.mark.parametrize("need", [0.5, 1.0, 1.5, 2.2])
def test_group_size_counts_whole_plants_and_parts_at_variable_cost(game, base, monkeypatch, need):
    """組の建設費は部品工場の数を整数に切り上げて数え、組の中の部品は島の値段でなく部品工場の変動費で
    数える。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    defs = game.defs
    makers = {"auto_parts": (PARTS, "default")}
    monkeypatch.setattr(firms, "group_size", lambda *_a: need)
    seen = []
    real = ctx.formula("entry_return")

    def spy(island, ctx_, kind, rname, prices, freight, capital, pos=None):
        seen.append((prices["auto_parts"], capital))
        return real(island, ctx_, kind, rname, prices, freight, capital, pos)

    monkeypatch.setattr(type(ctx), "formula", lambda self, name: spy)
    prices = dict(island.economy.prices)
    wage = island.economy.average_wage
    main = 1_000_000
    group_return(island, ctx, AUTO, "default", makers, prices, 0, main)
    group_return(
        island,
        ctx,
        AUTO,
        "default",
        makers,
        {**prices, "auto_parts": prices["auto_parts"] * 5},
        0,
        main,
    )
    plant = round_money(construction_cost(defs, PARTS, prices, wage))
    inner = inner_cost(island, ctx, PARTS, "default", "auto_parts", prices)
    assert inner > 0
    assert seen[0] == (inner, main + math.ceil(need - 1e-9) * plant)
    assert seen[1][0] == inner  # 島の部品の値段によらない


def test_parts_plant_waits_for_its_assembler_until_the_deadline(game, base):
    """組で建てた部品工場は、自動車工場が動き出すまで赤字と休みを数えない。期限を過ぎれば組を解き、
    いつもの撤退に戻る（島に解いたターンを残し、しばらく組で建てない）。自動車工場が動き出せば
    印を消す。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    f = private_members(game, island)[0]
    f.economy["loss_streak"] = 5
    f.economy["idle_turns"] = 5
    f.ext[GROUP_FOR] = {"kind": AUTO, "recipe": "default", "until": ctx.turn + 3}
    assert EntryExit._group_waiting(island, ctx, f)
    EntryExit._exit_signal(island, ctx, f, 0, True)
    assert f.economy["loss_streak"] == 0 and f.economy["idle_turns"] == 0
    assert EntryExit._group_maker(island, ctx, AUTO) is f
    # 自動車工場が建ち始めると、待つ相手が決まる（次の組み立ては同じ部品工場を待たない）
    partner = private_members(game, island)[1]
    f.ext[GROUP_FOR]["partner"] = partner.id
    assert EntryExit._group_maker(island, ctx, AUTO) is None
    partner.operating_rate = 0
    assert EntryExit._group_waiting(island, ctx, f)
    # 動き出したら印を消す（組は揃った。解いたとは数えない）
    partner.operating_rate = 1000
    assert not EntryExit._group_waiting(island, ctx, f)
    assert GROUP_FOR not in f.ext
    assert not group_cooling(island, ctx, AUTO)
    # 期限を過ぎた
    f.ext[GROUP_FOR] = {"kind": AUTO, "recipe": "default", "until": ctx.turn - 1}
    assert not EntryExit._group_waiting(island, ctx, f)
    assert GROUP_FOR not in f.ext
    assert group_cooling(island, ctx, AUTO)
    assert island.log[-1].kind == "group_entry_dissolved"


def test_assembler_is_funded_by_the_parts_plants_firm(game, base):
    """組の自動車工場は、部品工場の企業が預金で出せればその企業が出す（同業や資本家階級より先）。
    出せなければいつもの順。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    number = co.firm_of(f)
    cash = co.spare(island, number)
    assert cash > 0
    assert co.choose_investor(island, game.defs, AUTO, cash, prefer=number) == (
        co.entity(number),
        number,
    )
    usual = co.choose_investor(island, game.defs, AUTO, cash + 1)
    assert co.choose_investor(island, game.defs, AUTO, cash + 1, prefer=number) == usual
    # 出さない企業は prefer でも出さない
    assert co.choose_investor(island, game.defs, AUTO, cash, None, number, number) != (
        co.entity(number),
        number,
    )
    assert household(OWNERS)


def test_parts_plant_enters_first_and_the_assembler_follows_in_its_firm(game, base, monkeypatch):
    """自動車の値段が組の参入が見合う値段を超えると、部品工場が先に建つ（組の印つき）。部品工場が
    建っている途中なら自動車工場が続き、部品工場と同じ企業に入る。"""
    import hakoniwa_core.modules.companies as companies

    world = copy.deepcopy(base)
    island = no_auto_island(game, world, monkeypatch)
    ctx = context(game, world)
    price = entry_price(island, ctx, "automobile")
    high = price * 3
    island.economy.prices["automobile"] = high
    island.economy.price_history["automobile"] = [high] * 12
    # 資本家階級に、部品工場と自動車工場を建てる出資の分を持たせる（試験のため）
    owners = household(OWNERS)
    bank = banks.active_banks(island)[0]
    ledger.create(island, owners, ledger.DEPOSIT, bank, 10**10, "test")
    before = {f.id for f in island.facilities}
    built = None
    for _ in range(6):
        if EntryExit()._enter(island, ctx, False):
            new = [f for f in island.facilities if f.id not in before]
            built = next((f for f in new if f.kind in (AUTO, PARTS)), None)
            if built is not None:
                break
            before |= {f.id for f in new}
    assert built is not None and built.kind == PARTS, island.economy.finance.get("entry_block")
    assert built.ext[GROUP_FOR]["kind"] == AUTO
    companies.enroll(island, ctx)
    number = co.firm_of(built)
    assert number is not None
    # 部品工場が建っている途中: 自動車工場が続く
    monkeypatch.setattr(EntryExit, "_coming", staticmethod(lambda _i, _c: {"auto_parts"}))
    monkeypatch.setattr(EntryExit, "_pipeline", staticmethod(lambda _i, _c: {"auto_parts"}))
    before = {f.id for f in island.facilities}
    follower = None
    for _ in range(6):
        if EntryExit()._enter(island, ctx, False):
            new = [f for f in island.facilities if f.id not in before]
            follower = next((f for f in new if f.kind == AUTO), None)
            if follower is not None:
                break
            before |= {f.id for f in new}
    assert follower is not None, island.economy.finance.get("entry_block")
    assert co.firm_of(follower) == number
    assert built.ext[GROUP_FOR]["partner"] == follower.id
