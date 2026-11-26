"""H-7 の次の一手: 500 ターン目の写しを往復させた世界と、通しの世界の
world_to_dict を鍵ごとに比べ、最初に違う鍵を出す（掲示板 #5811・#5876・
#5882・#5888）。

対照（#5876）で「写しなし full 同士は別機械でも 200/200 一致」と分かり、
#5546 の割れは写し・復元の側に絞られた。残る問いは「写しの往復で、どの
状態が・いつ落ちるか」。この包みは同じ 500 ターン目から 2 本に分け、

  straight: 写しを取らずにそのまま続ける
  resume:   500 ターン目で測りの写し（hakoniwa_core.simulate の
            snapshot_dict）を JSON で往復させ、world_from_snapshot_dict で
            作り直してから続ける

両方とも 500 ターン目（resume は復元直後）と、その後の数ターン（既定は
10 ターン、501〜510）の終わりごとに world_to_dict を取り、型と鍵の並びを
保つ形に写して置く。report はターン順に比べ、最初に割れたターンの、
最初に違う鍵（道筋・両側の値・型）を出す。あわせて次番号カウンタ類
（貿易・外交・軍事の seq、島ごとの bond_seq / loan_seq / firm_seq /
facility_seq、ext の中にある seq / *_seq）の値を両側で残し、差がなくても
値を 1 行で報告する（#5882・#5888 の定型）。

当たりの順（#5552）: (a) 写しに入らない次番号カウンタ → (b) 鍵の型と順
→ (c) 読み込みのときに作り直す覚え → (d) 予約・待ちの類の型の変化。

    py -3.12 -X utf8 h7_dict501.py run --mode straight --scenario open \
        --seed 1 --turns 500 --extra 10 --snap-out snapshot-at-500.json \
        --out-dir dumps-straight
    py -3.12 -X utf8 h7_dict501.py run --mode resume --scenario open \
        --snapshot snapshot-at-500.json --extra 10 --out-dir dumps-resume
    py -3.12 -X utf8 h7_dict501.py report --straight dumps-straight \
        --resume dumps-resume --out report.md

煙確認（短い確認。配置時にまずこれで確かめる）:

    py -3.12 -X utf8 h7_dict501.py run --mode straight --scenario small \
        --seed 1 --turns 5 --extra 3 --snap-out smoke-snap.json \
        --out-dir smoke-straight
    py -3.12 -X utf8 h7_dict501.py run --mode resume --scenario small \
        --snapshot smoke-snap.json --extra 3 --out-dir smoke-resume
    py -3.12 -X utf8 h7_dict501.py report --straight smoke-straight \
        --resume smoke-resume --out smoke-report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# --- 型と並びを保つ写し -------------------------------------------------------
#
# world_to_dict の中身を、そのまま JSON に書くと tuple が list に、数字の
# 鍵が文字になり、鍵の並びも比べられなくなる。(b)・(d) を拾うため、型の
# 印（"!"）を付けて写す。集合は毎回同じ順に並べる。


def encode_key(key: Any) -> Any:
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, (bool, int, float)):
        return {"!": "key", "t": type(key).__name__, "v": key}
    return {"!": "key", "t": type(key).__name__, "v": encode(key)}


def encode(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            "!": "dict",
            "v": [[encode_key(k), encode(v)] for k, v in value.items()],
        }
    if isinstance(value, tuple):
        return {"!": "tuple", "v": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        items = [encode(v) for v in value]
        items.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        return {"!": "set", "v": items}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, float):
        if value != value:
            return {"!": "float", "v": "nan"}
        if value in (float("inf"), float("-inf")):
            return {"!": "float", "v": "inf" if value > 0 else "-inf"}
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    # それ以外の型（写しに残らないはずの物）は、型名と repr を残して気付けるようにする
    return {"!": "other", "t": type(value).__name__, "v": repr(value)}


def _enc_kind(value: Any) -> str:
    if isinstance(value, dict) and "!" in value:
        return str(value["!"])
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _key_text(enc_key: Any) -> str:
    if isinstance(enc_key, str):
        return enc_key
    if isinstance(enc_key, dict) and enc_key.get("!") == "key":
        return f"{enc_key['t']}:{enc_key['v']}"
    return json.dumps(enc_key, ensure_ascii=False, sort_keys=True)


def _key_bare(enc_key: Any) -> str:
    """鍵の型を落とした文字の形（int の 1 と str の "1" を同じと見るため）。"""
    if isinstance(enc_key, str):
        return enc_key
    if isinstance(enc_key, dict) and enc_key.get("!") == "key":
        return str(enc_key["v"])
    return _key_text(enc_key)


def _short(value: Any, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "…"



def _join(path: list[str]) -> str:
    out = ""
    for part in path:
        if part.startswith("["):
            out += part
        elif out:
            out += "." + part
        else:
            out = part
    return out


# --- 最初の差を探す -----------------------------------------------------------
#
# 戻り値: (道筋の文字列, 種類, 通しの値, 戻しの値) または None。
# 種類: value（値の差）/ type（型の差）/ key-missing（鍵の有無）/
#       key-order（鍵の並びだけ）/ length（並びの長さ）。
# 鍵の並びだけの差より、値・型・有無の差を先に拾う（並びの差は、同じ
# dict の中で値の差が無かったときに返す）。


def first_diff(a: Any, b: Any, path: list[str]) -> tuple[str, str, Any, Any] | None:
    kind_a, kind_b = _enc_kind(a), _enc_kind(b)
    if kind_a == "dict" and kind_b == "dict":
        pairs_a = a["v"]
        pairs_b = b["v"]
        keys_a = [_key_text(k) for k, _ in pairs_a]
        keys_b = [_key_text(k) for k, _ in pairs_b]
        if set(keys_a) != set(keys_b):
            bare_b = {_key_bare(k) for k, _ in pairs_b}
            bare_a = {_key_bare(k) for k, _ in pairs_a}
            for k, va in pairs_a:
                if _key_text(k) not in keys_b:
                    if _key_bare(k) in bare_b:
                        # 型だけが違う鍵（JSON 往復で int の鍵が str になる等）= (b)
                        return (_join(path + [_key_text(k)]), "type", va, None)
                    return (_join(path + [_key_text(k)]), "key-missing", va, None)
            for k, vb in pairs_b:
                if _key_text(k) not in keys_a:
                    if _key_bare(k) in bare_a:
                        return (_join(path + [_key_text(k)]), "type", None, vb)
                    return (_join(path + [_key_text(k)]), "key-missing", None, vb)
        by_key_b = {_key_text(k): v for k, v in pairs_b}
        for k, va in pairs_a:
            hit = first_diff(va, by_key_b[_key_text(k)], path + [_key_text(k)])
            if hit is not None:
                return hit
        if keys_a != keys_b:
            return (_join(path), "key-order", keys_a, keys_b)
        return None
    if kind_a in ("tuple", "set") and kind_a == kind_b:
        return first_diff(a["v"], b["v"], path)
    if isinstance(a, list) and isinstance(b, list):
        for i, (va, vb) in enumerate(zip(a, b)):
            hit = first_diff(va, vb, path + [f"[{i}]"])
            if hit is not None:
                return hit
        if len(a) != len(b):
            return (_join(path), "length", f"{len(a)} 件", f"{len(b)} 件")
        return None
    if kind_a != kind_b or type(a) is not type(b):
        return (_join(path), "type", f"{kind_a}:{_short(a)}", f"{kind_b}:{_short(b)}")
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return None
    if a != b:
        return (_join(path), "value", a, b)
    return None


# --- 次番号カウンタ -----------------------------------------------------------


def _scan_seq(data: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """dict の中を辿り、seq / *_seq という名前の数値を拾う（深さ 3 まで）。"""
    found: dict[str, Any] = {}
    if not isinstance(data, dict) or depth > 3:
        return found
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        name = prefix + k
        if k == "seq" or k.endswith("_seq"):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                found[name] = v
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                        found[f"{name}.{kk}"] = vv
        elif isinstance(v, dict):
            found.update(_scan_seq(v, name + ".", depth + 1))
    return found


def collect_counters(world) -> dict[str, Any]:
    """次番号カウンタ類の値を、世界の外（ext の世界段）と島ごとに拾う。

    ext は setdefault で作らず、読むだけ（比べる対象そのものを変えないため）。
    """
    out: dict[str, Any] = {"world": _scan_seq(world.ext), "islands": {}}
    for iid, island in sorted(world.islands.items()):
        row: dict[str, Any] = {}
        for name in ("bond_seq", "loan_seq", "firm_seq"):
            row[name] = getattr(island.economy, name, None)
        row["facility_seq"] = island.ext.get("facility_seq")
        row.update(_scan_seq(island.ext))
        out["islands"][str(iid)] = row
    return out


def counter_diffs(ca: dict, cb: dict) -> list[tuple[str, str, Any, Any]]:
    """カウンタの差を (場所, 欄, 通し, 戻し) の並びで返す。"""
    diffs: list[tuple[str, str, Any, Any]] = []
    for name in sorted(set(ca["world"]) | set(cb["world"])):
        va, vb = ca["world"].get(name), cb["world"].get(name)
        if va != vb:
            diffs.append(("世界", name, va, vb))
    island_ids = sorted(set(ca["islands"]) | set(cb["islands"]), key=lambda x: int(x))
    for iid in island_ids:
        ra = ca["islands"].get(iid, {})
        rb = cb["islands"].get(iid, {})
        for name in sorted(set(ra) | set(rb)):
            va, vb = ra.get(name), rb.get(name)
            if va != vb:
                diffs.append((f"島 {iid}", name, va, vb))
    return diffs


def counter_line(ca: dict, cb: dict, when: str) -> str:
    """次番号カウンタ値の 1 行（#5882・#5888: 差がなくても値を残す）。"""
    parts: list[str] = []
    for name in sorted(set(ca["world"]) | set(cb["world"])):
        va, vb = ca["world"].get(name), cb["world"].get(name)
        parts.append(f"{name} {va}" if va == vb else f"{name} 通し {va} / 戻し {vb}")
    diffs = counter_diffs(ca, cb)
    island_diffs = [d for d in diffs if d[0] != "世界"]
    sums: dict[str, tuple[Any, Any]] = {}
    fields = ("bond_seq", "loan_seq", "firm_seq", "facility_seq")
    for name in fields:
        sa = sum((r.get(name) or 0) for r in ca["islands"].values())
        sb = sum((r.get(name) or 0) for r in cb["islands"].values())
        sums[name] = (sa, sb)
    sum_text = "・".join(
        f"{name} 合計 {sa}" if sa == sb else f"{name} 合計 通し {sa} / 戻し {sb}"
        for name, (sa, sb) in sums.items()
    )
    if island_diffs:
        island_text = f"島別の欄は差のある島 {len({d[0] for d in island_diffs})} 島（詳細は下の表）"
    else:
        island_text = "島別の欄は全島一致"
    return (
        f"次番号カウンタ（{when}）: 世界段 {'; '.join(parts) if parts else 'なし'}"
        f" / 島別 {sum_text}、{island_text}"
    )


# --- 回す ---------------------------------------------------------------------


def _dump_turn(out_dir: Path, world) -> None:
    from hakoniwa_core.state.serialize import world_to_dict

    data = encode(world_to_dict(world))
    (out_dir / f"turn-{world.turn}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _finish(out_dir: Path, counters: dict[str, Any], meta: dict[str, Any]) -> None:
    (out_dir / "counters.json").write_text(
        json.dumps(counters, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.simulate import (
        snapshot_dict,
        world_digest,
        world_from_snapshot_dict,
    )
    from hakoniwa_core.state.generate import generate_world

    game = load_game(None)
    defs, config = game.defs, game.config
    scenario = measure.SCENARIOS[args.scenario]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counters: dict[str, Any] = {}

    def advance(world, turns: int):
        for _ in range(turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            _dump_turn(out_dir, world)
            counters[str(world.turn)] = collect_counters(world)
        return world

    if args.mode == "straight":
        world = generate_world(args.seed, scenario.islands, config, defs)
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
        _dump_turn(out_dir, world)
        counters[str(world.turn)] = collect_counters(world)
        digest = world_digest(world)
        # 写しは、この時点の状態を JSON で固定してから先へ進む
        snap = snapshot_dict(world)
        Path(args.snap_out).write_text(
            json.dumps(snap, ensure_ascii=False), encoding="utf-8"
        )
        advance(world, args.extra)
        meta = {
            "mode": "straight",
            "scenario": args.scenario,
            "seed": args.seed,
            "snapshot_at": args.turns,
            "digest_at_snapshot": digest,
        }
    else:
        data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        world, _ = world_from_snapshot_dict(data)
        digest = world_digest(world)
        _dump_turn(out_dir, world)
        counters[str(world.turn)] = collect_counters(world)
        advance(world, args.extra)
        meta = {
            "mode": "resume",
            "scenario": args.scenario,
            "seed": world.seed if hasattr(world, "seed") else None,
            "snapshot_at": world.turn - args.extra,
            "digest_after_restore": digest,
        }
    meta["turns"] = sorted(int(t) for t in counters)
    _finish(out_dir, counters, meta)
    return 0


# --- 報告 ---------------------------------------------------------------------


def _nature_line(diff: tuple[str, str, Any, Any] | None) -> str:
    if diff is None:
        return (
            "差の性質: 差なし（比べたターンの範囲では、ゲーム値にも、"
            "鍵の順序・型にも差が出なかった）"
        )
    path, kind, _va, _vb = diff
    if kind == "value":
        if ".log" in path or path.endswith("log"):
            base = "ゲーム値そのものではなく、表示・記録（log）の差"
        else:
            base = "ゲーム値の差"
    elif kind == "key-order":
        base = "外部表示・キャッシュ・順序の側（鍵の並びだけの差で、値は一致）"
    elif kind == "type":
        base = "順序・型の側（ゲーム値そのものの差ではなく、型の差）"
    elif kind == "key-missing":
        base = "ゲーム値の差の側（鍵そのものが片側にだけある＝状態の欠落）"
    else:  # length
        base = "ゲーム値の差の側（並びの長さの差）"
    return f"差の性質: {base}（最初の差鍵 {path}、種類 {kind}）"


def report(args: argparse.Namespace) -> int:
    straight_dir, resume_dir = Path(args.straight), Path(args.resume)
    meta_s = json.loads((straight_dir / "meta.json").read_text(encoding="utf-8"))
    meta_r = json.loads((resume_dir / "meta.json").read_text(encoding="utf-8"))
    counters_s = json.loads((straight_dir / "counters.json").read_text(encoding="utf-8"))
    counters_r = json.loads((resume_dir / "counters.json").read_text(encoding="utf-8"))
    turns = sorted(set(meta_s["turns"]) & set(meta_r["turns"]))
    snap_turn = meta_s["snapshot_at"]

    per_turn: list[tuple[int, tuple[str, str, Any, Any] | None]] = []
    for turn in turns:
        a = json.loads((straight_dir / f"turn-{turn}.json").read_text(encoding="utf-8"))
        b = json.loads((resume_dir / f"turn-{turn}.json").read_text(encoding="utf-8"))
        per_turn.append((turn, first_diff(a, b, [])))
    first = next(((t, d) for t, d in per_turn if d is not None), None)

    lines = [
        "# H-7: 500 ターン目の写しの往復で、どの鍵が最初に割れるか",
        "",
        f"- 場面: {meta_s['scenario']} / 種: {meta_s['seed']}",
        f"- 分け: {snap_turn} ターン目で測りの写しを JSON 往復させ、"
        "通し（straight）と復元して続き（resume）を比べる",
        f"- 比べたターン: {turns[0]}〜{turns[-1]}（{snap_turn} は復元直後）",
        "",
        "## 結果",
        "",
    ]
    digest_s = meta_s.get("digest_at_snapshot")
    digest_r = meta_r.get("digest_after_restore")
    if digest_s is not None and digest_r is not None:
        lines.append(
            f"- 写しの指紋（world_digest、{snap_turn} ターン目）: "
            f"{'一致' if digest_s == digest_r else '不一致'}（{digest_s}）"
        )
    if first is None:
        lines.append(
            f"- 最初に割れたターン: なし（{turns[-1]} ターンまで全部の鍵が一致）"
        )
        lines.append("- 最初の差鍵: なし")
        nature = _nature_line(None)
        counter_when_turn = turns[-1]
    else:
        turn, diff = first
        path, kind, va, vb = diff
        lines.append(f"- 最初に割れたターン: {turn}")
        lines.append(
            f"- 最初の差鍵: {path if path else '（最上段）'}（種類: {kind}）"
        )
        lines.append(f"  - 通し: {_short(va)}")
        lines.append(f"  - 戻し: {_short(vb)}")
        nature = _nature_line(diff)
        counter_when_turn = turn
    lines.append(f"- {nature}")
    lines.append(
        "- "
        + counter_line(
            counters_s[str(snap_turn)],
            counters_r[str(snap_turn)],
            f"{snap_turn} ターン目・復元直後",
        )
    )
    lines.append(
        "- "
        + counter_line(
            counters_s[str(counter_when_turn)],
            counters_r[str(counter_when_turn)],
            f"{counter_when_turn} ターン目",
        )
    )
    lines += ["", "## ターンごとの一致", "", "| ターン | 一致 | 最初の差鍵 |", "|---|---|---|"]
    for turn, diff in per_turn:
        if diff is None:
            lines.append(f"| {turn} | ○ | - |")
        else:
            lines.append(f"| {turn} | ✗ | {diff[0] if diff[0] else '（最上段）'}（{diff[1]}） |")
    # カウンタの差の詳細（あれば、最初の差のターンと復元直後）
    detail_turns = sorted({snap_turn, counter_when_turn})
    detail_lines: list[str] = []
    for turn in detail_turns:
        for place, name, va, vb in counter_diffs(counters_s[str(turn)], counters_r[str(turn)]):
            detail_lines.append(f"| {turn} | {place} | {name} | {va} | {vb} |")
    if detail_lines:
        lines += [
            "",
            "## 次番号カウンタの差（詳細）",
            "",
            "| ターン | 場所 | 欄 | 通し | 戻し |",
            "|---|---|---|---|---|",
            *detail_lines,
        ]
    lines += ["", "## 読み", ""]
    if first is None:
        lines.append(
            f"- {turns[-1]} ターンまでの範囲では、写しの往復で落ちる状態は"
            "見つからなかった。割れがもっと後のターンで出るなら、比べる"
            "ターンを延ばした包みが要る。"
        )
    else:
        turn, diff = first
        if turn == snap_turn:
            lines.append(
                "- 復元直後（ターンを進める前）から割れているので、落ちている"
                "のは写しの往復そのもので失われる状態。当たりは #5552 の"
                "(b) 鍵の型と順・(d) 型の変化、または写しに入らない欄。"
            )
        else:
            lines.append(
                f"- 復元直後は一致し、{turn} ターン目で割れた。往復で失われた"
                "状態が、そのターンの処理で初めて効いた形。当たりの順は"
                "#5552 の (a) 次番号カウンタ → (b) 鍵の型と順 → (c) 作り直す"
                "覚え。最初の差鍵とカウンタの差の有無で切り分ける。"
            )
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        if line.startswith("- 最初に割れたターン"):
            print(line)
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--mode", choices=["straight", "resume"], required=True)
    p_run.add_argument("--scenario", required=True)
    p_run.add_argument("--seed", type=int, default=1)
    p_run.add_argument("--turns", type=int, default=500)
    p_run.add_argument("--extra", type=int, default=10)
    p_run.add_argument("--snap-out", default="snapshot-at-500.json")
    p_run.add_argument("--snapshot", default="snapshot-at-500.json")
    p_run.add_argument("--out-dir", required=True)
    p_run.set_defaults(func=run)
    p_rep = sub.add_parser("report")
    p_rep.add_argument("--straight", required=True)
    p_rep.add_argument("--resume", required=True)
    p_rep.add_argument("--out", required=True)
    p_rep.set_defaults(func=report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
