"""受付の汎用の入れ口の試し: 箱庭のコアを読み込み、引数と版を out に書き、raw/ に生の記録を置く。"""
import argparse, json, os, platform, subprocess, sys
ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int); ap.add_argument("--out")
a = ap.parse_args()
import hakoniwa_core  # 作業ツリーのコアが読めるか
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
os.makedirs("raw", exist_ok=True)
with open("raw/rows.jsonl", "w", encoding="utf-8") as f:
    for i in range(3):
        f.write(json.dumps({"seed": a.seed, "i": i}) + "\n")
res = {"seed": a.seed, "argv": sys.argv, "head": head, "machine": platform.node(), "python": sys.version.split()[0],
       "core": os.path.dirname(hakoniwa_core.__file__)}
open(a.out, "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1))
print("試し済み", res)
res2 = open("AGENTS.md", encoding="utf-8").read().rstrip().splitlines()[-1]
open(a.out + ".patched", "w", encoding="utf-8").write(res2)
