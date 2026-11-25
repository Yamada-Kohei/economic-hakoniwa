"""W-307 別件の追跡（読むだけ。会議 #4731・#4733）: 輸出の抜けの粘りと戻り（trade.bloc_reentry）の
切と入で、商社が輸出の向きを失う・取り戻す様子を並べる。

島ごと・財ごと（その島の商社が扱っている財だけ）に、貿易の段の見直しの時点で 1 行に並べる:
  lim: 圏で払える上限（自国通貨）, home: 島の値段, diff: 差（lim − home）, exable: 輸出が引き合うか,
  sellable: 島の売り手が売りに出している量, unmet: 圏の買えなかった量（板寄せの記録 bloc["unmet"]
  と同じもの）, made: そのターンに島で作った量, tr: 商社ごとの {dir: 向き, x: 輸出の狙う量}
T4 の 4 例（種 1 の島 37、種 2 の島 40、種 9 の島 32・25）が、切では「条件は戻っても戻る道が無い」
ままになり、入では戻るかを見る。

走り（生の記録を jsonl.gz に残す。後の足しは --report だけかけ直す）:
    py -3.12 -X utf8 bloc_reentry_trace.py --seed 1 --turns 500 --out w307r-off-s1.jsonl.gz
    py -3.12 -X utf8 bloc_reentry_trace.py --seed 1 --turns 500 --on --out w307r-on-s1.jsonl.gz
集計（向きを失った島・財と、戻ったかを出す）:
    py -3.12 -X utf8 bloc_reentry_trace.py --report w307r-off-s1.jsonl.gz w307r-on-s1.jsonl.gz

1 行 = 1 ターン {"t": ターン, "i": {島: {財: {...}}}}
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.trade import traders, traders_of

    game = load_game(None)
    defs, config = game.defs, game.config
    config.raw["trade"]["bloc_reentry"] = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)

    rec: dict = defaultdict(dict)  # 島 → 財 → そのターンの行

    orig_update = traders.update_targets

    def update_targets(island, d, c, bu, bs, turn=None):
        rows = {}
        if not traders.is_planned(island):
            tracked = set()
            for f in traders_of(island, c):
                tracked.update(traders.goods_of(f).keys())
            for good in sorted(tracked):
                p = traders.prospects(island, d, c, good).get(good)
                home = traders.home_price(island, c, good)
                record = next((m for m in island.markets if m.good == good), None)
                supply, _left = traders.home_supply(island, record)
                lim = p.export_limit if p else 0
                rows[good] = {
                    "lim": lim,
                    "home": home,
                    "diff": lim - home,
                    "exable": int(traders.exportable(p, home)),
                    "sellable": supply,
                    "unmet": (
                        bu.get((p.export_bloc, good), 0)
                        if p is not None and p.export_bloc is not None
                        else 0
                    ),
                }
        orig_update(island, d, c, bu, bs, turn)
        for good, row in rows.items():
            tr = {}
            for f in traders_of(island, c):
                item = traders.goods_of(f).get(good)
                if item is not None:
                    tr[str(f.id)] = {"dir": item.get("dir") or "-", "x": item.get("x", 0)}
            row["tr"] = tr
            rec[island.id][good] = row

    traders.update_targets = update_targets

    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            rec.clear()
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            row: dict = {"t": world.turn, "i": {}}
            for i, island in sorted(world.islands.items()):
                if not island.has_economy or traders.is_planned(island):
                    continue
                goods = {}
                for good, r in rec[i].items():
                    r = dict(r)
                    r["made"] = island.stats.get(f"production.{good}", 0)
                    goods[good] = r
                if goods:
                    row["i"][str(i)] = goods
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


def report(paths: list[str]) -> int:
    """島・財ごとに、輸出の向きがあったターン数と、失ったターン・戻ったターンを出す。"""
    for path in paths:
        # (島, 財) → [輸出向きの商社がいたか] のターンの並び
        seen: dict[tuple[str, str], list[bool]] = defaultdict(list)
        with _open(path, "rt") as f:
            for line in f:
                row = json.loads(line)
                for i, goods in row["i"].items():
                    for good, r in goods.items():
                        seen[(i, good)].append(
                            any(t["dir"] == "x" for t in r["tr"].values())
                        )
        print(f"== {path}")
        lost = 0
        for (i, good), flags in sorted(seen.items()):
            if not any(flags):
                continue
            first_loss = next(
                (t for t in range(1, len(flags)) if flags[t - 1] and not flags[t]), None
            )
            if first_loss is None:
                continue
            lost += 1
            recovered = any(flags[first_loss:])
            print(
                f"  島 {i} {good}: 向きを失ったターン {first_loss + 1}"
                f"（輸出向き {sum(flags)}/{len(flags)} ターン）"
                f"{'・その後戻った' if recovered else '・戻らなかった'}"
            )
        print(f"  向きを失った島・財: {lost} 件")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--on", action="store_true")
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
