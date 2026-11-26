"""前のターンから変わったマス（グラフィック強化 案3 の静止版）。

サーバー側の決め方（server/web/changes.py）: いちばん新しいターンのバックアップと
今の地形・施設を見た目で比べ、建設・撤去・段階の3種に分ける。地図の API は
?changes=1 のとき、持ち主にだけ changed を足す。
"""

from __future__ import annotations

import gzip
import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from hakoniwa_core.state.serialize import world_to_dict
from server.turns.models import Backup
from server.web.changes import changed_cells
from server.world.models import Island
from server.world.repository import WorldRepository

pytestmark = pytest.mark.django_db


def _own_island(client) -> Island:
    user = User.objects.create_user("owner", password="<redacted>")
    client.force_login(user)
    island = Island.objects.order_by("id").first()
    island.owner = user
    island.save(update_fields=["owner"])
    return island


def _backup_with(world, island_id: int, mutate) -> None:
    """今の世界の写しに mutate を加えて、前のターンのバックアップとして置く。"""
    state = WorldRepository(world).load()
    data = world_to_dict(state)
    island = next(row for row in data["islands"] if row["id"] == island_id)
    mutate(island)
    Backup.objects.create(
        world=world,
        turn=world.turn,
        data=gzip.compress(json.dumps(data, ensure_ascii=False).encode(), compresslevel=6),
    )


def _marks(island: Island) -> set[tuple[int, int, str]]:
    return {(row["x"], row["y"], row["kind"]) for row in changed_cells(island)}


def test_no_backup_no_marks(world):
    island = Island.objects.order_by("id").first()
    assert changed_cells(island) == []


def test_classifies_built_removed_and_stage(world):
    island = Island.objects.order_by("id").first()
    placed = [f for f in island.facilities.all() if f.x >= 0]
    assert placed, "サンプルの世界にはマスを持つ施設があるはず"
    built_target = placed[0]
    stage_target = placed[1]
    used = {(f.x, f.y) for f in placed}
    empty = next(
        (tile.x, tile.y) for tile in island.tiles.order_by("y", "x") if (tile.x, tile.y) not in used
    )
    terrain_cell = next(
        (tile.x, tile.y)
        for tile in island.tiles.order_by("y", "x")
        if (tile.x, tile.y) not in used and (tile.x, tile.y) != empty
    )

    def mutate(before: dict) -> None:
        facilities = before["facilities"]
        # 建設: 前の写しから消すと、今ある施設が「建った」になる
        facilities[:] = [
            f for f in facilities if not (f["x"] == built_target.x and f["y"] == built_target.y)
        ]
        # 撤去: 前の写しにだけある施設は「無くなった」になる
        facilities.append(
            {
                "id": 99999,
                "kind": built_target.kind,
                "x": empty[0],
                "y": empty[1],
                "recipe": "default",
                "built": True,
                "ext": {},
            }
        )
        # 段階: 同じ施設の姿（規模）が前と違う
        row = next(f for f in facilities if f["x"] == stage_target.x and f["y"] == stage_target.y)
        row.setdefault("ext", {})["size"] = (row["ext"].get("size", 1) or 1) + 10
        # 段階: 施設のないマスの地形が違う
        tile = next(
            t for t in before["tiles"] if t["x"] == terrain_cell[0] and t["y"] == terrain_cell[1]
        )
        tile["terrain"] = "sea" if tile["terrain"] != "sea" else "plain"

    _backup_with(world, island.id, mutate)
    assert _marks(island) == {
        (built_target.x, built_target.y, "built"),
        (empty[0], empty[1], "removed"),
        (stage_target.x, stage_target.y, "stage"),
        (terrain_cell[0], terrain_cell[1], "stage"),
    }


def test_completed_construction_counts_as_built(world):
    island = Island.objects.order_by("id").first()
    target = next(f for f in island.facilities.all() if f.x >= 0 and f.built)

    def mutate(before: dict) -> None:
        row = next(f for f in before["facilities"] if f["x"] == target.x and f["y"] == target.y)
        row["built"] = False  # 前は工事中、今は完成

    _backup_with(world, island.id, mutate)
    assert _marks(island) == {(target.x, target.y, "built")}


def test_island_founded_after_backup_has_no_marks(world):
    island = Island.objects.order_by("id").first()

    def mutate(before: dict) -> None:
        pass

    state = WorldRepository(world).load()
    data = world_to_dict(state)
    data["islands"] = [row for row in data["islands"] if row["id"] != island.id]
    Backup.objects.create(
        world=world,
        turn=world.turn,
        data=gzip.compress(json.dumps(data, ensure_ascii=False).encode(), compresslevel=6),
    )
    assert changed_cells(island) == []


def test_api_adds_changed_only_for_owner_with_query(client, world):
    island = _own_island(client)
    url = reverse("api_island_map", args=[island.id])

    plain = client.get(url)
    assert "changed" not in plain.json()

    with_query = client.get(url, {"changes": "1"})
    assert with_query.json()["changed"] == []  # バックアップが無いので印なし

    other = User.objects.create_user("other", password="<redacted>")
    client.force_login(other)
    stranger = client.get(url, {"changes": "1"})
    assert "changed" not in stranger.json()
