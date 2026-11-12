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


@pytest.mark.django_db(transaction=True)
def test_farm_grade_from_recipe_migration():
    """0007（W-257）: 段階の作り方の農場の段階を作り方にそろえる。段階を上げる工事が残っている農場は
    そろえず、作り方を替える予約はそろえた段階の同じ作物の作り方にする。"""
    before = [("world", "0006_automobile_recipe")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    apps = executor.loader.project_state(before).apps
    World = apps.get_model("world", "World")
    Island = apps.get_model("world", "Island")
    Facility = apps.get_model("world", "Facility")
    world = World.objects.create(seed=1)
    island = Island.objects.create(world=world, name="旧国", wx=0, wy=0, size=32)

    def farm(number, x, recipe, built=True, ext=None):
        return Facility.objects.create(
            island=island,
            number=number,
            kind="farm",
            x=x,
            y=0,
            recipe=recipe,
            built=built,
            ext=ext or {},
        )

    mech = farm(1, 1, "default_mechanized", ext={"recipe_next": "cotton"})
    modern = farm(2, 2, "fertilized_modern")
    hand = farm(3, 3, "default")
    working = farm(4, 4, "cotton_mechanized", built=False)
    upgrading = farm(5, 5, "default_mechanized")
    farm(6, -1, "default_mechanized", built=False, ext={"grade_to": 1, "expands": 5})

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
    Facility = apps.get_model("world", "Facility")

    def ext(f):
        return Facility.objects.get(pk=f.pk).ext

    assert ext(mech) == {"grade": 1, "recipe_next": "cotton_mechanized"}
    assert ext(modern) == {"grade": 2}
    assert ext(hand) == {}
    assert ext(working) == {"grade": 1}  # 工事中も段階だけ（grade_build なし。手作業の資材で建つ）
    assert ext(upgrading) == {}  # 段階を上げる工事が済むと上がる
