"""W-272 ④ の測りの 3 回目（会議 #4157〜#4167）の走りの台本。生の記録を書くだけで、集計はしない。

集計は x870-w272-trigger-sum.py が、ここで書いた記録（.jsonl.gz）を読むだけで行う。会議で表が足されても、
走り終えた記録に集計をかけ直せば済むように、マス×ターンの値を広めに残す。

3 本（どれも帰農（population.return_to_land）は切、PO-63（subsistence_sales）は 1・2 回目と比べるため切）:
  --mode now      今の版（村か町かは人数で決める。farming_by_housing 切）
  --mode stage    段階だけで決める（farming_by_housing 入）
  --mode trigger  段階＋新しい引き金（farming_by_housing 入、housing.upgrade_without_subsistence 入。PO-65）

記録の種類（1 行 1 件、"k" が種類）:
  meta     走りの条件と、集計に要る定義（村の上限・village_size・通勤の半径・自給の段階）
  judge    雇用の段と記録の段の初めの refresh_farming の入口の値と、返った値（2 版と同じ。FIELDS）
  tile     ターンの終わりの、人の住むマスごと: 人口・村か・段階・住宅地の番号・働き手・雇われている人・
           自給の人・人手の足りない所の近くか（pull）・住める上限・村なら土地が食べさせられる残り
  island   ターンの終わりの島ごと: お金の経済か・labor_share・失業率・預金・飢饉で逃れた人・家計の支出・
           施設の数（種類ごと。住宅地は除く。マスを持たない施設も入れる。倒産した物は数えない）
  upgrade  住宅地の段階が変わった床の足し（Housing._add_floor の前後）: 道（呼んだ所。run_island は空き求人で
           育つ所＝_growable、_relocate は住み替え、_run_works は国の住宅の工事）と、その時のマスの人口・
           自給の人・働き手・雇われている人・床の前後
  gate     新しい引き金が村→町の一段を見たとき（trigger だけ）: 道・外す前と後の望み・道の線・止めたか・
           その時の人口と自給の人（5 版。会議 #4215 の ③b）
  fall     そのターンに初めて倒産の印（economy.foreclosed）が付いた施設: 番号・種類・マス・食べ物を作るか・
           マスを持つか（運送業・海運業などマスを持たない施設も数える。2 版の falls と同じ範囲。4 版で直した）

使い方（main 68998de 以降。PO-65 の入った版。6 本は並べてよい）:
  python x870-w272-trigger-run.py --selftest
  python x870-w272-trigger-run.py --mode now --scenario none --seed 1 --turns 300 --out tr-s1-now.jsonl.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core.geo import within
from hakoniwa_core.modules import employment as empmod
from hakoniwa_core.modules import population as popmod
from hakoniwa_core.modules import records as recmod

FIELDS = ("turn", "seq", "phase", "island", "x", "y", "pop", "stage", "home", "mark_before",
          "actual", "by_size", "by_stage")


class Sink:
    """記録を 1 行ずつ書く（値の写しだけを書く。ゲームの物の参照は持たない）。"""

    def __init__(self, out):
        self.out = out
        self.seq = 0
        self.rows: list[dict] = []  # 試験のとき（out が None）だけ貯める

    def write(self, rec: str, /, **row) -> None:
        row = {"k": rec, **row}
        if self.out is None:
            self.rows.append(row)
        else:
            self.out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _homes(config, island):
    return {(f.x, f.y): f for f in island.facilities if fac.is_housing(config, f) and fac.on_map(f)}


def stage_of(defs, home) -> str:
    if home is None:
        return "none"
    return str(home.ext.get("level") or defs.settlement_levels()[0])


def by_stage(defs, stage: str, population: int) -> bool:
    if stage == "none":
        return popmod._farming_by_size(defs, population)
    return popmod._built_farming(defs, SimpleNamespace(ext={"level": stage}))


def install_judge(sink: Sink, state: dict) -> None:
    """refresh_farming を包む: 入口の値を写してから本体を呼び、返った値を足して書く（2 版と同じ）。"""
    original = popmod.refresh_farming

    def wrapped_for(phase):
        def wrapped(defs, config, island):
            homes = _homes(config, island)
            entries = []
            for tile in island.tiles:
                if tile.population <= 0:
                    continue
                home = homes.get((tile.x, tile.y))
                mark = tile.ext.get(popmod.FARMING)
                entries.append((tile, int(tile.population), stage_of(defs, home),
                                home.id if home is not None else -1,
                                None if mark is None else bool(mark)))
            original(defs, config, island)
            for tile, pop, stage, hid, mark in entries:
                sink.seq += 1
                vals = (state["turn"], sink.seq, phase, island.id, tile.x, tile.y, pop, stage, hid,
                        mark, bool(popmod._farming(defs, tile)),
                        bool(popmod._farming_by_size(defs, pop)), bool(by_stage(defs, stage, pop)))
                sink.write("judge", **dict(zip(FIELDS, vals, strict=True)))
        return wrapped

    empmod.refresh_farming = wrapped_for("employment")
    recmod.refresh_farming = wrapped_for("records")


def install_upgrade(sink: Sink, state: dict, defs_box: dict) -> None:
    """Housing._add_floor を包み、段階の変わった足しを、呼んだ所（道）とその時のマスの値と書く。"""
    original = popmod.Housing._add_floor

    def wrapped(island, defs, home, floor, public=False):
        before = stage_of(defs, home)
        floor_before = fac.floor_of(defs, home)
        tile = island.tile(home.x, home.y)
        share = fac.labor_share(defs, island)
        pop = int(tile.population)
        sub = popmod.subsistence_persons(defs, island, tile, share)
        force = fac.labor_force(defs, island, tile, share)
        emp = int(tile.ext.get("employed", 0))
        route = sys._getframe(1).f_code.co_name
        original(island, defs, home, floor, public)
        after = stage_of(defs, home)
        if after != before:
            sink.write("upgrade", t=state["turn"], i=island.id, x=home.x, y=home.y, hid=home.id,
                       frm=before, to=after, route=route, pop=pop, sub=sub, force=force, emp=emp,
                       floor0=floor_before, floor1=fac.floor_of(defs, home))

    popmod.Housing._add_floor = staticmethod(wrapped)


def install_gate(sink: Sink, state: dict) -> None:
    """新しい引き金（_upgrade_want。村→町の一段でだけ呼ばれる）を包み、望みの前後と、その道の線（住み替えは
    今の床 nominal、空き求人で育つ所は nominal × fill_ratio）を書く。前の望みが線に届き、外した望みが届かない
    物が「門で止めたマス」（段階だけなら町へ上がったはずのマス。会議 #4215 の ③b）。"""
    original = popmod._upgrade_want

    def wrapped(defs, island, tile, want, share):
        got = original(defs, island, tile, want, share)
        caller = sys._getframe(1)
        loc = caller.f_locals
        route = caller.f_code.co_name
        if route == "_relocate":
            nominal = float(loc["d"].nominal)
            line = nominal
            blocked = want > line >= got
        else:  # _growable
            nominal = float(loc["status"][loc["pos"]].nominal)
            line = nominal * float(loc["fill_ratio"])
            blocked = want >= line > got
        sink.write("gate", t=state["turn"], i=island.id, x=tile.x, y=tile.y, route=route,
                   want=float(want), got=float(got), line=line, blocked=bool(blocked),
                   pop=int(tile.population),
                   sub=int(popmod.subsistence_persons(defs, island, tile, share)))
        return got

    popmod._upgrade_want = wrapped


def write_turn(sink: Sink, defs, config, world, gone: set) -> None:
    """ターンの終わりのマス・島・倒産を書く。"""
    t = world.turn
    for island in world.islands.values():
        share = fac.labor_share(defs, island)
        homes = _homes(config, island)
        pull = set()
        if island.has_economy:
            for site, radius, _missing, _ in popmod.vacancies_of(defs, island, config, t):
                pull.update(within(island, site, radius))
        for tile in island.tiles:
            if tile.population <= 0:
                continue
            pos = (tile.x, tile.y)
            home = homes.get(pos)
            farm = bool(popmod._farming(defs, tile))
            land = -1
            if farm and home is not None and island.has_economy:
                land = int(popmod._land_room(defs, config, island, tile, share))
            sink.write("tile", t=t, i=island.id, x=tile.x, y=tile.y, pop=int(tile.population),
                       farm=farm, stage=stage_of(defs, home), hid=home.id if home else -1,
                       force=int(fac.labor_force(defs, island, tile, share)),
                       emp=int(tile.ext.get("employed", 0)),
                       sub=int(popmod.subsistence_persons(defs, island, tile, share)),
                       pull=pos in pull,
                       cap=int(fac.housing_capacity(defs, home)) if home is not None else -1,
                       land=land)
        firms: dict[str, int] = {}
        for f in island.facilities:
            if fac.is_housing(config, f):
                continue
            if "foreclosed" in f.economy:
                if (island.id, f.id) not in gone:
                    gone.add((island.id, f.id))
                    sink.write("fall", t=t, i=island.id, fid=f.id, kind=f.kind, x=f.x, y=f.y,
                               food=bool(fac.food_maker(defs, f.kind)), onmap=fac.on_map(f))
                continue
            if f.built:
                firms[f.kind] = firms.get(f.kind, 0) + 1
        row = {"t": t, "i": island.id, "econ": bool(island.has_economy), "share": share,
               "u": island.stats.get("unemployment", 0.0),
               "dep": island.stats.get("money.deposits", 0),
               "fled": island.stats.get("population.fled_to_villages", 0), "firms": firms}
        if island.has_economy:
            from hakoniwa_core.economy import books

            hh = books.sector(island, books.HOUSEHOLDS)
            row["spend"] = hh.get("goods", 0) + hh.get("electricity", 0) + hh.get("freight", 0)
        sink.write("island", **row)


def configure(config, mode: str) -> None:
    pop = config.get("population")
    pop["subsistence_sales"] = False
    pop["return_to_land"] = False
    pop["farming_by_housing"] = mode in ("stage", "trigger")
    config.get("housing")["upgrade_without_subsistence"] = mode == "trigger"


def meta(defs, config, **extra) -> dict:
    levels = defs.settlement_levels()
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=False).stdout.strip()
    except OSError:
        sha = ""
    return {"version": sha, "village_capacity": popmod.village_capacity(defs),
            "village_size": popmod.village_size(defs),
            "commute_radius": config.get("labor.commute_radius"), "levels": list(levels),
            "subsistence_levels": [lv for lv in levels if defs.settlements[lv].get("subsistence")],
            **extra}


def run(argv):
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.state.generate import generate_world

    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["now", "stage", "trigger"], required=True)
    ap.add_argument("--scenario", default="none")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=300)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    game = load_game(None)
    defs, config = game.defs, game.config
    configure(config, a.mode)
    state = {"turn": 0}
    with gzip.open(a.out, "wt", encoding="utf-8") as out:
        sink = Sink(out)
        sink.write("meta", **meta(defs, config, mode=a.mode, scenario=a.scenario, seed=a.seed,
                                  turns=a.turns, label=f"{a.scenario}-s{a.seed}-t{a.turns}"))
        install_judge(sink, state)
        install_upgrade(sink, state, {})
        install_gate(sink, state)
        scenario = measure.SCENARIOS[a.scenario]
        world = generate_world(a.seed, scenario.islands, config, defs)
        gone: set = set()
        for _ in range(a.turns):
            state["turn"] = world.turn + 1
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            write_turn(sink, defs, config, world, gone)
    print(json.dumps({"out": a.out, "mode": a.mode, "seed": a.seed, "turns": a.turns,
                      "judge_rows": sink.seq}, ensure_ascii=False))


def selftest():
    """記録の試験: 判定の入口の写し（2 版と同じ）と、格上げの道と自給の人の記録。"""
    import copy
    import os

    sys.path.insert(0, os.getcwd())
    from hakoniwa_core import stock
    from hakoniwa_core.economy import GENERAL
    from hakoniwa_core.game import load_game
    from tests.core.test_rules import home, make_island, with_economy

    game = load_game(None)
    defs, config = game.defs, game.config
    configure(config, "trigger")
    state = {"turn": 1}

    # 判定の入口: 判定の後にマスを書き換えても、記録は入口の値のまま
    sink = Sink(None)
    install_judge(sink, state)
    town = popmod.village_size(defs) + 1
    island = make_island(game)
    house = home(game, island, 6, 6, town - 100, level="town")
    empmod.refresh_farming(defs, config, island)
    island.tile(6, 6).population = town - 200
    island.facilities.remove(house)
    (r,) = [x for x in sink.rows if x["k"] == "judge" and (x["x"], x["y"]) == (6, 6)]
    assert (r["pop"], r["stage"], r["actual"], r["by_size"], r["by_stage"]) == (
        town - 100, "town", False, True, False), r
    print("判定の入口の試験: 通った", r)

    # 格上げの道: 住み替えで村→町に入る足しは route=_relocate、その時の自給の人と書く
    sink = Sink(None)
    install_upgrade(sink, state, {})
    village = defs.settlement_levels()[0]
    cap = defs.settlements[village]["capacity"]
    island = make_island(game)
    house = home(game, island, 9, 9, cap * 9 // 10, level=village)
    fac.set_floor(defs, house, cap)
    with_economy(game, island)
    tile = island.tile(9, 9)
    tile.ext["employed"] = 0
    homes = popmod.homes_of(island, config)
    status = popmod.dwelling_status(defs, config, island, homes)
    d = status[(9, 9)]
    status[(9, 9)] = d._replace(want=d.nominal * 1.5, layers={GENERAL: d.nominal * 1.5})
    held = island.economy.housing.setdefault("stock", {})
    for good, qty in fac.floor_materials(defs, cap, cap + 500).items():
        stock.add(held, good, qty, 0, defs.goods)
    island.economy.housing["move"] = {}
    h = copy.deepcopy(config.get("housing"))
    h["upgrade_without_subsistence"] = False  # 切なら住み替えで町に入る
    ctx = SimpleNamespace(defs=defs, config=config, turn=1)
    popmod.Housing._relocate(island, ctx, h, homes, status, set())
    ups = [x for x in sink.rows if x["k"] == "upgrade"]
    assert len(ups) == 1 and ups[0]["route"] == "_relocate", ups
    assert ups[0]["frm"] == village and ups[0]["to"] != village and ups[0]["sub"] > 0, ups
    print("格上げの道の試験: 通った", ups[0])

    # 入なら、同じ村は住み替えで町に入らない（PO-65）
    sink.rows.clear()
    fac.set_floor(defs, house, cap)
    h["upgrade_without_subsistence"] = True
    popmod.Housing._relocate(island, ctx, h, homes, status, set())
    assert not [x for x in sink.rows if x["k"] == "upgrade"], sink.rows
    print("新しい引き金の試験: 通った（自給の農家の村は住み替えで町に入らない）")

    # 門の記録: 入で止めた村は gate に blocked=True で残る
    sink.rows.clear()
    install_gate(sink, state)
    fac.set_floor(defs, house, cap)
    house.ext.pop(fac.FLOOR_DUE, None)
    popmod.Housing._relocate(island, ctx, h, homes, status, set())
    gates = [x for x in sink.rows if x["k"] == "gate"]
    assert len(gates) == 1 and gates[0]["blocked"] and gates[0]["route"] == "_relocate", gates
    print("門の記録の試験: 通った", gates[0])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        selftest()
        if len(sys.argv) > 2:  # 受付で回すときは、通った印をファイルにも書く
            with open(sys.argv[2], "w", encoding="utf-8") as f:
                f.write("selftest: 通った（判定の入口・格上げの道・新しい引き金・門の記録）\n")
    else:
        run(sys.argv[1:])
