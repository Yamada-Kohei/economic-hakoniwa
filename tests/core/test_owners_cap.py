"""資本家階級の人数を財産と所得で暮らせる分までにする（LB-42・FM-58・LB-35・PL-20）。

公開世界の島 9 で、計画経済から混合経済へ移ったときに住民の割合どおり置かれた資本家階級が、株も
配当もないまま預金が尽きて飢えた（2026-11-08）。ターンを回す試験は、記録の段が毎ターン帳簿を
確かめる（tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import GENERAL, OWNERS, banks, household
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.companies import owners_collateral
from hakoniwa_core.economy.households import (
    CAP,
    SUPPORT,
    consumption,
    draw_founders,
    move_people,
    owners_room,
    pledged,
    share_target,
    split_persons,
    update_owners_cap,
)
from hakoniwa_core.modules import politics as politics_stage
from hakoniwa_core.modules.labor import Payroll
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import run_checked

BASE_TURNS = 24


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, BASE_TURNS)
    return world


def island_of(world, economy):
    return next(i for i in world.islands.values() if i.regime["economy"] == economy)


def population(island):
    return sum(h["persons"] for h in island.economy.households.values())


def drain(island, layer):
    entity = household(layer)
    ledger.pay(island, entity, ledger.GOV, ledger.money(island, entity), "unemployment_benefit")


def test_room_is_where_spendable_per_owner_meets_worker_income(game, base):
    """暮らせる人数は、1人が使える額（所得 + c2 × 財産）が一般の1人あたりの所得に届く人数。"""
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    config = game.config
    hh = island.economy.households
    owners, general = hh[OWNERS], hh[GENERAL]
    keep = config.get("household.income_turns")
    income = sum(owners["income"][-keep:]) / len(owners["income"][-keep:])
    per_general = sum(general["income"][-keep:]) / len(general["income"][-keep:])
    per_general /= general["persons"]
    c2 = config.get("household.layers")[OWNERS]["c2"]
    deposits = ledger.money(island, household(OWNERS))
    debt = pledged(island, OWNERS)
    kept = min(deposits, debt)
    movable = (deposits - kept) / owners["persons"]
    expected = (income + c2 * (owners_collateral(island) - debt + kept)) / (
        per_general - c2 * movable
    )
    assert owners_room(island, config, OWNERS) == int(expected)
    # 建国の島の資本家階級は株を持つので、上限は住民の割合よりずっと多い（上限を持たない）
    assert owners_room(island, config, OWNERS) > owners["persons"]
    update_owners_cap(island, config, population(island))
    assert owners.get(CAP) is None
    # 所得の記録がなければ（建国のターン）上限なし
    owners["income"] = []
    assert owners_room(island, config, OWNERS) is None


def test_owners_without_income_or_wealth_move_to_workers(game, base, monkeypatch):
    """所得も財産もない資本家階級は、その人数の段で一般の層へ移り、預金を持って出る。0人でも層は
    残り、物を買わない。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = island_of(world, "mixed")
    config = game.config
    hh = island.economy.households
    hh[OWNERS]["income"] = [0, 0, 0]
    # 株を全部手放したとみなす（株の値打ちを数えない）
    monkeypatch.setattr(co, "owners_collateral", lambda _island: 0)
    # 資本家階級に、株を担保にした借入の取り置き（LB-42）を超える預金を持たせる（道筋によっては、
    # 閉じた施設の出資が戻る前で、預金がみな取り置きになっている。2026-11-10 の FM-60 で閉鎖が
    # 売りに出す間だけ延びた）
    spare = pledged(island, OWNERS) + 1_000_000 - ledger.money(island, household(OWNERS))
    if spare > 0:
        ledger.pay(island, household(GENERAL), household(OWNERS), spare, "layer_transfer")
    open_books(island)
    # 持って出る預金を持たせる（2026-11-10 に工事が海外の商人からも資材を買うようにした後は、この
    # 島の資本家階級は 24 ターン目までに預金のほとんどを出資と借金の担保に回している）
    ledger.pay(island, ledger.GOV, household(OWNERS), 5_000_000, "grant")
    total = population(island)
    before = ledger.money(island, household(GENERAL))
    update_owners_cap(island, config, total)
    room = hh[OWNERS][CAP]
    assert room < hh[OWNERS]["persons"]
    move_people(island, split_persons(island, game.defs, total))
    assert hh[OWNERS]["persons"] == room
    assert ledger.money(island, household(GENERAL)) > before
    books_ok(game, island)
    hh[OWNERS]["persons"] = 0
    assert consumption(island, OWNERS, config, 0.0) == 0


def test_change_from_planned_starts_owners_from_what_they_own(game, base):
    """計画経済から移ると、株も配当もない資本家階級は0人から始まり（PL-20・LB-42）、民営の参入の
    出資は一般の層の預金から出した人が資本家階級へ移って出す（FM-58）。資本家階級は飢えない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    open_books(island)
    politics_stage.change_economy(island, {"economy": "mixed"}, context(game, world))
    books_ok(game, island)
    owners = island.economy.households[OWNERS]
    assert owners["persons"] == 0 and owners[CAP] == 0
    hungry = []
    for _ in range(24):
        run_checked(game, world, 1)
        owners = island.economy.households[OWNERS]
        assert owners["persons"] <= share_target(owners, population(island))
        hungry.append(owners.get("hunger", 0.0))
    # 出資した人が資本家階級に入って出資した（資本家階級が株を持つ）。軽い店だけの参入だと、工事中の
    # 施設の株は財産で暮らせる人数（LB-42）に数えないので、出資した人は次のターンに一般の層へ戻る
    # ことがある。人数ではなく株で見る
    assert any(f.ext.get("private_entry") for f in island.facilities)
    assert sum(ledger.holdings(island, household(OWNERS), ledger.EQUITY).values()) > 0
    assert max(hungry) < 1.0


def test_founders_bring_their_savings_from_workers(game, base):
    """資本家階級の預金で足りない出資は、一般の層の1人あたりの預金を持つ人が移って出す（FM-58）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    politics_stage.change_economy(island, {"economy": "mixed"}, context(game, world))
    hh = island.economy.households
    open_books(island)
    per = ledger.money(island, household(GENERAL)) // hh[GENERAL]["persons"]
    need = per * 10 + 1
    moved = draw_founders(island, need, ledger.money(island, household(OWNERS)))
    assert moved == 11
    assert hh[OWNERS]["persons"] == 11 and hh[OWNERS][CAP] == 11
    assert ledger.money(island, household(OWNERS)) >= need
    books_ok(game, island)
    # 住民の割合を超えては移らない
    delta = share_target(hh[OWNERS], population(island)) - hh[OWNERS]["persons"]
    hh[OWNERS]["persons"] += delta
    hh[GENERAL]["persons"] -= delta
    assert draw_founders(island, 10**12, 0) == 0


def test_starving_owners_receive_family_support(game, base, monkeypatch):
    """株はあっても預金が尽きて飢えた資本家階級も、家族の支え合いを受ける（LB-35）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    hh = island.economy.households
    drain(island, OWNERS)
    hh[OWNERS]["income"] = [0, 0, 0]
    hh[OWNERS]["fed"] = 0
    monkeypatch.setattr(co, "owners_collateral", lambda _island: 0)
    island.scratch.pop("owners_shares_worth", None)
    open_books(island)
    island.scratch["income"] = {GENERAL: 10**9}
    Payroll._support(island, context(game, world))
    got = ledger.money(island, household(OWNERS))
    assert got > 0
    assert island.scratch[SUPPORT][OWNERS] == got
    books_ok(game, island)


def test_change_to_planned_moves_owners_mortgages_and_hunger(game, base):
    """計画経済に移って資本家階級をまとめるとき、住宅ローンと飢えの蓄えも一般の層へ移す（PL-20）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    hh = island.economy.households
    hh[OWNERS]["hunger"] = 2.0
    persons = hh[OWNERS]["persons"]
    general = hh[GENERAL]["persons"]
    held = hh[GENERAL].get("hunger", 0.0)
    open_books(island)
    politics_stage.change_economy(island, {"economy": "planned"}, context(game, world))
    books_ok(game, island)
    assert OWNERS not in hh
    assert not [
        x
        for x in banks.loans_by_borrower(island, household(OWNERS))
        if x.purpose == banks.MORTGAGE and banks.outstanding(island, x) > 0
    ]
    expected = (held * general + 2.0 * persons) / (general + persons)
    assert hh[GENERAL]["hunger"] == pytest.approx(expected, rel=0.01, abs=1e-4)


def test_people_who_change_layers_carry_the_food_they_bought(game, base):
    """市場の段の後に層を移った人（参入の段で出資して資本家階級へ移った人など）は、このターンに
    買った食べ物を1人あたりの量で持って移る（2026-11-10。前は元の層に残り、移った先の層だけが
    食べられずに飢えの蓄えがたまった）。島の食べ物の合計は変わらない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "mixed")
    hh = island.economy.households
    persons = hh[GENERAL]["persons"]
    moved = persons // 10
    island.scratch["household_goods"] = {GENERAL: {"grain": 10_000, "fish": 5_000}}
    hh[GENERAL]["persons"] -= moved
    hh[OWNERS]["persons"] += moved
    move_people(island, {OWNERS: moved})
    bought = island.scratch["household_goods"]
    for good, total in (("grain", 10_000), ("fish", 5_000)):
        assert bought[OWNERS][good] == total * moved // persons
        assert bought[GENERAL][good] + bought[OWNERS][good] == total
