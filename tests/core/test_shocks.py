"""揺さぶりの試験（フェーズ2-2 の関門）: 島に揺さぶりを起こしてから 120 ターン回し、動く向きが
正しく、帳簿が崩れないことを確かめる。

揺さぶりを起こした島と、起こさなかった島（対照）を同じ種で回して比べる。記録の段が毎ターン帳簿を
確かめるので、帳簿が崩れればその場で止まる。
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.autoplan import plan_turn, queue_orders
from hakoniwa_core.economy import OWNERS, banks, household
from hakoniwa_core.game import load_game
from hakoniwa_core.state import OrderState, WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.serialize import world_from_dict

START = 24  # 揺さぶりを起こすターン
AFTER = 120  # 揺さぶりの後に回すターン数
CASCADE_TURNS = 24  # 破綻処理の後、ほかの銀行の破綻を連鎖と見るターン数
DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で START ターン回した3島（市場・混合・計画経済）の世界。"""
    world = generate_world(1, 3, game.config, game.defs)
    drive(game, world, START)
    return world


def only(world: WorldState, economy: str) -> WorldState:
    """経済体制が economy の島だけを残した写し（試験を軽くするため）。"""
    copied = copy.deepcopy(world)
    copied.islands = {
        i: island for i, island in copied.islands.items() if island.regime["economy"] == economy
    }
    return copied


def drive(game, world, turns, extra=None, skip=(), each=None):
    """標準の計画で turns ターン回す。extra(island) の命令を計画の後ろに足し、skip の種類の命令は
    計画から外す。each(island) を毎ターンの終わりに呼ぶ。"""
    for _ in range(turns):
        for island in world.islands.values():
            planned = plan_turn(island, game.defs, game.config, world.turn + 1)
            queue_orders(island, [o for o in planned if o[0] not in skip])
            if extra is not None:
                queue_orders(island, extra(island))
        game.engine.run_turn(world)
        if each is not None:
            for island in world.islands.values():
                each(island)


def history(game, world, turns, each=None, **options):
    """回しながら、島の統計と市場の値段を毎ターン残す（each(island) も毎ターン呼ぶ）。"""
    rows = []

    def keep(island):
        prices = {m.good: m.price for m in island.markets}
        rows.append(
            {
                **island.stats,
                "turn": world.turn,
                "prices": prices,
                "log": [e.kind for e in island.log],
            }
        )
        if each is not None:
            each(island)

    drive(game, world, turns, each=keep, **options)
    return rows


def mean(rows, key):
    values = [r.get(key, 0) for r in rows]
    return sum(values) / len(values)


@pytest.fixture(scope="module")
def control(game, base):
    """揺さぶりを起こさない市場経済の島の記録。"""
    return history(game, only(base, "market"), AFTER, skip=("set_budget",))


# --- 刷りすぎ ---------------------------------------------------------------------


def test_printing_money_raises_prices(game, base, control):
    """国の支出を中央銀行の直接引き受けで賄うと、物価が上がる。

    支出は減税と失業手当にする（お金がそのまま家計に渡る）。公共事業費を増やすと、国の工場が増えて
    物が多く作られるので、物価は下がることもある。失業が多い島では、物価より先に生産が増えることも
    ある（報告書 phase2/2-2-report.md）。
    """
    world = only(base, "market")

    def printing(island):
        orders = []
        if world.turn == START:
            orders += [
                ("set_taxes", {"income_tax": 0, "consumption_tax": 0}),
                ("set_labor_policy", {"unemployment_benefit": 100}),
            ]
        gdp = island.stats.get("gdp", 0)
        return orders + [
            ("set_bond_policy", {"underwrite": int(gdp), "underwrite_term": "bond_1y"})
        ]

    skip = ("set_budget", "set_taxes", "set_labor_policy")
    rows = history(game, world, AFTER, extra=printing, skip=skip)
    # 物価は上下に揺れるので、最後の 60 ターンの平均で比べる
    assert mean(rows[-60:], "cpi") > mean(control[-60:], "cpi") * 1.2
    assert max(p for r in rows for p in r["prices"].values()) < 10**12  # 桁あふれしない


# --- 利上げ -----------------------------------------------------------------------


def test_rate_hike_raises_deposit_and_lending_rates(game, base, control):
    """利上げは預金と融資の金利に届く。

    設計書の「融資と参入が減り、物価上昇率が下がる」は、今のモデルでは確かめない。民営工場の見込みの
    利益率が金利よりずっと高く参入は出資者のお金で決まること、預金の利子と銀行の利ざやが家計の収入を
    増やすことから、8島で比べても向きがそろわない（報告書 phase2/2-2-report.md）。家計の借入と株価が
    入る段で見直す。
    """
    world = only(base, "market")
    island = next(iter(world.islands.values()))
    rate = island.policy.policy_rate * 100 // 10000 + 5

    def hike(island):
        return [("set_monetary_policy", {"policy_rate": rate})] if world.turn == START else []

    lending = []

    def keep(island):
        loans = [x for x in island.loans if banks.outstanding(island, x) > 0]
        if loans:
            lending.append(sum(x.rate for x in loans) / len(loans) * 100 / 10000)

    rows = history(game, world, AFTER, extra=hike, skip=("set_budget",), each=keep)
    assert island.policy.policy_rate == rate * 10000 // 100
    late, base_late = rows[-60:], control[-60:]
    # 預金金利は少しずつ「政策金利 − 利ざや」へ寄っていく
    assert mean(late, "deposit_rate") > mean(base_late, "deposit_rate") + 3
    # 融資は変動金利なので、残っている融資の金利もすぐ上がる
    assert sum(lending[-60:]) / 60 > mean(base_late, "policy_rate") + 5


# --- 銀行の破綻 -------------------------------------------------------------------


@pytest.mark.parametrize("method", ["bailout", "transfer", "liquidation"])
def test_bank_failure_is_resolved_without_cascade(game, base, method):
    """融資の2割を貸し倒れにする。それで債務超過にならない（融資の少ない）銀行は、配当の払い
    すぎで預金の5%の穴を開けて、必ず破綻させる。"""
    world = only(base, "mixed")  # 銀行が2つある島
    island = next(iter(world.islands.values()))
    bank = max(banks.active_banks(island), key=lambda b: banks.loans_of(island, b))
    for loan in sorted(banks.loans_by_lender(island, bank), key=lambda x: x.id):
        banks.write_off(island, loan, banks.outstanding(island, loan) // 5)
    banks.drop_settled(island)
    hole = banks.net_worth(island, game.config, bank, world.turn)
    hole += banks.deposits_of(island, bank) // 20
    ledger.pay(island, bank, household(OWNERS), max(0, hole), "dividend")
    f = banks.facility_of(island, bank)

    deposits = []

    def keep(island):
        deposits.append(sum(banks.deposits_of(island, b) for b in banks.bank_list(island)))
        if any(e.kind.startswith("bank_failed") for e in island.log):
            # 標準の計画の命令に行政力を使い切られないように、命令の列の先頭に置く
            order = OrderState(
                id=max((o.id for o in island.orders), default=0) + 1,
                seq=0,
                kind="resolve_bank",
                payload={"x": f.x, "y": f.y, "method": method},
            )
            island.orders.insert(0, order)

    rows = history(game, world, AFTER, each=keep)
    logs = [[kind for kind in r["log"]] for r in rows]
    failed = [i for i, kinds in enumerate(logs) if any(k.startswith("bank_failed") for k in kinds)]
    done = {
        "bailout": "bank_bailed_out",
        "transfer": "bank_transferred",
        "liquidation": "bank_liquidated",
    }[method]
    resolved = next(i for i, kinds in enumerate(logs) if done in kinds)
    # 連鎖しない: 破綻処理の後 CASCADE_TURNS ターンのうちに、ほかの銀行が破綻しない
    assert failed[0] < resolved
    assert not [i for i in failed[1:] if i <= resolved + CASCADE_TURNS]
    # 預金は消えない（保護されない預金が削られても、島の預金の合計は大きく減らない）
    assert deposits[resolved] > deposits[failed[0]] * 0.9


# --- 国債の不履行 -----------------------------------------------------------------


def test_bond_default_raises_yields_and_hits_holders(game, base):
    world = only(base, "market")
    yields, worth = [], []

    def stop(island):
        return [("set_bond_payment", {"stop": True})] if world.turn == START else []

    def keep(island):
        yields.append(max(island.economy.finance["yields"]))
        worth.append(
            sum(
                banks.net_worth(island, game.config, b, world.turn) for b in banks.bank_list(island)
            )
        )

    rows = history(game, world, AFTER, extra=stop, each=keep)
    logs = [r["log"] for r in rows]
    # 命令は行政力の順番待ちで遅れることがあるので、不履行になったターンを探す
    i = next(i for i, kinds in enumerate(logs) if "bond_default_ordered" in kinds)
    # 利回りが跳ね上がり、国債を持っていた銀行の自己資本が減る
    premium = game.config.get("bonds.default_premium") * 10000
    assert yields[i] >= yields[i - 1] + 0.5 * premium
    assert worth[i] < worth[i - 1]
    # 不履行の後も国債は売れ（借り換えが止まらない）、利回りは上限の中に収まる
    assert any("bonds_issued" in kinds for kinds in logs[i + 1 :])
    assert all(0 <= y <= game.config.get("bonds.max_yield") * 10000 for y in yields)


# --- 発電所の停止 -----------------------------------------------------------------


def test_power_outage_moves_price_within_band_and_recovers(game, base, control):
    world = only(base, "market")
    island = next(iter(world.islands.values()))
    power = game.config.get("power.good")
    halt = 6
    plants = [
        f
        for f in island.facilities
        if f.built and power in game.defs.facilities[f.kind]["recipes"][f.recipe]["outputs"]
    ]
    # 一番大きな発電所を止める（全部止めると売り手がいなくなり、値段は付かずに前の値のまま）
    biggest = max(
        plants, key=lambda f: game.defs.facilities[f.kind]["recipes"][f.recipe]["outputs"][power]
    )
    biggest.ext["halted_until"] = world.turn + halt
    assert len(plants) > 1
    rows = history(game, world, AFTER, skip=("set_budget",))
    band = game.config.get("market.band")
    prices = [r["prices"].get(power) for r in rows]
    before = base.islands[island.id].economy.prices[power]
    for previous, price in zip([before, *prices], prices, strict=False):
        if previous and price:
            assert price <= previous * (1 + band) + 1  # 値幅の上端で止まる
    assert max(p for p in prices[:halt] if p) > before  # 停電の間は上がる
    end, base_end = rows[-1]["prices"][power], control[-1]["prices"][power]
    assert 0.5 * base_end <= end <= 2 * base_end  # 停電の後に戻る


# --- 計画経済 ---------------------------------------------------------------------


def test_planned_economy_never_bankrupts_or_stops(game, base):
    world = only(base, "planned")
    rows = history(game, world, AFTER)
    logs = {kind for r in rows for kind in r["log"]}
    assert not logs & {"facility_bankrupt", "facility_closing", "private_entry", "bank_opened"}
    assert all(r.get("gdp", 0) > 0 for r in rows)


# --- フェーズ2-1 からの移し替え -----------------------------------------------------


def test_upgrade_from_phase2_1_keeps_production(game):
    """フェーズ2-1 のコードで 24 ターン回した世界（tests/core/data/phase2_1_world.json.gz）を読み、
    12 ターン回す。生産量が、2-1 のコードで同じ 12 ターンを回したとき（phase2_1_reference.json）より
    大きく落ちないこと。"""
    with gzip.open(DATA / "phase2_1_world.json.gz") as fh:
        world = world_from_dict(json.load(fh))
    reference = json.loads((DATA / "phase2_1_reference.json").read_text(encoding="utf-8"))
    made: dict[str, float] = {}

    def keep(island):
        total = sum(v for k, v in island.stats.items() if k.startswith("production."))
        made[str(island.id)] = made.get(str(island.id), 0) + total

    drive(game, world, 12, each=keep)
    for island_id, expected in reference["production"].items():
        assert made[island_id] >= 0.85 * sum(expected), island_id
    for island in world.islands.values():
        assert island.economy.finance["version"] == banks.FINANCE_VERSION
