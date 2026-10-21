"""核を持たない約束（条項 no_nuclear。設計書 15-military の MI-47）。

両国が、核施設を持たず核弾頭を持たず、核を使わないと約束する条項。破ってもゲームは止めず、違反として
記録して信用の傷にする（条約の軍事の条項と同じ扱い。2 国の間の約束で、多くの国と結ぶには国どうしに
1 本ずつ結ぶ）。

- 持つ: 毎ターン `watch` が見る。建ち終わった核施設か核弾頭（国の倉庫と基地の在庫）を持つ国は、
  持ち始めたターンに 1 回違反とする。すっかり手放すと、また持ち始めたときに記録される
- 使う: 核を撃った国は `used` で、効いている約束ごとに違反とする
"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import treaties as tr
from hakoniwa_core.state import IslandState, WorldState

FACILITY = "nuclear_facility"
WARHEAD = "nuclear_warhead"


def holds(island: IslandState) -> bool:
    """島が核の備え（建ち終わった核施設か核弾頭）を持っているか。"""
    if stock.total(island.warehouse, WARHEAD) > 0:
        return True
    for f in island.facilities:
        if f.built and f.kind == FACILITY:
            return True
        if stock.total(f.stock, WARHEAD) > 0:
            return True
    return False


def _bound(world: WorldState, island_id: int) -> list[dict]:
    return [
        t
        for t in tr.of_island(world, island_id)
        if t["state"] == tr.IN_FORCE and cl.NO_NUCLEAR in tr.kinds(t)
    ]


def used(world: WorldState, config: GameConfig, by: int, turn: int) -> None:
    """核を使った国が結んでいる、核を持たない約束の違反を記録する。"""
    weight = config.get("military.nuclear.treaty_weight")
    for t in _bound(world, by):
        tr.violate(world, config, t, by, cl.NO_NUCLEAR, "nuclear_used", turn, weight)


def watch(world: WorldState, config: GameConfig, turn: int) -> None:
    """効いている約束の当事国が核の備えを持ち始めたら、違反を 1 回記録する。"""
    weight = config.get("military.nuclear.treaty_weight")
    for t in tr.treaties(world):
        if t["state"] != tr.IN_FORCE or cl.NO_NUCLEAR not in tr.kinds(t):
            continue
        clause = next(c for c in t["clauses"] if c["type"] == cl.NO_NUCLEAR)
        flagged = clause.setdefault("held", [])
        for party in t["parties"]:
            island = world.islands.get(party)
            if island is None:
                continue
            if holds(island):
                if party not in flagged:
                    flagged.append(party)
                    tr.violate(world, config, t, party, cl.NO_NUCLEAR, "nuclear_held", turn, weight)
            elif party in flagged:
                flagged.remove(party)
