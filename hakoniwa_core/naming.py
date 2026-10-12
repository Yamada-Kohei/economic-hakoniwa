"""施設に自動で付く名前。材料は naming.yaml にあります。

プレイヤーが名前を付けた施設はその名前を使い、付けていない施設は、今の体制・業種・地名から
その場で名前を作ります（体制が変われば自動の名前も変わる）。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState


def _style(defs: Defs, config: GameConfig) -> Mapping[str, Any] | None:
    return defs.naming.get(config.raw.get("locale", "ja"))


def generate_places(defs: Defs, config: GameConfig, rng: random.Random, count: int) -> list[str]:
    """島の地名を count 個作る（なるべく重ならないように）。"""
    style = _style(defs, config)
    if style is None:
        return [f"{i + 1}" for i in range(count)]
    first, second = style["places"]["first"], style["places"]["second"]
    pool = [a + b for a in first for b in second]
    rng.shuffle(pool)
    while len(pool) < count:
        pool += pool
    return pool[:count]


def place_count(island_size: int, config: GameConfig) -> int:
    block = config.get("naming.place_block")
    per_side = -(-island_size // block)
    return per_side * per_side


def place_of(island: IslandState, x: int, y: int, config: GameConfig) -> str:
    """マスの地名。島を place_block マス四方の区画に分け、区画ごとに1つの地名を使う。"""
    places = island.ext.get("places") or []
    if not places:
        return island.names.get("capital", island.name)
    block = config.get("naming.place_block")
    per_side = -(-island.size // block)
    return places[((y // block) * per_side + x // block) % len(places)]


def assign_serial(island: IslandState, f: Facility) -> None:
    """同じ種類の施設の中での通し番号（「第三製鉄所」の三）を付ける。作るときに1回だけ呼ぶ。"""
    serials = island.ext.setdefault("serials", {})
    serials[f.kind] = serials.get(f.kind, 0) + 1
    f.ext["serial"] = serials[f.kind]


def facility_name(defs: Defs, config: GameConfig, island: IslandState, f: Facility) -> str:
    if f.name:
        return f.name
    return auto_name(defs, config, island, f)


def auto_name(defs: Defs, config: GameConfig, island: IslandState, f: Facility) -> str:
    style = _style(defs, config)
    display = defs.name("facilities", f.kind, config.raw.get("locale", "ja"))
    if style is None:
        return display
    serial = f.ext.get("serial", f.id)
    facts = {**island.regime, "owner": f.owner, "category": defs.facilities[f.kind]["category"]}
    industry = style.get("industries", {}).get(f.kind, {})
    numbers = style.get("numbers", [])
    values = {
        "place": place_of(island, f.x, f.y, config),
        "short": industry.get("short", display),
        "long": industry.get("long", display),
        "number": numbers[serial - 1] if 0 < serial <= len(numbers) else str(serial),
        "leader": island.names.get("leader", ""),
    }
    for pattern in style["patterns"]:
        when = pattern.get("when", {})
        if any(facts.get(k) != v for k, v in when.items()):
            continue
        every = pattern.get("every")
        if every and serial % every != 0:
            continue
        if "{leader}" in pattern["text"] and not values["leader"]:
            continue
        return pattern["text"].format(**values)
    return display
