"""火力発電所の燃料の知らせと、回復してから再び知らせる境目（UI-87）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import freight, power_fuel, power_fuel_notice
from hakoniwa_core.economy.power_fuel_notice import LOW, NOTICES, PRICE, after_market, stock_status
from hakoniwa_core.economy.prices import subsidized_floor
from hakoniwa_core.founding import found_island
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState
from tests.core.test_founding import plan_for
from tests.core.test_recession_round1 import context
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def _plant(game, economy: str = "mixed") -> tuple[IslandState, Facility, int]:
    island = found_island(game.defs, game.config, plan_for(game, economy=economy), 1, 3, 4)
    f = next(f for f in island.facilities if power_fuel.is_thermal(game.defs, f))
    good = next(iter(power_fuel.fuel_goods(game.defs, f)))
    need = fac.recipe_of(game.defs, f)["inputs"][good]
    f.status, f.operating_rate = "", fac.FULL
    f.economy["input_plan"] = fac.FULL
    f.economy["power_previous_price"] = island.economy.prices[game.config.get("power.good")]
    return island, f, need


def _flow(f: Facility, need: int, turn: int = 1, acquired: int = 0) -> None:
    f.economy[power_fuel.FLOW] = {
        "turn": turn,
        "used": {"coal": need},
        "acquired": {"coal": acquired},
        "value": {},
    }


@pytest.mark.parametrize("owner", [OWNER_PRIVATE, OWNER_STATE])
@pytest.mark.parametrize("reason", ["supply", "price", "funds"])
def test_low_fuel_notice_once_until_stock_recovers(game, owner: str, reason: str) -> None:
    island, f, need = _plant(game)
    f.owner = owner
    f.stock = {"coal": [[0, need]]}
    power_fuel.reset_book(game.defs, f, island.economy.prices)
    f.economy["short"] = {"coal": reason}
    _flow(f, need)
    after_market(island, context(game))
    entries = [e for e in island.log if e.kind == LOW]
    assert len(entries) == 1
    assert entries[0].args["reason"] == f"fuel_short_{reason}"
    assert entries[0].args["months"] == 1
    _flow(f, need, turn=2)
    after_market(island, context(game, turn=2))
    assert len([e for e in island.log if e.kind == LOW]) == 1
    # 回復の線ちょうどで次を出せる。線の手前で足りる月は、印を外さない。
    reset = need * game.config.get("power.fuel_notice_turns") * 2
    f.stock = {"coal": [[0, int(reset) - 1]]}
    after_market(island, context(game, turn=2))
    assert f.economy[NOTICES]["low_notified"]
    f.stock = {"coal": [[0, int(reset)]]}
    after_market(island, context(game, turn=2))
    assert not f.economy[NOTICES]["low_notified"]
    f.stock = {"coal": [[0, need]]}
    _flow(f, need, turn=3)
    after_market(island, context(game, turn=3))
    assert len([e for e in island.log if e.kind == LOW]) == 2


def test_receipts_include_the_warehouse_and_suppress_a_low_notice(game) -> None:
    island, f, need = _plant(game)
    f.stock = {"coal": [[0, need]]}
    _flow(f, need, acquired=need)
    after_market(island, context(game))
    assert not any(e.kind == LOW for e in island.log)
    # 使う分が在庫で足りて short が空のときは、需要に数えない買い足しの理由を使う。
    f.economy["short"] = {}
    f.economy["fuel_restock_short"] = {"coal": "funds"}
    _flow(f, need, turn=2)
    after_market(island, context(game, turn=2))
    entry = next(e for e in island.log if e.kind == LOW)
    assert entry.args["reason"] == "fuel_short_funds"
    assert f.economy["short"] == {}


def test_low_fuel_notice_starts_strictly_below_the_threshold(game) -> None:
    island, f, need = _plant(game)
    threshold = int(need * game.config.get("power.fuel_notice_turns"))
    f.stock = {"coal": [[0, threshold]]}
    _flow(f, need)
    after_market(island, context(game))
    assert not any(e.kind == LOW for e in island.log)
    f.stock = {"coal": [[0, threshold - 1]]}
    after_market(island, context(game))
    assert any(e.kind == LOW for e in island.log)


def _price_rise(game, economy: str = "mixed") -> tuple[IslandState, Facility, int]:
    island, f, need = _plant(game, economy)
    f.stock = {"coal": [[0, need * 2]]}
    power_fuel.reset_book(game.defs, f, island.economy.prices)
    island.economy.prices["coal"] *= 100
    power = game.config.get("power.good")
    rate = freight.facility_rates(island, game.defs, game.config).get(f.id, 0)
    args = (island, game.defs, game.config, f, power, island.economy.prices, 0, rate, 1)
    f.economy["wage"] = 0
    cheap = subsidized_floor(*args)
    current = subsidized_floor(*args, fuel_prices={})
    assert cheap < current
    f.economy["power_previous_price"] = (cheap + current) // 2
    _flow(f, need)
    return island, f, need


def test_price_notice_once_and_reset_on_the_configured_line(game) -> None:
    island, f, need = _price_rise(game)
    after_market(island, context(game))
    assert len([e for e in island.log if e.kind == PRICE]) == 1
    _flow(f, need, turn=2)
    after_market(island, context(game, turn=2))
    assert len([e for e in island.log if e.kind == PRICE]) == 1
    high = island.economy.prices["coal"]
    island.economy.prices["coal"] = high // 100
    after_market(island, context(game, turn=2))
    assert not f.economy[NOTICES]["price_notified"]
    island.economy.prices["coal"] = high
    _flow(f, need, turn=3)
    after_market(island, context(game, turn=3))
    assert len([e for e in island.log if e.kind == PRICE]) == 2


def test_price_notice_uses_the_grid_price_and_the_utility_cap(game) -> None:
    island, f, _ = _price_rise(game)
    power = game.config.get("power.good")
    previous = f.economy["power_previous_price"]
    island.economy.prices[power] = previous * 100
    after_market(island, context(game))
    assert any(e.kind == PRICE for e in island.log)
    f.economy[NOTICES] = {}
    f.economy["power_previous_price"] = previous * 100
    island.policy.price_cap[power] = previous
    after_market(island, context(game))
    assert len([e for e in island.log if e.kind == PRICE]) == 2


def test_price_notice_reset_requires_a_price_below_the_line(game, monkeypatch) -> None:
    island, f, need = _plant(game)
    f.stock = {"coal": [[0, need * 2]]}
    f.economy["power_previous_price"] = 100
    f.economy[NOTICES] = {"price_notified": True}
    _flow(f, need)
    current = [90]

    def floor(*args, fuel_prices: dict[str, int] | None = None) -> int:
        return current[0] if fuel_prices is not None else 10

    monkeypatch.setattr(power_fuel_notice, "subsidized_floor", floor)
    after_market(island, context(game))
    assert f.economy[NOTICES]["price_notified"]
    current[0] = 89
    after_market(island, context(game))
    assert not f.economy[NOTICES]["price_notified"]
    current[0] = 101
    after_market(island, context(game))
    assert len([e for e in island.log if e.kind == PRICE]) == 1


@pytest.mark.parametrize("economy, status", [("planned", ""), ("mixed", "power_price")])
def test_no_price_notice_for_official_prices_or_an_already_stopped_plant(
    game, economy: str, status: str
) -> None:
    island, f, _ = _price_rise(game, economy)
    f.status = status
    after_market(island, context(game))
    assert not any(e.kind == PRICE for e in island.log)


def test_stock_status_uses_last_consumption_and_planned_target(game) -> None:
    island, f, need = _plant(game)
    f.stock = {"coal": [[0, need]]}
    f.economy["input_plan"] = fac.FULL // 2
    _flow(f, need // 4)
    row = stock_status(game.defs, game.config, f)[0]
    assert row["months"] == need / (need // 4)
    assert row["expected"] == fac.scale(need, fac.FULL // 2)
    assert row["target"] == row["expected"] * game.config.get("power.fuel_stock_turns")


def test_low_fuel_line_uses_the_same_recent_use_as_the_months_in_the_text(game) -> None:
    """見込みが実際の使用より大きくても、残りの月数が線より多ければ知らせない。

    LAN の世界の試し回しで「残り約5か月で買えていない」が出た外れ。
    """
    island, f, need = _plant(game)
    used = need // 4
    notice = game.config.get("power.fuel_notice_turns")
    # 見込み（全力）の 1.5 か月分は切るが、直近の使用では 2.5 か月分ある
    f.stock = {"coal": [[0, int(used * notice) + used]]}
    power_fuel.reset_book(game.defs, f, island.economy.prices)
    f.economy["short"] = {"coal": "funds"}
    _flow(f, used)
    after_market(island, context(game))
    assert not any(e.kind == LOW for e in island.log)
    f.stock = {"coal": [[0, used]]}
    after_market(island, context(game))
    entries = [e for e in island.log if e.kind == LOW]
    assert len(entries) == 1
    assert entries[0].args["months"] == 1
