"""W-272 ④ の測りの 3 回目の集計の台本。x870-w272-trigger-run.py の記録（.jsonl.gz）を読むだけ。

ゲームは読み込まない（記録の meta に、集計に要る定義が入っている）。会議で表が足されたら、この台本だけを
直して、走り終えた記録にかけ直す。

出す表（2 回目の表すべて。帰農は切なので帰農の欄は除く）:
  flip と合否／段階×帯の交差の表／50 ターンごと／移り変わりの 3×3／flip の出どころ（段階が変わったか×
  そのターンの格上げの道）と往復／詰まり／300 ターンの合計
足した表（会議 #4164・#4167）:
  - 格上げ: 道（_growable＝空き求人で育つ所・_relocate＝住み替え・_run_works＝国の住宅）× 段階の前後ごとの
    件数・人口・自給の人。村→町は 50 ターンごと
  - 食べ物以外の倒産: 種類ごと・50 ターンごとの件数と、企業数（そのターンに動いている施設の数の窓の平均）
    あたりの率
  - 倒産と格上げ: 食べ物以外の倒産のうち、その前 10 ターン（倒産のターンを含む）に、同じ島で／その施設の
    通勤の半径の内で、村→町の格上げがあった物の割合。比べの目安に、同じ島で格上げのあったターンの割合
    （企業数の重み）
  - 町の住宅地に残る仕事のない延べと割合（働き手で割る）を、村のある島とない島に分けた物（50 ターンごと）
    と、島別の物（#4167）
  - 町化の速さ: 窓の終わりの町のマス・人口・雇われている働き手と、stage との比・伸び（#4215 の ③c）
  - 門で止めたマスのその後: 最後のターンに村のまま自給／村で雇われ／町になって雇われ・失業の割合（③b）

使い方:
  python x870-w272-trigger-sum.py tr-s1-now.jsonl.gz tr-s1-stage.jsonl.gz tr-s1-trigger.jsonl.gz
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict

LOCAL_ROUTES = {"run_island": "_growable", "_relocate": "_relocate", "_run_works": "_run_works"}
WINDOW = 10  # 倒産の前の何ターンに格上げを探すか（倒産のターンを含む）


def load(path: str) -> dict:
    rep = {"judge": [], "tiles": defaultdict(lambda: defaultdict(dict)),
           "islands": defaultdict(dict), "upgrades": [], "falls": []}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            k = r.pop("k")
            if k == "meta":
                rep["meta"] = r
            elif k == "judge":
                rep["judge"].append(r)
            elif k == "tile":
                rep["tiles"][r["t"]][r["i"]][(r["x"], r["y"])] = r
            elif k == "island":
                rep["islands"][r["t"]][r["i"]] = r
            elif k == "upgrade":
                r["route"] = LOCAL_ROUTES.get(r["route"], r["route"])
                rep["upgrades"].append(r)
            elif k == "fall":
                rep["falls"].append(r)
            elif k == "gate":
                rep.setdefault("gates", []).append(r)
    rep["mode"] = rep["meta"]["mode"]
    rep["turns"] = sorted(rep["tiles"])
    return rep


def village_stage(meta, stage: str) -> bool:
    return stage in meta["subsistence_levels"]


def is_town_home(meta, r) -> bool:
    """町の住宅地のマス（住宅地があり、段階が村でない）。2 回目の town_idle と同じ。"""
    return r["hid"] >= 0 and not village_stage(meta, r["stage"])


def to_town(meta, u) -> bool:
    return village_stage(meta, u["frm"]) and not village_stage(meta, u["to"])


# --- flip と合否（2 回目の analyse と同じ） ------------------------------------------------------


def band(pop: int, cap: int, town: int) -> str:
    if pop >= town:
        return "over"
    if pop >= cap:
        return "band"
    return "under"


def analyse(meta, judge: list[dict]) -> dict:
    cap = meta["village_capacity"]
    town = meta["village_size"] + 1
    rows = defaultdict(dict)
    for r in judge:
        if r["phase"] == "employment":
            rows[(r["island"], r["x"], r["y"])][r["turn"]] = r
    cross, judged, replay, pairs, unhoused = Counter(), Counter(), Counter(), Counter(), Counter()
    per_stage, mismatch, first_mismatch = Counter(), Counter(), {}
    flips_list, violations, unknown = [], [], 0
    for key, by_turn in rows.items():
        turns = sorted(by_turn)
        for t in turns:
            r = by_turn[t]
            per_stage[r["stage"]] += 1
            if r["stage"] != "none" and r["actual"] != r["by_stage"]:
                mismatch[r["stage"]] += 1
                first_mismatch.setdefault(r["stage"], {"turn": t, "pop": r["pop"], "island": key[0],
                                                       "x": key[1], "y": key[2]})
        for t in turns:
            prev, cur = by_turn.get(t - 1), by_turn[t]
            if prev is None:
                if turns[0] <= t - 1 <= turns[-1]:
                    unknown += 1
                continue
            kind = "none" if cur["stage"] == "none" else (
                "village" if village_stage(meta, cur["stage"]) else "town")
            cell = (kind, band(cur["pop"], cap, town))
            judged[cell] += 1
            flipped = prev["actual"] != cur["actual"]
            if flipped:
                cross[cell] += 1
                flips_list.append((key[0], key[1], key[2], t))
            replay["flips"] += prev["by_stage"] != cur["by_stage"]
            if cur["stage"] == "none" or prev["stage"] == "none":
                unhoused["pairs"] += 1
                unhoused["flips"] += flipped
                continue
            if prev["home"] != cur["home"] or prev["stage"] != cur["stage"]:
                continue
            up = prev["pop"] < town <= cur["pop"]
            down = cur["pop"] < town <= prev["pop"]
            if up or down:
                pairs[(kind, "up" if up else "down")] += 1
                if flipped:
                    violations.append({"island": key[0], "x": key[1], "y": key[2], "turn": t,
                                       "prev": prev, "cur": cur})
    return {"flips": len(flips_list), "flips_list": flips_list,
            "cross": {f"{a}|{b}": n for (a, b), n in cross.items()},
            "judged": {f"{a}|{b}": n for (a, b), n in judged.items()},
            "replay_flips": replay["flips"],
            "pairs": {f"{a}|{b}": n for (a, b), n in sorted(pairs.items())},
            "violations": len(violations), "violation_rows": violations[:1],
            "unhoused": dict(unhoused), "unknown": unknown, "per_stage": dict(per_stage),
            "mismatch": dict(mismatch), "first_mismatch": first_mismatch}


# --- 移り変わりの 3×3（2 回目の flows と同じ。帰農はないので帰農のタグは 0） ---------------------


def snapshot(tiles: dict, share: float) -> dict:
    out = {}
    for pos, r in tiles.items():
        e = min(r["force"], r["emp"])
        s = int(r["sub"] * share)
        u = max(0, r["force"] - e - s)
        out[pos] = (r["farm"], e, u, s, r["stage"], r["sub"])
    return out


def flows(prev: dict, cur: dict) -> Counter:
    f, seen = Counter(), Counter()
    for pos in set(prev) | set(cur):
        p = prev.get(pos, (False, 0, 0, 0, "none", 0))
        c = cur.get(pos, (p[0], 0, 0, 0, "none", 0))
        pe, pu, ps = p[1], p[2], p[3]
        if p[0] != c[0] and pos in prev and pos in cur:
            if p[0]:
                f["S>U"] += ps
                f["reclass.S>U"] += ps
                f["reclass.S>U.persons"] += p[5]
                pu, ps = pu + ps, 0
            else:
                f["U>S"] += pu
                f["reclass.U>S"] += pu
                ps, pu = ps + pu, 0
        de = c[1] - pe
        idle_from = "S" if c[0] else "U"
        take = 0
        if de > 0:
            take = min(de, pu if idle_from == "U" else ps)
            f[f"{idle_from}>E"] += take
            f["other>E"] += de - take
        elif de < 0:
            f[f"E>{idle_from}"] += -de
        f["E>E"] += min(pe, c[1])
        f["U>U"] += pu - (take if idle_from == "U" else 0)
        f["S>S"] += ps - (take if idle_from == "S" else 0)
        f["net.E"] += c[1] - p[1]
        f["net.U"] += c[2] - p[2]
        f["net.S"] += c[3] - p[3]
        for k in ("S>U", "U>S", "S>E", "U>E", "E>S", "E>U"):
            if f[k] != seen[k]:
                f[f"tiles.{k}"] += 1
                seen[k] = f[k]
    f["U.net_inflow"] = f["E>U"] + f["S>U"] - f["U>E"] - f["U>S"]
    f["U.net.job"] = f["E>U"] - f["U>E"]
    f["U.net.reclass"] = f["reclass.S>U"] - f["reclass.U>S"]
    f["U.net.rest"] = f["U.net_inflow"] - f["U.net.job"] - f["U.net.reclass"]
    return f


# --- ターンごとの集計 -----------------------------------------------------------------------------


def dist(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def quantiles(pairs: list) -> dict:
    if not pairs:
        return {}
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    out, acc = {"min": pairs[0][0], "max": pairs[-1][0]}, 0
    marks = [(0.5, "p50"), (0.75, "p75"), (0.9, "p90")]
    for v, w in pairs:
        acc += w
        while marks and acc >= marks[0][0] * total:
            out[marks[0][1]] = v
            marks.pop(0)
    return out


def per_turn(rep: dict) -> None:
    """ターンごとの行（rows）、flip の出どころ、詰まり、島別の延べを rep に足す。"""
    meta = rep["meta"]
    size = meta["village_size"]
    ups_at = defaultdict(list)
    for u in rep["upgrades"]:
        ups_at[(u["i"], u["x"], u["y"], u["t"])].append(u)
    falls_at = defaultdict(Counter)
    for f in rep["falls"]:
        falls_at[f["t"]]["falls_food" if f["food"] else "falls_other"] += 1
    rows, snaps, end_prev = [], {}, {}
    origin, origin_people, last_flip = Counter(), Counter(), {}
    reverse, end_flips = 0, []
    jam, spare = Counter(), []
    by_island = defaultdict(Counter)  # 島 → 村あり／なしの延べ（#4167）
    for t in rep["turns"]:
        row = Counter(falls_at.get(t, {}))
        us, uw = [], []
        for i, tiles in sorted(rep["tiles"][t].items()):
            isl = rep["islands"][t].get(i, {})
            share = isl.get("share", 0.0)
            for pos, r in tiles.items():  # ターンの終わりの村か町かの入れ替わり
                key = (i, *pos)
                if key in end_prev and end_prev[key] != r["farm"]:
                    end_flips.append((i, pos[0], pos[1], t))
                end_prev[key] = r["farm"]
            snap = snapshot(tiles, share)
            before = snaps.get(i)
            if before is not None:
                for k, n in flows(before, snap).items():
                    row[f"flow.{k}"] += n
                for pos, c in snap.items():
                    p = before.get(pos)
                    if p is None or p[0] == c[0]:
                        continue
                    key = (i, *pos)
                    routes = sorted({u["route"] for u in ups_at.get((i, pos[0], pos[1], t), [])})
                    kind = ("段階が変わった" if p[4] != c[4] else "段階は同じ") + "×" + (
                        "格上げ（" + "・".join(routes) + "）" if routes else "格上げなし")
                    origin[kind] += 1
                    origin_people[kind] += tiles[pos]["pop"]
                    if key in last_flip:
                        reverse += 1
                    last_flip[key] = c[0]
            snaps[i] = snap
            if not isl.get("econ"):
                continue
            us.append(isl.get("u", 0.0))
            row["fled"] += isl.get("fled", 0)
            row["spending"] += isl.get("spend", 0)
            row["deposits"] += isl.get("dep", 0)
            villages = []
            receivable = False
            for pos, r in tiles.items():
                if not (r["farm"] and r["hid"] >= 0):
                    continue
                free = r["cap"] - r["pop"]
                if free <= 0:
                    why = "満杯（空き残 0）"
                elif size - r["pop"] <= 0:
                    why = "空きあり・village_size の上限"
                elif r["land"] <= 0:
                    why = "空きあり・土地の上限"
                else:
                    receivable = True
                    why = None
                villages.append((pos, why, max(0, free)))
            has_village = bool(villages)
            labor = idle_all = 0
            for pos, r in tiles.items():
                idle = max(0, r["force"] - r["emp"])
                labor += r["force"]
                idle_all += idle
                row["subsistence"] += r["sub"]
                if not is_town_home(meta, r):
                    continue
                row["town_idle"] += idle
                row["town_pop"] += r["pop"]
                row["town_emp"] += min(r["force"], r["emp"])
                row["town_tiles"] += 1
                row["town_labor"] += r["force"]
                row["town_idle_room" if receivable else "town_idle_noroom"] += idle
                side = "村のある島" if has_village else "村のない島"
                row[f"{side}.town_idle"] += idle
                row[f"{side}.town_labor"] += r["force"]
                by_island[i][f"{side}.town_idle"] += idle
                by_island[i][f"{side}.town_labor"] += r["force"]
                if r["pull"]:
                    row["town_idle_pull"] += idle
                if not receivable and idle > 0 and not r["farm"]:
                    if not villages:
                        jam["村がない"] += idle
                    else:
                        _, why, free = min(villages, key=lambda v: (dist(pos, v[0]), v[0][1], v[0][0]))
                        jam[why] += idle
                        if free > 0:
                            spare.append((free, idle))
            by_island[i]["村のあるターン" if has_village else "村のないターン"] += 1
            row["村のある島の数" if has_village else "村のない島の数"] += 1
            uw.append(idle_all / labor if labor else 0.0)
        out = dict(row)
        out["turn"] = t
        out["u_stat"] = sum(us) / len(us) if us else 0.0
        out["u_wide"] = sum(uw) / len(uw) if uw else 0.0
        out["town_idle_share"] = out.get("town_idle", 0) / out["town_labor"] if out.get("town_labor") else 0.0
        rows.append(out)
    rep.update(rows=rows, origin=origin, origin_people=origin_people, reverse=reverse,
               end_flips=end_flips, jam=jam, spare=quantiles(spare), by_island=by_island)


# --- 表 ------------------------------------------------------------------------------------------


def windows(rep, size=50):
    rows = rep["rows"]
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def span(win) -> str:
    return f"{win[0]['turn']}〜{win[-1]['turn']}"


def fmt_rate(n, d) -> str:
    return f"{n / d:.3f}" if d else "-"


def compare(paths):
    reps = [load(p) for p in paths]
    for r in reps:
        r["analysis"] = analyse(r["meta"], r["judge"])
        per_turn(r)
    m = reps[0]["meta"]
    print(f"## {m['label']}（版 {m.get('version', '')[:9]}）")

    print("\n### flip と合否")
    print("| 本 | flip（入口） | flip（ターンの終わり） | 入口と終わりの重なり | リプレイの flip | 違反 | 判定不能 | "
          "住宅地なしのペア・flip |\n|---|---|---|---|---|---|---|---|")
    for r in reps:
        a = r["analysis"]
        end, ent = set(r["end_flips"]), set(a["flips_list"])
        uh = a["unhoused"]
        over = {"both": len(end & ent), "end_only": len(end - ent), "entry_only": len(ent - end)}
        print(f"| {r['mode']} | {a['flips']} | {len(end)} | {over} | {a['replay_flips']} | {a['violations']} | "
              f"{a['unknown']} | {uh.get('pairs', 0)}・{uh.get('flips', 0)} |")
    for r in reps:
        a = r["analysis"]
        print(f"\n### {r['mode']}: flip の交差の表（段階 × 帯。件数／判定回数＝率）")
        print("| 段階＼帯 | over（50,000 以上） | band（48,000〜49,999） | under（48,000 未満） |\n|---|---|---|---|")
        for st in ("village", "town", "none"):
            cells = []
            for b in ("over", "band", "under"):
                n, d = a["cross"].get(f"{st}|{b}", 0), a["judged"].get(f"{st}|{b}", 0)
                cells.append(f"{n}／{d}＝{n / d:.4f}" if d else "対象なし")
            print(f"| {st} | " + " | ".join(cells) + " |")
        print(f"- 検査できたペア（段階が同じで 50,000 をまたいだ）: {a['pairs'] or '対象なし'}")
        print(f"- 段階ごとの判定回数: {a['per_stage']}、食い違い: {a['mismatch'] or 0}、最初: {a['first_mismatch'] or '-'}")
        if a["violation_rows"]:
            print(f"- 違反の最初の組: {json.dumps(a['violation_rows'][0], ensure_ascii=False)}")

    keys = ["town_idle", "town_idle_share", "town_pop", "town_idle_room", "town_idle_noroom", "town_idle_pull",
            "fled", "subsistence", "u_stat", "u_wide", "spending", "deposits", "falls_food", "falls_other"]
    print("\n### 50 ターンごと（延べ・内訳は合計、率は平均、倒産は合計）")
    print("| ターン | 本 | " + " | ".join(keys) + " |\n|---|---|" + "---|" * len(keys))
    for wins in zip(*(list(windows(r)) for r in reps), strict=True):
        for r, win in zip(reps, wins, strict=True):
            vals = []
            for k in keys:
                if k.startswith("u_") or k == "town_idle_share":
                    vals.append(f"{sum(x.get(k, 0) for x in win) / len(win):.3f}")
                elif k in ("spending", "deposits", "subsistence"):
                    vals.append(f"{sum(x.get(k, 0) for x in win) / len(win):.3e}")
                else:
                    vals.append(f"{sum(x.get(k, 0) for x in win):.0f}")
            print(f"| {span(win)} | {r['mode']} | " + " | ".join(vals) + " |")

    flow_keys = sorted({k for r in reps for x in r["rows"] for k in x if k.startswith("flow.")})
    print("\n### 移り変わりの 3×3（働き手。マスごとの増減からの推定。50 ターンごとの合計）")
    print("| ターン | 本 | " + " | ".join(k[5:] for k in flow_keys) + " |\n|---|---|" + "---|" * len(flow_keys))
    for wins in zip(*(list(windows(r)) for r in reps), strict=True):
        for r, win in zip(reps, wins, strict=True):
            vals = [f"{sum(x.get(k, 0) for x in win):.3e}" for k in flow_keys]
            print(f"| {span(win)} | {r['mode']} | " + " | ".join(vals) + " |")

    print("\n### flip の出どころ（段階が変わったか×そのターンの格上げの道。件数・人数）と往復")
    for r in reps:
        print(f"- {r['mode']}: " + "、".join(f"{k} {v} 件・{r['origin_people'].get(k, 0):.3e} 人"
                                         for k, v in sorted(r["origin"].items())) + f"、往復 {r['reverse']} 回")

    print("\n### 格上げ（道 × 段階の前後。件数・その時の人口・自給の人）")
    for r in reps:
        c = defaultdict(Counter)
        for u in r["upgrades"]:
            c[(u["route"], u["frm"], u["to"])]["n"] += 1
            c[(u["route"], u["frm"], u["to"])]["pop"] += u["pop"]
            c[(u["route"], u["frm"], u["to"])]["sub"] += u["sub"]
        print(f"- {r['mode']}: " + ("、".join(
            f"{route} {a}→{b} {v['n']} 件・人口 {v['pop']:.3e}・自給 {v['sub']:.3e}（{fmt_rate(v['sub'], v['pop'])}）"
            for (route, a, b), v in sorted(c.items())) or "なし"))
    print("\n### 村→町の格上げ（50 ターンごと。件数・自給の人。道ごと）")
    print("| ターン | 本 | _growable 件数 | _growable 自給 | _relocate 件数 | _relocate 自給 | ほか 件数 |\n|---|---|---|---|---|---|---|")
    for wins in zip(*(list(windows(r)) for r in reps), strict=True):
        for r, win in zip(reps, wins, strict=True):
            lo, hi = win[0]["turn"], win[-1]["turn"]
            c = Counter()
            for u in r["upgrades"]:
                if lo <= u["t"] <= hi and to_town(r["meta"], u):
                    route = u["route"] if u["route"] in ("_growable", "_relocate") else "ほか"
                    c[f"{route}.n"] += 1
                    c[f"{route}.sub"] += u["sub"]
            print(f"| {lo}〜{hi} | {r['mode']} | {c['_growable.n']} | {c['_growable.sub']:.3e} | "
                  f"{c['_relocate.n']} | {c['_relocate.sub']:.3e} | {c['ほか.n']} |")

    print("\n### 食べ物以外の倒産（種類ごと・50 ターンごと。件数／企業数（窓の平均）＝率）")
    for r in reps:
        kinds = sorted({f["kind"] for f in r["falls"] if not f["food"]})
        food = {f["kind"] for f in r["falls"] if f["food"]}
        wins = list(windows(r))
        head = [span(w) for w in wins] + ["通し"]
        print(f"\n#### {r['mode']}")
        print("| 種類 | " + " | ".join(head) + " |\n|---|" + "---|" * len(head))
        firms_t = {t: Counter() for t in r["turns"]}
        for t in r["turns"]:
            for isl in r["islands"][t].values():
                firms_t[t].update(isl.get("firms", {}))
        all_kinds = sorted({k for c in firms_t.values() for k in c} - food)
        for kind in kinds + ["（食べ物以外の計）"]:
            cells = []
            for win in wins + [r["rows"]]:
                lo, hi = win[0]["turn"], win[-1]["turn"]
                n = sum(1 for f in r["falls"] if not f["food"] and lo <= f["t"] <= hi
                        and (kind == "（食べ物以外の計）" or f["kind"] == kind))
                pool = all_kinds if kind == "（食べ物以外の計）" else [kind]
                firms = sum(firms_t[t][k] for t in range(lo, hi + 1) if t in firms_t for k in pool)
                avg = firms / (hi - lo + 1)
                cells.append(f"{n}／{avg:.0f}＝{fmt_rate(n, avg)}")
            print(f"| {kind} | " + " | ".join(cells) + " |")

    print(f"\n### 食べ物以外の倒産と、その前 {WINDOW} ターン（倒産のターンを含む）の村→町の格上げ")
    for r in reps:
        meta = r["meta"]
        radius = meta["commute_radius"]
        ups = [u for u in r["upgrades"] if to_town(meta, u)]
        by_isl = defaultdict(list)
        for u in ups:
            by_isl[u["i"]].append(u)
        falls = [f for f in r["falls"] if not f["food"]]
        same = near = 0
        for f in falls:
            prior = [u for u in by_isl.get(f["i"], []) if f["t"] - WINDOW < u["t"] <= f["t"]]
            same += bool(prior)
            near += f.get("onmap", True) and any(
                dist((f["x"], f["y"]), (u["x"], u["y"])) <= radius for u in prior)
        food = {f["kind"] for f in r["falls"] if f["food"]}
        base_n = base_d = 0
        for t in r["turns"]:
            for i, isl in r["islands"][t].items():
                if not isl.get("econ"):
                    continue
                n = sum(v for k, v in isl.get("firms", {}).items() if k not in food)
                hit = any(t - WINDOW < u["t"] <= t for u in by_isl.get(i, []))
                base_d += n
                base_n += n * hit
        offmap = sum(1 for f in falls if not f.get("onmap", True))
        print(f"- {r['mode']}: 倒産 {len(falls)} 件（うちマスを持たない施設 {offmap}。半径の内は数えない）、同じ島で格上げあり {same}（{fmt_rate(same, len(falls))}）、"
              f"通勤の半径 {radius} の内で格上げあり {near}（{fmt_rate(near, len(falls))}）。"
              f"目安: 同じ島で格上げのあったターンの割合（企業数の重み） {fmt_rate(base_n, base_d)}")

    print("\n### 町の住宅地に残る仕事のない延べと割合（働き手で割る）: 村のある島とない島（50 ターンごと。#4167）")
    print("| ターン | 本 | 村のある島の数（延べ） | 村のある島の延べ | 割合 | 村のない島の数（延べ） | 村のない島の延べ | 割合 |\n"
          "|---|---|---|---|---|---|---|---|")
    for wins in zip(*(list(windows(r)) for r in reps), strict=True):
        for r, win in zip(reps, wins, strict=True):
            s = Counter()
            for x in win:
                for k in ("村のある島の数", "村のない島の数", "村のある島.town_idle", "村のある島.town_labor",
                          "村のない島.town_idle", "村のない島.town_labor"):
                    s[k] += x.get(k, 0)
            print(f"| {span(win)} | {r['mode']} | {s['村のある島の数']} | {s['村のある島.town_idle']:.3e} | "
                  f"{fmt_rate(s['村のある島.town_idle'], s['村のある島.town_labor'])} | {s['村のない島の数']} | "
                  f"{s['村のない島.town_idle']:.3e} | {fmt_rate(s['村のない島.town_idle'], s['村のない島.town_labor'])} |")
    print("\n### 島別（通しの延べ。村のあるターン数／村のない、それぞれの町の仕事のない延べと割合）")
    head = []
    for r in reps:
        head += [f"{r['mode']} 村あり ターン", "延べ", "割合", "村なし ターン", "延べ", "割合"]
    print("| 島 | " + " | ".join(head) + " |\n|---|" + "---|" * len(head))
    islands = sorted({i for r in reps for i in r["by_island"]})
    for i in islands:
        cells = []
        for r in reps:
            b = r["by_island"].get(i, Counter())
            for side, turns in (("村のある島", "村のあるターン"), ("村のない島", "村のないターン")):
                cells += [str(b[turns]), f"{b[f'{side}.town_idle']:.2e}",
                          fmt_rate(b[f"{side}.town_idle"], b[f"{side}.town_labor"])]
        print(f"| {i} | " + " | ".join(cells) + " |")

    print("\n### 町化の速さ（各窓の終わりのターンの、町の住宅地のマスの数・人口・雇われている働き手。"
          "括弧は stage を 1 とした比。#4215 の ③c）")
    base = next((r for r in reps if r["mode"] == "stage"), reps[0])
    print("| ターン | 本 | 町のマス | 町の人口 | 町で雇われている働き手 | 前の窓からの伸び（人口・雇われ） |\n|---|---|---|---|---|---|")
    for idx, wins in enumerate(zip(*(list(windows(r)) for r in reps), strict=True)):
        bwin = list(windows(base))[idx]
        b = bwin[-1]
        for r, win in zip(reps, wins, strict=True):
            e = win[-1]
            prev = list(windows(r))[idx - 1][-1] if idx else r["rows"][0]
            def ratio(k, e=e, b=b):
                return f"（{e.get(k, 0) / b[k]:.3f}）" if b.get(k) else ""
            grow = (f"{e.get('town_pop', 0) / prev['town_pop'] - 1:+.3f}・{e.get('town_emp', 0) / prev['town_emp'] - 1:+.3f}"
                    if prev.get("town_pop") and prev.get("town_emp") else "-")
            print(f"| {e['turn']} | {r['mode']} | {e.get('town_tiles', 0)}{ratio('town_tiles')} | "
                  f"{e.get('town_pop', 0):.4g}{ratio('town_pop')} | {e.get('town_emp', 0):.4g}{ratio('town_emp')} | {grow} |")

    print("\n### 門で止めたマスのその後（新しい引き金が村→町の一段を止めたマス。最後のターンの人の行き先。#4215 の ③b）")
    for r in reps:
        gates = r.get("gates", [])
        if not gates:
            continue
        first = {}
        for g in gates:
            if g["blocked"]:
                first.setdefault((g["i"], g["x"], g["y"]), g["t"])
        last = r["turns"][-1]
        end = r["tiles"][last]
        ups = {(u["i"], u["x"], u["y"]) for u in r["upgrades"] if to_town(r["meta"], u)}
        c = Counter()
        for key, t0 in first.items():
            i, x, y = key
            tile = end.get(i, {}).get((x, y))
            share = r["islands"][last].get(i, {}).get("share", 0.0)
            later = any(u["i"] == i and (u["x"], u["y"]) == (x, y) and u["t"] > t0 for u in r["upgrades"]
                        if to_town(r["meta"], u))
            c["後に町へ上がった"] += later
            if tile is None:
                c["マス.人がいない"] += 1
                continue
            work = min(tile["force"], tile["emp"]) / share if share else 0
            work = min(work, tile["pop"])
            sub = min(tile["sub"], tile["pop"] - work)
            rest = max(0, tile["pop"] - work - sub)
            if village_stage(r["meta"], tile["stage"]):
                c["マス.村のまま"] += 1
                c["人.村のまま自給"] += sub
                c["人.村で雇われ"] += work
                c["人.村でほか"] += rest
            else:
                c["マス.町になった"] += 1
                c["人.町で雇われ"] += work
                c["人.町で失業（家族を含む）"] += rest + sub
        delays = []
        for key, t0 in first.items():
            later = [u["t"] for u in r["upgrades"]
                     if (u["i"], u["x"], u["y"]) == key and u["t"] > t0 and to_town(r["meta"], u)]
            if later:
                delays.append(min(later) - t0)
        people = sum(v for k, v in c.items() if k.startswith("人."))
        tiles = len(first)
        print(f"- {r['mode']}: 止めたマス {tiles}（止めた判定 {sum(1 for g in gates if g['blocked'])} 回、"
              f"村→町の格上げのあったマス {len(ups)}）。最後のターン: "
              + "、".join(f"{k[2:]} {v}" for k, v in c.items() if k.startswith("マス."))
              + f"、後に町へ上がった {c['後に町へ上がった']}。人（{people:.4g} 人）の割合: "
              + "、".join(f"{k[2:]} {v / people:.3f}" for k, v in c.items() if k.startswith("人.") and people))
        if delays:
            q = quantiles([(d, 1) for d in delays])
            bins = Counter("1〜10" if d <= 10 else "11〜50" if d <= 50 else "51〜100" if d <= 100 else "101〜"
                           for d in delays)
            print(f"  - 止めてから町へ上がるまでの遅れ（ターン、{len(delays)} マス）: {q}、"
                  + "、".join(f"{k} {bins[k]}" for k in ("1〜10", "11〜50", "51〜100", "101〜") if bins[k])
                  + f"。最後のターンまで上がらない {len(first) - len(delays)} マス")

    print("\n### 詰まり: 受け入れられる村のない島の、町の住宅地の仕事のない延べを、いちばん近い村の状態で分けた物")
    for r in reps:
        tot = sum(r["jam"].values()) or 1
        print(f"- {r['mode']}: " + "、".join(f"{k} {v:.3e}（{v / tot:.0%}）"
                                         for k, v in sorted(r["jam"].items(), key=lambda kv: -kv[1]))
              + f"。空きありの側の空き残（人数の重み）: {r['spare'] or '対象なし'}")

    print("\n### 通しの合計")
    for r in reps:
        tot = {k: sum(x.get(k, 0) for x in r["rows"]) for k in keys
               if not k.startswith("u_") and k != "town_idle_share"}
        labor = sum(x.get("town_labor", 0) for x in r["rows"])
        tot["town_idle ÷ town_labor（延べどうし）"] = tot["town_idle"] / labor if labor else 0.0
        tot["村→町の格上げ"] = sum(1 for u in r["upgrades"] if to_town(r["meta"], u))
        print(f"- {r['mode']}: " + "、".join(f"{k} {v:.4g}" for k, v in tot.items()))


if __name__ == "__main__":
    compare(sys.argv[1:])
