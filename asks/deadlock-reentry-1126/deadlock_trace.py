"""取引の行き詰まりの直しの追跡（会議 #5303 の測り 3）: 決着回しと同じ
w307-reentry-main の形（offtake 入り・種 1・2・9・500 ターン）を、直しの版で回す。

直しの版（枝 mugi/trade-deadlock-1126）は main 由来で trade.bloc_reentry 自体が
無い（= 決着回しの「切」と同じ土台）に、処分売りだけを足したもの。切では戻ら
なかった T4 の 3 島（種 1 島 37・種 2 島 40・種 9 島 25）が、処分で荷を売り
切って向きを取り戻すかを見る。合格線は README.md に固定してある。

島ごと・財ごと（その島の商社が扱っている財だけ）に、貿易の段の見直しの時点で
1 行に並べる:
  lim: 圏で払える上限（自国通貨）, home: 島の値段, diff: 差（lim − home）,
  exable: 輸出が引き合うか, sellable: 島の売り手が売りに出している量,
  unmet: 圏の買えなかった量, made: そのターンに島で作った量,
  tr: 商社ごとの {dir: 向き, x: 輸出の狙う量, held: 手元の荷}

走り（生の記録を jsonl.gz に残す。集計は --report だけかけ直せる）:
    py -3.12 -X utf8 deadlock_trace.py --seed 1 --turns 500 --out dlr-s1.jsonl.gz
集計（向きを失った島・財と、戻ったか・残った荷を出す）:
    py -3.12 -X utf8 deadlock_trace.py --report dlr-s1.jsonl.gz

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
    config.raw["trade"]["offtake"]["enabled"] = True  # 決着回しと同じ: T4 由来の引き気を立てるため offtake 入りで回す
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
                    tr[str(f.id)] = {
                        "dir": item.get("dir") or "-",
                        "x": item.get("x", 0),
                        "held": item["held"][0],
                    }
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
    """島・財ごとに、輸出の向きがあったターン数と、失ったターン・戻ったかを出す。"""
    for path in paths:
        # (島, 財) → [輸出向きの商社がいたか] のターンの並びと、最終ターンの残荷
        seen: dict[tuple[str, str], list[bool]] = defaultdict(list)
        last_held: dict[tuple[str, str], int] = {}
        with _open(path, "rt") as f:
            for line in f:
                row = json.loads(line)
                for i, goods in row["i"].items():
                    for good, r in goods.items():
                        seen[(i, good)].append(
                            any(t["dir"] == "x" for t in r["tr"].values())
                        )
                        last_held[(i, good)] = sum(
                            t.get("held", 0) for t in r["tr"].values()
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
                f"・最終ターンの残荷 {last_held.get((i, good), 0)}"
            )
        print(f"  向きを失った島・財: {lost} 件")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=500)
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
