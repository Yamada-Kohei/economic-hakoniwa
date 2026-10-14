"""フェーズ2-3 のインフラ・公共事業・教育・技術者を、小さな手作りの島で確かめる。"""

from __future__ import annotations

from hakoniwa_core import autoplan, infra, stock
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import EDUCATION, freight, groundwork, productivity
from hakoniwa_core.economy.households import family_of, set_workers
from hakoniwa_core.orders import check_order
from tests.core.test_rules import (
    add,
    freight_island,
    home,
    make_island,
    run,
    with_economy,
    without_roads,
)
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）

ROAD = "road"
WORKS = ["employment", "construction", "payroll", "market", "infrastructure"]


def wear(game, kind: str) -> int:
    return round(game.defs.infrastructure[kind]["wear"] * infra.FULL)


# --- 建国と移し替えの初めの値 ---------------------------------------------------------


def test_groundwork_sets_roads_water_school_and_engineers(game):
    island = make_island(game)
    home(game, island, 5, 6, 8000, level="city")
    add(island, "farm", 9, 9)
    island.tile(9, 11).infra["power"] = 1  # フェーズ1・2 の送電線
    with_economy(game, island, groundwork=True)
    assert island.economy.ext["version"] == groundwork.VERSION
    # 送電線は送電網 100%、施設と町のマスとその周りは道路 60%、都市のマスは上下水道 80%
    assert island.tile(9, 11).infra["power"] == [infra.FULL, infra.FULL]
    for x, y in ((9, 9), (10, 10), (5, 6), (4, 7)):
        assert infra.level(island.tile(x, y), ROAD) == 600
    assert infra.level(island.tile(12, 3), ROAD) == 0
    assert infra.water(game.defs, island.tile(5, 6)) == 800
    # 首都の近くに学校が建った状態で1つ、教育水準 60%、教育費の予算、技術者
    schools = [f for f in island.facilities if f.kind == "school"]
    assert len(schools) == 1 and schools[0].built
    assert island.economy.education == 600
    assert island.policy.budget[EDUCATION] > 0
    assert island.economy.workers[fac.ENGINEER] > 0
    # 2度目は何もしない
    groundwork.prepare(island, game.defs, game.config)
    assert sum(1 for f in island.facilities if f.kind == "school") == 1


# --- 工事の命令と公共事業 ------------------------------------------------------------


def test_order_queues_land_tiles_once(game):
    island = make_island(game)
    home(game, island, 5, 6, 2000)
    with_economy(game, island)
    without_roads(island)
    payload = {"kind": ROAD, "tiles": [[3, 3], [4, 3], [0, 0]]}  # (0, 0) は海
    run(game, island, ["execute_orders"], orders=[("build_infrastructure", payload)])
    assert infra.queue(island) == [[ROAD, 3, 3], [ROAD, 4, 3]]
    assert [e.args["count"] for e in island.log if e.kind == "infrastructure_ordered"] == [2]
    # 並んでいるマスと海だけなら、命令は通らない
    again = check_order(game.defs, game.registry, island, "build_infrastructure", payload)
    assert not again.ok
    unknown = {"kind": "canal", "tiles": [[5, 5]]}
    assert not check_order(game.defs, game.registry, island, "build_infrastructure", unknown).ok


def test_public_works_build_a_queued_road_from_the_warehouse(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    without_roads(island)
    infra.enqueue(island, ROAD, [(3, 3)])
    # 石材を作れない島なので、道路は代わりの資材（建材）で作る
    fallback = game.defs.infrastructure[ROAD]["fallback"]["building_materials"]
    stock.add(island.warehouse, "building_materials", 10_000, 0, game.defs.goods)
    run(game, island, WORKS)  # 1ターン目は、国の倉庫から工事の資材が移るだけ
    assert infra.level(island.tile(3, 3), ROAD) == 0
    run(game, island, WORKS)
    tile = island.tile(3, 3)
    assert infra.level(tile, ROAD) == infra.FULL - wear(game, ROAD)  # 工事の後に傷む
    assert infra.keep(tile, ROAD) == infra.FULL
    assert infra.queue(island) == []
    assert island.stats["works.workers"] > 0
    used = 10_000 - stock.total(island.warehouse, "building_materials")
    assert used >= fallback


def test_roads_wear_and_are_kept_up(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    tile = island.tile(5, 6)  # 町のマスは道路 60%
    run(game, island, ["infrastructure"])
    assert infra.level(tile, ROAD) == 600 - wear(game, ROAD)
    assert infra.keep(tile, ROAD) == 600
    stock.add(island.warehouse, "building_materials", 10_000, 0, game.defs.goods)
    run(game, island, WORKS, turns=3)
    # 維持の工事が、傷んだ分を毎ターン目標まで戻す（そのターンの終わりにまた傷む）
    assert infra.level(tile, ROAD) == 600 - wear(game, ROAD)
    assert stock.total(island.warehouse, "building_materials") < 10_000


# --- 効き目 ---------------------------------------------------------------------------


def test_infrastructure_and_education_change_productivity(game):
    island = make_island(game)
    home(game, island, 5, 6, 4000)
    farm = add(island, "farm", 9, 9)  # インフラ 0.3・教育 0.2
    with_economy(game, island)
    without_roads(island)
    # 交通 0% なら 1 + 0.3 ×（0.7 − 1）、教育 60% は 1
    assert productivity.update(island, game.defs, game.config, farm) == 910
    for x in range(8, 11):
        for y in range(8, 11):
            infra.set_level(island.tile(x, y), ROAD, infra.FULL)
    assert productivity.update(island, game.defs, game.config, farm) == 1060
    island.economy.education = infra.FULL
    assert productivity.update(island, game.defs, game.config, farm) == round(1.06 * 1.04 * 1000)


def test_roads_shorten_freight(game):
    island, far, plant, _, _ = freight_island(game)  # 道路なし
    before = freight.compute(island, game.defs, game.config)[0][far.id]
    for y in (10, 11, 12):  # 林業場 (12, 12) から建材工場 (12, 10) までの道
        infra.set_level(island.tile(12, y), ROAD, infra.FULL)
    freight.forget(island)
    after = freight.compute(island, game.defs, game.config)[0][far.id]
    assert after == before // 2  # 道路 100% のマスを越える重さは 0.5


# --- 教育と技術者 ----------------------------------------------------------------------


def test_school_moves_education_toward_its_goal(game):
    island = make_island(game)
    home(game, island, 5, 6, 20_000)  # 子ども 3,000 人（学校1つの定員）
    with_economy(game, island, groundwork=True)
    school = next(f for f in island.facilities if f.kind == "school")
    school.operating_rate = fac.FULL
    run(game, island, ["education"])
    assert island.economy.education == 600 + round(400 / 12)
    assert island.stats["education.goal"] == 100
    school.operating_rate = 0  # 休校: 目標 0 に向かって 1/12 ずつ下がる
    level = island.economy.education
    run(game, island, ["education"])
    assert island.economy.education == level - round(level / 12)


def test_university_trains_engineers_and_some_retire(game):
    island = make_island(game)
    home(game, island, 5, 6, 20_000)
    university = add(island, "university", 9, 9)
    with_economy(game, island, groundwork=True)
    university.operating_rate = fac.FULL
    pool = island.economy.workers[fac.ENGINEER]
    rate = game.config.get("education.retire_rate")
    run(game, island, ["education"])
    assert island.economy.workers[fac.ENGINEER] == pool + 10 - int(pool * rate)
    assert island.stats["engineers.trained"] == 10
    layer = island.economy.households["engineers"]
    assert layer["persons"] == family_of(
        game.defs, fac.ENGINEER, island.economy.workers[fac.ENGINEER]
    )


def test_engineers_take_their_seats_and_spare_ones_fill_general_jobs(game):
    island = make_island(game)
    home(game, island, 5, 6, 8000)  # 一般の労働者 4,000 人
    plant = add(island, "machinery_plant", 6, 5, stock={"steel": 4000})  # 一般 1,350・技術者 150
    far = add(island, "farm", 12, 12)  # 通勤圏の外
    with_economy(game, island)  # 技術者 0
    run(game, island, ["employment"])
    # 技術者がいなければ席は埋まらず、稼働率の上限は 0.6
    assert plant.economy["engineers_wanted"] == 150 and "engineers" not in plant.economy
    assert fac.engineer_cap(game.config, plant) == 600
    assert far.workers == 0
    set_workers(island, game.defs, fac.ENGINEER, 400)
    run(game, island, ["employment"])
    assert plant.economy["engineers"] == 150
    assert fac.engineer_cap(game.config, plant) == fac.FULL
    assert plant.economy["engineer_wage"] == 2 * plant.economy["wage"]
    # 余った 250 人は、通勤圏の外の農場の一般の席にも就く
    assert far.economy["engineers_general"] == 250
    assert far.workers == 250


def test_fewer_students_enter_university_when_engineers_are_jobless(game):
    island = make_island(game)
    home(game, island, 5, 6, 8000)  # 一般の労働者 4,000 人で、どの施設の一般の席も埋まる
    add(island, "machinery_plant", 6, 5, stock={"steel": 4000})  # 技術者の席 150
    university = add(island, "university", 7, 6)
    with_economy(game, island)
    university.operating_rate = fac.FULL
    set_workers(island, game.defs, fac.ENGINEER, 176)
    run(game, island, ["employment", "education"])
    # 26 人が席に就けず、失業率 14.8%。上限 30% の半分ほどなので、入学は半分ほど
    jobless = 26 / 176
    share = 1 - jobless / game.config.get("education.intake_unemployment")
    assert island.stats["engineers.trained"] == int(10 * share) == 5
    set_workers(island, game.defs, fac.ENGINEER, 400)  # 失業率が上限を超えると、入学はない
    run(game, island, ["employment", "education"])
    assert island.stats["engineers.trained"] == 0


# --- 標準の計画 ------------------------------------------------------------------------


def test_plan_keeps_the_machinery_a_steel_mill_waits_for(game):
    island = make_island(game)
    home(game, island, 5, 6, 8000)
    add(island, "steel_mill", 9, 9, built=False)  # 機械 5 を待つ。島に鋼鉄と機械を作る施設はない
    with_economy(game, island)
    island.warehouse = {}
    for good, qty in (("machinery", 5000), ("steel", 20_000), ("building_materials", 200_000)):
        stock.add(island.warehouse, good, qty, 0, game.defs.goods)
    # 倉庫の機械は製鉄所の分しかないので、機械を使う発電所は建てない（製鉄所と機械工場が
    # 互いの鋼鉄と機械を待って止まるので）。機械を作る機械工場は建ててよい
    assert not autoplan._materials_ok(island, game.defs, "thermal_power_plant")
    assert autoplan._materials_ok(island, game.defs, "machinery_plant")
    stock.add(island.warehouse, "machinery", 5000, 0, game.defs.goods)
    assert autoplan._materials_ok(island, game.defs, "thermal_power_plant")
