"""探鉱（設計書 15-military MI-68。W-234）: 国がお金と年月をかけて、島の持たない鉱業資源を探す。

島の地質は前から決まっています。資源ごとに、鉱脈（veins.py）と同じ乱数 1 本の数を引き、鉱脈の割合
（world.veins）より下なら鉱脈、鉱脈の割合 + explore.chance より下なら探鉱で見つかる鉱床、それより上
なら鉱床なしです。同じ数を使うので、鉱脈の出なかった島で鉱脈の資源が探鉱で見つかるのは、鉱脈の外の
くじだけです。

探鉱は国の施設「探鉱隊」（explore.facility。国営だけ・島に 1 つ）が行い、全力で explore.turns ターン
分動くと終わります（進みは施設の ext["explore"] が持つ。稼働率の分だけ進む）。終わりに鉱床があれば
島の鉱業資源に足し、なければ「見つからなかった」と覚えて（island.ext["explored"]）、その資源はもう
探せません。島の鉱業資源は、鉱脈と探鉱を足しても founding.max_resources より 1 つ多い所までです。

商人の値段と計画経済の公定価格の基準（始めの値段）は、探鉱で見つけた資源を除いた資源で解きます
（base_resources。途中で資源が増えて基準の値段が跳ばないように）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import veins
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState

EXPLORED = "explored"  # island.ext: 探鉱を終えた資源 → FOUND か NONE
FOUND = "found"
NONE = "none"
EXPLORE = "explore"  # 探鉱隊の ext: {"good": 探す資源, "progress": 進み（全力の 1 ターンが 1000）}
FULL = 1000


def facility_kind(config: GameConfig) -> str:
    return config.get("explore.facility")


def explored(island: IslandState) -> dict[str, str]:
    return island.ext.get(EXPLORED, {})


def base_resources(island: IslandState) -> list[str]:
    """始めの値段（商人の値段・公定価格の基準）に使う資源: 探鉱で見つけた資源を除く。"""
    found = {g for g, r in explored(island).items() if r == FOUND}
    return [g for g in island.resources if g not in found]


def most_resources(config: GameConfig) -> int:
    """鉱脈と探鉱を足した、島の鉱業資源の上限（建国で選べる数 + 1。MI-60）。"""
    return config.get("founding.max_resources") + 1


def is_full(island: IslandState, config: GameConfig) -> bool:
    return len(island.resources) >= most_resources(config)


def miner_terrains(defs: Defs, good: str) -> set[str]:
    """資源 good を掘る施設（鉱山・採石場・油井）の建つ地形。"""
    return {
        t
        for fdef in defs.facilities.values()
        if any(r.get("requires_resource") == good for r in fdef["recipes"].values())
        for t in fdef["terrain"]
    }


def candidates(defs: Defs, config: GameConfig) -> list[str]:
    """探鉱で探せる資源（explore.chance に割合のある鉱業資源。鉱脈だけの資源は除く）。"""
    chance = config.get("explore.chance")
    return sorted(
        g
        for g, d in defs.goods.items()
        if d.get("mining") and not d.get("vein_only") and chance.get(g, 0) > 0
    )


def team(island: IslandState, config: GameConfig) -> Facility | None:
    """島の探鉱隊（工事中を含む）。なければ None。"""
    kind = facility_kind(config)
    return next((f for f in island.facilities if f.kind == kind), None)


def problem(
    island: IslandState, defs: Defs, config: GameConfig, good: str
) -> tuple[str, dict[str, Any]] | None:
    """島で資源 good の探鉱を始められないときは (理由, 引数)。マスの決まりは見ない。"""
    if not island.has_economy:
        return ("reason_no_economy", {})
    if good not in candidates(defs, config):
        return ("reason_explore_resource", {"good": good})
    if island.ext.get(veins.KEY, 0) < veins.VERSION:  # 建国の後の最初の更新の前
        return ("reason_explore_not_yet", {})
    if good in island.resources:
        return ("reason_explore_have", {"good": good})
    if explored(island).get(good) == NONE:
        return ("reason_explore_tried", {"good": good})
    if is_full(island, config):
        return ("reason_explore_full", {"max": most_resources(config)})
    if team(island, config) is not None:
        return ("reason_explore_busy", {})
    terrains = miner_terrains(defs, good)
    if not any(t.terrain in terrains for t in island.tiles):
        return ("reason_explore_no_land", {"good": good})
    return None


def explorable(island: IslandState, defs: Defs, config: GameConfig) -> list[str]:
    """今、探鉱を始められる資源（マスの決まりを除く）。"""
    return [g for g in candidates(defs, config) if problem(island, defs, config, g) is None]


def has_deposit(island: IslandState, config: GameConfig, seed: int, good: str) -> bool:
    """島の地質に、探鉱で見つかる good の鉱床があるか（鉱脈の割合 ≦ 数 < 鉱脈の割合 + 割合）。"""
    share = config.get("world.veins").get(good, 0)
    chance = config.get("explore.chance").get(good, 0)
    value = veins.roll(seed, island.id, good)
    return share <= value < share + chance


def status(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, Any]:
    """画面・API・MCP に出す探鉱のようす: 探せる資源、終えた資源、探鉱中の資源と進み。"""
    f = team(island, config)
    current = None
    if f is not None and EXPLORE in f.ext:
        spec = f.ext[EXPLORE]
        total = config.get("explore.turns") * FULL
        current = {
            "good": spec["good"],
            "x": f.x,
            "y": f.y,
            "built": f.built,
            "progress": round(min(spec["progress"], total) / total, 3),
        }
    return {
        "explorable": explorable(island, defs, config),
        "explored": dict(sorted(explored(island).items())),
        "current": current,
        "turns": config.get("explore.turns"),
    }
