"""画面を開く道具用の世界を、新しい SQLite の DB に作る（段6 B4）。

    DATABASE_URL=sqlite:////tmp/screens.sqlite3 python tools/screens/seed.py

部隊・艦隊・占領・反乱軍・革命のあとの選び直し待ち・建国直後・人のいない島・銀行のない島の
6 島を作り、島の種類 → 島の番号の JSON を標準出力に出す。アカウントは島の種類ごとに
owner-<種類>（パスワードは tests/server/busy_world.py の PASSWORD）。そのあと
`python manage.py runserver` と tools/screens/open_all.mjs で画面を開く。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

call_command("migrate", verbosity=0)

from tests.server.busy_world import PASSWORD, build  # noqa: E402

kinds = build()
print(json.dumps({"islands": kinds, "password": PASSWORD}, ensure_ascii=False))
