"""画面も DB もなしで、同じ段を回す一括シミュレーション。

    python -m hakoniwa_core.simulate --islands 50 --turns 300 --seed 1 --autoplan --gate

更新にかかる時間の測定や、自動テストでの確認に使います。--autoplan を付けると、
どの島にも標準の計画（hakoniwa_core/autoplan.py）で命令を並べます。--gate を付けると、
フェーズ2の関門の目安（hakoniwa_core/gate.py）から外れたところを最後に並べます。

バランス調整の回2 から、--scenario で場面（標準の計画・命令なし・中央銀行を独立させた国・少ない島の
世界。hakoniwa_core/measure.py）を選べます。--out で結果のまとめを JSON に（measure compare で
比べる）、--series で島×ターンの時系列（失業の内わけなど）を JSON Lines に書きます。
--digests で各ターンの終わりの指紋を JSON Lines に書き、measure digests でずれの始まりを
調べられます。重い指紋の計算は、この指定があるときだけ毎ターン行います。

崩れを調べるため（やり直しの報告の I5）、--save で最後のターンの世界を写しのファイルに書き、
--load で写しから続けられます。同じ種・同じ版なら同じ世界になるので、崩れの初めの数ターン前まで
回して写します（測りと夜の点検が、崩れごとにその命令を出します）。

    python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 137 --save s1-t137.json
    python -m hakoniwa_core.simulate --scenario plan --load s1-t137.json --turns 10 --gate

測り（--gate・--out・--series）を付けて --save すると、写しに測りの途中の状態（関門の記録・測りの
足し上げ・戦争の見張り）も入ります（C3 W-236 H-3）。その写しを --load して --out を付けると、
残りのターンだけを回して、通しで回したのと同じ結果の JSON を書きます（違うのは seconds と、
どこから続けたかを書く resumed の欄だけ）。quick（300 ターン）の最後の写しから full（500 ターン）
の残りを回すのに使います。名前が .gz で終わる写しは gzip で読み書きします。

    python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 300 --gate \\
        --out plan-s1-t300.json --save plan-s1-t300.snap.json.gz
    python -m hakoniwa_core.simulate --scenario plan --load plan-s1-t300.snap.json.gz \\
        --turns 200 --gate --out plan-s1-t500.json

続けてよいのは、同じ場面・同じ種を同じ計算の中身（指紋）で回した写しだけです。違う写しに --out か
--series を付けると、何も回さずに終了コード 2 で止まります（使う側が通しで回し直す）。--series は、
初めの分の時系列のファイルに書き足します（行数と中身の指紋が写しの記録と合うときだけ）。
--gate だけなら、これまでどおり写しの世界から新しい記録で続けます（崩れを調べる使い方）。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import asdict
from typing import Any

from hakoniwa_core import measure, reuse, scale
from hakoniwa_core.game import load_game
from hakoniwa_core.gate import START, Recorder
from hakoniwa_core.state import MarketRecord, TradeRecord, WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict


def world_digest(world: WorldState) -> str:
    """世界の状態の指紋。同じ種で同じ結果になるかを比べるのに使う。"""
    data = json.dumps(world_to_dict(world), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def snapshot_dict(
    world: WorldState, measure_state: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """測りの写しの中身（ファイルに書く前の dict）。

    世界の写し（world_to_dict）の横に、前のターンの統計・市場・約定も一緒に持つ。
    測りの命令作り（標準の計画）は前のターンのこの3つを読んで次の命令を作るので、
    写しから続けるときは一緒に運ばないと通しとずれる。本番のバックアップの写し
    （world_to_dict だけ）には入らない物で、そちらは変えない。
    """
    data = {
        "world": world_to_dict(world),
        "stats": {str(i.id): i.stats for i in world.islands.values()},
        "markets": {str(i.id): [asdict(m) for m in i.markets] for i in world.islands.values()},
        "trades": {str(i.id): [asdict(t) for t in i.trades] for i in world.islands.values()},
    }
    if measure_state is not None:
        data["measure"] = measure_state
    return data


def world_from_snapshot_dict(
    data: Mapping[str, Any],
) -> tuple[WorldState, dict[str, Any] | None]:
    """snapshot_dict の中身から、世界と（あれば）測りの途中の状態を作り直す。"""
    world = world_from_dict(data["world"])
    for iid, island in world.islands.items():
        island.stats = dict(data["stats"].get(str(iid), {}))
        island.markets = [MarketRecord(**m) for m in data["markets"].get(str(iid), [])]
        island.trades = [TradeRecord(**t) for t in data.get("trades", {}).get(str(iid), [])]
    return world, data.get("measure")


def save_snapshot(
    world: WorldState, path: str, measure_state: Mapping[str, Any] | None = None
) -> None:
    """世界の写しを書く。前のターンの統計・市場・約定と、測りの途中の状態も一緒に書く
    （サーバーでは統計・市場・約定は状態の写しでなく表にある）。"""
    data = snapshot_dict(world, measure_state)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_snapshot(path: str) -> tuple[WorldState, dict[str, Any] | None]:
    """世界の写しと、あれば測りの途中の状態を読む。"""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return world_from_snapshot_dict(data)


def load_snapshot(path: str) -> WorldState:
    """世界の写しだけを読む（これまでの呼び出し用）。"""
    return read_snapshot(path)[0]


def _file_sha256(path: str) -> str:
    """ファイルの中身の指紋（時系列は大きいので、少しずつ読む）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resume_reason(
    data: Mapping[str, Any],
    args: argparse.Namespace,
    scenario: str,
    people: float,
    world: WorldState,
) -> str:
    """測りを続けられなければ、そのわけを返す。時系列はまだ開き直さない。

    続けてよいのは、同じ場面を同じ計算の中身で回した写しだけ（違えば通しで回し直す。回しすぎる
    側に倒す）。国の大きさの倍は環境変数でも替わり、指紋に入らないので、別に確かめる。
    """
    if data.get("scenario") != scenario:
        return "場面が違う"
    if args.seed is not None and data.get("seed") != args.seed:
        return "種（seed）が違う"
    if data.get("seed") != world.seed or data.get("turn") != world.turn:
        return "測りの途中の状態が、写しの世界の種かターンと合わない"
    if data.get("code_fingerprint") != reuse.code_fingerprint(args.config):
        return "計算の中身の指紋（code_fingerprint）が違う"
    if data.get("people") != people:
        return "国の大きさの倍（people）が違う"
    if (args.gate or args.out) and data.get("recorder") is None:
        return "関門の記録の途中の状態（recorder）がない"
    if args.out or args.series:
        if data.get("meter") is None:
            return "測りの途中の状態（meter）がない"
        if data.get("zones_every") != args.zones_every:
            return "町と村の内わけを書く間隔（zones_every）が違う"
    if args.series:
        if data.get("series_lines") is None or data.get("series_sha256") is None:
            return "時系列の行数と中身の指紋（series_lines・series_sha256）がない"
        try:
            with open(args.series, encoding="utf-8") as f:
                lines = sum(1 for _ in f)
            content = _file_sha256(args.series)
        except (OSError, UnicodeError):
            return "続きに使う時系列のファイルがないか、読めない"
        if lines != data["series_lines"]:
            return "時系列の行数が写しの series_lines と違う"
        if content != data["series_sha256"]:  # 行数が同じでも、別の回の時系列には書き足さない
            return "時系列の中身が写しの series_sha256 と違う"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--islands", type=int, default=None, help="島の数（既定は場面の数、50）")
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="世界の種（既定は 1）。--load のときは世界を作らず、写しの種と同じかを確かめるだけ",
    )
    parser.add_argument("--config", default=None, help="game.yaml の場所")
    parser.add_argument("--autoplan", action="store_true", help="標準の計画で命令を並べる")
    parser.add_argument(
        "--scenario",
        choices=sorted(measure.SCENARIOS),
        default=None,
        help="場面（既定は --autoplan なら plan、なければ none）",
    )
    parser.add_argument("--gate", action="store_true", help="関門の目安から外れたところを並べる")
    parser.add_argument("--out", default=None, help="結果のまとめを書く JSON のファイル")
    parser.add_argument(
        "--series", default=None, help="島×ターンの時系列を書く JSON Lines のファイル"
    )
    parser.add_argument(
        "--digests", default=None, help="各ターンの終わりの指紋を書く JSON Lines のファイル"
    )
    parser.add_argument(
        "--zones-every", type=int, default=12, help="時系列に町と村ごとの内わけを書く間隔（ターン）"
    )
    parser.add_argument("--save", default=None, help="最後のターンの世界の写しを書くファイル")
    parser.add_argument("--load", default=None, help="この写しから続ける（--seed は使わない）")
    args = parser.parse_args(argv)

    scenario = measure.SCENARIOS[args.scenario or ("plan" if args.autoplan else "none")]
    islands = args.islands or scenario.islands
    seed = 1 if args.seed is None else args.seed
    game = load_game(args.config)
    started = time.perf_counter()
    saved_measure = None
    if args.load:
        world, saved_measure = read_snapshot(args.load)
        islands = len(world.islands)
        print(f"写しを読みました: ターン {world.turn}、{islands} 島")
    else:
        world = generate_world(seed, islands, game.config, game.defs)
        print(f"世界を作りました: {islands} 島（{time.perf_counter() - started:.2f} 秒）")
    print(f"場面: {scenario.title}")

    recorder = Recorder(world, game.defs, game.config) if args.gate or args.out else None
    resumed = None
    people = scale.people(game.config)
    # 写しに測りの途中の状態があり、測り（関門・結果・時系列）を求めているなら、続きとして扱う
    if saved_measure is not None and (recorder is not None or args.series):
        reason = _resume_reason(saved_measure, args, scenario.name, people, world)
        if reason:
            if args.out or args.series:
                print(f"写しの測りの途中の状態は使えません（{reason}）", file=sys.stderr)
                return 2
            print(f"写しの測りの途中の状態は使わない（{reason}）", file=sys.stderr)
        else:
            resumed = saved_measure
            if recorder is not None:
                recorder.restore(resumed["recorder"])
            print(f"測りの途中の状態を読みました: ターン {resumed['turn']} から")
    with ExitStack() as stack:
        series = None
        if args.series:
            mode = "a" if resumed is not None else "w"
            series = stack.enter_context(open(args.series, mode, encoding="utf-8"))
        digests = None
        if args.digests:
            digests = stack.enter_context(open(args.digests, "w", encoding="utf-8"))
        meter = None
        if args.out or series is not None:
            meter = measure.Measure(
                world, game.defs, game.config, series, args.zones_every, scenario.war
            )
            if resumed is not None:
                meter.restore(resumed["meter"])
        per_turn = []
        digest = None
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, game.defs, game.config)
            report = game.engine.run_turn(world)
            per_turn.append(report.total_seconds)
            if recorder is not None:
                recorder.record(world)
            if meter is not None:
                meter.record(world)
            if digests is not None:
                digest = world_digest(world)
                digests.write(json.dumps({"turn": world.turn, "digest": digest}) + "\n")
            # この3つは写しに入れず、次の run_turn の初めにも消える。その前の命令作り
            # （queue_scenario_orders）が読んでも、通しと続きで同じ空の状態になるようにする。
            # stats・markets・trades は写しに入るので消さない。
            for island in world.islands.values():
                island.scratch.clear()
                island.journal.clear()
                island.books.clear()
    seconds = time.perf_counter() - started
    total_seconds = seconds + (resumed["seconds"] if resumed is not None else 0)
    print(
        f"{args.turns} ターン: 1ターンの平均 {statistics.mean(per_turn) * 1000:.1f} ms、"
        f"最大 {max(per_turn) * 1000:.1f} ms"
    )
    population = sum(t.population for i in world.islands.values() for t in i.tiles)
    facilities = sum(len(i.facilities) for i in world.islands.values())
    print(f"人口の合計 {population}、施設の数 {facilities}")
    slowest = max(report.seconds, key=report.seconds.get) if report.seconds else "-"
    print(f"最後のターンで一番時間のかかった段: {slowest}")
    if digest is None:
        digest = world_digest(world)
    print(f"ターン {world.turn} の状態の指紋: {digest}")
    if args.save:
        measure_state = None
        if recorder is not None or meter is not None:
            measure_state = {
                "scenario": scenario.name,
                "seed": world.seed,
                "turn": world.turn,
                "code_fingerprint": reuse.code_fingerprint(args.config),
                "people": people,
                "zones_every": args.zones_every,
                "seconds": total_seconds,
                "recorder": recorder.state() if recorder is not None else None,
                "meter": meter.state() if meter is not None else None,
                "series_lines": meter.lines if meter is not None and args.series else None,
                "series_sha256": _file_sha256(args.series) if args.series else None,
            }
        save_snapshot(world, args.save, measure_state)
        print(f"写しを書きました: {args.save}")
    if args.out and recorder is not None and meter is not None:
        data = measure.result(
            scenario,
            world.seed if resumed is not None else seed,
            world.turn if resumed is not None else args.turns,
            islands,
            recorder,
            meter,
            total_seconds,
            digest,
            world,
            resumed={"from": resumed["turn"], "seconds": seconds} if resumed is not None else None,
        )
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    if args.gate and recorder is not None:
        summary = recorder.summary()
        print("関門の目安で見る値: " + "、".join(f"{k} {v:.3f}" for k, v in summary.items()))
        print("値段 ÷ 初めの値段 ÷ 物価指数の伸び（最小・中央・最大）:")
        for good, (low, mid, high) in recorder.prices().items():
            print(f"  {good}: {low:.2f}・{mid:.2f}・{high:.2f}")
        print("財ごとの、作った島と売り買いのあった島の数（1ターンあたりの平均）:")
        for good, (made, traded) in recorder.goods().items():
            print(f"  {good}: {made:.1f}・{traded:.1f}")
        collapses = recorder.collapses()
        print(f"崩れ（1ターン目から）: {len(collapses)} 件")
        for c in collapses:
            print("  " + c["line"])
            if not args.load:
                print("    写し: " + measure.snapshot_command(scenario.name, seed, c["first"]))
        failures = recorder.failures(START, kinds=scenario.kinds)
        counted = "全部" if scenario.kinds is None else "、".join(scenario.kinds)
        print(f"目安から外れたところ（この場面で数える目安: {counted}）: {len(failures)} 件")
        for line in failures:
            print("  " + line)
        others = len(recorder.failures(START)) - len(failures)
        if others:
            print(f"（この場面で数えない目安から外れたところ: {others} 件）")
        return 1 if failures or collapses else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
