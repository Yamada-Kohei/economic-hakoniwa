"""W-272 既定入の前に、PO-63 の見張り（作れる量が食べる量に届かない村のマス）を測る（会議 #4388 の (2)）。

走らせ方: python po63_watch.py --scenario none --seed 1 --turns 300 --on 1 --out none-s1-on.jsonl.gz
  --on 1 で population.farming_by_housing と housing.upgrade_without_subsistence を入にする（0 は今の既定）。
  population.subsistence_sales は今の既定（入）のまま。
記録（毎ターン 1 行）: 島ごとの村のマス（自給の人がいるマス）の数・作れる量が食べる量に届かないマスの数・
作れる量の計 ÷ 食べる量の計・マスごとの比の最小と下 1 割。作れる量は settle_farm_capacity と同じ式
（_land_output）でターンの終わりの人数からかけ直し、マスに書かれた値（farm_capacity）も並べる。
--selftest out.txt: none 種 1 を 3 ターン回して記録が書けることを確かめる。
"""

import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from hakoniwa_core import facilities as fac  # noqa: E402
from hakoniwa_core import measure  # noqa: E402
from hakoniwa_core.game import load_game  # noqa: E402
from hakoniwa_core.modules import population as popmod  # noqa: E402
from hakoniwa_core.state.generate import generate_world  # noqa: E402
from hakoniwa_core.units import to_milli  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--scenario", default="none")
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--turns", type=int, default=300)
ap.add_argument("--on", type=int, default=1)
ap.add_argument("--out", default="po63.jsonl.gz")
ap.add_argument("--selftest", default=None)
a = ap.parse_args()
if a.selftest:
    a.scenario, a.seed, a.turns, a.out = "none", 1, 3, a.selftest + ".jsonl.gz"

game = load_game()
defs, config = game.defs, game.config
config.get("population")["farming_by_housing"] = bool(a.on)
config.get("housing")["upgrade_without_subsistence"] = bool(a.on)
scenario = measure.SCENARIOS[a.scenario]
world = generate_world(a.seed, scenario.islands, config, defs)
per = config.get("population.food_per_person")


def island_row(island):
    share = fac.labor_share(defs, island)
    tiles = short = short_ext = short_zero = 0
    cap_sum = need_sum = 0
    ratios = []
    for tile in island.tiles:
        if tile.population <= 0 or not popmod._farming(defs, tile):
            continue
        persons = popmod.subsistence_persons(defs, island, tile, share)
        if persons <= 0:
            continue
        need = to_milli(persons * per)
        cap = popmod._land_output(defs, config, island, tile, int(persons * share))
        tiles += 1
        cap_sum += cap
        need_sum += need
        short += cap < need
        short_zero += cap < need and int(persons * share) == 0
        short_ext += tile.ext.get(popmod.FARM_CAPACITY, 0) < need
        ratios.append(cap / need if need else 0.0)
    ratios.sort()
    return {"i": island.id, "tiles": tiles, "short": short, "short_ext": short_ext, "short_zero": short_zero,
            "cap": cap_sum, "need": need_sum,
            "ratio": cap_sum / need_sum if need_sum else None,
            "min": ratios[0] if ratios else None,
            "p10": ratios[len(ratios) // 10] if ratios else None}


out = gzip.open(a.out, "wt", encoding="utf-8")
out.write(json.dumps({"k": "meta", "scenario": a.scenario, "seed": a.seed, "turns": a.turns, "on": a.on,
                      "subsistence_sales": config.get("population.subsistence_sales")}) + "\n")
for _ in range(a.turns):
    measure.queue_scenario_orders(world, scenario, defs, config)
    game.engine.run_turn(world)
    rows = [island_row(isl) for isl in world.islands.values() if isl.has_economy]
    out.write(json.dumps({"k": "turn", "t": world.turn, "islands": rows}) + "\n")
    for island in world.islands.values():
        island.scratch.clear()
        island.journal.clear()
        island.books.clear()
out.close()
if a.selftest:
    with gzip.open(a.out, "rt", encoding="utf-8") as f:
        lines = [json.loads(x) for x in f]
    ok = len(lines) == 4 and all("islands" in x for x in lines[1:])
    with open(a.selftest, "w", encoding="utf-8") as f:
        f.write(("selftest: 通った" if ok else "selftest: 落ちた") + f"（行 {len(lines)}）\n")
print("済み", a.out)
