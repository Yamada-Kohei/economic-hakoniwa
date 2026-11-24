"""商社と施設の引き取り契約（W-307。掲示板 #3591。2026-11-23 セビーリャさんの発案）。

市場経済の島の商社は、島の市場に出た物を買って輸出するだけで、輸出の狙う量は「島の売り手の売りたい
量」で頭打ちになる（traders.update_targets）。そのため、今の産出を超える海外の需要は作り手に届かず、
輸出に向けて作る量も設備も増えなかった。計画経済の輸出計画（economy/export_plan.py）が国の命令で
することを、市場経済では商社と作り手の契約がする形にする。現実では、石炭・鉄鉱石の長期の引き取り
契約（offtake）と、商社が工場に出す先の注文に当たる。

決まり（新しい数は置かない。game.yaml の trade.offtake で入り切りと財を選ぶ。既定は切り）:
- 申し込み: 計画経済でない島の商社は、trade.offtake.goods の財を輸出しているとき、頭打ちにする前の
  狙う量（traders の item["xo"]。圏で売れた量と圏の買えなかった量の分け前から、狙う量と同じ決まりで
  育てた量）まで、島の作り手（その財を作る独立採算の施設）に引き取り契約を申し込む（propose）。
  増やすのは、その財が輸出で引き合う（traders.exportable）ときだけ。量は商社・財ごとの契約の合計に
  掛ける。
- 引き受け: 作り手は、本当に余る分まで引き受ける: 全力の産出（設備の傷みの分を引く。原料か電力が
  足りずに動けない施設は動けた割合まで）− 国内に売る見込み − すでに結んだ契約の量。国内に売る見込み
  は、売れ行きの見込み（economy["expected"]）から、前のターンに商社が市場で買った量（economy の
  OFFTAKE_SPOT。商社の輸出向けの仕入れが見込みに入っている分）を引いた分（二重数えの差し引き）。
  余る分の多い作り手から順に。引き受けられなかった量は、作り手の売り逃し（record_missed。設備投資の
  合図）と、島の品切れの記録（参入の合図）に重み 1 で足す（全力の産出で割った割合を、作り手の全力の
  産出の比で分ける）。増設が動けば、次のターンからの申し込みで引き受ける量が増える。
- 期間: 無期限で、商社の狙う量が契約の合計を下回るか、輸出をやめたら、新しい契約から順に
  trade.contract_cancel_turns ターン後に縮める・やめる（今の国どうしの契約と同じやめ方）。作り手が
  閉鎖に入るかなくなれば、その場で終わる。
- 納品: 市場の段の板寄せの前（作り手が売りに出す前）に、契約の量を作り手の在庫から商社の輸出の荷へ
  移し（deliver）、代金を「量 × 前の値段の値幅の上端 ＋ 商社の運賃」だけ商社のお金から取り置く。
  在庫か商社のお金が足りなければ、あるだけ。板寄せの後に、このターンの島の市場の値段で精算する
  （settle。上乗せ・最低の値段は置かない）。運賃は市場と同じく、それぞれの施設の率で島の運送業に
  払う。約定した分は商社が引き取り、輸出できなかった分は商社の荷に残る（外貨は輸出できたときに
  入る）。
- 足りなかったとき: 違約金は置かない。作り手が作れなかった・商社が払えなかった分を記録し、
  trade.contract_cancel_turns ターン続けて足りなければ、その間に渡せた量の平均まで縮める（0 なら
  終わる）。
- 作る側: 操業目標に、契約の量から前のターンに商社が市場で買った量を引いた分（重み 1）を足す
  （modules/market の operating_target。committed_extra）。

契約は ``island.ext["trade"]["offtake"]`` に、id・trader（商社の施設の番号）・maker（作り手の施設の
番号）・good・qty（1ターンの量）・start（結んだターン）・change（[効くターン, 新しい量]。縮める・
やめる予告）・recent（直近の渡せた量。古い順、contract_cancel_turns まで）・short（続けて足りな
かったターン数）で持つ。

初版に入れない物: 1 ターンずつの注文、政府の国内向け義務（別の論点）、複数の仕入れ先への按分、
初回の納期までに動く増設の分を先に引き受けること（増設は今の建て増しの決まりで動き、動いた後の
申し込みで引き受ける）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock, stops
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    Payer,
    books,
    entity_of,
    equipment,
    facility_entity,
    funds,
    is_self_supporting,
)
from hakoniwa_core.market import auction
from hakoniwa_core.state import Facility, IslandState, TradeRecord
from hakoniwa_core.trade import KEY as TRADE_KEY
from hakoniwa_core.trade import is_trader, island_state, traders, traders_of
from hakoniwa_core.units import QUANTITY_SCALE, money_of, part, to_basis

KEY = "offtake"
# 作り手の economy: 財 → このターンに引き受けられなかった量（record_missed が設備投資の合図に足す）
REFUSED = "offtake_refused"
# 作り手の economy: 財 → 前のターンに商社が島の市場で買った量（二重数えの差し引き）
SPOT = "offtake_spot"
# island.scratch: このターンの納品 [(契約の id, 量, 取り置いた額)]
HELD = "offtake_held"
# island.scratch: 財 → 引き受け手のなかった量（firms の品切れの記録に足す。参入の合図）
UNMET = "offtake_unmet"
# island.scratch: 財 → このターンに契約で商社に渡した量（traders.update_targets が島の売り手の
# 売りたい量に足す。契約で引き取った分が市場に出なくなり、輸出をやめたと読まないように）
DELIVERED = traders.OFFTAKE_DELIVERED
# 商社の財の状態: 頭打ちにする前の輸出の狙う量の直近 contract_cancel_turns ターン分（_target）
SEEN = "offtake_seen"


def enabled(config: GameConfig) -> bool:
    return bool(config.get("trade.offtake.enabled"))


def goods(config: GameConfig) -> tuple[str, ...]:
    return tuple(config.get("trade.offtake.goods") or ())


def contracts(island: IslandState) -> list[dict[str, Any]]:
    return island_state(island).setdefault(KEY, [])


def _rows(island: IslandState) -> list[dict[str, Any]]:
    """結んでいる契約（ない島に貿易の状態を作らない）。"""
    return island.ext.get(TRADE_KEY, {}).get(KEY, [])


def _next_id(island: IslandState) -> int:
    rows = contracts(island)
    return max((c["id"] for c in rows), default=0) + 1


def committed(island: IslandState, f: Facility, good: str) -> int:
    """作り手 f が財 good で結んでいる契約の 1 ターンの量の合計（縮める予告の間も、効くまでは今の
    量）。"""
    return sum(c["qty"] for c in _rows(island) if c["maker"] == f.id and c["good"] == good)


def committed_extra(island: IslandState, f: Facility, good: str) -> int:
    """操業目標に足す量: 契約の量から、前のターンに商社が島の市場で f から買った量（売れ行きの見込み
    にもう入っている分）を引いた分（重み 1。二重数えの差し引き）。"""
    total = committed(island, f, good)
    if total <= 0:
        return 0
    return max(0, total - f.economy.get(SPOT, {}).get(good, 0))


def _can_make(defs: Defs, config: GameConfig, f: Facility, good: str) -> bool:
    if not f.built or not is_self_supporting(f) or not fac.on_map(f):
        return False
    if "closing" in f.economy or is_trader(config, f):
        return False
    return fac.outputs_of(defs, f).get(good, 0) > 0


def spare(island: IslandState, defs: Defs, config: GameConfig, f: Facility, good: str) -> int:
    """作り手 f が新しく引き受けられる 1 ターンの量（本当に余る分）。"""
    full = fac.outputs_of(defs, f).get(good, 0)
    cap = fac.scale(full, min(fac.FULL, equipment.condition(f)))
    if stops.caps_target(config, f.status):  # 原料か電力が足りずに動けない
        cap = min(cap, fac.scale(full, f.operating_rate))
    expected = f.economy.get("expected", {}).get(good, 0)
    domestic = max(0, expected - f.economy.get(SPOT, {}).get(good, 0))
    return max(0, cap - domestic - committed(island, f, good))


def _effective(c: dict[str, Any]) -> int:
    change = c.get("change")
    return change[1] if change else c["qty"]


def _schedule(c: dict[str, Any], turn: int, qty: int) -> None:
    change = c.get("change")
    if change is None or qty < change[1]:
        c["change"] = [change[0] if change else turn, max(0, qty)]


def _target(item: dict[str, Any] | None, cancel: int) -> int:
    """商社が契約で引き取りたい量: 圏で実際に売れた輸出の量（頭打ち前の狙う量まで）の、直近
    contract_cancel_turns ターンの一番小さい値（予告の長さと同じ間ずっと売れている量）。
    2026-11-23 の比べで、頭打ち前の狙う量（試しの上乗せを含み、圏で売れる量より多い）で結んだ
    契約の荷が商社に積み上がり、市場での買いがなくなって、作り手から見た売れ行きがかえって減った
    （x870-offtake-diag）。売れる見込みのうち島の売り手の売りたい量で抑えた分は、契約にせず
    投資の合図にする（_signal）。"""
    if item is None:
        return 0
    exporting = item.get("dir") == traders.EXPORT
    now = min(item.get(traders.UNCAPPED_X, 0), item.get(traders.EXPORTED, 0)) if exporting else 0
    seen = item.setdefault(SEEN, [])
    seen.append(now)
    del seen[: max(0, len(seen) - cancel)]
    return min(seen) if len(seen) >= cancel else 0


def _signal(
    island: IslandState, defs: Defs, config: GameConfig, good: str, qty: int, unmet: dict[str, int]
) -> None:
    """引き受けられない輸出の買いの量 qty を、作り手の売り逃し（設備投資の合図）と島の品切れの
    記録（参入の合図）に重み 1 で足す。"""
    if qty <= 0:
        return
    unmet[good] = unmet.get(good, 0) + qty
    makers = [m for m in island.facilities if _can_make(defs, config, m, good)]
    outputs = {m.id: fac.outputs_of(defs, m).get(good, 0) for m in makers}
    whole = sum(outputs.values())
    for m in makers:
        if whole <= 0:
            break
        refused = m.economy.setdefault(REFUSED, {})
        refused[good] = refused.get(good, 0) + qty * outputs[m.id] // whole


def propose(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """市場の段の初め: 予告の効いた契約を縮め・やめ、商社の申し込みを作り手が引き受ける。"""
    if not enabled(config) or traders.is_planned(island):
        return
    for m in island.facilities:
        m.economy.pop(REFUSED, None)
    rows = contracts(island)
    cancel = config.get("trade.contract_cancel_turns")
    by_id = {f.id: f for f in island.facilities}
    working = {f.id: f for f in traders_of(island, config)}
    keep = []
    for c in rows:
        change = c.get("change")
        if change and change[0] <= turn:
            c["qty"] = change[1]
            del c["change"]
        maker = by_id.get(c["maker"])
        if (
            c["qty"] <= 0
            or c["trader"] not in working
            or maker is None
            or "closing" in maker.economy
        ):
            continue
        keep.append(c)
    rows[:] = keep
    if not goods(config):
        return
    found = traders.prospects(island, defs, config)
    unmet = island.scratch.setdefault(UNMET, {})
    for f in traders_of(island, config, turn):
        for good in goods(config):
            mine = sorted(
                (c for c in rows if c["trader"] == f.id and c["good"] == good),
                key=lambda c: c["id"],
            )
            item = traders.goods_of(f).get(good)
            target = _target(item, cancel)
            p = found.get(good)
            if (
                item is not None
                and item.get("dir") == traders.EXPORT
                and traders.exportable(p, traders.home_price(island, config, good))
            ):
                # 圏で売れる見込みのうち、島の売り手の売りたい量で抑えた分（頭打ち前 − 頭打ち後）
                _signal(
                    island,
                    defs,
                    config,
                    good,
                    item.get(traders.UNCAPPED_X, 0) - item.get("x", 0),
                    unmet,
                )
            total = sum(_effective(c) for c in mine)
            if total > target:  # 新しい契約から縮める（予告して contract_cancel_turns 後）
                over = total - target
                for c in reversed(mine):
                    if over <= 0:
                        break
                    now = _effective(c)
                    cut = min(over, now)
                    _schedule(c, turn + cancel, now - cut)
                    over -= cut
                continue
            for c in mine:  # 狙う量が戻れば、縮める予告を取り消す
                if c.get("change") and total - _effective(c) + c["qty"] <= target:
                    total += c["qty"] - _effective(c)
                    del c["change"]
            ask = target - sum(_effective(c) for c in mine)
            if ask <= 0 or not traders.exportable(p, traders.home_price(island, config, good)):
                continue
            makers = [m for m in island.facilities if _can_make(defs, config, m, good)]
            room = {m.id: spare(island, defs, config, m, good) for m in makers}
            for m in sorted(makers, key=lambda x: (-room[x.id], x.id)):
                take = min(ask, room[m.id])
                if take <= 0:
                    break
                same = next((c for c in mine if c["maker"] == m.id and not c.get("change")), None)
                if same is not None:
                    same["qty"] += take
                else:
                    c = {
                        "id": _next_id(island),
                        "trader": f.id,
                        "maker": m.id,
                        "good": good,
                        "qty": take,
                        "start": turn,
                        "recent": [],
                        "short": 0,
                    }
                    rows.append(c)
                    mine.append(c)
                ask -= take
            # 引き受けられなかった分は、作り手の売り逃しと島の品切れの記録に重み 1 で足す
            _signal(island, defs, config, good, ask, unmet)
    stats = island.stats
    for c in rows:
        k = f"offtake.qty.{c['good']}"
        stats[k] = stats.get(k, 0) + c["qty"]
    for good, qty in unmet.items():
        stats[f"offtake.unmet.{good}"] = qty


def deliver(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """市場の段の板寄せの前: 契約の量を作り手の在庫から商社の輸出の荷へ移し、代金を取り置く。"""
    rows = _rows(island)
    if not rows or not enabled(config):
        return
    from hakoniwa_core.economy import freight as freight_rates

    cancel = config.get("trade.contract_cancel_turns")
    rates = freight_rates.facility_rates(island, defs, config)
    width = to_basis(config.get("market.band"))
    prices = island.economy.prices
    by_id = {f.id: f for f in island.facilities}
    held: list[tuple[int, int, int]] = []
    delivered = island.scratch.setdefault(DELIVERED, {})
    for c in sorted(rows, key=lambda x: x["id"]):
        f, m, good = by_id[c["trader"]], by_id[c["maker"]], c["good"]
        qty = min(c["qty"], stock.total(m.stock, good))
        _, high = auction.band(prices.get(good, 1), width)
        unit = high + part(high, rates.get(f.id, 0))
        room = funds.available(island, Payer(entity_of(config, f)))
        qty = min(qty, room * QUANTITY_SCALE // max(1, unit))
        commit = money_of(unit, qty)
        if qty > 0:
            batches = stock.take_batches(m.stock, good, qty)
            books.stock_moved(island, m.id, good, -qty)
            stock.put_batches(f.stock, good, batches)
            books.stock_moved(island, f.id, good, qty)
            traders.entry(f, good)["held"][0] += qty  # 仕入れ値は精算で足す
            funds.commit(island, Payer(entity_of(config, f)), commit)
            delivered[good] = delivered.get(good, 0) + qty
        held.append((c["id"], qty, commit))
        c["recent"] = [*c.get("recent", []), qty][-cancel:]
        c["short"] = c.get("short", 0) + 1 if qty < c["qty"] else 0
        if c["short"] >= cancel:  # 続けて足りなければ、渡せた量の平均まで縮める（0 なら終わる）
            c["qty"] = sum(c["recent"]) // len(c["recent"])
            c["short"] = 0
            c.pop("change", None)
    island.scratch[HELD] = held


def settle(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    results: dict[str, auction.Result],
    transport: tuple[str, int | None],
) -> None:
    """市場の段の板寄せの後: 納品した分を、このターンの島の市場の値段で精算する。"""
    if not enabled(config):
        return
    _note_spot(island, config)
    held = island.scratch.pop(HELD, [])
    if not held:
        return
    from hakoniwa_core.economy import freight as freight_rates

    carrier, carrier_id = transport
    rates = freight_rates.facility_rates(island, defs, config)
    prices = island.economy.prices
    by_id = {f.id: f for f in island.facilities}
    rows = {c["id"]: c for c in contracts(island)}
    stats = island.stats
    for cid, qty, commit in held:
        c = rows[cid]
        f, m, good = by_id[c["trader"]], by_id[c["maker"]], c["good"]
        payer = Payer(entity_of(config, f))
        if qty <= 0:
            continue
        result = results.get(good)
        price = result.price if result is not None and result.volume > 0 else prices.get(good, 1)
        amount = money_of(price, qty)
        buyer_freight = part(amount, rates.get(f.id, 0))
        seller_freight = part(amount, rates.get(m.id, 0))
        paid = min(commit, amount + buyer_freight)
        amount = min(amount, paid)
        buyer_freight = paid - amount
        funds.pay(island, payer, entity_of(config, m), amount, "sale", committed=True)
        funds.pay(island, payer, carrier, buyer_freight, "freight", committed=True)
        funds.release(island, payer, commit - paid)
        ledger.pay(island, entity_of(config, m), carrier, seller_freight, "freight")
        if carrier_id is not None:
            books.add(island, carrier_id, books.SALES, buyer_freight + seller_freight)
        island.economy.freight["paid"] = (
            island.economy.freight.get("paid", 0) + buyer_freight + seller_freight
        )
        books.add(island, m.id, books.SALES, amount)
        books.add(island, m.id, books.FREIGHT, seller_freight)
        books.add(island, f.id, books.PURCHASES, amount)
        books.add(island, f.id, books.FREIGHT, buyer_freight)
        item = traders.entry(f, good)
        item["held"][1] += amount + buyer_freight
        record = traders.state_of(f).setdefault("turn", {})
        record["spent"] = record.get("spent", 0) + amount + buyer_freight
        island.trades.append(
            TradeRecord(good, facility_entity(f.id), facility_entity(m.id), qty, amount)
        )
        for k, v in ((f"offtake.delivered.{good}", qty), (f"offtake.paid.{good}", amount)):
            stats[k] = stats.get(k, 0) + v


def _note_spot(island: IslandState, config: GameConfig) -> None:
    """このターンに商社が島の市場で作り手から買った量を、作り手ごとに残す（次のターンの二重数えの
    差し引き。契約の精算の記録より前の、板寄せの約定だけ）。"""
    names = {facility_entity(f.id) for f in traders_of(island, config)}
    wanted = set(goods(config))
    by_name = {facility_entity(f.id): f for f in island.facilities}
    for f in island.facilities:
        f.economy.pop(SPOT, None)
    for record in island.trades:
        if record.good in wanted and record.buyer in names and record.seller in by_name:
            m = by_name[record.seller]
            spot = m.economy.setdefault(SPOT, {})
            spot[record.good] = spot.get(record.good, 0) + record.quantity
