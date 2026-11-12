"""鉱脈の置き方（銅とウラン。設計書 15-military MI-60）。

銅鉱石は建国のとき鉱業資源として選べますが（goods.yaml の mining）、選ばなかった島にも、現実の
銅の産地が限られるのと同じに、一部の島に鉱脈を置きます。置くかは世界の種と島の番号だけで決まる
乱数で、ターンや島の順番には左右されません。島ごとに 1 度だけ置き（版の印 island.ext["veins"]）、
鉱業資源の上限（founding.max_resources）の外とします。経済のない島も対象です。

ただし島の鉱業資源は、建国で選べる数より 1 つ多いところまでです（今は 3 種類。2026-11-01 本人
「4 種類持ってる国がいたらまずい」）。建国で上限まで選び、銅の鉱脈も出た島には、ウランを置きません。

軍事の部品ではなく島の自然条件（建国で選ぶ鉱業資源と同じ仲間）なので、nature.py や founding.py と
並べて置いてあります（4-2 では銅を軍艦の材料に使うため military/ に置いたが、2026-10-25 に移した）。
"""

from __future__ import annotations

import random

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.rng import make_rng

KEY = "veins"  # island.ext: 鉱脈を置いた版
VERSION = 2
# 版ごとに置く資源。版 1 は銅（4-2）、版 2 はウラン（4-3）。古い世界の島は、足りない版の分だけ
# 置く。版の乱数は別に引くので、先の版の結果は変わらない
RESOURCES = {1: ("copper_ore",), 2: ("uranium_ore",)}


def place(island: IslandState, config: GameConfig, seed: int) -> list[str]:
    """まだ鉱脈の版が古い島に、設定 world.veins の割合で鉱脈を置く。置いた資源を返す。

    島の資源が建国で選べる数 + 1 に届いていたら、それ以上は置かない。"""
    have = island.ext.get(KEY, 0)
    shares = config.get("world.veins")
    most = config.get("founding.max_resources") + 1  # 鉱脈を足しても、資源はここまで
    placed = []
    for version in range(have + 1, VERSION + 1):
        rng = _rng(seed, version, island.id)
        # 置く置かないにかかわらず名前の順に1つずつ引く
        for good in sorted(RESOURCES[version]):
            roll = rng.random()
            full = len(island.resources) >= most
            if roll < shares[good] and good not in island.resources and not full:
                island.resources = sorted([*island.resources, good])
                placed.append(good)
    island.ext[KEY] = VERSION
    return placed


def _rng(seed: int, version: int, island_id: int) -> random.Random:
    """版 version の鉱脈の乱数。版 1 の乱数は 4-2 のまま（銅の結果を変えない）。版 2 からは版を
    乱数の種に足す。"""
    if version == 1:
        return make_rng(seed, 0, KEY, island_id)
    return make_rng(seed, 0, f"{KEY}:{version}", island_id)


def roll(seed: int, island_id: int, good: str) -> float:
    """島の地質の数（0 以上 1 未満。探鉱 MI-68）。鉱脈の資源は place が引くのと同じ数で、鉱脈の
    割合より下なら鉱脈。鉱脈のない資源は、世界の種と島の番号と資源で別に 1 つ引く。"""
    for version, goods in sorted(RESOURCES.items()):
        if good in goods:
            rng = _rng(seed, version, island_id)
            for name in sorted(goods):  # place と同じく名前の順に引く
                value = rng.random()
                if name == good:
                    return value
    return make_rng(seed, 0, f"geology:{good}", island_id).random()
