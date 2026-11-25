"""国の倉庫の穀物の先行放出（下書き）の probe（掲示板 #5061 の頼み 1、#4991・#5010 の続き）。

下書き: 放出の判定を「前のターンに飢えた家計があったか」と「値段がいつもの値段の
(1 + release) 倍以上」だけでなく、「今のターンの値段の上がり」（直近の約定値が、
直近 window ターンの値段の移動中央値の multiple 倍以上か、rise_turns ターン前から
rise 以上上がったか）でも先に出す形（hakoniwa_core/economy/reserve.py の price_lead。
game.yaml の food_reserve.price_lead、既定は切）。この台本は、その線が「飢えが出る
前に値段の上がりを抑えるか」を見る材料を、毎ターン・島ごとに残す。

    py -3.12 -X utf8 grain_release_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out g-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_release_probe.py probe --scenario plan --seed 1 --on \
        --turns 300 --out g-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_release_probe.py report g-*.jsonl.gz --out report.md

版は下書きの枝 mugi/food-reserve-price-lead の commit（ask.json の commit）。
切/入は --off/--on で config の food_reserve.price_lead.enabled をその場で切り替える
（game.yaml は書き換えない）。場面は quick と同じ 6 場面（plan / none / independent /
small / open / war）× 種 1、300 ターン。1 行 = 1 島 × 1 ターンで、grn の中に
そのターンの穀物（food_reserve.good）の記録を置く。量は市場の記録の単位
（1000 分の 1）のまま。

  price: 約定した値段（市場の記録 MarketRecord.price）
  usual: いつもの値段。ターン開始時点の状態から、家計と同じ式（economy/prices.py の
    usual_price、usual_price_turns と reprice_turns）で計算したもの
  base: 先行放出の基準。ターン開始時点の price_history の直近 window ターンの
    移動中央値（reserve.price_lead が市場の段で使うのと同じ並び・同じ窓）
  last: 直近の約定値 = ターン開始時点の economy.prices（price_lead の判定に使う price）
  release_qty / release_reason / release_ask: このターンの備蓄の放出。市場の段の
    _reserve_orders が island.scratch[RESERVE_RELEASE] に残す量・わけ・指値。
    わけは hunger（飢えの線）/ price（(1 + release) の線）/ lead_multiple（倍率の線）/
    lead_rise（上がりの速さの線）。放出しなかったターンは qty 0・reason null
  rotate_due: 入れ替えで売る量。ターン開始時点の reserve.rotating の値（傷む前に
    売る分。放出とは別に毎ターン出ることがある）
  warehouse: ターン後の国の倉庫の穀物の量
  demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量
  fed_min: 家計の層の FED（前のターンに食べられた割合、1000 分の 1）の最小。
    ターン後に読むので、そのターンに食べた結果
  hungry: ターン後の状態で _anyone_hungry と同じ判定（次のターンの放出の線に
    届くか）。層の FED が household.hungry.threshold 未満か、still_hungry
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

集計の軸（report）:
  切/入を場面ごとに並べ、先行放出（lead_multiple / lead_rise）の回数と量、放出の
  合計、飢えの線に届いた島×ターン数（hungry）、FED の最小、穀物の値段の中央値と
  最大、倉庫の最後の量を見る。狙いは「入で、飢え（hungry）や値段の跳ねが、
  切より先に・小さく収まるか」。
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure, stock
    from hakoniwa_core.economy import reserve
    from hakoniwa_core.economy.prices import usual_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.modules.market import RESERVE_RELEASE
    from hakoniwa_core.modules.population import FED, still_hungry
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.units import to_milli

    game = load_game(None)
    defs, config = game.defs, game.config
    spec = config.get("food_reserve")
    if "price_lead" not in spec:
        print("この版には food_reserve.price_lead がありません（下書きの枝の版で回すこと）")
        return 2
    config.raw["food_reserve"]["price_lead"]["enabled"] = bool(args.on)
    lead = spec["price_lead"]
    window = int(lead.get("window", 12))
    good = spec["good"]
    settings = config.get("household")
    threshold = to_milli(settings["hungry"]["threshold"])
    hunger_settings = config.get("population.hunger")
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            # 基準と直近値はターン開始時点の状態で計算する（市場の段の判定が見る値と一致）
            starts: dict[int, dict] = {}
            for iid, island in world.islands.items():
                if not island.has_economy:
                    continue
                history = island.economy.price_history.get(good, [])
                starts[iid] = {
                    "usual": usual_price(
                        island, good, settings["usual_price_turns"], settings["reprice_turns"]
                    ),
                    "base": round(statistics.median(history[-window:])) if history else 0,
                    "last": island.economy.prices.get(good, 0),
                    "rotate_due": reserve.rotating(island, defs, config, world.turn),
                }
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                record = next((m for m in island.markets if m.good == good), None)
                if record is None:
                    continue
                note = island.scratch.get(RESERVE_RELEASE) or {}
                layers = island.economy.households.values()
                fed_min = min((s.get(FED, 1000) for s in layers), default=1000)
                hungry = any(
                    s.get(FED, 1000) < threshold or still_hungry(s.get("hunger", 0), hunger_settings)
                    for s in layers
                )
                start = starts.get(iid, {})
                grn = {
                    "price": record.price,
                    "usual": start.get("usual"),
                    "base": start.get("base"),
                    "last": start.get("last"),
                    "release_qty": note.get("qty", 0),
                    "release_reason": note.get("reason"),
                    "release_ask": note.get("price"),
                    "rotate_due": start.get("rotate_due", 0),
                    "warehouse": stock.total(island.warehouse, good),
                    "demand": record.demand,
                    "supply": record.supply,
                    "sold": record.volume,
                    "fed_min": fed_min,
                    "hungry": hungry,
                }
                row = {"t": world.turn, "i": iid, "grn": grn}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"counts": recorder.collapse_counts(), "found": recorder.collapses()},
            fh,
            ensure_ascii=False,
        )
    return 0


def _load(path: str) -> list[dict]:
    rows = []
    with _open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _kind_of(path: str) -> tuple[str, bool]:
    """ファイル名 g-<on|off>-<場面>-s<種>.jsonl.gz から (場面, 入か) を取る。"""
    name = Path(path).name
    stem = name.removesuffix(".jsonl.gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2] if len(parts) > 2 else "?"
    return scenario, on


def report(args: argparse.Namespace) -> int:
    by_kind: dict[tuple[str, bool], list[dict]] = defaultdict(list)
    for path in args.files:
        scenario, on = _kind_of(path)
        by_kind[(scenario, on)].extend(_load(path))
    lines = ["# 穀物の先行放出の probe の集計", ""]
    scenarios = sorted({s for s, _ in by_kind})
    for scenario in scenarios:
        lines.append(f"## {scenario}")
        lines.append("")
        lines.append(
            "| 側 | 放出ターン | 先行放出（回・量） | 飢えの線の放出（回） "
            "| 値段の線の放出（回） | 放出量の合計 | 飢えターン（島×ターン） "
            "| FED 最小 | 値段の中央値 | 値段の最大 | 倉庫の最後 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for on in (False, True):
            rows = by_kind.get((scenario, on), [])
            if not rows:
                continue
            rel = [r for r in rows if r["grn"]["release_qty"] > 0]
            lead = [r for r in rel if str(r["grn"]["release_reason"]).startswith("lead_")]
            hunger_rel = [r for r in rel if r["grn"]["release_reason"] == "hunger"]
            price_rel = [r for r in rel if r["grn"]["release_reason"] == "price"]
            prices = [r["grn"]["price"] for r in rows if r["grn"]["price"]]
            last_turn = max(r["t"] for r in rows)
            warehouse_last = sum(r["grn"]["warehouse"] for r in rows if r["t"] == last_turn)
            lines.append(
                f"| {'入' if on else '切'} | {len(rel)} "
                f"| {len(lead)}・{sum(r['grn']['release_qty'] for r in lead):,} "
                f"| {len(hunger_rel)} | {len(price_rel)} "
                f"| {sum(r['grn']['release_qty'] for r in rel):,} "
                f"| {sum(1 for r in rows if r['grn']['hungry'])} "
                f"| {min(r['grn']['fed_min'] for r in rows)} "
                f"| {round(statistics.median(prices)) if prices else '-'} "
                f"| {max(prices) if prices else '-'} "
                f"| {warehouse_last:,} |"
            )
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
