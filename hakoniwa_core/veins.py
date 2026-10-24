"""鉱脈の置き方（銅とウラン。設計書 15-military MI-60）。

銅鉱石は建国のとき鉱業資源として選べますが（goods.yaml の mining）、選ばなかった島にも、現実の
銅の産地が限られるのと同じに、一部の島に鉱脈を置きます。置くかは世界の種と島の番号だけで決まる
乱数で、ターンや島の順番には左右されません。島ごとに 1 度だけ置き（版の印 island.ext["veins"]）、
鉱業資源の上限（founding.max_resources）の外とします。経済のない島も対象です。

軍事の部品ではなく島の自然条件（建国で選ぶ鉱業資源と同じ仲間）なので、nature.py や founding.py と
並べて置いてあります（4-2 では銅を軍艦の材料に使うため military/ に置いたが、2026-10-25 に移した）。
"""

from __future__ import annotations

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.rng import make_rng

KEY = "veins"  # island.ext: 鉱脈を置いた版
VERSION = 2
# 版ごとに置く資源。版 1 は銅（4-2）、版 2 はウラン（4-3）。古い世界の島は、足りない版の分だけ
# 置く。版の乱数は別に引くので、先の版の結果は変わらない
RESOURCES = {1: ("copper_ore",), 2: ("uranium_ore",)}


def place(island: IslandState, config: GameConfig, seed: int) -> list[str]:
    """まだ鉱脈の版が古い島に、設定 world.veins の割合で鉱脈を置く。置いた資源を返す。"""
    have = island.ext.get(KEY, 0)
    shares = config.get("world.veins")
    placed = []
    for version in range(have + 1, VERSION + 1):
        # 版 1 の乱数は 4-2 のまま（銅の結果を変えない）。版 2 からは版を乱数の種に足す
        rng = (
            make_rng(seed, 0, KEY, island.id)
            if version == 1
            else make_rng(seed, 0, f"{KEY}:{version}", island.id)
        )
        # 置く置かないにかかわらず名前の順に1つずつ引く
        for good in sorted(RESOURCES[version]):
            roll = rng.random()
            if roll < shares[good] and good not in island.resources:
                island.resources = sorted([*island.resources, good])
                placed.append(good)
    island.ext[KEY] = VERSION
    return placed
