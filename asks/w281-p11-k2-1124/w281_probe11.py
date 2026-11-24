"""probe11（probe10 に、W-308 の測りの下地 2026-11-24 を足した物。会議の中間のまとめ 3・4。読むだけ。計算は変えない）:
- ``ac``・``am``・``aq``（島 -> 全部の島ターンの数のリスト。1000 分の 1 トン）: 圏の市場の前の割り振り（_allocate）で、
  1 ターンに流れる分のうち扱える重さで削った重さ（cut_capacity）・お金で削った重さ（cut_money）、割り振った重さ（qty）の
  島の商社の合計。財ごとのわけは 1 つだけ残る（扱える重さで切ると M が C に書き換わる）ので、わけの文字でなく量で両方残す。
  計画経済の島は割り振りの外なので 0。
- ``hw``（同じ）: 動いている商社の、雇いたい人数に届かない人数の合計（扱える重さはあるが人が足りない量の下地）。
- ``iv``（同じ）: 動いている商社のこのターンの扱った重さの合計（1000 分の 1 トン）。

probe10（probe9 に、会議 #4185 (b) と W-308 の下調べ 2026-11-24 を足した物。読むだけ。計算は変えない）:
- ``po``（marks と同じ並び。印 s・w の島ターンだけ、ほかは ""）: そのターンに島の商社が圏の市場に出した輸入の買い注文の
  財の数（0〜9、9 以上は 9）。0 なら注文を出す前に切られた（わけは sy）。1 以上で印が s・w なら、注文は出たが約定しなかった。
- ``ow``（同じ並び、s・w だけ）: 出した輸入の買い注文の重さの合計 ÷（扱える重さ × 動いている商社の数）を 10 分の 1 の段（0〜9）で。
- ``gp``（島 -> 全部の島ターンの数のリスト）: 1 人あたりの実質の産出（stats の gdp ÷（cpi ÷ 100）÷ 人口。自国通貨。
  島の間の比べは同じ種・同じターンの順位で見る）。
- ``pp``（同じ）: 人口。``pl``（marks と同じ並びの文字列）: 計画経済の島 ``p``、ほか ``m``。

probe9（probe8 に、601〜900T の長い途切れの下調べと新しい物差しの下地 2026-11-23 を足した物。会議 #3335 の続き）:
- ``od``（marks と同じ並び、全部の島ターン）: 海運業が寿命を過ぎた船を使い続けているか（economy["overdue"] > 0 なら
  ``1``、0 なら ``0``、海運業がなければ ``-``）。
- ``sg``（同じ並び、全部の島ターン）: そのターンの島の本当の品不足（TRUE_SHORT）が、動いている商社が輸入を狙って
  いる財（dir が輸入で狙う量 m > 0）にあったか: ``y``・``n``、市場の段の記録がなければ ``?``、そういう財がなければ ``-``。
- ``sv``（同じ並び）: その不足の重さ = 不足のあった輸入を狙う財のうち一番大きい「不足 ÷ 島の市場の需要」を 10 分の 1 の
  段（0〜9、0.9 以上は 9）で。狙う財がないか記録がなければ ``-``。
- ``gg``（同じ並び）: 印 s・w の島ターンの、その不足のあった輸入を狙う財の名前（「,」でつなぐ）。s・w 以外は ""。

probe8（probe7 に、運賃が上がったわけと商社の扱える重さの詰まりの下調べ 2026-11-23 を足した物）:
- ``world``: ターンごとの世界の数（海運業ごとの値の中央値と合計）。運賃 rate、船の値段（島の値段）、運航費（船員の
  賃金と燃料）、1 隻 1 ターンの資本費（直近の capital_costs ÷ 延べ隻数）、船の使い具合 u（直近の運んだ量 ÷ 船団を
  全部使った量）、船団の合計、寿命を過ぎて使い続けている船の合計、輸出・輸入の買い付けの合計、商社の輸出の荷の
  重さの合計 ÷ 扱える重さの合計、動いている商社の数。
- ``sc``: 印 s・w の島ターンの、商社の輸出の荷が扱える重さを占める割合（動いている商社の中の一番大きい物）を
  10 分の 1 の段（0〜9、1 以上は 9）で。s・w 以外は ""。

probe7（probe6 に、#399 で「需要あり・届かない」が増えたわけの下調べ 2026-11-23 を足した物。下の「probe7」）。

probe7: 印 s・w の島ターンごとに、動いている商社の輸入の財（dir が輸入）の「届かなかったわけ」（item["why"]。貿易の
画面と同じ物）を ``sy``（島 -> marks と同じ並びの文字列のリスト。s・w 以外は ""）に残す。文字は WHY の頭:
``N`` 圏に値段がない、``P`` 運賃と使用料を引くと引き合わない、``H`` 空いた船が足りない、``C`` 商社の扱える重さ、
``M`` 外貨が足りない、``S`` 圏の売りが足りないか売りの指値が高い、``F`` 商社の預金が足りない、``Q`` 割り当て、
``K`` 封鎖、``R`` 外貨の取り置き、``x`` 使う圏がない、``.`` わけの記録なし（狙う量が 0 など）、``-`` 輸入の財がない。
あわせて ``sf``（同じ並び、全部の島ターン）に、そのターンの自国の運賃（海運業の economy["rate"]。なければ -1）を残す。

probe6（probe5 に会議 #3248 の頼み 2026-11-23 を足した物。下の「probe6」）。

probe5（probe4 に条件 4・5 の追記 2026-11-22 の数を足した物）: #399＋W-281 の条件（w281-criteria.md）の
3〜7 を数える調べ（読むだけ。計算は変えない）。

使い方: python w281_probe.py --scenario plan --seed 13 --turns 1200 --out probe.json
4 組（main・#399・#399+W-281・0.75）とも同じ台本で回す。数えるのは:

- 条件 3（§2）: 島ターンごとの印。前のターンの終わりに動いている商社がない ``u``（未確認）、
  商社の輸入の見込み（traders.IMPORT_NEED）がない ``n``、見込みがあり輸入の買い付け（trade.imports）が
  0 ``s``、届いた ``o``。``s`` のうち、そのターンに商社が銀行の体力で借りきれなかった（bank_wall）物は ``w``。
- 条件 4: 自国の船 0.5 隻未満で、他国の船も借りなかった（import.freight = 0）島ターン。
- 条件 5: 海運業の倒産（facility_bankrupt）と取り壊し（foreclosure_demolished・facility_demolished）。
- 掲示板 #1563（x870-w281_probe2.py で足した）: 全ターンの施設の種類ごとの倒産・取り壊し
  （fail_by_kind）と、動いていた施設 × ターン（alive_by_kind。率の分母）、海運業がいた島 × ターン。
- probe3: 条件 4 の島ターンを条件 3 の印（u・n・s・w・o）で分けた数（no_transport_by_mark。n は
  輸入の見込みがない島）と、海運業の倒産・取り壊しの 1 件ずつの前のターンの様子（failures: 年、
  開き直しか、持ち主、船、預金、借り、運賃、使い具合、買いたい船、営業の現金収支の直近 6 つ）。
- 条件 6: 島の stats の ship_loans.*（W-281 の版だけにある。ターンごとの値を足す）。
- 参考（判定に使わない）: 船の借りの審査があった島ターンごとの [船の使い具合（直近に航海に出ていた船 ÷
  船団）, 稼ぎ, 返済, 件数]（ship_loans.review_*。運賃の取り方の問題か、船が余っている島の問題かを分ける）。
- 条件 7: 寿命の来た船のうち直近に使っていた分（前のターンの終わりの船団で、このターンに寿命に
  達する船のうち、寿命の前の船で使った分が足りない分）と、それを 30 ターン以内に買い替えた分（このターンに
  船団に入った船を古い順に当てる）。寿命が 151 ターン以降・（最後のターン − 30）までに来た分だけ数える。

probe6（会議 #3248・#3267・#3268）:
- 印 u（前のターンの終わりに動いている商社がない）のわけ ``ur``（marks と同じ並び。u 以外は ``.``）:
  ``v`` 商社がまだ一度も建っていない、``b`` 建てている途中、``q`` 閉鎖の途中、``z`` 差し押さえ（管財人が
  動かしていない）、``h`` 暴動・災害・占領で止まっている、商社がいなくなった後は最後の出来事で ``k`` 倒産、
  ``f`` 差し押さえの後の取り壊し、``d`` 取り壊し、``c`` 閉鎖、``g`` 出来事の記録なし、``p`` 島に経済が
  なかった（前のターンの記録なし）。
- 同じ島ターンの輸入の見込み ``ui``（前のターンの終わりの traders.prospects。商社がいなくても島の値段と
  運賃で見る）: ``i`` 引き合う財がある、``-`` ない、``x`` 港がないか圏に入っていない。
- ``sh``: その島ターンの島の不足（市場の段の TRUE_SHORT。約定の値段以上を付けたのに買えなかった量）:
  ``S`` 今の値段と運賃で輸入が引き合う財（traders.importable）に不足がある、``s`` 港で扱う財
  （traders.prospects に出る財）にだけある、``o`` それ以外の財にだけある、``0`` ない、
  ``?`` 市場の段の記録がない（読めない）。全部の島ターン（marks と同じ並び）。
- ``un``: u の島ターンで、商社が消える直前（最後に動いていたターンの終わり）の IMPORT_NEED: ``y``・``n``、
  一度も動いていなければ ``?``。u 以外は ``.``。
- ``u_since``: u の島ターンごとの、最後に動いている商社がいたターンからの数（一度もいなければ -1）。
- 海運業への運転資金の貸し出し（banking の _borrow・_creditworthy を数えるだけ。計算は変えない）:
  ``ship_wc`` = {前半（〜600T）/後半: [借りに行った回数, 審査した回数, 審査で断った回数, 頼んだ額, 貸した額,
  計画経済の島（審査なし）の回数, 審査が営業の現金収支の記録（直した式の値）で返す力を見た回数, そのうち
  船を保つ買い替えの額（ships.fleet_wear）が 0 より大きかった回数]}。返済の借り換え（repaying あり）と運転資金を分けて数える。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque

from hakoniwa_core import ledger, measure
from hakoniwa_core.economy import SEIZED, banks, facility_entity, in_receivership, is_seized, is_stopped
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core import stock
from hakoniwa_core.trade import port_of, shipping_of, ships, traders, traders_of, weight

def used_ships(config, f):
    """直近に一番多く航海に出ていた船（1000 分の 1 隻）。ships.used_ships と同じ式（main にはないので
    ここで数える）。"""
    return ships.ships_of_load(config, max(f.economy.get("used", []), default=0))


START = 151
WINDOW = 30
MILLI = 1000


SKIP_KINDS = {"housing", "government_office"}
GRID = (0.25, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0, 3.0)


WHY_CHAR = {
    "no_price": "N", "price": "P", "ships": "H", "capacity": "C", "money": "M", "sellers": "S",
    "funds": "F", "quota": "Q", "blockade": "K", "fx_priority": "R",
}
GONE = {"facility_bankrupt": "k", "foreclosure_demolished": "f", "facility_demolished": "d", "facility_closed": "c"}
SHIP_WC = {}  # (前半か後半, 借り換えか) -> [借りに行った, 審査した, 断った, 頼んだ額, 貸した額, 計画経済,
#   審査が営業の現金収支の記録（直した式の値）を使った, そのうち船を保つ買い替えの額が 0 より大きかった]
_SHIP_KIND = [None]
_IN_SHIP = [None]  # 今 _borrow している海運業の鍵


def _wrap_banking():
    """海運業の運転資金の借りを数えるだけの包み（probe6）。"""
    orig_borrow = Banking._borrow
    orig_credit = Banking._creditworthy

    def borrow(self, island, ctx, f, amount, lent, planned, repaying=None):
        if f.kind != _SHIP_KIND[0]:
            return orig_borrow(self, island, ctx, f, amount, lent, planned, repaying)
        key = ("late" if ctx.turn > 600 else "early", "refi" if repaying is not None else "wc")
        row = SHIP_WC.setdefault(key, [0] * 8)
        row[0] += 1
        row[3] += amount
        if planned:
            row[5] += 1
        _IN_SHIP[0] = key
        try:
            left = orig_borrow(self, island, ctx, f, amount, lent, planned, repaying)
        finally:
            _IN_SHIP[0] = None
        row[4] += amount - left
        return left

    def credit(island, ctx, f, amount, offers):
        ok = orig_credit(island, ctx, f, amount, offers)
        key = _IN_SHIP[0]
        if key is not None and f.kind == _SHIP_KIND[0]:
            row = SHIP_WC[key]
            row[1] += 1
            if not ok:
                row[2] += 1
            config = ctx.config
            history = banks.ebitda(f)[-config.get("firms.ebitda_turns"):]
            young = ctx.turn - f.economy.get("entered", -(10**9)) <= config.get("loans")["grace_turns"]
            if history and not young:  # _creditworthy と同じ分け方: 記録の平均で返す力を見た
                row[6] += 1
                if hasattr(ships, "fleet_wear") and ships.fleet_wear(island, config, f, ctx.turn) > 0:
                    row[7] += 1
        return ok

    Banking._borrow = borrow
    Banking._creditworthy = staticmethod(credit)


ORDERS = defaultdict(dict)  # probe10: 島 -> 財 -> 出した輸入の買い注文の重さ（このターン）


def _wrap_orders(defs):
    """輸入の買い注文を数えるだけの包み（probe10）。"""
    from hakoniwa_core.modules.trade import _Round as Trade  # 輸入の注文は 1 回の貿易の段（_Round）の物

    orig = Trade._import_order

    def order(self, island, f, entity, item, good, bloc, left, money_left):
        got = orig(self, island, f, entity, item, good, bloc, left, money_left)
        if got is not None and got.quantity > 0:
            row = ORDERS[island.id]
            row[good] = row.get(good, 0) + weight(defs, good, got.quantity)
        return got

    Trade._import_order = order


ALLOC = defaultdict(lambda: [0, 0, 0])  # probe11: 島 -> [cut_capacity, cut_money, qty]（重さ）


def _wrap_alloc(defs):
    """割り振りの結果を数えるだけの包み（probe11）。"""
    from hakoniwa_core.modules.trade import _Round

    orig = _Round._allocate

    def allocate(self):
        orig(self)
        for (i, _fid, good), plan in self.alloc.items():
            row = ALLOC[i]
            row[0] += weight(defs, good, plan.cut_capacity)
            row[1] += weight(defs, good, plan.cut_money)
            row[2] += weight(defs, good, plan.qty)

    _Round._allocate = allocate


def _export_weight(defs, f) -> int:
    return sum(
        weight(defs, good, stock.total(f.stock, good))
        for good, item in traders.goods_of(f).items()
        if item.get("dir") == traders.EXPORT
    )


def _median(values):
    values = sorted(values)
    if not values:
        return None
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _world_row(world, game, config, cap, ship_good, t):
    rates, prices, runs, caps, uses = [], [], [], [], []
    fleet_sum = overdue = exports = imports = ex_w = n_tr = 0
    for island in world.islands.values():
        if not island.has_economy:
            continue
        exports += island.stats.get("trade.exports", 0)
        imports += island.stats.get("trade.imports", 0)
        for x in traders_of(island, config, t):
            ex_w += _export_weight(game.defs, x)
            n_tr += 1
        f = shipping_of(island, config)
        if f is None:
            continue
        fleet_sum += ships.fleet(f)
        overdue += f.economy.get("overdue", 0)
        prices.append(island.economy.prices.get(ship_good, 0))
        runs.append(ships._ship_run_cost(island, config))
        if "rate" in f.economy:
            rates.append(f.economy["rate"])
        fleets = f.economy.get("fleets", [])
        if sum(fleets) > 0:
            caps.append(sum(f.economy.get("capital_costs", [])) / (sum(fleets) / MILLI))
            uses.append(sum(f.economy.get("carried", [])) / (sum(fleets) * ships._full_use(config)))
    return [t, _median(rates), _median(prices), _median(runs), _median(caps), _median(uses),
            fleet_sum / MILLI, overdue / MILLI, exports, imports, ex_w / (cap * n_tr) if n_tr else 0.0, n_tr]


def _trader_reason(island, config, t, ever, gone_by) -> str:
    kind = config.get("trade.trader_facility")
    all_tr = [x for x in island.facilities if x.kind == kind]
    if not all_tr:
        return (gone_by or "g") if ever else "v"
    if any(not x.built for x in all_tr) and not any(x.built for x in all_tr):
        return "b"
    if any(x.built and "closing" in x.economy for x in all_tr):
        return "q"
    if any(x.built and SEIZED in x.economy and is_stopped(x) for x in all_tr):
        return "z"
    if any(x.built and not is_stopped(x) for x in all_tr):
        return "h"
    return "?"


def _import_hope(island, defs, config) -> str:
    if port_of(island, config) is None or not traders.views(island):
        return "x"
    return "i" if any(traders.importable(p) for p in traders.prospects(island, defs, config).values()) else "-"


def _importable_at(island, defs, config, rate) -> bool:
    real = ships.freight_rate
    ships.freight_rate = lambda *a, **k: rate
    try:
        return any(traders.importable(p) for p in traders.prospects(island, defs, config).values())
    finally:
        ships.freight_rate = real


def _k_max(island, defs, config) -> float:
    """船がなく輸入の見込みもなかった島で、運賃を同じ見積もり方（今の値段の船を半分使ったとき:
    運航費 ＋ その版の 1 隻の資本費 × min(1, 2 × 0.5)）にしたら、その何倍まで引き合う財が残るか
    （GRID の中の一番大きい倍。どの倍でもなければ 0。probe5。条件 4 の n の判定し直し）。"""
    est = ships._rate(config, ships._ship_run_cost(island, config), ships._ship_wear(island, config), 0.5)
    lo, hi = -1, len(GRID) - 1
    if _importable_at(island, defs, config, round(est * GRID[hi])):
        return GRID[hi]
    while hi - lo > 1:  # 引き合うのは倍が小さい側。lo は引き合う（-1 は未確認）、hi は引き合わない
        mid = (lo + hi) // 2
        if _importable_at(island, defs, config, max(1, round(est * GRID[mid]))):
            lo = mid
        else:
            hi = mid
    return GRID[lo] if lo >= 0 else 0.0


def _freight_made_n(island, defs, config, f, t) -> bool:
    """輸入の見込みがなかった島で、運賃から船の利子を除いた見積もり（傷みだけの資本費）なら引き合う
    輸入の財が出たか（probe4。条件 4 の n を運賃の見積もりで見直す）。"""
    fleets = f.economy.get("fleets", [])
    if not sum(fleets) or "rate" not in f.economy:
        return False
    loads = f.economy.get("carried", [])
    use = sum(loads) / (sum(fleets) * ships._full_use(config))
    own = ships.fleet(f)
    if own <= 0:
        return False
    wear = ships.fleet_wear(island, config, f, t) / (own / MILLI) if hasattr(ships, "fleet_wear") else (
        island.economy.prices.get(config.get("trade.ship_good"), 0) / config.get("trade.ship_life")
    )
    alt = ships._rate(config, ships._ship_run_cost(island, config), wear, use)
    actual = f.economy["rate"]
    if alt >= actual:
        return False
    before = {g for g, p in traders.prospects(island, defs, config).items() if traders.importable(p)}
    f.economy["rate"] = alt
    try:
        after = {g for g, p in traders.prospects(island, defs, config).items() if traders.importable(p)}
    finally:
        f.economy["rate"] = actual
    return bool(after - before)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--turns", type=int, default=1200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=START)  # 試しに短く回すときだけ変える
    a = ap.parse_args(argv)
    game = load_game(None)
    config = game.config
    _SHIP_KIND[0] = config.get("trade.shipping_facility")
    _wrap_banking()
    _wrap_orders(game.defs)
    _wrap_alloc(game.defs)
    from hakoniwa_core.trade import staff as trade_staff
    ac = defaultdict(list)  # probe11
    am = defaultdict(list)  # probe11
    aq = defaultdict(list)  # probe11
    hw = defaultdict(list)  # probe11
    iv = defaultdict(list)  # probe11
    po = defaultdict(list)  # probe10
    ow = defaultdict(list)  # probe10
    gp = defaultdict(list)  # probe10
    pp = defaultdict(list)  # probe10
    pl = defaultdict(list)  # probe10
    trader_kind = config.get("trade.trader_facility")
    ever_trader = set()  # 商社が建ったことのある島
    gone_by = {}  # 島 -> 最後の商社の倒産・取り壊し・閉鎖の印
    last_working = {}  # 島 -> 最後に動いている商社がいたターン
    ur = defaultdict(list)
    sh = defaultdict(list)  # 島 -> そのターンの島の不足（marks と同じ並び）
    un = defaultdict(list)  # 島 -> u のとき、商社が消える直前の IMPORT_NEED（u 以外は .）
    last_need = {}  # 島 -> 最後に商社が動いていたターンの終わりの IMPORT_NEED
    ui = defaultdict(list)
    u_since = defaultdict(list)
    sy = defaultdict(list)  # probe7
    sf = defaultdict(list)  # probe7
    sc = defaultdict(list)  # probe8
    od = defaultdict(list)  # probe9
    sg = defaultdict(list)  # probe9
    gg = defaultdict(list)  # probe9
    sv = defaultdict(list)  # probe9
    world_rows = []  # probe8
    cap = config.get("trade.trader_capacity") * MILLI
    ship_good = config.get("trade.ship_good")
    scenario = measure.SCENARIOS[a.scenario]
    world = generate_world(a.seed, scenario.islands, config, game.defs)
    life = config.get("trade.ship_life")
    shipping_kind = config.get("trade.shipping_facility")
    start = a.start
    last_counted = a.turns - WINDOW
    marks = defaultdict(list)  # 島 -> [(t, 印)]
    no_transport = 0
    shipping_fail = defaultdict(int)
    fail_by_kind = defaultdict(lambda: defaultdict(int))  # 種類 -> 印 -> 件数（掲示板 #1563）
    alive_by_kind = defaultdict(int)  # 種類 -> 動いていた施設 × ターン（倒産の率の分母）
    shipping_firms = 0  # 海運業がいた島 × ターン
    no_transport_by_mark = defaultdict(int)  # 条件 4 の島ターンを条件 3 の印で分ける（probe3）
    opened_at = {}  # 海運業の施設 id -> 最初に見えたターン（probe3）
    reopened_ids = set()
    snap = {}  # 島 -> 前のターンの終わりの海運業の様子（probe3）
    failures = []  # 海運業の倒産・取り壊しの 1 件ずつ（probe3）
    nt_flags = defaultdict(list)  # 島 -> 条件 4 の島ターンか（marks と同じ並び。probe4）
    fn_flags = defaultdict(list)  # 島 -> n のうち、利子なしの運賃なら引き合う財があったか（probe4）
    lives = {}  # (島, 施設 id) -> [種類, 最初に見えたターン, 最後に見えたターン, 倒産のターン, 印]
    first_turn_seen = None
    kn = defaultdict(dict)  # 島 -> {marks の位置: 運賃の見積もりの何倍まで引き合う財があったか}（probe5）
    err = [0, 0.0, 0.0]  # 運賃の見積もり（前のターン）と実際（このターン）の差の割合の 件数・和・2 乗の和
    with_interest = hasattr(ships, "fleet_capital")  # 運賃に船の利子が入った版だけ見直す
    loans = defaultdict(float)
    reviews = []  # [使い具合, 稼ぎ, 返済, 件数]（参考）
    retire_total = 0
    retire_replaced = 0
    pending = defaultdict(deque)  # 島 -> [寿命のターン, 残りの隻（1000 分の 1）]
    prev = {}  # 島 -> (商社がいたか, 見込みがあったか, 船団の写し, 使った船)
    for _ in range(a.turns):
        measure.queue_scenario_orders(world, scenario, game.defs, config)
        ORDERS.clear()
        ALLOC.clear()
        game.engine.run_turn(world)
        t = world.turn
        if first_turn_seen is None:
            first_turn_seen = t
        world_rows.append(_world_row(world, game, config, cap, ship_good, t))
        for i, island in world.islands.items():
            if not island.has_economy:
                continue
            stats = island.stats
            f = shipping_of(island, config)
            fleet_now = [list(x) for x in f.economy.get("fleet", [])] if f is not None else []
            own = sum(q for _, q in fleet_now)
            if f is not None:
                shipping_firms += 1
            for x in island.facilities:
                if x.built:
                    alive_by_kind[x.kind] += 1
                key = (i, x.id)
                if key not in lives:
                    lives[key] = [x.kind, t, t, -1, "o" if t == first_turn_seen else "n", True]
                life_row = lives[key]
                life_row[2] = t
                if life_row[5] and life_row[3] < 0 and x.kind not in SKIP_KINDS and (
                    life_row[4] != "o" or x.kind == shipping_kind
                ):
                    if ledger.money(island, facility_entity(x.id)) > 0:
                        life_row[5] = False  # 開いてから倒れるまで現金 0 が続いたか（probe5）
            for le in island.log:
                if le.args.get("facility") == shipping_kind and le.kind in (
                    "facility_bankrupt", "foreclosure_demolished", "facility_demolished"
                ):
                    failures.append({"t": t, "island": i, "kind": le.kind, **snap.get(i, {})})
                if le.kind in (
                    "facility_bankrupt", "foreclosure_demolished", "facility_demolished"
                ):
                    fail_by_kind[str(le.args.get("facility"))][le.kind] += 1
                if le.kind == "facility_bankrupt":
                    for x in island.facilities:
                        if (x.kind, x.x, x.y) == (le.args.get("facility"), le.args.get("x"), le.args.get("y")):
                            if (i, x.id) in lives and lives[(i, x.id)][3] < 0:
                                lives[(i, x.id)][3] = t
                            break
                if le.args.get("facility") == trader_kind and le.kind in GONE:
                    gone_by[i] = GONE[le.kind]
                if le.kind in ("shipping_opened", "shipping_opened_state"):  # W-281 の開き直し
                    shipping_fail["reopened"] += 1
                if le.args.get("facility") != shipping_kind:
                    continue
                if le.kind in ("facility_bankrupt", "foreclosure_demolished", "facility_demolished"):
                    shipping_fail[le.kind] += 1
            for key, value in stats.items():
                if key.startswith("ship_loans.") and isinstance(value, (int, float)):
                    loans[key] += value
            if stats.get("ship_loans.reviews") and f is not None:
                use = used_ships(config, f) / own if own > 0 else 0.0
                reviews.append([
                    round(use, 3),
                    stats.get("ship_loans.review_earnings", 0),
                    stats.get("ship_loans.review_service", 0),
                    stats.get("ship_loans.reviews", 0),
                ])
            p = prev.get(i)
            if t >= start:
                # 条件 3
                if p is None or not p[0]:
                    mark = "u"
                elif not p[1]:
                    mark = "n"
                elif stats.get("trade.imports", 0) > 0:
                    mark = "o"
                else:
                    walled = any(
                        traders.state_of(x).get("bank_wall") == t for x in traders_of(island, config)
                    )
                    mark = "w" if walled else "s"
                marks[i].append((t, mark))
                people = stats.get("population", 0)  # probe10
                cpi = stats.get("cpi", 0) or 100
                gp[i].append(int(stats.get("gdp", 0) / (cpi / 100) / people) if people > 0 else -1)
                pp[i].append(int(people))
                pl[i].append("p" if traders.is_planned(island) else "m")
                row = ALLOC.get(i, (0, 0, 0))  # probe11
                ac[i].append(int(row[0]))
                am[i].append(int(row[1]))
                aq[i].append(int(row[2]))
                working_now = traders_of(island, config, t)
                hw[i].append(sum(max(0, (trade_staff.wanted(config, game.defs, x) or 0) - x.workers) for x in working_now))
                iv[i].append(sum(int(traders.state_of(x).get("volume", [0])[-1] if traders.state_of(x).get("volume") else 0)
                                 for x in working_now))
                found = island.scratch.get(traders.TRUE_SHORT)
                if found is None:
                    sh[i].append("?")
                else:
                    pro = traders.prospects(island, game.defs, config) if any(found.values()) else {}
                    if any(v > 0 and traders.importable(pro.get(g)) for g, v in found.items()):
                        sh[i].append("S")
                    elif any(v > 0 and g in pro for g, v in found.items()):
                        sh[i].append("s")
                    elif any(v > 0 for v in found.values()):
                        sh[i].append("o")
                    else:
                        sh[i].append("0")
                if mark in "sw":
                    codes = set()
                    for x in traders_of(island, config, t):
                        for item in traders.goods_of(x).values():
                            if item.get("dir") != traders.IMPORT:
                                continue
                            if item.get("bm") is None:
                                codes.add("x")
                            else:
                                codes.add(WHY_CHAR.get(item.get("why"), "."))
                    sy[i].append("".join(sorted(codes)) or "-")
                    fill = max((_export_weight(game.defs, x) / cap for x in traders_of(island, config, t)), default=0.0)
                    sc[i].append(str(min(9, int(fill * 10))))
                else:
                    sy[i].append("")
                    sc[i].append("")
                sf[i].append(f.economy.get("rate", -1) if f is not None else -1)
                od[i].append("-" if f is None else ("1" if f.economy.get("overdue", 0) > 0 else "0"))
                targets = {
                    g for x in traders_of(island, config, t) for g, item in traders.goods_of(x).items()
                    if item.get("dir") == traders.IMPORT and item.get("m", 0) > 0
                }
                if not targets:
                    sg[i].append("-")
                    hit = []
                elif found is None:
                    sg[i].append("?")
                    hit = []
                else:
                    hit = sorted(g for g in targets if found.get(g, 0) > 0)
                    sg[i].append("y" if hit else "n")
                demand = {m.good: m.demand for m in island.markets}
                worst = max((found.get(g, 0) / demand[g] for g in hit if demand.get(g, 0) > 0), default=0.0) if hit else 0.0
                sv[i].append(str(min(9, int(worst * 10))) if targets and found is not None else "-")
                gg[i].append(",".join(hit) if mark in "sw" else "")
                if mark in "sw":  # probe10
                    placed = ORDERS.get(i, {})
                    n_tr = max(1, len(traders_of(island, config, t)))
                    po[i].append(str(min(9, len(placed))))
                    ow[i].append(str(min(9, int(sum(placed.values()) / (cap * n_tr) * 10))))
                else:
                    po[i].append("")
                    ow[i].append("")
                if mark == "u":
                    un[i].append(("y" if last_need[i] else "n") if i in last_need else "?")
                    ur[i].append("p" if p is None else p[7])
                    ui[i].append("." if p is None else p[8])
                    u_since[i].append(t - 1 - last_working[i] if i in last_working else -1)
                else:
                    un[i].append(".")
                    ur[i].append(".")
                    ui[i].append(".")
                fn_flags[i].append("f" if mark == "n" and p is not None and p[4] else ".")
                # 条件 4
                if own < MILLI // 2 and not stats.get("import.freight", 0):
                    no_transport += 1
                    no_transport_by_mark[mark] += 1
                    nt_flags[i].append("1")
                    if mark == "n" and p is not None and p[5] is not None:
                        kn[i][len(marks[i]) - 1] = p[5]
                else:
                    nt_flags[i].append("0")
            # 条件 7: このターンに寿命に達した船（前のターンの終わりの船団で見る）
            if p is not None:
                old_fleet, used = p[2], p[3]
                aged_now = sum(q for since, q in old_fleet if t - since == life)
                aged_all = sum(q for since, q in old_fleet if t - since >= life)
                young = sum(q for _, q in old_fleet) - aged_all
                needed = min(aged_now, max(0, used - young))
                if needed > 0 and start <= t <= last_counted:
                    pending[i].append([t, needed])
                    retire_total += needed
            added = sum(q for since, q in fleet_now if since == t)
            queue = pending[i]
            while queue and t - queue[0][0] > WINDOW:
                queue.popleft()  # 30 ターンのうちに買い替えなかった
            while added > 0 and queue:
                take = min(added, queue[0][1])
                retire_replaced += take
                added -= take
                queue[0][1] -= take
                if queue[0][1] <= 0:
                    queue.popleft()
            working = traders_of(island, config, t)
            has_need = any(traders.state_of(x).get(traders.IMPORT_NEED) for x in working)
            used_now = used_ships(config, f) if f is not None else 0
            freight_n = False
            if with_interest and working and not has_need and f is not None:
                freight_n = _freight_made_n(island, game.defs, config, f, t)
            k_max = None
            if working and not has_need and own < MILLI // 2:
                k_max = _k_max(island, game.defs, config)
            rate_now = f.economy.get("rate") if f is not None else None
            if p is not None and p[6] and rate_now and own > 0 and stats.get("trade.imports", 0) > 0:
                rel = rate_now / p[6] - 1
                err[0] += 1
                err[1] += rel
                err[2] += rel * rel
            if any(x.kind == trader_kind and x.built for x in island.facilities):
                ever_trader.add(i)
            if working:
                last_working[i] = t
                last_need[i] = has_need
                gone_by.pop(i, None)
                reason = hope = "."
            else:
                reason = _trader_reason(island, config, t, i in ever_trader, gone_by.get(i))
                hope = _import_hope(island, game.defs, config)
            prev[i] = (bool(working), has_need, fleet_now, used_now, freight_n, k_max, rate_now, reason, hope)
            if f is not None:
                if f.id in reopened_ids and (i, f.id) in lives:
                    lives[(i, f.id)][4] = "r"
                if f.id not in opened_at:
                    opened_at[f.id] = t
                if any(le.kind in ("shipping_opened", "shipping_opened_state") for le in island.log):
                    reopened_ids.add(f.id)
                    if (i, f.id) in lives:
                        lives[(i, f.id)][4] = "r"
                fleets = f.economy.get("fleets", [])
                loads = f.economy.get("carried", [])
                full = ships._full_use(config) * sum(fleets)
                snap[i] = {
                    "id": f.id,
                    "age": t - opened_at[f.id],
                    "reopened": f.id in reopened_ids,
                    "owner": f.owner,
                    "fleet": own / MILLI,
                    "money": ledger.money(island, facility_entity(f.id)),
                    "debt": banks.debt_of(island, facility_entity(f.id)),
                    "rate": f.economy.get("rate", 0),
                    "use": round(sum(loads) / full, 3) if full else 0.0,
                    "wanted": f.economy.get("ships_wanted", 0) / MILLI,
                    "ebitda": f.economy.get("ebitda", [])[-6:],
                    "need": has_need,
                    "seized": is_seized(f),
                    "receivership": in_receivership(f),
                }
            else:
                snap.pop(i, None)
    out = {
        "scenario": a.scenario,
        "seed": a.seed,
        "turns": a.turns,
        "marks": {str(i): "".join(m for _, m in v) for i, v in marks.items()},
        "first_turn": start,
        "no_transport": no_transport,
        "shipping_fail": dict(shipping_fail),
        "fail_by_kind": {k: dict(v) for k, v in fail_by_kind.items()},
        "alive_by_kind": dict(alive_by_kind),
        "shipping_firms": shipping_firms,
        "no_transport_by_mark": dict(no_transport_by_mark),
        "failures": failures,
        "nt": {str(i): "".join(v) for i, v in nt_flags.items()},
        "fn": {str(i): "".join(v) for i, v in fn_flags.items()},
        "lives": [[i, *v] for (i, _), v in lives.items()],
        "end_turn": world.turn,
        "kn": {str(i): {str(k): v for k, v in d.items()} for i, d in kn.items()},
        "freight_err": err,
        "ship_loans": dict(loans),
        "retire_total": retire_total / MILLI,
        "retire_replaced": retire_replaced / MILLI,
        "reviews": reviews,
        "ur": {str(i): "".join(v) for i, v in ur.items()},
        "ui": {str(i): "".join(v) for i, v in ui.items()},
        "u_since": {str(i): v for i, v in u_since.items()},
        "sh": {str(i): "".join(v) for i, v in sh.items()},
        "sy": {str(i): v for i, v in sy.items()},
        "sf": {str(i): v for i, v in sf.items()},
        "sc": {str(i): v for i, v in sc.items()},
        "od": {str(i): "".join(v) for i, v in od.items()},
        "sg": {str(i): "".join(v) for i, v in sg.items()},
        "gg": {str(i): v for i, v in gg.items()},
        "sv": {str(i): "".join(v) for i, v in sv.items()},
        "po": {str(i): v for i, v in po.items()},
        "ow": {str(i): v for i, v in ow.items()},
        "gp": {str(i): v for i, v in gp.items()},
        "pp": {str(i): v for i, v in pp.items()},
        "pl": {str(i): "".join(v) for i, v in pl.items()},
        "ac": {str(i): v for i, v in ac.items()},
        "am": {str(i): v for i, v in am.items()},
        "aq": {str(i): v for i, v in aq.items()},
        "hw": {str(i): v for i, v in hw.items()},
        "iv": {str(i): v for i, v in iv.items()},
        "world_cols": ["t", "rate", "ship_price", "run_cost", "capital_per_ship", "use", "fleet", "overdue",
                       "exports", "imports", "export_fill", "traders"],
        "world": world_rows,
        "un": {str(i): "".join(v) for i, v in un.items()},
        "ship_wc": {f"{a_}-{b_}": v for (a_, b_), v in SHIP_WC.items()},
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
