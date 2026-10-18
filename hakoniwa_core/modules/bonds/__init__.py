"""国債の段（フェーズ2-2）: 利払いと償還（払えなければ債務不履行）、入札と売れ残りの引き受け、
国債の売買の市場。

1. 区分ごとの利回り（economy.finance["yields"]）を、前のターンからの政策金利の動きだけ動かす。
2. 命令 set_bond_payment で支払いを止めていれば、すべての銘柄を不履行にする。
3. 満期の銘柄のうち中央銀行の持ち分は、同じ期間の新しい国債に乗り換える（現実の中央銀行と同じ。
   独立していても乗り換える。持ち分を返すお金で世の中のお金が減り、借り換えの入札が一度に大きく
   ならないように）。
4. 今のターンの利払いと償還に国庫が足りなければ、足りない分を入札で発行する（借り換え）。借り換えで
   売れ残った分は、独立した中央銀行も、売れ残りを引き受けない政策の国も、中央銀行が買い支える
   （バランス調整。借り換えの売れ残りだけで不履行にしない）。
5. 銘柄ごとに利子を払い、満期の銘柄は額面を返して消す。不履行は命令 set_bond_payment で選んだとき
   だけ: 額面を bonds.haircut だけ切り下げ（持ち手の損）、残りを同じ期間で出し直す。不履行の記録は
   利回りに δ を上乗せし（bonds.default_premium。bonds.default_decay_turns ターンかけて 0 に減る）、
   区分の利回りも同じだけ跳ね上がる。不履行から bonds.default_exclusion_turns ターンは、銀行が国債
   を買わない（入札で売れず、新しい発行の売れ残りは政策どおり中央銀行が引き受けるか支出を減らす。
   売買の市場でも買わない）。損をした銀行は banking.default_crunch_turns ターン新しい融資を止める
   （economy/banks.py の lending_stopped。計画経済の島を除く）。
6. 命令で決めた中央銀行の直接引き受けと追加の発行。
7. 国債の売買の市場（区分ごとの板寄せ）。
8. 「このターンの国債以外の政府の支出（次のターンの見込み）× (1 + bonds.buffer_turns) ＋ 次のターン
   の利払い − 国庫の残高」がプラスなら、その額を入札で発行する。期間は政策の割合
   （policy.bond_terms）で分ける。

入札: 買い手は市中銀行です。各銀行は準備預金のうち「最低限 + 預金 × banking.liquidity_share」と
中央銀行・銀行間から借りている額を超えた分を、banking.auction_steps の段に分けて注文します。段の指値
は、その段まで買ったときに銀行が求める利回り（市場の利回り（式 bond_yield）+ 国債への偏りの上乗せ。
bank_yield）で、発行額が売れるいちばん低い利回りで全員が買います（その利回りが銘柄の利率。回5、
カード「市場の分だけ」）。新しい発行の売れ残りは、政策
（policy.bond_unsold）が「引き受ける」で中央銀行が独立していなければ中央銀行が同じ利回りで引き受け、
そうでなければ発行しません（足りない支出は支払いの順番で削られる）。借り換えの売れ残りは、いつも
中央銀行が同じ利回りで買い支えます。

国債の売買の市場: 残りの期間で分けた区分ごとに、利回りを値段にした板寄せで売り買いします（値幅は前の
ターンの利回り ± bonds.trade_band）。市中銀行は国債の額面を目安（偏りの上乗せで求める利回りが区分の
今の利回りと等しくなる持ち高。target_holding）に近づけるように注文し（売りは自分の求める利回り ＋
banking.sell_premium まで受け入れ、買いは自分の求める利回り以上）、
準備預金が最低限に足りない銀行は足りない分を指値なしで売ります。独立していない中央銀行は、政策
（policy.cb_bond_trade）の額を指値なしで買う・売ります。約定した区分の利回りは約定した利回りになりま
す。銘柄の代金は、約定した利回りで割り引いた値段です（hakoniwa_core/economy/debt.py の price）。

決算の段より前に置くのは、受け取った利子と、売った・償還された国債の損益を、同じターンの損益に
入れるためです。国債の持ち手ごとの「時価の評価の元になる額」の出入り（買った代金・売った代金・償還）は
island.scratch["bond_flows"] に残します（決算の段の評価に使う）。取得原価と実現損益の記録は
hakoniwa_core/economy/debt.py の bought・released・traded です。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import BONDS, Payer, banks, books, debt, funds
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.banking import is_planned
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import Bond, IslandState, LogEntry, WorldState
from hakoniwa_core.trade import abroad, currency_label
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis

UNDERWRITE = "underwrite"
CUT = "cut"


def term_premium(defs: Defs, turns: int) -> float:
    """期間 turns（ターン）の上乗せ: finance.yaml の bond_term の premium を期間で直線でつなぐ。"""
    points = sorted((v["turns"], v["premium"]) for v in defs.bond_terms().values())
    if not points:
        return 0.0
    if turns <= points[0][0]:
        return points[0][1] * max(0, turns) / points[0][0]
    for (a, pa), (b, pb) in zip(points, points[1:], strict=False):
        if turns <= b:
            return pa + (pb - pa) * (turns - a) / (b - a)
    return points[-1][1]


def default_premium(island: IslandState, ctx: StageContext) -> float:
    """債務不履行の上乗せ δ（不履行の直後に bonds.default_premium で、だんだん 0 に減る）。"""
    turn = island.economy.finance.get("default_turn")
    if turn is None:
        return 0.0
    settings = ctx.config.get("bonds")
    left = 1 - (ctx.turn - turn) / settings["default_decay_turns"]
    return settings["default_premium"] * max(0.0, left)


def bond_yield(island: IslandState, turns: int, ctx: StageContext) -> int:
    """市場の利回り（1万分の1）: r_P + λ_T + σ max(0, I/R − 閾値) + κ max(0, π − π*) + δ − β。

    λ_T は期間 turns の上乗せ、I/R は返す力の比（中央銀行以外に払う利子 ÷ 国の収入。年。
    hakoniwa_core/economy/debt.py の burden。中央銀行の持ち分は市場の外。回5）、σ と閾値は
    bonds.burden_sigma と bonds.burden_threshold、δ は過去の債務不履行の上乗せ、β は中央銀行が
    独立していれば bonds.independence_discount。0 から bonds.max_yield の間に収める。銀行ごとに
    求める利回りは、これに国債への偏りの上乗せを足す（bank_yield）。
    """
    settings = ctx.config.get("bonds")
    rate = island.policy.policy_rate / BASIS + term_premium(ctx.defs, turns)
    rate += settings["burden_sigma"] * max(0.0, _burden(island, ctx) - settings["burden_threshold"])
    target = ctx.config.get("central_bank.inflation_target")
    rate += settings["kappa"] * max(0.0, inflation(island.economy) - target)
    rate += default_premium(island, ctx)
    if island.policy.cb_independent:
        rate -= settings["independence_discount"]
    return to_basis(max(0.0, min(settings["max_yield"], rate)))


def _burden(island: IslandState, ctx: StageContext) -> float:
    """返す力の比（ターンの初めに一度だけ数える。利回りの式は1ターンに何十回も呼ばれ、銘柄も多いので）。"""
    cached = island.scratch.get("bond_burden")
    if cached is None or cached[0] != ctx.turn:
        cached = (ctx.turn, debt.burden(island))
        island.scratch["bond_burden"] = cached
    return cached[1]


def bond_assets(island: IslandState, bank: str) -> int:
    """銀行の資産（準備預金・融資・国債の額面・銀行間の貸し）。国債への偏りの分母。"""
    held = sum(debt.holdings_of(island, bank).values())
    return (
        max(0, banks.reserves_of(island, bank))
        + banks.loans_of(island, bank)
        + held
        + banks.interbank_lent(island, bank)
    )


def concentration(ctx: StageContext, island: IslandState) -> tuple[float, float]:
    """国債への偏りの上乗せの (上乗せのない割合, 係数)。計画経済の国営銀行は預金を国に貸すのが
    仕事なので上乗せしない（ソ連の貯蓄銀行の預金は国の借り入れに回った）。"""
    settings = ctx.config.get("banking.bond_concentration")
    if is_planned(ctx.defs, island):
        return 1.0, 0.0
    return settings["free"], settings["premium"]


def bank_yield(base: int, share: float, free: float, premium: float, top: int) -> int:
    """国債を資産の share だけ持つ銀行が求める利回り（市場の利回り base ＋ 偏りの上乗せ）。"""
    return min(top, base + to_basis(premium * max(0.0, share - free)))


def target_holding(assets: int, market: int, base: int, free: float, premium: float) -> int:
    """区分の今の利回り market で銀行が持ちたい国債の額面（偏りで求める利回りが market と等しく
    なる割合 × 資産。0 から資産まで）。"""
    if premium <= 0:
        return assets
    share = free + max(0, market - base) / BASIS / premium
    return int(assets * max(0.0, min(1.0, share)))


def auction_budget(island: IslandState, ctx: StageContext, bank: str) -> int:
    """銀行が国債の入札に回すお金: 準備預金のうち、最低限 ＋ 預金 × banking.liquidity_share と、
    中央銀行と銀行間から借りている額と、政府の預金を超えた分（決算の支払いと預金の出入りの分を
    手元に残す）。政府の預金は国がいつでも引き出すので、全額を準備のまま持つ（回5。国債に替えると、
    預けた分だけ入札の買いが増えて利回りが下がり、引き出されたときに売る相手のない島で詰まる）。

    中央銀行と銀行間からの借り入れは翌ターンの銀行の段で返すので、国債の段では借りている額が 0
    になっている。前のターンに借りた額（このターンに返した額）も残し、満期で戻ったお金で国債を
    買い直してはまた中央銀行から借りる、を繰り返さないようにする。"""
    config = ctx.config
    keep = part(banks.deposits_of(island, bank), to_basis(config.get("banking.liquidity_share")))
    keep += banks.cb_borrowed(island, bank) + banks.interbank_borrowed(island, bank)
    keep += banks.overnight_repaid(island, bank)
    keep += banks.government_deposit(island, bank)
    return max(0, banks.excess_reserves(island, config, bank) - keep)


def init_yields(island: IslandState, ctx: StageContext) -> list[int]:
    """区分ごとの利回りの初めの値（区分を代表する期間の、求める利回り）。"""
    terms = ctx.config.get("bonds.bucket_terms")
    return [ctx.formula("bond_yield")(island, t, ctx) for t in terms]


def flows(island: IslandState) -> dict[str, int]:
    return island.scratch.setdefault("bond_flows", {})


def _add_flow(island: IslandState, holder: str, amount: int) -> None:
    if island.scratch.get(debt.VALUED):
        # 決算の段で時価を評価した後（破綻の段の救済のための発行など）の出入りは、帳簿の値に
        # 直接足す（次のターンの評価損益に、買った額面が益として入らないように）
        book = island.economy.finance.setdefault("bond_book", {})
        book[holder] = book.get(holder, 0) + amount
        return
    table = flows(island)
    table[holder] = table.get(holder, 0) + amount


class Bonds(WorldStage):
    """国債の段。フェーズ3-3 から世界の段で、島ごとの処理（run_island）を島の番号の順に行い、外国の
    持ち手と買い手（hakoniwa_core/trade/abroad.py）をその島の通貨で加える。外国の持ち手の注文は段の
    初めに全部の島の分を決め、ある島の処理がほかの島の処理の読む値を変えないので、島の順番によらない。

    破綻の段（島ごとの段）が救済のために呼ぶ top_up・raise_funds では、外国の買い手は加わらない。
    """

    name = "bonds"

    def __init__(self) -> None:
        self.world: WorldState | None = None
        self.bids: dict[str, abroad.Bid] = {}  # 外国の持ち手の名前 → この島への注文

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        world = ctx.before
        foreign = abroad.orders(world, ctx)
        self.world = world
        try:
            for island_id in world.island_ids():
                self.bids = {bid.name: bid for bid in foreign.get(island_id, [])}
                self.run_island(world.islands[island_id], ctx)
        finally:
            self.world, self.bids = None, {}
        abroad.revalue(world, ctx.config, ctx.turn)
        _log_foreign(world, foreign)
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None

    def _world(self) -> WorldState:
        assert self.world is not None, "外国の持ち手の処理は国債の段の中だけ"
        return self.world

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        finance = island.economy.finance
        debt.start_costs(island, ctx.turn - 1, ctx.config.get("bonds.bucket_terms"))
        self._shift_yields(island, ctx)
        if debt.reopen_turn(island, ctx.config) == ctx.turn:
            island.log.append(LogEntry("bonds_market_reopened", {}))
        if finance.pop("default_order", False) and island.bonds:
            self._default(
                island, ctx, sorted(island.bonds, key=lambda b: b.id), "bond_default_ordered"
            )
        payer = Payer(ledger.GOV, BONDS)
        self._roll_central_bank(island, ctx)
        short = debt.due(island, ctx.turn) - funds.available(island, payer)
        if short > 0:
            self.raise_funds(island, ctx, short, "bonds_refinanced")
        self._make_up(island, ctx, debt.due(island, ctx.turn) - funds.available(island, payer))
        self._pay(island, ctx, payer)
        self._ordered(island, ctx)
        self._secondary(island, ctx)
        self.top_up(island, ctx)
        # 国庫に残す額を超えた分は、市中銀行に政府の預金として預ける（政府の黒字で準備預金が国庫へ
        # 吸い上げられたままにならないように。回5）。まだ支出のない月（建国の月）は、残す額が
        # 分からないので預けない
        if any(v for k, v in funds.spent(island).items() if k != BONDS):
            # 預け先は、自己資本比率が規制の値以上の銀行だけ（預金の額の割合で分ける）
            weights = {
                b: banks.deposits_of(island, b)
                for b in banks.healthy_banks(island, ctx.config, ctx.turn)
            }
            funds.deposit_surplus(island, self.treasury_need(island, ctx), weights)
        finance["last_policy_rate"] = island.policy.policy_rate
        self.record_stats(island)

    def top_up(
        self, island: IslandState, ctx: StageContext, spent: dict[str, int] | None = None
    ) -> None:
        """国庫が「このターンの国債以外の政府の支出（次のターンの見込み）×（1 + bonds.buffer_turns）
        ＋ 次のターンの利払い」に足りなければ、足りない分を入札で発行する。spent は支出の項目ごとの
        額（既定はこのターンに使った額）。

        破綻の段も、銀行の救済と預金保護で国庫から出した後に呼ぶ（次のターンの教育費などが、救済の
        ために払えなくならないように）。そのときの spent は救済の前の額にする（1度きりの救済を、次の
        ターンにも出す支出に数えない）。
        """
        need = self.treasury_need(island, ctx, spent) - ledger.money(island, ledger.GOV)
        if need > 0:
            self.raise_funds(island, ctx, need)

    @staticmethod
    def treasury_need(
        island: IslandState, ctx: StageContext, spent: dict[str, int] | None = None
    ) -> int:
        """国庫に残す額: このターンの国債以外の政府の支出（次のターンの見込み）×（1 +
        bonds.buffer_turns）＋ 次のターンの利払い。spent は支出の項目ごとの額（既定はこのターンに
        使った額）。"""
        if spent is None:
            spent = funds.spent(island)
        spending = sum(v for k, v in spent.items() if k != BONDS)
        buffer = ctx.config.get("bonds.buffer_turns")
        return spending * (1 + buffer) + debt.due(island, ctx.turn + 1, redemptions=False)

    # --- 利回り -----------------------------------------------------------------------

    @staticmethod
    def _shift_yields(island: IslandState, ctx: StageContext) -> None:
        """区分の利回りを、前のターンからの政策金利の動きだけ動かす（記録がなければ求める利回り）。"""
        finance = island.economy.finance
        yields = finance.get("yields")
        if not yields:
            finance["yields"] = init_yields(island, ctx)
            finance["last_policy_rate"] = island.policy.policy_rate
            return
        change = island.policy.policy_rate - finance.get(
            "last_policy_rate", island.policy.policy_rate
        )
        top = to_basis(ctx.config.get("bonds.max_yield"))
        finance["yields"] = [max(0, min(top, y + change)) for y in yields]

    @staticmethod
    def _set_yield(island: IslandState, ctx: StageContext, turns: int, rate: int) -> None:
        edges = ctx.config.get("bonds.buckets")
        yields = island.economy.finance.setdefault("yields", init_yields(island, ctx))
        yields[debt.bucket_of(turns, edges)] = rate

    # --- 支払いと不履行 ---------------------------------------------------------------

    def _roll_central_bank(self, island: IslandState, ctx: StageContext) -> None:
        """今のターンに満期の銘柄のうち中央銀行の持ち分を、同じ期間の新しい国債（求める利回り）に
        乗り換える。国庫に入った額は、同じターンの償還で中央銀行に返る。"""
        rolled: dict[int, int] = {}  # 期間（ターン）→ 乗り換えた額面
        for bond in sorted(island.bonds, key=lambda b: b.id):
            if ctx.turn < bond.maturity:
                continue
            face = debt.holders(island, bond).get(ledger.CB, 0)
            if face <= 0:
                continue
            rate = ctx.formula("bond_yield")(island, bond.term, ctx)
            debt.underwrite(island, face, bond.term, rate, ctx.turn)
            _add_flow(island, ledger.CB, face)
            rolled[bond.term] = rolled.get(bond.term, 0) + face
        for turns, amount in sorted(rolled.items()):
            term = _term_name(ctx.defs, turns)
            args: dict[str, Any] = {"amount": amount}
            if term is not None:
                args["term"] = term
            island.log.append(LogEntry("bonds_rolled" if term else "bonds_rolled_any", args))

    def _make_up(self, island: IslandState, ctx: StageContext, short: int) -> None:
        """借り換えの後もまだ払う額に足りなければ（外国の持ち手が入札を外れたときなど）、足りない分を
        中央銀行が自動の発行のいちばん短い期間で直接買い支える（不履行は命令で選んだときだけ）。"""
        if short <= 0:
            return
        terms = ctx.defs.bond_terms()
        names = island.policy.bond_terms or ctx.config.get("bonds.auto_terms")
        term = min(names, key=lambda t: terms[t]["turns"])
        turns = terms[term]["turns"]
        rate = ctx.formula("bond_yield")(island, turns, ctx)
        if debt.underwrite(island, short, turns, rate, ctx.turn) is None:
            return
        _add_flow(island, ledger.CB, short)
        stats = island.scratch.setdefault("bond_stats", {})
        stats["underwritten"] = stats.get("underwritten", 0) + short
        island.log.append(LogEntry("bonds_backstop", {"amount": short, "term": term}))

    def _pay(self, island: IslandState, ctx: StageContext, payer: Payer) -> None:
        unpaid: list[Bond] = []
        redeemed: dict[int, int] = {}  # 期間（ターン）→ 償還した額面
        for bond in sorted(island.bonds, key=lambda b: b.id):
            interest = debt.interest_due(island, bond, ctx.turn)
            mature = ctx.turn >= bond.maturity
            due = sum(interest.values()) + (debt.outstanding(island, bond) if mature else 0)
            if due > funds.available(island, payer):
                unpaid.append(bond)
                continue
            for holder, amount in sorted(interest.items()):
                if abroad.parse_holder(holder) is not None:  # 外国の持ち手（フェーズ3-3）
                    abroad.pay_interest(self._world(), island, payer, holder, amount)
                    continue
                funds.pay(island, payer, holder, amount, "bond_interest")
                _book_interest(island, holder, amount)
            if not mature:
                continue
            item = ledger.bond_item(bond.id)
            for holder, face in sorted(debt.holders(island, bond).items()):
                redeemed[bond.term] = redeemed.get(bond.term, 0) + face
                if abroad.parse_holder(holder) is not None:
                    abroad.redeem(self._world(), island, payer, holder, bond.id, face)
                    continue
                funds.pay(island, payer, holder, face, "bond_redemption")
                debt.released(island, holder, bond.id, face, face, face)
                ledger.cancel(island, holder, item, ledger.GOV, face, "bond_redemption")
                _add_flow(island, holder, -face)
        for turns, amount in sorted(redeemed.items()):
            term = _term_name(ctx.defs, turns)
            args: dict[str, Any] = {"amount": amount}
            if term is not None:
                args["term"] = term
            island.log.append(LogEntry("bonds_redeemed" if term else "bonds_redeemed_any", args))
        if unpaid:
            self._default(island, ctx, unpaid, "bond_default")
        island.bonds = [b for b in island.bonds if debt.outstanding(island, b) > 0]

    def _default(
        self, island: IslandState, ctx: StageContext, bonds: Sequence[Bond], kind: str
    ) -> None:
        """銘柄を不履行にする: 額面を切り下げ（持ち手の損。外国の持ち手も同じ）、残りを同じ期間で
        出し直す。

        罰として、銀行はしばらく国債を買わなくなり（market_closed）、損をした銀行は新しい融資を
        止める（economy["crunch_until"]）。どちらもログで知らせる。
        """
        config = ctx.config
        haircut = to_basis(config.get("bonds.haircut"))
        lost = 0
        hit: set[str] = set()  # 損をした銀行
        for bond in bonds:
            item = ledger.bond_item(bond.id)
            for holder, face in sorted(debt.holders(island, bond).items()):
                cut = part(face, haircut)
                if abroad.parse_holder(holder) is not None:
                    abroad.write_down(self._world(), island, holder, cut)
                else:
                    debt.released(island, holder, bond.id, cut, face, 0)
                ledger.cancel(island, holder, item, ledger.GOV, cut, "bond_default")
                lost += cut
                if cut > 0 and ledger.is_bank(holder):
                    hit.add(holder)
            bond.issued = ctx.turn
            bond.face = debt.outstanding(island, bond)
        finance = island.economy.finance
        finance["default_turn"] = ctx.turn
        top = to_basis(config.get("bonds.max_yield"))
        jump = to_basis(config.get("bonds.default_premium"))
        finance["yields"] = [min(top, y + jump) for y in finance.get("yields") or []]
        island.log.append(LogEntry(kind, {"amount": lost, "count": len(bonds)}))
        crunch = ctx.turn + config.get("banking.default_crunch_turns")
        if is_planned(ctx.defs, island):
            hit = set()  # 計画経済の国営銀行は政府の決めたとおりに貸す
        for bank in sorted(hit):
            f = banks.facility_of(island, bank)
            if f is not None:
                f.economy["crunch_until"] = max(crunch, f.economy.get("crunch_until", 0))
        args: dict[str, Any] = {  # 文面のターンは、まだ罰の続く最後のターン
            "turn": ctx.turn + config.get("bonds.default_exclusion_turns") - 1,
            "premium": round(config.get("bonds.default_premium") * 100, 2),
            "decay": config.get("bonds.default_decay_turns"),
        }
        if hit:
            args.update({"banks": len(hit), "crunch": crunch - 1})
        island.log.append(LogEntry("bond_default_penalty" + ("" if hit else "_no_banks"), args))

    # --- 発行 -------------------------------------------------------------------------

    def raise_funds(
        self, island: IslandState, ctx: StageContext, amount: int, kind: str = "bonds_issued"
    ) -> int:
        """amount を、自動の発行の期間の割合で分けて入札で発行する（端数は最後の期間に足す）。
        集まった額を返す。kind はログの種類（借り換えは bonds_refinanced）。

        市中銀行に政府の預金があれば、発行する前に先に引き出す（回5）。"""
        recalled = funds.recall(island, amount)
        amount -= recalled
        if amount <= 0:
            return recalled
        shares = sorted(island.policy.bond_terms.items()) or [
            (name, to_basis(share)) for name, share in ctx.config.get("bonds.auto_terms").items()
        ]
        total = sum(share for _, share in shares) or 1
        left = amount
        raised = 0
        for i, (name, share) in enumerate(shares):
            face = left if i == len(shares) - 1 else amount * share // total
            left -= face
            raised += self.issue(island, ctx, face, name, kind)
        return recalled + raised

    def issue(self, island: IslandState, ctx: StageContext, face: int, term: str, kind: str) -> int:
        """期間 term の国債 face を入札で出す。売れた額（中央銀行の引き受けを含む）を返す。"""
        if face <= 0:
            return 0
        turns = ctx.defs.bond_terms()[term]["turns"]
        wanted = ctx.formula("bond_yield")(island, turns, ctx)
        closed = debt.market_closed(island, ctx.config, ctx.turn)
        if (
            closed
        ):  # 債務不履行の後: 銀行は入札に来ない（利回りは不履行の上乗せの入った市場の利回り）
            filled, rate = {}, wanted
        else:
            filled, rate = self._auction(island, ctx, face, wanted, self._foreign_bids())
        sold = sum(filled.values())
        policy = island.policy
        underwrite = 0
        by_policy = policy.bond_unsold == UNDERWRITE and not policy.cb_independent
        refinancing = kind == "bonds_refinanced"
        if sold < face and (by_policy or refinancing):
            # 借り換えの売れ残りは、政策によらず中央銀行が買い支える（借り換えだけで不履行にしない）
            underwrite = face - sold
        unsold_kind = "bonds_unsold_closed" if closed else "bonds_unsold"
        unsold_args = {"turn": (debt.reopen_turn(island, ctx.config) or 0) - 1} if closed else {}
        if sold + underwrite <= 0:
            island.log.append(LogEntry(unsold_kind, {"amount": face, "term": term, **unsold_args}))
            return 0
        bond = debt.issue_bond(island, sold + underwrite, turns, rate, ctx.turn)
        item = ledger.bond_item(bond.id)
        abroad_bought = 0
        for bank, amount in sorted(filled.items()):
            if bank in self.bids:  # 外国の買い手（フェーズ3-3）
                abroad.buy_new(self._world(), island, bank, bond.id, amount)
                self.bids[bank].budget -= amount
                self.bids[bank].done += amount
                abroad_bought += amount
                continue
            ledger.create(island, bank, item, ledger.GOV, amount, "bond_issue")
            ledger.pay(island, bank, ledger.GOV, amount, "bond_issue")
            debt.bought(island, bank, bond.id, amount)
            _add_flow(island, bank, amount)
        if underwrite:
            ledger.create(island, ledger.CB, item, ledger.GOV, underwrite, "bond_issue")
            ledger.pay(island, ledger.CB, ledger.GOV, underwrite, "bond_issue")
            debt.bought(island, ledger.CB, bond.id, underwrite)
            _add_flow(island, ledger.CB, underwrite)
            stats = island.scratch.setdefault("bond_stats", {})
            stats["underwritten"] = stats.get("underwritten", 0) + underwrite
        if sold:
            self._set_yield(island, ctx, turns, rate)
        island.log.append(
            LogEntry(
                f"{kind}_closed" if closed else kind,
                {
                    "amount": sold + underwrite,
                    "term": term,
                    "rate": rate * 100 / BASIS,
                    "banks": sold,
                    "central_bank": underwrite,
                },
            )
        )
        if underwrite and not by_policy:
            island.log.append(LogEntry("bonds_backstop", {"amount": underwrite, "term": term}))
        if abroad_bought:
            island.log.append(
                LogEntry("bonds_bought_abroad", {"amount": abroad_bought, "term": term})
            )
            stats = island.scratch.setdefault("bond_stats", {})
            stats["abroad"] = stats.get("abroad", 0) + abroad_bought
        if sold + underwrite < face:
            island.log.append(
                LogEntry(
                    unsold_kind, {"amount": face - sold - underwrite, "term": term, **unsold_args}
                )
            )
        return sold + underwrite

    def _foreign_bids(self) -> list[tuple[int, str, int]]:
        """入札に加わる外国の買い手の (上乗せ, 名前, 額)。政府と中央銀行は指値なし（上乗せ -1。一番
        低い段）、市中銀行は値下がりの率を上乗せした利回り（発行した島の bond_yield ＋ 上乗せ）。"""
        return [
            (-1 if bid.premium is None else bid.premium, name, bid.budget)
            for name, bid in sorted(self.bids.items())
            if bid.budget > 0
        ]

    @staticmethod
    def _auction(
        island: IslandState,
        ctx: StageContext,
        face: int,
        wanted: int,
        foreign: Sequence[tuple[int, str, int]] = (),
    ) -> tuple[dict[str, int], int]:
        """入札: 買い手ごとの落札額と、落札の利回り（売れなければ一番高い段の利回り）。

        foreign は外国の買い手の (求める利回りの上乗せ, 名前, 額)。上乗せが負なら指値なしで、一番
        低い段（wanted）に入れる（フェーズ3-3）。"""
        config = ctx.config
        steps = config.get("banking.auction_steps")
        top = to_basis(config.get("bonds.max_yield"))
        free, premium = concentration(ctx, island)
        bids: dict[int, dict[str, int]] = {}
        for bank in banks.active_banks(island):
            budget = auction_budget(island, ctx, bank)
            assets = bond_assets(island, bank)
            if budget <= 0 or assets <= 0:
                continue
            held = sum(debt.holdings_of(island, bank).values())
            amount = budget // steps
            for step in range(1, steps + 1):
                if amount <= 0:
                    break
                # 段の終わりまで買ったときの偏りで求める利回り
                level = bank_yield(wanted, (held + amount * step) / assets, free, premium, top)
                by_bank = bids.setdefault(level, {})
                by_bank[bank] = by_bank.get(bank, 0) + amount
        for premium, name, amount in foreign:
            level = wanted if premium < 0 else min(top, wanted + premium)
            if amount > 0:
                by_name = bids.setdefault(level, {})
                by_name[name] = by_name.get(name, 0) + amount
        filled: dict[str, int] = {}
        left = face
        rate = max(bids) if bids else wanted
        for level in sorted(bids):
            if left <= 0:
                break
            group = bids[level]
            total = sum(group.values())
            rate = level
            if total <= left:
                for bank, amount in group.items():
                    filled[bank] = filled.get(bank, 0) + amount
                left -= total
                continue
            # 同じ利回りの注文は量に比例して分け、割り切れない分は銀行の名前の順に1ずつ足す
            shares = {bank: amount * left // total for bank, amount in sorted(group.items())}
            rest = left - sum(shares.values())
            for bank in sorted(group):
                if rest <= 0:
                    break
                if shares[bank] < group[bank]:
                    shares[bank] += 1
                    rest -= 1
            for bank, amount in shares.items():
                filled[bank] = filled.get(bank, 0) + amount
            left = 0
        return {b: a for b, a in filled.items() if a > 0}, rate

    def _ordered(self, island: IslandState, ctx: StageContext) -> None:
        """命令で決めた追加の発行と、中央銀行の直接引き受け（このターンだけ）。"""
        finance = island.economy.finance
        extra = finance.pop("extra_issue", None)
        if extra:
            self.issue(island, ctx, extra["amount"], extra["term"], "bonds_issued")
        direct = finance.pop("underwrite_order", None)
        if direct and not island.policy.cb_independent:
            term = direct["term"]
            turns = ctx.defs.bond_terms()[term]["turns"]
            rate = ctx.formula("bond_yield")(island, turns, ctx)
            bond = debt.underwrite(island, direct["amount"], turns, rate, ctx.turn)
            if bond is not None:
                _add_flow(island, ledger.CB, direct["amount"])
                stats = island.scratch.setdefault("bond_stats", {})
                stats["underwritten"] = stats.get("underwritten", 0) + direct["amount"]
                island.log.append(
                    LogEntry(
                        "bonds_underwritten",
                        {"amount": direct["amount"], "term": term, "rate": rate * 100 / BASIS},
                    )
                )

    # --- 国債の売買の市場 -------------------------------------------------------------

    def _secondary(self, island: IslandState, ctx: StageContext) -> None:
        config = ctx.config
        edges = config.get("bonds.buckets")
        terms = config.get("bonds.bucket_terms")
        count = len(edges) + 1
        yields = island.economy.finance["yields"]
        by_id = {b.id: b for b in island.bonds}
        # 区分ごとの (注文, 出した者, 売りか)。売りは板寄せの買い（受け入れる一番高い利回り）
        sellers: list[list[tuple[Order, str]]] = [[] for _ in range(count)]
        buyers: list[list[tuple[Order, str]]] = [[] for _ in range(count)]
        holdings = {
            holder: self._by_bucket(island, holder, ctx.turn, edges, by_id)
            for holder in [*banks.active_banks(island), ledger.CB]
        }
        wanted = [ctx.formula("bond_yield")(island, t, ctx) for t in terms]
        premium = to_basis(config.get("banking.sell_premium"))
        top = to_basis(config.get("bonds.max_yield"))
        free, slope = concentration(ctx, island)
        closed = debt.market_closed(island, config, ctx.turn)  # 不履行の後は、銀行は買わない
        for bank in banks.active_banks(island):
            held = holdings[bank]
            total = sum(held)
            short = -banks.excess_reserves(island, config, bank)
            assets = bond_assets(island, bank)
            share = total / assets if assets > 0 else 0.0
            # 区分ごとに、自分の偏りで求める利回りと、今の利回りで持ちたい額面
            asks = [bank_yield(wanted[k], share, free, slope, top) for k in range(count)]
            target = (
                sum(target_holding(assets, yields[k], wanted[k], free, slope) for k in range(count))
                // count
            )
            if short > 0 and total > 0:  # 準備預金が足りない: 指値なしで売る
                for k in range(count):
                    qty = min(held[k], -(-short * held[k] // total))
                    if qty > 0:
                        sellers[k].append((Order(qty), bank))
            elif total > target * 11 // 10 and total > 0:
                over = total - target
                for k in range(count):
                    qty = min(held[k], over * held[k] // total)
                    if qty > 0:
                        sellers[k].append((Order(qty, min(top, asks[k] + premium)), bank))
            elif total < target * 9 // 10 and not closed:
                room = auction_budget(island, ctx, bank)
                qty = min(target - total, room) // count
                if qty > 0:
                    for k in range(count):
                        buyers[k].append((Order(qty, asks[k]), bank))
        trade = island.policy.cb_bond_trade
        if trade and not island.policy.cb_independent:
            held = holdings[ledger.CB]
            total = sum(held)
            if trade > 0:
                for k in range(count):
                    buyers[k].append((Order(trade // count), ledger.CB))
            elif total > 0:
                for k in range(count):
                    qty = min(held[k], -trade * held[k] // total)
                    if qty > 0:
                        sellers[k].append((Order(qty), ledger.CB))
        self._foreign_orders(island, ctx, sellers, buyers, wanted, closed, by_id)
        band = to_basis(config.get("bonds.trade_band"))
        top = to_basis(config.get("bonds.max_yield"))
        cb_done = 0  # 中央銀行が売り買いした額面
        for k in range(count):
            if not sellers[k] or not buyers[k]:
                continue
            ref = yields[k]
            bounds = (max(0, ref - band), min(top, ref + band))
            # 板寄せの値段は利回り: 国債を売る者は「この利回りまでなら売る」買い注文、
            # 買う者は「この利回り以上なら買う」売り注文
            result = auction.clear(
                [o for o, _ in sellers[k]], [o for o, _ in buyers[k]], ref, bounds=bounds
            )
            if result.volume <= 0:
                continue
            yields[k] = result.price
            for s, b, qty in auction.match(result.bought, result.sold):
                seller, buyer = sellers[k][s][1], buyers[k][b][1]
                done = self._deliver(island, ctx, seller, buyer, qty, k, result.price, by_id)
                if ledger.CB in (seller, buyer):
                    cb_done += done
            stats = island.scratch.setdefault("bond_stats", {})
            stats["traded"] = stats.get("traded", 0) + result.volume
        if trade and not island.policy.cb_independent:
            self._log_cb_trade(island, trade, cb_done)

    @staticmethod
    def _log_cb_trade(island: IslandState, trade: int, done: int) -> None:
        """中央銀行の国債の売り買い（政策 cb_bond_trade）の結果をログと統計に残す。

        相手は売り買いしたい銀行だけなので、決めた額に届かないことがある（届かなかったわけも出す）。
        """
        stats = island.scratch.setdefault("bond_stats", {})
        stats["cb_bought" if trade > 0 else "cb_sold"] = done
        kind = "cb_bonds_bought" if trade > 0 else "cb_bonds_sold"
        if done < abs(trade):
            kind += "_short"
        island.log.append(LogEntry(kind, {"amount": done, "target": abs(trade)}))

    @staticmethod
    def _by_bucket(
        island: IslandState, holder: str, turn: int, edges: Sequence[int], by_id: dict[int, Bond]
    ) -> list[int]:
        out = [0] * (len(edges) + 1)
        for number, face in debt.holdings_of(island, holder).items():
            bond = by_id.get(number)
            if bond is not None and face > 0:
                out[debt.bucket_of(debt.remaining(bond, turn), edges)] += face
        return out

    def _foreign_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        sellers: list[list[tuple[Order, str]]],
        buyers: list[list[tuple[Order, str]]],
        wanted: list[int],
        closed: bool,
        by_id: dict[int, Bond],
    ) -> None:
        """外国の持ち手の売り買い（フェーズ3-3）。政府と中央銀行の売りは、持っている区分の割合で
        指値なし。買いは区分に等しく分けて、政府と中央銀行は指値なし、市中銀行は求める利回り
        （bond_yield ＋ 値下がりの率）を指値にする。債務不履行の後（closed）は買わない。"""
        edges = ctx.config.get("bonds.buckets")
        count = len(edges) + 1
        top = to_basis(ctx.config.get("bonds.max_yield"))
        for name, bid in sorted(self.bids.items()):
            if bid.sell > 0:
                held = self._by_bucket(island, name, ctx.turn, edges, by_id)
                total = sum(held)
                for k in range(count):
                    qty = min(held[k], bid.sell * held[k] // total) if total else 0
                    if qty > 0:
                        sellers[k].append((Order(qty), name))
            elif bid.budget > 0 and not closed:
                qty = bid.budget // count
                for k in range(count):
                    if qty <= 0:
                        break
                    limit = None if bid.premium is None else min(top, wanted[k] + bid.premium)
                    buyers[k].append((Order(qty, limit), name))

    def _deliver(
        self,
        island: IslandState,
        ctx: StageContext,
        seller: str,
        buyer: str,
        qty: int,
        bucket: int,
        rate: int,
        by_id: dict[int, Bond],
    ) -> int:
        """売り手が区分 bucket の銘柄を満期の近い順に額面 qty だけ渡し、買い手が時価で払う。渡した
        額面を返す（外国の買い手は、持っている外貨で払える分まで）。"""
        edges = ctx.config.get("bonds.buckets")
        left = qty
        held = sorted(
            (
                (by_id[n].maturity, n, face)
                for n, face in debt.holdings_of(island, seller).items()
                if n in by_id
                and debt.bucket_of(debt.remaining(by_id[n], ctx.turn), edges) == bucket
            )
        )
        paying = self.bids.get(buyer)
        for _, number, face in held:
            if left <= 0:
                break
            price = debt.price(by_id[number], ctx.turn, rate)
            take = min(face, left)
            if paying is not None:
                take = min(take, int(paying.budget / price) if price > 0 else take)
                if take <= 0:
                    break
            left -= take
            amount = int(take * price)
            self._move(island, number, seller, buyer, take, amount)
            if paying is not None:
                paying.budget -= amount
                paying.done += take
            if seller in self.bids:
                self.bids[seller].done += take
        return qty - left

    def _move(
        self, island: IslandState, number: int, seller: str, buyer: str, face: int, amount: int
    ) -> None:
        """銘柄 number の額面 face を売り手から買い手へ移し、代金 amount を払う。外国の持ち手が
        いれば、代金はその島の外貨の持ち高から・へ動く（hakoniwa_core/trade/abroad.py）。"""
        item = ledger.bond_item(number)
        abroad_seller = abroad.parse_holder(seller) is not None
        abroad_buyer = abroad.parse_holder(buyer) is not None
        if not abroad_seller:
            held = ledger.balance(island, ledger.account(seller, item, ledger.GOV))
            debt.released(island, seller, number, face, held, amount)
            _add_flow(island, seller, -amount)
        if not abroad_buyer:
            debt.bought(island, buyer, number, amount)
            _add_flow(island, buyer, amount)
        ledger.transfer(island, item, ledger.GOV, seller, buyer, face, "bond_trade")
        if abroad_seller or abroad_buyer:
            abroad.trade_payment(self._world(), island, seller, buyer, face, amount)
        else:
            ledger.pay(island, buyer, seller, amount, "bond_trade")

    # --- 記録 -------------------------------------------------------------------------

    @staticmethod
    def record_stats(island: IslandState) -> None:
        """利回りと、このターンの引き受け・売買・中央銀行の保有の統計。破綻の段が救済のために
        銀行を処理したら、もう一度取る（救済のための発行が、そのターンの統計から抜けていた）。"""
        stats = island.stats
        for k, rate in enumerate(island.economy.finance.get("yields") or []):
            stats[f"yield.{k}"] = rate * 100 / BASIS
        extra = island.scratch.get("bond_stats", {})
        stats["bonds.underwritten"] = extra.get("underwritten", 0)
        stats["bonds.traded"] = extra.get("traded", 0)
        stats["bonds.cb_bought"] = extra.get("cb_bought", 0)
        stats["bonds.cb_sold"] = extra.get("cb_sold", 0)
        stats["bonds.central_bank"] = sum(debt.holdings_of(island, ledger.CB).values())
        stats["bonds.bought_abroad"] = extra.get("abroad", 0)


def _log_foreign(world: WorldState, foreign: dict[int, list[abroad.Bid]]) -> None:
    """政府と中央銀行の外国の国債の売り買い（命令 set_foreign_bonds）の結果を、持ち手の島のログに
    出す（決めた額に届かなかったら、そのことも）。"""
    for issuer_id in sorted(foreign):
        for bid in foreign[issuer_id]:
            if not bid.target or ledger.is_bank(bid.entity):
                continue
            holder = world.islands[bid.holder]
            kind = "foreign_bonds_sold" if bid.sell else "foreign_bonds_bought"
            if bid.done < bid.target:
                kind += "_short"
            args = {
                "holder": f"bond_holder_{bid.entity}",
                "issuer": world.islands[issuer_id].name,
                "currency": currency_label(world, issuer_id),
                "amount": bid.done,
                "target": bid.target,
            }
            holder.log.append(LogEntry(kind, args))


def _term_name(defs: Defs, turns: int) -> str | None:
    """期間 turns（ターン）の bond_term の名前（定義になければ None）。"""
    for name, spec in sorted(defs.bond_terms().items()):
        if spec["turns"] == turns:
            return name
    return None


def _book_interest(island: IslandState, holder: str, amount: int) -> None:
    """受け取った国債の利子を、中央銀行か銀行の損益に入れる。"""
    if holder == ledger.CB:
        books.add_sector(island, books.CENTRAL_BANK, "interest", amount)
    elif ledger.is_bank(holder):
        books.add(island, banks.bank_number(holder), books.INTEREST, amount)


# --- 命令 -------------------------------------------------------------------------


def whole_terms(island: IslandState) -> None:
    """期間の割合に小数が入っている島（夜の点検 2026-10-17 の前の set_bond_policy の命令）の割合を
    整数に直す（小数のままだと、発行額から国庫・工事・配給まで小数が広がって止まる）。"""
    terms = island.policy.bond_terms
    if any(type(share) is not int for share in terms.values()):
        island.policy.bond_terms = {name: round(share) for name, share in sorted(terms.items())}


def check_bond_policy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    terms = defs.bond_terms()
    shares = payload.get("terms")
    if shares is not None and (not shares or any(t not in terms for t in shares)):
        raise OrderFailed("reason_invalid_payload")
    # NaN・無限大と、足すと無限大になる大きさも断る（JSON の API は NaN と Infinity も通す。割合に
    # できず、ターンの処理が止まる）
    total = sum(shares.values()) if shares is not None else 1
    if not (math.isfinite(total) and total > 0):
        raise OrderFailed("reason_invalid_payload")
    for key in ("issue_term", "underwrite_term"):
        if payload.get(key) is not None and payload[key] not in terms:
            raise OrderFailed("reason_invalid_payload")
    if payload.get("underwrite") and island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")
    if payload.get("unsold") == UNDERWRITE and island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")


def set_bond_policy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """自動の発行の期間の割合、売れ残りの扱い、追加の発行、中央銀行の直接引き受け（額は今のターンだけ）。

    ログには、命令で変えた項目だけを出す（期間の割合と売れ残りの扱いは、前の値も）。
    """
    policy = island.policy
    finance = island.economy.finance
    terms = ctx.defs.bond_terms()
    default_term = min(
        policy.bond_terms or ctx.config.get("bonds.auto_terms"), key=lambda t: terms[t]["turns"]
    )

    def shares() -> list[list[Any]]:
        now = policy.bond_terms or {
            name: to_basis(share) for name, share in ctx.config.get("bonds.auto_terms").items()
        }
        return [
            [name, share * 100 // BASIS]
            for name, share in sorted(now.items(), key=lambda kv: terms[kv[0]]["turns"])
        ]

    if payload.get("terms"):
        before = shares()
        total = sum(payload["terms"].values())
        # 割合は整数にする（33.3 のような小数が入ると、発行額から国庫・工事・配給まで小数が広がって
        # 数ターン後に止まった。X870 の夜の点検 2026-10-17）
        rounded = {name: round(share / total * BASIS) for name, share in payload["terms"].items()}
        policy.bond_terms = {name: share for name, share in sorted(rounded.items()) if share > 0}
        island.log.append(LogEntry("bond_terms_set", {"terms_before": before, "terms": shares()}))
    if payload.get("unsold") in (UNDERWRITE, CUT):
        before = policy.bond_unsold
        policy.bond_unsold = payload["unsold"]
        finance.pop("unsold_before", None)  # 命令で決めたら、独立をやめたときに戻さない
        island.log.append(
            LogEntry(
                "bond_unsold_set",
                {
                    "unsold_before": f"bond_unsold_{before}",
                    "unsold": f"bond_unsold_{policy.bond_unsold}",
                },
            )
        )
    for key, kind, slot in (
        ("issue", "bond_issue_ordered", "extra_issue"),
        ("underwrite", "bond_underwrite_ordered", "underwrite_order"),
    ):
        if payload.get(key):
            term = payload.get(f"{key}_term") or default_term
            finance[slot] = {"amount": int(payload[key]), "term": term}
            island.log.append(LogEntry(kind, {"term": term, "amount": int(payload[key])}))


def check_bond_payment(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["stop"] and not island.bonds:
        raise OrderFailed("reason_no_bonds")


def set_bond_payment(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国債の利払いと償還を止める（このターンの国債の段で、すべての銘柄を不履行にする）・取り消す。"""
    finance = island.economy.finance
    if payload["stop"]:
        finance["default_order"] = True
        island.log.append(LogEntry("bond_payment_stopped", {}))
    else:
        finance.pop("default_order", None)
        island.log.append(LogEntry("bond_payment_resumed", {}))


def register(registry: Registry) -> None:
    registry.add_stage(Bonds())
    registry.add_formula("bond_yield", bond_yield)
    registry.add_order(
        OrderHandler("set_bond_policy", execute=set_bond_policy, check=check_bond_policy)
    )
    registry.add_order(
        OrderHandler("set_bond_payment", execute=set_bond_payment, check=check_bond_payment)
    )
