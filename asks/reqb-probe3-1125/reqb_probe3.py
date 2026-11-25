"""要望 B probe3（掲示板 #5226）: 長い衣料の品切れで「作る側が増えない理由」を読む台本。

案 A+B の集計（#5206）と長期品切れの調査（#5225）で、合図（見込み需要）は作る量の
見方まで届くのに、衣料の品切れが減らないことが分かった。#5225 の提案 1〜4 と
#5226 で足された輸入の確認 5 を、probe の記録に足す。版は #500 の枝
mugi/req-b-expected-demand @95a31939（案 A+B 入り）。切/入は --off/--on で
config の market.expected_demand をその場で切り替える（game.yaml は触らない）。
構成は 6 場面 × 種 1 × 切/入、300 ターン（種 1 本だけなのは、36 本でゆらぎの
幅が分かっているため。probe2 と同じ考え方）。

    py -3.12 -X utf8 reqb_probe3.py probe --scenario plan --seed 1 --off \
        --turns 300 --out p-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 reqb_probe3.py report p-off-*.jsonl.gz p-on-*.jsonl.gz \
        --out report.md

1 行 = 1 島 × 1 ターン。goods は、そのターンに市場の記録（island.markets）に
出た財だけ（probe・probe-ab と同じ）。量は市場の記録の単位（1000 分の 1）の
まま。production だけ島の統計の単位（個）。財ごとの項目は probe-ab と同じ
（price / demand / supply / sold / shortage / expected / added / shortage_exp /
first / placed / wanted_off / subst / unmet_first / view_demand / view_shortage /
plan_goal / production）。exp_price と diag は、この包みの読みには要らないので
落とした（#5014 の診断は #5206 で済んでいる）。

島の行に足した物（#5226 の 1〜5）:

  entry_block: そのターンの参入の回で建たなかったわけ（economy.finance の
    entry_block の写し。{facility, reason, args}）。参入が起きた・候補が
    無かったターンは null。計画経済では参入自体が走らないので null のまま。
  entries_list: そのターンに新しく建った施設の {kind, recipe} の並び
    （entries はその件数。probe-ab は件数だけだった）。
  chain: 衣料の鎖の施設（今の作り方の産出に cotton か clothing を含む施設。
    綿畑 = farm の cotton 系の作り方、衣料工場 = textile_mill）ごとの記録:
      fid / kind / recipe / out（産出の財）/ rate（稼働率。fac.FULL = 1000 が
      全力）/ status（止まった理由の生の文字。"input:cotton" の形）/
      group（stops の組: inputs / workers / funds / sales など）/
      workers（雇えた人数）/ wanted（雇いたかった人数）/
      stock_in（作り方の原料ごとの在庫）/ size / max_size /
      pending（建て増しの工事の数）/ owner / missed_n・missed_pos・
      invest_signal（建て増しの合図: 直近 invest_turns の売り逃しの記録が
      半分以上の月で 0 より大きい。firms._invest の入口の判定と同じ式）
  imports_clothing: 衣料を輸入で埋めていたか（#5226 の 5）:
      ordered: 商社が出した衣料の輸入の買い注文の量の合計（貿易の段の
        _import_order を包んで数える。fxsplit probe と同じやり方で、戻り値の
        注文量だけを見る。ゲームの挙動は変えない）
      why / why_market: ターン終わりに商社の衣料の item に残っていた
        届かなかったわけ（why = 注文の段のわけ、why_market = 圏の市場の段の
        わけ）。商社ごとの数を数える
      money: そのターンに決済された衣料の輸入額（books の import.clothing。
        圏の市場の約定・契約の履行・海外の商人の売りが全部ここに入る）
      held / transit: ターン終わりの商社の衣料の持ち高（運ぶ途中を含む）と、
        輸送中の量の合計
      contract_qty: そのターンに履行中の衣料の買い契約の、1 ターンの
        取り決め量の合計 / contract_delivered: そのターンに契約で届いた量
        （delivered の累計の差）/ contract_short: そのターンに契約で
        届かなかった量（short の累計の差）

拾えなかった物（README にも明記）:
  - 建て増し（_invest）が見送られた理由そのもの: ゲームは理由をどこにも
    残さない。代わりに、判定の入力になる状態（売り逃しの合図・大きさ・
    工事中かどうか）を chain に残す。
  - 輸入の「届いた量」の物理量: 注文ごとの約定量は注文の物に書き戻され
    ないため、注文からは追えない。決済された輸入額（money）と商社の
    持ち高・輸送中の量で代える。到着そのものの量は拾えない。
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CHAIN_GOODS = ("cotton", "clothing")
CEILING = 900  # 稼働率の天井付近（fac.FULL = 1000 の 9 割。firms.invest_use と同じ）


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


_IMPORT_ORDERED: dict[int, int] = {}


def _wrap_import_orders() -> None:
    """貿易の段の _import_order を包み、衣料の輸入の買い注文の量だけを数える
    （fxsplit probe と同じやり方。戻り値と引数はそのまま返す）。"""
    from hakoniwa_core.modules.trade import _Round

    orig = _Round._import_order

    def order(self, island, f, entity, item, good, bloc, left, money_left):
        got = orig(self, island, f, entity, item, good, bloc, left, money_left)
        if good == "clothing" and got is not None and got.quantity > 0:
            _IMPORT_ORDERED[island.id] = (
                _IMPORT_ORDERED.get(island.id, 0) + got.quantity
            )
        return got

    _Round._import_order = order


def _chain_record(island, defs, config):
    """衣料の鎖の施設ごとの記録（産出に cotton か clothing を含む、建っている施設）。"""
    from hakoniwa_core import facilities as fac
    from hakoniwa_core import stock, stops

    turns = config.get("firms")["invest_turns"]
    out = []
    for f in island.facilities:
        if not f.built:
            continue
        outputs = fac.outputs_of(defs, f)
        if not any(g in outputs for g in CHAIN_GOODS):
            continue
        recipe = defs.facilities[f.kind]["recipes"][f.recipe]
        missed = f.economy.get("missed", [])
        out.append(
            {
                "fid": f.id,
                "kind": f.kind,
                "recipe": f.recipe,
                "out": sorted(g for g in outputs if g in CHAIN_GOODS),
                "rate": f.operating_rate,
                "status": f.status,
                "group": stops.show(config, f.status, f.ext).group,
                "workers": f.workers,
                "wanted": f.economy.get("wanted", 0),
                "stock_in": {
                    g: stock.total(f.stock, g) for g in recipe.get("inputs", {})
                },
                "size": fac.size_of(f),
                "max_size": fac.max_size(defs, f.kind),
                "pending": fac.pending_expansions(island, f),
                "owner": f.owner,
                "missed_n": len(missed),
                "missed_pos": sum(1 for m in missed if m > 0),
                "invest_signal": len(missed) >= turns
                and sum(1 for m in missed if m > 0) * 2 >= turns,
            }
        )
    return out


def _imports_record(island, world, prev_contracts):
    """衣料の輸入の記録（#5226 の 5）。prev_contracts は島ごとの前ターンの
    衣料の買い契約の累計 {delivered, short} で、この場で更新する。"""
    from hakoniwa_core.trade import contracts as contracts_mod
    from hakoniwa_core.trade import traders

    why: Counter = Counter()
    why_market: Counter = Counter()
    held = transit = 0
    for f in island.facilities:
        if f.kind != "trading":
            continue
        item = traders.goods_of(f).get("clothing")
        if not item:
            continue
        if item.get("why"):
            why[item["why"]] += 1
        if item.get("why_market"):
            why_market[item["why_market"]] += 1
        held += item.get("held", [0, 0])[0]
        transit += item.get("transit", 0)
    qty = delivered_total = short_total = 0
    for c in contracts_mod.of_island(world, island.id):
        if c["buyer"] != island.id or c["good"] != "clothing":
            continue
        delivered_total += c.get("delivered", 0)
        short_total += c.get("short", 0)
        if contracts_mod.running(c, world.turn):
            qty += c.get("qty", 0)
    prev = prev_contracts.get(island.id, {"delivered": 0, "short": 0})
    prev_contracts[island.id] = {"delivered": delivered_total, "short": short_total}
    return {
        "ordered": _IMPORT_ORDERED.get(island.id, 0),
        "why": dict(why),
        "why_market": dict(why_market),
        "money": island.stats.get("import.clothing", 0),
        "held": held,
        "transit": transit,
        "contract_qty": qty,
        "contract_delivered": delivered_total - prev["delivered"],
        "contract_short": short_total - prev["short"],
    }


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
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
    _wrap_import_orders()
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    prev_facilities: dict[int, dict[int, str]] = {}
    prev_kinds: dict[int, dict[int, str]] = {}
    prev_contracts: dict[int, dict[str, int]] = {}
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            _IMPORT_ORDERED.clear()
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                now_fac = {f.id: f.recipe for f in island.facilities if f.built}
                now_kind = {f.id: f.kind for f in island.facilities if f.built}
                prev = prev_facilities.get(iid)
                if prev is None:
                    entries = recipe_changes = exits = 0
                    entries_list: list = []
                else:
                    entries_list = [
                        {"kind": now_kind[fid], "recipe": now_fac[fid]}
                        for fid in now_fac
                        if fid not in prev
                    ]
                    entries = len(entries_list)
                    recipe_changes = sum(
                        1
                        for fid, r in now_fac.items()
                        if fid in prev and prev[fid] != r
                    )
                    exits = sum(1 for fid in prev if fid not in now_fac)
                prev_facilities[iid] = now_fac
                prev_kinds[iid] = now_kind
                view_demand, view_shortage = firms_mod._demand_view(island, config)
                goals = island.ext.get("plan", {}).get("goals", {})
                first = island.scratch.get(market_mod.EXPECTED_FIRST, {})
                placed = island.scratch.get(market_mod.EXPECTED_PLACED, {})
                wanted_off = island.scratch.get(market_mod.EXPECTED_WANTED, {})
                goods: dict[str, dict] = {}
                for m in island.markets:
                    g = m.good
                    shortage = max(0, m.demand - m.supply)
                    shortage_exp = (
                        max(0, m.expected_demand - m.supply)
                        if m.expected_demand is not None
                        else None
                    )
                    goods[g] = {
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
                if not goods:
                    continue
                block = island.economy.finance.get("entry_block")
                if block:
                    block = json.loads(json.dumps(block, default=str))
                row = {
                    "t": world.turn,
                    "i": iid,
                    "regime": island.regime.get("economy", ""),
                    "entries": entries,
                    "entries_list": entries_list,
                    "recipe_changes": recipe_changes,
                    "exits": exits,
                    "entry_block": block,
                    "chain": _chain_record(island, defs, config),
                    "imports_clothing": _imports_record(island, world, prev_contracts),
                    "goods": goods,
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


def _load(path: str):
    """(scenario, seed, on) はファイル名 p-<on|off>-<場面>-s<種>.jsonl.gz から取る。"""
    name = Path(path).name
    stem = name.removesuffix(".gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2]
    seed = int(parts[3].lstrip("s"))
    rows: dict[tuple, dict] = {}
    island_rows: dict[int, list] = defaultdict(list)
    with _open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            island_rows[row["i"]].append(row)
            for g, rec in row["goods"].items():
                rows[(row["i"], row["t"], g)] = {**rec, "regime": row.get("regime", "")}
    for rs in island_rows.values():
        rs.sort(key=lambda r: r["t"])
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = (
        json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    )
    return (scenario, seed, on), {"rows": rows, "islands": island_rows, "gate": gate}


def _runs(island_rows: list) -> list:
    """衣料の品切れの「続き」（shortage > 0 が続く島ターンの並び）を出す。"""
    runs = []
    cur = None
    for row in island_rows:
        rec = row["goods"].get("clothing")
        shortage = rec["shortage"] if rec else 0
        if shortage > 0:
            if cur is None:
                cur = {
                    "start": row["t"],
                    "turns": [],
                    "shortage": 0,
                    "chain": [],
                    "entry_block": [],
                    "entries_list": [],
                    "imports": [],
                }
            cur["turns"].append(row["t"])
            cur["shortage"] += shortage
            cur["chain"].extend(row.get("chain", []))
            if row.get("entry_block"):
                cur["entry_block"].append(row["entry_block"])
            cur["entries_list"].extend(row.get("entries_list", []))
            cur["imports"].append(row.get("imports_clothing", {}))
        else:
            if cur is not None:
                runs.append(cur)
                cur = None
    if cur is not None:
        runs.append(cur)
    for rn in runs:
        rn["len"] = len(rn["turns"])
        rn["founding"] = rn["start"] <= 10
        mills = [c for c in rn["chain"] if "clothing" in c["out"]]
        rn["mills_rate"] = [c["rate"] for c in mills]
    return runs


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    lines: list[str] = []
    lines.append("# 要望 B probe3 の集計（切/入、6 場面 × 種 1、300 ターン）")
    lines.append("")
    lines.append(
        "これは合格/不合格の線で読む包みではなく、#5216 の分岐順で詰まりを"
        "切り分けるための集計。種は 1 本だけなので、切/入の差はゆらぎと分けて"
        "読めない。まず両側に共通する姿を見て、差は参考に留める。"
    )
    lines.append("")
    # --- 崩れ ---
    lines.append("## 崩れの数（当たった島の数）")
    by_side: dict[bool, dict[str, int]] = {False: Counter(), True: Counter()}
    totals: dict[bool, int] = {False: 0, True: 0}
    for (_sc, _sd, on), data in sorted(runs.items()):
        counts = data["gate"].get("counts", {})
        totals[on] += sum(counts.values())
        for kind, n in counts.items():
            by_side[on][kind] += n
    lines.append(f"- 合計: 切 {totals[False]} / 入 {totals[True]}")
    for kind in sorted(set(by_side[False]) | set(by_side[True])):
        lines.append(f"- {kind}: 切 {by_side[False][kind]} / 入 {by_side[True][kind]}")
    lines.append("")
    # --- 衣料の品切れの量（建国直後の分離つき） ---
    lines.append("## 衣料の品切れ（量と続き。建国直後は分けて数える。#5226 の 4）")
    lines.append(
        "| 側 | 品切れ量の合計 | 続きの数 | 長い続き（3 以上）"
        " | 建国直後から（開始 10 以下） | それ以外 |"
    )
    lines.append("|---|---|---|---|---|---|")
    all_runs: dict[bool, list] = {False: [], True: []}
    for (_sc, _sd, on), data in sorted(runs.items()):
        for rs in data["islands"].values():
            all_runs[on].extend(_runs(rs))
    for on in (False, True):
        rs = all_runs[on]
        total = sum(r["shortage"] for r in rs)
        founding = sum(r["shortage"] for r in rs if r["founding"])
        lines.append(
            f"| {'入' if on else '切'} | {total:,} | {len(rs)} "
            f"| {sum(1 for r in rs if r['len'] >= 3)} "
            f"| {founding:,}（{founding / total:.0%}） | {total - founding:,} |"
            if total
            else f"| {'入' if on else '切'} | 0 | 0 | 0 | 0 | 0 |"
        )
    lines.append("")
    # --- 読みの順序 1: 信号が作る量の見方に届いているか ---
    lines.append("## 読み 1: 合図は作る量の見方に届いているか（#5216 の先頭の分岐）")
    lines.append(
        "コード読みの結論（#5225）: 参入（_fill）と計画の生産目標は view_demand を"
        "読み、建て増し（_invest）と既存工場の操業目標は読まない（合図は売り逃しの"
        "記録と前ターンの売れた量）。ここでは記録側の裏づけだけを見る。"
    )
    for on in (False, True):
        n = pos = 0
        for (_sc, _sd, o), data in sorted(runs.items()):
            if o != on:
                continue
            for (_i, _t, g), rec in data["rows"].items():
                if g == "clothing" and rec["shortage"] > 0:
                    n += 1
                    if (rec.get("view_shortage") or 0) > 0:
                        pos += 1
        lines.append(
            f"- {'入' if on else '切'}: 衣料の品切れターン {n:,} のうち、"
            f"作る量の見方（view_shortage）が不足を示したのは {pos:,}"
            f"（{pos / n:.1%}）"
            if n
            else f"- {'入' if on else '切'}: 衣料の品切れターンなし"
        )
    lines.append("")
    # --- 読みの順序 2: 稼働率が天井か（長い続き、建国直後を分ける） ---
    lines.append("## 読み 2: 衣料工場の稼働率は天井か（長い続き = 3 ターン以上）")
    lines.append(
        "天井付近は稼働率 900 以上（FULL = 1000 の 9 割）。続きの中で衣料工場の"
        "記録が 1 つでもあれば「工場あり」。両側を合わせて数える（姿の読み）。"
    )
    lines.append(
        "| 続きの組 | 続きの数 | 工場なし | 工場あり・天井に届かず | 工場あり・天井付近あり |"
    )
    lines.append("|---|---|---|---|---|")
    long_runs = [r for on in (False, True) for r in all_runs[on] if r["len"] >= 3]
    for label, subset in (
        ("建国直後から", [r for r in long_runs if r["founding"]]),
        ("それ以外", [r for r in long_runs if not r["founding"]]),
        ("全部", long_runs),
    ):
        none_ = sum(1 for r in subset if not r["mills_rate"])
        ceil_ = sum(
            1 for r in subset if r["mills_rate"] and max(r["mills_rate"]) >= CEILING
        )
        lines.append(
            f"| {label} | {len(subset)} | {none_} | {len(subset) - none_ - ceil_} | {ceil_} |"
        )
    lines.append("")
    # --- 読みの順序 3a: 天井側（参入・建て増しの止まり） ---
    lines.append(
        "## 読み 3a: 天井付近まで作っても足りない続きでは、参入・建て増しがどう止まったか"
    )
    ceil_runs = [
        r for r in long_runs if r["mills_rate"] and max(r["mills_rate"]) >= CEILING
    ]
    reasons: Counter = Counter()
    facilities: Counter = Counter()
    for r in ceil_runs:
        for b in r["entry_block"]:
            reasons[b.get("reason", "")] += 1
            facilities[b.get("facility", "")] += 1
    lines.append(
        f"- 対象の続き {len(ceil_runs)} 件。その島ターンに残っていた参入の"
        f"止まり理由（entry_block）: {sum(reasons.values())} 件"
    )
    for reason, n in reasons.most_common():
        facs = ", ".join(f"{k} {v}" for k, v in facilities.most_common(3))
        lines.append(f"  - {reason}: {n}（施設: {facs}）")
    mills_chain = [c for r in ceil_runs for c in r["chain"] if "clothing" in c["out"]]
    sig = sum(1 for c in mills_chain if c["invest_signal"])
    pend = sum(1 for c in mills_chain if c["pending"] > 0)
    lines.append(
        f"- 衣料工場の記録のうち、建て増しの合図が立っていたもの {sig} 件、"
        f"建て増しの工事中だったもの {pend} 件"
        "（見送られた理由そのものはゲームが残さないので、ここは合図と工事の有無まで）"
    )
    chain_entries = Counter(e["kind"] for r in long_runs for e in r["entries_list"])
    lines.append(
        "- 長い続きの間に新しく建った施設の種類: "
        + (", ".join(f"{k} {v}" for k, v in chain_entries.most_common()) or "なし")
    )
    lines.append("")
    # --- 読みの順序 3b: 非天井側（鎖の止まり） ---
    lines.append("## 読み 3b: 天井に届かない続きでは、鎖のどこで止まったか")
    groups: Counter = Counter()
    cotton_stock0 = mills_n = 0
    cotton_short_turns = cotton_turns = 0
    for r in long_runs:
        if r["mills_rate"] and max(r["mills_rate"]) >= CEILING:
            continue
        for c in r["chain"]:
            groups[c["group"]] += 1
            if "clothing" in c["out"]:
                mills_n += 1
                if c["stock_in"].get("cotton", 0) <= 0:
                    cotton_stock0 += 1
    for (_sc, _sd, _on), data in sorted(runs.items()):
        for (_i, _t, g), rec in data["rows"].items():
            if g == "cotton":
                cotton_turns += 1
                if rec["shortage"] > 0:
                    cotton_short_turns += 1
    lines.append("- 鎖の施設の止まった理由の組（groups）:")
    for grp, n in groups.most_common():
        lines.append(f"  - {grp}: {n}")
    lines.append(
        f"- 衣料工場の記録 {mills_n} 件のうち、綿の在庫が 0 だったもの {cotton_stock0} 件"
    )
    lines.append(
        f"- 綿の市場の記録がある島ターンのうち、綿が品切れだった割合: "
        f"{cotton_short_turns:,} / {cotton_turns:,}"
        if cotton_turns
        else "- 綿の市場の記録なし"
    )
    lines.append("")
    # --- 読みの順序 4: 輸入で埋めていたか ---
    lines.append("## 読み 4: 輸入で埋めていたか（#5226 の 5）")
    imp = [im for r in long_runs for im in r["imports"]]
    why_all: Counter = Counter()
    why_m: Counter = Counter()
    for im in imp:
        why_all.update(im.get("why", {}))
        why_m.update(im.get("why_market", {}))
    lines.append(f"- 長い続きの島ターン {len(imp)} 件での合計:")
    lines.append(
        f"  - 商社の輸入の注文（ordered）: {sum(im.get('ordered', 0) for im in imp):,}"
    )
    lines.append(
        f"  - 決済された衣料の輸入額（money）: {sum(im.get('money', 0) for im in imp):,}"
    )
    delivered = sum(im.get("contract_delivered", 0) for im in imp)
    short = sum(im.get("contract_short", 0) for im in imp)
    lines.append(f"  - 契約で届いた量（contract_delivered）: {delivered:,}")
    lines.append(f"  - 契約で届かなかった量（contract_short）: {short:,}")
    lines.append(
        "  - 注文の段の届かなかったわけ（商社の数）: "
        + (", ".join(f"{k} {v}" for k, v in why_all.most_common()) or "なし")
    )
    lines.append(
        "  - 圏の市場の段のわけ: "
        + (", ".join(f"{k} {v}" for k, v in why_m.most_common()) or "なし")
    )
    lines.append("")
    lines.append(
        "読み方: 合図が届いている（読み 1）→ 衣料工場が天井か（読み 2）→ "
        "天井なら参入・建て増しの止まり（読み 3a）、天井でなければ綿・働き手など"
        "鎖の止まり（読み 3b）→ それでも埋まらない分を輸入が埋めていたか（読み 4）。"
        "建国直後の続きは、工場が立つ前の話として分けて読む。"
    )
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
