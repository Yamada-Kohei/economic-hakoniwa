"""測りを途中の写しから続けた結果が、通しで回した結果と同じかを、組ごとに確かめる（C3 W-236 H-3）。

組ごとに次の 2 通りを別のプロセスで回して比べます。

- 通し: 初めから最後まで（simulate --turns T）
- 続き: 切る所まで回して写しを書き（--turns S --save）、新しいプロセスで写しから残りを回す
  （--load --turns T-S）。続きのプロセスは文字列の hash の種（PYTHONHASHSEED）を変える

比べる物: 結果の JSON（seconds と resumed の欄のほか全部）、島×ターンの時系列（バイトまで）、
各ターンの世界の指紋（simulate に --digests があるとき）、最後のターンの写し（世界と測りの途中の
状態。かかった秒のほか、欄の順まで）。1 つでも違えば終了コード 1。

写しは結果とは別に比べます。途中の状態の中の順（dict に入れた順など）が hash の種で変わっても、
結果の JSON には出ないことがあるためです。

    # full の 500 ターンの組を、300 ターン目で切って比べる（重いので家の計算機で）
    python tools/measure/resume_check.py --set full --split 300 --out-dir runs/resume-check --jobs 8
    # 300 ターンの組も含めて、150 ターン目（商人が量を減らしている間・戦争の後）で切る
    python tools/measure/resume_check.py --set full --split 150 --out-dir runs/resume-check-150
    # 通しの結果がもうあるとき（同じ版の full の結果のフォルダ）は、続きだけ回して結果を比べる
    python tools/measure/resume_check.py --split 300 --straight runs/full --out-dir runs/rc
    # 通しの結果が前の版（main）の物のとき。指紋の欄のほかが同じなら、この直しで通しの結果も
    # 変わっていない
    python tools/measure/resume_check.py --split 300 --straight runs/main-full --other-code \\
        --out-dir runs/rc
    # 回し終えた --out-dir を、回し直さずにもう一度比べる（上の --straight と組み合わせる）
    python tools/measure/resume_check.py --split 300 --out-dir runs/resume-check --keep \\
        --straight runs/main-full --other-code
    # 何回も切って続ける（商人が量を減らし始める 120 ターン目と去る 240 ターン目の前後）
    python tools/measure/resume_check.py --set quick --only small --split 110,130,230,250 \\
        --out-dir runs/rc-merchant
    # 短い確かめ（場面:種:ターン:切る所。切る所は + でつないで何回でも）
    python tools/measure/resume_check.py --pairs small:1:8:5,war:1:8:3+5 --out-dir /tmp/rc

結果は --out-dir の resume-check.md にも書きます。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from hakoniwa_core import measure  # noqa: E402

NOT_COMPARED = ("seconds", "resumed")  # 通しと続きで違ってよい欄
SHOWN = 5  # 違いを並べる数


@dataclass
class Pair:
    scenario: str
    seed: int
    turns: int
    splits: tuple[int, ...]  # 写しを書いて切るターン（小さい順。何回でも）
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)

    @property
    def name(self) -> str:
        cuts = "-".join(str(t) for t in self.splits)
        return f"{measure.run_name(self.scenario, self.seed, self.turns)}-from{cuts}"


def simulate(args: list[str], log: Path, hash_seed: str) -> tuple[int, float]:
    """simulate を別のプロセスで回す。終了コードと、かかった秒。"""
    env = os.environ | {"PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO), env.get("PYTHONPATH", "")) if p)
    started = time.perf_counter()
    with open(log, "w", encoding="utf-8") as out:
        code = subprocess.run(
            [sys.executable, "-m", "hakoniwa_core.simulate", *args],
            cwd=REPO,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
    return code, time.perf_counter() - started


def has_digests() -> bool:
    """simulate に各ターンの指紋を書く --digests があるか（別の直しで足す）。"""
    from hakoniwa_core import simulate as module

    return "--digests" in Path(module.__file__).read_text(encoding="utf-8")


def run_straight(pair: Pair, out: Path, digests: bool) -> None:
    base = out / pair.name
    args = ["--scenario", pair.scenario, "--seed", str(pair.seed), "--turns", str(pair.turns)]
    args += ["--gate", "--out", f"{base}.straight.json", "--series", f"{base}.straight.jsonl"]
    args += ["--save", f"{base}.straight.snap.json.gz"]
    if digests:
        args += ["--digests", f"{base}.straight.digests"]
    _, pair.seconds["straight"] = simulate(args, Path(f"{base}.straight.log"), "1")


def run_resumed(pair: Pair, out: Path, digests: bool) -> None:
    """切る所ごとに写しを書き、次は新しいプロセスでその写しから続ける。"""
    base = out / pair.name
    series = ["--gate", "--series", f"{base}.resumed.jsonl"]
    done, snap = 0, ""
    pair.seconds["rest"] = 0.0
    for number, end in enumerate([*pair.splits, pair.turns]):
        last = end == pair.turns
        args = ["--scenario", pair.scenario, "--seed", str(pair.seed), "--turns", str(end - done)]
        args += series
        if snap:
            args += ["--load", snap]
        if last:
            args += ["--out", f"{base}.resumed.json", "--save", f"{base}.resumed.snap.json.gz"]
        else:
            # 切る所でも結果を書く（quick が結果と写しの両方を書くのと同じ形）
            snap = f"{base}.t{end}.snap.json.gz"
            args += ["--out", f"{base}.t{end}.json", "--save", snap]
        if digests:
            args += ["--digests", f"{base}.part{number}.digests"]
        # 続きのプロセスは、文字列の hash の種を初めの分と変える
        code, seconds = simulate(args, Path(f"{base}.part{number}.log"), str(number + 1))
        pair.seconds["first" if number == 0 else "rest"] = (
            pair.seconds.get("first" if number == 0 else "rest", 0.0) + seconds
        )
        if code == 2:
            pair.problems.append(f"続きが写しを使わなかった（.part{number}.log）")
            return
        if not last and not Path(snap).exists():
            pair.problems.append(f"{end} ターン目の写しを書かずに終わった（.part{number}.log）")
            return
        done = end


def differences(a: Any, b: Any, path: str = "") -> list[str]:
    """2 つの JSON の値の、違う所の名前（上から SHOWN 個まで探す）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        found = [f"{path}.{k}（片方だけ）" for k in sorted(set(a) ^ set(b))]
        if list(a) != list(b) and not found:
            found.append(f"{path}（欄の順）")
        for key in a:
            if key in b and len(found) < SHOWN:
                found += differences(a[key], b[key], f"{path}.{key}")
        return found[:SHOWN]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}（長さ {len(a)} と {len(b)}）"]
        found = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if len(found) >= SHOWN:
                break
            found += differences(x, y, f"{path}[{i}]")
        return found[:SHOWN]
    same = type(a) is type(b) and (a == b or (a != a and b != b))  # NaN どうしは同じとみる
    return [] if same else [f"{path}: {a!r} と {b!r}"]


def compared(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in NOT_COMPARED}


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def compare_series(a: Path, b: Path) -> str | None:
    """時系列の最初に違う行（なければ None）。大きいので 1 行ずつ読む。"""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        for number, line in enumerate(fa, start=1):
            other = fb.readline()
            if line != other:
                try:
                    where = json.loads(line)
                    return f"{number} 行目（ターン {where.get('t')}・島 {where.get('island')}）"
                except ValueError:
                    return f"{number} 行目"
        if fb.readline():
            return "続きの方が長い"
    return None


def read_snapshot(path: Path) -> dict | None:
    """写しの中身。かかった秒（回すたびに違う）は外す。"""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    if isinstance(data.get("measure"), dict):
        data["measure"].pop("seconds", None)
    return data


def compare_snapshots(a_path: Path, b_path: Path) -> tuple[str, bool]:
    """最後のターンの写しを比べた結果の文と、違ったかどうか。"""
    if not a_path.exists() or not b_path.exists():
        return "最後の写しは比べていない（通しか続きの写しがない）", False
    a, b = read_snapshot(a_path), read_snapshot(b_path)
    if a is None or b is None:
        return "最後の写しを読めない", True
    # 文字にして比べる（欄の順も見る）。違うときだけ、どこが違うかを探す
    if json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False):
        return "最後の写しも同じ", False
    found = differences(a, b) or ["（文字にすると違う）"]
    return "最後の写しが違う: " + " / ".join(found), True


def read_digests(*paths: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["turn"]] = row["digest"]
    return out


def check(
    pair: Pair, out: Path, straight_dir: Path | None, digests: bool, other_code: bool = False
) -> None:
    base = out / pair.name
    if straight_dir is None:
        a_path = Path(f"{base}.straight.json")
    else:
        a_path = straight_dir / f"{measure.run_name(pair.scenario, pair.seed, pair.turns)}.json"
    a, b = read_json(a_path), read_json(Path(f"{base}.resumed.json"))
    if a is None:
        pair.problems.append(f"通しの結果がない（{a_path.name}）")
    if b is None:
        pair.problems.append("続きの結果がない（.part*.log）")
    if a is None or b is None:
        return
    if other_code:  # 通しは前の版の結果。指紋の欄は違って当たり前なので比べない
        a, b = a | {"code_fingerprint": ""}, b | {"code_fingerprint": ""}
        pair.notes.append("通しは別の版の結果")
    elif a.get("code_fingerprint") != b.get("code_fingerprint"):
        pair.problems.append("通しと続きで計算の中身の指紋が違う（版をそろえて回す）")
        return
    found = differences(compared(a), compared(b))
    if found:
        pair.problems.append("結果の JSON が違う: " + " / ".join(found))
    if (b.get("resumed") or {}).get("from") != pair.splits[-1]:
        pair.problems.append(f"続きの結果に resumed.from={pair.splits[-1]} がない")
    a_series, b_series = Path(f"{base}.straight.jsonl"), Path(f"{base}.resumed.jsonl")
    if straight_dir is not None:
        a_series = a_path.with_suffix(".jsonl")
    if a_series.exists() and b_series.exists():
        where = compare_series(a_series, b_series)
        if where:
            pair.problems.append(f"時系列が違う: {where}")
    else:
        pair.notes.append("時系列は比べていない（通しの時系列がない）")
    if digests and straight_dir is None:
        x = read_digests(Path(f"{base}.straight.digests"))
        parts = range(len(pair.splits) + 1)
        y = read_digests(*(Path(f"{base}.part{n}.digests") for n in parts))
        wrong = sorted(t for t in x if x[t] != y.get(t))
        if wrong or len(x) != pair.turns:
            first = wrong[0] if wrong else "（行が足りない）"
            pair.problems.append(
                f"各ターンの指紋が違う: 最初は {first} ターン目（{len(wrong)} ターン）"
            )
        else:
            pair.notes.append(f"各ターンの指紋 {len(x)} ターン同じ")
    else:
        pair.notes.append("各ターンの指紋は比べていない（最後の指紋は結果の JSON の中）")
    text, wrong_snapshot = compare_snapshots(
        Path(f"{base}.straight.snap.json.gz"), Path(f"{base}.resumed.snap.json.gz")
    )
    (pair.problems if wrong_snapshot else pair.notes).append(text)
    if "rest" in pair.seconds:
        pair.notes.append(
            f"通し {a.get('seconds', 0):.0f} 秒、続きだけ {b['resumed'].get('seconds', 0):.0f} 秒"
        )


def cuts_of(text: str, separator: str) -> tuple[int, ...]:
    cuts = tuple(int(t) for t in text.split(separator))
    if not cuts or list(cuts) != sorted(set(cuts)) or cuts[0] < 1:
        raise SystemExit(f"切る所は 1 以上の数を小さい順に書いてください（{text}）")
    return cuts


def pairs_of(args: argparse.Namespace) -> list[Pair]:
    if args.pairs:
        out = []
        for text in args.pairs.split(","):
            scenario, seed, turns, cuts = text.split(":")
            out.append(Pair(scenario, int(seed), int(turns), cuts_of(cuts, "+")))
        return out
    splits = cuts_of(args.split, ",")
    keep = set(args.only.split(",")) if args.only else None
    return [
        Pair(scenario, seed, turns, splits)
        for scenario, seed, turns in measure.SETS[args.set]
        if turns > splits[-1] and (keep is None or scenario in keep)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=sorted(measure.SETS), default="full")
    parser.add_argument(
        "--split", default="300", help="写しを書くターン（切る所）。カンマで区切ると何回も切る"
    )
    parser.add_argument("--only", help="場面の名前をカンマで区切って、その場面だけ")
    parser.add_argument(
        "--pairs",
        help="場面:種:ターン:切る所 をカンマで区切って（--set の代わり。切る所は + でつなぐ）",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument(
        "--keep", action="store_true", help="--out-dir にもう結果がある組は回さず、比べだけやり直す"
    )
    parser.add_argument("--straight", help="通しの結果のフォルダ（あれば通しを回さない）")
    parser.add_argument(
        "--other-code",
        action="store_true",
        help="--straight が別の版（main など）の結果のとき。指紋の欄を比べない",
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error(f"--jobs は 1 以上にしてください（{args.jobs}）")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    straight_dir = Path(args.straight) if args.straight else None
    digests = has_digests()
    pairs = pairs_of(args)
    if not pairs:
        print("比べる組がありません（切る所より長い組がない）")
        return 2
    # 長い組から先に始める（最後に長い組だけが残って待つのを避ける）
    work = []
    for pair in sorted(pairs, key=lambda p: -p.turns * measure.SCENARIOS[p.scenario].islands):
        base = out / pair.name
        if straight_dir is None and not (args.keep and Path(f"{base}.straight.json").exists()):
            work.append((run_straight, pair))
        if not (args.keep and Path(f"{base}.resumed.json").exists()):
            work.append((run_resumed, pair))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for job in [pool.submit(run, pair, out, digests) for run, pair in work]:
            job.result()
    lines = ["| 組 | 結果 | 中身 |", "|---|---|---|"]
    for pair in pairs:
        check(pair, out, straight_dir, digests, args.other_code)
        verdict = "違う" if pair.problems else "同じ"
        lines.append(f"| {pair.name} | {verdict} | {'。'.join(pair.problems + pair.notes)} |")
    failed = sum(1 for p in pairs if p.problems)
    lines += ["", f"{len(pairs)} 組のうち、違う組 {failed}"]
    text = "\n".join(lines)
    (out / "resume-check.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
