"""港と海運業の探し方（W-236 K-1。結果は変えずに速くする）。

- K-1: port_of・shipping_of は施設を並べ替えずに 1 回なめて、絞りに合う中で番号の一番小さい物を
  返す。港は「建っていて、閉鎖中でも差し押さえ中でもない」物、海運業は種類だけで選ぶ（状態は見ない）
"""

from __future__ import annotations

import copy
import random
from dataclasses import replace

from hakoniwa_core import trade
from hakoniwa_core.economy import SEIZED, is_stopped
from hakoniwa_core.trade import port_of, shipping_of
from tests.core.test_trade import game, traded  # noqa: F401


def _sorted_port_of(island, config):
    """K-1 の前の探し方（番号の順に並べ替えて、初めに合った物）。"""
    for f in sorted(island.facilities, key=lambda f: f.id):
        if trade.is_port(config, f) and f.built and not is_stopped(f):
            return f
    return None


def _sorted_shipping_of(island, config):
    for f in sorted(island.facilities, key=lambda f: f.id):
        if trade.is_shipping(config, f):
            return f
    return None


def _island(game, traded):  # noqa: F811
    world = copy.deepcopy(traded[0])
    return world, world.islands[1]


def _more(island, f, count):
    """f と同じ種類の施設を count 個足す（番号は続き。帳簿は別の物）。足した施設を返す。"""
    added = []
    for _ in range(count):
        g = replace(f, id=island.next_facility_id(), economy=dict(f.economy), ext=dict(f.ext))
        island.facilities.append(g)
        added.append(g)
    return added


def test_port_of_returns_the_lowest_numbered_working_port(game, traded):  # noqa: F811
    _, island = _island(game, traded)
    first = port_of(island, game.config)
    assert first is not None
    second, third, fourth = _more(island, first, 3)
    # 施設の並びは番号の順とは限らない（並びを逆にしても、番号の一番小さい港を選ぶ）
    island.facilities.reverse()
    assert port_of(island, game.config) is first
    # 閉鎖の途中の港は飛ばす
    first.economy["closing"] = 1
    assert port_of(island, game.config) is second
    # まだ建っていない港は飛ばす
    second.built = False
    assert port_of(island, game.config) is third
    # 差し押さえ中の港は飛ばす。管財人が動かしている港（FM-59）は動いている港に数える
    third.economy[SEIZED] = {"run": False}
    assert port_of(island, game.config) is fourth
    third.economy[SEIZED] = {"run": True}
    assert port_of(island, game.config) is third
    # 合う港がなければ None
    third.economy[SEIZED] = {"run": False}
    fourth.economy["closing"] = 1
    assert port_of(island, game.config) is None


def test_shipping_of_picks_by_kind_only_and_ignores_the_state(game, traded):  # noqa: F811
    _, island = _island(game, traded)
    first = shipping_of(island, game.config)
    assert first is not None
    _more(island, first, 2)
    island.facilities.reverse()
    assert shipping_of(island, game.config) is first
    # 港と違い、建っているか・閉鎖中か・差し押さえ中かは見ない
    first.built = False
    first.economy["closing"] = 1
    first.economy[SEIZED] = {"run": False}
    assert shipping_of(island, game.config) is first
    island.facilities = [f for f in island.facilities if not trade.is_shipping(game.config, f)]
    assert shipping_of(island, game.config) is None


def test_lookups_match_the_sorted_search_on_shuffled_facilities(game, traded):  # noqa: F811
    """並べ替えて探す前の形と、どんな並び・状態でも同じ施設を返す。"""
    _, island = _island(game, traded)
    ports = [port_of(island, game.config), *_more(island, port_of(island, game.config), 4)]
    _more(island, shipping_of(island, game.config), 3)
    rng = random.Random(236)
    for _ in range(200):
        rng.shuffle(island.facilities)
        for f in ports:
            f.built = rng.random() < 0.7
            f.economy.pop("closing", None)
            f.economy.pop(SEIZED, None)
            roll = rng.random()
            if roll < 0.2:
                f.economy["closing"] = 1
            elif roll < 0.4:
                f.economy[SEIZED] = {"run": rng.random() < 0.5}
        assert port_of(island, game.config) is _sorted_port_of(island, game.config)
        assert shipping_of(island, game.config) is _sorted_shipping_of(island, game.config)
