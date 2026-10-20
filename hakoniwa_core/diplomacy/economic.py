"""経済の取り決めの条項（フェーズ4-1 の②。docs/design/10-politics.md PL-45〜PL-50）。

貿易圏・二国間契約・固定相場・中央銀行の融通・お金の援助・移民の受け入れの 6 つの取り決めを、条約の
条項として結べるようにします。今ある命令（貿易圏をつくる・契約を申し込む・固定相場を決める・援助を送る・
移民の政策を決める）は変えません。条項は、両国の署名で効くと、それぞれの仕組みに「約束」を置きます。
ゲームは約束を強いも止めもせず（PL-37）、破ると違反として記録して信用の傷にします（credit.py）。

- 効き始め（on_force）: 約束どおりの状態にします（圏をつくる・契約を始める・固定相場にする・移民の
  受け入れを開ける）
- 毎ターンの見張り（watch）: 約束が守られているかを、外交の段で見ます。破っていれば、破った国の
  違反として記録します。破棄を予告したあとの条約は、約束を守る必要がありません（予告どおりの破棄は
  罰なし）
- 済む条項（契約・援助）は、期間か回数を終えると done になり、条項がすべて済んだ条約は終わります

状態を見張る条項（貿易圏・固定相場・移民）は、守れている状態から守れていない状態に変わったときに
1 回だけ違反にします（元に戻して、また破ると、もう 1 回）。契約の不足は、同じ条項で 1 回記録したら
diplomacy.violation_repeat_turns ターンは重ねません。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.state import LogEntry, WorldState
from hakoniwa_core.units import BASIS, QUANTITY_SCALE

# 移民の受け入れの政策が約束を満たす値（island.policy.immigration。"" は受け入れない）
ACCEPTS = {cl.FREE_MOVEMENT: ("open",), cl.REFUGEES: ("open", "refugees")}
OPEN_TO = {cl.FREE_MOVEMENT: "open", cl.REFUGEES: "refugees"}
DELIVERIES = ("market", "warehouse")
# 契約の不足のうち、違反にするもの（輸送や港や禁輸は約束した国のせいとは言えない）
SHORT_BY_SELLER = ("goods",)
SHORT_BY_BUYER = ("money",)  # 外貨が買えなかった（為替の市場の厚み）のは買う国のせいにしない


def clean(
    out: dict[str, Any], clause: dict[str, Any], a: int, b: int, config: GameConfig
) -> tuple[dict[str, Any] | None, str | None]:
    """命令で書かれた経済の条項を、保存する形にそろえる。(条項, None) か (None, 断るわけ)。"""
    kind = out["type"]
    cfg = config.get("diplomacy")
    other = {a: b, b: a}
    if kind == cl.BLOC:
        bloc = clause.get("bloc")
        name = str(clause.get("name") or "").strip()[:20]
        if bloc is None and not name:
            return None, "reason_clause_bloc"
        out.update(bloc=bloc, name=name, open=bool(clause.get("open", True)), bloc_id=None)
    elif kind == cl.CONTRACT:
        seller = clause.get("seller")
        good, quantity, price = clause.get("good"), clause.get("quantity"), clause.get("price")
        turns = int(clause.get("turns", 0))
        if seller not in (a, b) or not isinstance(good, str) or not good:
            return None, "reason_clause_contract"
        if not isinstance(quantity, int | float) or round(quantity * QUANTITY_SCALE) < 1:
            return None, "reason_clause_contract"
        if not isinstance(price, int) or price < 0:
            return None, "reason_clause_contract"
        if not 1 <= turns <= cfg["max_contract_turns"]:
            return None, "reason_clause_contract"
        currency = clause.get("currency", seller)
        if currency not in (a, b):
            return None, "reason_contract_currency"
        delivery = clause.get("delivery") or "market"
        if delivery not in DELIVERIES:
            return None, "reason_clause_contract"
        out.update(
            seller=seller,
            buyer=other[seller],
            good=good,
            quantity=quantity,
            price=price,
            currency=currency,
            turns=turns,
            delivery=delivery,
            contract=None,
            done=False,
            cancel_flagged=False,
            last_violation=None,
        )
    elif kind == cl.PEG:
        pegger, rate = clause.get("pegger"), clause.get("rate")
        if pegger not in (a, b) or not isinstance(rate, int | float) or round(rate * BASIS) <= 0:
            return None, "reason_clause_peg"
        out.update(pegger=pegger, currency=other[pegger], rate=rate, rate_basis=round(rate * BASIS))
    elif kind == cl.AID:
        payer = clause.get("payer")
        amount = int(clause.get("amount", 0))
        period, count = int(clause.get("period", 1)), int(clause.get("count", 1))
        if payer not in (a, b) or amount <= 0:
            return None, "reason_clause_aid"
        if not 1 <= period <= cfg["max_aid_period_turns"] or not 1 <= count <= cfg["max_aid_count"]:
            return None, "reason_clause_aid"
        currency = clause.get("currency", payer)
        if currency not in (a, b):
            return None, "reason_contract_currency"
        out.update(
            payer=payer,
            payee=other[payer],
            currency=currency,
            amount=amount,
            period=period,
            count=count,
            start=None,
            paid=0,
            forgiven=0,
            done_count=0,
            done=False,
        )
    elif kind == cl.REFUGEES:
        host = clause.get("host")
        if host not in (a, b):
            return None, "reason_clause_refugees"
        out["host"] = host
    if kind in (cl.BLOC, cl.PEG, cl.FREE_MOVEMENT, cl.REFUGEES):
        out["ok"] = {}
    return out, None


def check(world: WorldState, clause: dict[str, Any], a: int, b: int) -> str | None:
    """経済の条項と、今の世界との突き合わせ（提案のときと、署名のとき）。断るわけがあれば
    messages の名前。

    a は提案した島。どちらの島も、お金の経済が始まっていなければ結べない（命令が動かない島との約束に
    なってしまうため）。"""
    island_a, island_b = world.islands.get(a), world.islands.get(b)
    if island_a is None or island_b is None:
        return "reason_treaty_partner"
    if not (island_a.has_economy and island_b.has_economy):
        return "reason_clause_economy"
    kind = clause["type"]
    if kind == cl.PEG and world.islands[clause["pegger"]].policy.cb_independent:
        return "reason_peg_independent"
    if kind == cl.BLOC and clause["bloc"] is not None:
        from hakoniwa_core.trade import blocs

        bloc = blocs.find(world, clause["bloc"])
        if bloc is None or bloc["rep"] != a or b in bloc["members"]:
            return "reason_clause_bloc"
    return None


# --- 効き始め ---------------------------------------------------------------------------------


def on_force(
    world: WorldState, config: GameConfig, treaty: dict[str, Any], clause: dict[str, Any], turn: int
) -> None:
    """条約が効き始めたとき、経済の条項の約束どおりの状態にする。"""
    kind = clause["type"]
    proposer = world.islands[treaty["proposer"]]
    partner = world.islands[treaty["parties"][1 if treaty["parties"][0] == proposer.id else 0]]
    if kind == cl.BLOC:
        _force_bloc(world, clause, proposer, partner, turn)
    elif kind == cl.CONTRACT:
        _force_contract(world, treaty, clause, turn)
    elif kind == cl.PEG:
        _force_peg(world, clause)
    elif kind == cl.AID:
        clause["start"] = turn
        _log_aid(world, treaty, clause)
    elif kind in ACCEPTS:
        for i in treaty["parties"] if kind == cl.FREE_MOVEMENT else (clause["host"],):
            _open_doors(world.islands[i], kind)


def _force_bloc(world: WorldState, clause: dict[str, Any], proposer: Any, partner: Any, turn: int):
    from hakoniwa_core.trade import blocs

    bloc = blocs.find(world, clause["bloc"]) if clause["bloc"] is not None else None
    if bloc is None:
        blocs.create(world, proposer, clause["name"], clause["open"], turn)
        bloc = blocs.blocs(world)[-1]
    if partner.id not in bloc["members"]:
        blocs.admit(world, bloc, partner)
    clause["bloc_id"] = bloc["id"]


def _force_contract(
    world: WorldState, treaty: dict[str, Any], clause: dict[str, Any], turn: int
) -> None:
    from hakoniwa_core.trade import contracts

    clause["contract"] = contracts.start_from_treaty(world, treaty, clause, turn)["id"]


def _force_peg(world: WorldState, clause: dict[str, Any]) -> None:
    from hakoniwa_core.trade import currency_label
    from hakoniwa_core.trade import fx as fxrates

    pegger = world.islands.get(clause["pegger"])
    if pegger is None:
        return
    key = str(clause["currency"])
    fxrates.ensure(world, pegger, clause["currency"])
    pegger.policy.fx_peg[key] = clause["rate_basis"]
    pegger.policy.fx_intervention.pop(key, None)  # 固定のあいだは介入の額を使わない
    args = {"currency": currency_label(world, clause["currency"]), "rate": clause["rate"]}
    pegger.log.append(LogEntry("fx_peg_set", args))


def _open_doors(island: Any, kind: str) -> None:
    """移民の受け入れが約束に足りなければ、約束の受け入れ方にする（上限の人数は今のまま）。"""
    if (island.policy.immigration or "closed") in ACCEPTS[kind]:
        return
    island.policy.immigration = OPEN_TO[kind]
    mode = f"immigration_{OPEN_TO[kind]}"
    island.log.append(LogEntry("treaty_immigration_set", {"mode": mode}))


# --- 毎ターンの見張り -------------------------------------------------------------------------


def watch(world: WorldState, config: GameConfig, treaty: dict[str, Any], turn: int) -> None:
    """効いている条約の経済の条項が守られているかを見る（外交の段。番号の順）。"""
    noticed = treaty["notice"] is not None
    for clause in treaty["clauses"]:
        kind = clause["type"]
        if kind == cl.BLOC:
            _watch_bloc(world, config, treaty, clause, turn, noticed)
        elif kind == cl.CONTRACT:
            _watch_contract(world, config, treaty, clause, turn)
        elif kind == cl.PEG:
            island = world.islands.get(clause["pegger"])
            if island is None:
                continue  # 島が世界からなくなった（管理画面で消した）。見張るものがない
            ok = island.policy.fx_peg.get(str(clause["currency"])) == clause["rate_basis"]
            _state(
                world, config, treaty, clause, turn, noticed, clause["pegger"], ok, "peg_dropped"
            )
        elif kind == cl.AID:
            _watch_aid(world, config, treaty, clause, turn)
        elif kind in ACCEPTS:
            hosts = treaty["parties"] if kind == cl.FREE_MOVEMENT else [clause["host"]]
            for i in hosts:
                if i not in world.islands:
                    continue
                mode = world.islands[i].policy.immigration or "closed"
                ok = mode in ACCEPTS[kind]
                reason = "migration_closed"
                _state(world, config, treaty, clause, turn, noticed, i, ok, reason)


def _violate(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    by: int,
    kind: str,
    reason: str,
    turn: int,
) -> None:
    """違反を記録する。島が世界になく、休止中なら、動けない国のことなので記録しない。"""
    from hakoniwa_core.diplomacy import is_inactive
    from hakoniwa_core.diplomacy import treaties as tr

    island = world.islands.get(by)
    if island is None or is_inactive(island):
        return
    tr.violate(world, config, treaty, by, kind, reason, turn)


def _state(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    clause: dict[str, Any],
    turn: int,
    noticed: bool,
    by: int,
    ok: bool,
    reason: str,
) -> None:
    """状態を見張る条項: 守れている状態から守れていない状態に変わったときに 1 回だけ違反にする。"""
    if noticed:
        return
    flags = clause.setdefault("ok", {})
    if ok:
        flags[str(by)] = True
    elif flags.get(str(by), True):
        flags[str(by)] = False
        _violate(world, config, treaty, by, clause["type"], reason, turn)


def _watch_bloc(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    clause: dict[str, Any],
    turn: int,
    noticed: bool,
) -> None:
    from hakoniwa_core.trade import blocs

    bloc = blocs.find(world, clause["bloc_id"]) if clause["bloc_id"] is not None else None
    for i in treaty["parties"]:
        ok = bloc is not None and i in bloc["members"] and i not in bloc["leaving"]
        _state(world, config, treaty, clause, turn, noticed, i, ok, "bloc_left")


def _watch_contract(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    clause: dict[str, Any],
    turn: int,
) -> None:
    from hakoniwa_core.trade import contracts

    c = contracts.find(world, clause["contract"]) if clause["contract"] is not None else None
    if c is not None and c.get("cancelled_by") is not None and not clause["cancel_flagged"]:
        clause["cancel_flagged"] = True
        _violate(world, config, treaty, c["cancelled_by"], cl.CONTRACT, "contract_cancelled", turn)
    if c is None:
        clause["done"] = True
        return
    # 契約の最後のターンの不足も見てから、済んだことにする
    why = c.get("short_why")
    if (
        c["short"] > 0
        and c.get("short_turn") == turn - 1
        and why in SHORT_BY_SELLER + SHORT_BY_BUYER
    ):
        last = clause["last_violation"]
        if last is None or turn - last >= config.get("diplomacy.violation_repeat_turns"):
            clause["last_violation"] = turn
            by = c["seller"] if why in SHORT_BY_SELLER else c["buyer"]
            _violate(world, config, treaty, by, cl.CONTRACT, f"contract_short_{why}", turn)
    if c["state"] == contracts.ENDED:
        clause["done"] = True


def _watch_aid(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    clause: dict[str, Any],
    turn: int,
) -> None:
    """援助の約束: n 回目の期日（効いてから period × n ターン）の次のターンに、n 回分の累計が届いて
    いなければ違反にする（先に多く送った分は次の回に数える）。"""
    while not clause["done"]:
        nth = clause["done_count"] + 1
        if turn <= clause["start"] + clause["period"] * nth:
            return
        need = clause["amount"] * nth - clause["forgiven"]
        if clause["paid"] < need:
            # 1 回落としても、次の回からは、その遅れを数え直さない（遅れた分は赦す）
            clause["forgiven"] += need - clause["paid"]
            _violate(world, config, treaty, clause["payer"], cl.AID, "aid_missed", turn)
        clause["done_count"] = nth
        clause["done"] = nth >= clause["count"]


def settled(clause: dict[str, Any]) -> bool:
    """済む条項（契約・援助）が済んだか。"""
    return bool(clause.get("done"))


def count_aid(
    treaties: list[dict[str, Any]], payer: int, payee: int, currency: int, amount: int
) -> None:
    """payer から payee へ送ったお金の援助を、効いている援助の約束の払いに数える（amount は、講和の
    賠償に数えたあとの残り）。"""
    left = amount
    for t in treaties:
        if t["state"] != "in_force":
            continue
        for clause in t["clauses"]:
            if (
                clause["type"] == cl.AID
                and not clause["done"]
                and clause["payer"] == payer
                and clause["payee"] == payee
                and clause["currency"] == currency
            ):
                # 約束の残り（全部の回の合計から、数えた分を引いた額）まで、番号の順に充てる
                total = clause["amount"] * clause["count"] - clause["forgiven"]
                take = min(left, max(0, total - clause["paid"]))
                clause["paid"] += take
                left -= take


def _log_aid(world: WorldState, treaty: dict[str, Any], clause: dict[str, Any]) -> None:
    """援助の約束を、払う国と受け取る国のログに書く。"""
    from hakoniwa_core.trade import currency_label

    for i, key in ((clause["payer"], "treaty_aid_owed"), (clause["payee"], "treaty_aid_due")):
        island = world.islands.get(i)
        if island is None:
            continue
        other = world.islands.get(clause["payee"] if i == clause["payer"] else clause["payer"])
        island.log.append(
            LogEntry(
                key,
                {
                    "treaty": treaty["id"],
                    "country": other.name if other is not None else str(i),
                    "amount": clause["amount"],
                    "currency": currency_label(world, clause["currency"]),
                    "period": clause["period"],
                    "count": clause["count"],
                },
            )
        )
