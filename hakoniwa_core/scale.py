"""国の大きさ（段6 A3、W-85）: マス 1 つが表す土地の広さ。

game.yaml の `scale.people` は、マス 1 つ・施設 1 つが表す大きさの倍です。定義データ（packs）と
game.yaml の数は倍 1 の大きさで書き、読み込むときに、大きさに比例する数（町に住める人数・
初めの人口・施設の働き手と産出と材料・インフラの資材と人手・陸の部隊の兵と装備・輸送船の積める重さ・お金の額で
書いた規模）だけをこの倍にします。1 人あたりの量・賃金・値段・率・ターン数・マスの数は変えないので、
国の中の割合（失業・物価・税）は倍 1 と同じ形になり、国の大きさだけが変わります。

現実の物の大きさで決まる数（軍艦 1 隻の乗員と材料、自動車 1 台、兵 1 人の武器と食べ物）は
変えません。

倍は世界ごとに決まります。サーバーは世界を作るときの倍を世界の拡張欄（World.ext の "scale"）に書き、
次からはその倍で設定を読みます（server/game.py）。倍を入れる前に作った世界（8000・8001）は拡張欄に
倍がないので、倍 1 のまま動きます（人口やお金を後から 20 倍に移し替えると、預金・国債・会社の
資本など数の多い状態を全部揃えて直す必要があり、間違えやすいため）。

試験は環境変数 HK_SCALE で倍を替えられます（tests/conftest.py は 1 にして、倍 1 の数で書いた試験を
そのまま使う）。測りで倍 1 と比べるときも同じです。環境変数は世界の倍より先に効きます。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

ENV = "HK_SCALE"
WORLD_KEY = "scale"  # 世界の拡張欄に書く倍

# 1 単位が倍の分だけ大きくなる財（大きな貨物船）。作る数・持つ数は倍 1 のまま
GROWN = frozenset({"transport_ship"})

# 基地の定員（兵の人数・ミサイルと迎撃ミサイルの数）。レーダーの見える距離（区画）は掛けない
MILITARY_SIZED = ("barracks", "fortress", "missile_base", "warhead", "air_defense")

# game.yaml の大きさに比例する設定（名前 → 倍の掛け方。"x" は掛ける、"/" は割る）
CONFIG_FIELDS: dict[str, str] = {
    "trade.ship_capacity": "x",  # 輸送船 1 隻を大きな貨物船にして、隻数を倍 1 と同じに保つ
    "trade.ship_crew": "x",
    "trade.ship_fuel": "x",  # 大きな貨物船は 1 隻で多く燃やす
    "trade.start_fuel": "x",
    "trade.port_capacity": "x",
    "trade.trader_capacity": "x",
    "trade.trader_capital": "x",  # 平均賃金の何倍かで書いた商社の資金
    "fx.min_depth": "x",
    "banking.staff_volume": "x",
    "banking.entry_deposits": "x",
    "population.deaths.crowd": "x",  # マスの人数の混み具合
    "military.keep_per_tile": "x",
    "military.trench.soldiers": "x",
    "military.trench.materials": "x",
    "military.combat.landing_tons": "x",
    "military.combat.police.workers": "x",
    "military.supply.unload_per_ship": "x",
    "military.civil_war.start_min": "x",
    "military.naval.blockade_full": "x",  # 大きな島の港を封じるには多くの軍艦が要る
    # ミサイル 1 発は実物の大きさ: 広いマスの人のうち亡くなる・逃げる割合と、施設を壊す見込みは
    # 倍で割る
    "military.missiles.dead": "/",
    "military.missiles.flee": "/",
    "military.missiles.ruin": "/",
    "awards.prosperity": "x",
    "awards.peace_kills": "x",
    "monuments.cenotaph_dead": "x",
}


def factor(raw: Mapping[str, Any], world: float | None = None) -> float:
    """設定の倍。環境変数 HK_SCALE、世界の倍（world）、game.yaml の順に先に効く。"""
    env = os.environ.get(ENV)
    if env:
        return float(env)
    if world is not None:
        return float(world)
    return float((raw.get("scale") or {}).get("people", 1))


def people(config: Any) -> float:
    """GameConfig から倍を引く（計算の中の決まった人数に掛ける）。"""
    return float((config.raw.get("scale") or {}).get("people", 1))


def _times(value: Any, k: float) -> Any:
    """数・数の表・数の並びに k を掛ける（整数は整数のまま。財の表の GROWN は掛けない）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return round(value * k)
    if isinstance(value, float):
        return value * k
    if isinstance(value, Mapping):
        return {key: v if key in GROWN else _times(v, k) for key, v in value.items()}
    if isinstance(value, list):
        return [_times(v, k) for v in value]
    return value


def apply_config(raw: dict[str, Any], world: float | None = None) -> dict[str, Any]:
    """game.yaml の中身に倍を掛けて、掛けた倍を scale.people に書く。"""
    k = factor(raw, world)
    raw.setdefault("scale", {})
    raw["scale"] = {**raw["scale"], "people": k}
    if k == 1:
        return raw
    for dotted, how in CONFIG_FIELDS.items():
        *parents, last = dotted.split(".")
        node: Any = raw
        for part in parents:
            node = node.get(part) if isinstance(node, Mapping) else None
            if node is None:
                break
        if not isinstance(node, dict) or last not in node:
            continue
        node[last] = _times(node[last], k if how == "x" else 1 / k)
    return raw


def apply_defs(tables: dict[str, dict[str, dict[str, Any]]], k: float) -> None:
    """読み込んだ定義データ（正規化の前）に倍を掛ける。"""
    if k == 1:
        return
    for entry in tables.get("facilities", {}).values():
        _facility(entry, k)
    for entry in tables.get("settlements", {}).values():
        for key in ("capacity", "min_population", "materials"):
            if key in entry:
                entry[key] = _times(entry[key], k)
    for entry in tables.get("infrastructure", {}).values():
        for key in ("materials", "fallback", "labor"):
            if key in entry:
                entry[key] = _times(entry[key], k)
    for entry in tables.get("start_island", {}).values():
        _start(entry, k)
    for entry in tables.get("army", {}).values():
        if entry.get("arm") == "land":
            for key in ("soldiers", "equipment", "weight"):
                if key in entry:
                    entry[key] = _times(entry[key], k)
        elif "capacity" in entry:  # 輸送船の艦隊: 大きな貨物船（軍艦は実物の大きさのまま）
            for key in ("capacity", "soldiers", "fuel"):
                if key in entry:
                    entry[key] = _times(entry[key], k)


def _facility(entry: dict[str, Any], k: float) -> None:
    entry["workers"] = _times(entry["workers"], k)
    construction = entry.get("construction") or {}
    for key in ("labor", "materials"):
        if key in construction:
            construction[key] = _times(construction[key], k)
    for recipe in (entry.get("recipes") or {}).values():
        for key in ("inputs", "outputs"):
            if key in recipe:
                recipe[key] = _times(recipe[key], k)
    for grade in entry.get("grades") or []:  # 農場の段階の資材と燃料も、国の大きさの倍に合わせる
        construction = grade.get("construction") or {}
        if "materials" in construction:
            construction["materials"] = _times(construction["materials"], k)
        if "inputs" in grade:
            grade["inputs"] = _times(grade["inputs"], k)
    automation = entry.get("automation")
    if automation and "electronics" in automation:
        automation["electronics"] = automation["electronics"] * k
    education = entry.get("education")
    if education:
        for key in ("capacity", "engineers"):
            if key in education:
                education[key] = _times(education[key], k)
    military = entry.get("military")
    if military:
        for key in MILITARY_SIZED:
            if key in military and not isinstance(military[key], bool):
                military[key] = _times(military[key], k)
    if "admin_power" in entry:  # 公務員 1 人あたりの行政力: 働き手が増えた分だけ割る
        entry["admin_power"] = entry["admin_power"] / k


def _start(entry: dict[str, Any], k: float) -> None:
    capital = entry.get("capital") or {}
    if "town_population" in capital:
        capital["town_population"] = _times(capital["town_population"], k)
    villages = entry.get("villages") or {}
    if "population" in villages:
        villages["population"] = _times(villages["population"], k)
    if "warehouse" in entry:
        entry["warehouse"] = _times(entry["warehouse"], k)
