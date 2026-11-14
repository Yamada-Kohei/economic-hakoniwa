"""命令の打ち間違いの確かめ（UI-75・W-264）。

命令を足すとき、打ち間違いに見えるもの（相場から大きく離れた指値、1 ターンの生産より多い量など）
と、取り壊しのように戻せない命令にだけ、確かめの文を返す。画面・JSON API・MCP の道具は、みな命令の
列を置き換える API（PUT .../orders）を通るので、そこで新しく足した命令にこの文があれば、
`confirm: true` の付いていない送りは並べずに文を返す（server/web/api.py の orders）。命令の確かめ
（POST .../orders/check）も同じ文を返す。

目安は命令の種類ごとの表 RULES に書く。値段の目安は、買う指値が相場の PRICE_HIGH 倍以上、売る
指値が相場の PRICE_LOW 倍以下。相場は直近のターンの市場の記録の値段で、値段の種類（約定・気配・
持ち越し）を文に添え、記録のない財（未観測）は値段を確かめない。契約の値段は契約の通貨で書くので、自国通貨に
直してから自国の相場と比べる（レートのない通貨は確かめない）。量の目安は、市場から買う・市場へ売る
命令は「1 ターンの生産と、市場の 1 ターンの売買の量の多いほう」、国の倉庫から売る命令は「倉庫の今の
在庫」。毎ターン続く命令（決まった売り買い・契約）は、文にそのことを添える。

ターンの計算は変えない（市場の記録・統計・島の状態を読むだけ。UI-02）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import IslandState
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS, from_milli
from server.economy.models import MarketRecord
from server.game import get_game
from server.web import display
from server.world import models as m

PRICE_HIGH = 2  # 買う指値がこの倍以上なら確かめる
PRICE_LOW = 0.5  # 売る指値がこの倍以下なら確かめる

# 打ち間違いに見える命令と戻せない命令を、確かめずに並べようとしたときの答え（UI-75）。428 は
# 「条件（ここでは confirm: true）を付けて送り直す」という意味の状態。並べずに確かめの文を返す
CONFIRM_STATUS = 428
CONFIRM_TEXT = (
    "打ち間違いに見える命令か、戻せない命令があります。confirm の文を読み、このままでよければ "
    "confirm: true を付けて同じ列を送り直してください（まだ並べていません）"
)

# 相場の値段の種類ごとの、文に添える説明。持ち越し（直近のターンに売り買いがなかった）は、最後に
# 約定した値段を相場にし、一度も約定していなければ未観測と同じに値段を確かめない
PRICE_LABELS = {
    "trade": "直近のターンに約定した値段",
    "quote": "直近のターンの気配。売り買いが折り合わず、約定していない値段",
    "carry": "持ち越し。直近のターンは売り買いがなく、最後に約定した{date}の値段",
}


@dataclass
class Market:
    """財の相場。price は計算の単位、volume は直近のターンの売買の量（1 単位で数えた量）、label は
    値段の種類の説明（PRICE_LABELS）。"""

    price: int
    kind: str
    volume: float
    label: str


@dataclass
class Context:
    """確かめに読む島の値（市場の記録と統計は、初めて要るときに読む）。"""

    island: m.Island
    state: IslandState
    _markets: dict[str, Market | None] | None = None
    _records: dict[str, MarketRecord] | None = None
    _before: dict[str, MarketRecord] = field(default_factory=dict)
    _stats: dict[str, float] | None = None
    _currency: str | None = None

    @property
    def currency(self) -> str:
        if self._currency is None:
            self._currency = (self.island.names or {}).get("currency_symbol", "")
        return self._currency

    @property
    def stats(self) -> dict[str, float]:
        if self._stats is None:
            self._stats = display.latest_stats(self.island)[1]
        return self._stats

    def market(self, good: str) -> Market | None:
        """財の相場（直近のターンの市場の記録から）。値段を確かめられなければ None。"""
        if self._markets is None:
            self._markets = {}
            self._records = {}
            last = (
                MarketRecord.objects.filter(island=self.island)
                .order_by("-turn")
                .values_list("turn", flat=True)
                .first()
            )
            if last is not None:
                before = {}
                for r in MarketRecord.objects.filter(island=self.island, turn__in=[last, last - 1]):
                    (self._records if r.turn == last else before)[r.good] = r
                self._before = before
        if good not in self._markets:
            self._markets[good] = self._read(good)
        return self._markets[good]

    def _read(self, good: str) -> Market | None:
        assert self._records is not None
        record = self._records.get(good)
        if record is None:  # 未観測
            return None
        kind = display.price_kind(record, self._before.get(good))
        volume = from_milli(record.volume)
        if kind == "carry":
            traded = (
                MarketRecord.objects.filter(
                    island=self.island, good=good, volume__gt=0, turn__lt=record.turn
                )
                .order_by("-turn")
                .first()
            )
            if traded is None or traded.price <= 0:
                return None
            label = PRICE_LABELS["carry"].format(date=display.turn_text(traded.turn))
            return Market(traded.price, kind, volume, label)
        if kind not in PRICE_LABELS or record.price <= 0:
            return None
        return Market(record.price, kind, volume, PRICE_LABELS[kind])

    def production(self, good: str) -> float:
        return self.stats.get(f"production.{good}", 0.0)

    def stock(self, good: str) -> float:
        return from_milli(stock.total(self.state.warehouse, good))


# --- 文の部品 -----------------------------------------------------------------------------


def _goods_name(good: str) -> str:
    return get_game().defs.name("goods", good)


def _qty(good: str, units: float) -> str:
    """1 単位で数えた量を、見せる単位の文に（例「1,200 トン」）。"""
    value = display.shown(good, units)
    text = f"{value:,.1f}".removesuffix(".0")
    unit = get_game().defs.unit_name(good)
    return f"{text} {unit}" if unit else text


def _price(ctx: Context, good: str, price: float, currency: str | None = None) -> str:
    return display.price_text(good, price, ctx.currency if currency is None else currency)


def _how_long(turns: Any) -> str:
    """毎ターン続く命令の長さの文。"""
    if isinstance(turns, int) and turns > 0:
        return f"{turns} ターンのあいだ毎ターン続きます"
    return "止めるまで毎ターン続きます"


def price_note(
    ctx: Context, good: str, side: str, price: Any, shown: str | None = None, what: str = ""
) -> str | None:
    """値段（自国通貨・計算の単位）が相場から離れているときの文。shown は値段の見せ方（契約の通貨の
    値段など。省くと自国通貨で書く）。相場が未観測なら None。"""
    if not isinstance(price, int | float) or isinstance(price, bool) or price <= 0:
        return None
    market = ctx.market(good)
    if market is None:
        return None
    shown = shown or _price(ctx, good, price)
    name = what or ("買う指値" if side == "buy" else "売る指値")
    rate = f"{_price(ctx, good, market.price)}（{market.label}）"
    if side == "buy" and price >= PRICE_HIGH * market.price:
        return f"{_goods_name(good)}の{name} {shown} は、相場 {rate}の {PRICE_HIGH} 倍以上です。"
    if side == "sell" and price <= PRICE_LOW * market.price:
        return f"{_goods_name(good)}の{name} {shown} は、相場 {rate}の半分以下です。"
    return None


def market_quantity_note(ctx: Context, good: str, quantity: Any, what: str) -> str | None:
    """市場から買う・市場へ売る量が、1 ターンの生産と売買の量の多いほうを超えるときの文。どちらも
    0 なら（作っておらず、売り買いもない財）確かめない。"""
    if not isinstance(quantity, int | float) or isinstance(quantity, bool) or quantity <= 0:
        return None
    made = ctx.production(good)
    market = ctx.market(good)
    traded = market.volume if market is not None else 0.0
    base = max(made, traded)
    if base <= 0 or quantity <= base:
        return None
    return (
        f"{_goods_name(good)}の{what} {_qty(good, quantity)} は、島の 1 ターンの生産 "
        f"{_qty(good, made)}・市場の 1 ターンの売買の量 {_qty(good, traded)} より多いです。"
    )


def stock_note(ctx: Context, good: str, quantity: Any, what: str) -> str | None:
    """国の倉庫から出す量が、倉庫の今の在庫を超えるときの文。"""
    if not isinstance(quantity, int | float) or isinstance(quantity, bool) or quantity <= 0:
        return None
    have = ctx.stock(good)
    if quantity <= have:
        return None
    return (
        f"{_goods_name(good)}の{what} {_qty(good, quantity)} は、国の倉庫の今の在庫 "
        f"{_qty(good, have)} より多いです（在庫の分しか出せません）。"
    )


def _keep(*notes: str | None) -> list[str]:
    return [n for n in notes if n]


# --- 命令の種類ごとの目安 -------------------------------------------------------------------


def _warehouse_trade(ctx: Context, p: dict[str, Any]) -> list[str]:
    """国の倉庫の売り買い（このターンだけ）。"""
    good, side = p.get("good"), p.get("side")
    if side == "buy":
        return _keep(
            price_note(ctx, good, "buy", p.get("limit")),
            market_quantity_note(ctx, good, p.get("quantity"), "買う量"),
        )
    return _keep(
        price_note(ctx, good, "sell", p.get("limit")),
        stock_note(ctx, good, p.get("quantity"), "売る量"),
    )


def _warehouse_standing(ctx: Context, p: dict[str, Any]) -> list[str]:
    """国の倉庫の決まった売り買い（毎ターン続く）。"""
    good, kind = p.get("good"), p.get("kind")
    if kind == "buy":
        notes = _keep(
            price_note(ctx, good, "buy", p.get("limit")),
            market_quantity_note(ctx, good, p.get("quantity"), "1 ターンに買う量"),
        )
    elif kind == "sell":
        notes = _keep(
            price_note(ctx, good, "sell", p.get("limit")),
            stock_note(ctx, good, p.get("quantity"), "1 ターンに売る量"),
        )
    elif kind == "reserve":
        notes = _keep(
            price_note(ctx, good, "buy", p.get("limit"), what="買い入れの上限の値段"),
            price_note(ctx, good, "sell", p.get("release"), what="放出の値段"),
        )
    else:
        return []
    return [*notes, f"この決まった売り買いは、{_how_long(p.get('turns'))}。"] if notes else []


def _contract_notes(
    ctx: Context, p: dict[str, Any], side: str, currency: int, what: str
) -> list[str]:
    """二国間契約（命令の契約と、条約の契約の条項）。値段は契約の通貨で、毎ターン続く。"""
    good = p.get("good")
    if not isinstance(good, str) or good not in get_game().defs.goods:
        return []
    price = p.get("price")
    note = None
    if isinstance(price, int | float) and not isinstance(price, bool) and price > 0:
        if currency == ctx.state.id:
            note = price_note(ctx, good, side, price, what=f"{what}の値段")
        elif fxrates.has_rate(ctx.state, currency):
            own = price * fxrates.rate(ctx.state, currency) / BASIS
            foreign = _price(ctx, good, price, display.currency_symbol(currency))
            name = display.currency_name(currency)
            shown = f"{foreign}（{name}建て。自国通貨で約 {_price(ctx, good, own)}）"
            note = price_note(ctx, good, side, own, shown=shown, what=f"{what}の値段")
    qty = market_quantity_note(
        ctx, good, p.get("quantity"), f"{what}の 1 ターンの量（{display.SIDES.get(side, '')}）"
    )
    notes = _keep(note, qty)
    return [*notes, f"この契約は、{_how_long(p.get('turns'))}。"] if notes else []


def _propose_contract(ctx: Context, p: dict[str, Any]) -> list[str]:
    currency = p.get("currency") or ctx.state.id
    return _contract_notes(ctx, p, p.get("side", ""), currency, "契約")


def _propose_treaty(ctx: Context, p: dict[str, Any]) -> list[str]:
    """条約の契約の条項（売る国 seller がこちらなら売り、相手なら買い。通貨を省くと売る国の
    通貨）。"""
    notes: list[str] = []
    for clause in p.get("clauses") or []:
        if not isinstance(clause, dict) or clause.get("type") != "contract":
            continue
        seller = clause.get("seller")
        side = "sell" if seller == ctx.state.id else "buy"
        currency = clause.get("currency") or seller or ctx.state.id
        notes += _contract_notes(ctx, clause, side, currency, "条約の契約")
    return notes


def _send_aid(ctx: Context, p: dict[str, Any]) -> list[str]:
    good = p.get("good")
    if not isinstance(good, str) or good not in get_game().defs.goods:
        return []
    note = market_quantity_note(ctx, good, p.get("quantity"), "1 ターンに送る量")
    return [note, f"この援助は、{_how_long(p.get('turns'))}。"] if note else []


def _price_cap(ctx: Context, p: dict[str, Any]) -> list[str]:
    return _keep(price_note(ctx, p.get("good"), "sell", p.get("price"), what="値段の上限"))


def _price_floor(ctx: Context, p: dict[str, Any]) -> list[str]:
    return _keep(price_note(ctx, p.get("good"), "buy", p.get("price"), what="値段の下限"))


def _official_price(ctx: Context, p: dict[str, Any]) -> list[str]:
    good, price = p.get("good"), p.get("price")
    return _keep(
        price_note(ctx, good, "buy", price, what="公定価格")
        or price_note(ctx, good, "sell", price, what="公定価格")
    )


def _retail_price(ctx: Context, p: dict[str, Any]) -> list[str]:
    good, price = p.get("good"), p.get("price")
    return _keep(
        price_note(ctx, good, "buy", price, what="小売の値段")
        or price_note(ctx, good, "sell", price, what="小売の値段")
    )


def _export_subsidy(ctx: Context, p: dict[str, Any]) -> list[str]:
    good, amount = p.get("good"), p.get("amount")
    market = ctx.market(good) if isinstance(good, str) else None
    if market is None or not isinstance(amount, int | float) or amount < market.price:
        return []
    return [
        f"{_goods_name(good)}の 1 単位あたりの輸出補助 {_price(ctx, good, amount)} は、相場 "
        f"{_price(ctx, good, market.price)}（{market.label}）と同じか高いです。"
    ]


# --- 戻せない命令 -----------------------------------------------------------------------------


def _demolish(ctx: Context, p: dict[str, Any]) -> list[str]:
    x, y = p.get("x"), p.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return []
    f = ctx.state.facility_at(x, y)
    if f is None:
        return [
            f"({x}, {y}) の送電網・道路などのインフラと、並んでいる工事を外します。戻せません。"
        ]
    game = get_game()
    name = facility_name(game.defs, game.config, ctx.state, f)
    return [
        f"({x}, {y}) の{name}を取り壊します。働いている人は解雇され、建て直すには建設費と工期が"
        "かかります。戻せません。"
    ]


def _disband_unit(ctx: Context, p: dict[str, Any]) -> list[str]:
    return [f"部隊（{p.get('unit')} 番）を解散します。兵は未配属に、装備は国の倉庫に戻ります。"]


def _declare_war(ctx: Context, p: dict[str, Any]) -> list[str]:
    target = display.island_name(p.get("target"))
    return [f"{target}に宣戦します。実行すると次のターンから戦争になります。"]


def _cancel_contract(ctx: Context, p: dict[str, Any]) -> list[str]:
    turns = get_game().config.get("trade.contract_cancel_turns")
    return [
        f"契約（{p.get('contract')} 番）をやめます。結ばれている契約は {turns} ターン後に終わり、"
        "申し込み中の契約は取り下げ（断り）になります。"
    ]


def _leave_bloc(ctx: Context, p: dict[str, Any]) -> list[str]:
    return [f"{display._bloc_name(p.get('bloc'))}から抜けます（命令を実行した次のターン）。"]


def _fire_missile(ctx: Context, p: dict[str, Any]) -> list[str]:
    if p.get("nuclear") is not True:
        return []
    return [
        "核を使うと、周りの住民の多くが亡くなり、汚染が長く残り、大きな違反として記録されます。"
    ]


def _bond_payment(ctx: Context, p: dict[str, Any]) -> list[str]:
    if p.get("stop") is not True:
        return []
    return ["国債の利払いと償還を止めると、そのターンにすべての国債が債務不履行になります。"]


def _fixed(text: str):
    return lambda ctx, p: [text]


# 命令の種類 → 確かめの文を返す関数（中身は計算の単位）
RULES = {
    "warehouse_trade": _warehouse_trade,
    "set_warehouse_standing": _warehouse_standing,
    "propose_contract": _propose_contract,
    "propose_treaty": _propose_treaty,
    "send_aid": _send_aid,
    "set_price_cap": _price_cap,
    "set_price_floor": _price_floor,
    "set_official_price": _official_price,
    "set_retail_price": _retail_price,
    "set_export_subsidy": _export_subsidy,
    "demolish": _demolish,
    "disband_unit": _disband_unit,
    "declare_war": _declare_war,
    "cancel_contract": _cancel_contract,
    "leave_bloc": _leave_bloc,
    "fire_missile": _fire_missile,
    "set_bond_payment": _bond_payment,
    "denounce_treaty": _fixed("条約の破棄を予告します。条約に定めた予告期間の後に終わります。"),
    "dissolve_parliament": _fixed("議会を解散して、今の支持率で選挙をします。"),
    "amend_constitution": _fixed("憲法を改正します。混乱期間に入ります。"),
    "change_economy": _fixed("経済体制を変えます。混乱期間に入ります。"),
    "review_official_prices": _fixed(
        "選んだ財（選ばなければ、命令で決めた値段のない財すべて）の公定価格を今の原価に改めます。"
        "選んだ据え置く財は小売の値段も改めます。値上げは支持率と政情に響きます。"
    ),
}


def confirmations(ctx: Context, kind: Any, payload: Any) -> list[str]:
    """命令 1 つの確かめの文（なければ空）。payload は計算の単位の中身。形の違う中身は確かめない
    （命令の確かめ check_order が断る）。"""
    rule = RULES.get(kind) if isinstance(kind, str) else None
    if rule is None or not isinstance(payload, dict):
        return []
    try:
        return rule(ctx, payload)
    except (TypeError, ValueError, KeyError):
        return []
