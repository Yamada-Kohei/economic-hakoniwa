"""核兵器（フェーズ4-3 の③b。15-military の MI-46・MI-47）。

核弾頭は核施設（facilities.yaml の nuclear_facility）がウラン鉱石から作り、ミサイル基地の在庫
（`military: {warhead: 2}`）へ国の倉庫から満たす。命令 fire_missile に nuclear を付けると、
ミサイル 1 発に弾頭 1 発を積んで撃つ。迎撃ミサイルで落とせる見込みはふつうのミサイルより低い。

着弾すると、着弾したマス・隣の 1 周・その外の 1 周の 3 つの輪で、住民が亡くなり
（残りの一部は逃げる）、施設が壊れ、港と道路の整備度が大きく下がり、汚染が長く残る
（半減期 `half_life` ターン。毎ターン計算する汚染 PO-36 に足し、幸福度と農場と漁港の産出と
肥沃度に効く）。マスは消えない。
使った国は大きな違反として記録され、核を持たない約束（条項 no_nuclear。diplomacy/arms.py）を
結んでいれば、その条約の違反にもなる。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, monuments, nature
from hakoniwa_core.diplomacy import arms, credit
from hakoniwa_core.military import battle
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext

WARHEAD = arms.WARHEAD
FACILITY = arms.FACILITY


def ring(x: int, y: int, cx: int, cy: int) -> int:
    """着弾したマスからの輪の番号（0 が着弾したマス、1 が隣の 1 周）。"""
    return max(abs(x - cx), abs(y - cy))


def detonate(
    world: WorldState, ctx: StageContext, loc: IslandState, x: int, y: int, attacker: IslandState
) -> int:
    """マス (x, y) に核弾頭が落ちたときの被害。亡くなった人の数を返す。"""
    cfg = ctx.config.get("military.nuclear")
    refuge = ctx.config.get("military.refugees.abroad_share")
    rings = len(cfg["dead"])
    killed = 0
    for tile in loc.tiles:
        r = ring(tile.x, tile.y, x, y)
        if r >= rings:
            continue
        if tile.population > 0:
            dead = min(tile.population, round(tile.population * cfg["dead"][r]))
            flee = round((tile.population - dead) * cfg["flee"])
            tile.population -= dead + flee
            killed += dead
            battle._flee(world, loc, tile.x, tile.y, flee, refuge)
        facility = loc.facility_at(tile.x, tile.y)
        if facility is not None and facility.built and not fac.is_housing(ctx.config, facility):
            battle._ruin(loc, ctx, facility)
        for kind in ctx.defs.infra_kinds("transport"):
            level = infra.level(tile, kind)
            if level:
                infra.set_level(tile, kind, round(level * cfg["infra"]))
        tile.ext[nature.FALLOUT] = tile.ext.get(nature.FALLOUT, 0) + round(cfg["fallout"][r] * 1000)
    monuments.add_dead(loc, monuments.WAR_DEAD, killed)
    monuments.add_kills(attacker, killed)
    loc.stats["military.civilian_dead"] = loc.stats.get("military.civilian_dead", 0) + killed
    loc.log.append(LogEntry("nuclear_hit", {"x": x, "y": y, "dead": killed}))
    return killed


def condemn(world: WorldState, ctx: StageContext, attacker: IslandState, loc: IslandState) -> None:
    """核を使った国を、大きな違反として記録する（信用の傷）。核を持たない約束を結んでいれば、
    その条約の違反にもなる。"""
    cfg = ctx.config.get("military.nuclear")
    credit.record(
        attacker, ctx.turn, 0, "war_rules", cfg["violation_weight"], "nuclear_used", ctx.config
    )
    attacker.log.append(LogEntry("nuclear_violation", {"country": loc.name}))
    arms.used(world, ctx.config, attacker.id, ctx.turn)


def holds(island: IslandState) -> bool:
    """島が核の備え（建ち終わった核施設か核弾頭）を持っているか。"""
    return arms.holds(island)


def watch(world: WorldState, ctx: StageContext) -> None:
    """核を持たない約束の見張り（毎ターン）。"""
    arms.watch(world, ctx.config, ctx.turn)
