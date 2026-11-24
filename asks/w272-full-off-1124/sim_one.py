"""標準の full の 1 組を回す（measure batch --set full と同じ simulate の呼び方。--gate と --series 付き）。

simulate --gate は、目安の外れや崩れがあると 1 で終わる（結果は書いてある）。受付が失敗と扱わないよう、
終わりのコードは <名前>.exit に書いて 0 で終わる。名前は measure batch と同じ <場面>-s<種>-t<ターン>。
集める物は <名前>.json（measure compare で比べる）と <名前>.exit、series は raw/ に残す。
"""

import argparse
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--scenario", required=True)
ap.add_argument("--seed", type=int, required=True)
ap.add_argument("--turns", type=int, required=True)
a = ap.parse_args()
name = f"{a.scenario}-s{a.seed}-t{a.turns}"
os.makedirs("raw", exist_ok=True)
command = [sys.executable, "-m", "hakoniwa_core.simulate", "--scenario", a.scenario, "--seed", str(a.seed),
           "--turns", str(a.turns), "--gate", "--out", f"{name}.json", "--series", f"raw/{name}.jsonl"]
code = subprocess.run(command, check=False).returncode
with open(f"{name}.exit", "w", encoding="utf-8") as f:
    f.write(f"{code}\n")
print(name, "終わりのコード", code)
