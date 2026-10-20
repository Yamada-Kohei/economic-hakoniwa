"""夜の点検: 長い年月の世界と、でたらめな命令を出す国の世界を回し、壊れた所を並べる。

セビーリャさんの PC（X870）で、空いている夜に回すための道具です（tools/nightly/run-nightly.ps1、
docs/nightly.md）。ゲームの状態は読むだけで、数字を寄せることはしません。見るのは次の2つです。

- 長い年月（long）: 標準の計画・命令なし・少ない島の世界を LONG_TURNS ターン（100 年）回し、関門の
  目安（hakoniwa_core/gate.py）から外れたところを、バランスの測りが見る範囲（START〜MEASURED_TURNS）と
  その後の期間に分けて数えます。後の期間にだけ出た種類は「40 年より後に出た崩れ」です。
- 崩れ（collapse）: 長い年月の組で、1ターン目から崩れの見張り（gate.COLLAPSE_KINDS。飢饉・人口の
  大きな減り・品切れ・取引が成り立たない・作り方の群れ・国庫や銀行の手元が 0・理由の出ない停止）に
  当たったところを、種類ごとに並べます。崩れの数ターン前の世界の写しを作る命令も付けます。
- でたらめな国（random）: 標準の計画で動く世界のうち RANDOM_ISLANDS 島が、毎ターン決まりの中で
  でたらめな命令を出します。命令は画面と同じ確かめ（hakoniwa_core/orders.check_order）を通った
  ものだけを並べます。ターンの処理が例外で止まったら、そのターンと並べた命令を書き残します（サーバー
  ならターンが進まなくなる不具合）。命令の受け付けの確かめで例外が出たもの（画面なら 500）も書き残し
  ます。

    python -m hakoniwa_core.nightly run --out-dir runs/nightly/20261017 --seed-base 20261017
    python -m hakoniwa_core.nightly one --kind random --seed 202610171 --turns 300 --out r.json
    python -m hakoniwa_core.nightly summary --out-dir runs/nightly/20261017

run は組をまとめて別の処理で回し、終わったら summary.json と summary.md を書きます。--known に前の夜
までに見つけたものの一覧（known.json）を渡すと、新しく見つけたものだけに「新」を付けて一覧を更新し
ます。止まった組は、summary.md の「同じことを起こす命令」で1つだけ回し直せます（同じ版・同じ種なら
同じ結果）。--until の時刻（既定は朝 6 時 30 分、PC の時計）になったら残りの組を止めてまとめを書き
ます（朝 7 時前の確かめに間に合わせるため。--hours で時間の長さでも決められます）。組は SAVE_EVERY
ターンごとに途中の結果を書くので、止めた組もそこまでの分を見ます。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

from hakoniwa_core import measure
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.defs.freeze import thaw
from hakoniwa_core.diplomacy import treaties as diplomacy_treaties
from hakoniwa_core.game import Game, load_game
from hakoniwa_core.gate import KINDS, START, Recorder
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import ORDER_DONE, ORDER_FAILED, ORDER_PENDING, IslandState, WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs as trade_blocs
from hakoniwa_core.trade import contracts as trade_contracts

LONG_TURNS = 1200  # 100 年
MEASURED_TURNS = 500  # バランスの測り（measure の full）が見るいちばん長いターン数
LONG_SCENARIOS = ("plan", "none", "small")
LONG_SEEDS = 2  # 場面ごとの種の数
RANDOM_TURNS = 300
RANDOM_SEEDS = 30
RANDOM_ISLANDS = 10  # でたらめな命令を出す島の数（残りは標準の計画）
RANDOM_PENDING = 4  # 実行を待つ命令がこれだけあれば、そのターンは足さない（行政力が足りない国）
RANDOM_COUNTS = (0, 0, 1, 1, 1, 2, 3)  # 1ターンに足す命令の数（ここから選ぶ）
TRACEBACK_LINES = 12
SAVE_EVERY = 100  # 途中の結果を書くターンの間隔（時間で止めた組もそこまでの分を見る）
COLLAPSE_STEPS = (1, 2, 5, 10, 20)  # 崩れた島の数の段（増えたら夜の点検の「新」）
CUT = "時間切れで止めた"  # 時間で止めた組の記録の最後の行

# 名前の命令に入れる文字（長すぎるものは maxLength で切る）
NAMES = ("新しい名前", "A", "ｱｲｳｴｵ", "名前 空白", "島🙂", "<b>名</b>", "x" * 40, "　")


# --- でたらめな命令 ----------------------------------------------------------------


class OrderMaker:
    """命令の形（orders.yaml の payload の JSON Schema）を見て、でたらめな中身を作る。

    マスの x・y はなるべく島の施設のあるマス、相手の島はほかの島、財・施設・作り方・体制などは定義の
    名前から選びます。数はときどき下限・上限・とても大きい値にします。作った命令は check_order を
    通ったものだけを返します。
    """

    def __init__(self, game: Game, world: WorldState, rng: random.Random) -> None:
        self.game = game
        self.world = world
        self.rng = rng
        defs = game.defs
        self.kinds = sorted(k for k in defs.orders if k in game.registry.orders)
        self.schemas = {k: thaw(defs.orders[k]["payload"]) for k in self.kinds}
        self.goods = list(defs.goods)
        self.facilities = list(defs.facilities)
        self.recipes = sorted({r for spec in defs.facilities.values() for r in spec["recipes"]})
        self.axes = defs.regime_axes()
        self.industries = defs.industries()
        self.infrastructure = list(defs.infrastructure)
        self.bond_terms = list(defs.bond_terms())
        self.budget_items = list(game.config.get("fiscal.budget_items"))
        self.kind = ""
        self.spot: tuple[int, int, str | None] = (0, 0, None)

    def orders(self, island: IslandState, count: int) -> tuple[list[tuple[str, dict]], list]:
        """確かめを通った命令を count 個まで。2つ目は確かめで例外が出たもの（種類, 中身, 例外）。"""
        out: list[tuple[str, dict]] = []
        errors = []
        for _ in range(count * 20):
            if len(out) >= count:
                break
            kind = self.rng.choice(self.kinds)
            payload = self.payload(island, kind)
            try:
                check = check_order(self.game.defs, self.game.registry, island, kind, payload)
            except Exception as e:  # 画面なら 500。記録して続ける
                errors.append((kind, payload, e))
                continue
            if check.ok:
                out.append((kind, payload))
        return out, errors

    def payload(self, island: IslandState, kind: str) -> Any:
        self.kind = kind
        self.spot = self._spot(island)
        return self._value("", self.schemas[kind], island)

    def _spot(self, island: IslandState) -> tuple[int, int, str | None]:
        if self.kind == "build" and self.rng.random() < 0.7:
            # 建てる命令は、なるべく空いたマスに（施設のあるマスは埋まっていて通らない）
            occupied = island.facilities_by_tile()
            empty = [(t.x, t.y) for t in island.tiles if (t.x, t.y) not in occupied]
            if empty:
                x, y = self.rng.choice(empty)
                return x, y, None
        if island.facilities and self.rng.random() < 0.7:
            f = self.rng.choice(island.facilities)
            return f.x, f.y, f.kind
        return self.rng.randrange(island.size), self.rng.randrange(island.size), None

    def _value(self, name: str, schema: dict[str, Any], island: IslandState) -> Any:
        if "const" in schema:
            return schema["const"]
        if "enum" in schema:
            return self.rng.choice(schema["enum"])
        for key in ("anyOf", "oneOf"):
            if key in schema:
                return self._value(name, self.rng.choice(schema[key]), island)
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = self.rng.choice(kind)
        if kind == "object":
            return self._object(name, schema, island)
        if kind == "array":
            return self._array(name, schema, island)
        if kind == "boolean":
            return self.rng.random() < 0.5
        if kind == "integer":
            return self._integer(name, schema)
        if kind == "number":
            return self._number(schema)
        if kind == "string":
            return self._string(name, schema)
        return None

    def _object(self, name: str, schema: dict[str, Any], island: IslandState) -> dict[str, Any]:
        props = schema.get("properties", {})
        required = set(schema.get("required", ()))
        out = {
            key: self._value(key, sub, island)
            for key, sub in props.items()
            if key in required or self.rng.random() < 0.5
        }
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):  # 予算の項目・国債の年限
            keys = self.bond_terms if name == "terms" else self.budget_items
            for key in self.rng.sample(keys, self.rng.randint(1, len(keys))):
                out[key] = self._value(key, extra, island)
        optional = [key for key in props if key not in out]
        self.rng.shuffle(optional)
        while len(out) < schema.get("minProperties", 0) and optional:
            key = optional.pop()
            out[key] = self._value(key, props[key], island)
        return out

    def _array(self, name: str, schema: dict[str, Any], island: IslandState) -> list:
        if "prefixItems" in schema:
            return [self._value(name, sub, island) for sub in schema["prefixItems"]]
        low = schema.get("minItems", 0)
        high = max(low, min(schema.get("maxItems", 6), 8))
        n = self.rng.randint(low, high)
        if name == "tiles":
            return self._tiles(island, max(1, n))
        items = [self._value(name, schema.get("items", {}), island) for _ in range(n)]
        if schema.get("uniqueItems"):
            items = list(dict.fromkeys(items))
        return items

    def _tiles(self, island: IslandState, n: int) -> list[list[int]]:
        """施設のあるマスなどから始めて、上下左右に1マスずつ進んだ道筋（送電線や道路の形）。"""
        x, y, _ = self.spot
        tiles = [[x, y]]
        for _ in range(n - 1):
            dx, dy = self.rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
            x = min(island.size - 1, max(0, x + dx))
            y = min(island.size - 1, max(0, y + dy))
            tiles.append([x, y])
        return tiles

    def _integer(self, name: str, schema: dict[str, Any]) -> int:
        if name in ("x", "y"):
            return self.spot[0] if name == "x" else self.spot[1]
        if name in (
            "partner",
            "issuer",
            "to",
            "approve",
            "currency",
            "target",
            "payer",
            "seller",
            "pegger",
            "host",
        ):
            return self.rng.choice(self.world.island_ids())
        if name == "bloc":
            ids = [b["id"] for b in trade_blocs.blocs(self.world)]
            return self.rng.choice(ids + [1, 2, 3])
        if name == "treaty":
            ids = [t["id"] for t in diplomacy_treaties.treaties(self.world)]
            return self.rng.choice(ids + [1, 2, 3])
        if name == "contract":
            ids = [c["id"] for c in trade_contracts.contracts(self.world)]
            return self.rng.choice(ids + [1, 2, 3])
        low, high = schema.get("minimum"), schema.get("maximum")
        if low is not None and high is not None and high - low <= 1000:
            return self.rng.randint(low, high)
        return int(self._amount(low, high))

    def _number(self, schema: dict[str, Any]) -> float:
        low = schema.get("minimum", schema.get("exclusiveMinimum"))
        high = schema.get("maximum")
        if high is not None:
            pick = self.rng.random()
            if pick < 0.2 and "minimum" in schema:
                return float(low)
            if pick < 0.4:
                return float(high)
            return round(self.rng.uniform(low or 0, high), 3)
        value = self._amount(low, None)
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            value = schema["exclusiveMinimum"] + 1
        return round(value, 3)

    def _amount(self, low: float | None, high: float | None) -> float:
        """量やお金: 0・下限・ふつうの大きさ・とても大きい値。下限がなければ負もある。"""
        pick = self.rng.random()
        if pick < 0.1:
            value = 0.0 if low is None or low <= 0 else float(low)
        else:
            value = 10 ** self.rng.uniform(0, 9 if pick < 0.2 else 5)
            if low is None and self.rng.random() < 0.3:
                value = -value
        if low is not None:
            value = max(value, low)
        if high is not None:
            value = min(value, high)
        return value

    def _string(self, name: str, schema: dict[str, Any]) -> str:
        if name in ("good", "goods", "held"):
            return self.rng.choice(self.goods)
        if name == "facility":
            return self.rng.choice(self.facilities)
        if name == "recipe":
            kind = self.spot[2]
            if kind is not None and self.rng.random() < 0.7:
                return self.rng.choice(list(self.game.defs.facilities[kind]["recipes"]))
            return self.rng.choice(self.recipes)
        if name == "kind" and self.kind == "build_infrastructure":
            return self.rng.choice(self.infrastructure)
        if name in self.axes:
            return self.rng.choice(self.axes[name])
        if name == "industries":
            return self.rng.choice(self.industries)
        text = self.rng.choice(NAMES)
        text = text[: schema.get("maxLength", len(text))]
        return text if len(text) >= schema.get("minLength", 0) else "名"


# --- 1つの組を回す ------------------------------------------------------------------


def run_one(
    kind: str,
    scenario_name: str,
    seed: int,
    turns: int,
    game: Game | None = None,
    save: Callable[[dict], None] | None = None,
) -> dict:
    """1つの組を回して結果を返す。ターンの処理が例外で止まったら、そこで終える。

    save があれば、SAVE_EVERY ターンごとに途中の結果（unfinished が真）を渡す。
    """
    game = game or load_game(None)
    scenario = measure.SCENARIOS[scenario_name]
    started = time.perf_counter()
    world = generate_world(seed, scenario.islands, game.config, game.defs)
    rng = random.Random(seed)
    chosen: list[int] = []
    maker = None
    if kind == "random":
        ids = world.island_ids()
        chosen = sorted(rng.sample(ids, min(RANDOM_ISLANDS, len(ids))))
        maker = OrderMaker(game, world, rng)
    recorder = Recorder(world, game.defs, game.config) if kind == "long" else None
    queued: Counter[str] = Counter()
    ours: set[tuple[int, int]] = set()  # でたらめに並べた命令（島, 命令の番号）
    accept_errors: list[dict] = []
    stopped = None

    def result() -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": kind,
            "scenario": scenario_name,
            "seed": seed,
            "islands": scenario.islands,
            "turns": turns,
            "turns_done": world.turn,
            "seconds": round(time.perf_counter() - started, 1),
            "stopped": stopped,
        }
        if kind == "random":
            statuses = Counter(
                o.status for i in chosen for o in world.islands[i].orders if (i, o.id) in ours
            )
            out["random_islands"] = chosen
            out["orders"] = {
                "queued": sum(queued.values()),
                "done": statuses.get(ORDER_DONE, 0),
                "failed": statuses.get(ORDER_FAILED, 0),
                "kinds": dict(sorted(queued.items())),
            }
            out["accept_errors"] = accept_errors[:20]
        if recorder is not None:
            out["windows"] = windows(recorder, scenario.kinds, world.turn)
            out["late"] = late_kinds(out["windows"])
            out["collapse"] = measure.collapse(scenario_name, seed, recorder)
        return out

    for _ in range(turns):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        this_turn = []
        for island_id in chosen:
            island = world.islands[island_id]
            if sum(o.status == ORDER_PENDING for o in island.orders) >= RANDOM_PENDING:
                continue
            orders, errors = maker.orders(island, rng.choice(RANDOM_COUNTS))
            queue_orders(island, orders)
            queued.update(k for k, _ in orders)
            added = island.orders[len(island.orders) - len(orders) :]
            ours.update((island_id, o.id) for o in added)
            this_turn += [{"island": island_id, "kind": k, "payload": p} for k, p in orders]
            for k, p, e in errors:
                accept_errors.append(
                    {"turn": world.turn + 1, "island": island_id, "kind": k, "payload": p}
                    | _error(e)
                )
        try:
            game.engine.run_turn(world)
        except Exception as e:  # ターンが進まなくなる不具合。世界は中途半端なのでここで終える
            stopped = {"turn": world.turn + 1, "orders": this_turn} | _error(e)
            break
        if recorder is not None:
            recorder.record(world)
        if world.turn % SAVE_EVERY == 0 and world.turn < turns:
            print(f"ターン {world.turn}（{time.perf_counter() - started:.0f} 秒）", flush=True)
            if save is not None:
                save(result() | {"unfinished": True})
    return result()


def windows(recorder: Recorder, kinds: Iterable[str] | None, last: int) -> list[dict[str, Any]]:
    """目安から外れた島の数を、測りの範囲とその後の期間（残りを2つに分けた）で数える。"""
    keep = set(kinds or KINDS)
    edges = [START, MEASURED_TURNS]
    if last > MEASURED_TURNS:
        edges += [MEASURED_TURNS + (last - MEASURED_TURNS) // 2, last]
    out = []
    for start, end in zip(edges, edges[1:], strict=False):
        if end <= start or start >= last:
            continue
        counts = recorder.counts(start, min(end, last))
        kept = {k: counts[k] for k in KINDS if k in keep}
        out.append({"start": start, "end": end, "counts": kept})
    return out


def late_kinds(found: list[dict[str, Any]]) -> list[str]:
    """測りの範囲では出ず、その後の期間にだけ出た目安の種類。"""
    if len(found) < 2:
        return []
    first = found[0]["counts"]
    return [k for k in first if not first[k] and any(w["counts"][k] for w in found[1:])]


def _error(e: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(e.__traceback__)
    ours = [f for f in frames if "hakoniwa_core" in f.filename] or frames
    where = ""
    if ours:
        f = ours[-1]
        path = f.filename.replace("\\", "/")
        path = path[path.find("hakoniwa_core") :] if "hakoniwa_core" in path else path
        where = f"{path}:{f.lineno} {f.name}"
    lines = traceback.format_exception(type(e), e, e.__traceback__)
    text = "".join(lines).splitlines()
    return {
        "error": f"{type(e).__name__}: {e}"[:500],
        "where": where,
        "signature": f"{type(e).__name__} @ {where}",
        "traceback": text[-TRACEBACK_LINES:],
    }


# --- まとめて回す ------------------------------------------------------------------


def plan(
    seed_base: int, long_turns: int, random_turns: int, random_seeds: int
) -> list[tuple[str, str, int, int]]:
    """一晩に回す組（種類, 場面, 種, ターン数）。長いものから始める。"""
    out = [
        ("long", scenario, seed_base * 10 + i, long_turns)
        for scenario in LONG_SCENARIOS
        for i in range(1, LONG_SEEDS + 1)
    ]
    out.sort(key=lambda r: -measure.SCENARIOS[r[1]].islands)
    out += [("random", "plan", seed_base * 100 + i, random_turns) for i in range(random_seeds)]
    return out


def run_name(kind: str, scenario: str, seed: int, turns: int) -> str:
    return f"{kind}-{scenario}-s{seed}-t{turns}"


def hours_until(clock: str, now: datetime.datetime | None = None) -> float:
    """次に時計が clock（"06:30"）になるまでの時間。"""
    now = now or datetime.datetime.now()
    hour, minute = (int(x) for x in clock.split(":"))
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if end <= now:
        end += datetime.timedelta(days=1)
    return (end - now).total_seconds() / 3600


def batch(
    runs: list[tuple[str, str, int, int]], out_dir: Path, jobs: int, hours: float | None = None
) -> None:
    """組を jobs 本ずつ別の処理で回す。結果は out_dir/<名前>.json。

    hours がたったら、走っている組を止め（記録の最後に CUT を書く）、残りは始めない。始めなかった
    組は out_dir/not_started.json に書く。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + hours * 3600 if hours is not None else None
    waiting = list(runs)
    running: list[tuple[str, subprocess.Popen, IO[str]]] = []
    while waiting or running:
        if deadline is not None and time.monotonic() >= deadline:
            for name, process, log in running:
                process.kill()
                process.wait()
                log.write(f"\n{CUT}\n")
                log.close()
                print(f"時間切れで止めた: {name}", flush=True)
            names = [run_name(*r) for r in waiting]
            text = json.dumps(names, ensure_ascii=False, indent=1)
            (out_dir / "not_started.json").write_text(text, encoding="utf-8")
            return
        while waiting and len(running) < jobs:
            kind, scenario, seed, turns = waiting.pop(0)
            name = run_name(kind, scenario, seed, turns)
            command = [
                sys.executable,
                *("-m", "hakoniwa_core.nightly", "one", "--kind", kind),
                *("--scenario", scenario, "--seed", str(seed), "--turns", str(turns)),
                *("--out", str(out_dir / f"{name}.json")),
            ]
            log = open(out_dir / f"{name}.log", "w", encoding="utf-8")  # 処理が終わったら閉じる
            running.append((name, subprocess.Popen(command, stdout=log, stderr=log), log))
            print(f"始めた: {name}", flush=True)
        time.sleep(1)
        for item in list(running):
            name, process, log = item
            if process.poll() is None:
                continue
            running.remove(item)
            log.close()
            print(f"終わった: {name}", flush=True)


def summarize(out_dir: Path, known_path: Path | None, label: str = "") -> dict[str, Any]:
    """結果を読み、見つけたものを並べる。

    known_path があれば、そこにない（新しい）ものに印を付け、一覧に足す。
    """
    findings = []
    runs = 0
    times = []  # 終わった組の (秒, 名前)
    for log in sorted(out_dir.glob("*.log")):
        name = log.stem
        if not name.startswith(("long-", "random-")):  # 夜の予定や pip の記録
            continue
        runs += 1
        path = out_dir / f"{name}.json"
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        cut = CUT in tail
        if not path.exists() and not cut:  # 処理そのものが落ちた（メモリが足りない など）
            signature = f"crash @ {name}"
            findings.append({"type": "crash", "signature": signature, "run": name, "log": tail})
            continue
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if cut or data.get("unfinished"):
            # 時間内に終わらなかった（止めたところまでの結果は下で見る）
            kind, scenario = name.split("-")[:2]
            findings.append(
                {"type": "timeout", "signature": f"timeout {kind}-{scenario}", "run": name}
                | {"turns_done": data.get("turns_done", 0), "seconds": data.get("seconds")}
            )
            if not data:
                continue
        elif data.get("seconds") is not None:
            times.append((data["seconds"], name))
        command = (
            f"python -m hakoniwa_core.nightly one --kind {data['kind']} --scenario "
            f"{data['scenario']} --seed {data['seed']} --turns {data['turns']} --out {name}.json"
        )
        if data.get("stopped"):
            s = data["stopped"]
            findings.append(
                {"type": "stop", "signature": s["signature"], "run": name, "turn": s["turn"]}
                | {"error": s["error"], "reproduce": command}
            )
        for a in data.get("accept_errors", []):
            findings.append(
                {"type": "accept", "signature": a["signature"], "run": name, "turn": a["turn"]}
                | {"error": a["error"], "order": {"kind": a["kind"], "payload": a["payload"]}}
            )
        found = data.get("collapse", {}).get("found", [])
        for kind in dict.fromkeys(c["kind"] for c in found):
            # 島の数の段（1・2・5・10・20 島以上）を名前に入れ、増えたら新しいものとして印を付ける
            these = [c for c in found if c["kind"] == kind]
            step = max(n for n in COLLAPSE_STEPS if len(these) >= n)
            first = min(these, key=lambda c: c["first"])
            findings.append(
                {"type": "collapse", "signature": f"collapse {data['scenario']} {kind} {step}+"}
                | {"run": name, "turn": first["first"], "islands": len(these)}
                | {"line": first["line"], "snapshot": first["snapshot"]}
            )
        for kind in data.get("late", []):
            counts = [w["counts"][kind] for w in data["windows"]]
            findings.append(
                {"type": "late", "signature": f"late {data['scenario']} {kind}", "run": name}
                | {"counts": counts, "windows": [[w["start"], w["end"]] for w in data["windows"]]}
            )
    known: dict[str, str] = {}
    if known_path is not None and known_path.exists():
        known = json.loads(known_path.read_text(encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    for f in findings:
        f["new"] = f["signature"] not in known
    if known_path is not None:
        for f in findings:
            known.setdefault(f["signature"], today)
        known_path.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")
    not_started_path = out_dir / "not_started.json"
    not_started = []
    if not_started_path.exists():
        not_started = json.loads(not_started_path.read_text(encoding="utf-8"))
    slowest = max(times, default=None)
    summary = {
        "label": label,
        "date": today,
        "runs": runs,
        "findings": findings,
        "new": sum(f["new"] for f in findings),
        "slowest": {"run": slowest[1], "seconds": slowest[0]} if slowest else None,
        "not_started": not_started,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(show(summary), encoding="utf-8")
    return summary


TYPE_TITLES = {
    "stop": "ターンが止まった",
    "accept": "命令の受け付けで例外",
    "crash": "処理が落ちた",
    "late": "40 年より後に出た崩れ",
    "collapse": "崩れ（1ターン目から）",
    "timeout": "時間内に終わらなかった",
}


def show(summary: dict[str, Any]) -> str:
    lines = [
        f"# 夜の点検 {summary['date']} {summary['label']}".rstrip(),
        "",
        f"回した組 {summary['runs']}、見つけたもの {len(summary['findings'])}"
        f"（新しいもの {summary['new']}）",
    ]
    if summary.get("slowest"):
        slow = summary["slowest"]
        lines.append(f"いちばん長くかかった組: {slow['run']}（{slow['seconds'] / 60:.0f} 分）")
    if summary.get("not_started"):
        lines.append(f"時間切れで始めなかった組: {len(summary['not_started'])}")
    seen = set()
    for f in summary["findings"]:
        if f["signature"] in seen:  # 同じものは最初の1つだけ
            continue
        seen.add(f["signature"])
        mark = "【新】" if f["new"] else ""
        lines += ["", f"## {mark}{TYPE_TITLES[f['type']]}: {f['signature']}", f"- 組: {f['run']}"]
        if "turn" in f:
            lines.append(f"- ターン: {f['turn']}")
        if "turns_done" in f:
            took = f" {f['seconds'] / 60:.0f} 分で" if f.get("seconds") else ""
            lines.append(f"- 止めたとき:{took} {f['turns_done']} ターンまで")
        if "error" in f:
            lines.append(f"- 例外: {f['error']}")
        if "order" in f:
            lines.append(f"- 命令: {json.dumps(f['order'], ensure_ascii=False)}")
        if "counts" in f:
            pairs = zip(f["windows"], f["counts"], strict=True)
            spans = "、".join(f"{a}〜{b} ターン {c} 島" for (a, b), c in pairs)
            lines.append(f"- 目安から外れた島の数: {spans}")
        if "line" in f:
            lines.append(f"- 島の数: {f['islands']}。いちばん早いもの: {f['line']}")
        if "snapshot" in f:
            lines.append(f"- 調べる写しを作る命令: `{f['snapshot']}`")
        if "log" in f:
            lines += ["- 記録の最後:", *(f"    {line}" for line in f["log"])]
        if "reproduce" in f:
            lines.append(f"- 同じことを起こす命令: `{f['reproduce']}`")
    if not summary["findings"]:
        lines += ["", "壊れた所は見つかりませんでした。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="一晩の組をまとめて回し、まとめを書く")
    run.add_argument("--out-dir", required=True)
    run.add_argument("--jobs", type=int, default=min(14, os.cpu_count() or 1))
    run.add_argument("--seed-base", type=int, default=int(time.strftime("%Y%m%d")))
    run.add_argument("--long-turns", type=int, default=LONG_TURNS)
    run.add_argument("--random-turns", type=int, default=RANDOM_TURNS)
    run.add_argument("--random-seeds", type=int, default=RANDOM_SEEDS)
    run.add_argument("--known", help="前の夜までに見つけたものの一覧（known.json）")
    run.add_argument("--label", default="", help="まとめの見出しに付ける（版など）")
    run.add_argument("--until", default="06:30", help="この時刻に残りの組を止めてまとめを書く")
    run.add_argument("--hours", type=float, help="時刻の代わりに、この時間で止める")
    one = sub.add_parser("one", help="1つの組を回す")
    one.add_argument("--kind", choices=("long", "random"), required=True)
    one.add_argument("--scenario", choices=sorted(measure.SCENARIOS), default="plan")
    one.add_argument("--seed", type=int, required=True)
    one.add_argument("--turns", type=int, required=True)
    one.add_argument("--out", required=True)
    again = sub.add_parser("summary", help="結果からまとめを書き直す")
    again.add_argument("--out-dir", required=True)
    again.add_argument("--known")
    again.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.command == "one":

        def save(result: dict) -> None:
            # 書いている途中で止められても壊れないよう、別の名前に書いてから置き換える
            out = Path(args.out)
            temp = out.with_name(out.name + ".tmp")
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            temp.replace(out)

        result = run_one(args.kind, args.scenario, args.seed, args.turns, save=save)
        save(result)
        return 1 if result["stopped"] else 0
    out_dir = Path(args.out_dir)
    known = Path(args.known) if args.known else None
    if args.command == "run":
        runs = plan(args.seed_base, args.long_turns, args.random_turns, args.random_seeds)
        hours = args.hours if args.hours is not None else hours_until(args.until)
        batch(runs, out_dir, args.jobs, hours)
    summary = summarize(out_dir, known, args.label)
    print(show(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
