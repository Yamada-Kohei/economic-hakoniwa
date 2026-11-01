"""経済体制ごと（planned・mixed・market）に、島の暮らし向きと伸びを平均する。読むだけ。

    py -3.12 -X utf8 tools/regime_cmp/regime_cmp.py <measure の out-dir（--series つき）> out.txt

島の体制は結果の JSON の関門の行「島 N（体制）」から取る（関門に一度も出ない島は「?」）。
時系列は <組>.jsonl か <組>.jsonl.gz（measure batch --series）。
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REGIME = re.compile(r"島 (\d+)（(\w+)）")
KEYS = (
    "happiness",
    "wage.real",
    "food.ratio",
    "politics.stability",
    "unemployment",
    "debt_ratio",
    "power.shortage",
)
HEAD = (
    "値は島の平均。前半 = 13〜120 ターン、後半 = 最後の 120 ターン。"
    "伸び = 最後の 12 ターンの平均 ÷ 13〜24 ターンの平均。\n"
    "実質 GDP/人 = gdp ÷ cpi × 100 ÷ 人口。預金/人・融資/人・国庫/人も ÷ cpi × 100 ÷ 人口。"
)


def regimes(d: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for w in d.get("windows", {}).values():
        for line in w.get("gate", {}).get("lines", []):
            for m in REGIME.finditer(line):
                out[int(m.group(1))] = m.group(2)
    for c in d.get("collapse", {}).get("found", []):
        m = REGIME.search(c.get("line", ""))
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def rows(out: Path, name: str):
    for p in (out / f"{name}.jsonl", out / f"{name}.jsonl.gz", out / "series" / f"{name}.jsonl.gz"):
        if p.exists():
            op = gzip.open if p.suffix == ".gz" else open
            with op(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if "island" in r:
                        yield r
            return


def avg(rs: list[dict], lo: int, hi: int, f) -> float | None:
    v = [f(r) for r in rs if lo <= r["t"] <= hi]
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def real(key: str | None):
    def f(r: dict) -> float | None:
        pop, cpi = r.get("population") or 0, r.get("cpi") or 0
        if not pop or not cpi:
            return None
        return (r.get(key or "gdp") or 0) / cpi * 100 / pop

    return f


def ratio(a: float | None, b: float | None) -> float | None:
    return b / a if a and b else None


def island_values(rs: list[dict], turns: int) -> dict[str, float | None]:
    late = (turns - 119, turns)
    vals: dict[str, float | None] = {}
    for k in KEYS:
        vals[f"{k} 前半"] = avg(rs, 13, 120, lambda r, k=k: r.get(k))
        vals[f"{k} 後半"] = avg(rs, *late, lambda r, k=k: r.get(k))
    gdp = real(None)
    vals["実質GDP/人 前半"] = avg(rs, 13, 120, gdp)
    vals["実質GDP/人 後半"] = avg(rs, *late, gdp)
    vals["実質GDP/人 伸び"] = ratio(avg(rs, 13, 24, gdp), avg(rs, turns - 11, turns, gdp))
    pop = lambda r: r.get("population")  # noqa: E731
    vals["人口 伸び"] = ratio(avg(rs, 13, 24, pop), avg(rs, turns - 11, turns, pop))
    vals["預金/人 後半"] = avg(rs, *late, real("money.deposits"))
    vals["融資/人 後半"] = avg(rs, *late, real("loans"))
    vals["国庫/人 後半"] = avg(rs, *late, real("treasury"))
    return vals


def line(k: str, agg: dict, count: bool = False) -> str:
    parts = []
    for g, m in sorted(agg.items()):
        if m.get(k):
            n = f"（{len(m[k])}）" if count else ""
            parts.append(f"{g} {sum(m[k]) / len(m[k]):.3g}{n}")
    return f"  {k}: " + "、".join(parts)


def main(out: Path, dest: Path) -> None:
    lines = [f"# 体制ごとの比べ（{out}）", HEAD]
    total: dict = defaultdict(lambda: defaultdict(list))
    for p in sorted(out.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not (isinstance(d, dict) and "windows" in d):
            continue
        name, turns = p.stem, int(d.get("turns", 0))
        reg = regimes(d)
        per: dict[int, list[dict]] = defaultdict(list)
        for r in rows(out, name):
            per[r["island"]].append(r)
        if not per:
            continue
        agg: dict = defaultdict(lambda: defaultdict(list))
        keys: list[str] = []
        for i, rs in per.items():
            rs.sort(key=lambda r: r["t"])
            vals = island_values(rs, turns)
            keys = list(vals)
            for k, v in vals.items():
                if v is not None:
                    agg[reg.get(i, "?")][k].append(v)
                    total[reg.get(i, "?")][k].append(v)
        n = "、".join(f"{g} {len(m['人口 伸び'])}" for g, m in sorted(agg.items()))
        lines += ["", f"## {name}（{turns} ターン）島の数: {n}"]
        lines += [line(k, agg) for k in keys]
    lines += ["", "## 全部の組を合わせて（島の平均、かっこは島の数）"]
    if total:
        lines += [line(k, total, True) for k in next(iter(total.values()))]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
