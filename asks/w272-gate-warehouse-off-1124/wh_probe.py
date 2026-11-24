"""関門の試験の種 S を、国の倉庫を売り手に数える荷の境（要る量の何ターン分か）を替えて回す（会議 #4423・#4426）。
使い方: python wh_probe.py --seed 2 --wh 1 --need 5 --log wh-s2-1.txt"""

import argparse
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=2)
ap.add_argument("--wh", default="1")
ap.add_argument("--need", default="5")
ap.add_argument("--log", default="wh.txt")
a = ap.parse_args()
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=here + os.pathsep + os.environ.get("PYTHONPATH", ""),
           W272_WH_TURNS=a.wh, W272_NEED_TURNS=a.need)
test = f"tests/core/test_gate.py::test_supply_chain_runs_for_300_turns[{a.seed}]"
res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-p", "wh_plugin", test],
                     capture_output=True, text=True, check=False, env=env)
with open(a.log, "w", encoding="utf-8") as f:
    f.write(f"境 {a.wh} ターン分・要る量の平均 {a.need} ターン・種 {a.seed}\n終わりのコード {res.returncode}\n")
    f.write(res.stdout[-60000:] + "\n" + res.stderr[-20000:])
sys.exit(0)
