"""参入と撤退の段（フェーズ2-2）: 民営工場と民間銀行の参入、民営工場の撤退、差し押さえた施設の売却。
命令: 工業団地とオフィス街の指定（set_zone）、国有化と民営化、補助金。

民営工場の参入（市場経済と混合経済の島。計画経済の島では建たない）:
- 建てられるのは、民間が建てられる施設（facilities.yaml の private が false でなく、行政・銀行・運送
  業・町でない）のうち、経済体制で国営と決まっていない施設（混合経済の基幹産業は建たない）。
- 見込みの年の利益率 R（式 entry_return）に売れる見込みの割合を掛けた値が「借りる金利 +
  firms.entry_margin」を上回り、その財が直近 firms.price_turns ターンのうち firms.shortage_turns
  ターン以上品切れ（買いたい量に売りに出た量が、新しい工場の全力の産出の firms.entry_gap 倍以上足り
  ない）か、値段が原価の firms.cost_ratio 倍を超えているとき。売れる見込みの割合は、値段が合図なら直
  近の買いたい量の平均 ÷（今ある施設と新しい工場の全力の産出の合計）、品切れが合図なら直近の足りない
  量の平均 ÷ 新しい工場の全力の産出（どちらも1まで。両方なら大きい方）。1つの島で1ターンに
  firms.entries_per_turn 件まで、見込みの大きい順。原料（投入財）のどれかが島で作られていないか、工
  事の資材のどれかが島で作られておらず直近 firms.price_turns ターンに売りに出てもいない（国の倉庫の
  物は政府が売りに出さなければ買えない）か、原料が品切れ続き（数え方は産出の品切れと同じで、量は新し
  い工場の使う量で見る）の作り方では建たない。原料に新しい工場の使う量の firms.entry_gap 倍の空き
  （今ある施設の全力の産出 − 今ある施設の全力の使う量か直近の買いたい量の平均の大きい方）がなく、そ
  の原料を作る施設も島にこれ以上建てられない（鉱山の数の上限など）ときも建たない。
- 同じ財を作る民営工場が建設中か、動き出して firms.price_turns ターンたっていなければ、その財では参
  入しない（新しい工場が売り出した後の値段と品切れを見てから決める）。
- 場所: 地形の合う空いた工業団地のマスのうち、通勤圏の失業者が要る人数の半分以上いて、運賃の率が最も
  低いマス。
- 資金: 建設費（今の資材の値段と平均賃金。設備投資補助を引く）の1割増しと、動き出すときの支払い（全
  力で firms.operating_turns ターン分）の firms.equity_share を資産を持つ層の預金から出資し、残りを
  銀行の建設資金で借りる（元金は工事が済んでから loans.grace_turns ターン据え置き、その後
  loans.construction_turns ターンで返す）。金利の低い銀行から順に、自己資本比率の規制と大口融資の規
  制（1つの借り手への残高は自己資本 × banking.large_exposure まで）の残りの分ずつ借り、借りきれない
  分は資産を持つ層が多く出資する。資産を持つ層の預金が足りないか、審査に通らなければ建たない。工事の
  資材と賃金は施設が自分の預金から払う。
- 差し押さえ中の同じ施設があれば、その売り値を建設費として新しく建てる案と比べ、良い方を選ぶ（買い取
  るときも、動き出すときの支払いを合わせて調達し、元金を据え置く）。

撤退: 民営の独立採算の施設は、動かない（稼働率0）まま firms.exit_idle_turns ターン続くか、変動費の利
益が firms.exit_loss_turns ターン続けてマイナス（全力の売上の firms.exit_loss_margin を超える損）な
ら閉鎖に入る（economy["closing"]）。人を雇わず仕入れず、在庫を売る。在庫がなくなるか
firms.foreclosure_turns ターンたったら、借入を預金で返し、残りを出資者に返してマスを空ける（工業団地
の指定は残る）。返しきれなければ倒産と同じ扱い（差し押さえ）にする。民営の工事が資材を買えずに
firms.abandon_turns ターン進まなければ、工事をやめる（建設資金は預金で返し、返しきれない分は貸し倒
れ、買ってあった資材は国の倉庫へ）。

差し押さえた施設は firms.foreclosure_turns ターン売れなければ取り壊す（在庫は国の倉庫へ、預金は差し
押さえた銀行の回収）。

商社（フェーズ3-1）: 民営の商社が入れる島（計画経済でない島）では、島の商社の扱った重さが直近
trade.entry_turns ターンの平均で上限の trade.entry_threshold を超え、空いたオフィス街のマスがあり、
資産を持つ層が出資できれば、商社を1つ開く（hakoniwa_core/trade/setup.py の entry）。商社の撤退
（在庫・外貨・運ぶ途中の荷の始末が要る）は 3-1 では入れていない。

作り方の見直し（フェーズ3-2）: 作り方が複数ある民営の施設は、firms.recipe_turns ターンごとに変動費
の利益が一番大きい作り方を選び、今より firms.recipe_margin の割合以上大きければ替える
（_review_recipes）。鉱山の掘る物（鉱業資源の違う作り方）は替えない（facilities.switch_problem）。

民間銀行の参入: 参入の政策（policy.bank_entry。決めていなければ経済体制の既定）が「自由」なら、参入
した後の銀行1つあたりの島の預金 D が banking.entry_deposits を超え、預金 D を集めたときの利益の見込
み（今ある銀行の「賃金を払う前の利益 ÷ 預金」× D − 職員の賃金）の年率が、資本金に対して「政策金利 +
banking.entry_roe_margin」を超え、政策金利が今の銀行の預金金利より高く（開業した銀行が預金金利を上げ
て預金を集められる）、銀行の数が banking.max_banks 未満で、空いたオフィス街のマスがあれ
ば、島の預金の banking.entry_capital を資産を持つ層が出資して開業する。「許可制」なら免許の命令
（grant_bank_license）を出したときだけ開業する。開業した銀行は、預金が D に届くまで預金金利を政策金
利まで上げて預金を集める（銀行の段）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics, stock, trade
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    OWNERS,
    STATE_FACILITIES,
    SUBSIDIES,
    Payer,
    arrears,
    banks,
    default_accounting,
    facility_entity,
    funds,
    household,
    is_bank,
    is_private_site,
    is_public_service,
    is_self_supporting,
    is_stopped,
    is_transport,
    productivity,
    services,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.prices import construction_cost, unit_cost, variable_profit
from hakoniwa_core.economy.start import fund_facility, init_facility, invest, operating_cost
from hakoniwa_core.geo import within
from hakoniwa_core.modules.banking import entry_policy, is_planned
from hakoniwa_core.modules.failures import bankrupt, close_foreclosure, distribute
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    ZONE_NONE,
    ZONE_OFFICE,
    Facility,
    IslandState,
    LogEntry,
)
from hakoniwa_core.trade import merchant
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, money_of, part, round_money, to_basis

NOT_PRIVATE = ("admin", "finance", "housing", "security", "transport")  # 民間が建てない施設の分類


def private_allowed(defs: Defs, island: IslandState, kind: str) -> bool:
    """民間が建てられる・持てる施設か（regimes.yaml の private_entry。行政・銀行などは除く）。"""
    fdef = defs.facilities[kind]
    if fdef.get("private") is False or fdef.get("category") in NOT_PRIVATE:
        return False
    if fdef.get("buildable") is False or fdef.get("public_service"):
        return False
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    rule = regime.get("private_entry", "none")
    if rule == "non_core":
        return kind not in regime.get("state_owned", ())
    return rule == "any"


def average_prices(island: IslandState, turns: int) -> dict[str, int]:
    """直近 turns ターンの値段の平均（記録がなければ今の値段）。"""
    out = dict(island.economy.prices)
    for good, history in island.economy.price_history.items():
        tail = history[-turns:]
        if tail:
            out[good] = max(1, sum(tail) // len(tail))
    return out


def entry_return(
    island: IslandState,
    ctx: StageContext,
    kind: str,
    recipe: str,
    prices: dict[str, int],
    freight: int,
    capital: int,
    pos: tuple[int, int] | None = None,
) -> float:
    """見込みの年の利益率 R = 12 × (変動費の利益 + 生産補助) × (1 − 法人税率) ÷ 建設費
    （名前付きの式 entry_return の既定）。pos は建てるマス（生産性の係数に使う）。"""
    if capital <= 0:
        return 0.0
    probe = productivity.probe(island, ctx.defs, ctx.config, kind, recipe, pos)
    wage = island.economy.average_wage
    profit = variable_profit(ctx.defs, ctx.config, probe, prices, wage, freight)
    outputs = ctx.defs.facilities[kind]["recipes"][recipe]["outputs"]
    sales = sum(prices.get(g, 0) * q // 1000 for g, q in outputs.items())
    subsidy = sum(
        part(sales, s["rate"])
        for s in island.policy.subsidies
        if s["kind"] == "production" and s.get("good") in outputs
    )
    tax = island.policy.corporate_tax / BASIS
    return YEAR * (profit + subsidy) * (1 - tax) / capital


def capital_subsidy(island: IslandState, kind: str, recipe: dict[str, Any]) -> int:
    """設備投資補助の率（1万分の1。品目の合計）。"""
    return sum(
        s["rate"]
        for s in island.policy.subsidies
        if s["kind"] == "capital" and s.get("good") in recipe["outputs"]
    )


class EntryExit(IslandStage):
    name = "entry_exit"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        self._record_shortages(island, ctx)
        self._review_recipes(island, ctx)
        self._exits(island, ctx)
        self._foreclosures(island, ctx)
        if not is_planned(ctx.defs, island):
            for _ in range(ctx.config.get("firms.entries_per_turn")):
                if not self._enter(island, ctx):
                    break
        self._bank_entry(island, ctx)
        trade_setup.entry(island, ctx.defs, ctx.config, is_planned(ctx.defs, island), ctx.turn)
        private = [
            f
            for f in island.facilities
            if f.owner == OWNER_PRIVATE and not is_bank(ctx.config, f) and fac.on_map(f)
        ]
        island.stats["firms.private"] = sum(1 for f in private if f.built)
        island.stats["firms.private_sites"] = sum(1 for f in private if not f.built)

    # --- 品切れの記録 -------------------------------------------------------------------

    @staticmethod
    def _record_shortages(island: IslandState, ctx: StageContext) -> None:
        """財ごとの、買いたい量に売りに出た量が足りなかった量と、買いたい量と、売りに出た量（直近
        firms.price_turns ターン）。"""
        keep = ctx.config.get("firms.price_turns")
        finance = island.economy.finance
        for key, amount in (
            ("shortages", lambda m: max(0, m.demand - m.supply)),
            ("demand", lambda m: m.demand),
            ("supply", lambda m: m.supply),
        ):
            history = finance.setdefault(key, {})
            seen = set()
            for m in island.markets:
                history[m.good] = [*history.get(m.good, []), amount(m)][-keep:]
                seen.add(m.good)
            for good in list(history):
                if good not in seen:
                    history[good] = [*history[good], 0][-keep:]

    # --- 撤退 -------------------------------------------------------------------------

    # --- 作り方の見直し（フェーズ3-2） ---------------------------------------------------

    def _review_recipes(self, island: IslandState, ctx: StageContext) -> None:
        """民営の施設は firms.recipe_turns ターンごとに、変動費の利益（直近の値段の平均で、全力
        で動いたとき）が一番大きい作り方を選び、今の作り方より firms.recipe_margin の割合以上
        大きければ替える（次の市場の段で替える。facilities.RECIPE_NEXT）。"""
        defs, config = ctx.defs, ctx.config
        settings = config.get("firms")
        margin = to_basis(settings["recipe_margin"])
        prices: dict[str, int] | None = None
        supplied: set[str] = set()
        rates: dict[int, int] = {}
        for f in sorted(island.facilities, key=lambda f: f.id):
            if (
                len(defs.facilities[f.kind]["recipes"]) < 2
                or f.owner != OWNER_PRIVATE
                or not f.built
                or not fac.on_map(f)
                or "closing" in f.economy
                or "foreclosed" in f.economy
                or fac.RECIPE_NEXT in f.ext
                or (ctx.turn + f.id) % settings["recipe_turns"]
            ):
                continue
            if prices is None:
                prices = average_prices(island, settings["price_turns"])
                supplied = self._supplied(island, ctx)
                rates = freight_rates.facility_rates(island, defs, config)
            busy = self._switched_into(island, ctx)
            profits = self._recipe_profits(
                island, ctx, f, prices, supplied, rates.get(f.id, 0), busy
            )
            now = profits.get(f.recipe, 0)
            choice = max(sorted(profits), key=lambda name: profits[name])
            if choice != f.recipe and profits[choice] - now > part(abs(now), margin):
                f.ext[fac.RECIPE_NEXT] = choice

    @staticmethod
    def _switched_into(island: IslandState, ctx: StageContext) -> set[str]:
        """作り方を替えて作り始めてから firms.price_turns ターンたたない財と、替えると決めて
        まだ替えていない作り方の財（命令と民営の見直しの両方）、畑を蒔き直している途中の作物。新しい民営工場の参入
        （_pipeline）と同じく、その施設が売り出した後の値段と売れ行きを見てから次を決める。"""
        defs = ctx.defs
        recent = ctx.turn - ctx.config.get("firms.price_turns")
        goods: set[str] = set()
        for f in island.facilities:
            if not f.built:
                continue
            pending = f.ext.get(fac.RECIPE_NEXT)
            if pending in defs.facilities[f.kind]["recipes"]:
                goods |= set(defs.facilities[f.kind]["recipes"][pending]["outputs"])
            if f.ext.get(fac.RECIPE_CHANGED, -(10**9)) > recent:
                goods |= set(fac.recipe_of(defs, f)["outputs"])
            if fac.FIELDS in f.economy or f.ext.get(fac.CROP_READY, -(10**9)) > recent:
                # 畑を蒔き直している途中か、蒔き直し終えたばかりの作物（回3）
                goods |= set(defs.facilities[f.kind]["recipes"][f.recipe]["outputs"])
        return goods

    @staticmethod
    def _recipe_profits(
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        prices: dict[str, int],
        supplied: set[str],
        rate: int,
        busy: set[str] | None = None,
    ) -> dict[str, int]:
        """施設 f が選べる作り方ごとの変動費の利益（今の作り方を含む）。選べるのは、島で使える
        作り方（鉱業資源と気候）のうち、原料がどれも島で手に入り、今より多く使う原料は直近に
        売りに出た量が買いたい量より増える分以上多く、産出物に買い手のいる（直近に買いたい量が
        あった）作り方（今と同じ原料を倍使う作り方に替えて、その原料が品切れになったため）。
        今作っていない産出物は、直近の買いたい量が売りに出た量を、新しい作り方の全力の産出の
        firms.entry_gap 倍以上上回っていなければ選ばない（綿の値段が高いターンに民営の農場が
        そろって綿に替え、綿が余って値段が初めの 0.1 倍まで下がった）。ほかの施設が作り方を
        替えて作り始めたばかりの財（busy。_switched_into）も選ばない（嗜好品の値段が高いうちに、
        民営の農場が1ターンずつずれて3つとも嗜好品に替え、島の穀物がなくなった。替えた農場の
        売り出しが直近の平均に効く前に、次の農場が替えていた）。"""
        defs, config = ctx.defs, ctx.config
        power = config.get("power.good")
        gap = config.get("firms.entry_gap")
        finance = island.economy.finance
        demand = finance.get("demand", {})
        now = fac.recipe_of(defs, f)["inputs"]
        made = fac.field_goods(defs, f)  # 畑を作り替えている途中の前の作物も、作っている物と見る
        wage = f.economy.get("wage", 0)
        out = {}
        for name, recipe in sorted(defs.facilities[f.kind]["recipes"].items()):
            if name != f.recipe and (
                fac.switch_problem(defs, island, f, name)
                or set(recipe["inputs"]) - supplied
                or not all(g == power or any(demand.get(g, [])) for g in recipe["outputs"])
                or any(
                    _spare(finance, g) < qty - now.get(g, 0)
                    for g, qty in recipe["inputs"].items()
                    if g != power and qty > now.get(g, 0)
                )
                or any(
                    -_spare(finance, g) < qty * gap
                    for g, qty in recipe["outputs"].items()
                    if g != power and g not in made
                )
                or any(g in (busy or ()) for g in recipe["outputs"] if g != power and g not in made)
            ):
                continue
            probe = productivity.probe(island, defs, config, f.kind, name, (f.x, f.y))
            out[name] = variable_profit(defs, config, probe, prices, wage, rate)
        return out

    def _exits(self, island: IslandState, ctx: StageContext) -> None:
        settings = ctx.config.get("firms")
        rates = freight_rates.facility_rates(island, ctx.defs, ctx.config)
        for f in sorted(island.facilities, key=lambda f: f.id):
            if (
                f.owner != OWNER_PRIVATE
                or f.accounting != ACCOUNTING_SELF
                or is_bank(ctx.config, f)
                or is_transport(ctx.config, f)
                or services.is_services(ctx.config, f)
                or trade.is_trade_facility(ctx.config, f)
                or "foreclosed" in f.economy
            ):
                continue
            if not f.built:
                if f.ext.get("stalled_turns", 0) >= settings["abandon_turns"]:
                    self._abandon(island, ctx, f)
                continue
            if "closing" in f.economy:
                self._finish_closing(island, ctx, f)
                continue
            idle = f.economy.get("idle_turns", 0) + 1 if f.operating_rate <= 0 else 0
            wage = f.economy.get("wage", 0)
            prices = island.economy.prices
            profit = variable_profit(ctx.defs, ctx.config, f, prices, wage, rates.get(f.id, 0))
            outputs = fac.recipe_of(ctx.defs, fac.steady(f))["outputs"]
            sales = sum(money_of(prices.get(g, 0), q) for g, q in outputs.items())
            # 値段が変動費ちょうどの売り手は、端数でマイナスになることがあるので少し見逃す
            margin = part(sales, to_basis(settings["exit_loss_margin"]))
            losing = f.economy.get("loss_streak", 0) + 1 if profit < -margin else 0
            f.economy["idle_turns"] = idle
            f.economy["loss_streak"] = losing
            if idle >= settings["exit_idle_turns"] or losing >= settings["exit_loss_turns"]:
                f.economy["closing"] = ctx.turn
                f.economy["target"] = 0
                island.log.append(
                    LogEntry("facility_closing", {"facility": f.kind, "x": f.x, "y": f.y})
                )

    @staticmethod
    def _abandon(island: IslandState, ctx: StageContext, f: Facility) -> None:
        """進まないまま止まった民営の工事をやめる（先に受け取った設備投資補助は、借入を返せる分を
        残して政府に返し、建設資金は預金で返し、返しきれない分は貸し倒れ。買ってあった資材は国の
        倉庫へ）。"""
        entity = facility_entity(f.id)
        spare = ledger.money(island, entity) - banks.debt_of(island, entity)
        refund = min(f.ext.pop("subsidy_paid", 0), max(0, spare))
        if refund > 0:
            ledger.pay(island, entity, ledger.GOV, refund, "subsidy_refund")
        remove_facility(island, ctx, f)
        island.log.append(
            LogEntry("private_site_abandoned", {"facility": f.kind, "x": f.x, "y": f.y})
        )
        stats = island.stats
        stats["firms.abandoned"] = stats.get("firms.abandoned", 0) + 1

    @staticmethod
    def _finish_closing(island: IslandState, ctx: StageContext, f: Facility) -> None:
        f.economy["target"] = 0
        held = sum(stock.totals(f.stock).values())
        waited = ctx.turn - f.economy["closing"]
        if held > 0 and waited < ctx.config.get("firms.foreclosure_turns"):
            return
        entity = facility_entity(f.id)
        for loan in sorted(banks.loans_by_borrower(island, entity), key=lambda x: x.id):
            amount = min(banks.outstanding(island, loan), ledger.money(island, entity))
            banks.repay(island, loan, amount)
        banks.drop_settled(island)
        if banks.debt_of(island, entity) > 0:
            f.economy.pop("closing", None)
            bankrupt(island, ctx, f)
            return
        remove_facility(island, ctx, f)
        island.log.append(LogEntry("facility_closed", {"facility": f.kind, "x": f.x, "y": f.y}))
        stats = island.stats
        stats["firms.closed"] = stats.get("firms.closed", 0) + 1

    @staticmethod
    def _foreclosures(island: IslandState, ctx: StageContext) -> None:
        turns = ctx.config.get("firms.foreclosure_turns")
        for f in sorted(island.facilities, key=lambda f: f.id):
            seized = f.economy.get("foreclosed")
            if seized and ctx.turn - seized["since"] >= turns:
                # 残った在庫は国が市場の値段で引き取り、その代金も同じ順で分ける（回3。E25）
                arrears.sell_stock_to_state(island, facility_entity(f.id), f.stock)
                close_foreclosure(island, f)
                stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
                fac.vacate(island, f)
                freight_rates.forget(island)
                island.log.append(
                    LogEntry("foreclosure_demolished", {"facility": f.kind, "x": f.x, "y": f.y})
                )

    # --- 民営工場の参入 ---------------------------------------------------------------

    def _enter(self, island: IslandState, ctx: StageContext) -> bool:
        """民営工場を1つ参入させる。建たなかったときは、いちばん見込みのあった作り方の建たないわけを
        economy.finance["entry_block"] に残し、わけが変わったらログで知らせる。"""
        blocks: list[tuple[float, str, str, dict[str, Any]]] = []  # (見込み, 施設, わけ, 引数)
        entered = self._try_enter(island, ctx, blocks)
        finance = island.economy.finance
        if entered or not blocks:
            finance.pop("entry_block", None)
            return entered
        _, kind, reason, args = max(blocks, key=lambda b: b[0])
        before = finance.get("entry_block") or {}
        finance["entry_block"] = {"facility": kind, "reason": reason, "args": args}
        if (before.get("facility"), before.get("reason")) != (kind, reason):
            island.log.append(
                LogEntry(
                    "private_entry_blocked",
                    {
                        "facility": kind,
                        "reason": f"entry_block_{reason}",
                        "reason_args": dict(args),
                    },
                )
            )
        return False

    def _try_enter(
        self,
        island: IslandState,
        ctx: StageContext,
        blocks: list[tuple[float, str, str, dict[str, Any]]],
    ) -> bool:
        defs, config = ctx.defs, ctx.config
        settings = config.get("firms")
        prices = average_prices(island, settings["price_turns"])
        wage = island.economy.average_wage
        rates = freight_rates.facility_rates(island, defs, config)
        typical = sum(rates.values()) // len(rates) if rates else 0
        shortages = island.economy.finance.get("shortages", {})
        demand = island.economy.finance.get("demand", {})
        capacity = self._capacity(island, ctx)
        uses = self._capacity(island, ctx, "inputs")
        busy = self._pipeline(island, ctx)
        supplied = self._supplied(island, ctx)
        on_sale = {g for g, h in island.economy.finance.get("supply", {}).items() if any(h)}
        expandable: dict[str, bool] = {}
        room = funds.available(island, Payer(ledger.GOV, SUBSIDIES))
        candidates = []
        for kind, fdef in sorted(defs.facilities.items()):
            if not private_allowed(defs, island, kind):
                continue
            for rname, recipe in sorted(fdef["recipes"].items()):
                if fac.recipe_problem(defs, island, kind, rname) is not None:
                    continue  # 鉱業資源がないか、気候で使えない（フェーズ3-2）
                if not recipe["outputs"] or busy & set(recipe["outputs"]):
                    continue
                cost = unit_cost(defs, config, kind, recipe, prices, wage)
                fill = self._fill(recipe, shortages, demand, capacity, prices, cost, settings)
                if fill <= 0:
                    continue  # 参入の合図（品切れか高い値段）がない
                missing = sorted(set(recipe.get("inputs", {})) - supplied)
                if missing:  # 原料が島で作られていない
                    blocks.append((0.0, kind, "inputs_missing", {"goods": missing}))
                    continue
                short = self._inputs_short(recipe, shortages, settings)
                if short:  # 原料が品切れ続きで、新しい工場は原料を買えない
                    blocks.append((0.0, kind, "inputs_short", {"good": short}))
                    continue
                capped = self._inputs_capped(
                    island, ctx, recipe, capacity, uses, demand, expandable
                )
                if capped:  # 原料に空きがなく、作る施設もこれ以上建てられない
                    blocks.append((0.0, kind, "inputs_capped", {"good": capped}))
                    continue
                full = round_money(construction_cost(defs, kind, prices, wage))
                rate = capital_subsidy(island, kind, recipe)
                subsidy = min(part(full, rate), room)  # 先に払える分だけを補助として見る
                capital = full - subsidy
                estimate = ctx.formula("entry_return")(
                    island, ctx, kind, rname, prices, typical, capital
                )
                candidates.append((estimate * fill, kind, rname, capital, fill, rate, subsidy))
        threshold = self._threshold(island, ctx)
        for estimate, kind, rname, capital, fill, rate, subsidy in sorted(candidates, reverse=True)[
            :3
        ]:
            if estimate <= threshold:
                if threshold == float("inf"):
                    blocks.append((estimate, kind, "no_bank", {}))
                else:
                    args = {"rate": estimate * 100, "need": threshold * 100}
                    blocks.append((estimate, kind, "unprofitable", args))
                break
            if self._buy_foreclosed(island, ctx, kind, rname, prices, threshold, estimate, fill):
                return True
            materials = defs.facilities[kind]["construction"].get("materials", {})
            unavailable = sorted(set(materials) - supplied - on_sale)
            if unavailable:  # 工事の資材が島で作られておらず、売りに出てもいない
                blocks.append((estimate, kind, "materials", {"goods": unavailable}))
                continue
            site = self._site(island, ctx, kind, rname)
            if isinstance(site, str):
                blocks.append((estimate, kind, site, {}))
                continue
            pos, freight = site
            r = ctx.formula("entry_return")(island, ctx, kind, rname, prices, freight, capital, pos)
            if r * fill <= threshold:
                args = {"rate": r * fill * 100, "need": threshold * 100}
                blocks.append((estimate, kind, "unprofitable_site", args))
                continue
            failure = self._build(
                island, ctx, kind, rname, pos, capital, prices, freight, (rate, subsidy)
            )
            if failure is None:
                return True
            blocks.append((estimate, kind, failure[0], failure[1]))
        return False

    @staticmethod
    def _fill(
        recipe: dict[str, Any],
        shortages: dict[str, list[int]],
        demand: dict[str, list[int]],
        capacity: dict[str, int],
        prices: dict[str, int],
        cost: int,
        settings: dict[str, Any],
    ) -> float:
        """新しい工場が売れると見込む割合（0 なら参入の合図がない）。

        値段が原価の firms.cost_ratio 倍を超える財は、直近の買いたい量の平均を、今ある施設と新しい
        工場の全力の産出の合計で割った割合（1まで）だけ売れると見る（買い手の少ない財や、売り買いが
        なくて値段が前のままの財で、売れない工場が建たないように）。品切れが合図の財は、直近の足りない
        量の平均を全力の産出で割った割合（1まで）だけ売れると見る（工事が重なったときだけの品不足で、
        大きな工場が建たないように）。
        """
        best = 0.0
        for g, qty in recipe["outputs"].items():
            wanted = demand.get(g, [])
            if prices.get(g, 0) > settings["cost_ratio"] * cost and wanted:
                share = sum(wanted) / len(wanted) / (capacity.get(g, 0) + qty)
                best = max(best, min(1.0, share))
            history = shortages.get(g, [])
            gap = settings["entry_gap"] * qty
            if sum(1 for short in history if short >= gap) >= settings["shortage_turns"]:
                best = max(best, min(1.0, sum(history) / len(history) / qty))
        return best

    @staticmethod
    def _capacity(island: IslandState, ctx: StageContext, side: str = "outputs") -> dict[str, int]:
        """財ごとの、動いている施設（閉鎖中と差し押さえ中を除く）の全力の産出（side が "inputs" なら
        使う量）の合計。"""
        total: dict[str, int] = {}
        for f in island.facilities:
            if not f.built or is_stopped(f):
                continue
            for g, qty in fac.recipe_of(ctx.defs, f).get(side, {}).items():
                total[g] = total.get(g, 0) + qty
        return total

    @staticmethod
    def _pipeline(island: IslandState, ctx: StageContext) -> set[str]:
        """新しい民営工場が建てている途中か、動き出して firms.price_turns ターンたたない財
        （その工場が売り出した後の値段と品切れを見てから、次の参入を決める）。"""
        recent = ctx.turn - ctx.config.get("firms.price_turns")
        goods: set[str] = set()
        for f in island.facilities:
            if is_private_site(f) or f.economy.get("entered", -(10**9)) > recent:
                goods |= set(ctx.defs.facilities[f.kind]["recipes"][f.recipe]["outputs"])
        return goods

    @staticmethod
    def _inputs_short(
        recipe: dict[str, Any], shortages: dict[str, list[int]], settings: dict[str, Any]
    ) -> str | None:
        """直近 firms.price_turns ターンのうち firms.shortage_turns ターン以上、新しい工場の全力の
        使う量の firms.entry_gap 倍以上足りなかった原料（なければ None）。"""
        for g, qty in sorted(recipe.get("inputs", {}).items()):
            gap = settings["entry_gap"] * qty
            short = sum(1 for amount in shortages.get(g, []) if amount >= gap)
            if short >= settings["shortage_turns"]:
                return g
        return None

    @staticmethod
    def _inputs_capped(
        island: IslandState,
        ctx: StageContext,
        recipe: dict[str, Any],
        capacity: dict[str, int],
        uses: dict[str, int],
        demand: dict[str, list[int]],
        expandable: dict[str, bool],
    ) -> str | None:
        """新しい工場の全力の使う量の firms.entry_gap 倍の空き（今ある施設の全力の産出 − 今ある施設
        の全力の使う量か直近の買いたい量の平均の大きい方）がなく、それを作る施設も島にこれ以上建てら
        れない原料（鉱山の数の上限など。expandable に財ごとの答えをためる。なければ None）。"""
        gap = ctx.config.get("firms.entry_gap")
        for g, qty in sorted(recipe.get("inputs", {}).items()):
            wanted = demand.get(g, [])
            used = max(uses.get(g, 0), sum(wanted) / len(wanted) if wanted else 0)
            spare = capacity.get(g, 0) - used
            if spare >= gap * qty:
                continue
            if g not in expandable:
                expandable[g] = EntryExit._expandable(island, ctx, g)
            if not expandable[g]:
                return g
        return None

    @staticmethod
    def _expandable(island: IslandState, ctx: StageContext, good: str) -> bool:
        """good を作る施設を、島のどこかに新しく建てられるか（数の上限・地下資源・地形で見る。
        工業団地の指定はプレイヤーが足せるので見ない）。"""
        defs = ctx.defs
        occupied = island.facilities_by_tile()
        for kind, fdef in sorted(defs.facilities.items()):
            limit = fdef.get("max_per_island")
            if limit is not None and sum(1 for f in island.facilities if f.kind == kind) >= limit:
                continue
            for rname, recipe in sorted(fdef["recipes"].items()):
                if good not in recipe.get("outputs", {}):
                    continue
                for tile in island.tiles:
                    if (tile.x, tile.y) in occupied:
                        continue
                    problem = fac.placement_problem(defs, island, kind, tile.x, tile.y, rname)
                    if problem is None or problem[0] == "reason_zone_required":
                        return True
        return False

    @staticmethod
    def _supplied(island: IslandState, ctx: StageContext) -> set[str]:
        """島で手に入る財: 動いている施設が作っている財（国の倉庫の物は、政府が売りに出さなければ
        市場に出ないので数えない）と、商社が輸入している財・海外の商人が売っている財（フェーズ3-1）。"""
        goods: set[str] = set()
        for f in island.facilities:
            if f.built and not is_stopped(f):
                goods |= set(ctx.defs.facilities[f.kind]["recipes"][f.recipe].get("outputs", {}))
        goods |= trade_traders.imported(island, ctx.config)
        goods |= {g for g, _, _ in merchant.offers(island, ctx.defs, ctx.config, ctx.turn)}
        return goods

    @staticmethod
    def _threshold(island: IslandState, ctx: StageContext) -> float:
        """参入に要る利益率: 借りる金利（建設費の7割を借りたとき）+ firms.entry_margin（体制の特色
        entry_margin があればそちら。市場経済。フェーズ3-4）。"""
        config = ctx.config
        active = banks.active_banks(island)
        if not active:
            return float("inf")
        margin = min(banks.margin_of(island, config, b) for b in active)
        debt_share = 1 - config.get("firms.equity_share")
        rate = island.policy.policy_rate / BASIS + margin / BASIS
        rate += config.get("banking.rho") * debt_share
        found = politics.trait(island, ctx.defs, "entry_margin")
        return rate + (config.get("firms.entry_margin") if found is None else found)

    def _site(
        self, island: IslandState, ctx: StageContext, kind: str, rname: str
    ) -> tuple[tuple[int, int], int] | str:
        """建てる場所: 空いた工業団地のマスのうち、通勤圏の失業者と商業・サービス業の店で働く人
        （ほかの施設は店から引き抜ける。回4）が要る人数の半分以上いて、運賃の率が最も低いマス。
        なければ、そのわけ（no_zone: 地形の合う空いた工業団地のマスがない、no_workers: あるが
        通勤圏に雇える人が足りない）。"""
        defs, config = ctx.defs, ctx.config
        need = fac.workers_needed(defs, kind)
        radius = fac.commute_radius(defs, config, kind)
        shop = services.staff_by_tile(island, config)
        occupied = island.facilities_by_tile()
        spots = []
        fitting = 0
        for tile in island.tiles:
            if tile.zone != ZONE_INDUSTRIAL or (tile.x, tile.y) in occupied:
                continue
            if fac.placement_problem(defs, island, kind, tile.x, tile.y, rname) is not None:
                continue
            fitting += 1
            idle = 0
            for x, y in within(island, (tile.x, tile.y), radius):
                t = island.tile(x, y)
                if t.population:
                    idle += max(0, fac.labor_force(defs, island, t) - t.ext.get("employed", 0))
                    idle += shop.get((x, y), 0)
            if idle * 2 >= need:
                spots.append((tile.x, tile.y))
        if not spots:
            return "no_workers" if fitting else "no_zone"
        rates = freight_rates.candidate_rates(island, defs, config, kind, rname, spots)
        best = min(spots, key=lambda p: (rates[p], p[1], p[0]))
        return best, rates[best]

    def _finance(
        self, island: IslandState, ctx: StageContext, amount: int, earnings: int
    ) -> list[tuple[str, int, int]] | tuple[str, dict[str, Any]]:
        """出資と建設資金の融資の組み立て: [(銀行, 借りる額, 金利)]（出資は amount − 借りる額の合
        計）。資産を持つ層が出資しきれないか、審査に通らなければ、そのわけ（わけと引数の組）。
        国債の債務不履行で損をして新しい融資を止めている銀行からは借りない。"""
        config = ctx.config
        want = amount - round_money(amount * config.get("firms.equity_share"))
        owners = Payer(household(OWNERS))
        if OWNERS not in island.economy.households:
            return ("no_owners", {})
        offers = []
        stopped = []  # 新しい融資を止めている銀行が、止めるのをやめるターン
        for bank in banks.active_banks(island):
            if banks.labor_ratio(island, config, ctx.defs, bank) <= 0:
                continue
            if banks.lending_stopped(island, bank, ctx.turn):
                stopped.append(banks.facility_of(island, bank).economy["crunch_until"])
                continue
            offers.append((banks.margin_of(island, config, bank), bank))
        pieces = []
        left = want
        for _, bank in sorted(offers):
            room = min(
                banks.capital_room(island, config, bank, ctx.turn),
                banks.exposure_limit(island, config, bank, ctx.turn),
            )
            piece = min(left, room)
            if piece > 0:
                pieces.append((bank, piece))
                left -= piece
        loan = want - left
        have = funds.available(island, owners)
        if have < amount - loan:
            if stopped and loan == 0:
                return ("credit_crunch", {"turn": min(stopped) - 1})
            return ("owners_short", {"need": amount - loan, "have": have, "loan": loan})
        turns = config.get("loans.construction_turns")
        leverage = to_basis(config.get("banking.rho")) * loan // max(1, amount)
        plan = []
        service = 0
        for bank, piece in pieces:
            rate = island.policy.policy_rate + banks.margin_of(island, config, bank) + leverage
            plan.append((bank, piece, rate))
            service += ledger.interest(piece, rate) + -(-piece // turns)
        if earnings < config.get("loans.coverage") * service:
            return ("loan_refused", {"earnings": earnings, "service": service})
        return plan

    def _build(
        self,
        island: IslandState,
        ctx: StageContext,
        kind: str,
        rname: str,
        pos: tuple[int, int],
        capital: int,
        prices: dict[str, int],
        rate: int,
        subsidy: tuple[int, int] = (0, 0),
    ) -> tuple[str, dict[str, Any]] | None:
        """民営工場の工事を始める。建てられなければ、そのわけ（_finance）。

        subsidy は設備投資補助の（率, 先に払う額）。先に払う額は工事を始めるときに補助金の予算から
        払い（建設費の残りだけを集めて、工事の途中で資金が尽きないように）、完成のときに精算する。
        """
        defs, config = ctx.defs, ctx.config
        probe = productivity.probe(island, defs, config, kind, rname, pos)
        wage = island.economy.average_wage
        earnings = variable_profit(defs, config, probe, prices, wage, rate)
        # 資材の値上がりに備えて建設費を1割多めに用意し、動き出すときの支払い（firms.operating_turns
        # ターン分）も合わせて調達する
        start = operating_cost(defs, config, probe, prices, wage, rate)
        amount = capital + capital // 10 + start * config.get("firms.operating_turns")
        plan = self._finance(island, ctx, amount, earnings)
        if isinstance(plan, tuple):
            return plan
        f = Facility(
            id=island.next_facility_id(),
            kind=kind,
            x=pos[0],
            y=pos[1],
            recipe=rname,
            owner=OWNER_PRIVATE,
            accounting=ACCOUNTING_SELF,
            economy={"capital": 0},
            ext={"private_entry": True},
        )
        assign_serial(island, f)
        island.facilities.append(f)
        entity = facility_entity(f.id)
        invest(island, household(OWNERS), entity, amount - sum(p for _, p, _ in plan))
        turns = config.get("loans.construction_turns")
        far = 10**9  # 元金は工事が済んでから返す（工事の段で返し始めるターンを決める）
        for bank, loan, loan_rate in plan:
            purpose = banks.CONSTRUCTION
            banks.lend(island, bank, entity, loan, purpose, turns, ctx.turn, loan_rate, far)
        rate_of_subsidy, upfront = subsidy
        payer = Payer(ledger.GOV, SUBSIDIES)
        upfront = min(upfront, funds.available(island, payer))
        if rate_of_subsidy:
            f.ext["subsidy_rate"] = rate_of_subsidy
        if upfront > 0:
            funds.pay(island, payer, entity, upfront, "subsidy")
            f.ext["subsidy_paid"] = upfront
        freight_rates.forget(island)
        island.log.append(
            LogEntry(
                "private_entry",
                {
                    "facility": kind,
                    "x": pos[0],
                    "y": pos[1],
                    "amount": amount + max(0, upfront),
                },
            )
        )
        stats = island.stats
        stats["firms.entered"] = stats.get("firms.entered", 0) + 1
        return None

    def _buy_foreclosed(
        self,
        island: IslandState,
        ctx: StageContext,
        kind: str,
        rname: str,
        prices: dict[str, int],
        threshold: float,
        estimate: float,
        fill: float,
    ) -> bool:
        """差し押さえ中の同じ施設を、新しく建てるより利益率が高ければ買い取る。"""
        config = ctx.config
        rates = freight_rates.facility_rates(island, ctx.defs, config)
        wage = island.economy.average_wage
        for f in sorted(island.facilities, key=lambda f: f.id):
            seized = f.economy.get("foreclosed")
            if not seized or f.kind != kind or f.recipe != rname:
                continue
            price = max(1, seized["price"])
            rate = rates.get(f.id, 0)
            r = ctx.formula("entry_return")(island, ctx, kind, rname, prices, rate, price) * fill
            if r <= max(threshold, estimate):
                continue
            earnings = variable_profit(ctx.defs, config, f, prices, wage, rate)
            start = operating_cost(ctx.defs, config, f, prices, wage, rate)
            amount = price + start * config.get("firms.operating_turns")
            plan = self._finance(island, ctx, amount, earnings)
            if isinstance(plan, tuple):
                continue
            close_foreclosure(island, f)
            entity = facility_entity(f.id)
            invest(island, household(OWNERS), entity, amount - sum(p for _, p, _ in plan))
            turns = config.get("loans.construction_turns")
            grace = ctx.turn + 1 + config.get("loans.grace_turns")
            for bank, loan, loan_rate in plan:
                purpose = banks.CONSTRUCTION
                banks.lend(island, bank, entity, loan, purpose, turns, ctx.turn, loan_rate, grace)
            pay_seller(island, entity, seized, price)
            restart(island, ctx, f, OWNER_PRIVATE, price)
            f.economy["entered"] = ctx.turn
            island.log.append(
                LogEntry(
                    "foreclosure_sold", {"facility": f.kind, "x": f.x, "y": f.y, "price": price}
                )
            )
            return True
        return False

    # --- 民間銀行の参入 ---------------------------------------------------------------

    def _bank_entry(self, island: IslandState, ctx: StageContext) -> None:
        """民間銀行の参入。参入できる政策（自由か、免許を出した許可制）なのに開業しなかったときは、
        そのわけを economy.finance["bank_entry_block"] に残し、わけが変わったらログで知らせる。"""
        finance = island.economy.finance
        reason = self._open_private_bank(island, ctx)
        if reason is None:
            finance.pop("bank_entry_block", None)
            return
        key, args = reason
        before = finance.get("bank_entry_block") or {}
        finance["bank_entry_block"] = {"reason": key, "args": args}
        if before.get("reason") != key:
            island.log.append(
                LogEntry(
                    "bank_entry_blocked",
                    {"reason": f"bank_block_{key}", "reason_args": dict(args)},
                )
            )

    def _open_private_bank(
        self, island: IslandState, ctx: StageContext
    ) -> tuple[str, dict[str, Any]] | None:
        """条件がそろえば民間銀行を開業させる。参入できる政策なのに開業しなければ、そのわけ。"""
        config = ctx.config
        finance = island.economy.finance
        rule = entry_policy(island, ctx)
        licensed = finance.get("bank_license", False)
        if rule == "closed" or (rule == "license" and not licensed):
            return None
        current = banks.bank_list(island)
        if len(current) >= config.get("banking.max_banks"):
            finance.pop("bank_license", None)
            return ("max_banks", {"count": len(current)})
        total = sum(banks.deposits_of(island, b) for b in current)
        capital = part(total, to_basis(config.get("banking.entry_capital")))
        share = total // (len(current) + 1)
        if rule == "free" and not licensed:
            if not current or share <= config.get("banking.entry_deposits"):
                need = config.get("banking.entry_deposits")
                return ("deposits_low", {"share": share, "need": need})
            # 開業した銀行は預金金利を政策金利まで上げて預金を集める。今の銀行がもう政策金利を
            # 付けていれば（政策金利 0% など）、預金は移ってこない（預金のない銀行が赤字で破綻した）
            best = max(banks.deposit_rate_of(island, b) for b in current)
            if island.policy.policy_rate <= best:
                rate = round(island.policy.policy_rate * 100 / BASIS, 2)
                return ("no_rate_room", {"rate": rate})
            line = island.policy.policy_rate / BASIS + config.get("banking.entry_roe_margin")
            if self._entry_profit(island, ctx, current, share) * YEAR <= line * max(1, capital):
                return ("unprofitable", {})
        kind = config.get("banking.facility")
        spot = None
        occupied = island.facilities_by_tile()
        for tile in island.tiles:
            if tile.zone == ZONE_OFFICE and (tile.x, tile.y) not in occupied:
                if fac.placement_problem(ctx.defs, island, kind, tile.x, tile.y) is None:
                    spot = (tile.x, tile.y)
                    break
        owners = Payer(household(OWNERS))
        if spot is None:
            return ("no_office", {})
        if OWNERS not in island.economy.households:
            return ("no_owners", {})
        have = funds.available(island, owners)
        if have < capital or capital <= 0:
            return ("owners_short", {"need": capital, "have": have})
        finance.pop("bank_license", None)
        bank = open_bank(island, ctx, spot, OWNER_PRIVATE)
        invest(island, household(OWNERS), ledger.bank_entity(bank.id), capital)
        bank.economy["deposit_goal"] = share
        island.log.append(LogEntry("bank_opened", {"x": spot[0], "y": spot[1], "capital": capital}))
        return None

    @staticmethod
    def _entry_profit(
        island: IslandState, ctx: StageContext, current: list[str], share: int
    ) -> float:
        """預金 share を集めた新しい銀行の1ターンの利益の見込み: 今ある銀行の「職員の賃金を払う
        前の利益 ÷ 預金」を当てはめ、自分の職員の賃金を引く。"""
        earned = deposits = 0.0
        for bank in current:
            f = banks.facility_of(island, bank)
            profits = (f.economy.get("profits") or []) if f is not None else []
            if f is None or not profits:
                continue
            earned += sum(profits) / len(profits) + f.workers * f.economy.get("wage", 0)
            deposits += banks.deposits_of(island, bank)
        if deposits <= 0:
            return 0.0
        kind = ctx.config.get("banking.facility")
        payroll = fac.workers_needed(ctx.defs, kind) * island.economy.average_wage
        return share * earned / deposits - payroll


def _spare(finance: dict[str, Any], good: str) -> float:
    """直近の、島の市場に売りに出た量の平均 − 買いたい量の平均（1000 分の 1）。"""
    supply = finance.get("supply", {}).get(good, [])
    demand = finance.get("demand", {}).get(good, [])
    average = sum(supply) / len(supply) if supply else 0.0
    return average - (sum(demand) / len(demand) if demand else 0.0)


def open_bank(island: IslandState, ctx: StageContext, pos: tuple[int, int], owner: str) -> Facility:
    """銀行を開業させる（資本は呼ぶ側が出資する）。"""
    config = ctx.config
    f = island.facility_at(*pos)
    if f is None:
        f = Facility(
            id=island.next_facility_id(),
            kind=config.get("banking.facility"),
            x=pos[0],
            y=pos[1],
            owner=owner,
            accounting=ACCOUNTING_SELF,
            built=True,
        )
        assign_serial(island, f)
        island.facilities.append(f)
    f.owner = owner
    f.accounting = ACCOUNTING_SELF
    init_facility(
        island, ctx.defs, config, f, dict(island.economy.prices), island.economy.average_wage
    )
    f.economy["deposit_rate"] = island.policy.deposit_rate
    f.economy["margin"] = to_basis(config.get("banking.margin"))
    if f.id not in island.economy.banks:
        island.economy.banks.append(f.id)
    return f


def remove_facility(island: IslandState, ctx: StageContext, f: Facility) -> None:
    """閉鎖した民営施設の在庫を国が市場の値段で引き取り、預金を未払い賃金 → 未払いの税 → 借入の
    順に払って残りを出資者に返し、出資を消して、在庫を国の倉庫に移して取り除く。"""
    from hakoniwa_core.modules.construction import close_accounts, sell_leftover_stock

    sell_leftover_stock(island, f)
    close_accounts(island, f)
    stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
    fac.vacate(island, f)
    freight_rates.forget(island)


def pay_seller(island: IslandState, payer: str, seized: dict[str, Any], price: int) -> None:
    """差し押さえた施設の代金を、差し押さえの記録 seized の請求の順（未払い賃金 → 未払いの税 →
    貸し手の割合）に払い、余りを元の持ち主に返す（回3。Codex E24）。"""
    distribute(island, payer, price, seized, "foreclosure_sale")


def restart(island: IslandState, ctx: StageContext, f: Facility, owner: str, price: int) -> None:
    """売れた施設を新しい持ち主の下で動き出させる（設備の額は買った値段にする）。"""
    f.owner = owner
    f.accounting = default_accounting(ctx.defs, island, f.kind, owner)
    for key in ("closing", "idle_turns", "loss_streak", "loss_turns", "ebitda"):
        f.economy.pop(key, None)
    init_facility(
        island, ctx.defs, ctx.config, f, dict(island.economy.prices), island.economy.average_wage
    )
    f.economy["capital"] = price
    step = round_money(ctx.config.get("market.target_step") * fac.FULL + 0.5)
    f.economy["target"] = step
    f.status = fac.STATUS_STARTING
    freight_rates.forget(island)


# --- 命令: 指定 ---------------------------------------------------------------------

ZONES = {"industrial": ZONE_INDUSTRIAL, "office": ZONE_OFFICE, "none": ZONE_NONE}


def check_set_zone(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["zone"] not in ZONES:
        raise OrderFailed("reason_invalid_payload")
    occupied = island.facilities_by_tile()
    for x, y in payload["tiles"]:
        if not island.in_bounds(x, y):
            raise OrderFailed("reason_out_of_bounds")
        if not fac.is_land(defs, island.tile(x, y).terrain):
            raise OrderFailed("reason_not_land", x=x, y=y)
        if island.capital == (x, y):
            raise OrderFailed("reason_zone_capital", x=x, y=y)
        here = occupied.get((x, y))
        if here is not None and defs.facilities[here.kind].get("category") == "housing":
            raise OrderFailed("reason_zone_town", x=x, y=y)


def set_zone(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """マスを工業団地・オフィス街に指定する・解除する（建っている施設はそのまま）。"""
    zone = ZONES[payload["zone"]]
    tiles = sorted({(x, y) for x, y in payload["tiles"]})
    for x, y in tiles:
        island.tile(x, y).zone = zone
    kind = "zone_cleared" if zone == ZONE_NONE else "zone_set"
    island.log.append(LogEntry(kind, {"count": len(tiles), "zone": f"zone_{zone or 'none'}"}))


# --- 命令: 国有化と民営化 ---------------------------------------------------------


def _owned_facility(island: IslandState, payload: dict[str, Any], defs: Defs) -> Facility:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False and f.kind != "transport":
        raise OrderFailed("reason_no_facility")
    if not f.built:
        raise OrderFailed("reason_under_construction", facility=f.kind)
    if defs.facilities[f.kind].get("category") == "finance":
        raise OrderFailed("reason_bank_ownership")
    return f


def check_nationalize(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = _owned_facility(island, payload, defs)
    if f.owner != OWNER_PRIVATE and "foreclosed" not in f.economy:
        raise OrderFailed("reason_not_private", facility=f.kind)


def nationalize(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """民営の施設を純資産で政府が買い取る（借入は引き継ぐ）。差し押さえ中の施設は銀行から売り値で買う。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    entity = facility_entity(f.id)
    seized = f.economy.get("foreclosed")
    price = seized["price"] if seized else banks.net_assets(island, f, entity)
    payer = Payer(ledger.GOV)
    if funds.available(island, payer) < price:
        raise OrderFailed("reason_treasury_short", amount=price)
    if seized:
        close_foreclosure(island, f)
        funds.pay(island, payer, entity, price, "nationalization")
        pay_seller(island, entity, seized, price)
        restart(island, ctx, f, OWNER_STATE, price)
        if is_self_supporting(f):
            fund_facility(island, ctx.defs, ctx.config, f, Payer(ledger.GOV, STATE_FACILITIES))
    else:
        funds.pay(island, payer, household(OWNERS), price, "nationalization")
        _move_equity(island, entity, household(OWNERS), ledger.GOV)
        f.owner = OWNER_STATE
        for key in ("closing", "idle_turns", "loss_streak"):
            f.economy.pop(key, None)
    island.log.append(
        LogEntry("nationalized", {"facility": f.kind, "x": f.x, "y": f.y, "price": price})
    )


def check_privatize(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = _owned_facility(island, payload, defs)
    if f.owner != OWNER_STATE or "foreclosed" in f.economy:
        raise OrderFailed("reason_not_state_owned", facility=f.kind)
    if is_public_service(defs, f) or not private_allowed(defs, island, f.kind):
        raise OrderFailed("reason_cannot_privatize", facility=f.kind)


def privatize(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国営の施設を純資産で資産を持つ層に売る。国庫直轄の施設は、先に運転資金を出資して独立採算にする。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    entity = facility_entity(f.id)
    owners = Payer(household(OWNERS))
    if OWNERS not in island.economy.households:
        raise OrderFailed("reason_cannot_privatize", facility=f.kind)
    if f.accounting == ACCOUNTING_TREASURY:
        f.accounting = ACCOUNTING_SELF
        fund_facility(island, ctx.defs, ctx.config, f, Payer(ledger.GOV, STATE_FACILITIES))
    price = banks.net_assets(island, f, entity)
    if funds.available(island, owners) < price:
        raise OrderFailed("reason_owners_short", amount=price)
    funds.pay(island, owners, ledger.GOV, price, "privatization")
    _move_equity(island, entity, ledger.GOV, household(OWNERS))
    f.owner = OWNER_PRIVATE
    island.log.append(
        LogEntry("privatized", {"facility": f.kind, "x": f.x, "y": f.y, "price": price})
    )


def _move_equity(island: IslandState, entity: str, source: str, target: str) -> None:
    value = ledger.balance(island, ledger.account(source, ledger.EQUITY, entity))
    if value > 0:
        ledger.transfer(island, ledger.EQUITY, entity, source, target, value, "equity_transfer")


# --- 命令: 補助金 -------------------------------------------------------------------

SUBSIDY_KINDS = ("production", "capital", "bailout")


def check_subsidy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    kind = payload["kind"]
    if kind not in SUBSIDY_KINDS:
        raise OrderFailed("reason_invalid_payload")
    has_good = payload.get("good") is not None
    has_tile = payload.get("x") is not None and payload.get("y") is not None
    if has_good and payload["good"] not in defs.goods:
        raise OrderFailed("reason_unknown_good")
    if kind == "capital" and not has_good:
        raise OrderFailed("reason_invalid_payload")
    if kind == "bailout" and not has_tile:
        raise OrderFailed("reason_invalid_payload")
    if kind == "production" and not (has_good or has_tile):
        raise OrderFailed("reason_invalid_payload")
    if has_tile and not island.in_bounds(payload["x"], payload["y"]):
        raise OrderFailed("reason_out_of_bounds")


def set_subsidy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """補助金を決める（率は %、期間はターン）。率 0 で同じ対象の補助金をやめる。"""
    kind = payload["kind"]
    target = {k: payload[k] for k in ("good", "x", "y") if payload.get(k) is not None}
    keep = [
        s
        for s in island.policy.subsidies
        if not (s["kind"] == kind and {k: s.get(k) for k in target} == target)
    ]
    rate = to_basis(payload["rate"] / 100)
    if rate > 0:
        keep.append(
            {"kind": kind, **target, "rate": rate, "until": ctx.turn + payload["turns"] - 1}
        )
    island.policy.subsidies = keep
    args = {"subsidy": f"subsidy_{kind}", "rate": payload["rate"], "turns": payload["turns"]}
    where = "good" if "good" in target else "tile"
    action = "set" if rate > 0 else "cleared"
    island.log.append(LogEntry(f"subsidy_{action}_{where}", {**args, **target}))


def register(registry: Registry) -> None:
    registry.add_stage(EntryExit())
    registry.add_formula("entry_return", entry_return)
    registry.add_order(OrderHandler("set_zone", execute=set_zone, check=check_set_zone))
    registry.add_order(OrderHandler("nationalize", execute=nationalize, check=check_nationalize))
    registry.add_order(OrderHandler("privatize", execute=privatize, check=check_privatize))
    registry.add_order(OrderHandler("set_subsidy", execute=set_subsidy, check=check_subsidy))
