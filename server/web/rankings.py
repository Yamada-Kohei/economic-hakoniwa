"""統計の順位の画面（フェーズ3-3）と、島の得意産業と称号の組み立て。

部門ごとの順位（GDP・1人あたり GDP・人口・幸福度・輸出額）は、どの島の直近のターンの統計からも
誰にでも見せる。お金の額は島ごとの通貨なので、見る人が選んだ通貨（決めなければ見る人の国、国が
なければ貿易を始めた一番番号の小さい国の通貨）に今のレートで直して比べる。レートのない通貨
（共通の通貨を通しても計算できない）の国は、直せないので順位から外して最後に並べる。

得意産業と称号は、各産業の経験の累積（Island.ext["experience"]）と、世界の段 rankings が書いた
称号（World.ext["rankings"]["titles"]）から作る。誰にでも見せる。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.economy import experience
from hakoniwa_core.state import WorldState
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS, from_milli
from server.game import get_game
from server.web import display
from server.web.trade import directory, trading_islands
from server.world import models as m
from server.world.repository import island_from_row

# 部門: 統計の名前（gdp_per_person は gdp ÷ population で作る）・表示名・単位
CATEGORIES = [
    ("gdp", "GDP（1ターン）", "money"),
    ("gdp_per_person", "1人あたり GDP（1ターン）", "money"),
    ("population", "人口", "人"),
    ("happiness", "幸福度", ""),
    ("trade.exports", "輸出額（1ターン）", "money"),
]
MONEY = {name for name, _, unit in CATEGORIES if unit == "money"}
SOURCES = ("gdp", "population", "happiness", "trade.exports")
TOP = 3  # 産業ごとに見せる順位の数


def _latest(world: m.World) -> dict[int, dict[str, float]]:
    """島ごとの、直近のターンの部門の統計。"""
    out: dict[int, dict[str, float]] = {}
    rows = m.StatRecord.objects.filter(
        island__world=world, turn=world.turn, name__in=SOURCES
    ).values_list("island_id", "name", "value")
    for island_id, name, value in rows:
        out.setdefault(island_id, {})[name] = value
    for stats in out.values():
        if stats.get("population"):
            stats["gdp_per_person"] = stats.get("gdp", 0) / stats["population"]
    return out


def _factors(world: m.World, base: int | None) -> dict[int, float]:
    """島の番号 → その島の通貨 1 を基準の通貨 base に直す倍率（直せない島は入れない）。"""
    if base is None:
        return {}
    trading = trading_islands(world)
    if base not in trading:
        return {base: 1.0}
    here = trading[base]
    view = WorldState(turn=world.turn, seed=world.seed, islands=trading)
    out = {base: 1.0}
    for other in trading:
        if other == base:
            continue
        if fxrates.has_rate(here, other):
            out[other] = fxrates.rate(here, other) / BASIS
        else:
            e = fxrates.cross(view, here, other)
            if e:
                out[other] = e / BASIS
    return out


def default_base(world: m.World, own: m.Island | None) -> int | None:
    """基準の通貨: 見る人の国、国がなければ貿易を始めた一番番号の小さい国（どちらもなければ
    None）。"""
    if own is not None:
        return own.id
    trading = trading_islands(world)
    return min(trading) if trading else None


def ranking_data(world: m.World, base: int | None) -> dict[str, Any]:
    """部門ごとの順位と、産業ごとの称号と上位の国。"""
    islands = directory(world)
    stats = _latest(world)
    factors = _factors(world, base)
    symbol = islands.get(base, {}).get("symbol", "") if base is not None else ""
    categories = []
    for name, label, unit in CATEGORIES:
        ranked, unranked = [], []
        for island_id, values in stats.items():
            if name not in values or island_id not in islands:
                continue
            value = values[name]
            if name in MONEY:
                factor = factors.get(island_id)
                if factor is None:
                    unranked.append(_row(islands, island_id, None, "直せない"))
                    continue
                value *= factor
            ranked.append((value, island_id))
        ranked.sort(key=lambda r: (-r[0], r[1]))
        rows = [
            _row(islands, i, rank, display.stat_text(name, v, unit, symbol))
            for rank, (v, i) in enumerate(ranked, start=1)
        ]
        categories.append({"name": name, "label": label, "rows": rows + unranked})
    return {
        "turn": world.turn,
        "base": base,
        "base_name": islands.get(base, {}).get("currency", "") if base is not None else "",
        "currencies": [
            {"id": i, "name": islands[i]["currency"]} for i in sorted(trading_islands(world))
        ],
        "categories": categories,
        "industries": industry_rows(world, islands),
    }


def _row(
    islands: dict[int, dict[str, Any]], island_id: int, rank: int | None, text: str
) -> dict[str, Any]:
    return {"rank": rank, "island": island_id, "name": islands[island_id]["name"], "text": text}


def _experience(world: m.World) -> dict[str, list[tuple[int, int]]]:
    """産業 → [(累積, 島の番号)]（累積の多い順。同じなら番号の小さい島が上）。"""
    out: dict[str, list[tuple[int, int]]] = {}
    for island_id, ext in world.islands.values_list("id", "ext"):
        for industry, (amount, _) in ((ext or {}).get(experience.KEY) or {}).items():
            if amount > 0:
                out.setdefault(industry, []).append((amount, island_id))
    for rows in out.values():
        rows.sort(key=lambda r: (-r[0], r[1]))
    return out


def industry_rows(
    world: m.World, islands: dict[int, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """産業ごとの称号を持つ国と、経験の累積の上位の国。"""
    defs = get_game().defs
    islands = islands if islands is not None else directory(world)
    titles = ((world.ext or {}).get(experience.RANKINGS) or {}).get("titles", {})
    found = _experience(world)
    rows = []
    for industry in defs.industries():
        holder = titles.get(industry)
        top = found.get(industry, [])[:TOP]
        rows.append(
            {
                "industry": industry,
                "name": defs.name("industries", industry),
                "title": f"{defs.name('industries', industry)}の国",
                "holder": holder,
                "holder_name": islands.get(holder, {}).get("name", "") if holder else "",
                "top": [
                    {
                        "rank": rank,
                        "island": i,
                        "name": islands.get(i, {}).get("name", f"島 {i}"),
                        "turns": round(from_milli(amount), 1),
                    }
                    for rank, (amount, i) in enumerate(top, start=1)
                ],
            }
        )
    return rows


def island_strengths(island: m.Island, stats: dict[str, float]) -> dict[str, Any]:
    """島の得意産業（経験の累積の多い順に3つ。世界の順位つき）と、持っている称号。"""
    defs = get_game().defs
    state = island_from_row(island)
    world_ext = island.world.ext or {}
    titles = (world_ext.get(experience.RANKINGS) or {}).get("titles", {})
    table = experience.table(state)
    strengths = []
    for industry in experience.strengths(state, 3):
        rank = stats.get(f"experience_rank.{industry}")
        strengths.append(
            {
                "industry": industry,
                "name": defs.name("industries", industry),
                "turns": round(from_milli(table[industry][0]), 1),
                "rank": int(rank) if rank else None,
            }
        )
    held = sorted(industry for industry, holder in titles.items() if holder == island.id)
    return {
        "strengths": strengths,
        "titles": [f"{defs.name('industries', industry)}の国" for industry in held],
    }
