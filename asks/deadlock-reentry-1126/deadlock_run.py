"""取引の行き詰まりの直しの測りの入口（asks の枝から回す）。1 つ目の引数で分ける:
  reentry <deadlock_trace.py の引数...>"""

import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONPATH=os.getcwd())
script = {"reentry": "deadlock_trace.py"}[sys.argv[1]]
sys.exit(subprocess.call([sys.executable, "-X", "utf8", os.path.join(here, script), *sys.argv[2:]], env=env))
