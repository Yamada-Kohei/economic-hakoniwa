"""測りの結果のフォルダ 2 つが、同じ結果かを組ごとに確かめる（C3 W-236 H-3）。

`measure batch` を「写しから続き」で回した結果と、`--no-resume`（通し）で回した結果を比べるための
道具です。結果の JSON は seconds と resumed の欄のほか全部（欄の順も）、時系列はバイトまで比べます。
1 組でも違うか、片方にしかなければ終了コード 1。

    python tools/measure/same_results.py runs/full-resumed runs/full-straight
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from resume_check import compare_series, compared, differences, read_json  # noqa: E402


def check(a_dir: Path, b_dir: Path) -> tuple[list[str], int]:
    """表の行と、違う組の数。"""
    names = sorted({p.stem for folder in (a_dir, b_dir) for p in folder.glob("*.json")})
    lines = ["| 組 | 結果 | 中身 |", "|---|---|---|"]
    failed = 0
    for name in names:
        a, b = read_json(a_dir / f"{name}.json"), read_json(b_dir / f"{name}.json")
        problems, notes = [], []
        if a is None or b is None:
            problems.append("片方にしか結果がない（か読めない）")
        else:
            found = differences(compared(a), compared(b))
            if found:
                problems.append("結果の JSON が違う: " + " / ".join(found))
            for label, data in (("1 つ目", a), ("2 つ目", b)):
                if "resumed" in data:
                    notes.append(f"{label}は {data['resumed'].get('from')} ターン目から続き")
            notes.append(f"{a.get('seconds', 0):.0f} 秒と {b.get('seconds', 0):.0f} 秒")
            a_series, b_series = a_dir / f"{name}.jsonl", b_dir / f"{name}.jsonl"
            if a_series.exists() and b_series.exists():
                where = compare_series(a_series, b_series)
                if where:
                    problems.append(f"時系列が違う: {where}")
                else:
                    notes.append("時系列も同じ")
            else:
                notes.append("時系列は比べていない（片方か両方にない）")
        failed += 1 if problems else 0
        verdict = "違う" if problems else "同じ"
        lines.append(f"| {name} | {verdict} | {'。'.join(problems + notes)} |")
    lines += ["", f"{len(names)} 組のうち、違う組 {failed}"]
    return lines, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("first")
    parser.add_argument("second")
    args = parser.parse_args(argv)
    lines, failed = check(Path(args.first), Path(args.second))
    print("\n".join(lines))
    if len(lines) == 4:  # 表の頭と終わりの行だけ
        print("比べる結果がありません")
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
