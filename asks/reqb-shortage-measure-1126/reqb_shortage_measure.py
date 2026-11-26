"""要望 B（market.shortage_response、PR #527 で main 入り・既定は切）の測りの probe。

家計が品切れで代わりを買ったとき、本来ほしかった量を値段と作る量に伝える仕組み
（倭国さんの要望 B、掲示板 #5406 の案・#5412 の線・#5425／#5427 で最終形）。
実装は main 7c948cb5（PR #527 = 3730545d を含む）に固定し、切/入は台本の
--off/--on で config の market.shortage_response をその場で切り替える
（game.yaml は触らない）。

    py -3.12 -X utf8 reqb_shortage_measure.py probe --scenario plan --seed 1 --off \
        --turns 300 --out reqbm-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 reqb_shortage_measure.py report reqbm-*.jsonl.gz --out report.md

場面は none を除く 5 場面（plan / independent / small / open / war。none は
#5417 で場面の作りどおりと確かめられたので主の比べから外す。#5427・#5562）
× 種 1〜3、300 ターン、切/入の 30 本。pool は any。

残す物（1 行 = 1 島 × 1 ターン）:
  markets: 全財の市場の記録（price / demand / supply / sold / shortage）。
    線 (1) 衣料の長い品切れ・線 (2) 綿の品切れの割合・線 (5) ほかの原料の
    品切れはここから数える
  cpi: そのターン終わりの物価指数（economy.cpi_history の最後）
  cotton_prod: そのターンの綿の産出量（island.stats の production.cotton）
  mill_rate: 衣料を作る施設（textile_mill など）の稼働率の平均（1000 が全力。
    #5412 の見る物「衣料工場の稼働率」）
  cotton_orders: 綿の板寄せの注文単位の写し（#5404 の ①〜④ と #5397）:
    buys: [{fid, kind, qty（注文量）, bid（実際の指値）, pri, filled（約定量）,
            plost（優先度落ち = 自分より高い指値か、同じ指値で高い優先順位の
            買いが約定した量。#5404 の ③）,
            need（資金で削る前の要る量。#5404 の ④ の元）,
            ilimit（input_limit = 変動費の利益が 0 になる値段。#5404 の ①）,
            fcut（need − qty = 資金で削られた量。#5404 の ④）}]
    sells: 施設ごとに {fid, stock（出した時点の綿の在庫。#5397）,
            offered（出した量）, sold（売れた量）,
            pout（priced_out = 売り指値が約定価格より高くて約定の対象に
            ならなかった量）}
    注文単位の写しは、市場の段の関数の戻りと板寄せの結果を読むだけで、
    ゲームの挙動は変えない（包むだけ）。
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

合格の線は README.md に置いた（結果を見る前に固定。#5406・#5412・#5425・#5427）。
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CHAIN = "cotton"
MAIN_GOODS = ("cotton", "clothing")
FINAL_LO, FINAL_HI = 251, 300

# 市場の段の写し取り（包んだ関数の戻りと板寄せの結果を読むだけ）
_CAP: dict = {
    "active": False,
    "island": None,
    "needs": {},  # (島, 施設) -> 資金で削る前の要る量（綿）
    "bids": {},  # (島, 施設) -> input_limit（綿）
    "stocks": {},  # (島, 施設) -> 出した時点の綿の在庫
    "clears": {},  # 島 -> 綿の板寄せの写し
}


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def _install_wraps() -> None:
    from hakoniwa_core import stock as stock_mod
    from hakoniwa_core.modules.market import Market

    def wrap_static(name, make):
        orig = Market.__dict__[name]
        if isinstance(orig, staticmethod):
            setattr(Market, name, staticmethod(make(orig.__func__)))
        elif isinstance(orig, classmethod):
            setattr(Market, name, classmethod(make(orig.__func__)))
        else:
            setattr(Market, name, make(orig))

    def make_input_needs(func):
        def inner(ctx, f, power, stockpile=True, on_hand=True):
            needs = func(ctx, f, power, stockpile=stockpile, on_hand=on_hand)
            if _CAP["active"]:
                for good, qty in needs:
                    if good == CHAIN:
                        _CAP["needs"].setdefault((_CAP["island"], f.id), qty)
            return needs

        return inner

    def make_bid(func):
        def inner(ctx, f, good, prices, rate, fixed=False, subsidy=0):
            out = func(ctx, f, good, prices, rate, fixed=fixed, subsidy=subsidy)
            if _CAP["active"] and good == CHAIN:
                _CAP["bids"].setdefault((_CAP["island"], f.id), out[1])
            return out

        return inner

    orig_facility_orders = Market.__dict__["_facility_orders"]

    def facility_orders(self, island, ctx, books_by_good, rates):
        _CAP["active"] = True
        _CAP["island"] = island.id
        try:
            result = orig_facility_orders(self, island, ctx, books_by_good, rates)
        finally:
            _CAP["active"] = False
        book = books_by_good.get(CHAIN)
        if book is not None:
            for _order, party in book.sells:
                if party.facility is not None:
                    _CAP["stocks"][(island.id, party.facility.id)] = stock_mod.total(
                        party.facility.stock, CHAIN
                    )
        return result

    orig_clear = Market.__dict__["_clear"]

    def clear(self, island, ctx, good, books_by_good, official, persons, results, stale):
        out = orig_clear(
            self, island, ctx, good, books_by_good, official, persons, results, stale
        )
        if good == CHAIN:
            result = results.get(good)
            book = books_by_good.get(good)
            if result is not None and book is not None:
                buys = []
                for idx, (order, party) in enumerate(book.buys):
                    filled = result.bought[idx] if idx < len(result.bought) else 0
                    eff = order.limit if order.limit is not None else result.high
                    buys.append(
                        {
                            "fid": party.facility.id if party.facility else None,
                            "kind": party.kind,
                            "qty": order.quantity,
                            "bid": order.limit,
                            "pri": order.priority,
                            "filled": filled,
                            "eff": eff,
                        }
                    )
                for b in buys:
                    b["plost"] = sum(
                        o["filled"]
                        for o in buys
                        if o is not b
                        and (o["eff"] > b["eff"] or (o["eff"] == b["eff"] and o["pri"] > b["pri"]))
                    )
                for b in buys:
                    del b["eff"]
                sells = []
                for idx, (order, party) in enumerate(book.sells):
                    sold = result.sold[idx] if idx < len(result.sold) else 0
                    sells.append(
                        {
                            "fid": party.facility.id if party.facility else None,
                            "kind": party.kind,
                            "qty": order.quantity,
                            "ask": order.limit,
                            "sold": sold,
                        }
                    )
                _CAP["clears"][island.id] = {
                    "price": result.price,
                    "buys": buys,
                    "sells": sells,
                }
        return out

    wrap_static("_input_needs", make_input_needs)
    wrap_static("_bid", make_bid)
    Market._facility_orders = facility_orders
    Market._clear = clear


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import facilities as fac
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.state.generate import generate_world

    game = load_game(None)
    defs, config = game.defs, game.config
    config.raw["market"]["shortage_response"] = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    _install_wraps()
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            for key in ("needs", "bids", "stocks", "clears"):
                _CAP[key] = {}
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                markets = {}
                for m in island.markets:
                    markets[m.good] = {
                        "price": m.price,
                        "demand": m.demand,
                        "supply": m.supply,
                        "sold": m.volume,
                        "shortage": max(0, m.demand - m.supply),
                    }
                mills = [
                    f.operating_rate
                    for f in island.facilities
                    if f.built and "clothing" in fac.outputs_of(defs, f)
                ]
                orders = None
                cap = _CAP["clears"].get(iid)
                if cap is not None:
                    buys = []
                    for b in cap["buys"]:
                        fid = b["fid"]
                        need = (
                            _CAP["needs"].get((iid, fid)) if fid is not None else None
                        )
                        ilimit = (
                            _CAP["bids"].get((iid, fid)) if fid is not None else None
                        )
                        buys.append(
                            {
                                **b,
                                "need": need,
                                "ilimit": ilimit,
                                "fcut": (need - b["qty"]) if need is not None else None,
                            }
                        )
                    sells_agg: dict[int, dict] = {}
                    for s in cap["sells"]:
                        if s["fid"] is None:
                            continue
                        agg = sells_agg.setdefault(
                            s["fid"],
                            {
                                "fid": s["fid"],
                                "stock": _CAP["stocks"].get((iid, s["fid"])),
                                "offered": 0,
                                "sold": 0,
                                "pout": 0,
                            },
                        )
                        agg["offered"] += s["qty"]
                        agg["sold"] += s["sold"]
                        if s["ask"] is not None and s["ask"] > cap["price"]:
                            agg["pout"] += s["qty"] - s["sold"]
                    orders = {
                        "price": cap["price"],
                        "buys": buys,
                        "sells": list(sells_agg.values()),
                    }
                row = {
                    "t": world.turn,
                    "i": iid,
                    "regime": island.regime.get("economy", ""),
                    "markets": markets,
                    "cpi": (
                        island.economy.cpi_history[-1]
                        if island.economy.cpi_history
                        else None
                    ),
                    "cotton_prod": island.stats.get("production.cotton"),
                    "mill_rate": (
                        round(sum(mills) / len(mills)) if mills else None
                    ),
                    "cotton_orders": orders,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"counts": recorder.collapse_counts(), "found": recorder.collapses()},
            fh,
            ensure_ascii=False,
        )
    return 0


def _load(path: str) -> tuple[tuple[str, bool, int], dict]:
    """(場面, 入か, 種) はファイル名 reqbm-<off|on>-<場面>-s<種>.jsonl.gz から取る。"""
    name = Path(path).name
    stem = name.removesuffix(".gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2]
    seed = int(parts[3].lstrip("s"))
    rows: dict[tuple, dict] = {}
    with _open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            rows[(row["i"], row["t"])] = row
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    return (scenario, on, seed), {"rows": rows, "gate": gate}


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _fmt(value: float | None, spec: str = ",.0f") -> str:
    return format(value, spec) if value is not None else "-"


def _long_stockouts(rows: dict, good: str) -> tuple[int, int]:
    """3 ターン以上続く品切れの (件数, 量)。島ごとに、記録のあるターンだけを並べる。"""
    by_island: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for (iid, t), row in rows.items():
        m = row["markets"].get(good)
        if m is not None:
            by_island[iid].append((t, m["shortage"]))
    count = volume = 0
    for series in by_island.values():
        series.sort()
        run_len = run_vol = 0
        prev_t = None
        for t, shortage in series:
            if shortage > 0 and (prev_t is None or t == prev_t + 1):
                run_len += 1
                run_vol += shortage
            else:
                if run_len >= 3:
                    count += 1
                    volume += run_vol
                if shortage > 0:
                    run_len, run_vol = 1, shortage
                else:
                    run_len, run_vol = 0, 0
            prev_t = t
        if run_len >= 3:
            count += 1
            volume += run_vol
    return count, volume


def _stockout_share(rows: dict, good: str) -> tuple[int, int]:
    """(品切れの島ターン, 記録のある島ターン)。"""
    total = hit = 0
    for row in rows.values():
        m = row["markets"].get(good)
        if m is not None:
            total += 1
            if m["shortage"] > 0:
                hit += 1
    return hit, total


def _final_median(rows: dict, pick) -> float | None:
    """島ごとの最後のターンの値を pick(row) で取り、島の中央値。"""
    last: dict[int, tuple[int, float]] = {}
    for (iid, t), row in rows.items():
        value = pick(row)
        if value is None:
            continue
        if iid not in last or t > last[iid][0]:
            last[iid] = (t, value)
    return _median([v for _t, v in (last[i] for i in sorted(last))]) if last else None


def _order_totals(rows: dict) -> dict:
    """綿の施設の買い注文の合計（need・fcut・need−filled・limit 到達）。"""
    need = fcut = need_filled = 0
    orders = reach = 0
    limits: list[int] = []
    bids: list[int] = []
    for row in rows.values():
        cap = row.get("cotton_orders")
        if not cap:
            continue
        for b in cap["buys"]:
            if b["kind"] != "facility" or b["fid"] is None:
                continue
            if b["need"] is not None:
                need += b["need"]
                need_filled += b["need"] - b["filled"]
            if b["fcut"] is not None:
                fcut += b["fcut"]
            if b["ilimit"] is not None:
                orders += 1
                if b["ilimit"] > 0:
                    limits.append(b["ilimit"])
                    if b["bid"] is not None:
                        bids.append(b["bid"])
                        if b["bid"] >= b["ilimit"]:
                            reach += 1
    return {
        "need": need,
        "fcut": fcut,
        "need_filled": need_filled,
        "orders": orders,
        "reach": reach,
        "limit_med": _median(limits),
        "bid_med": _median(bids),
    }


def _prod_final(rows: dict) -> float:
    total = 0.0
    for (_iid, t), row in rows.items():
        if FINAL_LO <= t <= FINAL_HI and row.get("cotton_prod") is not None:
            total += row["cotton_prod"]
    return total


def _mill_final(rows: dict) -> float | None:
    values = [
        row["mill_rate"]
        for (_iid, t), row in rows.items()
        if FINAL_LO <= t <= FINAL_HI and row.get("mill_rate") is not None
    ]
    return _median(values)


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    scenarios = sorted({k[0] for k in runs})
    seeds = sorted({k[2] for k in runs})
    lines: list[str] = []
    lines.append("# 要望 B（shortage_response）の probe の集計（5 場面 × 種 1〜3、切/入）")
    lines.append("")
    lines.append("合格の線は README.md（結果を見る前に固定。#5406・#5412・#5425・#5427）。")
    lines.append("判定は場面ごと・種の中央値。ここでの判定は集計係の暫定で、最終判定は会議。")
    lines.append("")

    def med(key_fn, scenario, on):
        values = [key_fn(runs[(scenario, on, s)]["rows"]) for s in seeds if (scenario, on, s) in runs]
        return _median(values) if values else None

    # --- 線 (1) 衣料の長い品切れ ---
    lines.append("## 線 (1) 衣料の長い品切れ（3 ターン以上）: 件数と量（種の中央値）")
    lines.append("")
    lines.append("| 場面 | 切 件数 | 入 件数 | 切 量 | 入 量 |")
    lines.append("|---|---|---|---|---|")
    line1_ok = True
    for scenario in scenarios:
        off_c = med(lambda r: _long_stockouts(r, "clothing")[0], scenario, False)
        on_c = med(lambda r: _long_stockouts(r, "clothing")[0], scenario, True)
        off_v = med(lambda r: _long_stockouts(r, "clothing")[1], scenario, False)
        on_v = med(lambda r: _long_stockouts(r, "clothing")[1], scenario, True)
        if None not in (off_c, on_c, off_v, on_v) and not (on_c < off_c and on_v < off_v):
            line1_ok = False
        lines.append(
            f"| {scenario} | {_fmt(off_c)} | {_fmt(on_c)} | {_fmt(off_v)} | {_fmt(on_v)} |"
        )
    lines.append("")
    lines.append(f"暫定: {'全場面で件数・量とも減' if line1_ok else '減っていない場面あり（上の表）'}")
    lines.append("")

    # --- 線 (2) 綿の品切れの割合 + 条件 ---
    lines.append("## 線 (2) 綿の市場の品切れの島ターンの割合と、合格の条件（種の中央値）")
    lines.append("")
    lines.append(
        "| 場面 | 切 割合 | 入 割合 | 切 綿産出（終盤） | 入 綿産出（終盤）"
        " | 切 資金削り | 入 資金削り | 切 要る量−約定 | 入 要る量−約定 | 終盤の衣料工場の稼働率 切 → 入 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    line2_ok = True
    for scenario in scenarios:
        def share(rows):
            hit, total = _stockout_share(rows, "cotton")
            return hit / total if total else None

        off_s = med(share, scenario, False)
        on_s = med(share, scenario, True)
        off_p = med(_prod_final, scenario, False)
        on_p = med(_prod_final, scenario, True)
        off_f = med(lambda r: _order_totals(r)["fcut"], scenario, False)
        on_f = med(lambda r: _order_totals(r)["fcut"], scenario, True)
        off_nf = med(lambda r: _order_totals(r)["need_filled"], scenario, False)
        on_nf = med(lambda r: _order_totals(r)["need_filled"], scenario, True)
        off_m = med(_mill_final, scenario, False)
        on_m = med(_mill_final, scenario, True)
        ok = (
            None not in (off_s, on_s, off_p, on_p, off_f, on_f)
            and on_s < off_s
            and on_p > off_p
            and on_f <= off_f
        )
        if not ok:
            line2_ok = False
        lines.append(
            f"| {scenario} | {_fmt(off_s, '.3f')} | {_fmt(on_s, '.3f')}"
            f" | {_fmt(off_p)} | {_fmt(on_p)} | {_fmt(off_f)} | {_fmt(on_f)}"
            f" | {_fmt(off_nf)} | {_fmt(on_nf)} | {_fmt(off_m)} → {_fmt(on_m)} |"
        )
    lines.append("")
    lines.append(
        "暫定: "
        + (
            "全場面で割合が減り、産出増・資金削り増なし"
            if line2_ok
            else "条件を満たさない場面あり（割合・産出・資金削りのどれか）"
        )
    )
    lines.append("")

    # --- 線 (3) 値段の許容幅 + limit の切り分け ---
    lines.append("## 線 (3) 値段: 300 ターン目の CPI と綿の約定価格の中央値（島の中央値）")
    lines.append("")
    lines.append("| 場面 | 種 | 切 CPI | 入 CPI | CPI 比 | 切 綿価格 | 入 綿価格 | 綿価格比 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    line3_notes: list[str] = []
    for scenario in scenarios:
        off_cpis = []
        for seed in seeds:
            off = runs.get((scenario, False, seed))
            on = runs.get((scenario, True, seed))
            if off is None or on is None:
                continue
            off_cpi = _final_median(off["rows"], lambda r: r["cpi"])
            on_cpi = _final_median(on["rows"], lambda r: r["cpi"])
            off_cp = _final_median(
                off["rows"], lambda r: r["markets"].get("cotton", {}).get("price")
            )
            on_cp = _final_median(
                on["rows"], lambda r: r["markets"].get("cotton", {}).get("price")
            )
            if off_cpi is not None:
                off_cpis.append(off_cpi)
            ratio_c = on_cpi / off_cpi if off_cpi and on_cpi else None
            ratio_p = on_cp / off_cp if off_cp and on_cp else None
            lines.append(
                f"| {scenario} | {seed} | {_fmt(off_cpi, '.3f')} | {_fmt(on_cpi, '.3f')}"
                f" | {_fmt(ratio_c, '.3f')} | {_fmt(off_cp)} | {_fmt(on_cp)} | {_fmt(ratio_p, '.3f')} |"
            )
        if len(off_cpis) >= 2:
            spread = max(off_cpis) / min(off_cpis) if min(off_cpis) else None
            bound = max(1.10, spread) if spread else 1.10
            line3_notes.append(
                f"- {scenario}: 切の種のばらつき（最大/最小）= {_fmt(spread, '.3f')}、"
                f"CPI の bound は広い方の {bound:.3f}"
            )
    lines.append("")
    lines.extend(line3_notes)
    lines.append("")
    lines.append("### limit の切り分け（綿の施設の買い注文、全ターンの合計。種の中央値）")
    lines.append("")
    lines.append(
        "| 場面 | 切 注文件数 | 入 注文件数 | 切 limit 到達の割合 | 入 limit 到達の割合"
        " | 切 input_limit 中央値 | 入 input_limit 中央値 | 切 指値の中央値 | 入 指値の中央値 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for scenario in scenarios:
        # _order_totals は dict を返すので、種ごとに取り直して中央値を取る
        def agg(on, key):
            values = [
                _order_totals(runs[(scenario, on, s)]["rows"])[key]
                for s in seeds
                if (scenario, on, s) in runs
            ]
            values = [v for v in values if v is not None]
            return _median(values)

        def reach_share(on):
            shares = []
            for s in seeds:
                if (scenario, on, s) not in runs:
                    continue
                t = _order_totals(runs[(scenario, on, s)]["rows"])
                if t["orders"]:
                    shares.append(t["reach"] / t["orders"])
            return _median(shares)

        lines.append(
            f"| {scenario} | {_fmt(agg(False, 'orders'))} | {_fmt(agg(True, 'orders'))}"
            f" | {_fmt(reach_share(False), '.3f')} | {_fmt(reach_share(True), '.3f')}"
            f" | {_fmt(agg(False, 'limit_med'))} | {_fmt(agg(True, 'limit_med'))}"
            f" | {_fmt(agg(False, 'bid_med'))} | {_fmt(agg(True, 'bid_med'))} |"
        )
    lines.append("")

    # --- 線 (4) 崩れ ---
    lines.append("## 線 (4) 崩れ（gate の合計）")
    totals = {False: 0, True: 0}
    for (scenario, on, _seed), data in sorted(runs.items()):
        totals[on] += sum(data["gate"].get("counts", {}).values())
    lines.append(f"- 切（{sum(1 for k in runs if not k[1])} 本）: {totals[False]}")
    lines.append(f"- 入（{sum(1 for k in runs if k[1])} 本）: {totals[True]}")
    lines.append("")
    lines.append(
        "暫定: " + ("入 ≤ 切で、種のゆらぎの内と読める" if totals[True] <= totals[False] else "入が切を超えた（内訳は gate の found を見る）")
    )
    lines.append("")

    # --- 線 (5) ほかの原料 ---
    lines.append("## 線 (5) ほかの財の品切れの島ターン（全場面・全種の合計）")
    lines.append("")
    lines.append("| 財 | 切 | 入 |")
    lines.append("|---|---|---|")
    goods = sorted(
        {g for data in runs.values() for row in data["rows"].values() for g in row["markets"]}
        - set(MAIN_GOODS)
    )
    line5_bad = []
    for good in goods:
        pair = {}
        for on in (False, True):
            total = 0
            for (scenario, run_on, _seed), data in runs.items():
                if run_on == on:
                    total += _stockout_share(data["rows"], good)[0]
            pair[on] = total
        if pair[False] or pair[True]:
            lines.append(f"| {good} | {pair[False]} | {pair[True]} |")
        if pair[True] > pair[False]:
            line5_bad.append(good)
    lines.append("")
    lines.append(
        "暫定: "
        + (
            "増えた財なし"
            if not line5_bad
            else f"増えた財あり: {', '.join(line5_bad)}（同じ道を通る原料かは内訳を見る）"
        )
    )
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--off", action="store_true")
    p.add_argument("--on", action="store_true")
    p.add_argument("--out", required=True)
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args()
    if args.cmd == "probe":
        args.on = bool(args.on)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
