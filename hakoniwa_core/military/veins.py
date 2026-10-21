"""銅の鉱脈の置き方（設計書 15-military MI-60）。

銅鉱石は建国のとき鉱業資源として選べますが（goods.yaml の mining）、選ばなかった島にも、現実の
銅の産地が限られるのと同じに、一部の島に鉱脈を置きます。置くかは世界の種と島の番号だけで決まる
乱数で、ターンや島の順番には左右されません。島ごとに 1 度だけ置き（版の印 island.ext["veins"]）、
鉱業資源の上限（founding.max_resources）の外とします。経済のない島も対象です。
"""

from __future__ import annotations

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.rng import make_rng

KEY = "veins"  # island.ext: 鉱脈を置いた版
VERSION = 1


def place(island: IslandState, config: GameConfig, seed: int) -> list[str]:
    """まだ鉱脈の版が古い島に、設定 military.veins の割合で鉱脈を置く。置いた資源を返す。"""
    if island.ext.get(KEY, 0) >= VERSION:
        return []
    island.ext[KEY] = VERSION
    rng = make_rng(seed, 0, KEY, island.id)
    placed = []
    # 置く置かないにかかわらず名前の順に1つずつ引く。資源を足すときは、名前の順で後ろに置くか、
    # 新しい版（VERSION）で別の乱数を使う（先に引く資源の結果が変わるため）
    for good, share in sorted(config.get("military.veins").items()):
        roll = rng.random()
        if roll < share and good not in island.resources:
            island.resources = sorted([*island.resources, good])
            placed.append(good)
    return placed
