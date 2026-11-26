"""W-308 海運再参入の測り（2026-11-26。会議 #5526・#5475。読むだけ。計算は変えない）:

probe14 と同じ世界（2 ペア A↔W・K2↔WK × 種 17〜22、場面 plan、1200 ターン）を、
切り替え trade.shipping_reentry の入／切で回し、倒れた海運の会社が戻る速さと、
戻らない島に何が残るかを読む。台本は probe14（w281_probe14.py）の panel・panel_ids・
failures をそのまま残し、再参入の測りに要る記録だけを足す:

- ``transfers``: 島ごとの移管の台帳（貿易の状態の shipping_transfers。施設の番号・
  買い手・移ったターン・船団の組）。0（管財人の売り／政府の引き取り）の成約を拾う。
- ``island_series``: 海運業の有無にかかわらず、島ごとにターンごと（151〜）に記録する列:
  ``carried_local``（地元の海運の運んだ量。会社が無いターンは 0）、``freight_paid``
  （他国・借りの船に払った運賃 = stats の import.freight）、``short_load``（そのターンに
  船がなくて運べなかった荷の要る船の重さ。再参入入のときだけ貿易の状態に残る
  reentry_short_load。切では null）、``shipyard``（造船所があるか 0/1）、``port``
  （港があるか 0/1）。倒産から 100 ターンの窓（注文があったターン／地元の運んだ量が
  0 のターン、#5480）と、造船所の有無の分け（#5484）に使う。
- failures の snap に ``shipyard``（倒産直前のターンに造船所があったか）を足す。

probe14 の panel の列（probe13 の 16 列＋運賃の側の 7 列）は変えない:
- ``sales``: そのターンに海運業が受け取った運賃の総額（決算の帳簿 island.books の
  その施設の sales。自国の荷の運賃も、他国へ貸した船の運賃も、どちらもここに入る）。
- ``carried_last``: そのターンに運んだ量（トン × 区画、1000 分の 1。economy["carried"] の最後）。
- ``rate_used``: そのターンの初めの運賃（荷 1 トン × 区画 1 つあたり。ターン開始前に読む。
  運賃は島ごと（= 航路の相手によらず同じ率で、航路差は区画の倍率だけ）なので、この率が
  そのまま航路ごとの水準になる）。
- ``rate_base``: そのターンの初めの「ふだんの運賃」= 船を半分使ったとき（u = 0.5）の
  全費用の見積もり（ships._estimate。今の値段・運航費・船の傷みで数える）。
  運賃の水準 = rate_used ÷ rate_base（集計で出す）。
- ``cost_last``: そのターンの費用の合計（economy["costs"] の最後 = 人件費 + 燃料 + 資本費）。
- ``wage_last``: 人件費（close_turn と同じ数え: economy["wage"] × 働く人の数）。
- ``fuel_last``: 燃料（cost_last − 資本費 capital_last − wage_last の残り）。
- ``busy_last``: そのターンに航海に出ていた船の重さ（economy["used"] の最後）。
  集計で 1 稼働隻あたりの稼ぎを出すのに使う（#5114 の列。所有隻あたりと分ける）。

空で走った割合は、使い具合 use の裏（1 − use）として集計で出す（船は売り手の港へ空で
行く決まりで、空の区間そのものは率に織り込み済みのため、別には数えない）。

条件 4 の 2 件は probe13 と同じく、この台本でも閉じない。

使い方: python w308_reentry_probe.py --scenario plan --seed 17 --turns 1200 \
    --label A-on --out reentry-Aon-plan-s17-t1200.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque

from hakoniwa_core import ledger, measure, stock
from hakoniwa_core.economy import banks, facility_entity, in_receivership, is_seized
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import island_state, port_of, shipping_of, ships

START = 151
MILLI = 1000
FAIL_KINDS = ("facility_bankrupt", "foreclosure_demolished", "facility_demolished")

PANEL_COLS = [
    "fleet",        # 船団（1000 分の 1 隻）
    "added",        # そのターンに船団に入った船（1000 分の 1 隻）
    "added_100",    # 直近 100 ターン（そのターンを含む）に船団に入った船
    "fleet_new",    # 船齢 100 ターン未満の船
    "stock_ships",  # 買ったがまだ船団に入っていない船（在庫）
    "ship_price",   # 船の値段（島の値段、その島の通貨）
    "money",        # 現金
    "debt",         # 借金の残高
    "interest_est", # そのターンの利息の見積もり（終わりの残高 × 利率の合計）
    "capital_last", # そのターンの資本費（船の傷み + 沈んだ船の値段）
    "run_cost",     # 運航費（1 隻 1 ターン）
    "use",          # 使い具合（運んだ量 ÷ 船団を全部使った量。1000 分の 1）
    "ebitda_last",  # 営業の現金収支の直近 1 ターン
    "wanted",       # 買いたい船（1000 分の 1 隻）
    "overdue",      # 寿命を過ぎて使い続けている船（1000 分の 1 隻）
    "freight_paid", # 他国・借りの船に払った運賃（島の stats の import.freight）
    # --- probe14 で足した列（運賃の側） ---
    "sales",        # そのターンに受け取った運賃の総額（帳簿の sales）
    "carried_last", # そのターンに運んだ量（トン × 区画、1000 分の 1）
    "rate_used",    # そのターンの初めの運賃（荷 1 トン × 区画 1 つあたり）
    "rate_base",    # ふだんの運賃（u = 0.5 の全費用の見積もり）
    "cost_last",    # そのターンの費用の合計（人件費 + 燃料 + 資本費）
    "wage_last",    # 人件費（wage × 働く人の数。close_turn と同じ数え）
    "fuel_last",    # 燃料（cost_last − capital_last − wage_last の残り）
    "busy_last",    # そのターンに航海に出ていた船の重さ（1000 分の 1 トン。economy["used"] の最後）
]


ISLAND_SERIES_COLS = [
    "carried_local",  # 地元の海運がそのターンに運んだ量（トン × 区画、1000 分の 1。会社が無ければ 0）
    "freight_paid",   # 他国・借りの船に払った運賃（stats の import.freight）
    "short_load",     # 船がなくて運べなかった荷（要る船の重さ。再参入入のときだけ残る。切では None）
    "shipyard",       # 造船所があるか（0/1）
    "port",           # 港があるか（0/1）
]


def _panel_row(island, config, f, t, ship_good, added_hist, pre):
    fleet_list = f.economy.get("fleet", [])
    own = sum(q for _, q in fleet_list)
    added = sum(q for since, q in fleet_list if since == t)
    added_hist.append((t, added))
    while added_hist and t - added_hist[0][0] >= 100:
        added_hist.popleft()
    added_100 = sum(q for _, q in added_hist)
    entity = facility_entity(f.id)
    loans = banks.loans_by_borrower(island, entity)
    interest = sum(ledger.interest(banks.outstanding(island, loan), loan.rate) for loan in loans)
    fleets = f.economy.get("fleets", [])
    loads = f.economy.get("carried", [])
    full = ships._full_use(config) * sum(fleets)
    use = round(sum(loads) / full * MILLI) if full else 0
    capital = f.economy.get("capital_costs", [])
    ebitda = banks.ebitda(f)
    # --- probe14 の列 ---
    sales = 0
    for b in island.books:  # そのターンの決算の帳簿（次のターンの初めに消える）
        if b.facility == f.id:
            sales = b.items.get("sales", 0)
            break
    carried_last = loads[-1] if loads else 0
    if pre is not None:
        rate_used, rate_base = pre
    else:  # そのターンに初めて出た会社は、終わりの値で代用する
        rate_used = ships.freight_rate(island, config, f)
        rate_base = ships._estimate(island, config, f)
    costs = f.economy.get("costs", [])
    cost_last = costs[-1] if costs else 0
    capital_last = capital[-1] if capital else 0
    wage_last = f.economy.get("wage", 0) * f.workers
    fuel_last = cost_last - capital_last - wage_last
    used_list = f.economy.get("used", [])
    busy_last = used_list[-1] if used_list else 0
    return [
        own,
        added,
        added_100,
        sum(q for since, q in fleet_list if t - since < 100),
        stock.total(f.stock, ship_good),
        island.economy.prices.get(ship_good, 0),
        ledger.money(island, entity),
        banks.debt_of(island, entity),
        interest,
        capital_last,
        ships._ship_run_cost(island, config),
        use,
        ebitda[-1] if ebitda else 0,
        f.economy.get("ships_wanted", 0),
        f.economy.get("overdue", 0),
        island.stats.get("import.freight", 0),
        sales,
        carried_last,
        rate_used,
        rate_base,
        cost_last,
        wage_last,
        fuel_last,
        busy_last,
    ], added, added_100


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--turns", type=int, default=1200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--start", type=int, default=START)
    a = ap.parse_args(argv)
    game = load_game(None)
    config = game.config
    ship_good = config.get("trade.ship_good")
    shipping_kind = config.get("trade.shipping_facility")
    scenario = measure.SCENARIOS[a.scenario]
    world = generate_world(a.seed, scenario.islands, config, game.defs)
    start = a.start
    panel = defaultdict(list)  # 島 -> [列 or None]（start 以降）
    panel_ids = defaultdict(list)  # 島 -> [会社 id or 0]（panel と同じ並び）
    series = defaultdict(list)  # 島 -> [ISLAND_SERIES_COLS の列]（start 以降、panel と同じ並び）
    carried_ix = PANEL_COLS.index("carried_last")
    failures = []
    shipping_firms = 0
    opened_at = {}   # 施設 id -> 最初に見えたターン
    cum_added = {}   # 施設 id -> 一生で船団に入った船の合計
    added_hist = {}  # 施設 id -> deque[(ターン, 入った船)]
    snap = {}        # 島 -> 直前ターンの終わりの海運業の様子（倒産の記録用）
    for _ in range(a.turns):
        measure.queue_scenario_orders(world, scenario, game.defs, config)
        # probe14: ターンの初めの運賃とふだんの運賃を先に読む（運賃はそのターン中は固定）
        pre = {}
        for i, island in world.islands.items():
            if not island.has_economy:
                continue
            f0 = shipping_of(island, config)
            if f0 is not None:
                pre[i] = (ships.freight_rate(island, config, f0), ships._estimate(island, config, f0))
        game.engine.run_turn(world)
        t = world.turn
        for i, island in world.islands.items():
            if not island.has_economy:
                continue
            f = shipping_of(island, config)
            if f is not None:
                shipping_firms += 1
                if f.id not in opened_at:
                    opened_at[f.id] = t
                    cum_added[f.id] = 0
                    added_hist[f.id] = deque()
            for le in island.log:
                if le.args.get("facility") == shipping_kind and le.kind in FAIL_KINDS:
                    failures.append({"t": t, "island": i, "kind": le.kind, **snap.get(i, {})})
            if t < start:
                if f is not None:  # start 前も買い増しの履歴だけは積む（cum_added のため）
                    hist = added_hist[f.id]
                    added = sum(q for since, q in f.economy.get("fleet", []) if since == t)
                    cum_added[f.id] += added
                    hist.append((t, added))
                    while hist and t - hist[0][0] >= 100:
                        hist.popleft()
                continue
            has_yard = any(fac.kind == "shipyard" for fac in island.facilities)
            if f is None:
                panel[i].append(None)
                panel_ids[i].append(0)
                snap.pop(i, None)
                series[i].append([
                    0,
                    island.stats.get("import.freight", 0),
                    island_state(island).get("reentry_short_load"),
                    1 if has_yard else 0,
                    1 if port_of(island, config) is not None else 0,
                ])
                continue
            row, added, added_100 = _panel_row(island, config, f, t, ship_good, added_hist[f.id], pre.get(i))
            cum_added[f.id] += added
            panel[i].append(row)
            panel_ids[i].append(f.id)
            series[i].append([
                row[carried_ix],
                island.stats.get("import.freight", 0),
                island_state(island).get("reentry_short_load"),
                1 if has_yard else 0,
                1 if port_of(island, config) is not None else 0,
            ])
            snap[i] = {
                "id": f.id,
                "age": t - opened_at[f.id],
                "owner": f.owner,
                "fleet": row[0] / MILLI,
                "money": row[6],
                "debt": row[7],
                "interest_est": row[8],
                "capital_last": row[9],
                "use": row[11] / MILLI,
                "wanted": row[13] / MILLI,
                "ebitda": f.economy.get("ebitda", [])[-6:],
                "cum_added": cum_added[f.id] / MILLI,
                "added_100": added_100 / MILLI,
                "seized": is_seized(f),
                "receivership": in_receivership(f),
                "shipyard": has_yard,
            }
    out = {
        "scenario": a.scenario,
        "seed": a.seed,
        "turns": a.turns,
        "first_turn": start,
        "label": a.label,
        "reentry_on": bool(config.get("trade.shipping_reentry")),
        "expand_on": bool(config.get("trade.trader_expand")),
        "panel_cols": PANEL_COLS,
        "panel": {str(i): v for i, v in panel.items()},
        "panel_ids": {str(i): v for i, v in panel_ids.items()},
        "island_series_cols": ISLAND_SERIES_COLS,
        "island_series": {str(i): v for i, v in series.items()},
        "transfers": {
            str(i): island_state(island).get("shipping_transfers", [])
            for i, island in world.islands.items()
            if island.has_economy
        },
        "failures": failures,
        "shipping_firms": shipping_firms,
        "ship_capacity": config.get("trade.ship_capacity"),
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
