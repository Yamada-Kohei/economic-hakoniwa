"""地名と、施設に自動で付く名前。材料は naming.yaml にあります。

地名は島を区画に分けて1つずつ付けます。建国のときに選んだ作風（日本風・イギリス風など）で
自動で作り、プレイヤーが命令で付け直せます。首都のある区画の地名は首都名です。
プレイヤーが名前を付けた施設はその名前を使い、付けていない施設は、今の体制・業種・地名から
その場で名前を作ります（体制が変われば自動の名前も変わる）。町（住宅地）には名前を付けません。
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


def place_styles(defs: Defs, config: GameConfig) -> dict[str, str]:
    """選べる地名の作風と表示名（定義した順）。"""
    style = _style(defs, config)
    if style is None:
        return {}
    return {sid: s["label"] for sid, s in style["places"].items()}


def default_place_style(defs: Defs, config: GameConfig) -> str:
    styles = place_styles(defs, config)
    chosen = config.raw.get("naming", {}).get("place_style")
    return chosen if chosen in styles else next(iter(styles), "")


def generate_places(
    defs: Defs, config: GameConfig, rng: random.Random, count: int, style_id: str = ""
) -> list[str]:
    """島の地名を、作風 style_id で count 個作る（なるべく重ならないように）。"""
    style = _style(defs, config)
    if style is None:
        return [f"{i + 1}" for i in range(count)]
    places = style["places"]
    spec = places.get(style_id) or places[default_place_style(defs, config)]
    joiner = spec.get("joiner", "")
    pool = [a + joiner + b for a in spec["first"] for b in spec["second"] if a != b]
    rng.shuffle(pool)
    while len(pool) < count:
        pool += pool
    return pool[:count]


def place_count(island_size: int, config: GameConfig) -> int:
    block = config.get("naming.place_block")
    per_side = -(-island_size // block)
    return per_side * per_side


def place_index(island: IslandState, x: int, y: int, config: GameConfig) -> int:
    """マスの地名の区画の番号。島を place_block マス四方の区画に分け、区画ごとに1つの地名を使う。"""
    block = config.get("naming.place_block")
    per_side = -(-island.size // block)
    return (y // block) * per_side + x // block


def place_of(island: IslandState, x: int, y: int, config: GameConfig) -> str:
    """マスの地名。"""
    places = island.ext.get("places") or []
    if not places:
        return island.names.get("capital", island.name)
    return places[place_index(island, x, y, config) % len(places)]


def assign_serial(island: IslandState, f: Facility) -> None:
    """同じ種類の施設の中での通し番号（「第三製鉄所」の三）を付ける。作るときに1回だけ呼ぶ。"""
    serials = island.ext.setdefault("serials", {})
    serials[f.kind] = serials.get(f.kind, 0) + 1
    f.ext["serial"] = serials[f.kind]


def facility_name(defs: Defs, config: GameConfig, island: IslandState, f: Facility) -> str:
    """施設の名前。町（住宅地）には名前を付けず、段階の表示名（村・町など）を返す。"""
    if f.kind == config.get("housing.facility"):
        level = f.ext.get("level") or defs.settlement_levels()[0]
        return defs.name("settlements", level, config.raw.get("locale", "ja"))
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
