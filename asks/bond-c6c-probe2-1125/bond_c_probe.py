"""国債の会議 #3981 の C3・B（bonds.rollover_pricing）の比べ（読むだけ）。

--pricing で bonds.rollover_pricing を、--crisis で bonds.rollover_crisis を入にする。毎ターン、島ごとに
（動きがなく市場も閉じていない島は --every ターンごとに）1 行に記録する:
  dt・ro・mc: 最後の不履行のターン（finance.default_turn）・市場が開くターン・今閉じているか（会議 #4605）,
  u: 埋まらない割合, gfn・cap: 1 年の総資金需要と余力（cap は満期の市中の元本を数える。会議 #4019）,
  mat: 12 ターン先までの市中の満期, bud: 銀行の入札予算（1 ターン）, cut: 削れる支出（1 ターン）,
  int・dfc: 市中への利払いと基礎的収支の赤字（年）,
  ucb: 中銀の持ち分も満期と利払いに入れた u（会議 #4018 の「中銀分を引かない」並べ）,
  umat: 満期の元本を余力に数えない u（会議で取り下げた案。比べのために残す）,
  peak: 満期の固まり（12 ターンのうちいちばん重い月の市中の満期 ÷ 月の平均）,
  cbs: 中銀の持ち分（国債の残高のうち）, cbm: 12 ターン先までの満期のうち中銀の持ち分,
  use: 銀行の上限の使い具合（持ち高の合計 ÷ 上限の合計。上限がなければ null）,
  debt・gdp・rev: 残高・年の GDP・年の収入, y0: 1 年未満の区分の利回り（%）, ind: 独立か,
  h: 使った haircut, prem: C の上乗せ（h·u ÷ (1 − h·u)）, bcap・bheld: B の上限の合計と市中銀行の持ち高,
  bs・uns・rs・ac・df・cd: 買い支え・入札の未達（売れ残り）・借り換えの売れ残り・緊縮・払えない不履行・利払いの不足の不履行
  sp: 政府の支出の内訳（このターンに使った額、項目ごと。項目の無い支出（銀行の救済・預金保護など）は
  空の項目名で入る。会議 #4909 の 1: 救済の支出が赤字の元かを見るため）,
  rxb: 組み替えでの銀行の損（組み替えのあったターンに銀行が負った切り捨ての合計。仕訳 bond_default の
  うち銀行の国債勘定への消しで数える。組み替えの切り下げが主で、同ターンに組み替えより前に起きた
  不足分の元本の切り捨てがあればそれも含む。会議 #4909 の 1）,
  cutr: 削れる支出の余り（cut から、このターンの緊縮 ac で使った分を引いた残り。1 ターン分。利払いの
  不足 cd があったターンに cd と比べると、支出を削れば利払いをまかなえたかが見える。G3c の材料）,
  nr: このターンに出した銘柄の利率（期間 → 利率 %。残高で重み付けした平均。出し直しを含む）
--report で、中銀の持ち分（満期の分）の分布ごとの島×ターンの数と、残高/GDP・u・使い具合の中央値を出す
（線引きの閾値は置かず、10% 刻みで並べる）。

    py -3.12 -X utf8 bond_c_probe.py --scenario independent --seed 1 --turns 500 --pricing --out c-on-ind-s1.jsonl.gz
    py -3.12 -X utf8 bond_c_probe.py --report c-off-ind-s1.jsonl.gz c-on-ind-s1.jsonl.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import defaultdict


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def _u(gfn: int, cap: int) -> float:
    return round(min(1.0, max(0, gfn - cap) / gfn), 4) if gfn > 0 else 0.0


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import ledger, measure
    from hakoniwa_core.economy import banks, debt
    from hakoniwa_core.game import load_game
    from hakoniwa_core.modules import bonds as bonds_mod
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.turn.context import StageContext

    game = load_game(None)
    defs, config = game.defs, game.config
    raw = config.raw["bonds"]
    haircut = config.get("bonds.haircut")
    flags = ((args.pricing, "rollover_pricing"), (args.crisis, "rollover_crisis"), (args.exchange, "default_exchange"))
    for flag, name in flags:
        if flag:
            if name not in raw:
                raise SystemExit(f"この版には bonds.{name} がありません")
            raw[name] = True
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    from hakoniwa_core.gate import Recorder

    recorder = Recorder(world, defs, config)  # 崩れ（救済・連鎖も）の島ごとの最初のターン
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            ctx = StageContext(
                defs=defs,
                config=config,
                registry=game.registry,
                formulas=game.engine.formulas,
                turn=world.turn + 1,
                seed=world.seed,
                stage="probe",
                before=None,
            )
            row: dict = {"t": world.turn, "i": {}}
            for i, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                st = island.stats
                bs = sum(e.args.get("amount", 0) for e in island.log if e.kind == "bonds_backstop")
                # 入札で札が足りず売れ残った額（未達。市中以外が埋めたかは bs・rs・ac・df で見る）
                uns = sum(e.args.get("amount", 0) for e in island.log if e.kind.startswith("bonds_unsold"))
                act = {
                    "bs": bs,
                    "uns": uns,
                    "rs": st.get("bonds.rollover_short", 0),
                    "ac": st.get("bonds.austerity", 0),
                    "df": st.get("bonds.rollover_default", 0),
                    "cd": sum(e.args.get("amount", 0) for e in island.log if e.kind == "bond_coupon_default"),
                    # 銀行の破綻（債務超過・資金繰り）の数。政府が止まる前に銀行が割れる順かを見る（会議 #4093 ③）
                    "bf": sum(1 for e in island.log if e.kind.startswith("bank_failed")),
                    # 債務の組み替え（G2）で切り下げた額と替えた額（会議 #4615）
                    "rx": sum(e.args.get("cut", 0) for e in island.log if e.kind == "bond_restructured"),
                    "rxa": sum(e.args.get("amount", 0) for e in island.log if e.kind == "bond_restructured"),
                    # 企業の倒産（連鎖の元）と、中央銀行から借りている銀行の数（救済の元。gate の bank_rescue）
                    "fb": sum(1 for e in island.log if e.kind in ("facility_bankrupt", "firm_bankrupt")),
                    "cbb": sum(1 for b in banks.active_banks(island) if banks.cb_borrowed(island, b) > 0),
                }
                # 組み替えでの銀行の損（会議 #4909）: 組み替えのあったターンに銀行が負った切り捨てを
                # 仕訳から数える（_cut は bond_default の消しとして銀行の国債勘定に残る）
                rxb = 0
                if act["rx"]:
                    for e in island.journal:
                        if e.kind != "bond_default":
                            continue
                        holder, item, _issuer = ledger.parse(e.credit)
                        if ledger.is_bank(holder) and item.startswith("bond:"):
                            rxb += e.amount
                # このターンに出した銘柄の利率（会議 #4909。期間ごとに、残高で重み付けした平均）
                issued: dict[int, list[int]] = {}
                for bond in island.bonds:
                    if bond.issued != world.turn:
                        continue
                    out_amt = debt.outstanding(island, bond)
                    if out_amt <= 0:
                        continue
                    acc = issued.setdefault(bond.term, [0, 0])
                    acc[0] += out_amt * bond.rate
                    acc[1] += out_amt
                nr = {str(term): round(tot / amt / 100, 2) for term, (tot, amt) in sorted(issued.items())}
                closed = debt.market_closed(island, config, ctx.turn)
                if world.turn % args.every and not any(act.values()) and not nr and not closed:  # 閉じている間は毎ターン
                    continue
                island.scratch.pop("bond_need", None)  # 次のターンの初めの値で数え直す
                n = bonds_mod.rollover_need(island, ctx)
                by_month = [0] * 12
                cb_mat = 0
                for bond in island.bonds:
                    k = bond.maturity - ctx.turn
                    if 0 <= k < 12:
                        hold = debt.holders(island, bond)
                        by_month[k] += sum(v for h, v in hold.items() if h != ledger.CB)
                        cb_mat += hold.get(ledger.CB, 0)
                mean = sum(by_month) / 12
                cb_int = 0
                for bond in island.bonds:
                    cb_int += debt.interest_due(island, bond, ctx.turn).get(ledger.CB, 0)
                total = debt.total_debt(island)
                cb = sum(debt.holdings_of(island, ledger.CB).values())
                held = cap = 0
                capped = False
                for b in banks.active_banks(island):
                    c = bonds_mod.holding_cap(island, ctx, b)
                    if c is None:
                        continue
                    capped = True
                    held += sum(debt.holdings_of(island, b).values())
                    cap += c
                ys = island.economy.finance.get("yields") or [0]
                u = _u(n["gfn"], n["capacity"])
                loss = haircut * u
                rec = {
                    "u": u,
                    "h": haircut,
                    "prem": round(loss / (1 - loss), 4) if loss < 1 else None,
                    "bcap": cap if capped else None,
                    "bheld": held if capped else None,
                    "gfn": n["gfn"],
                    "cap": n["capacity"],
                    "mat": n["maturing"],
                    "bud": n["budget"],
                    "cut": n["cuttable"],
                    "int": n["interest"],
                    "dfc": n["deficit"],
                    "ucb": _u(n["gfn"] + cb_mat + cb_int * 12, n["capacity"]),
                    "umat": _u(n["gfn"], n["capacity"] - n["maturing"]),
                    "peak": round(max(by_month) / mean, 2) if mean > 0 else None,
                    "cbs": round(cb / total, 3) if total else 0,
                    "cbm": round(cb_mat / (cb_mat + n["maturing"]), 3) if cb_mat + n["maturing"] else None,
                    "use": round(held / cap, 3) if capped and cap > 0 else (None if not capped else 9.99),
                    "debt": total,
                    "gdp": debt.annual_gdp(island.economy),
                    "rev": n.get("revenue", debt.annual_revenue(island.economy)),
                    "y0": round(ys[0] / 100, 2),
                    "dp": round(bonds_mod.default_premium(island, ctx) * 100, 2),
                    "ind": int(island.policy.cb_independent),
                    # 締め出し（会議 #4605・むぎの指摘）: 最後の不履行のターン、市場が開くターン、今閉じているか。
                    # G1 で締め出しが延びなくなったか、G2 で組み替えた後に開くかを直に数える
                    "dt": island.economy.finance.get("default_turn"),
                    "ro": debt.reopen_turn(island, config),
                    "mc": int(closed),
                    # 会議 #4909 の 4 つの記録（sp・rxb・cutr・nr。rxb と nr は上の段で数える）
                    "sp": dict(island.economy.spending),
                    "rxb": rxb,
                    "cutr": max(0, n["cuttable"] - act["ac"]),
                    "nr": nr,
                    **act,
                }
                row["i"][i] = rec
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as g:
        keep = ("kind", "island", "first", "turns")
        json.dump([{k: c[k] for k in keep} for c in recorder.collapses()], g, ensure_ascii=False)
    return 0


def _med(xs: list) -> str:
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.2f}" if xs else "-"


def report(paths: list[str]) -> int:
    for path in paths:
        groups: dict[int, list[dict]] = defaultdict(list)
        diff = []
        tot = defaultdict(int)
        with _open(path, "rt") as f:
            for line in f:
                for r in json.loads(line)["i"].values():
                    k = 10 if r["cbm"] is None else min(9, int(r["cbm"] * 10))
                    groups[k].append(r)
                    diff.append(r["ucb"] - r["u"])
                    for a in ("bs", "rs", "ac", "df", "cd", "bf"):
                        r.setdefault(a, 0)
                        tot[a] += r[a]
        print(f"== {path}")
        print(
            "  合計: 買い支え {bs:,} / 売れ残り {rs:,} / 緊縮 {ac:,} / 払えない不履行 {df:,} / 利払いの不足の不履行 {cd:,}".format(**tot)
        )
        print("  満期のうち中銀の持ち分 | 島×ターン | 残高/GDP | 中銀の持ち分 | u | u(満期を数えない) | 使い具合 | 満期の固まり | 利回り")
        for k in sorted(groups):
            rs = groups[k]
            label = "満期なし" if k == 10 else f"{k * 10}〜{k * 10 + 10}%"
            print(
                f"  {label} | {len(rs)} | {_med([r['debt'] / r['gdp'] if r['gdp'] else None for r in rs])} | "
                f"{_med([r['cbs'] for r in rs])} | {_med([r['u'] for r in rs])} | {_med([r['umat'] for r in rs])} | "
                f"{_med([r['use'] for r in rs])} | {_med([r['peak'] for r in rs])} | {_med([r['y0'] for r in rs])}"
            )
        bands: dict[str, list[dict]] = defaultdict(list)
        for rs in groups.values():
            for r in rs:
                u = r["u"]
                key = "0" if u == 0 else ("1.0" if u >= 1 else f"{min(0.9, int(u * 10) / 10):.1f}〜")
                bands[key].append(r)
        print("  u の帯 | 島×ターン | 上乗せ | B の上限の合計 | 使い具合 | 未達の合計 | 買い支え | 緊縮 | 不履行")
        for key in sorted(bands):
            rs = bands[key]
            print(
                f"  {key} | {len(rs)} | {_med([r['prem'] for r in rs])} | {_med([r['bcap'] for r in rs])} | "
                f"{_med([r['use'] for r in rs])} | {sum(r['uns'] for r in rs):,} | {sum(r['bs'] for r in rs):,} | "
                f"{sum(r['ac'] for r in rs):,} | {sum(r['df'] for r in rs):,}"
            )
        # 振動の確かめ（上限 0.2 を外したので。会議 #4033）: 島ごとに u の向きが 0.1 以上の幅で変わった回数
        series: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        with _open(path, "rt") as f:
            for line in f:
                row = json.loads(line)
                for i, r in row["i"].items():
                    series[i].append((row["t"], r["u"], r["y0"]))
        flips = {}
        for i, xs in series.items():
            xs.sort()
            n, last, ref = 0, 0, xs[0][1] if xs else 0.0
            for _, u, _ in xs[1:]:
                d = u - ref
                if abs(d) >= 0.1:
                    s_ = 1 if d > 0 else -1
                    if last and s_ != last:
                        n += 1
                    last, ref = s_, u
            flips[i] = n
        # 収まったか（会議 #4035）: u はターンの初めに 1 度だけ数える（ターンの中の反復はない）ので、
        # ターンをまたいだ動きで見る。最後の記録と 12 ターン前の記録の差 Δu と、最後の 12 ターンの u の幅
        settled = 0
        for i, xs in sorted(series.items()):
            tail = [u for t, u, _ in xs if t > xs[-1][0] - 12] if xs else []
            du = xs[-1][1] - tail[0] if tail else 0.0
            ok = bool(tail) and max(tail) - min(tail) < 0.05
            settled += ok
            if not ok:
                print(f"  島 {i}: 最後の Δu {du:+.3f}、最後の 12 ターンの u の幅 {max(tail) - min(tail):.3f}（収まっていない）")
        print(f"  収まった島（最後の 12 ターンの u の幅 < 0.05）: {settled} / {len(series)}")
        # どちらが先に割れたか: 島ごとに、最初に銀行が破綻したターンと、最初に政府が詰まったターン
        # （入札の未達・借り換えの売れ残り・不履行のどれか）
        # 銀行の側の最初 = 銀行の破綻（bf）・中央銀行から借りる銀行（cbb）・企業の倒産（fb）のどれか最初。
        # 古い記録で cbb・fb の列がなければ bf だけで見る（そのことを表に書く）。同じターンは「並び」
        order = {"銀行が先": 0, "政府が先": 0, "並び": 0, "銀行だけ": 0, "政府だけ": 0}
        has_more = False
        firsts: dict[str, list] = defaultdict(lambda: [None, None])
        with _open(path, "rt") as f:
            for line in f:
                row = json.loads(line)
                for i, r in row["i"].items():
                    fs = firsts[i]
                    has_more = has_more or "cbb" in r
                    if fs[0] is None and (r.get("bf") or r.get("cbb") or r.get("fb")):
                        fs[0] = row["t"]
                    if fs[1] is None and (r.get("uns") or r.get("rs") or r.get("df") or r.get("cd")):
                        fs[1] = row["t"]
        for b, g in firsts.values():
            if b is None and g is None:
                continue
            if g is None:
                order["銀行だけ"] += 1
            elif b is None:
                order["政府だけ"] += 1
            else:
                order["銀行が先" if b < g else ("政府が先" if g < b else "並び")] += 1
        what = "破綻・中銀からの借り・企業の倒産" if has_more else "破綻だけ（この記録には救済・連鎖の列がない）"
        print(f"  最初に割れた側（島ごとに決めた数。銀行の側は{what}）: " + "、".join(f"{k} {v}" for k, v in order.items()))
        for i, (b, g) in sorted(firsts.items(), key=lambda kv: int(kv[0])):
            if b is not None or g is not None:
                print(f"    島 {i}: 銀行の側 {b}、政府の側 {g}")
        worst = sorted(flips.items(), key=lambda kv: -kv[1])[:3]
        print(f"  u の向きが 0.1 以上の幅で変わった回数: 島の中央値 {_med(list(flips.values()))}、多い島 {worst}")
        if diff:
            print(
                f"  中銀分を引かない u − 引く u: 中央値 {statistics.median(diff):.3f}、最大 {max(diff):.3f}、"
                f"0.1 を超えた島×ターン {sum(1 for d in diff if d > 0.1)} / {len(diff)}"
            )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="independent")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--pricing", action="store_true")
    ap.add_argument("--crisis", action="store_true")
    ap.add_argument("--exchange", action="store_true", help="bonds.default_exchange を入（G2）")
    ap.add_argument("--every", type=int, default=12)
    ap.add_argument("--out")
    ap.add_argument("--report", nargs="+")
    args = ap.parse_args()
    if args.report:
        return report(args.report)
    if not args.out:
        ap.error("--out が要ります")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
