"""2つの SQLite の世界の DB の中身が同じかを比べる（W-35、速くする直しの前後の確かめ）。

同じ写しから、直す前と後のコードで同じターン数を進め（manage.py profile_turn --keep）、
その2つを渡す。時刻の欄は比べず、バックアップは解凍した中身で比べる。

    python tools/perf/compare_db.py before.sqlite3 after.sqlite3
"""

from __future__ import annotations

import gzip
import sqlite3
import sys

SKIP = {"created_at", "updating_since", "next_turn_at", "applied"}


def main(argv: list[str]) -> int:
    a, b = (sqlite3.connect(p) for p in argv[1:3])
    tables = [
        r[0]
        for r in a.execute(
            "select name from sqlite_master where type='table'"
            " and name not like 'sqlite_%' and name not like 'django_%' order by name"
        )
    ]
    bad = 0
    for t in tables:
        cols = [r[1] for r in a.execute(f'pragma table_info("{t}")') if r[1] not in SKIP]
        q = f'select {",".join(chr(34) + c + chr(34) for c in cols)} from "{t}" order by 1'
        ra, rb = a.execute(q).fetchall(), b.execute(q).fetchall()
        if t == "turns_backup":
            i = cols.index("data")
            ra = [r[:i] + (gzip.decompress(r[i]),) + r[i + 1 :] for r in ra]
            rb = [r[:i] + (gzip.decompress(r[i]),) + r[i + 1 :] for r in rb]
        if ra != rb:
            bad += 1
            diff = sum(1 for x, y in zip(ra, rb, strict=False) if x != y)
            print(f"違う: {t} 行 {len(ra)} 対 {len(rb)}、違う行 {diff}")
    print(f"表 {len(tables)}、違う表 {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
