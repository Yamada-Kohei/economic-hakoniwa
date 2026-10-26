"""予算の目安（W-143）: 年の予算を決めるときの手がかり。

財政の画面の「予算と使った額」と、予算の命令の入力欄に出す。どの数字も今の島の状態から読むだけで、
計算には口を出さない（hakoniwa_core には触れない）。

項目ごとに出すもの:
- last_year: 去年（直近 12 ターン）に使った額の合計
- peak_year: いちばん使った月を 1 年続けた額（今までの最高の目安。直近 12 ターンの中で）
- limit: これより積んでも使い切れない額の目安（わかる項目だけ。None はわからない）
- note: limit の中身の説明

公共事業費（construction）の limit は、始めている国の工事の残りと、インフラの工事の列の残り、
インフラの維持（傷みを戻す分）と国の施設の設備の修理の 1 年分の合計。命令で新しい工事を足すと
増える。教育費（education）と治安費（security）の limit は、その予算で賃金を払う国営の施設を全力で
動かす 1 年の費用（標準の計画の教育費と同じ考え。余裕は足さない）。住宅費（housing）の limit は、
命令の住宅の工事の残りの資材と、国の床の 1 年の手入れの合計。ほかの項目は去年の使った額だけ。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import equipment
from hakoniwa_core.economy import public_housing as pub
from hakoniwa_core.state import OWNER_STATE, Tile
from hakoniwa_core.units import money_of
from server.game import get_game
from server.web import display
from server.world import models as m

STAFFED = ("education", "security")  # 賃金を払う国営の施設を全力で動かす費用が上限の項目


def _unit_price(prices: dict[str, int], good: str, band: float) -> int:
    """財 1 単位を買う値段の見込み（今の値段の上端まで）。"""
    return int(prices.get(good, 0) * band)


def _materials_cost(
    materials: dict[str, int], prices: dict[str, int], band: float, share: float = 1.0
) -> int:
    """資材（1000 分の 1 単位の量）の代金。share は残りの割合。"""
    return sum(
        money_of(_unit_price(prices, good, band), round(qty * share))
        for good, qty in materials.items()
    )


def _past(island: m.Island, items: list[str]) -> dict[str, list[float]]:
    """項目ごとに、直近 12 ターンに使った額（新しいターンから）。"""
    names = [f"spent.{item}" for item in items]
    out: dict[str, list[float]] = {item: [] for item in items}
    # 使わなかったターンの記録はないので、窓は項目の記録ではなく世界の今のターンから数える
    rows = m.StatRecord.objects.filter(
        island=island, name__in=names, turn__gt=island.world.turn - TURNS_PER_YEAR
    ).order_by("-turn")
    for name, value in rows.values_list("name", "value"):
        out[name.removeprefix("spent.")].append(value)
    return out


def _works_left(state, defs, prices, wage, band) -> int:
    """始めている国の施設の工事の残り（人手の賃金と、これから使う資材の代金）。"""
    total = 0
    for f in state.facilities:
        if f.built or f.owner != OWNER_STATE or f.kind not in defs.facilities:
            continue
        spec = defs.facilities[f.kind]["construction"]
        whole = fac.total_progress(defs, f)
        left = max(0, whole - f.progress)
        turns = left / fac.FULL
        total += round(turns * spec.get("labor", 0) * wage)
        total += _materials_cost(fac.materials_of(defs, f), prices, band, left / whole)
    return total


def _infra_costs(island: m.Island, state, defs, prices, wage, band) -> tuple[int, int]:
    """インフラの工事の列の残りと、維持（傷みを戻す分）の 1 年分。"""
    kinds = defs.infrastructure
    tiles = {
        (x, y): Tile(x, y, terrain="", infra=data)
        for x, y, data in island.tiles.values_list("x", "y", "infra")
        if data
    }

    def full_cost(kind: str) -> int:  # 整備度 0% から 100% にする費用
        spec = kinds[kind]
        return round(spec["labor"] * wage) + _materials_cost(spec["materials"], prices, band)

    queued = 0
    for kind, x, y in infra.queue(state):
        tile = tiles.get((x, y))
        if kind in kinds:
            level = infra.level(tile, kind) if tile else 0
            queued += round(full_cost(kind) * (infra.FULL - level) / infra.FULL)
    upkeep = 0.0
    for tile in tiles.values():
        for kind in tile.infra:
            if kind in kinds and infra.level(tile, kind) > 0:
                upkeep += kinds[kind]["wear"] * TURNS_PER_YEAR * full_cost(kind)
    return queued, round(upkeep)


def _staffed_cost(state, defs, config, item: str, wage: int, prices, band) -> int:
    """予算の項目 item で賃金を払う国営の施設（工事中を含む）を全力で動かす 1 年の費用。"""
    per_turn = 0
    for f in state.facilities:
        spec = defs.facilities.get(f.kind, {})
        if f.owner != OWNER_STATE or spec.get("budget") != item:
            continue
        per_turn += fac.labor_cost(defs, config, f.kind, wage, f)
        per_turn += _materials_cost(fac.recipe_of(defs, f)["inputs"], prices, band)
    return per_turn * TURNS_PER_YEAR


def _housing_costs(state, defs, config, prices, band) -> tuple[int, int]:
    """命令の住宅の工事の列の残り（資材の代金）と、国の床の手入れの 1 年分（W-190・W-202）。"""
    left = 0
    for work in pub.works_pending(state):
        home = state.facility_at(work["x"], work["y"])
        floor = fac.floor_of(defs, home) if home is not None else 0
        left += _materials_cost(fac.floor_materials(defs, floor, work["floor"]), prices, band)
    wear = config.get("housing.wear")
    upkeep = sum(
        _materials_cost(fac.housing_materials(defs, pub.floor_of(f)), prices, band)
        for f in state.facilities
        if f.ext.get(pub.PUBLIC)
    )
    return left, round(upkeep * wear)


def guide(island: m.Island) -> dict[str, dict[str, Any]]:
    """予算の項目ごとの目安（数は島の通貨の最小の単位）。"""
    game = get_game()
    defs, config = game.defs, game.config
    items = list(config.get("fiscal.budget_items"))
    state = display.island_for_naming(island)
    prices = dict(state.economy.prices)
    wage = state.economy.average_wage or config.get("labor.initial_wage")
    band = 1 + config.get("market.band")
    past = _past(island, items)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        months = past[item]
        out[item] = {
            "last_year": round(sum(months)),
            "peak_year": round(max(months, default=0) * TURNS_PER_YEAR),
            "limit": None,
            "note": "",
        }
    if not state.has_economy:
        return out
    works = _works_left(state, defs, prices, wage, band)
    queued, upkeep = _infra_costs(island, state, defs, prices, wage, band)
    repairs = round(
        sum(
            equipment.yearly_wear(defs, config, f.kind, prices)
            for f in state.facilities
            if f.owner == OWNER_STATE and equipment.has_equipment(defs, config, f)
        )
    )
    out["construction"].update(
        limit=works + queued + upkeep + repairs,
        works=works,
        queued=queued,
        upkeep=upkeep,
        repairs=repairs,
        note="進んでいる国の工事の残り＋インフラの工事の列の残り＋1年の維持と設備の修理",
    )
    if pub.HOUSING in out:
        left, upkeep = _housing_costs(state, defs, config, prices, band)
        out[pub.HOUSING].update(
            limit=left + upkeep,
            housing_works=left,
            housing_upkeep=upkeep,
            note="命令の住宅の工事の残り＋国の床の1年の手入れ（計画経済の町が育つ分は含みません）",
        )
    for item in STAFFED:
        if item in out:
            out[item].update(
                limit=_staffed_cost(state, defs, config, item, wage, prices, band),
                note="この予算で賃金を払う国営の施設を全力で動かす1年の費用",
            )
    return out


def texts(g: dict[str, Any], currency: str) -> dict[str, str]:
    """画面に出す文（お金は通貨記号つき）。わからない上限は「—」。"""

    def money(value: int) -> str:
        return display.money_text(value, currency)

    out = {
        "last_year": money(g["last_year"]),
        "peak_year": money(g["peak_year"]),
        "limit": money(g["limit"]) if g["limit"] is not None else "—",
        "note": g["note"],
    }
    if "works" in g:
        out["parts"] = (
            f"国の施設の工事の残り {money(g['works'])}"
            f"・インフラの工事の列の残り {money(g['queued'])}"
            f"・インフラの維持（1年） {money(g['upkeep'])}"
            f"・国の施設の設備の修理（1年） {money(g['repairs'])}"
        )
    if "housing_works" in g:
        out["parts"] = (
            f"命令の住宅の工事の残りの資材 {money(g['housing_works'])}"
            f"・国の床の手入れ（1年） {money(g['housing_upkeep'])}"
        )
    return out


def attach(policy: dict[str, Any], island: m.Island) -> dict[str, Any]:
    """政策（display.policy_view の答え）の予算の項目に、目安の文 guide を足す。"""
    currency = (island.names or {}).get("currency_symbol", "")
    guides = guide(island)
    for item, row in policy["budget"].items():
        row["guide"] = texts(guides[item], currency)
    return policy
