"""地図の変遷（グラフィック強化 案29）: 節目のターンの地図の保存。

- 記録が無い島は、最初に記録が走ったターンの姿を「記録開始」の 1 枚として残す
- 節目（案27 のログ）があったターンだけ、節目の種類を付けた写しを足す
- 写しは最大 16 枚で、最初の 1 枚は落とさない
- 計算は変えない（残すのは地形と施設の見た目だけ）
"""

from __future__ import annotations

import pytest

from hakoniwa_core import map_history
from hakoniwa_core.game import load_game
from hakoniwa_core.state import LogEntry
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def island(game):
    world = generate_world(7, 2, game.config, game.defs)
    island = next(iter(world.islands.values()))
    island.log.clear()
    return island


def snaps(island):
    return island.ext.get(map_history.SNAPSHOTS, [])


def test_first_record_saves_start_snapshot(island):
    map_history.record(island, 30)
    rows = snaps(island)
    assert len(rows) == 1
    assert rows[0]["turn"] == 30
    assert rows[0]["kinds"] == [map_history.START]
    assert rows[0]["size"] == island.size
    assert len(rows[0]["terrain"]) == island.size * island.size
    tile = island.tiles[0]
    assert rows[0]["terrain"][tile.y * island.size + tile.x] == tile.terrain
    placed = [f for f in island.facilities if f.x >= 0]
    assert len(rows[0]["facilities"]) == len(placed)
    if placed:
        row = rows[0]["facilities"][0]
        assert row["kind"] == placed[0].kind
        assert row["x"] == placed[0].x and row["y"] == placed[0].y


def test_milestone_turn_adds_snapshot(island):
    map_history.record(island, 30)
    island.log.append(LogEntry("milestone_population", {"count": 100000}))
    map_history.record(island, 40)
    rows = snaps(island)
    assert len(rows) == 2
    assert rows[1]["turn"] == 40
    assert rows[1]["kinds"] == ["milestone_population"]


def test_quiet_turn_adds_nothing(island):
    map_history.record(island, 30)
    map_history.record(island, 31)
    assert len(snaps(island)) == 1


def test_first_turn_with_milestone_merges_into_start(island):
    island.log.append(LogEntry("anniversary_founding", {"years": 1}))
    map_history.record(island, 13)
    rows = snaps(island)
    assert len(rows) == 1
    assert rows[0]["kinds"] == [map_history.START, "anniversary_founding"]


def test_cap_keeps_first_and_latest(island):
    island.ext[map_history.SNAPSHOTS] = [
        {
            "turn": t,
            "kinds": [map_history.START],
            "size": island.size,
            "terrain": [],
            "facilities": [],
        }
        for t in range(1, map_history.MAX_SNAPSHOTS + 1)
    ]
    island.log.append(LogEntry("milestone_gdp", {"amount": 100000}))
    map_history.record(island, 999)
    rows = snaps(island)
    assert len(rows) == map_history.MAX_SNAPSHOTS
    assert rows[0]["turn"] == 1
    assert rows[-1]["turn"] == 999


def test_snapshot_captures_the_current_look(island):
    tile = island.tiles[0]
    tile.terrain = "mountain" if tile.terrain != "mountain" else "forest"
    map_history.record(island, 5)
    rows = snaps(island)
    assert rows[0]["terrain"][tile.y * island.size + tile.x] == tile.terrain
