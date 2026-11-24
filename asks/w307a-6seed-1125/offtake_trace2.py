"""W-307 の追跡 2（読むだけ。島ごとに輸出の見込みの材料 why を足した: 圏で払える一番高い値段 lim、島の値段 home、
輸出が引き合うか exable、島の売り手の売りたい量 hsup、契約の受け取り dlv、圏の買えなかった量 bu）。

W-307 の追跡（読むだけ）: 市場経済の島の石炭の商社 1 つずつの毎ターンの動きを、引き取り契約の
切と入で並べる。商社の買いの合計が入りで 2〜4 割減る訳を見る（w307-offtake-result.md の 11-24 の節）。

走り（生の記録を jsonl.gz に残す。後の足しは --report だけかけ直す）:
    py -3.12 -X utf8 offtake_trace2.py --seed 1 --turns 500 --out w307t-off-s1.jsonl.gz
    py -3.12 -X utf8 offtake_trace2.py --seed 1 --turns 500 --on --out w307t-on-s1.jsonl.gz
集計:
    py -3.12 -X utf8 offtake_trace2.py --report w307t-off-s1.jsonl.gz w307t-on-s1.jsonl.gz --from 301

1 行 = 1 ターン {"t": ターン, "i": {島: {"mk": {...}, "tr": {商社: {...}}, "c": [...]}}}
記録は全ターン・全商社分を書く（石炭の商社がそのターンに動かなくても、0 の行を残す。売り 0 が
何ターン続いたかを、記録なしと区別して数えられるようにするため）。
商社ごと（石炭を扱っている商社だけ）:
  pre_x/pre_xo/pre_xs: 貿易の段の見直しの前の狙う量・頭打ち前の狙う量・圏で売れた量（前のターン）
  x/xo/xs/dir: 見直しの後
  held0: 見直しの前の荷（輸出しきれずに残った量）, left: 見直しの前の手元の在庫
  exp: このターンに圏で売れた量, hsold: 島の市場で売った量（輸入の売りと、輸出の荷の島への戻し）
  want: 市場の段で出した輸出向けの買いの量（x − 契約の受け取りを含む荷）, back: 島に戻す売りの量
  spot: 島の市場で買えた量, ct: 契約で受け取った量, age: 売れ残りのターン数, probe: 多めに狙ったか
  ev: このターンの販売イベント（圏で売れた 1 件ごと。量・値段・指値の有無はこの行の中だけで揃い、
    前後のターンの値では補わない）:
    q: 売れた量, px: 1 単位あたりの値段（決済通貨。取れないときは null）, cur: 決済通貨の番号
    lim: その売りに付いていた指値（決済通貨）, lm: 指値の有無（1 = 指値あり、0 = 指値なし、
      null = 台本が拾えなかった。そのときは lmiss: "script" を同じ行に残す）
    miss: 値段が欠けたわけ（"data" = ゲーム側の記録に額が残っていない、"script" = exp の合計と
      イベントの量が合わず、台本が拾えなかった分を足した行）
島ごと mk: 石炭の市場の supply・demand・volume・price、作り手の在庫・見込み・目標の合計、作り手の数
c: 契約 [商社, 作り手, qty, 予告の量 or -1, short]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict

GOOD = "coal"


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import facilities as fac
    from hakoniwa_core import measure, stock
    from hakoniwa_core.economy import facility_entity
    from hakoniwa_core.game import load_game
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.trade import is_trader, offtake, traders

    game = load_game(None)
    defs, config = game.defs, game.config
    config.raw["trade"]["offtake"]["enabled"] = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)

    rec: dict = defaultdict(lambda: defaultdict(dict))  # 島 → 商社 → 値
    events: dict = defaultdict(list)  # (島, 商社) → このターンの販売イベント
    ask_limits: dict = {}  # (島, 商社, 財) → そのターンに圏へ出した売りの指値（None は指値なし）

    def coal_traders(island):
        return [
            f for f in island.facilities if is_trader(config, f) and GOOD in traders.goods_of(f)
        ]

    # 施設のオブジェクト → 島の番号（輸出の精算は施設しか渡さないので、島を引くために要る）
    home_island: dict = {}

    def island_of(f) -> int | None:
        if id(f) not in home_island:
            for isl in world.islands.values():
                for x in isl.facilities:
                    home_island[id(x)] = isl.id
        return home_island.get(id(f))

    orig_exported = traders.exported

    def exported(f, good, *args, **kwargs):
        orig_exported(f, good, *args, **kwargs)
        qty = args[0] if args else kwargs.get("qty", 0)
        currency = args[1] if len(args) > 1 else kwargs.get("currency")
        amount = args[2] if len(args) > 2 else kwargs.get("amount", 0)
        if good != GOOD or not qty or qty <= 0:
            return
        isl = island_of(f)
        if isl is None:
            return
        ev: dict = {"q": qty, "cur": currency}
        if currency is not None and amount > 0:
            ev["px"] = amount * 1000 // qty  # units.unit_price と同じ（決済通貨、1 単位あたり）
        else:
            # ゲーム側の記録に額が残っていない（データ側の欠け）
            ev["px"] = None
            ev["miss"] = "data"
        key = (isl, f.id, good)
        if key in ask_limits:
            ev["lim"] = ask_limits[key]
            ev["lm"] = 1 if ask_limits[key] is not None else 0
        else:
            ev["lim"] = None
            ev["lm"] = None
            ev["lmiss"] = "script"
        events[(isl, f.id)].append(ev)

    traders.exported = exported

    # 圏へ出した売りの指値そのものを拾う（売りの注文は _bloc_orders の中だけで作られる）
    try:
        from hakoniwa_core.modules import trade as trade_mod

        orig_bloc_orders = trade_mod.Trade._bloc_orders

        def bloc_orders(self, *args, **kwargs):
            out = orig_bloc_orders(self, *args, **kwargs)
            good = args[2] if len(args) > 2 else kwargs.get("good")
            if good == GOOD:
                try:
                    sells, asks = out[1], out[3]
                    for order, ask in zip(sells, asks):
                        ask_limits[(ask.island, ask.facility.id, ask.good)] = order.limit
                except Exception:
                    pass  # 拾えなかった分は ev 側で lmiss として残る
            return out

        trade_mod.Trade._bloc_orders = bloc_orders
    except Exception:
        pass  # 指値が拾えない形なら、ev の lm は null（lmiss: script）になる

    orig_orders = traders.home_orders

    def home_orders(island, d, c, t):
        out = orig_orders(island, d, c, t)
        for o in out:
            if o.good != GOOD:
                continue
            r = rec[island.id][o.facility.id]
            k = "want" if o.side == "buy" else "back"
            r[k] = r.get(k, 0) + o.qty
        return out

    traders.home_orders = home_orders

    orig_deliver = offtake.deliver

    def deliver(island, d, c, t):
        orig_deliver(island, d, c, t)
        rows = {r["id"]: r for r in offtake.contracts(island)}
        for cid, qty, _ in island.scratch.get(offtake.HELD, []):
            row = rows.get(cid)
            if row is None or row["good"] != GOOD:
                continue
            r = rec[island.id][row["trader"]]
            r["ct"] = r.get("ct", 0) + qty

    offtake.deliver = deliver

    orig_update = traders.update_targets

    def update_targets(island, d, c, bu, bs, turn=None):
        before = {}
        # 輸出の向きと狙う量を決める材料（x が 0 になる訳を分ける。会議 #4313・#4322）
        p = traders.prospects(island, d, c, GOOD).get(GOOD)
        home = traders.home_price(island, c, GOOD)
        record = next((m for m in island.markets if m.good == GOOD), None)
        supply, left_over = traders.home_supply(island, record)
        delivered = island.scratch.get(traders.OFFTAKE_DELIVERED, {}).get(GOOD, 0)
        rec[island.id]["_why"] = {
            "lim": p.export_limit if p else 0,
            "bx": p.export_bloc if p else None,
            "imp": p.import_price if p else 0,
            "ilim": p.import_limit if p else 0,
            "home": home,
            "exable": int(traders.exportable(p, home)),
            "imable": int(traders.importable(p)),
            "hsup": supply,
            "dlv": delivered,
            "dem": record.demand if record else 0,
            "bu": sum(v for (b, g), v in bu.items() if g == GOOD and p and b == p.export_bloc),
        }
        for f in coal_traders(island):
            item = traders.goods_of(f)[GOOD]
            st = traders.state_of(f).get("turn", {})
            left = stock.total(f.stock, GOOD)
            before[f.id] = {
                "pre_x": item.get("x", 0),
                "pre_xo": item.get(traders.UNCAPPED_X, 0),
                "pre_xs": item.get(traders.EXPORTED, 0),
                "held0": item.get("held", [0, 0])[0],
                "left": left,
                "exp": st.get("exported", {}).get(GOOD, 0),
                "hsold": st.get("sold", {}).get(GOOD, 0),
                "age": item.get("age", 0),
                "probe": int(
                    traders._probing(item.get("x", 0), st.get("exported", {}).get(GOOD, 0), left)
                ),
            }
        orig_update(island, d, c, bu, bs, turn)
        for fid, b in before.items():
            f = next(x for x in island.facilities if x.id == fid)
            item = traders.goods_of(f).get(GOOD, {})
            r = rec[island.id][fid]
            r.update(b)
            r.update(
                {
                    "x": item.get("x", 0),
                    "xo": item.get(traders.UNCAPPED_X, 0),
                    "xs": item.get(traders.EXPORTED, 0),
                    "dir": item.get("dir") or "-",
                }
            )

    traders.update_targets = update_targets

    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            rec.clear()
            events.clear()
            ask_limits.clear()
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            row: dict = {"t": world.turn, "i": {}}
            for i, island in sorted(world.islands.items()):
                if not island.has_economy or traders.is_planned(island):
                    continue
                makers = [
                    f for f in island.facilities if f.built and GOOD in fac.outputs_of(defs, f)
                ]
                trs = coal_traders(island)
                if not makers and not trs:
                    continue
                # 島の市場で商社が買えた量: 板寄せの約定のうち、商社が買い手の分（契約の精算の記録を引く）
                names = {facility_entity(f.id): f.id for f in island.facilities}
                spot: dict = defaultdict(int)
                sell: dict = defaultdict(int)
                for t in island.trades:
                    if t.good != GOOD:
                        continue
                    if t.buyer in names and names[t.buyer] in rec[i]:
                        spot[names[t.buyer]] += t.quantity
                    if t.seller in names and names[t.seller] in rec[i]:
                        sell[names[t.seller]] += t.quantity
                why = rec[i].pop("_why", None)
                tr = {}
                # 全ターン・全商社の行にする（動かなかった商社も 0 で残す）
                fids = {f.id for f in trs} | set(rec[i])
                for fid in sorted(fids):
                    r = rec[i].get(fid)
                    if r is None:
                        r = {
                            "pre_x": 0, "pre_xo": 0, "pre_xs": 0, "held0": 0, "left": 0,
                            "exp": 0, "hsold": 0, "age": 0, "probe": 0,
                            "x": 0, "xo": 0, "xs": 0, "dir": "-",
                        }
                    r["spot"] = spot.get(fid, 0) - r.get("ct", 0)
                    r["tsell"] = sell.get(fid, 0)
                    evs = list(events.get((i, fid), []))
                    gap = r.get("exp", 0) - sum(e["q"] for e in evs)
                    if gap > 0:
                        # exp の合計とイベントの量が合わない分（台本が拾えなかった売り）
                        evs.append(
                            {"q": gap, "px": None, "cur": None, "lim": None,
                             "lm": None, "miss": "script"}
                        )
                    r["ev"] = evs
                    tr[str(fid)] = r
                m = next((x for x in island.markets if x.good == GOOD), None)
                row["i"][str(i)] = {
                    "mk": {
                        "n": len(makers),
                        "sup": m.supply if m else 0,
                        "dem": m.demand if m else 0,
                        "vol": m.volume if m else 0,
                        "px": m.price if m else 0,
                        "mst": sum(stock.total(f.stock, GOOD) for f in makers),
                        "exp": sum(f.economy.get("expected", {}).get(GOOD, 0) for f in makers),
                        "tgt": sum(f.economy.get("target", 0) for f in makers),
                        "out": sum(fac.outputs_of(defs, f).get(GOOD, 0) for f in makers),
                        "made": island.stats.get(f"production.{GOOD}", 0),
                    },
                    "tr": tr,
                    "why": why,
                    "c": [
                        [
                            c["trader"],
                            c["maker"],
                            c["qty"],
                            c["change"][1] if c.get("change") else -1,
                            c.get("short", 0),
                        ]
                        for c in offtake.contracts(island)
                        if c["good"] == GOOD
                    ],
                }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


KEYS = ("want", "spot", "ct", "exp", "back", "hsold", "held0", "left", "x", "xo", "xs", "probe")


def report(paths: list[str], start: int, end: int) -> int:
    for path in paths:
        tot: dict = defaultdict(int)
        isl: dict = defaultdict(lambda: defaultdict(int))
        turns = 0
        dirs: dict = defaultdict(int)
        contracts = 0
        cuts = 0
        with _open(path, "rt") as f:
            for line in f:
                row = json.loads(line)
                if not start <= row["t"] <= end:
                    continue
                turns += 1
                for i, r in row["i"].items():
                    for k in ("vol", "sup", "dem", "mst", "made"):
                        isl[i][k] += r["mk"].get(k, 0)
                    contracts += len(r["c"])
                    cuts += sum(1 for c in r["c"] if c[3] >= 0)
                    for tr in r["tr"].values():
                        dirs[tr.get("dir", "-")] += 1
                        for k in KEYS:
                            tot[k] += tr.get(k, 0)
                            isl[i][k] += tr.get(k, 0)
                        isl[i]["buy"] += tr.get("spot", 0) + tr.get("ct", 0)
                        isl[i]["stuck"] += int(tr.get("left", 0) > 0 and tr.get("dir") == "x")
        n = max(1, turns)
        print(f"== {path}（{start}〜{end} ターン、{turns} ターン）")
        buy = tot["spot"] + tot["ct"]
        print(
            f"  商社の買い {buy:,}（市場 {tot['spot']:,} ＋ 契約 {tot['ct']:,}） / 出した買い {tot['want']:,}"
            f" / 圏で売れた {tot['exp']:,} / 島に戻した売り {tot['back']:,}（島で売れた {tot['hsold']:,}）"
        )
        print(
            f"  1 ターン平均: 狙う量 x {tot['x'] // n:,} / 頭打ち前 xo {tot['xo'] // n:,} / "
            f"圏で売れた xs {tot['xs'] // n:,} / 荷 {tot['held0'] // n:,} / 手元 {tot['left'] // n:,}"
            f" / 多めに狙った回 {tot['probe']} / 契約 {contracts / n:.1f} 本（縮める予告つき {cuts / n:.1f}）"
        )
        print(f"  向き（商社×ターン）: {dict(dirs)}")
        for i, v in sorted(isl.items(), key=lambda kv: -kv[1]["buy"])[:8]:
            print(
                f"  島 {i}: 買い {v['buy']:,}（市場 {v['spot']:,} 契約 {v['ct']:,}） 売れた {v['exp']:,}"
                f" 戻し {v['back']:,} 荷の平均 {v['held0'] // n:,} 輸出の荷が残った回 {v['stuck']}"
                f" 多めに狙った回 {v['probe']} 市場の約定 {v['vol']:,} 作った {v['made']:,}"
            )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--report", nargs="+")
    ap.add_argument("--from", dest="start", type=int, default=301)
    ap.add_argument("--to", dest="end", type=int, default=10**9)
    args = ap.parse_args()
    if args.report:
        return report(args.report, args.start, args.end)
    if not args.out:
        ap.error("--out が要ります")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
