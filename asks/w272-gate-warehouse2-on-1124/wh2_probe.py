"""関門の試験の種 S を wh2_plugin を差し込んで回し、生の記録と試験の出力を残す（W-272 燃料、会議 #4429・#4433）。
使い方: python wh2_probe.py --seed 2 --raw wh2-on-s2.json --log wh2-on-s2.txt
集計は wh2_sum.py（読むだけ。境の候補 × K × 分母をここで並べる）。"""

import argparse
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=2)
ap.add_argument("--raw", default="wh2-raw.json")
ap.add_argument("--log", default="wh2.txt")
a = ap.parse_args()
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=here + os.pathsep + os.environ.get("PYTHONPATH", ""),
           W272_WH2_OUT=os.path.abspath(a.raw))
test = f"tests/core/test_gate.py::test_supply_chain_runs_for_300_turns[{a.seed}]"
res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-p", "wh2_plugin", test],
                     capture_output=True, text=True, check=False, env=env)
with open(a.log, "w", encoding="utf-8") as f:
    f.write(f"種 {a.seed}\n終わりのコード {res.returncode}\n{res.stdout[-60000:]}\n{res.stderr[-20000:]}")
sys.exit(0)
