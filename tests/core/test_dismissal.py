"""解雇の決まり（LB-43。2026-11-10、W-251）: 減らせる人の割合の上限・抱えた人の賃金と作る量・解雇
手当・閉鎖の全員解雇・政策の命令。"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    Payer,
    arrears,
    dismissal,
    facility_entity,
    funds,
    household,
    payer_of,
)
from hakoniwa_core.economy.households import jobless_layer
from hakoniwa_core.modules.employment import Employment
from hakoniwa_core.modules.labor import set_labor_policy
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import ACCOUNTING_SELF, ACCOUNTING_TREASURY, OWNER_PRIVATE, OWNER_STATE
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
    assert dismissal.actual_profit(f, 10_000) == 10_000 - 3_000


def test_policy_order_sets_the_rule_and_logs_it(game):
    island, mill, full = _mill(game)
    set_labor_policy(island, {"dismissal": "strong"}, context(game))
    assert island.policy.dismissal == "strong"
    assert [e.kind for e in island.log][-1] == "dismissal_policy_set"
    # 体制を変えると、新しい経済体制の既定に戻る
    assert dismissal.level(island, game.defs) == dismissal.STRONG


def test_a_facility_keeps_the_people_it_needed_within_the_severance_months(game):
    """操業目標が一時に下がっただけなら、手当の月数のうちに要った人数までは解雇せずに抱える
    （解雇して手当を払い、翌月また雇い直すより安い）。手当のない弱い段は抱えない。"""
    island, mill, full = _mill(game)
    ctx = context(game)
    employment = Employment()
    assert dismissal.terms(island, game.defs, game.config)["months"] == 2  # ふつう
    employment._bounds(island, ctx, mill, full)  # 先月は全員が要った
    least, _ = employment._bounds(island, ctx, mill, 0)
    assert least == full  # 割合の上限より多く残す
    employment._bounds(island, ctx, mill, 0)
    least, _ = employment._bounds(island, ctx, mill, 0)  # 2 か月続けて要らなかった
    rate = game.config.get("labor.dismissal")["normal"]["rate"] + game.config.get("labor.attrition")
    assert least == full - int(full * rate)
    # 弱い段は抱えない
    island.policy.dismissal = dismissal.WEAK
    employment._bounds(island, ctx, mill, full)
    least, _ = employment._bounds(island, ctx, mill, 0)
    assert least < full
    assert dismissal.NEEDS not in mill.economy


def test_treasury_facility_severance_uses_the_unreserved_treasury(game):
    """国庫直轄の施設の手当は、施設の予算の項目の月割りを使い切っていても、どの項目にも取り置いて
    いない国庫のお金から払う（法で決まった手当は義務的な支出。未払い賃金にしない）。"""
    island, mill, full = _mill(game)
    mill.owner, mill.accounting = OWNER_STATE, ACCOUNTING_TREASURY
    mill.economy["wage"] = 10
    payer = payer_of(game.defs, game.config, mill)
    assert payer.entity == ledger.GOV and payer.item
    state = funds.fiscal_state(island)
    state["caps"][payer.item] = state["used"].get(payer.item, 0)  # 月割りを使い切った
    assert funds.available(island, payer) == 0
    assert funds.available(island, Payer(ledger.GOV)) >= 200
    before = ledger.money(island, household(jobless_layer(island)))
    assert dismissal.pay(island, game.defs, game.config, mill, 10) == 10 * 10 * 2
    assert arrears.total(island, ledger.GOV) == 0
    assert ledger.money(island, household(jobless_layer(island))) == before + 200


def _short_handed(game, wage_change):
    """製鉄所が先月の人数を抱え、横の採石場が人手を探す島（失業者はいない）。"""
    island, mill, full = _mill(game)
    island.tile(5, 6).population = full  # 失業者はいない（全員が製鉄所で働いている）
    mill.economy[dismissal.NEEDS] = [full]  # 先月は全員が要った: 今月は解雇せずに抱える
    other = add(island, "quarry", 4, 6, owner=OWNER_PRIVATE, accounting=ACCOUNTING_SELF)
    other.economy["target"] = 1000
    ledger.pay(island, ledger.GOV, facility_entity(other.id), 10**9, "capital_injection")
    other.economy["wage"] = mill.economy.get("wage", 0) + wage_change
    run(game, island, ["employment"])
    return mill, other, full


def test_short_handed_facilities_hire_the_held_workers_first(game):
    """抱えている余った人は、同じ賃金なら人手の足りない施設へ自分から移る（解雇でなく、手当は
    ない）。1 か月に移るのは引き抜きと同じ上限（labor.poach_limit）まで。"""
    mill, other, full = _short_handed(game, 0)
    assert other.workers > 0
    assert other.workers <= int(full * game.config.get("labor.poach_limit"))
    assert mill.economy.get("dismissed", 0) == 0
    assert mill.workers + other.workers <= full


def test_held_workers_do_not_move_to_a_lower_wage(game):
    """抱えた人は、賃金の安い施設へは移らない（席にいれば元の賃金がもらえる）。"""
    mill, other, full = _short_handed(game, -1)
    assert other.workers == 0


def test_new_hires_on_fixed_terms_leave_first_without_the_cap_or_severance(game):
    """入って labor.fixed_term か月までの人は有期: 減らすときは新しい人から、割合の上限にも手当にも
    かからずに更新しないで辞めてもらう（解雇に数えない）。決まりは期間の定めのない人にかける。"""
    island, mill, full = _mill(game)
    config = game.config
    newcomers = full // 2
    mill.economy[dismissal.FIXED_TERM] = [newcomers]
    mill.economy["wage"] = 1000
    run(game, island, ["employment", "payroll"])
    regular = full - newcomers
    rate = config.get("labor.dismissal")["normal"]["rate"] + config.get("labor.attrition")
    assert mill.workers == regular - int(regular * rate)
    cut = full - dismissal.natural_leavers(config, full) - mill.workers
    assert island.stats["labor.term_ended"] == newcomers
    assert island.stats["labor.dismissed"] == cut - newcomers
    assert island.stats["labor.severance"] == round((cut - newcomers) * 1000 * 2)
    assert dismissal.FIXED_TERM not in mill.economy  # 有期の人はもういない


def test_fixed_terms_become_permanent_after_the_months(game):
    """有期の人は labor.fixed_term か月の後に期間の定めのない雇用に変わる。辞めるのは新しい人から"""
    from hakoniwa_core.state import Facility

    config = game.config
    months = config.get("labor.fixed_term")
    f = Facility(id=1, kind="steel_mill", x=0, y=0, recipe="default", built=True)
    dismissal.track_fixed_term(config, f, 100, 130)  # 30 人が入った
    dismissal.track_fixed_term(config, f, 130, 140)  # 翌月 10 人
    dismissal.track_fixed_term(config, f, 140, 135)  # 5 人減った: 新しい月の人から
    assert f.economy[dismissal.FIXED_TERM] == [30, 5, 0]
    for _ in range(months - 2):
        dismissal.track_fixed_term(config, f, 135, 135)
    assert dismissal.fixed_term(f) == 5  # 初めの 30 人は月数を過ぎた
    dismissal.track_fixed_term(config, f, 135, 135)
    assert dismissal.fixed_term(f) == 0


def test_facilities_short_of_inputs_furlough_instead_of_dismissing(game):
    """原料が届かずに動けない施設は、操業目標に要る人を解雇せずに休ませる（休業手当は賃金の段で
    賃金の labor.furlough_pay）。原料が高い・買うお金がない（施設の都合）なら決まりの割合で減る。"""
    island, mill, full = _mill(game)
    config = game.config
    mill.economy["target"] = 1000  # 売れ行きはある
    mill.stock.clear()  # 原料がない: 手元の原料で動ける人は 0
    mill.status = "input:iron_ore"
    run(game, island, ["employment"])
    assert mill.workers == full - dismissal.natural_leavers(config, full)
    assert "dismissed" not in mill.economy
    assert mill.economy[dismissal.HELD] == 0  # 休んでいる人は抱えた人に数えない
    # 施設の都合（原料が高い）は休業でない: 決まりの割合で減る
    island, mill, full = _mill(game)
    mill.economy["target"] = 1000
    mill.stock.clear()
    mill.status = "input_price:iron_ore"
    run(game, island, ["employment"])
    rate = config.get("labor.dismissal")["normal"]["rate"] + config.get("labor.attrition")
    assert mill.workers == full - int(full * rate)
