"""measure の quick（全部の場面）を回し、出た物を 1 つの zip にまとめる（asks の入口）。使い方: measure_quick.py <out-dir>"""
import os
import shutil
import subprocess
import sys

out = sys.argv[1]
env = dict(os.environ, PYTHONPATH=os.getcwd(), PYTHONUTF8="1")
code = subprocess.call([sys.executable, "-X", "utf8", "-m", "hakoniwa_core.measure", "batch", "--set", "quick",
                        "--jobs", "1", "--out-dir", out], env=env)
shutil.make_archive(out, "zip", out)
sys.exit(code)
