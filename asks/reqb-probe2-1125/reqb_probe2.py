"""要望 B（見込み需要 MK-48、#500 c0dd979c）の probe2: 値の流れの切断点を 1 件ずつ追う。

probe1（reqb-probe-1125、掲示板 #4974）では、入にしても品切れの財の expected_price と
作る量の見込みがほとんど動かなかった。コード読み（#4994）で切断点は 2 つに絞れた:
(1) prices.expected_price は見込みの系列を受け取るが、需要水準の推定で品切れターン
（売りに出た量 == 売れた量）を平均から外すので、見込みの差分がほぼ捨てられる。
(2) clothing など食料以外の家計財は、記録の側で第一希望が作られず
expected_demand ≡ demand になる。
この台本は、その 2 点をターンの流れで確かめるために次を残す。

    py -3.12 -X utf8 reqb_probe2.py probe --scenario plan --seed 1 --on --turns 300 \
        --out q-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 reqb_probe2.py report q-*.jsonl.gz --out report.md

版は c0dd979c（#500）。計測パッチ reqb2-bundle.diff（家計束ごとの記録。挙動は変えない）
を当てた版で回す。切/入は --off/--on で config をその場で切り替える。種は 1 本ずつ。

1 行 = 1 島 × 1 ターン。goods の列は probe1 と同じ（price / demand / supply / sold /
shortage / expected / added / shortage_exp / first / placed / wanted_off / subst /
unmet_first / view_demand / view_shortage / plan_goal / production / exp_price）。
量は市場の記録の単位（1000 分の 1）のまま。

■ view_demand の列の定義（#4981 の併記）
view_demand は firms._demand_view（hakoniwa_core/modules/firms/__init__.py）が返す
需要系列の最後の値で、そのターンの市場の記録の demand（入では expected_demand に
置き換わる）。元の量は「そのターンに買い手が注文した量の合計」（約定した量ではない）。
第一希望の未充足分は、食料についてのみ expected_demand 経由で含まれる。clothing など
非食料の家計財では expected_demand ≡ demand なので、買いたかったのに注文できなかった
分は含まれない。view_shortage は同じ作りの品切れ系列（入では max(0, expected − supply)
の再計算）の最後の値。

■ 3 か所の term と反事実（sites。品切れのあった財と、家計束の買いたい量があった
clothing にだけ付ける）
  plan_term: 作る量の計画（計画経済の島の _plan_record）が使う需要の値
    （入では記録の expected_demand、切では demand）。plan_term_noexp は demand の値。
  exp_price: prices.expected_price（ahead=1）をターン後に計算したもの。
  exp_price_noexp: 同じ状態で切り替えを切にして計算したもの（見込みを使わない場合）。
  exp_price_zero: 同じ状態で、見込みの系列を 0 にして計算したもの
    （expected_demand=0 の差分。入のときだけ）。
  view_avg: _demand_view の需要系列の平均（参入の _fill が実際に使う term）。
  view_avg_zero: 見込みの系列を 0 にして _demand_view を計算し直した平均（入のときだけ）。
  short_avg / short_avg_zero: 品切れ系列の平均（_fill の品切れ側の term）も同じに 2 通り。

■ 家計束（bundles_cloth / bundles_food。#4989 / #4990 の追跡）
家計束は島の家計の層。hash は (島, 層) から作る同一性の印で、切/入の同じ束を
突き合わせるためのもの。persons はそのターンの層の人数。
bundles_cloth（計測パッチが層ごとに貯めた衣料の記録。全ターン、want > 0 の束だけ）:
  want: 真ん中の値段で買いたい量（needs.clothes_wanted の qty）
  want_capped: 売りに出ていないときは衣料の予算で買える量まで細った値、
    売りに出ているときは want と同じ
  ordered: 実際に出した注文の量の合計（elastic_orders の段の合計。売りに出て
    いなければ 0）, offered: 売りに出ていたか（1/0）
  bought: その層が買えた量（household_goods）
  unmet_first = max(0, want − ordered), subst = max(0, ordered − want),
  unmet_bought = max(0, want − bought)
  ※ clothing には第一希望の仕組みが無いので、first に当たる量として want を使う
  （定義は note.md にも書いた）。
bundles_food（入のときだけ。unmet_first > 0 か subst > 0 の束 × 財 × ターンだけ）:
  first: その層の第一希望の献立の量, placed: その層が注文した食料の量,
  bought: household_goods, unmet_first = max(0, first − placed),
  subst = max(0, placed − first)

集計（report）では、probe1 と同じ財ごとの表に加えて、入の clothing の家計束から
unmet_first の合計が大きい束を最大 3 件（あれば subst > 0 の束を最大 2 件追加）選び、
ターンごとの流れ（束の want / ordered / bought と、同じターンの島の clothing の
demand / expected / exp_price 3 通り / view_avg 2 通り）を並べる。
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def _bundle_hash(iid: int, layer: str) -> str:
    return hashlib.sha256(f"{iid}:{layer}".encode()).hexdigest()[:10]


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.economy.prices import expected_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.modules import firms as firms_mod
    from hakoniwa_core.modules import market as market_mod
    from hakoniwa_core.state.generate import generate_world

    game = load_game(None)
    defs, config = game.defs, game.config
    if "expected_demand" not in config.raw.get("market", {}):
        raise SystemExit(
            "この版には market.expected_demand がありません（c0dd979c 以後で回してください）"
        )
    config.raw["market"]["expected_demand"] = bool(args.on)
    on = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    prev_facilities: dict[int, dict[int, str]] = {}

    def zeroed_view(island, good):
        """見込みの系列を 0 にして _demand_view を計算し直す（finance は元に戻す）。"""
        finance = island.economy.finance
        saved = finance.get("expected_demand")
        if saved is None:
            return None, None
        new = dict(saved)
        new[good] = [0] * len(saved.get(good, []))
        finance["expected_demand"] = new
        try:
            return firms_mod._demand_view(island, config)
        finally:
            finance["expected_demand"] = saved

    def exp_price_with(island, good, mode):
        """mode: real（そのまま）/ noexp（切り替えを切）/ zero（見込みの系列を 0）。"""
        if mode == "real":
            return expected_price(island, good, 1, defs, config, turn=world.turn)
        if mode == "noexp":
            config.raw["market"]["expected_demand"] = False
            try:
                return expected_price(island, good, 1, defs, config, turn=world.turn)
            finally:
                config.raw["market"]["expected_demand"] = on
        finance = island.economy.finance
        saved = finance.get("expected_demand")
        if saved is None:
            return None
        new = dict(saved)
        new[good] = [0] * len(saved.get(good, []))
        finance["expected_demand"] = new
        try:
            return expected_price(island, good, 1, defs, config, turn=world.turn)
        finally:
            finance["expected_demand"] = saved

    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                now_fac = {f.id: f.recipe for f in island.facilities if f.built}
                prev = prev_facilities.get(iid)
                if prev is None:
                    entries = recipe_changes = exits = 0
                else:
                    entries = sum(1 for fid in now_fac if fid not in prev)
                    recipe_changes = sum(
                        1 for fid, r in now_fac.items() if fid in prev and prev[fid] != r
                    )
                    exits = sum(1 for fid in prev if fid not in now_fac)
                prev_facilities[iid] = now_fac
                view_demand, view_shortage = firms_mod._demand_view(island, config)
                goals = island.ext.get("plan", {}).get("goals", {})
                first = island.scratch.get(market_mod.EXPECTED_FIRST, {})
                placed = island.scratch.get(market_mod.EXPECTED_PLACED, {})
                wanted_off = island.scratch.get(market_mod.EXPECTED_WANTED, {})
                cloth = island.scratch.get("reqb2_cloth", {})
                foodb = island.scratch.get("reqb2_food", {})
                bought_map = island.scratch.get("household_goods", {})
                cloth_goods = {g for per in cloth.values() for g in per}
                goods: dict[str, dict] = {}
                for m in island.markets:
                    g = m.good
                    shortage = max(0, m.demand - m.supply)
                    shortage_exp = (
                        max(0, m.expected_demand - m.supply)
                        if m.expected_demand is not None
                        else None
                    )
                    rec: dict = {
                        "price": m.price,
                        "demand": m.demand,
                        "supply": m.supply,
                        "sold": m.volume,
                        "shortage": shortage,
                        "expected": m.expected_demand,
                        "added": (m.expected_demand - m.demand)
                        if m.expected_demand is not None
                        else None,
                        "shortage_exp": shortage_exp,
                        "first": first.get(g, 0),
                        "placed": placed.get(g, 0),
                        "wanted_off": wanted_off.get(g, 0),
                        "subst": max(0, placed.get(g, 0) - first.get(g, 0)),
                        "unmet_first": max(0, first.get(g, 0) - placed.get(g, 0)),
                        "view_demand": (view_demand.get(g) or [None])[-1],
                        "view_shortage": (view_shortage.get(g) or [None])[-1],
                        "plan_goal": goals.get(g),
                        "production": island.stats.get(f"production.{g}"),
                    }
                    if shortage > 0 or (shortage_exp or 0) > 0 or g in cloth_goods:
                        sites: dict = {
                            "plan_term": m.expected_demand
                            if (on and m.expected_demand is not None)
                            else m.demand,
                            "plan_term_noexp": m.demand,
                            "exp_price": exp_price_with(island, g, "real"),
                            "view_avg": _mean(view_demand.get(g, [])),
                            "short_avg": _mean(view_shortage.get(g, [])),
                        }
                        if on:
                            sites["exp_price_noexp"] = exp_price_with(island, g, "noexp")
                            sites["exp_price_zero"] = exp_price_with(island, g, "zero")
                            vd0, vs0 = zeroed_view(island, g)
                            sites["view_avg_zero"] = _mean(vd0.get(g, [])) if vd0 else None
                            sites["short_avg_zero"] = _mean(vs0.get(g, [])) if vs0 else None
                        rec["sites"] = sites
                    goods[g] = rec
                bundles_cloth: list[dict] = []
                for layer, per_good in sorted(cloth.items()):
                    persons = island.economy.households.get(layer, {}).get("persons")
                    for g, cell in sorted(per_good.items()):
                        if cell["want"] <= 0:
                            continue
                        bought = bought_map.get(layer, {}).get(g, 0)
                        bundles_cloth.append(
                            {
                                "layer": layer,
                                "hash": _bundle_hash(iid, layer),
                                "good": g,
                                "persons": persons,
                                "want": cell["want"],
                                "want_capped": cell["want_capped"],
                                "ordered": cell["ordered"],
                                "offered": cell["offered"],
                                "bought": bought,
                                "unmet_first": max(0, cell["want"] - cell["ordered"]),
                                "subst": max(0, cell["ordered"] - cell["want"]),
                                "unmet_bought": max(0, cell["want"] - bought),
                            }
                        )
                bundles_food: list[dict] = []
                if on:
                    for layer, per_good in sorted(foodb.items()):
                        persons = island.economy.households.get(layer, {}).get("persons")
                        for g, cell in sorted(per_good.items()):
                            unmet = max(0, cell["first"] - cell["placed"])
                            subst = max(0, cell["placed"] - cell["first"])
                            if unmet <= 0 and subst <= 0:
                                continue
                            bundles_food.append(
                                {
                                    "layer": layer,
                                    "hash": _bundle_hash(iid, layer),
                                    "good": g,
                                    "persons": persons,
                                    "first": cell["first"],
                                    "placed": cell["placed"],
                                    "bought": bought_map.get(layer, {}).get(g, 0),
                                    "unmet_first": unmet,
                                    "subst": subst,
                                }
                            )
                if not goods:
                    continue
                row = {
                    "t": world.turn,
                    "i": iid,
                    "regime": island.regime.get("economy", ""),
                    "entries": entries,
                    "recipe_changes": recipe_changes,
                    "exits": exits,
                    "goods": goods,
                }
                if bundles_cloth:
                    row["bundles_cloth"] = bundles_cloth
                if bundles_food:
                    row["bundles_food"] = bundles_food
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"counts": recorder.collapse_counts(), "found": recorder.collapses()},
            fh,
            ensure_ascii=False,
        )
    return 0


def _load(path: str) -> tuple[dict, dict]:
    """(scenario, seed, on) はファイル名 q-<on|off>-<場面>-s<種>.jsonl.gz から取る。"""
    name = Path(path).name
    stem = name.removesuffix(".gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2]
    seed = int(parts[3].lstrip("s"))
    rows: dict[tuple, dict] = {}
    full: list[dict] = []
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    with _open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            full.append(row)
            for g, rec in row["goods"].items():
                rows[(row["i"], row["t"], g)] = rec
    return (scenario, seed, on), {"rows": rows, "full": full, "gate": gate}


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    lines: list[str] = []
    lines.append("# 要望 B probe2 の集計（切/入、場面 × 種 1 本、300 ターン）")
    lines.append("")
    lines.append("## view_demand の列の定義")
    lines.append(
        "view_demand は firms._demand_view が返す需要系列の最後の値で、そのターンの市場の"
        "記録の demand（入では expected_demand に置き換わる）。元の量は「そのターンに買い手が"
        "注文した量の合計」（約定した量ではない）。第一希望の未充足分は、食料についてのみ "
        "expected_demand 経由で含まれる。clothing など非食料の家計財では "
        "expected_demand ≡ demand なので、買いたかったのに注文できなかった分は含まれない。"
    )
    lines.append("")
    # --- 崩れ ---
    lines.append("## 崩れの数（当たった島の数）")
    by_side: dict[bool, dict[str, list[int]]] = {False: defaultdict(list), True: defaultdict(list)}
    totals: dict[bool, list[int]] = {False: [], True: []}
    for (_scenario, _seed, on), data in sorted(runs.items()):
        counts = data["gate"].get("counts", {})
        totals[on].append(sum(counts.values()))
        for kind, n in counts.items():
            by_side[on][kind].append(n)
    lines.append(f"- 合計: 切 {totals[False]} / 入 {totals[True]}")
    for kind in sorted(set(by_side[False]) | set(by_side[True])):
        off, on_ = by_side[False].get(kind, []), by_side[True].get(kind, [])
        lines.append(f"- {kind}: 切 合計 {sum(off)} / 入 合計 {sum(on_)}")
    lines.append("")
    # --- 財ごと（probe1 と同じ比べ方） ---
    lines.append("## 財ごと（品切れのあった島×ターンの組で比べる）")
    lines.append(
        "| 財 | 組の数 | exp_price の差（入−切）の中央値 | 上がった組の割合"
        " | view_demand の差の中央値 | plan_goal の差の中央値"
        " | 品切れ量の合計 切 → 入 | 代替購入 subst の合計（入）"
        " | 第一希望の減り unmet_first の合計（入） |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    goods_all = set()
    for data in runs.values():
        goods_all |= {g for (_, _, g) in data["rows"]}
    for g in sorted(goods_all):
        pairs = []
        shortage_off = shortage_on = 0
        subst_on = unmet_on = 0
        for (scenario, seed, on), data in runs.items():
            if on:
                continue
            other = runs.get((scenario, seed, True))
            if other is None:
                continue
            for (iid, t, good), rec in data["rows"].items():
                if good != g:
                    continue
                o = other["rows"].get((iid, t, good))
                if o is None:
                    continue
                shortage_off += rec["shortage"]
                shortage_on += o["shortage"]
                subst_on += o.get("subst", 0)
                unmet_on += o.get("unmet_first", 0)
                if rec["shortage"] > 0 or (o.get("shortage_exp") or 0) > 0:
                    pairs.append((rec, o))
        if not pairs:
            continue
        dep = [
            o["sites"]["exp_price"] - r["sites"]["exp_price"]
            for r, o in pairs
            if "exp_price" in r.get("sites", {}) and "exp_price" in o.get("sites", {})
        ]
        dvd = [
            o["view_demand"] - r["view_demand"]
            for r, o in pairs
            if r.get("view_demand") is not None and o.get("view_demand") is not None
        ]
        dpg = [
            o["plan_goal"] - r["plan_goal"]
            for r, o in pairs
            if r.get("plan_goal") is not None and o.get("plan_goal") is not None
        ]
        med = lambda xs: f"{statistics.median(xs):,.0f}" if xs else "-"  # noqa: E731
        share = f"{sum(1 for x in dep if x > 0) / len(dep):.0%}" if dep else "-"
        lines.append(
            f"| {g} | {len(pairs)} | {med(dep)} | {share} | {med(dvd)} | {med(dpg)} | "
            f"{shortage_off:,} → {shortage_on:,} | {subst_on:,} | {unmet_on:,} |"
        )
    lines.append("")
    # --- 3 か所の反事実（入の行だけ。同じ状態での比べ） ---
    lines.append("## 3 か所の反事実（入の同じ状態で、見込みを使わない / 0 にした場合）")
    lines.append(
        "exp_price の差（real − noexp、real − zero）と、view_avg の差（real − zero）を、"
        "品切れのあった組で財ごとに集める。real と noexp が同じなら、その状態では"
        "expected_price は見込みを読んでいない（または読んでも効いていない）。"
    )
    lines.append("| 財 | 組の数 | exp_price real−noexp の中央値 | real−zero の中央値 | view_avg real−zero の中央値 |")
    lines.append("|---|---|---|---|---|")
    for g in sorted(goods_all):
        ep_n, ep_z, va_z = [], [], []
        for (_s, _seed, on), data in runs.items():
            if not on:
                continue
            for (_iid, _t, good), rec in data["rows"].items():
                if good != g:
                    continue
                s = rec.get("sites")
                if not s:
                    continue
                if s.get("exp_price_noexp") is not None:
                    ep_n.append(s["exp_price"] - s["exp_price_noexp"])
                if s.get("exp_price_zero") is not None:
                    ep_z.append(s["exp_price"] - s["exp_price_zero"])
                if s.get("view_avg_zero") is not None and s.get("view_avg") is not None:
                    va_z.append(s["view_avg"] - s["view_avg_zero"])
        if not (ep_n or ep_z or va_z):
            continue
        med = lambda xs: f"{statistics.median(xs):,.0f}" if xs else "-"  # noqa: E731
        lines.append(f"| {g} | {max(len(ep_n), len(ep_z), len(va_z))} | {med(ep_n)} | {med(ep_z)} | {med(va_z)} |")
    lines.append("")
    # --- 家計束の追跡（clothing、入） ---
    lines.append("## 家計束の追跡（clothing、入。unmet_first の合計が大きい束）")
    agg: dict[tuple, dict] = {}
    for (scenario, seed, on), data in runs.items():
        if not on:
            continue
        for row in data["full"]:
            for b in row.get("bundles_cloth", []):
                key = (scenario, row["i"], b["layer"], b["good"])
                a = agg.setdefault(key, {"unmet": 0, "subst": 0, "turns": 0, "hash": b["hash"]})
                a["unmet"] += b["unmet_first"]
                a["subst"] += b["subst"]
                a["turns"] += 1
    picked = sorted(
        ((k, a) for k, a in agg.items() if a["unmet"] > 0),
        key=lambda kv: -kv[1]["unmet"],
    )[:3]
    picked_keys = {k for k, _ in picked}
    extra = sorted(
        ((k, a) for k, a in agg.items() if a["subst"] > 0 and k not in picked_keys),
        key=lambda kv: -kv[1]["subst"],
    )[:2]
    picked += extra
    if not picked:
        lines.append("unmet_first > 0 の clothing の家計束は無かった。")
    for (scenario, iid, layer, good), a in picked:
        lines.append(
            f"### 場面 {scenario} / 島 {iid} / 層 {layer}（hash {a['hash']}）/ {good}"
            f" — unmet_first 合計 {a['unmet']:,}、subst 合計 {a['subst']:,}、記録 {a['turns']} ターン"
        )
        lines.append(
            "| ターン | 人数 | want | want_capped | ordered | bought | unmet_first | subst"
            " | 島 demand | 島 expected | exp_price | exp_price_noexp | exp_price_zero"
            " | view_avg | view_avg_zero |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for (sc, seed, on), data in runs.items():
            if not on or sc != scenario:
                continue
            for row in data["full"]:
                if row["i"] != iid:
                    continue
                for b in row.get("bundles_cloth", []):
                    if b["layer"] != layer or b["good"] != good:
                        continue
                    grec = row["goods"].get(good, {})
                    s = grec.get("sites", {})
                    fmt = lambda v: f"{v:,.0f}" if isinstance(v, (int, float)) else "-"  # noqa: E731
                    lines.append(
                        f"| {row['t']} | {b['persons']} | {b['want']:,} | {b['want_capped']:,} "
                        f"| {b['ordered']:,} | {b['bought']:,} | {b['unmet_first']:,} | {b['subst']:,} "
                        f"| {fmt(grec.get('demand'))} | {fmt(grec.get('expected'))} "
                        f"| {fmt(s.get('exp_price'))} | {fmt(s.get('exp_price_noexp'))} "
                        f"| {fmt(s.get('exp_price_zero'))} | {fmt(s.get('view_avg'))} "
                        f"| {fmt(s.get('view_avg_zero'))} |"
                    )
        lines.append("")
    # --- 食料の家計束の出事の数 ---
    lines.append("## 食料の家計束の出事（入。unmet_first > 0 / subst > 0 の束×ターン数）")
    ev: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (_s, _seed, on), data in runs.items():
        if not on:
            continue
        for row in data["full"]:
            for b in row.get("bundles_food", []):
                if b["unmet_first"] > 0:
                    ev[b["good"]][0] += 1
                if b["subst"] > 0:
                    ev[b["good"]][1] += 1
    if not ev:
        lines.append("出事は無かった。")
    for g in sorted(ev):
        lines.append(f"- {g}: unmet_first {ev[g][0]} 件 / subst {ev[g][1]} 件")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.add_argument("--out", required=True)
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
