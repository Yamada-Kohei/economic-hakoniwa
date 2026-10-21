"""自然条件（フェーズ3-2）: 気候帯と肥沃度。

気候帯: 世界マップの行を上から game.yaml の climate.zones の順に、climate.shares の割合で分けます
（寒冷・温帯・熱帯）。島の気候は建国したときの区画で決まり、``island.ext["climate"]`` に持ちます。
気候帯ごとの産出の倍率と家計の電力の倍率は climate.yaml にあり、作り方の factors に climate が
あるときに、作る物の倍率を生産性に掛けます（economy/productivity.py）。倍率が 0 の作り方は、その
気候の島では使えません（placement_problem と usable）。

前のフェーズから移し替えた島（migrate）は、気候帯を世界マップの区画（島が world.default_islands の
ときの区画の数で数える）から決め、倍率を climate.blend_turns ターンかけて 1 から効かせます
（``island.ext["climate_blend"]``: 残りのターン数）。肥沃度は 1 のままです。

肥沃度: 平地のマスに、島を作るときに climate.fertility の幅でまとまりにして配ります
（``tile.ext["fertility"]``。1000 分の 1。書いていなければ 1）。作り方の factors に fertility が
あるとき（農場と牧場）に、施設のマスの肥沃度を掛けます。汚染のあるマスは、汚染のあったターンが
1年分たまるごとに climate.fertility_loss だけ下がります（下限 climate.fertility_floor）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState, LogEntry, Tile

VERSION = 1  # economy.ext["nature"]: 1 はフェーズ3-2 の自然条件を置いた島
CLIMATE = "climate"  # island.ext: 気候帯の名前
BLEND = "climate_blend"  # island.ext: 倍率を効かせ終わるまでの残りのターン数（移し替えた島）
FERTILITY = "fertility"  # tile.ext: 肥沃度（1000 分の 1）
FALLOUT = "fallout"  # tile.ext: 核の汚染の残り（1000 分の 1。半減期で減る。MI-46）
POLLUTED = "polluted_turns"  # tile.ext: 肥沃度が下がるまでにたまった、汚染のあったターン数
FULL = 1000
STRETCH = 1.5  # 肥沃度の点の間をならすと幅が狭まるので、真ん中から広げる倍率


def zone_of(wy: int, grid: int, config: GameConfig) -> str:
    """世界マップの行 wy（区画の1辺が grid）の気候帯。"""
    settings = config.get("climate")
    position = (wy + 0.5) / max(1, grid)
    total = 0.0
    for zone, share in zip(settings["zones"], settings["shares"], strict=True):
        total += share
        if position < total:
            return zone
    return settings["zones"][-1]


def climate_of(island: IslandState) -> str | None:
    return island.ext.get(CLIMATE)


def _zone_spec(defs: Defs, island: IslandState) -> Mapping[str, Any]:
    zone = climate_of(island)
    return defs.climate.get(zone, {}) if zone else {}


def output_multiplier(defs: Defs, island: IslandState, good: str) -> float:
    """気候帯の、財 good の産出の倍率（定義の値。移し替えの途中でも効かせ終えた値）。"""
    return float(_zone_spec(defs, island).get("outputs", {}).get(good, 1.0))


def recipe_multiplier(
    defs: Defs, config: GameConfig, island: IslandState, recipe: Mapping[str, Any]
) -> float:
    """作り方の産出に掛ける気候の倍率（作る物の倍率の小さい方。移し替えの途中は 1 から寄せる）。"""
    values = [output_multiplier(defs, island, g) for g in recipe["outputs"]]
    value = min(values, default=1.0)
    left = island.ext.get(BLEND, 0)
    turns = config.get("climate.blend_turns")
    if left > 0 and turns > 0 and value > 0:
        value = 1 + (value - 1) * max(0.0, 1 - left / turns)
    return value


def usable(defs: Defs, island: IslandState, recipe: Mapping[str, Any]) -> bool:
    """気候が合って、この島で使える作り方か（気候の効く作り方で、作る物の倍率が 0 でない）。"""
    if not (recipe.get("factors") or {}).get("climate"):
        return True
    return all(output_multiplier(defs, island, g) > 0 for g in recipe["outputs"])


def household_power(defs: Defs, island: IslandState) -> float:
    """家計の電力の要る量の倍率（寒冷の島は多い）。"""
    return float(_zone_spec(defs, island).get("household_power", 1.0))


def fertility(tile: Tile) -> int:
    """マスの肥沃度（1000 分の 1）。"""
    return tile.ext.get(FERTILITY, FULL)


def spread_fertility(island: IslandState, config: GameConfig, seed: int) -> None:
    """平地のマスに肥沃度を配る。値は climate.fertility の幅で、climate.fertility_cell マスおきの
    格子の点に種と位置から決めた値を置いて間をならす（近いマスどうしが似た値のまとまりになる）。"""
    settings = config.get("climate")
    low, high = settings["fertility"]
    cell = settings["fertility_cell"]
    plain = settings["fertility_terrain"]
    for tile in island.tiles:
        if tile.terrain != plain:
            tile.ext.pop(FERTILITY, None)
            continue
        gx, gy = tile.x / cell, tile.y / cell
        x0, y0 = int(gx), int(gy)
        fx, fy = gx - x0, gy - y0
        value = (
            _noise(seed, x0, y0) * (1 - fx) * (1 - fy)
            + _noise(seed, x0 + 1, y0) * fx * (1 - fy)
            + _noise(seed, x0, y0 + 1) * (1 - fx) * fy
            + _noise(seed, x0 + 1, y0 + 1) * fx * fy
        )
        value = min(1.0, max(0.0, 0.5 + (value - 0.5) * STRETCH))  # ならして狭まった幅を広げる
        tile.ext[FERTILITY] = round((low + (high - low) * value) * FULL)


def _noise(seed: int, x: int, y: int) -> float:
    """種と格子の点だけで決まる 0 以上 1 未満の数。"""
    key = f"fertility:{seed}:{x},{y}".encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") / 2**64


def decay_fallout(tile: Tile, config: GameConfig) -> int:
    """マスに残る核の汚染を 1 ターン分減らし、今の濃さ（1000 分の 1）を返す。薄まりきったら消す。"""
    left = tile.ext.get(FALLOUT, 0)
    if left <= 0:
        return 0
    left = int(left * 0.5 ** (1 / config.get("military.nuclear.half_life")))
    if left < 1000 // 20:
        tile.ext.pop(FALLOUT, None)
        return 0
    tile.ext[FALLOUT] = left
    return left


def wear_fertility(island: IslandState, config: GameConfig) -> None:
    """汚染のあるマスの、汚染のあったターンを数え、1年分たまるごとに肥沃度を下げる。"""
    settings = config.get("climate")
    loss = round(settings["fertility_loss"] * FULL)
    floor = round(settings["fertility_floor"] * FULL)
    for tile in island.tiles:
        if FERTILITY not in tile.ext or tile.pollution <= 0:
            continue
        turns = tile.ext.get(POLLUTED, 0) + 1
        if turns >= YEAR:
            tile.ext[FERTILITY] = max(floor, tile.ext[FERTILITY] - loss)
            turns = 0
        tile.ext[POLLUTED] = turns


def begin_blend(island: IslandState, config: GameConfig) -> None:
    """移し替えた島の気候の倍率を、climate.blend_turns ターンかけて効かせ始める。"""
    turns = config.get("climate.blend_turns")
    if turns > 0:
        island.ext[BLEND] = turns


def step_blend(island: IslandState) -> None:
    """気候の倍率を効かせる残りのターンを1つ進める。"""
    left = island.ext.get(BLEND, 0) - 1
    if left > 0:
        island.ext[BLEND] = left
    else:
        island.ext.pop(BLEND, None)


def migrate(island: IslandState, config: GameConfig) -> None:
    """前のフェーズの島に気候帯を置き、倍率を効かせ始める（1度だけ）。"""
    from hakoniwa_core.state.generate import world_grid_size  # generate がこのモジュールを読むため

    economy = island.economy
    if economy.ext.get("nature", 0) >= VERSION:
        return
    economy.ext["nature"] = VERSION
    if climate_of(island) is None:
        grid = world_grid_size(config.get("world.default_islands"), config)
        island.ext[CLIMATE] = zone_of(island.wy, grid, config)
    begin_blend(island, config)
    island.log.append(LogEntry("nature_started", {"climate": climate_of(island)}))
