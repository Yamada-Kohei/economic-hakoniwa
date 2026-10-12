"""定義データの検査: python -m hakoniwa_core.defs [game.yaml]"""

from __future__ import annotations

import sys

from hakoniwa_core.config import default_config_path, load_config
from hakoniwa_core.defs import DefsError, load_defs


def main(argv: list[str]) -> int:
    path = argv[0] if argv else default_config_path()
    try:
        defs = load_defs(load_config(path))
    except DefsError as e:
        print(f"定義データに {len(e.issues)} 件の誤りがあります:", file=sys.stderr)
        for issue in e.issues:
            print(f"  {issue}", file=sys.stderr)
        return 1
    packs = ", ".join(f"{p.name} {p.version}" for p in defs.packs)
    counts = ", ".join(f"{k} {len(v)}" for k, v in defs.tables.items())
    print(f"OK: パック {packs} / {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
