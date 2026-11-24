"""W-272 ④ 既定入の下書き（PR #490）で落ちた関門の試験（1 島・標準の計画・300 ターン、種 2）の調べ。

--pytest: 試験そのものを -s で回し、出力を out に書く（どの値段がいつ外れたか）。
--record: 試験と同じ島を同じ回し方で回し、ターンごとの島の stats（値段・産出・在庫など全部）と、村と町の
          マスの数・人数・自給の人を jsonl.gz に書く。切と入の 2 つの頼みで同じ物を書き、集計で比べる。
"""

import argparse
import gzip
import json
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["pytest", "record"], required=True)
ap.add_argument("--seed", type=int, default=2)
ap.add_argument("--out", required=True)
ap.add_argument("--turns", type=int, default=0)  # 0 なら試験と同じ 300
a = ap.parse_args()
sys.path.insert(0, os.getcwd())

if a.mode == "pytest":
    test = f"tests/core/test_gate.py::test_supply_chain_runs_for_300_turns[{a.seed}]"
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", test],
                         capture_output=True, text=True, check=False)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(f"終わりのコード {res.returncode}\n{res.stdout[-60000:]}\n{res.stderr[-20000:]}")
    sys.exit(0)

from hakoniwa_core import facilities as fac  # noqa: E402
from hakoniwa_core.game import load_game  # noqa: E402
from hakoniwa_core.modules import population as popmod  # noqa: E402
from tests.core.test_gate import TURNS, make_world, run_with_plan  # noqa: E402

game = load_game()
defs, config = game.defs, game.config
world = make_world(game, a.seed, ["coal", "iron_ore"])
out = gzip.open(a.out, "wt", encoding="utf-8")
out.write(json.dumps({"k": "meta", "farming_by_housing": config.get("population.farming_by_housing"),
                      "upgrade_without_subsistence": config.get("housing.upgrade_without_subsistence"),
                      "seed": a.seed, "turns": TURNS}) + "\n")


def each(island):
    share = fac.labor_share(defs, island)
    homes = {(f.x, f.y): f for f in island.facilities if fac.is_housing(config, f) and fac.on_map(f)}
    row = {"village_tiles": 0, "town_tiles": 0, "village_pop": 0, "town_pop": 0, "sub": 0, "town_idle": 0}
    for pos, h in homes.items():
        t = island.tile(*pos)
        if popmod._built_farming(defs, h):
            row["village_tiles"] += 1
            row["village_pop"] += t.population
        else:
            row["town_tiles"] += 1
            row["town_pop"] += t.population
            row["town_idle"] += max(0, fac.labor_force(defs, island, t, share) - t.ext.get("employed", 0))
        row["sub"] += popmod.subsistence_persons(defs, island, t, share)
    kinds = {}
    for f in island.facilities:
        if not fac.is_housing(config, f) and f.built and "foreclosed" not in f.economy:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
    out.write(json.dumps({"k": "turn", "t": world.turn, "stats": dict(island.stats), "homes": row,
                          "facilities": kinds}, ensure_ascii=False, default=str) + "\n")


run_with_plan(game, world, a.turns or TURNS, each)
out.close()
print("済み", a.out)
