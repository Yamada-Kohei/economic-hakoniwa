"""強制移住の命令（force_relocate。W-286）。

権威主義・全体主義の国（regimes.yaml の特色 forced_relocation）は、住める数を超えて住む人
（過密の分。住宅地のないマスは全員）を、指した今ある町へ移せます。現実ではソ連の国内旅券で
大都市から人を出す・特別移住地へ送る動きや、中国の上山下郷（町にあふれた若者を村へ送る）に
当たります。

- 空きのある町へは、人が毎ターン自分で移ります（消費と人口の段の _migrate）。なので国が移す先を
  自分で選ぶ形は置かず、遊ぶ人が移す先と人数を指します。住める数を超えて入れてよく、超えた分には
  今の過密の決まり（亡くなる人が増え、生まれる人が減り、幸福度が下がる）がそのまま効きます。
- 移す先が自給の農業をする村なら、村のままでいられる人数（village_size）までしか入れません
  （超えた分は次のターンに今の決まりで町へ押し出されるため）。
- 運ぶ費用: 人数 × 平均賃金（1 か月）× min(freight.max_rate, freight.per_step × 道のり)。
  島の中の運賃と同じ率で、運ぶ物の値打ちを移す人の 1 か月の賃金とみなします。国庫から島の
  運送業へ払います。
- 移る間: 命令のターンは、移る人はどのマスにもいません（雇用・生産・市場の段で働かない。家計の
  人数は変わらないので食べ物は買います）。消費と人口の段の初めに着きます（arrive）。
- 政情: 直近 1 年に移した人の住民に対する割合を、出国を止められて出られない人の割合に足し、
  1% あたり migration.trapped を、合わせて migration.trapped_max まで政情の目標から引きます
  （上限は一度だけ。modules/politics の events_bonus）。幸福度には足しません（政情の式が
  幸福度を読むので、同じ重さを二度数えないため）。
"""

from __future__ import annotations

from typing import Any, NamedTuple

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import Payer, books, entity_of, funds, is_transport
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis

Pos = tuple[int, int]
KEY = "relocation"  # island.ext: 移動中の人（moving）と、直近に移した人（recent: [ターン, 人数]）


class Plan(NamedTuple):
    source: Pos
    target: Pos
    count: int  # 移す人数（過密の分と、村の上限で抑えた後）
    steps: float  # 道のり（陸のマス）
    cost: int  # 運ぶ費用（国庫から）
    before: tuple[int, int, int, int]  # (出る所の人口, 住める数, 着く所の人口, 住める数)


def state_of(island: IslandState) -> dict[str, Any]:
    return island.ext.setdefault(KEY, {})


def allowed(island: IslandState, defs: Defs) -> bool:
    return bool(politics.trait(island, defs, "forced_relocation", False))


def movable(defs: Defs, island: IslandState, config: Any, pos: Pos) -> int:
    """pos から移せる人数（住める数を超えた分。住宅地のないマスは全員）。"""
    from hakoniwa_core.modules.population import homes_of

    home = homes_of(island, config).get(pos)
    capacity = fac.housing_capacity(defs, home) if home is not None else 0
    return max(0, island.tile(*pos).population - capacity)


def plan(defs: Defs, config: Any, island: IslandState, payload: dict[str, Any]) -> Plan:
    """命令の中身を確かめ、移す人数と費用を出す（足りなければ OrderFailed）。"""
    from hakoniwa_core.modules.population import _built_farming, homes_of, village_size

    if not island.has_economy:
        raise OrderFailed("reason_no_economy")
    if not allowed(island, defs):
        raise OrderFailed("reason_relocation_regime")
    source = (payload["x"], payload["y"])
    target = (payload["to_x"], payload["to_y"])
    for x, y in (source, target):
        if not (0 <= x < island.size and 0 <= y < island.size):
            raise OrderFailed("reason_out_of_bounds")
    if source == target:
        raise OrderFailed("reason_relocation_same")
    homes = homes_of(island, config)
    home = homes.get(target)
    if home is None:
        raise OrderFailed("reason_relocation_destination", x=target[0], y=target[1])
    count = min(int(payload["count"]), movable(defs, island, config, source))
    if count <= 0:
        raise OrderFailed("reason_relocation_not_crowded", x=source[0], y=source[1])
    tile = island.tile(*target)
    if _built_farming(defs, home):
        count = min(count, village_size(defs) - tile.population)
        if count <= 0:
            raise OrderFailed("reason_relocation_village_full", x=target[0], y=target[1])
    distance = freight_rates.Routes(island, defs).between(source, target)
    if distance is None:
        raise OrderFailed("reason_relocation_unreachable", x=target[0], y=target[1])
    steps = distance / freight_rates.STEP
    rate = min(
        to_basis(config.get("freight.max_rate")),
        round(to_basis(config.get("freight.per_step")) * steps),
    )
    cost = part(count * island.economy.average_wage, rate)
    if funds.available(island, Payer(ledger.GOV)) < cost:
        raise OrderFailed("reason_treasury_short", amount=cost)
    src_home = homes.get(source)
    before = (
        island.tile(*source).population,
        fac.housing_capacity(defs, src_home) if src_home is not None else 0,
        tile.population,
        fac.housing_capacity(defs, home),
    )
    return Plan(source, target, count, round(steps, 2), cost, before)


def check_force_relocate(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    # 命令を並べるときの確かめ（設定のいらない所だけ。人数と費用は実行のときに見直す）
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")
    if not allowed(island, defs):
        raise OrderFailed("reason_relocation_regime")
    if (payload["x"], payload["y"]) == (payload["to_x"], payload["to_y"]):
        raise OrderFailed("reason_relocation_same")


def force_relocate(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """過密の分の人を、指した町へ移す（命令のターンは移動中で働かず、消費と人口の段の初めに着く）。"""
    p = plan(ctx.defs, ctx.config, island, payload)
    island.tile(*p.source).population -= p.count
    state = state_of(island)
    state.setdefault("moving", []).append({"x": p.target[0], "y": p.target[1], "count": p.count})
    recent = [r for r in state.get("recent", []) if ctx.turn - r[0] < YEAR]
    recent.append([ctx.turn, p.count])
    state["recent"] = recent
    _pay(island, ctx, p.cost)
    island.stats["population.relocated"] = island.stats.get("population.relocated", 0) + p.count
    island.log.append(
        LogEntry(
            "relocation_ordered",
            {
                "x": p.source[0],
                "y": p.source[1],
                "to_x": p.target[0],
                "to_y": p.target[1],
                "count": p.count,
                "cost": p.cost,
            },
        )
    )


def _pay(island: IslandState, ctx: StageContext, cost: int) -> None:
    """運ぶ費用を国庫から島の運送業へ払う（運送業の運賃の収入になり、雇う人数に効く）。"""
    if cost <= 0:
        return
    carrier = next((f for f in island.facilities if is_transport(ctx.config, f)), None)
    if carrier is not None:
        entity = entity_of(ctx.config, carrier)
        if entity != ledger.GOV:
            funds.pay(island, Payer(ledger.GOV), entity, cost, "relocation_freight")
        books.add(island, carrier.id, books.SALES, cost)
    freight = island.economy.freight
    surcharge = freight.get("surcharge", BASIS) or BASIS
    # 運賃の収入（割り増しを含む形で足す。payroll が割り増しを除いて読む）
    freight["paid"] = freight.get("paid", 0) + cost * surcharge // BASIS


def arrive(island: IslandState) -> int:
    """移動中の人を、着く町に入れる（消費と人口の段の初め）。入れた人数を返す。"""
    moving = state_of(island).pop("moving", [])
    total = 0
    for m in moving:
        island.tile(m["x"], m["y"]).population += m["count"]
        total += m["count"]
    return total


def in_transit(island: IslandState) -> int:
    return sum(m["count"] for m in island.ext.get(KEY, {}).get("moving", []))


def recent_share(island: IslandState, turn: int) -> float:
    """直近 1 年に移した人の、住民に対する割合（政情の段が読む）。"""
    found = island.ext.get(KEY, {}).get("recent", [])
    moved = sum(n for t, n in found if turn - t < YEAR)
    people = sum(t.population for t in island.tiles if t.population > 0) + in_transit(island)
    return moved / people if people > 0 else 0.0
