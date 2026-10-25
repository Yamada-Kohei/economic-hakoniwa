"""経験と集積（フェーズ3-3）。長く多く動かした産業や、集めて置いた産業ほど生産性が上がり、遊び方から
国ごとの得意産業が育つようにします。

経験: 島×産業（facilities.yaml の industry）ごとに、累積の産出を「標準の施設が全力で動いたターン数」
で数えて持ちます（``island.ext["experience"]``: 産業 → [累積, その年の量]。どちらも 1000 分の 1
ターン）。施設が1ターンに足す量は、産出の倍率（稼働率 × 生産性）です（品目の単位が違っても比べ
られる）。係数は min(上限, 1 + experience.slope × log2(1 + 累積 ÷ experience.base_turns)) で、上限は
experience.cap、組み立て産業（experience.assembly）は experience.assembly_cap です。その年（12
ターン）に動かした量が base_turns の experience.dormant_share に満たない産業は、年の終わりに累積が
experience.decay だけ減ります。貿易で経験が少し移る形（技術の移転）は、add で足せるようにして
あります。

建国のとき（W-186 の直し 回1）: 初めの施設の産業（start_island.yaml に書いた施設の産業。農林水産業・
消費財・電力・鉱業）は、島で昔から営んできた産業なので、累積 founding.experience_turns（標準の施設が
動いたターン数）から始めます（start）。0 から始めると、建国の1年目に生産性が 15% ほど伸びて費用と
値段が下がり続けた。初めの値段も、建国で置く施設の作り方（founding_recipes）はこの係数
（founding_factor）の生産性で出します（economy/prices.py）。島の資源がなくて置かない施設の産業と、
島に新しく入る産業は 0 から（新しい産業の習熟）。

集積: 半径 experience.cluster_radius マス以内に、同じ産業のほかの施設（建ったもの）が1つあるごとに
experience.cluster_step（上限 experience.cluster_cap）だけ生産性を上げます。効くのは
experience.cluster_categories の分類の施設（既定は工場の industry だけ。農場や鉱山は島じゅうに
並べるのがふつうで、どの島も同じだけ上がり得意産業の違いにならないため）。汚染も重なるので、
周りの幸福度と肥沃度は下がります（汚染の仕組みはそのまま）。

称号: 各産業の累積が世界で1位の国に「鉄鋼の国」などの称号を付けます（``world.ext["rankings"]``。
modules/experience の世界の段 rankings が毎ターン書く。他国からも見える）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState, WorldState

KEY = "experience"  # island.ext: 産業 → [累積, その年の量]（1000 分の 1 ターン）
RANKINGS = "rankings"  # world.ext: 称号と順位（modules/experience）
FULL = 1000


def table(island: IslandState) -> dict[str, list[int]]:
    return island.ext.setdefault(KEY, {})


def total(island: IslandState, industry: str) -> int:
    """産業の累積（1000 分の 1 ターン）。"""
    return island.ext.get(KEY, {}).get(industry, [0, 0])[0]


def add(island: IslandState, industry: str, amount: int) -> None:
    """産業の累積とその年の量に amount（1000 分の 1 ターン）を足す。"""
    if amount <= 0:
        return
    entry = table(island).setdefault(industry, [0, 0])
    entry[0] += amount
    entry[1] += amount


def founding_industries(defs: Defs) -> frozenset[str]:
    """建国のときに置く施設（start_island.yaml のどの中身にでも書いた施設）の産業。"""
    found = set()
    for start in defs.start_island.values():
        for item in start.get("facilities", ()):
            industry = defs.facilities[item["kind"]].get("industry")
            if industry:
                found.add(industry)
    return frozenset(found)


def founding_recipes(defs: Defs) -> frozenset[tuple[str, str]]:
    """建国のときに置く施設の (施設, 作り方)。初めの値段で経験の係数を掛けるのはこの作り方だけ
    （同じ産業でも、建国で置かない作り方を島が昔から営んでいたとは限らない）。"""
    found = set()
    for start in defs.start_island.values():
        for item in start.get("facilities", ()):
            kind = item["kind"]
            found.add((kind, item.get("recipe") or fac.default_recipe(defs, kind) or "default"))
    return frozenset(found)


def founding_factor(config: GameConfig) -> float:
    """建国のときの累積（founding.experience_turns）の経験の係数。"""
    settings = config.get("experience")
    turns = config.get("founding.experience_turns")
    if turns <= 0:
        return 1.0
    value = 1 + settings["slope"] * math.log2(1 + turns / settings["base_turns"])
    return min(settings["cap"], value)


def start(island: IslandState, config: GameConfig, industries: set[str]) -> None:
    """建国した島の、昔からの産業 industries の累積を founding.experience_turns にする（その年の量は
    0。年の終わりに減らす判定は、その年に動かした量で見る）。"""
    amount = round(config.get("founding.experience_turns") * FULL)
    if amount <= 0:
        return
    rows = table(island)
    for industry in sorted(industries):
        rows[industry] = [amount, 0]


def close_year(island: IslandState, config: GameConfig) -> list[str]:
    """年の終わり: その年に動かした量が少ない産業の累積を減らし、その年の量を 0 に戻す。減らした
    産業を返す。"""
    settings = config.get("experience")
    dormant = round(settings["base_turns"] * settings["dormant_share"] * FULL)
    keep = 1 - settings["decay"]
    faded = []
    rows = table(island)
    for industry in sorted(rows):
        entry = rows[industry]
        if entry[1] < dormant:
            entry[0] = int(entry[0] * keep)
            faded.append(industry)
        entry[1] = 0
        if entry[0] <= 0:
            del rows[industry]
    return faded


def factor(island: IslandState, config: GameConfig, industry: str | None) -> float:
    """経験の係数（産業がなければ 1）。"""
    if not industry:
        return 1.0
    amount = total(island, industry)
    if amount <= 0:
        return 1.0
    settings = config.get("experience")
    cap = settings["assembly_cap"] if industry in settings["assembly"] else settings["cap"]
    value = 1 + settings["slope"] * math.log2(1 + amount / FULL / settings["base_turns"])
    return min(cap, value)


def clusters(defs: Defs, config: GameConfig, category: str | None) -> bool:
    """分類 category の施設に集積が効くか。"""
    return category in config.get("experience.cluster_categories")


def neighbors(
    island: IslandState, defs: Defs, config: GameConfig, industry: str, x: int, y: int
) -> int:
    """(x, y) の半径 experience.cluster_radius マス以内にある、同じ産業のほかの施設（建ったもの）
    の数。"""
    radius = config.get("experience.cluster_radius")
    count = 0
    for f in island.facilities:
        if not f.built or f.x < 0 or (f.x == x and f.y == y):
            continue
        if max(abs(f.x - x), abs(f.y - y)) > radius:
            continue
        spec = defs.facilities.get(f.kind, {})
        if spec.get("industry") == industry and clusters(defs, config, spec.get("category")):
            count += 1
    return count


def cluster_factor(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    recipe: Mapping[str, Any],
    x: int,
    y: int,
) -> float:
    """集積の係数（場所を決める前の見込みや、集積の効かない施設は 1）。"""
    industry = recipe.get("industry")
    if not industry or not island.in_bounds(x, y):
        return 1.0
    if not clusters(defs, config, recipe.get("category")):
        return 1.0
    settings = config.get("experience")
    found = neighbors(island, defs, config, industry, x, y)
    return 1 + min(settings["cluster_cap"], settings["cluster_step"] * found)


# --- 称号と順位 -------------------------------------------------------------------


def leaders(world: WorldState, defs: Defs) -> dict[str, int]:
    """産業ごとの、累積が世界で1位の島（同じなら番号の小さい島。累積のない産業は入れない）。"""
    best: dict[str, tuple[int, int]] = {}
    for island_id in world.island_ids():
        island = world.islands[island_id]
        for industry, (amount, _) in sorted(island.ext.get(KEY, {}).items()):
            if amount <= 0 or industry not in defs.industries():
                continue
            if industry not in best or amount > best[industry][0]:
                best[industry] = (amount, island_id)
    return {industry: island_id for industry, (_, island_id) in sorted(best.items())}


def strengths(island: IslandState, limit: int = 3) -> list[str]:
    """島の得意産業: 累積の多い順に limit まで。"""
    rows = island.ext.get(KEY, {})
    ordered = sorted(rows.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [industry for industry, (amount, _) in ordered[:limit] if amount > 0]


def titles_of(world: WorldState, island_id: int) -> list[str]:
    """島が持つ称号（産業の名前の順）。世界の段 rankings が書いた結果から。"""
    titles = world.ext.get(RANKINGS, {}).get("titles", {})
    return sorted(industry for industry, holder in titles.items() if holder == island_id)
