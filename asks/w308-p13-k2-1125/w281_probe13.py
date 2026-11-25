"""probe13（W-308 設計 3 の条件 5 の裏付け用、2026-11-25。会議 #4828・#4834・#4835。読むだけ。計算は変えない）:

probe12 と同じ世界（同じ commit・種・パッチ）でもう一度回し、海運業（島に 1 つ）の台帳だけを
ターンごとに記録する。狙いは、建て増しの後に増えた海運業の倒産が「買いすぎ」（好況で船を
買い増し、借金と新しい船の固定費の重さで潰れる）で説明できるかを、運賃・価格側の落ち込みと
分けて読めるようにすること。赤字の合計だけでは「買いすぎ」とは読まない（#4834）。

記録する物:
- ``panel``（島 -> 151 ターン以降の各ターンの列。海運業が無いターンは null）: ``panel_cols`` の
  16 列。買い増し量（そのターンに船団に入った船 ``added`` と、直近 100 ターンの合計
  ``added_100``、船齢 100 ターン未満の船 ``fleet_new``）、借金 ``debt`` と利息の見積もり
  ``interest_est``（そのターンの終わりの借りごとに ledger.interest(残高, 利率) を足した物）、
  新しい船の固定費の重さの物差し ``capital_last``（そのターンの資本費 = 船の傷み + 沈んだ船の
  値段、economy["capital_costs"] の最後）、運航費 ``run_cost``（1 隻 1 ターン）、使い具合
  ``use``、営業の現金収支の直近 ``ebitda_last``、買いたい船 ``wanted``、寿命過ぎの船
  ``overdue``、他国・借りの船に払った運賃 ``freight_paid``（島の stats の import.freight）。
  金額はその島の通貨。4 組（A・W・K2・WK）とも同じ列・同じ単位で出す。
- ``panel_ids``（島 -> panel と同じ並びの会社 id。海運業が無いターンは 0）: panel の行を
  会社ごとに追うための物。集計で「後に倒産した会社」と「残った会社」を分けるのに使う。
- ``failures``: 海運業の倒産・取り壊しの 1 件ずつ（probe12 と同じ直前ターンの様子に、
  その会社が一生で買った船の合計 ``cum_added`` と直近 100 ターンの買い増し ``added_100`` を足した物）。
- ``shipping_firms``: 海運業がいた島 × ターン（率の分母）。

条件 4 の 2 件（K2→WK の n の増えの残り、上位 2 割の外貨切れの相手先別）は、この台本では
閉じない（#4835 の 4）。panel の ``freight_paid`` と船団の列は観測だけに使う。

使い方: python w281_probe13.py --scenario plan --seed 17 --turns 1200 --out probe13.json
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
from hakoniwa_core.trade import shipping_of, ships

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
]


def _panel_row(island, config, f, t, ship_good, added_hist):
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
        capital[-1] if capital else 0,
        ships._ship_run_cost(island, config),
        use,
        ebitda[-1] if ebitda else 0,
        f.economy.get("ships_wanted", 0),
        f.economy.get("overdue", 0),
        island.stats.get("import.freight", 0),
    ], added, added_100


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--turns", type=int, default=1200)
    ap.add_argument("--out", required=True)
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
    failures = []
    shipping_firms = 0
    opened_at = {}   # 施設 id -> 最初に見えたターン
    cum_added = {}   # 施設 id -> 一生で船団に入った船の合計
    added_hist = {}  # 施設 id -> deque[(ターン, 入った船)]
    snap = {}        # 島 -> 直前ターンの終わりの海運業の様子（倒産の記録用）
    for _ in range(a.turns):
        measure.queue_scenario_orders(world, scenario, game.defs, config)
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
            if f is None:
                panel[i].append(None)
                panel_ids[i].append(0)
                snap.pop(i, None)
                continue
            row, added, added_100 = _panel_row(island, config, f, t, ship_good, added_hist[f.id])
            cum_added[f.id] += added
            panel[i].append(row)
            panel_ids[i].append(f.id)
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
            }
    out = {
        "scenario": a.scenario,
        "seed": a.seed,
        "turns": a.turns,
        "first_turn": start,
        "panel_cols": PANEL_COLS,
        "panel": {str(i): v for i, v in panel.items()},
        "panel_ids": {str(i): v for i, v in panel_ids.items()},
        "failures": failures,
        "shipping_firms": shipping_firms,
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
