"""「生産調整中」「生産停止中（買い注文不足）」のわけ（W-250・UI-84・UI-93）。

施設の状態が target（売れ行きに合わせて作る量を下げた）のとき、操業目標を下げた主なわけと、
打てる手を1つの形にまとめる。島の地図の窓・施設の一覧と帳簿・国の要約・JSON API・MCP の道具で
同じものを出す。

わけは、市場の段の操業目標の見直し（hakoniwa_core/modules/market の operating_target）の決まりに
合わせて、施設に残った数だけで組み立てる（計算はしない）:

- 値段の上限が1単位の費用より低い（価格統制の島で、上限に張り付いた値段が費用に届かないと、売れる
  見込みを 0 にする）
- 買い手の付け値が費用に届かない（費用より安くは売らないので、市場の値段が費用より安いと売れ残る。
  市場に買えなかった買い手がいて値段が費用以上なら、高い希望価格で売れなかった分も売れたと見るので、
  わけに出さない）
- 安い輸入に先を越された（同じターンに海外の商人や商社が同じ品を売り、こちらが売れ残った）
- 希望価格が市場の値段より高く売れ残った・売りに出した量が買い注文より多く売れ残った
- 在庫が売れ行きの見込みの目安（market.inventory_turns）より多い（多い分を減らすまで少なく作る）
- 売る物がなく、市場に買えなかった買い手もいない（売れる見込みが立たない）

数は施設の economy（sales・expected・target・ask と、見せるためだけに残した cost_floor）、施設の
在庫、直近のターンの市場の記録（MarketRecord）と約定（Trade）から読む。計画経済の島の生産目標、
発電所（送電網の買いたい量）、閉鎖の途中の施設は、それぞれの決まりの文にする。
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from django.db.models import Max, Sum

from hakoniwa_core import facilities as fac
from hakoniwa_core import politics
from hakoniwa_core.economy import is_self_supporting, is_stopped
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules.market import COST_FLOOR, MERCHANT, inventory_turns
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState
from server.economy.models import MarketRecord, Trade
from server.game import get_game
from server.web import display
from server.world import models as m

TARGET = "target"  # 施設の status: 売れ行きに合わせて作る量を下げた

# 打てる手（今ある命令の名前で書く）。わけの種類ごとに ACTIONS の手を選び、重なりを除いてつなぐ
ACTIONS = {
    "cap": "「値段の上限（公共料金など）の変更」で{good}の上限を外すか費用より上げる",
    "subsidy": "「補助金の変更」で生産補助を出して費用の下限を下げる",
    "export": "「二国間契約の申し込み」で輸出する",
    "recipe": "国営なら「生産方式の変更」で費用の安い作り方に替える",
    "tariff": "「関税」か「輸入の割り当て」で商社の輸入を抑える",
    "warehouse": "「国の倉庫のこのターンの売り買い」で買い取る",
    "grid": "「送電線の敷設」で電気を使う町や施設をつなぐ",
}
KIND_ACTIONS = {
    "price_cap": ("cap", "subsidy"),
    "below_cost": ("subsidy", "export", "recipe"),
    "imports": ("tariff", "subsidy", "export", "recipe"),  # 商社の手は商社の輸入があるときだけ
    "priced_out": ("warehouse", "export"),
    "unsold": ("warehouse", "export", "subsidy"),
    "stock": ("warehouse", "export", "subsidy"),
    # 国の倉庫の買い注文が買えずに残れば「市場に買えなかった買い手」になり、作り始める
    "no_buyers": ("warehouse", "subsidy", "export"),
    "power": ("grid",),
}
# 手の前に置く一言（待てば戻るもの・島の側で変えられないもの）
NOTES = {
    "priced_out": "売れ残ると希望価格は次のターンから下がり、売れ行きが戻ります。",
    "no_buyers": "その品を使う工場や町が増えるか、今の値段で利益が出るようになれば作り始めます。",
    "plan": "需要（配給と工場の注文）が増えると、次の目標も上がります。",
    "merchant": "海外の商人の売り値は島の側では変えられません。",
}


class Facts:
    """島ごとに1度だけ読む、わけを組み立てるための数（直近の市場の記録・約定・施設の在庫）。"""

    def __init__(self, island: m.Island, state: IslandState | None = None) -> None:
        game = get_game()
        self.island = island
        self.state = state if state is not None else display.island_for_naming(island)
        self.currency = (self.state.names or {}).get("currency_symbol", "")
        turn = MarketRecord.objects.filter(island=island).aggregate(t=Max("turn"))["t"]
        self.records: dict[str, MarketRecord] = (
            {r.good: r for r in MarketRecord.objects.filter(island=island, turn=turn)}
            if turn is not None
            else {}
        )
        # 輸入: 海外の商人と商社が島の市場で売った量と代金（財 → 売り手の種類 → [量, 代金]）
        traders = {
            f"fac:{n}"
            for n in island.facilities.filter(
                kind=game.config.get("trade.trader_facility")
            ).values_list("number", flat=True)
        }
        self.imports: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0])
        )
        if turn is not None:
            for good, seller, qty, amount in Trade.objects.filter(
                island=island, turn=turn
            ).values_list("good", "seller", "quantity", "amount"):
                kind = "merchant" if seller == MERCHANT else "trader" if seller in traders else ""
                if kind:
                    entry = self.imports[good][kind]
                    entry[0] += qty
                    entry[1] += amount
        self.stock: dict[int, dict[str, int]] = defaultdict(dict)
        for number, good, qty in (
            m.Stock.objects.filter(island=island, facility__isnull=False)
            .values("facility__number", "good")
            .annotate(qty=Sum("quantity"))
            .values_list("facility__number", "good", "qty")
        ):
            self.stock[number][good] = qty


def stop_text(f: Facility) -> str | None:
    """作る量を 0 まで下げて止まった施設の状態の文（UI-84）。止まっていなければ None。"""
    if not f.built or f.status.partition(":")[0] != TARGET or f.operating_rate > 0:
        return None
    return display.message("stop_target_closed" if is_stopped(f) else "stop_target")


def applies(f: Facility) -> bool:
    """わけを出す施設か（建ち終わり、状態が売れ行きの調整 target）。Facts を作る前に見る。"""
    return bool(f.built) and f.status.partition(":")[0] == TARGET


def explain(facts: Facts, f: Facility) -> dict[str, Any] | None:
    """施設 f の生産調整中（止まったときは生産停止中）のわけ。状態が target でなければ None。

    答え: stopped（0 まで下げて止まった）、target（操業目標 %）、good と good_name（目標を決めた
    産出物）、reasons（わけの文。初めが主なわけ）、text（主なわけ）、hint（打てる手の一言）。"""
    if not applies(f):
        return None
    game = get_game()
    defs, config = game.defs, game.config
    out: dict[str, Any] = {
        "stopped": f.operating_rate <= 0,
        "target": round(f.economy.get(TARGET, fac.FULL) * 100 / fac.FULL),
    }
    reasons: list[tuple[str, str]] = []
    good = None
    power = config.get("power.good")
    outputs = (
        {g: q for g, q in fac.outputs_of(defs, f).items() if q > 0}
        if f.kind in (defs.facilities)
        else {}
    )
    if is_stopped(f):
        reasons.append(("closing", "閉鎖の途中か差し押さえで止めていて、作る量を 0 にしています。"))
    elif power in outputs:
        reasons.append(
            (
                "power",
                "電気は在庫を持てないので、送電網で買いたい量に合わせて作る量を決めています。",
            )
        )
    else:
        goods = sorted(g for g in outputs if g != power)
        planned = _plan_reason(facts.state, goods) if goods else None
        if planned is not None:
            good, text = planned
            reasons.append(("plan", text))
        elif goods:
            good = _deciding_good(facts, f, outputs, goods)
            reasons = _market_reasons(facts, f, good)
    if not reasons:
        reasons.append(("unsold", "売れ行きに合わせて作る量を減らしています。"))
    if good is not None:
        out["good"] = good
        out["good_name"] = defs.name("goods", good)
    out["reasons"] = [{"kind": k, "text": t} for k, t in reasons]
    out["text"] = reasons[0][1]
    out["hint"] = _hint(facts, f, [k for k, _ in reasons], good)
    return out


def _plan_reason(state: IslandState, goods: list[str]) -> tuple[str, str] | None:
    """計画経済の島の生産目標で動いているときの (財, 文)。目標で動いていなければ None（市場の段は
    公定価格のある島で、需要の記録のある財を作る施設の目標を planning.target で決める）。"""
    game = get_game()
    if not planning.official(state):
        return None
    for good in goods:
        goal = planning.goal_of(state, game.config, good)
        if goal is not None:
            name = game.defs.name("goods", good)
            return good, (
                f"計画経済の{name}の生産目標（直近の需要の平均に少し足した 1 ターン "
                f"{_qty(good, goal)}）を、同じ品を作る国営施設に割り振った分だけ作っています。"
            )
    return None


def _deciding_good(facts: Facts, f: Facility, outputs: dict[str, int], goods: list[str]) -> str:
    """操業目標を決めた産出物: 見直しで作る量の割合が一番大きいもの（目標はその割合に合わせる）。"""
    settings = get_game().config.get("market")
    ctx = SimpleNamespace(config=get_game().config, defs=get_game().defs)
    held = facts.stock.get(f.id, {})
    best, best_share = goods[0], -1.0
    for good in goods:
        expected = f.economy.get("expected", {}).get(good, 0)
        gap = expected * inventory_turns(ctx, good) - held.get(good, 0)
        share = max(0.0, expected + gap * settings["inventory_adjust"]) / outputs[good]
        if share > best_share:
            best, best_share = good, share
    return best


def _market_reasons(facts: Facts, f: Facility, good: str) -> list[tuple[str, str]]:
    """市場の段の操業目標の見直し（operating_target）に合わせた、財 good のわけ（主なものから）。"""
    game = get_game()
    defs, config = game.defs, game.config
    state, cur = facts.state, facts.currency
    name = defs.name("goods", good)
    offered, sold, priced_out = f.economy.get("sales", {}).get(good, [0, 0, 0])
    late_offered = f.economy.get("late_sales", {}).get(good, [0, 0])[0]
    record = facts.records.get(good)
    unmet = max(0, record.demand - record.volume) if record is not None else 0
    price = state.economy.prices.get(good, record.price if record is not None else 0)
    floor = f.economy.get(COST_FLOOR, {}).get(good)
    expected = f.economy.get("expected", {}).get(good)
    held = facts.stock.get(f.id, {}).get(good, 0)
    out: list[tuple[str, str]] = []

    cap = state.policy.price_cap.get(good)
    if (
        is_self_supporting(f)
        and politics.trait(state, defs, "price_controls", False)
        and not (planning.is_planned(state, defs) and good in planning.official(state))
        and cap
        and floor is not None
        and price >= cap
        and cap < floor
    ):  # 上限に張り付いた値段が費用に届かず、売れる見込みを 0 にした
        return [
            (
                "price_cap",
                f"{name}の値段の上限 {_price(good, cap, cur)} が1単位の費用 "
                f"{_price(good, floor, cur)} より低く、新しく作っても費用を取り戻せないので、"
                "売れる見込みを 0 にしています。",
            )
        ]
    if offered == 0 and late_offered == 0:
        if unmet <= 0:
            text = (
                f"売りに出す{name}がなく、市場に買えなかった買い手もいないので、"
                "売れる見込みが立ちません"
            )
            if floor is not None and 0 < price < floor:
                text += (
                    f"（今の値段 {_price(good, price, cur)} は1単位の費用 "
                    f"{_price(good, floor, cur)} より安く、試しに作ることもしません）"
                )
            out.append(("no_buyers", text + "。"))
    else:
        counted = unmet > 0 and floor is not None and price >= floor  # 売れたと見る分
        if priced_out > 0 and not counted:
            if floor is not None and price < floor:
                out.append(
                    (
                        "below_cost",
                        f"買い手の付け値が費用に届きません: {name}の市場の値段 "
                        f"{_price(good, price, cur)} が1単位の費用 {_price(good, floor, cur)} "
                        f"より安く、費用より安くは売らないので {_qty(good, priced_out)}が"
                        "売れ残りました。",
                    )
                )
            else:
                out.append(
                    (
                        "priced_out",
                        f"希望価格が市場の値段 {_price(good, price, cur)} より高く、"
                        f"{_qty(good, priced_out)}が売れ残りました。",
                    )
                )
        unsold = offered - sold
        bought = facts.imports.get(good, {})
        imported = sum(q for q, _ in bought.values())
        if unsold > 0 and imported > 0:
            amount = sum(a for _, a in bought.values())
            who = "・".join(
                label
                for kind, label in (("merchant", "海外の商人"), ("trader", "商社の輸入"))
                if bought.get(kind, [0])[0] > 0
            )
            out.insert(
                1 if out and out[0][0] == "below_cost" else 0,
                (
                    "imports",
                    f"安い輸入に先を越されました: 一番安い売りの相手は{who}で、{name}を "
                    f"{_qty(good, imported)}（平均 {_price(good, amount * 1000 / imported, cur)}）"
                    "売り、こちらの売りが後に回りました。",
                ),
            )
        shown = priced_out if priced_out > 0 and not counted else 0  # 上のわけで数えた分
        rest = unsold - shown
        if rest > 0:
            head = "そのほかに " if shown else f"売りに出した {_qty(good, offered)}のうち "
            text = f"{head}{_qty(good, rest)}が売れ残りました"
            if record is not None and record.supply > record.demand:
                text += (
                    f"（市場の買い注文 {_qty(good, record.demand)}に、売り注文 "
                    f"{_qty(good, record.supply)}）"
                )
            out.append(("unsold", text + "。"))
    if expected is not None:
        ctx = SimpleNamespace(config=config, defs=defs)
        turns = inventory_turns(ctx, good)
        if held > round(expected * turns):
            if expected > 0:
                text = (
                    f"在庫 {_qty(good, held)}が売れ行きの見込み（1 ターン {_qty(good, expected)}）"
                    f"の約 {held / expected:.1f} ターン分あり、目安の {turns:g} ターン分を"
                    "超えた分を減らすまで少なく作ります。"
                )
            else:
                text = f"売れ行きの見込みが 0 で、在庫 {_qty(good, held)}が残っています。"
            out.append(("stock", text))
    return out


def _hint(facts: Facts, f: Facility, kinds: list[str], good: str | None) -> str:
    """わけの種類に合わせた打てる手の一言（主なわけ 2 つ分の手を、重ならないように 4 つまで）。"""
    defs = get_game().defs
    recipes = defs.facilities.get(f.kind, {}).get("recipes", {})
    bought = facts.imports.get(good, {}) if good else {}
    notes: list[str] = []
    picked: list[str] = []
    for kind in kinds[:2]:
        if kind in NOTES:
            notes.append(NOTES[kind])
        if kind == "imports" and bought.get("merchant", [0])[0] > 0:
            notes.append(NOTES["merchant"])
        for action in KIND_ACTIONS.get(kind, ()):
            if action == "tariff" and bought.get("trader", [0])[0] <= 0:
                continue
            if action == "recipe" and (f.owner != OWNER_STATE or len(recipes) < 2):
                continue
            if action not in picked:
                picked.append(action)
    name = defs.name("goods", good) if good else ""
    steps = "、".join(ACTIONS[a].format(good=name) for a in picked[:4])
    return "".join(dict.fromkeys(notes)) + (f"{steps}ことができます。" if steps else "")


def _qty(good: str, milli: float) -> str:
    unit = get_game().defs.unit_name(good)
    return f"{display.amount(round(milli), good):,g}{f' {unit}' if unit else ''}"


def _price(good: str, value: float, currency: str) -> str:
    return display.price_text(good, value, currency)
