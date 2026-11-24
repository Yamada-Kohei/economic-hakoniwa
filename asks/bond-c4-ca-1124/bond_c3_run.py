"""国債 c3 の比べの入口（asks の枝から回す）。1 つ目の引数で分ける:
  probe <bond_c_probe.py の引数...>  /  quick <out-dir>（independent・plan・small の measure quick）"""

import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=os.getcwd())
mode, rest = sys.argv[1], sys.argv[2:]
if mode == "probe":
    cmd = [sys.executable, "-X", "utf8", os.path.join(here, "bond_c_probe.py"), *rest]
else:
    cmd = [sys.executable, "-X", "utf8", "-m", "hakoniwa_core.measure", "batch", "--set", "quick",
           "--only", "independent,plan,small", "--jobs", "1", "--out-dir", rest[0]]
code = subprocess.call(cmd, env=env)
if mode == "quick":  # 集める物は 1 つの zip に（out-dir は手元に残す）
    import shutil

    shutil.make_archive(rest[0], "zip", rest[0])
sys.exit(code)
