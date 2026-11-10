"""解雇の決まり（LB-43。2026-11-10、W-251）: 減らせる人の割合の上限・抱えた人の賃金と作る量・解雇
手当・閉鎖の全員解雇・政策の命令。"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import arrears, dismissal, facility_entity, household
from hakoniwa_core.economy.households import jobless_layer
from hakoniwa_core.modules.employment import Employment
from hakoniwa_core.modules.labor import set_labor_policy
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, OWNER_STATE
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def _mill(game, regime="mixed"):
    """町の横の民営の製鉄所が全力の人数で働いている島（操業目標は 0 に落ちた）。"""
    island = make_island(game)
    home(game, island, 5, 6, 20000)
    mill = add(island, "steel_mill", 6, 6, owner=OWNER_PRIVATE, accounting=ACCOUNTING_SELF)
    with_economy(game, island)
    island.regime["economy"] = regime
    full = fac.workers_of(game.defs, mill)
    mill.workers = full
    mill.economy["staff"] = {"5,6": full}
    mill.economy["target"] = 0
    return island, mill, full


def test_the_rule_caps_how_many_a_facility_can_shed_in_a_month(game):
    island, mill, full = _mill(game)
    config = game.config
    assert dismissal.level(island, game.defs) == dismissal.NORMAL  # 混合経済の既定
    rate = config.get("labor.dismissal")["normal"]["rate"] + config.get("labor.attrition")
    run(game, island, ["employment"])
    kept = full - int(full * rate)
    assert mill.workers == kept
    # 操業目標に要る人数（0）を超えた人は抱えた人
    assert mill.economy[dismissal.HELD] == kept
    # 自然に辞める人を除いた分が解雇した人（手当は賃金の段で払う）
    assert mill.economy["dismissed"] == full - dismissal.natural_leavers(config, full) - kept
    # 抱えた人で操業目標より多く作らない
    mill.economy["target"] = 300
    run_ = Production._prepare(island, mill, context(game), config.get("power.good"))
    assert run_.rate <= 300


def test_weak_rule_sheds_faster_and_pays_no_severance(game):
    island, mill, full = _mill(game, regime="market")
    config = game.config
    assert dismissal.level(island, game.defs) == dismissal.WEAK
    rate = config.get("labor.dismissal")["weak"]["rate"] + config.get("labor.attrition")
    run(game, island, ["employment", "payroll"])
    assert mill.workers == full - int(full * rate)
    assert island.stats.get("labor.severance", 0) == 0


def test_severance_is_paid_to_the_households_that_lost_their_jobs(game):
    island, mill, full = _mill(game)
    island.policy.dismissal = dismissal.STRONG
    terms = dismissal.terms(island, game.defs, game.config)
    mill.economy["wage"] = 1000
    layer = household(jobless_layer(island))
    before = ledger.money(island, layer)
    run(game, island, ["employment", "payroll"])
    cut = island.stats["labor.dismissed"]
    assert cut == full - dismissal.natural_leavers(game.config, full) - mill.workers
    # 解雇した人1人に、月の賃金 × 強いの月数
    assert island.stats["labor.severance"] == round(cut * 1000 * terms["months"])
    assert ledger.money(island, layer) > before
    assert "dismissed" not in mill.economy  # 払ったら消す


def test_unpaid_severance_becomes_wage_arrears(game):
    island, mill, full = _mill(game)
    entity = facility_entity(mill.id)
    # 施設の預金を空にする（手当を払えない）
    spare = ledger.money(island, entity)
    ledger.pay(island, entity, ledger.GOV, spare, "equity_return")
    mill.economy["dismissed"] = 10
    paid = dismissal.pay(island, game.defs, game.config, mill, 10)
    assert paid > 0
    assert arrears.total(island, entity) == paid


def test_closing_dismisses_everyone_with_severance(game):
    island, mill, full = _mill(game)
    mill.economy["closing"] = 1  # 閉鎖に入った: 割合の上限によらず全員を解雇する
    run(game, island, ["employment"])
    assert mill.workers == 0
    assert mill.economy["dismissed"] == full - dismissal.natural_leavers(game.config, full)


def test_planned_state_facilities_keep_hoarding_and_others_are_outside(game):
    island, mill, full = _mill(game)
    # 計画経済の国営施設は人の抱え込み（PL-57）のまま、解雇の決まりの外
    mill.owner = OWNER_STATE
    island.regime["economy"] = "planned"
    ctx = context(game)
    assert not dismissal.covered(island, game.defs, game.config, mill, ctx.turn)
    _, kind = Employment()._bounds(island, ctx, mill, 0)
    assert kind == "plan"
    # 計画経済をやめた後は、解雇の決まりの割合で減る（一度に減らない）
    island.regime["economy"] = "market"
    least, kind = Employment()._bounds(island, ctx, mill, 0)
    assert kind == "dismissal"
    assert least == full - int(full * (0.2 + game.config.get("labor.attrition")))
    # 畑の農場は季節で人が出入りするので外
    farm = add(island, "farm", 8, 8)
    farm.recipe = next(
        r for r in game.defs.facilities["farm"]["recipes"] if fac.grow_turns(game.defs, "farm", r)
    )
    assert not dismissal.covered(island, game.defs, game.config, farm, ctx.turn)


def test_held_wages_lower_the_actual_profit():
    from hakoniwa_core.state import Facility

    f = Facility(id=1, kind="steel_mill", x=0, y=0, recipe="default", built=True)
    f.economy.update(target=500, wage=100)
    assert dismissal.actual_profit(f, 10_000) == 10_000  # 抱えていなければそのまま
    f.economy[dismissal.HELD] = 30
    assert dismissal.actual_profit(f, 10_000) == 5_000 - 3_000


def test_policy_order_sets_the_rule_and_logs_it(game):
    island, mill, full = _mill(game)
    set_labor_policy(island, {"dismissal": "strong"}, context(game))
    assert island.policy.dismissal == "strong"
    assert [e.kind for e in island.log][-1] == "dismissal_policy_set"
    # 体制を変えると、新しい経済体制の既定に戻る
    assert dismissal.level(island, game.defs) == dismissal.STRONG
