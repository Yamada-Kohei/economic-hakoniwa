"""中央銀行の段（フェーズ2-2）: 政策金利（独立のとき）、銀行間の貸し借り、中央銀行の貸し出し。

1. 中央銀行が独立していれば、政策金利を式 policy_rate で決める（1ターンに central_bank.max_step ま
   で）。
2. 銀行間の貸し借り: 準備預金が最低限より多い銀行が貸し手、少ない銀行が借り手になり、次のターンの銀
   行の段で返すお金を、金利を値段にした板寄せで貸し借りする。値幅は「政策金利 ±
   central_bank.corridor」で、下端は準備預金の余りに付く金利（政策金利 −
   central_bank.reserve_spread）、上端は中央銀行から借りる金利。貸し手は余りを下端・政策金利・その間
   の3段に分けて出し、借り手は上端まで借りる。破綻した銀行には誰も貸さない。
3. 中央銀行の貸し出し: それでも準備預金が最低限に足りない銀行に、国債の時価と延滞のない融資を担保に
   （担保の値打ちの central_bank.collateral_ratio まで）上端の金利で貸す（次のターンに返す）。
   緊急の貸し出し（命令 emergency_loan。独立した中央銀行は、自己資本がプラスの銀行に自動で）は担保の
   ない分も貸す。担保も足りず最低限を満たせない銀行は資金繰りが破綻し（economy["liquidity_failure"]）、
   破綻の段で破綻する。準備預金がマイナスのままでは払えないので、マイナスの分はいつも貸す。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import banks
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis


def policy_rate(island: IslandState, ctx: StageContext) -> int:
    """独立した中央銀行の政策金利: r* + π + a (π − π*) − b (u − u*)（名前付きの式 policy_rate）。

    π は直近12ターンの物価上昇率、u は前のターンの失業率、u* はふつうの失業率。1ターンに
    central_bank.max_step まで動かし、central_bank.rate_range の中に収める。
    """
    settings = ctx.config.get("central_bank")
    pi = inflation(island.economy)
    u = island.economy.finance.get("unemployment", ctx.config.get("labor.natural_unemployment"))
    u_star = ctx.config.get("labor.natural_unemployment")
    target = (
        settings["natural_rate"]
        + pi
        + settings["inflation_weight"] * (pi - settings["inflation_target"])
        - settings["unemployment_weight"] * (u - u_star)
    )
    low, high = settings["rate_range"]
    target_basis = to_basis(max(low, min(high, target)))
    now = island.policy.policy_rate
    step = to_basis(settings["max_step"])
    return max(now - step, min(now + step, target_basis))


def corridor(island: IslandState, ctx: StageContext) -> tuple[int, int]:
    """銀行間の金利の下端（準備預金の余りの金利）と上端（中央銀行から借りる金利）。"""
    rate = island.policy.policy_rate
    floor = max(0, rate - to_basis(ctx.config.get("central_bank.reserve_spread")))
    return floor, rate + to_basis(ctx.config.get("central_bank.corridor"))


class CentralBank(IslandStage):
    name = "central_bank"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        policy = island.policy
        if policy.cb_independent:
            new = ctx.formula("policy_rate")(island, ctx)
            if new != policy.policy_rate:
                island.log.append(
                    LogEntry(
                        "policy_rate_moved",
                        {"before": policy.policy_rate * 100 / BASIS, "after": new * 100 / BASIS},
                    )
                )
            policy.policy_rate = new
        floor, ceiling = corridor(island, ctx)
        finance = island.economy.finance
        finance["cb_loan_rate"] = ceiling
        finance["interbank_rate"] = self._interbank(island, ctx, floor, ceiling)
        self._lend(island, ctx)
        island.stats["interbank_rate"] = finance["interbank_rate"] * 100 / BASIS
        island.stats["cb_lending"] = sum(
            banks.cb_borrowed(island, b) for b in banks.bank_list(island)
        )

    @staticmethod
    def _interbank(island: IslandState, ctx: StageContext, floor: int, ceiling: int) -> int:
        config = ctx.config
        rate = island.policy.policy_rate
        lend: list[tuple[Order, str]] = []
        borrow: list[tuple[Order, str]] = []
        middle = (rate + ceiling) // 2
        for bank in banks.active_banks(island):
            gap = banks.excess_reserves(island, config, bank)
            if gap > 0:
                for i, level in enumerate((floor, rate, middle)):
                    amount = gap // 3 + (gap % 3 if i == 2 else 0)
                    if amount > 0:
                        lend.append((Order(amount, level), bank))
            elif gap < 0:
                borrow.append((Order(-gap, ceiling), bank))
        if not lend or not borrow:
            return rate
        result = auction.clear(
            [o for o, _ in borrow], [o for o, _ in lend], rate, bounds=(floor, ceiling)
        )
        if result.volume <= 0:
            return rate
        for b, s, qty in auction.match(result.bought, result.sold):
            borrower, lender = borrow[b][1], lend[s][1]
            if borrower == lender:
                continue
            ledger.create(island, lender, ledger.INTERBANK, borrower, qty, "interbank")
            ledger.pay(island, lender, borrower, qty, "interbank")
        return result.price

    @staticmethod
    def _lend(island: IslandState, ctx: StageContext) -> None:
        config = ctx.config
        ratio = to_basis(config.get("central_bank.collateral_ratio"))
        emergency: dict[str, Any] = island.economy.finance.get("emergency", {})
        for bank in banks.bank_list(island):
            f = banks.facility_of(island, bank)
            if f is None:
                continue
            need = -banks.excess_reserves(island, config, bank)
            if need <= 0:
                continue
            performing = sum(
                banks.outstanding(island, x)
                for x in banks.loans_by_lender(island, bank)
                if x.arrears == 0
            )
            collateral = part(banks.bond_value(island, config, bank, ctx.turn) + performing, ratio)
            amount = min(need, max(0, collateral - banks.cb_borrowed(island, bank)))
            if amount < need:
                if island.policy.cb_independent:
                    if banks.net_worth(island, config, bank, ctx.turn) > 0:
                        amount = need
                elif emergency.get(bank):
                    amount = min(need, amount + int(emergency[bank]))
            reserves = banks.reserves_of(island, bank)
            if amount < need:
                f.economy["liquidity_failure"] = ctx.turn
                amount = max(amount, -reserves)  # マイナスの準備預金のままでは払えない
            if amount > 0:
                ledger.create(island, ledger.CB, ledger.CB_LOAN, bank, amount, "cb_loan")
                ledger.pay(island, ledger.CB, bank, amount, "cb_loan")


# --- 命令 -------------------------------------------------------------------------


def check_monetary_policy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")
    if "policy_rate" not in payload and "bond_trade" not in payload:
        raise OrderFailed("reason_invalid_payload")


def set_monetary_policy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """政策金利（%）と、中央銀行が毎ターン国債を買う額（マイナスは売る）を決める（独立していないとき）。"""
    policy = island.policy
    if "policy_rate" in payload:
        low, high = ctx.config.get("central_bank.rate_range")
        rate = max(low, min(high, payload["policy_rate"] / 100))
        policy.policy_rate = to_basis(rate)
    if "bond_trade" in payload:
        policy.cb_bond_trade = int(payload["bond_trade"])
    island.log.append(
        LogEntry(
            "monetary_policy_set",
            {"policy_rate": policy.policy_rate * 100 / BASIS, "bond_trade": policy.cb_bond_trade},
        )
    )


def check_independence(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if bool(payload["independent"]) == island.policy.cb_independent:
        raise OrderFailed("reason_already_done")
    if payload["independent"] and planning.is_planned(island, defs):
        # 計画経済の国立銀行は政府の一部で、お金を国の計画どおりに出す窓口（ソ連のゴスバンク、
        # 中国の人民銀行）。独立させる例は現実にほぼないので選べない（回5 のカード「両方」）
        raise OrderFailed("reason_cb_independence_planned")


def set_independence(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """中央銀行を独立させる・戻す。切り替えた後 central_bank.switch_lock_turns ターンは戻せない。

    独立した中央銀行は国債を引き受けないので、売れ残りの扱いが「中央銀行が引き受ける」なら
    「支出を減らす」に変え、独立をやめたら元に戻す（その間に命令で変えていなければ）。どちらも
    ターンログで知らせる（知らせずに替えていたので、入札の売れ残りで支出が減り続け、債務不履行に
    なるまで理由が見えなかった）。
    """
    finance = island.economy.finance
    lock = ctx.config.get("central_bank.switch_lock_turns")
    switched = finance.get("cb_switched")
    if switched is not None and ctx.turn - switched < lock:
        raise OrderFailed("reason_cb_switch_locked", turns=lock - (ctx.turn - switched))
    switch_independence(island, ctx, bool(payload["independent"]))


def end_for_planned(island: IslandState, ctx: StageContext) -> None:
    """計画経済の国の中央銀行が独立していれば、政府の一部に戻す（計画経済に移ったときと、独立を
    選べなかった前の版の世界。更新の段で呼ぶ）。プレイヤーの切り替えではないので、切り替えの後の
    戻せないターン数は見ず、数え始めもしない。"""
    if island.policy.cb_independent and planning.is_planned(island, ctx.defs):
        switched = island.economy.finance.get("cb_switched")
        switch_independence(island, ctx, False, kind="cb_dependent_planned")
        if switched is None:
            island.economy.finance.pop("cb_switched", None)
        else:
            island.economy.finance["cb_switched"] = switched


def switch_independence(
    island: IslandState, ctx: StageContext, independent: bool, kind: str = ""
) -> None:
    """中央銀行の独立を切り替え、それに伴う政策を合わせる（命令 set_independence の中身）。kind は
    知らせるログの種類（既定は cb_independent か cb_dependent）。"""
    finance = island.economy.finance
    policy = island.policy
    policy.cb_independent = independent
    finance["cb_switched"] = ctx.turn
    kind = kind or ("cb_independent" if policy.cb_independent else "cb_dependent")
    island.log.append(LogEntry(kind, {}))
    if policy.cb_independent:
        policy.cb_bond_trade = 0
        finance.pop("emergency", None)
        if policy.bond_unsold == "underwrite":
            policy.bond_unsold = "cut"
            finance["unsold_before"] = "underwrite"
            island.log.append(LogEntry("cb_independent_unsold", {}))
        # 為替の介入・固定相場・中央銀行の外国の国債の売り買いは、政府が決めるものなのでやめる
        # （フェーズ3-3。持っている外貨と外国の国債はそのまま）
        if policy.fx_intervention or policy.fx_peg or policy.foreign_bonds.get("cb"):
            policy.fx_intervention.clear()
            policy.fx_peg.clear()
            policy.foreign_bonds.pop("cb", None)
            island.log.append(LogEntry("cb_independent_fx", {}))
    elif finance.pop("unsold_before", None) and policy.bond_unsold == "cut":
        # 独立のときに「支出を減らす」に変えた売れ残りの扱いを、独立をやめたら元に戻す
        policy.bond_unsold = "underwrite"
        island.log.append(LogEntry("cb_dependent_unsold", {}))


def check_emergency_loan(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("category") != "finance":
        raise OrderFailed("reason_not_bank")


def emergency_loan(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """銀行への緊急の貸し出し: 担保の足りない分も、決めた額まで毎ターン貸す（0 で取り消す）。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    bank = ledger.bank_entity(f.id)
    table = island.economy.finance.setdefault("emergency", {})
    if payload["amount"] > 0:
        table[bank] = int(payload["amount"])
    else:
        table.pop(bank, None)
    island.log.append(
        LogEntry("emergency_loan_set", {"x": f.x, "y": f.y, "amount": int(payload["amount"])})
    )


def register(registry: Registry) -> None:
    registry.add_stage(CentralBank())
    registry.add_formula("policy_rate", policy_rate)
    registry.add_order(
        OrderHandler(
            "set_monetary_policy", execute=set_monetary_policy, check=check_monetary_policy
        )
    )
    registry.add_order(
        OrderHandler(
            "set_central_bank_independence", execute=set_independence, check=check_independence
        )
    )
    registry.add_order(
        OrderHandler("emergency_loan", execute=emergency_loan, check=check_emergency_loan)
    )
