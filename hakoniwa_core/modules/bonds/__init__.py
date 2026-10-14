"""国債の段（フェーズ2-2）: 利払いと償還（払えなければ債務不履行）、入札と売れ残りの引き受け、
国債の売買の市場。

1. 区分ごとの利回り（economy.finance["yields"]）を、前のターンからの政策金利の動きだけ動かす。
2. 命令 set_bond_payment で支払いを止めていれば、すべての銘柄を不履行にする。
3. 今のターンの利払いと償還に国庫が足りなければ、足りない分を入札で発行する（借り換え）。
4. 銘柄ごとに利子を払い、満期の銘柄は額面を返して消す。国庫が足りずに払えない銘柄は不履行にする:
   額面を bonds.haircut だけ切り下げ（持ち手の損）、残りを同じ期間で出し直す。不履行の記録は
   利回りに δ を上乗せし（bonds.default_premium。bonds.default_decay_turns ターンかけて 0 に減る）、
   区分の利回りも同じだけ跳ね上がる。不履行から bonds.default_exclusion_turns ターンは、銀行が国債
   を買わない（入札で売れず、売れ残りは政策どおり中央銀行が引き受けるか支出を減らす。売買の市場でも
   買わない）。損をした銀行は banking.default_crunch_turns ターン新しい融資を止める
   （economy/banks.py の lending_stopped。計画経済の島を除く）。
5. 命令で決めた中央銀行の直接引き受けと追加の発行。
6. 国債の売買の市場（区分ごとの板寄せ）。
7. 「このターンの国債以外の政府の支出（次のターンの見込み）× (1 + bonds.buffer_turns) ＋ 次のターン
   の利払い − 国庫の残高」がプラスなら、その額を入札で発行する。期間は政策の割合
   （policy.bond_terms）で分ける。

入札: 買い手は市中銀行です。各銀行は準備預金の余りの banking.auction_share を、求める利回り
（式 bond_yield）と、それに banking.auction_tiers を足した利回りの3段に分けて注文し、発行額が売れる
いちばん低い利回りで全員が買います（その利回りが銘柄の利率）。売れ残りは、政策（policy.bond_unsold）が
「引き受ける」で中央銀行が独立していなければ中央銀行が同じ利回りで引き受け、そうでなければ発行
しません（足りない支出は支払いの順番で削られる）。

国債の売買の市場: 残りの期間で分けた区分ごとに、利回りを値段にした板寄せで売り買いします（値幅は前の
ターンの利回り ± bonds.trade_band）。市中銀行は国債の時価を目安（預金 × banking.bond_ratio）に近づけ
るように注文し（売りは求める利回り ＋ banking.sell_premium まで受け入れ、買いは求める利回り以上）、
準備預金が最低限に足りない銀行は足りない分を指値なしで売ります。独立していない中央銀行は、政策
（policy.cb_bond_trade）の額を指値なしで買う・売ります。約定した区分の利回りは約定した利回りになりま
す。銘柄の代金は、約定した利回りで割り引いた値段です（hakoniwa_core/economy/debt.py の price）。

決算の段より前に置くのは、受け取った利子と、売った・償還された国債の損益を、同じターンの損益に
入れるためです。国債の持ち手ごとの「時価の評価の元になる額」の出入り（買った代金・売った代金・償還）は
island.scratch["bond_flows"] に残します（決算の段の評価に使う）。取得原価と実現損益の記録は
hakoniwa_core/economy/debt.py の bought・released・traded です。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import BONDS, Payer, banks, books, debt, funds
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.banking import is_planned
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import Bond, IslandState, LogEntry
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
    """買い手が求める国債の利回り（1万分の1）:
    r_P + λ_T + σ max(0, B/Y − 閾値) + κ max(0, π − π*) + δ − β。

    λ_T は期間 turns の上乗せ、B/Y は政府の借金の対年GDP比、δ は過去の債務不履行の上乗せ、β は
    中央銀行が独立していれば bonds.independence_discount。0 から bonds.max_yield の間に収める。
    """
    settings = ctx.config.get("bonds")
    rate = island.policy.policy_rate / BASIS + term_premium(ctx.defs, turns)
    rate += settings["sigma"] * max(0.0, debt.debt_ratio(island) - settings["debt_threshold"])
    target = ctx.config.get("central_bank.inflation_target")
    rate += settings["kappa"] * max(0.0, inflation(island.economy) - target)
    rate += default_premium(island, ctx)
    if island.policy.cb_independent:
        rate -= settings["independence_discount"]
    return to_basis(max(0.0, min(settings["max_yield"], rate)))


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


class Bonds(IslandStage):
    name = "bonds"

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
        short = debt.due(island, ctx.turn) - funds.available(island, payer)
        if short > 0:
            self.raise_funds(island, ctx, short, "bonds_refinanced")
        self._pay(island, ctx, payer)
        self._ordered(island, ctx)
        self._secondary(island, ctx)
        self.top_up(island, ctx)
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
        if spent is None:
            spent = funds.spent(island)
        spending = sum(v for k, v in spent.items() if k != BONDS)
        buffer = ctx.config.get("bonds.buffer_turns")
        need = spending * (1 + buffer) + debt.due(island, ctx.turn + 1, redemptions=False)
        need -= ledger.money(island, ledger.GOV)
        if need > 0:
            self.raise_funds(island, ctx, need)

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
                funds.pay(island, payer, holder, amount, "bond_interest")
                _book_interest(island, holder, amount)
            if not mature:
                continue
            item = ledger.bond_item(bond.id)
            for holder, face in sorted(debt.holders(island, bond).items()):
                funds.pay(island, payer, holder, face, "bond_redemption")
                debt.released(island, holder, bond.id, face, face, face)
                ledger.cancel(island, holder, item, ledger.GOV, face, "bond_redemption")
                _add_flow(island, holder, -face)
                redeemed[bond.term] = redeemed.get(bond.term, 0) + face
        for turns, amount in sorted(redeemed.items()):
            term = _term_name(ctx.defs, turns)
            args: dict[str, Any] = {"amount": amount}
            if term is not None:
                args["term"] = term
            island.log.append(LogEntry("bonds_redeemed" if term else "bonds_redeemed_any", args))
        if unpaid:
            self._default(island, ctx, unpaid, "bond_default")
        island.bonds = [b for b in island.bonds if debt.outstanding(island, b) > 0]

    @staticmethod
    def _default(island: IslandState, ctx: StageContext, bonds: Sequence[Bond], kind: str) -> None:
        """銘柄を不履行にする: 額面を切り下げ（持ち手の損）、残りを同じ期間で出し直す。

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
        集まった額を返す。kind はログの種類（借り換えは bonds_refinanced）。"""
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
        return raised

    def issue(self, island: IslandState, ctx: StageContext, face: int, term: str, kind: str) -> int:
        """期間 term の国債 face を入札で出す。売れた額（中央銀行の引き受けを含む）を返す。"""
        if face <= 0:
            return 0
        turns = ctx.defs.bond_terms()[term]["turns"]
        wanted = ctx.formula("bond_yield")(island, turns, ctx)
        closed = debt.market_closed(island, ctx.config, ctx.turn)
        if closed:  # 債務不履行の後: 銀行は入札に来ない（利回りは入札のいちばん高い段）
            top = to_basis(ctx.config.get("bonds.max_yield"))
            tiers = ctx.config.get("banking.auction_tiers")
            filled, rate = {}, min(top, wanted + max(to_basis(t) for t in tiers))
        else:
            filled, rate = self._auction(island, ctx, face, wanted)
        sold = sum(filled.values())
        policy = island.policy
        underwrite = 0
        if sold < face and policy.bond_unsold == UNDERWRITE and not policy.cb_independent:
            underwrite = face - sold
        unsold_kind = "bonds_unsold_closed" if closed else "bonds_unsold"
        unsold_args = {"turn": (debt.reopen_turn(island, ctx.config) or 0) - 1} if closed else {}
        if sold + underwrite <= 0:
            island.log.append(LogEntry(unsold_kind, {"amount": face, "term": term, **unsold_args}))
            return 0
        bond = debt.issue_bond(island, sold + underwrite, turns, rate, ctx.turn)
        item = ledger.bond_item(bond.id)
        for bank, amount in sorted(filled.items()):
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
        if sold + underwrite < face:
            island.log.append(
                LogEntry(
                    unsold_kind, {"amount": face - sold - underwrite, "term": term, **unsold_args}
                )
            )
        return sold + underwrite

    @staticmethod
    def _auction(
        island: IslandState, ctx: StageContext, face: int, wanted: int
    ) -> tuple[dict[str, int], int]:
        """入札: 銀行ごとの落札額と、落札の利回り（売れなければ一番高い段の利回り）。"""
        config = ctx.config
        tiers = [to_basis(t) for t in config.get("banking.auction_tiers")]
        share = to_basis(config.get("banking.auction_share"))
        top = to_basis(config.get("bonds.max_yield"))
        bids: dict[int, dict[str, int]] = {}
        for bank in banks.active_banks(island):
            budget = part(max(0, banks.excess_reserves(island, config, bank)), share)
            for tier in tiers:
                level = min(top, wanted + tier)
                amount = budget // len(tiers)
                if amount > 0:
                    by_bank = bids.setdefault(level, {})
                    by_bank[bank] = by_bank.get(bank, 0) + amount
        filled: dict[str, int] = {}
        left = face
        rate = min(top, wanted + max(tiers))
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
        closed = debt.market_closed(island, config, ctx.turn)  # 不履行の後は、銀行は買わない
        for bank in banks.active_banks(island):
            held = holdings[bank]
            total = sum(held)
            short = -banks.excess_reserves(island, config, bank)
            target = part(
                banks.deposits_of(island, bank), to_basis(config.get("banking.bond_ratio"))
            )
            value = banks.bond_value(island, config, bank, ctx.turn)
            if short > 0 and total > 0:  # 準備預金が足りない: 指値なしで売る
                for k in range(count):
                    qty = min(held[k], -(-short * held[k] // total))
                    if qty > 0:
                        sellers[k].append((Order(qty), bank))
            elif value > target * 11 // 10 and total > 0:
                over = value - target
                for k in range(count):
                    qty = min(held[k], over * held[k] // total)
                    if qty > 0:
                        sellers[k].append((Order(qty, wanted[k] + premium), bank))
            elif value < target * 9 // 10 and not closed:
                room = part(max(0, banks.excess_reserves(island, config, bank)), BASIS // 2)
                qty = min(target - value, room) // count
                if qty > 0:
                    for k in range(count):
                        buyers[k].append((Order(qty, wanted[k]), bank))
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
                self._deliver(island, ctx, seller, buyer, qty, k, result.price, by_id)
                if ledger.CB in (seller, buyer):
                    cb_done += qty
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

    @staticmethod
    def _deliver(
        island: IslandState,
        ctx: StageContext,
        seller: str,
        buyer: str,
        qty: int,
        bucket: int,
        rate: int,
        by_id: dict[int, Bond],
    ) -> None:
        """売り手が区分 bucket の銘柄を満期の近い順に額面 qty だけ渡し、買い手が時価で払う。"""
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
        for _, number, face in held:
            if left <= 0:
                break
            take = min(face, left)
            left -= take
            amount = int(take * debt.price(by_id[number], ctx.turn, rate))
            debt.traded(island, number, seller, buyer, take, amount)
            ledger.transfer(
                island, ledger.bond_item(number), ledger.GOV, seller, buyer, take, "bond_trade"
            )
            ledger.pay(island, buyer, seller, amount, "bond_trade")
            _add_flow(island, buyer, amount)
            _add_flow(island, seller, -amount)

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


def check_bond_policy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    terms = defs.bond_terms()
    shares = payload.get("terms")
    if shares is not None and (not shares or any(t not in terms for t in shares)):
        raise OrderFailed("reason_invalid_payload")
    if shares is not None and sum(shares.values()) <= 0:
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
        policy.bond_terms = {
            name: share * BASIS // total
            for name, share in sorted(payload["terms"].items())
            if share
        }
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
