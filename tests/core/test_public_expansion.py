"""建て増した学校が教員を雇う（倭国さんの報告。掲示板 #25 の 70 番）と、教育費が足りない学校の教材。

- 建て増しで操業目標を割り直すのは、売れ行きで目標を上げ直す施設だけ（物を作らない学校・大学・
  警察・軍の施設と、毎ターン電気の需要で目標を決める発電所は割り直さない）。
- 前の版で目標が半分のまま残った物を作らない施設は、止めた施設を除き、市場の段の終わりに全力へ戻る。
- 教育費が足りない学校は、手元の教材があっても使う分の代金を見込んで教育費を分ける（在庫の
  あるうちに教育費を賃金に使い切り、教材が尽きた月に休校同然になるのを繰り返さない）。
"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import funds
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.construction import rescale_target
from hakoniwa_core.state import OrderState
from hakoniwa_core.state.generate import generate_world

EDUCATION = "education"
GOODS = "consumer_goods"


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(3, 2, game.config, game.defs)
    game.engine.run_turn(world)  # 経済のある島にする
    return world


def _school(island):
    return next(f for f in island.facilities if f.kind == "school" and f.built)


def _expand(game, world, island, f):
    island.orders = [
        OrderState(id=1, seq=1, kind="build", payload={"facility": f.kind, "x": f.x, "y": f.y})
    ]
    game.engine.run_turn(world)
    assert island.orders[0].status == "done", island.orders[0].reason
    for _ in range(10):
        if fac.size_of(f) == 2:
            return
        game.engine.run_turn(world)
    raise AssertionError("建て増しが済まない")


def test_only_facilities_that_raise_targets_by_sales_are_rescaled(game, world):
    island = world.islands[1]
    power = game.config.get("power.good")
    school = _school(island)
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant" and f.built)
    factory = next(
        f
        for f in island.facilities
        if f.built and any(g != power for g in fac.recipe_of(game.defs, f)["outputs"])
    )
    for f in (school, plant, factory):
        f.economy["target"] = fac.FULL
        rescale_target(game.defs, game.config, f, 2)
    assert school.economy["target"] == fac.FULL
    assert plant.economy["target"] == fac.FULL
    assert factory.economy["target"] == fac.FULL // 2


def test_expanded_school_hires_the_whole_size_when_the_budget_allows(game, world):
    island = world.islands[1]
    school = _school(island)
    island.policy.budget[EDUCATION] *= 3
    _expand(game, world, island, school)
    for _ in range(3):
        game.engine.run_turn(world)
    assert school.economy["target"] == fac.FULL
    assert school.workers == fac.workers_of(game.defs, school)
    assert school.operating_rate == fac.FULL


def test_expanded_school_with_the_same_budget_is_held_by_the_budget(game, world):
    island = world.islands[1]
    school = _school(island)
    before = school.workers  # 規模 1 の全力
    _expand(game, world, island, school)
    rates = []
    for _ in range(6):
        game.engine.run_turn(world)
        assert school.economy["target"] == fac.FULL
        assert school.workers > before  # 建て増した分も、教育費の許す分は雇う
        assert school.workers < fac.workers_of(game.defs, school)
        assert str(school.status).startswith("budget")  # 状態は「予算（教育費）」
        rates.append(school.operating_rate)
    assert min(rates) >= max(rates) * 0.9  # 平らに動く


def test_half_target_left_by_the_old_version_returns_to_full(game, world):
    island = world.islands[1]
    school = _school(island)
    school.ext[fac.SIZE] = 2
    school.economy["target"] = fac.FULL // 2
    game.engine.run_turn(world)
    assert school.economy["target"] == fac.FULL


def test_stopped_facility_keeps_its_target(game, world):
    island = world.islands[1]
    school = _school(island)
    school.economy["target"] = fac.FULL // 2
    school.economy["closing"] = world.turn
    game.engine.run_turn(world)
    if school in island.facilities:
        assert school.economy.get("target", 0) <= fac.FULL // 2
        assert school.workers == 0


def test_budget_short_school_restocks_every_month_within_the_budget(game, world, monkeypatch):
    island = world.islands[1]
    school = _school(island)
    island.policy.budget[EDUCATION] = int(island.policy.budget[EDUCATION] * 0.7)
    flows = {"in": 0, "out": 0}
    real_add, real_put = stock.add, stock.put_batches
    real_take_batches = stock.take_batches  # stock.take もこれを通る

    def add(lots, good, qty, *args, **kwargs):
        if lots is school.stock and good == GOODS and qty > 0:
            flows["in"] += qty
        return real_add(lots, good, qty, *args, **kwargs)

    def put_batches(lots, good, batches):
        if lots is school.stock and good == GOODS:
            flows["in"] += sum(b[1] for b in batches)
        return real_put(lots, good, batches)

    def take_batches(lots, good, qty, *args, **kwargs):
        got = real_take_batches(lots, good, qty, *args, **kwargs)
        if lots is school.stock and good == GOODS:
            flows["out"] += sum(b[1] for b in got)
        return got

    monkeypatch.setattr(stock, "add", add)
    monkeypatch.setattr(stock, "put_batches", put_batches)
    monkeypatch.setattr(stock, "take_batches", take_batches)
    game.engine.run_turn(world)  # 予算を下げた最初の月は除く
    rates, restocks = [], 0
    for _ in range(12):
        flows["in"] = flows["out"] = 0
        before = stock.total(school.stock, GOODS)
        game.engine.run_turn(world)
        after = stock.total(school.stock, GOODS)
        # 在庫の増減 ＝ 買った量 − 使った量（傷んで捨てた分を除く）
        assert after - before == flows["in"] - flows["out"]
        restocks += flows["in"] > 0
        state = funds.fiscal_state(island)
        assert state["used"][EDUCATION] <= state["caps"][EDUCATION]  # 教育費の月割りに収まる
        rates.append(school.operating_rate)
    assert restocks >= 6  # 毎月のように買い足す（数ターンおきにまとめて買い直さない）
    assert min(rates) >= max(rates) * 0.9  # 教材が尽きた月に落ちない
