"""港と海運業の探し方（W-236 K-1・K-2。結果は変えずに速くする）。

- K-1: port_of・shipping_of は施設を並べ替えずに 1 回なめて、絞りに合う中で番号の一番小さい物を
  返す。港は「建っていて、閉鎖中でも差し押さえ中でもない」物、海運業は種類だけで選ぶ（状態は見ない）
- K-2: 貿易の段の 1 回（_Round）の中では、島ごとの港・海運業を 1 度だけ探して持つ。持つ物は、
  その場で探し直した物といつも同じ（貿易の段は施設を足さず、除かず、状態の印も書かない）
"""

from __future__ import annotations

import copy
import random
from dataclasses import replace

from hakoniwa_core import trade
from hakoniwa_core.economy import SEIZED, is_stopped
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.trade import blocs, port_of, shipping_of
from tests.core.test_trade import _context, game, traded  # noqa: F401
from tests.core.test_trade import run as run_world


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


def test_a_round_looks_for_the_port_and_shipping_once_per_island(game, traded, monkeypatch):  # noqa: F811
    world, island = _island(game, traded)
    [bloc] = blocs.blocs(world)
    calls = {"port": 0, "shipping": 0}

    def counted(name, real):
        def lookup(i, config):
            calls[name] += 1
            return real(i, config)

        return lookup

    monkeypatch.setattr(trade_stage, "port_of", counted("port", port_of))
    monkeypatch.setattr(trade_stage, "shipping_of", counted("shipping", shipping_of))
    r = trade_stage._Round(world, _context(game, world))
    reach = r._reach(island, bloc, "coal")
    members = len([m for m in bloc["members"] if m in r.islands])
    assert reach > 0 and 0 < calls["port"] <= members and 0 < calls["shipping"] <= members
    before = dict(calls)
    for _ in range(5):
        assert r._reach(island, bloc, "coal") == reach
    assert calls == before  # 同じ回の中では探し直さない
    assert r._port(island) is port_of(island, game.config)
    assert r._shipping(island) is shipping_of(island, game.config)
    # 持つのは施設への参照だけ。船の空きは毎回数え直す
    r._shipping(island).economy["fleet"] = []
    for m in bloc["members"]:
        if m != island.id:
            world.islands[m].policy.lend_ships = False
    assert r._reach(island, bloc, "coal") == 0
    # 寿命は段の 1 回。次の回は探し直す（港を閉鎖すると、次の回は港なし）
    port_of(island, game.config).economy["closing"] = 1
    assert trade_stage._Round(world, _context(game, world))._port(island) is None


def test_kept_port_and_shipping_stay_the_same_through_the_trade_stage(game, traded, monkeypatch):  # noqa: F811
    """貿易の段を通して回しても、持っている港・海運業は、使うたびに探し直した物と同じ（段の中で
    施設の追加・削除・状態の書き込みがないことを、動かして確かめる）。"""
    world = copy.deepcopy(traded[0])
    seen = {"port": 0, "shipping": 0}
    kept_port, kept_shipping = trade_stage._Round._port, trade_stage._Round._shipping

    def checked_port(self, island):
        port = kept_port(self, island)
        assert port is port_of(island, self.config)
        seen["port"] += 1
        return port

    def checked_shipping(self, island):
        shipping = kept_shipping(self, island)
        assert shipping is shipping_of(island, self.config)
        seen["shipping"] += 1
        return shipping

    monkeypatch.setattr(trade_stage._Round, "_port", checked_port)
    monkeypatch.setattr(trade_stage._Round, "_shipping", checked_shipping)
    run_world(game, world, 3)
    assert seen["port"] > 0 and seen["shipping"] > 0
