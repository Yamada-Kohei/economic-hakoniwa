"""施設の建設と取り壊し（命令）と、工事の進み（段）。

建設の命令は工事中の施設を置くだけです。資材は市場の段で国の倉庫から先に使い、足りない分を
市場で買います。人手は雇用の段で通勤圏から島の平均賃金で雇います。資材と賃金は国庫が払い
（国の工事）、そろった分だけこの段で工事が進みます。払った額が施設の設備の額になり、
完成すると減価償却が始まります。独立採算の施設には、完成のときに政府が運転資金を出資します。

民営工場の参入（フェーズ2-2、参入と撤退の段）で建つ工事は、施設が自分の預金（出資と建設資金の
融資と、参入のときに先に受け取った設備投資補助）から払います。完成すると、建設資金の返済が据え置きの
後から始まり、設備投資補助をかかった設備の額で計算し直して差を受け取ります。命令で建てた銀行は、完成すると国営銀行として開業し、政府が資本金
（島の預金の banking.entry_capital）を出資します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, ledger, monuments, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    CONSTRUCTION,
    SUBSIDIES,
    Payer,
    arrears,
    banks,
    books,
    default_accounting,
    facility_entity,
    freight,
    funds,
    is_bank,
    is_private_site,
    is_self_supporting,
    owner_entity,
    retail,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.start import fund_facility, init_facility
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import part, to_basis


def check_build(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    kind = payload["facility"]
    if kind in defs.facilities and defs.facilities[kind].get("buildable") is False:
        raise OrderFailed("reason_not_buildable", facility=kind)
    problem = fac.placement_problem(
        defs, island, kind, payload["x"], payload["y"], payload.get("recipe")
    )
    if problem is not None:
        raise OrderFailed(problem[0], **problem[1])
    if payload.get("note") and not monuments.is_monument(defs, kind):
        raise OrderFailed("reason_note_not_monument", facility=kind)
    if monuments.is_monument(defs, kind):
        problem = monuments.problem(defs, island, kind)
        if problem is not None:
            raise OrderFailed(problem[0], **problem[1])
        note = monuments.inscription_note(payload.get("note", ""))
        lines = note.split("\n") if note else []
        if len(lines) > monuments.NOTE_LINES or any(len(x) > monuments.NOTE_CHARS for x in lines):
            raise OrderFailed(
                "reason_monument_note", lines=monuments.NOTE_LINES, chars=monuments.NOTE_CHARS
            )


def build(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    kind, x, y = payload["facility"], payload["x"], payload["y"]
    # 町の中の店（回7）は、マスを取らない負の位置に置き、町のマスを ext["site"] に持つ
    shop = ctx.defs.facilities[kind].get("site") == "town"
    at = retail.tileless_position(island) if shop else (x, y)
    if monuments.is_monument(ctx.defs, kind):
        # 条件は命令を受けたときと同じ（しきい値は設定から）。満たさなくなっていたら建てない
        problem = monuments.problem(ctx.defs, island, kind, dict(ctx.config.get("monuments")))
        if problem is not None:
            raise OrderFailed(problem[0], **problem[1])
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=at[0],
        y=at[1],
        recipe=payload.get("recipe") or fac.default_recipe(ctx.defs, kind) or "default",
        owner=OWNER_STATE,  # 命令で建てるのは国営（民営の工場が自分で建つのはフェーズ2-2から）
        accounting=default_accounting(ctx.defs, island, kind, OWNER_STATE),
        name=payload.get("name", "").strip(),
        economy={"capital": 0},
    )
    # 体制（権威主義の公共事業）と計画経済の重点産業で工期を変える（フェーズ3-4）
    factor = planning.build_factor(island, ctx.defs, kind, f.recipe)
    if factor != 1.0:
        turns = ctx.defs.facilities[kind]["construction"]["turns"]
        f.ext["build_total"] = max(fac.FULL, round(turns * factor * fac.FULL))
    if shop:
        f.ext[fac.SITE] = [x, y]
    if monuments.is_monument(ctx.defs, kind):
        monuments.stamp(ctx.defs, island, f, ctx.turn, payload.get("note", ""))
    assign_serial(island, f)
    island.facilities.append(f)
    island.log.append(LogEntry("construction_started", {"facility": kind, "x": x, "y": y}))


def check_demolish(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    x, y = payload["x"], payload["y"]
    if not island.in_bounds(x, y):
        raise OrderFailed("reason_out_of_bounds")
    if island.capital == (x, y):
        raise OrderFailed("reason_capital_building")
    f = island.facility_at(x, y)
    if f is None and not infra.has_any(island.tile(x, y)) and not infra.queued_at(island, x, y):
        raise OrderFailed("reason_nothing_to_demolish")
    if f is not None and defs.facilities[f.kind].get("category") == "finance":
        raise OrderFailed("reason_cannot_demolish", facility=f.kind)
    if f is not None and (f.owner == OWNER_PRIVATE or "foreclosed" in f.economy):
        raise OrderFailed("reason_private_demolish", facility=f.kind)


def demolish(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """施設を取り壊す。施設がなければ、マスのインフラ（送電網など）と並んでいる工事をすべて外す。

    施設の在庫は国の倉庫に戻し、独立採算の施設の預金は出資の払い戻しとして持ち主に返す。
    """
    x, y = payload["x"], payload["y"]
    f = island.facility_at(x, y)
    if f is None:
        infra.clear(island.tile(x, y))
        infra.unqueue(island, x, y)
        freight.forget(island)
        island.log.append(LogEntry("infrastructure_removed", {"x": x, "y": y}))
        return
    sell_leftover_stock(island, f)
    close_accounts(island, f)
    stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
    island.facilities.remove(f)
    island.log.append(LogEntry("facility_demolished", {"facility": f.kind, "x": x, "y": y}))


def close_accounts(island: IslandState, f: Facility) -> None:
    """独立採算の施設と民営の工事の預金を、未払い賃金 → 未払いの税 → 借入の順に払い（返しきれない
    借入は貸し倒れ。回3 の倒産と撤退の後の分け方）、残りの預金を持ち主に返して、出資を消す。
    払いきれなかった未払い賃金は、国営の施設なら政府が引き継ぎ、民営なら消える。"""
    if not island.has_economy or not (is_self_supporting(f) or is_private_site(f)):
        return
    entity = facility_entity(f.id)
    owner = owner_entity(f)
    arrears.settle(island, Payer(entity))
    for loan in sorted(banks.loans_by_borrower(island, entity), key=lambda x: x.id):
        banks.repay(
            island, loan, min(banks.outstanding(island, loan), ledger.money(island, entity))
        )
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    left = arrears.write_off(island, entity)
    if owner == ledger.GOV:
        for holder, value in sorted(left[arrears.WAGES].items()):
            arrears.add_wages(island, ledger.GOV, holder, value)
    ledger.pay(island, entity, owner, ledger.money(island, entity), "equity_return")
    for name, value in sorted(ledger.holdings(island, owner, ledger.EQUITY).items()):
        if name.endswith(f"/{entity}"):
            ledger.cancel(island, owner, ledger.EQUITY, entity, value, "equity_return")


def sell_leftover_stock(island: IslandState, f: Facility) -> None:
    """取り除く独立採算の施設と民営の工事の在庫を、国が市場の値段で引き取る（代金は施設に入り、
    close_accounts で未払い賃金から順に分ける。回3。Codex E25）。在庫は呼ぶ側が国の倉庫に移す。"""
    if island.has_economy and (is_self_supporting(f) or is_private_site(f)):
        arrears.sell_stock_to_state(island, facility_entity(f.id), f.stock)


# 資材があるのに人手が0のまま、このターン数続いた工事をログで知らせる（その後は進むまで出さない）
NO_WORKERS_TURNS = 2
# 資材が足りずに進まないまま、このターン数続いた工事をログで知らせる（同じく1度だけ）
NO_MATERIALS_TURNS = 2


def _where(f: Facility) -> dict[str, int]:
    """ログに書く施設の場所。町の中の店は町のマス（施設の位置は負で、本当のマスは ext にある）。"""
    x, y = fac.position(f)
    return {"x": x, "y": y}


class Construction(IslandStage):
    """工事中の施設とインフラの公共事業を、手元の資材と雇えた人手の分だけ進める。"""

    name = "construction"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs = ctx.defs
        for f in sorted(island.facilities, key=lambda f: f.id):
            if f.built:
                continue
            cdef = defs.facilities[f.kind]["construction"]
            materials = fac.material_ratio(defs, f)
            self._watch_workers(island, f, stalled=materials > 0 and cdef.get("labor", 0) > 0)
            self._watch_materials(island, f, defs, materials)
            rate = min(fac.ratio(f.workers, cdef.get("labor", 0)), materials)
            if rate <= 0:
                # 進まなかったターン数（わけを問わない）。民営の工事は firms.abandon_turns でやめる
                f.ext["stalled_turns"] = f.ext.get("stalled_turns", 0) + 1
                continue
            f.ext.pop("stalled_turns", None)
            total = fac.total_progress(defs, f)
            before = f.progress
            f.progress = min(before + rate, total)
            # 進んだ分だけ資材を使う。引き算で決めるので、工事全体でちょうど定義の量になる
            for good, qty in sorted(cdef.get("materials", {}).items()):
                stock.take(f.stock, good, qty * f.progress // total - qty * before // total)
            if f.progress >= total:
                self._complete(island, f, ctx)
        infrastructure.progress(island, ctx)

    @staticmethod
    def _watch_workers(island: IslandState, f: Facility, stalled: bool) -> None:
        """資材があるのに1人も雇えない工事を数え、続いたらログで知らせる。雇用の段で公共事業費が
        足りずに作業員を雇いきれなかった工事（ext["short_wages"]）は、そのわけで知らせる。"""
        if not stalled or f.workers > 0:
            f.ext.pop("no_workers_turns", None)
            return
        turns = f.ext.get("no_workers_turns", 0) + 1
        f.ext["no_workers_turns"] = turns
        if turns == NO_WORKERS_TURNS:
            args = {"facility": f.kind, **_where(f)}
            why = "wages" if f.ext.get("short_wages") else "workers"
            if why == "wages" and is_private_site(f):  # 足りないのは会社のお金
                why += "_private"
            island.log.append(LogEntry(f"construction_no_{why}", args))

    @staticmethod
    def _watch_materials(island: IslandState, f: Facility, defs: Defs, materials: int) -> None:
        """足りない資材を ext["waiting"] に書き（施設の状態に出す）、資材がなくて進まない工事が
        続いたらログで知らせる。前のターンの市場で、お金が足りずに注文しきれなかった工事
        （ext["short_funds"]）は、そのわけで知らせる。"""
        if materials >= fac.FULL:
            f.ext.pop("waiting", None)
            f.ext.pop("no_materials_turns", None)
            return
        f.ext["waiting"] = sorted(
            g
            for g, q in fac.step_materials(defs, f).items()
            if q > 0 and stock.total(f.stock, g) < q
        )
        if materials > 0:
            f.ext.pop("no_materials_turns", None)
            return
        turns = f.ext.get("no_materials_turns", 0) + 1
        f.ext["no_materials_turns"] = turns
        if turns == NO_MATERIALS_TURNS:
            args = {"facility": f.kind, **_where(f), "goods": f.ext["waiting"]}
            why = "funds" if f.ext.get("short_funds") else "materials"
            if why == "funds" and is_private_site(f):
                why += "_private"
            island.log.append(LogEntry(f"construction_no_{why}", args))
            if why == "funds" and isinstance(f.ext.get("short_funds"), str):
                island.log.append(LogEntry(f"funds_limit_{f.ext['short_funds']}", {}))

    @staticmethod
    def _complete(island: IslandState, f: Facility, ctx: StageContext) -> None:
        f.built = True
        f.workers = 0
        f.status = fac.STATUS_STARTING
        for key in ("waiting", "short_funds", "short_wages", "build_total"):
            f.ext.pop(key, None)
        for key in ("no_materials_turns", "no_workers_turns", "stalled_turns"):
            f.ext.pop(key, None)
        if f.ext.pop("repair", False):
            # 暴動で壊れた施設を直し終えた（フェーズ3-4）。在庫と帳簿と設備の額はそのまま動き出す
            island.log.append(LogEntry("facility_repaired", {"facility": f.kind, **_where(f)}))
            return
        stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
        f.ext[fac.FINISHED] = ctx.turn  # 民営の作り方の見直しが、売り出した後の値段を待つ（回6）
        island.log.append(LogEntry("construction_done", {"facility": f.kind, **_where(f)}))
        if not island.has_economy:
            return
        freight.forget(island)  # 取引の相手が変わるので、運賃の率を計算し直す
        capital = f.economy.get("capital", 0)
        init_facility(
            island,
            ctx.defs,
            ctx.config,
            f,
            dict(island.economy.prices),
            island.economy.average_wage,
        )
        f.economy["capital"] = capital
        if is_bank(ctx.config, f):
            _open_state_bank(island, ctx, f)
        elif f.ext.pop("private_entry", False):
            _start_private(island, ctx, f)
        elif is_self_supporting(f):
            fund_facility(island, ctx.defs, ctx.config, f, Payer(ledger.GOV, CONSTRUCTION))


def _start_private(island: IslandState, ctx: StageContext, f: Facility) -> None:
    """民営工場の完成: 建設資金の元金を据え置きの後から返し始める。

    設備投資補助は参入のときに見込みの額を先に払ってある（firms の pay_capital_subsidy）。完成の
    ときに、かかった設備の額で計算し直した額との差を払う（多く払っていても返させない）。
    """
    entity = facility_entity(f.id)
    f.economy["entered"] = ctx.turn
    for loan in banks.loans_by_borrower(island, entity):
        if loan.purpose == banks.CONSTRUCTION:
            loan.repay_from = ctx.turn + 1 + ctx.config.get("loans.grace_turns")
    rate = f.ext.pop("subsidy_rate", 0)
    paid = f.ext.pop("subsidy_paid", 0)
    payer = Payer(ledger.GOV, SUBSIDIES)
    due = part(f.economy.get("capital", 0), rate) - paid
    amount = min(due, funds.available(island, payer))
    if amount > 0:
        funds.pay(island, payer, entity, amount, "subsidy")
        books.add(island, f.id, books.SUBSIDY, amount)


def _open_state_bank(island: IslandState, ctx: StageContext, f: Facility) -> None:
    """命令で建てた銀行の完成: 国営銀行として開業し、政府が資本金を出資する。"""
    from hakoniwa_core.economy.start import invest
    from hakoniwa_core.modules.firms import open_bank

    f.owner = OWNER_STATE
    open_bank(island, ctx, (f.x, f.y), OWNER_STATE)
    total = sum(banks.deposits_of(island, b) for b in banks.bank_list(island))
    want = part(total, to_basis(ctx.config.get("banking.entry_capital")))
    amount = min(want, funds.available(island, Payer(ledger.GOV)))
    invest(island, ledger.GOV, ledger.bank_entity(f.id), amount)
    island.log.append(LogEntry("bank_opened_state", {"x": f.x, "y": f.y, "capital": amount}))


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("build", execute=build, check=check_build))
    registry.add_order(OrderHandler("demolish", execute=demolish, check=check_demolish))
    registry.add_stage(Construction())
