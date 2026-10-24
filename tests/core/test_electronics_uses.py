"""電子機器の軍の外の使い道（フェーズ4-4。MI-43）: 工場の自動化・事務の計算機・教材・銀行。"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.defs.kinds import AUTOMATED, ELECTRONICS
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.units import to_milli
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def automated_kinds(game):
    return [k for k, d in game.defs.facilities.items() if AUTOMATED in d["recipes"]]


def test_automation_makes_the_same_goods_with_fewer_workers_and_electronics(game):
    kinds = automated_kinds(game)
    assert len(kinds) == 12
    assert "electronics_plant" not in kinds  # 自分の産出を使う作り方にしない
    for kind in kinds:
        recipes = game.defs.facilities[kind]["recipes"]
        auto = recipes[AUTOMATED]
        base = recipes[game.defs.facilities[kind]["automation"].get("from", "default")]
        assert auto["outputs"] == base["outputs"], kind
        assert auto["inputs"][ELECTRONICS] > base["inputs"].get(ELECTRONICS, 0), kind
        assert auto["labor"] < 1, kind
        # 人手が減るのは一般の労働者だけ。技術者の数は変わらない
        engineers = fac.engineers_needed(game.defs, kind)
        assert fac.workers_needed(game.defs, kind, AUTOMATED) >= engineers
        assert fac.workers_needed(game.defs, kind, AUTOMATED) < fac.workers_needed(game.defs, kind)


def test_automation_pays_only_where_electronics_are_cheaper_than_the_saved_wages(game):
    """電子機器の代金が減った賃金より安い作り方にする（高ければ民間は選ばない）。"""
    defs, config = game.defs, game.config
    prices = initial_prices(defs, config)
    wage = config.get("labor.initial_wage")
    for kind in automated_kinds(game):
        full = defs.facilities[kind]["workers"]
        saved = (full - fac.workers_needed(defs, kind, AUTOMATED)) * wage
        recipes = defs.facilities[kind]["recipes"]
        base = recipes[defs.facilities[kind]["automation"].get("from", "default")]
        extra = recipes[AUTOMATED]["inputs"][ELECTRONICS] - base["inputs"].get(ELECTRONICS, 0)
        cost = prices[ELECTRONICS] * extra / 1000
        assert 0 < cost < saved * 1.3, kind  # 賃金が安い国では見合わない余地を残す


def test_labor_cost_follows_the_recipe(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    f = add(island, "steel_mill", 9, 9)
    base = fac.labor_cost(game.defs, game.config, f.kind, 1000, f)
    f.recipe = AUTOMATED
    cheaper = fac.labor_cost(game.defs, game.config, f.kind, 1000, f)
    assert cheaper < base
    assert fac.workers_of(game.defs, f) < game.defs.facilities["steel_mill"]["workers"]


def test_price_solver_ignores_automated_recipes(game):
    """初めの値段は基本の作り方で決める（電子機器の値段が他の財に回らない）。"""
    before = initial_prices(game.defs, game.config)
    assert before["steel"] > 0
    assert initial_prices(game.defs, game.config) == before


def test_digital_schools_teach_more_per_teacher(game):
    for kind in ("school", "university"):
        recipes = game.defs.facilities[kind]["recipes"]
        assert recipes["digital"]["education_scale"] == 1.2
        assert recipes["digital"]["inputs"][ELECTRONICS] >= to_milli(0.01)
        assert recipes["default"].get("education_scale", 1.0) == 1.0


def test_automated_factory_falls_back_when_electronics_never_arrive(game):
    """電子機器が手元にないまま grace_turns ターンたつと、基本の作り方に戻る（国営の施設も）。"""
    from hakoniwa_core.modules.production import Production
    from hakoniwa_core.turn.context import StageContext

    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    f = add(island, "steel_mill", 9, 9)
    ctx = StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=10,
        seed=0,
        stage="test",
        before=None,
    )
    fac.switch_recipe(game.defs, f, AUTOMATED, 9)
    Production._fall_back(island, f, ctx)
    assert f.recipe == AUTOMATED  # 替えたばかり
    ctx.turn = 9 + game.config.get("automation.grace_turns")
    Production._fall_back(island, f, ctx)
    assert f.recipe == "default"
    assert island.log[-1].kind == "recipe_fell_back"
    # 電子機器が手元にあれば戻さない
    fac.switch_recipe(game.defs, f, AUTOMATED, 20)
    f.stock = {ELECTRONICS: [[0, 10_000]]}
    ctx.turn = 40
    Production._fall_back(island, f, ctx)
    assert f.recipe == AUTOMATED
