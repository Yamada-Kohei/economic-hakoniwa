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

from django.urls import reverse

from hakoniwa_core import dates
from hakoniwa_core.economy import experience
from hakoniwa_core.state import WorldState
from hakoniwa_core.trade import contracts as contract_states
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS, from_milli
from server.accounts import keys
from server.game import get_game
from server.web import awards, display
from server.web.trade import directory, trading_islands, world_state
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
# 世界の様子の合計: 統計の名前・表示名・単位（お金は基準の通貨に直して足す）
WORLD_TOTALS = [
    ("population", "世界の人口", "人"),
    ("gdp", "世界の GDP（1ターン）", "money"),
    ("trade.exports", "世界の輸出額（1ターン）", "money"),
]
MONEY_TOTALS = {name for name, _, unit in WORLD_TOTALS if unit == "money"}
SOURCES = ("gdp", "population", "happiness", "trade.exports", "unemployment", "inflation")
TOP = 3  # 産業ごとに見せる順位の数

# 島の一覧の表（トップの画面）の列: 統計の名前・表示名・単位
# （お金はほかの順位と同じく基準の通貨に直す）
TABLE_COLUMNS = [
    ("population", "人口", "人"),
    ("gdp", "GDP", "money"),
    ("gdp_per_person", "1人あたり GDP", "money"),
    ("unemployment", "失業率", "%"),
    ("inflation", "物価上昇率", "%"),
    ("happiness", "幸福度", ""),
]
TABLE_NOTE = "GDP と1人あたり GDP は1ターン（1か月）の額、物価上昇率は年率"
NEW_TURNS = dates.TURNS_PER_YEAR  # 建国からこのターン数のうちは、島の一覧で NEW! の印を付ける
# 歴代の記録: 統計の名前・表示名・単位（どの国の通貨でも同じ物差しで比べられるものだけ）
RECORDS = [
    ("population", "最大の人口", "人"),
    ("facilities", "最多の施設", "か所"),
    ("happiness", "最高の幸福度", ""),
]


def _latest(world: m.World, turn: int | None = None) -> dict[int, dict[str, float]]:
    """島ごとの、直近のターン（turn を決めればそのターン）の部門の統計。"""
    out: dict[int, dict[str, float]] = {}
    rows = m.StatRecord.objects.filter(
        island__world=world, turn=world.turn if turn is None else turn, name__in=SOURCES
    ).values_list("island_id", "name", "value")
    for island_id, name, value in rows:
        out.setdefault(island_id, {})[name] = value
    for stats in out.values():
        if stats.get("population"):
            stats["gdp_per_person"] = stats.get("gdp", 0) / stats["population"]
    return out


def _factors(
    world: m.World, base: int | None, trading: dict[int, Any] | None = None
) -> dict[int, float]:
    """島の番号 → その島の通貨 1 を基準の通貨 base に直す倍率（直せない島は入れない）。trading は
    貿易を始めた島（何度も呼ぶときに渡す）。"""
    if base is None:
        return {}
    if trading is None:
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


def comparable_counts(world: m.World) -> dict[int, int]:
    """貿易を始めた島の番号 → その島の通貨を基準にしたとき、お金の額を直せる島の数
    （自分を含む）。"""
    trading = trading_islands(world)
    return {base: len(_factors(world, base, trading)) for base in trading}


def default_base(world: m.World, own: m.Island | None) -> int | None:
    """基準の通貨（U05）: お金の額を直せる国がいちばん多い通貨。同じ数なら見る人の国、さらに同じなら
    番号の小さい国。貿易を始めた国がなければ見る人の国（それもなければ None）。"""
    counts = comparable_counts(world)
    if not counts:
        return own.id if own is not None else None
    best = max(counts.values())
    if own is not None and counts.get(own.id) == best:
        return own.id
    return min(i for i, n in counts.items() if n == best)


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
        "table": island_table(world, base, islands, stats, factors),
        "records": records(world, islands),
        "world_summary": world_summary(world, base, islands, stats, factors),
        "fx": fx_table(world, base, islands, factors),
    }


def top_data(
    world: m.World, base: int | None, islands: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """トップの画面の、島の一覧の表・世界の様子・為替の相場表（/api/rankings の同じ名前の中身）。"""
    stats = _latest(world)
    factors = _factors(world, base)
    return {
        "table": island_table(world, base, islands, stats, factors),
        "world_summary": world_summary(world, base, islands, stats, factors),
        "fx": fx_table(world, base, islands, factors),
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
                "title": display.title_name(industry),
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
        "titles": [display.title_name(industry) for industry in held],
    }


def island_table(
    world: m.World,
    base: int | None,
    islands: dict[int, dict[str, Any]] | None = None,
    stats: dict[int, dict[str, float]] | None = None,
    factors: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """島の一覧の表（GDP の多い順）。1行に島の名前・体制・直近の統計・称号。

    お金の額は基準の通貨に直す（直せない島は値を None にして、文を「直せない」にする）。統計の
    まだない島（建国したばかり）も最後に並べる。"""
    defs = get_game().defs
    islands = islands if islands is not None else directory(world)
    stats = stats if stats is not None else _latest(world)
    factors = factors if factors is not None else _factors(world, base)
    symbol = islands.get(base, {}).get("symbol", "") if base is not None else ""
    titles = ((world.ext or {}).get(experience.RANKINGS) or {}).get("titles", {})
    held: dict[int, list[str]] = {}
    for industry in defs.industries():
        if titles.get(industry) is not None:
            held.setdefault(titles[industry], []).append(display.title_name(industry))
    ai_owners = keys.ai_owner_ids(world.turn)
    prizes = awards.holders(world)
    rows = []
    founded: dict[int, int] = {}
    for island in world.islands.select_related("owner").order_by("id"):
        founded[island.id] = (island.ext or {}).get("founded_turn", 0)
        values = stats.get(island.id, {})
        columns = {}
        for name, label, unit in TABLE_COLUMNS:
            value = values.get(name)
            if value is not None and name in MONEY:
                factor = factors.get(island.id)
                value = None if factor is None else value * factor
                text = "直せない" if value is None else display.stat_text(name, value, unit, symbol)
            else:
                text = "" if value is None else display.stat_text(name, value, unit, symbol)
            columns[name] = {"label": label, "value": value, "text": text}
        regime = island.regime or {}
        rows.append(
            {
                "island": island.id,
                "name": island.name,
                "owner": island.owner.username if island.owner else "",
                "ai": island.owner_id is not None and island.owner_id in ai_owners,  # AI の国の印
                "inactive": island.inactive,
                "regime": "・".join(
                    defs.name("regimes", regime[axis])
                    for axis in ("politics", "economy")
                    if regime.get(axis) in defs.regimes
                ),
                "columns": columns,
                "titles": held.get(island.id, []),
                "flag": reverse("island_flag", args=[island.id]),
                "awards": [a["name"] for a in prizes.get(island.id, [])],
            }
        )
    rows.sort(
        key=lambda r: _order(
            r["columns"]["gdp"]["value"], r["columns"]["population"]["value"], r["island"]
        )
    )
    before = _previous_ranks(world, factors)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        # 前のターンからの順位の上下（上がれば正）。前のターンに統計のなかった島は None
        row["previous_rank"] = before.get(row["island"])
        row["move"] = None if row["previous_rank"] is None else row["previous_rank"] - rank
        row["new"] = world.turn - founded[row["island"]] < NEW_TURNS  # 建国したばかり（NEW!）
    return rows


def _order(gdp: float | None, population: float | None, island_id: int) -> tuple:
    """島の一覧の並び: GDP（基準の通貨に直した額）の多い順。直せない島と統計のまだない島は後ろに、
    人口の順で。"""
    return (gdp is None, -(gdp or 0), -(population or 0), island_id)


def _previous_ranks(world: m.World, factors: dict[int, float]) -> dict[int, int]:
    """前のターンの島の一覧の順位（島の番号 → 順位）。前のターンの GDP も今のレートで直して比べる
    （昔のレートは直接の相場の分しか残っていないので、全部の国を同じ物差しにするため）。"""
    if world.turn <= 0:
        return {}
    previous = _latest(world, world.turn - 1)
    order = []
    for island_id, values in previous.items():
        gdp = values.get("gdp")
        factor = factors.get(island_id)
        gdp = None if gdp is None or factor is None else gdp * factor
        order.append(_order(gdp, values.get("population"), island_id))
    order.sort()
    return {key[-1]: rank for rank, key in enumerate(order, start=1)}


def records(
    world: m.World, islands: dict[int, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """歴代の記録: ターンの統計の履歴の中で一番の値と、その国とターン。それに、建国から一番長く
    続いている国。お金の額は通貨が国ごとで、昔のレートも残っていないので入れない。"""
    islands = islands if islands is not None else directory(world)
    out = []
    for name, label, unit in RECORDS:
        best = (
            m.StatRecord.objects.filter(island__world=world, name=name)
            .order_by("-value", "turn", "island_id")
            .values_list("value", "turn", "island_id")
            .first()
        )
        if best is None or best[0] <= 0:
            continue
        value, turn, island_id = best
        out.append(
            {
                "key": name,
                "label": label,
                "text": display.stat_text(name, value, unit),
                "island": island_id,
                "name": islands.get(island_id, {}).get("name", f"島 {island_id}"),
                "turn": turn,
                "date": display.turn_text(turn),
            }
        )
    founded = [
        ((ext or {}).get("founded_turn", 0), island_id)
        for island_id, ext, inactive in world.islands.values_list("id", "ext", "inactive")
        if not inactive
    ]
    if founded:
        turn, island_id = min(founded)
        years = (world.turn - turn) / dates.TURNS_PER_YEAR
        out.append(
            {
                "key": "longest",
                "label": "最も長く続く国",
                "text": f"建国から {years:.1f} 年",
                "island": island_id,
                "name": islands.get(island_id, {}).get("name", f"島 {island_id}"),
                "turn": turn,
                "date": display.turn_text(turn),
            }
        )
    return out


def world_summary(
    world: m.World,
    base: int | None,
    islands: dict[int, dict[str, Any]] | None = None,
    stats: dict[int, dict[str, float]] | None = None,
    factors: dict[int, float] | None = None,
) -> dict[str, Any]:
    """世界の様子: 国の数・世界の人口・世界の GDP と輸出額（基準の通貨に直した合計）と、前のターン
    からの増え方（% 。GDP と輸出額は前のターンの分も今のレートで直して比べる）、貿易圏・二国間の
    契約・運んでいる船荷の数。直せない国は合計に入れず、その数を unconverted に返す。"""
    islands = islands if islands is not None else directory(world)
    stats = stats if stats is not None else _latest(world)
    factors = factors if factors is not None else _factors(world, base)
    previous = _latest(world, world.turn - 1) if world.turn > 0 else {}
    symbol = islands.get(base, {}).get("symbol", "") if base is not None else ""

    def total(source: dict[int, dict[str, float]], name: str) -> float:
        out = 0.0
        for island_id, values in source.items():
            value = values.get(name, 0.0)
            if name in MONEY_TOTALS:
                factor = factors.get(island_id)
                value = 0.0 if factor is None else value * factor
            out += value
        return out

    unconverted = sum(1 for i in stats if stats[i].get("gdp") and i not in factors)
    out: dict[str, Any] = {
        "islands": sum(1 for e in islands.values() if not e["inactive"]),
        "inactive": sum(1 for e in islands.values() if e["inactive"]),
        "unconverted": unconverted,
        "totals": [],
    }
    for name, label, unit in WORLD_TOTALS:
        now, before = total(stats, name), total(previous, name)
        out["totals"].append(
            {
                "name": name,
                "label": label,
                "value": now,
                "text": display.stat_text(name, now, unit, symbol),
                "change": round((now / before - 1) * 100, 2) if before > 0 else None,
            }
        )
    state = world_state(world)
    out["blocs"] = len(state["blocs"])
    out["contracts"] = sum(1 for c in state["contracts"] if c["state"] != contract_states.ENDED)
    out["shipments"] = len(state["shipments"])
    return out


def fx_table(
    world: m.World,
    base: int | None,
    islands: dict[int, dict[str, Any]] | None = None,
    factors: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """為替の相場表: 貿易を始めた国の通貨 1 が、基準の通貨でいくらか（今のレート）と、前のターン
    からの上下（% 。基準の国が直接の相場を持つ通貨だけ。ほかの通貨を通した目安は前の値がない）。"""
    if base is None:
        return []
    islands = islands if islands is not None else directory(world)
    factors = factors if factors is not None else _factors(world, base)
    symbol = islands.get(base, {}).get("symbol", "")
    previous: dict[str, float] = {}
    if world.turn > 0:
        rows = m.StatRecord.objects.filter(
            island_id=base, turn=world.turn - 1, name__startswith="fx."
        ).values_list("name", "value")
        previous = {name: value for name, value in rows if name.removeprefix("fx.").isdigit()}
    here = trading_islands(world).get(base)
    out = []
    for other in sorted(factors):
        if other == base or other not in islands:
            continue
        rate = factors[other]
        direct = here is not None and fxrates.has_rate(here, other)
        before = previous.get(f"fx.{other}") if direct else None
        out.append(
            {
                "currency": other,
                "name": islands[other]["currency"],
                "symbol": islands[other]["symbol"],
                "island_name": islands[other]["name"],
                "rate": rate,  # この通貨 1 が基準の通貨でいくらか
                "text": f"{rate:,.4f} {symbol}".strip(),  # 為替の画面と同じ小数4桁
                "direct": direct,  # 直接の相場か（偽ならほかの通貨を通した目安）
                "change": round((rate / before - 1) * 100, 2) if before else None,
            }
        )
    return out
