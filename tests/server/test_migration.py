"""フェーズ1の修正の移行（0003）: 既存の世界の人口を 10 倍にし、首都の区画を首都名にする。"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_phase1_fixes_migration():
    executor = MigrationExecutor(connection)
    executor.migrate([("world", "0002_phase1")])
    apps = executor.loader.project_state([("world", "0002_phase1")]).apps
    World = apps.get_model("world", "World")
    Island = apps.get_model("world", "Island")
    Tile = apps.get_model("world", "Tile")
    world = World.objects.create(seed=1)
    island = Island.objects.create(
        world=world,
        name="旧国",
        wx=0,
        wy=0,
        size=32,
        capital_x=10,
        capital_y=3,
        names={"capital": "旧都"},
        ext={"places": [f"地{i}" for i in range(16)]},
    )
    Tile.objects.create(island=island, x=10, y=3, terrain="plain", population=300)

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
    island = apps.get_model("world", "Island").objects.get(pk=island.pk)
    assert apps.get_model("world", "Tile").objects.get(island=island).population == 3000
    assert island.ext["place_style"] == "japanese"
    assert island.ext["places"][1] == "旧都"  # (10, 3) は 8 マス四方の区画の 2 つ目
