"""W-307 の案 a の比べの入口（asks の枝から回す）。1 つ目の引数で分ける:
  probe <offtake_probe_v2.py の引数...>  /  trace3 <offtake_trace3.py の引数...>"""

import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=os.getcwd())
script = {"trace3": "offtake_trace3.py"}[sys.argv[1]]
sys.exit(subprocess.call([sys.executable, "-X", "utf8", os.path.join(here, script), *sys.argv[2:]], env=env))
