"""海運業の再参入（W-308。切り替え trade.shipping_reentry、既定は切。掲示板の会議 #5483・
#5490〜#5517 で固まった形）。

倒れた海運の会社が戻る道を、0 → 1 → 2 の順に足す。切のときは、どの道も動かない。

0. 管財人の売りで船団ごと引き継ぐ: 差し押さえ中の海運業だけ、居抜きの売りの輪（modules/firms の
   _sell_on_sale）の「マスがあること」と private_allowed の条件を、この経路の中だけで外す
   （companies.eligible の名簿には入れない。独立採算のまま）。売り値は成約時点の船団の帳簿の
   残り（設備の額 − 減価償却した額）から出すので、管財中に退役した組は売り値にも引き渡しにも
   入らない（売りの輪に入る前に、差し押さえの記録の帳簿の値打ちを今の船団で付け直す）。
   民営の島では資本家階級が参入と同じ物差しで買う。国営の島では政府が別の道
   （modules/firms の _take_over_shipping）で、売り値と引き継ぎ直後の燃料・賃金の不足分を
   国庫から出せるときだけ引き取る（出せない間は管財が続く）。
1. 買い手が付かないまま取り壊しになったら、船団の組（使い始めたターンごと）を国の倉庫へ移し、
   市場に出す。倉庫では船は傷まない（持ち越せる期間のない財）ので、船齢は組のまま残る。
   在庫の出し入れの既定（組を今のターンにまとめる）は変えず、この経路の中だけ組で移す。
2. 港に海運の会社がなく、前のターンに商社の運びたい荷が船がなくて運べなかった島では、後継の
   会社を 1 つ置く。需要は、貿易の段が航路の絞り込みの前に数えた商社の需要から、実際に運べた
   分（自分の船・借りた船・外国の商人）を引いた残り（ship_short_load。前のターンの分を
   modules/trade の _close が貿易の状態に残す）。元手は「いちばん古い組の船 1 隻の代金（倉庫に
   船がなければ新しい船の値段）＋ 運転資金」を、持ち主が今の出資の上限（国営は国庫の使える分の
   GOV_SHARE、民営は資産を持つ層の暮らしの分を残した預金）で出せるときだけ。出せなければ、
   足りなかった物を記録して次のターンも判定する。後継が倉庫の船を買う代金は、判定の後に
   国庫へ入るので、同じ後継の元手の余力には数えない（二重に数えない）。
   後継は倉庫の船をいちばん古い組から買い、組のまま船団に入れる（組の作られたターン 0 は
   新品として、今のターンから使い始める）。「使える組」は、退役・破損・数量 0 の組だけを除き、
   残りの使える期間が 0 の組も含める。船齢と退役の通常の処理は、船団に入れた後のターンから
   そのまま効く（入れたターンに遡って消さない）。買い手の帳簿の設備の額は購入額そのもので、
   減価償却は 0 から始める。

移管は台帳（貿易の状態の shipping_transfers と島のログ）に、施設の番号・買い手・移ったターン・
船団の組を結んで残す。買い手のもとでの初めての輸送は、0 では引き継いだ施設の番号、2 では後継の
施設の番号で、運んだ量の記録（施設の carried）から拾える。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import OWNERS, Payer, funds, household
from hakoniwa_core.economy.households import owners_spare
from hakoniwa_core.economy.start import init_facility
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    OWNER_STATE,
    Facility,
    IslandState,
    LogEntry,
)
from hakoniwa_core.trade import (
    island_state,
    port_of,
    send_to_market,
    shipping_of,
    ships,
    started,
)
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.units import QUANTITY_SCALE, money_of, round_money

TRANSFERS = "shipping_transfers"  # 貿易の状態に残す移管の台帳の名前
SHORT_LOAD = "reentry_short_load"  # このターンに船がなくて運べなかった荷（要る船の重さ）
SHORT_PREV = "reentry_short_prev"  # 前のターンの SHORT_LOAD（2 の判定が読む）
BLOCK = "reentry_block"  # 後継を置けなかったわけの記録（貿易の状態）


def enabled(config: GameConfig) -> bool:
    return bool(config.get("trade.shipping_reentry"))


def in_path(config: GameConfig, f: Facility) -> bool:
    """再参入の経路に入る施設か（差し押さえ中の海運業だけ。例外はこの中に閉じる）。"""
    from hakoniwa_core.economy import in_receivership
    from hakoniwa_core.trade import is_shipping

    return enabled(config) and is_shipping(config, f) and in_receivership(f)


def batch_price(config: GameConfig, unit: int, since: int, turn: int, qty: int) -> int:
    """倉庫の組 [使い始めたターン, 量] の代金（量は 1000 分の 1 隻）。

    減価は船団の帳簿と同じ物差し（設備の額を firms.depreciation_turns で均等に償却）で、
    使い始めたターンからの年数で見る。作られたターン 0 の組は新しい船として今の値段で見る。
    残りの使える期間が 0 の組も、代金はこの物差しのまま（倉庫では消えず、選考からも外さない）。
    """
    if qty <= 0 or unit <= 0:
        return 0
    full = money_of(unit, qty)
    if since == 0:
        return full
    years = max(1, config.get("firms.depreciation_turns"))
    left = max(0, years - (turn - since))
    return full * left // years


def warehouse_offer(
    island: IslandState, config: GameConfig, turn: int
) -> tuple[list[list[int]], int]:
    """後継が倉庫から買う船（いちばん古い組から 1 隻まで）と、その代金。

    倉庫に船がなければ、組は空で、代金は新しい船 1 隻の今の値段（元手の判定に使う）。
    """
    good = config.get("trade.ship_good")
    unit = island.economy.prices.get(good, 0)
    batches = sorted(island.warehouse.get(good, []), key=lambda b: b[0])
    if not batches:
        return [], money_of(unit, QUANTITY_SCALE)
    take: list[list[int]] = []
    left = QUANTITY_SCALE
    price = 0
    for made, qty in batches:
        if left <= 0:
            break
        use = min(qty, left)
        take.append([made, use])
        price += batch_price(config, unit, made, turn, use)
        left -= use
    return take, price


def operating_funds(island: IslandState, config: GameConfig) -> int:
    """船 1 隻の運転資金（建国の place_shipping の元手の式を、船 1 隻で見たもの: 船員の賃金と
    燃料の取り置き分を、firms.operating_turns ターン分）。"""
    prices = island.economy.prices
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    per_turn = config.get("trade.ship_crew") * wage
    per_turn += min(
        (
            money_of(prices.get(g, 0), q) * config.get("trade.fuel_turns")
            for g, q in ships.fuel_per_turn(
                config, config.get("trade.ship_capacity") * QUANTITY_SCALE
            ).items()
        ),
        default=0,
    )
    return per_turn * config.get("firms.operating_turns")


def operating_need(island: IslandState, config: GameConfig, f: Facility) -> int:
    """今の船団の運転資金の目安（建国の place_shipping の元手の式そのまま。政府の引き取りで、
    引き継ぎ直後の燃料・賃金の不足分を測るのに使う）。"""
    prices = island.economy.prices
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    per_turn = ships.crew_wanted(config, f) * wage
    per_turn += min(
        (
            money_of(prices.get(g, 0), q) * config.get("trade.fuel_turns")
            for g, q in ships.fuel_per_turn(config, ships.capacity(config, f)).items()
        ),
        default=0,
    )
    return per_turn * config.get("firms.operating_turns")


def owner_room(island: IslandState, defs: Defs, config: GameConfig, owner: str) -> int:
    """持ち主が後継に出せる額（今の出資の上限。setup._fund と同じ物差し）。"""
    entity = ledger.GOV if owner == OWNER_STATE else household(OWNERS)
    payer = Payer(entity) if entity != ledger.GOV else Payer(ledger.GOV, "trade")
    have = funds.available(island, payer)
    if entity == household(OWNERS):
        have = owners_spare(island, defs, config)
    if entity == ledger.GOV:
        have = round_money(have * trade_setup.GOV_SHARE)
    return have


def record_transfer(island: IslandState, f: Facility, buyer: str, turn: int) -> None:
    """移管の台帳: 施設の番号・買い手・移ったターン・船団の組（使い始めたターンと量）を結ぶ。"""
    entry = {
        "facility": f.id,
        "buyer": buyer,
        "turn": turn,
        "fleet": [[since, qty] for since, qty in f.economy.get("fleet", [])],
    }
    island_state(island).setdefault(TRANSFERS, []).append(entry)
    island.log.append(LogEntry("shipping_transferred", dict(entry)))


def fleet_to_warehouse(island: IslandState, config: GameConfig, f: Facility) -> int:
    """1: 取り壊される海運業の船団を、組のまま国の倉庫へ移して市場に出す。移した量を返す。"""
    good = config.get("trade.ship_good")
    batches = [[since, qty] for since, qty in f.economy.get("fleet", []) if qty > 0]
    qty = sum(q for _, q in batches)
    if qty <= 0:
        return 0
    stock.put_batches(island.warehouse, good, batches)
    f.economy["fleet"] = []
    send_to_market(island, good, qty)
    island.log.append(LogEntry("shipping_fleet_to_warehouse", {"facility": f.id, "ships": qty}))
    return qty


def place_successor(island: IslandState, ctx: Any) -> Facility | None:
    """2: 港に海運の会社がなく、前のターンに運べなかった荷がある島に、後継の会社を 1 つ置く。

    置けなければ、足りなかった物を貿易の状態（reentry_block）に記録して None を返す
    （次のターンも判定する）。"""
    defs, config, turn = ctx.defs, ctx.config, ctx.turn
    if not enabled(config):
        return None
    if not started(island) or shipping_of(island, config) is not None:
        return None
    if port_of(island, config) is None:
        return None
    state = island_state(island)
    if state.get(SHORT_PREV, 0) <= 0:
        return None  # 前のターンに、船がなくて運べなかった荷がない
    kind = config.get("trade.shipping_facility")
    owner = trade_setup._owner(defs, island, kind)
    offer, ship_price = warehouse_offer(island, config, turn)
    need = ship_price + operating_funds(island, config)
    have = owner_room(island, defs, config, owner)
    if have < need:
        block = {"reason": "funds", "short": need - have, "turn": turn}
        if state.get(BLOCK) != block:
            state[BLOCK] = block
            island.log.append(
                LogEntry("shipping_reentry_blocked", {"reason": "funds", "short": need - have})
            )
        return None
    state.pop(BLOCK, None)
    f = trade_setup._new(island, kind, trade_setup.SHIPPING_POS, owner, ACCOUNTING_SELF)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    init_facility(island, defs, config, f, dict(island.economy.prices), wage)
    trade_setup._fund(island, defs, config, f, need)
    if offer:
        _buy_warehouse_ships(island, config, f, offer, ship_price, turn)
    record_transfer(island, f, ledger.GOV if owner == OWNER_STATE else household(OWNERS), turn)
    island.log.append(
        LogEntry("shipping_successor", {"facility": f.id, "owner": owner, "price": ship_price})
    )
    stats = island.stats
    stats["firms.shipping_successor"] = stats.get("firms.shipping_successor", 0) + 1
    return f


def _buy_warehouse_ships(
    island: IslandState,
    config: GameConfig,
    f: Facility,
    offer: list[list[int]],
    price: int,
    turn: int,
) -> None:
    """後継が倉庫の船を買い、組のまま船団に入れる。代金は施設から国庫へ。帳簿の設備の額は
    購入額そのもの（減価償却は 0 から）。組の作られたターン 0 は新しい船として今のターンから。"""
    from hakoniwa_core.economy import facility_entity

    good = config.get("trade.ship_good")
    want = sum(q for _, q in offer)
    taken = stock.take_batches(island.warehouse, good, want)
    entity = facility_entity(f.id)
    paid = min(price, ledger.money(island, entity))
    if paid > 0:
        ledger.pay(island, entity, ledger.GOV, paid, "ship_purchase")
    for made, qty in taken:
        # 作られたターン 0 は新しい船として今のターンから。建国より前の負のターンは本物の
        # 使い始めたターンなので、そのまま残す
        ships.add_ships(f, qty, made if made != 0 else turn)
    f.economy["capital"] = f.economy.get("capital", 0) + paid
