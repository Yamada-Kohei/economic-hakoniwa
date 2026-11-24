"""関門の試験 test_supply_chain_runs_for_300_turns[2] を fuel_store_plugin を差し込んで回す（W-272 燃料、会議 #4388）。
使い方: python fuel_store_probe.py --out fuel-store-on.jsonl --log gate-pytest-on.txt"""

import argparse
import os
import shutil
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="fuel-store.jsonl")
ap.add_argument("--log", default="gate-pytest.txt")
a = ap.parse_args()
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=here + os.pathsep + os.environ.get("PYTHONPATH", ""))
test = "tests/core/test_gate.py::test_supply_chain_runs_for_300_turns[2]"
res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-p", "fuel_store_plugin",
                      test], capture_output=True, text=True, check=False, env=env)
with open(a.log, "w", encoding="utf-8") as f:
    f.write(f"終わりのコード {res.returncode}\n{res.stdout[-60000:]}\n{res.stderr[-20000:]}")
if os.path.exists("fuel-store.jsonl") and a.out != "fuel-store.jsonl":
    shutil.move("fuel-store.jsonl", a.out)
sys.exit(0)
