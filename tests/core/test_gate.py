"""関門: 標準の計画を与えた島を 300 ターン回す。

フェーズ1の関門:
- 鉄鉱石から鋼鉄、鋼鉄から機械、機械で新しい工場という生産の連鎖が回る
- 倉庫と施設の在庫がマイナス（や 0 の組）にならない
- 人口が爆発も消滅もしない

フェーズ2の関門（hakoniwa_core/gate.py の目安）:
- 帳簿が毎ターン合う（記録の段が確かめ、合わなければその場で止まる）
- 生産の側と支出の側の GDP が合う、売り買いのあった財の値段が初めの値段の 1/4〜4 倍に収まる、
  金利が 0〜20% に収まる
- 同じ種なら同じ結果になり、島の並び順や、途中で保存して読み直すことで結果が変わらない（フェーズ3-1
  から、標準の計画の貿易圏で島どうしが売り買いする世界で確かめる）

物価上昇率・失業率・物価指数の振れ幅を含むすべての目安は、50 島 × 300 ターンの一括シミュレーション
（python -m hakoniwa_core.simulate --islands 50 --turns 300 --autoplan --gate）で確かめる。
時間がかかるので、自動テストでは環境変数 HAKONIWA_FULL_GATE=1 のときだけ回す。
"""

from __future__ import annotations

import json
import os
import random

import pytest

from hakoniwa_core import autoplan, gate, stock, stops, veins
from hakoniwa_core import facilities as fac
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.founding import found_island
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.market import Market, standing
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state import WorldState
from hakoniwa_core.state.generate import generate_land, generate_world, random_plan
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from hakoniwa_core.trade import merchant as trade_merchant
from hakoniwa_core.units import from_milli

TURNS = 300
MERCHANT_CALM = 12  # 海外の商人が去った後、値段を見ないターン数（貿易のない 1 島の世界）
# 品不足の切り替え（GT-46）での国の倉庫（会議 #4423・#4429・#4433・#4437）: 倉庫が売り手になるのは、
# 売りに出せる荷（放出の決まりと同じ数え方）がそのターンの要る量の WAREHOUSE_TURNS ターン分以上の
# とき。品不足に入るのは 1 ターン、抜けるのは足りているターンが WAREHOUSE_RUN ターン続いたとき。
# 要る量が NEED_MIN 未満のターンは判定せず、品不足の状態を変えない
WAREHOUSE_TURNS = 1
WAREHOUSE_RUN = 3
NEED_MIN = 1000  # 1000 分の 1 単位（1 単位）


@pytest.fixture(scope="module")
def game():
    return load_game()


def make_world(game, seed: int, resources: list[str]) -> WorldState:
    config = game.config
    rng = random.Random(seed)
    land = generate_land(
        rng, config.get("island.size"), config.get("island.land_min"), config.get("island.land_max")
    )
    plan = random_plan(rng, "試験島", config, game.defs, land)
    plan.resources = resources
    island = found_island(game.defs, config, plan, 1, 0, 0)
    island.ext[veins.KEY] = veins.VERSION  # 資源を決めた島に、銅の鉱脈を足さない
    return WorldState(turn=0, seed=seed, islands={1: island})


def run_with_plan(game, world: WorldState, turns: int, each_turn=None) -> None:
    for _ in range(turns):
        for island_id in world.island_ids():
            island = world.islands[island_id]
            queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
        for island_id, orders in plan_trade(world, game.defs, game.config).items():
            queue_orders(world.islands[island_id], orders)  # 貿易圏（フェーズ3-1）
        game.engine.run_turn(world)
        if each_turn is not None:
            each_turn(world.islands[min(world.islands)])


@pytest.mark.parametrize("seed", [1, 2])
def test_supply_chain_runs_for_300_turns(game, seed):
    world = make_world(game, seed, ["coal", "iron_ore"])
    island = world.islands[1]
    start_population = sum(t.population for t in island.tiles)
    produced: dict[str, float] = {}
    first_made: dict[str, int] = {}  # 島で初めて作ったターン
    machinery_built_in = 0.0  # 島で機械を作り始めた後に建った施設が、資材に使った機械
    machinery_repaired = 0.0  # 島で機械を作り始めた後に、建っている施設が設備を直すのに買った機械
    populations = [start_population]
    bad_stock = []
    short = []  # 食べ物が足りなかったターン
    starved = 0
    offered: dict[int, dict[str, int]] = {}  # ターン → 商人が売りに出した量（財 → 1000 分の 1）
    needed: dict[int, dict[str, int]] = {}  # ターン → 島の建った施設が全力で要る量（1000 分の 1）
    stored: dict[int, dict[str, int]] = {}  # ターン → 国の倉庫の売りに出せる商人の財の量
    farms: dict[
        int, list[tuple[int, int, int]]
    ] = {}  # 機械化した農場の（燃料の手元, 人手, 燃料のある人手）
    makers: dict[int, dict[str, list[tuple]]] = {}  # ターン → 財 → 作り手の様子（_makers）
    held: dict[int, set[str]] = {}  # ターン → 国の倉庫に荷のある財

    def check(island):
        for key, value in island.stats.items():
            if key.startswith("production."):
                good = key.removeprefix("production.")
                produced[good] = produced.get(good, 0) + value
                if value > 0:
                    first_made.setdefault(good, world.turn)
        nonlocal machinery_built_in, machinery_repaired
        for entry in island.log:
            if entry.kind == "construction_done" and produced.get("machinery", 0) > 0:
                materials = game.defs.facilities[entry.args["facility"]]["construction"]
                machinery_built_in += from_milli(materials.get("materials", {}).get("machinery", 0))
        by_id = {f.id: f for f in island.facilities}
        for t in island.trades:
            if t.good != "machinery" or not t.buyer.startswith("fac:"):
                continue
            f = by_id.get(int(t.buyer.removeprefix("fac:")))
            # 建っていて、機械を原料に使わない施設が買った機械は、設備を直す分（回6）
            if f is not None and f.built and produced.get("machinery", 0) > 0:
                if "machinery" not in fac.recipe_of(game.defs, f).get("inputs", {}):
                    machinery_repaired += from_milli(t.quantity)
        for good, batches in island.warehouse.items():
            bad_stock.extend((good, q) for _, q in batches if q <= 0)
        for f in island.facilities:
            for g, batches in f.stock.items():
                bad_stock.extend((f.kind, g, q) for _, q in batches if q <= 0)
        # 品不足の確かめ（GT-46）: 商人の売りの予定、島の施設が全力で要る量、国の倉庫の荷、
        # 機械化した農場の燃料の手元と要る人手
        sale = {
            g: q for g, q, _ in trade_merchant.offers(island, game.defs, game.config, world.turn)
        }
        offered[world.turn] = sale
        needed[world.turn] = {
            g: sum(
                fac.recipe_of(game.defs, f, fuelled=False)["inputs"].get(g, 0)
                for f in island.facilities
                if f.built
            )
            for g in sale
        }
        stored[world.turn] = _sellable(game.defs, game.config, island, sale)
        makers[world.turn] = _makers(game.defs, island)
        held[world.turn] = {g for g in island.warehouse if stock.total(island.warehouse, g) > 0}
        farms[world.turn] = [
            (
                f.economy.get(fac.FUEL_SHARE, fac.FULL),
                fac.machine_ratio(f),
                fac.workers_of(game.defs, f),
                fac.scaled_workers(
                    game.defs, f.kind, fac.recipe_of(game.defs, f, fuelled=False).get("labor", 1.0)
                )
                * fac.size_of(f),
            )
            for f in island.facilities
            if f.built
            and fac.GRADE in f.ext
            and fac.recipe_of(game.defs, f, fuelled=False)["inputs"].get(fac.FUEL, 0) > 0
        ]
        populations.append(island.stats["population"])
        nonlocal starved
        starved += island.stats.get("population.starved", 0)
        if island.stats.get("food.ratio", 1.0) < 0.95:
            short.append(world.turn)
        recorder.record(world)

    recorder = gate.Recorder(world, game.defs, game.config)
    run_with_plan(game, world, TURNS, check)

    assert world.turn == TURNS
    # 原料の行き止まりで作れない財（GT-46 の広げ。2026-11-18 掲示板の会議、W-294）: その間の値段の
    # 外れは落ちにせず、記録に残す。値段の目安を見る前に、その間の値段の記録を目安から外す
    merchant = game.config.get("foreign_merchant")
    leave = island.ext["trade"]["merchant_from"] + merchant["turns"]
    window, blank = _merchant_window(offered, leave)
    stuck = _dead_end_turns(game.defs, island.resources, offered, held, makers, window)
    dead_notes = _excuse_dead_end(recorder.islands[island.id], recorder.initial[island.id], stuck)
    print(f"商人の不在を見る窓 {window} ターン（商人が去る前の、売りのない間の最長 {blank} + 1）")
    print("\n".join(dead_notes))
    # 生産の連鎖: 鉄鉱石 → 鋼鉄 → 機械 → 機械を使った新しい工場
    assert produced.get("iron_ore", 0) > 0
    assert produced.get("steel", 0) > 0
    assert produced.get("machinery", 0) > 0
    # 回3 までは「初めの倉庫の機械より多く使った」で見ていたが、回4 で人口が年1%ほどで増えるように
    # なり、工事が一巡した後に建つ工場が少ない（回2 で分かった E01）ので、機械を作り始めた後に機械を
    # 使う施設が建つことを見ていた。回6 で設備が傷むようになり、工事が一巡した後の機械の買い手は
    # 設備を直す施設なので、建つ施設か直す施設のどちらかが島の機械を使うことを見る
    assert machinery_built_in + machinery_repaired > 0, (
        "島で機械を作り始めた後に、機械を使って建った施設も、設備を直した施設もない"
    )
    # 在庫はマイナスにならない（0 になった組は消える）
    assert bad_stock == []
    # 人口は爆発も消滅もしない
    assert min(populations) >= start_population * 0.5
    assert populations[-1] <= start_population * 15
    jumps = [abs(b - a) / a for a, b in zip(populations, populations[1:], strict=False)]
    assert max(jumps) < 0.05
    # 食料は足りている
    assert island.stats["food.ratio"] >= 0.95
    # フェーズ2: GDP の両側が合い、値段と金利が目安に収まる（帳簿は毎ターン確かめている）
    hard = ("GDP の差", "の値段が", "金利の")
    # 飢饉のあいだは、食べ物の値段が目安を超えてよい（現実にも何倍にもなる。ベンガルの 1943 年の米は
    # 4〜5倍）。島でただ1つの穀物の農場が作物を替えると飢饉になることがある（回7 で企業と一緒に
    # 見る）ので、そのときは飢えで亡くなる人が少ないこと（島の人の1%まで）を見る
    if short:
        assert starved < start_population * 0.01
    food = {g for g, d in game.defs.goods.items() if "food" in d} if short else set()
    # 海外の商人の量が初めの 1 割を切ってから、去った後 MERCHANT_CALM ターンまでは、値段を見ない
    # （タブ 9 FX-27）。この世界は貿易の相手のいない 1 島で、島で作れない商人の品目（燃料など）は
    # 商人の最後の少ない荷を高い値段で奪い合い、物価が跳ねる（輸入が止まったときの現実の値動き）。
    # 前の形（120 ターンで急に去る）では、去った後はその品目が売り買いされず、値段に数えなかった
    tail = (merchant["turns"] - merchant["taper_from"]) // 10
    # 商人が去った後、島で作っていない品目は、施設に残った少しの荷（燃料 0.001 など）を施設どうしで
    # 売り買いするだけで、値段は商人の最後の値段のまま残る。その値段は見ない（FM-54 で立て直しが
    # 閉める施設が変わり、残りの荷を売る時が静かな間の後にずれた）
    unmade = {g for g in game.defs.goods if produced.get(g, 0) <= 0}
    after = [
        m
        for m in recorder.failures(start=leave + MERCHANT_CALM)
        if not any(f"）: {g} の値段が" in m for g in unmade)
    ]
    # 品不足に切り替える財（GT-46。2026-11-15 掲示板の会議）: 島で作らず、去る商人のほかに売り手も
    # 国の倉庫の放出もない財は、商人の売りの予定が島の施設の要る量を去るまで続けて下回り始めたターン
    # から、値段の目安の代わりに品不足の確かめに切り替える。輸入がほぼ止まれば値段が初めの 4 倍を
    # 超えるのは現実の動きで（種 2 の燃料は商人の売りが初めの 1 割 2 分のとき超えた）、そこで見る
    # のは値段でなく、足りない分の手当て（燃料なら畑が手作業に戻って人手が増える）。境目は値段や
    # 落ちたターンからは決めない
    # 国の倉庫に売りに出せる荷が戻って足りるターンが続けば、品不足を抜けて値段を見る（#4429）
    scarce = {
        g: t0 for g in unmade if (t0 := _shortage_from(offered, needed, g, leave)) is not None
    }
    short_turns = {g: _shortage_turns(stored, needed, g, t0, leave) for g, t0 in scarce.items()}
    priced = [
        m
        for m in recorder.failures(end=leave - tail - 1)
        if not any(f"）: {g} の値段が" in m for g in scarce)
    ]
    notes = []  # 品不足の判定と、品不足の間に値段が目安を外れたこと（記録に残す。試験は落とさない）
    last = leave - 1
    for g in sorted(needed.get(last, {})):
        if needed[last][g] > 0:
            stored_turns = [
                (t, stored[t][g], needed.get(t, {}).get(g, 0))
                for t in sorted(stored)
                if g in stored[t] and t >= last - 60
            ]
            notes.append(
                f"品不足の判定 {g}: 島で作った {produced.get(g, 0)}、T{last} の売り "
                f"{offered[last].get(g, 0)}・要る量 {needed[last][g]}、始まり "
                f"{_shortage_from(offered, needed, g, leave)}、"
                f"国の倉庫に売りに出せる荷のあるターン（ターン, 荷, 要る量） {stored_turns[:5]}"
            )
    for g, t0 in scarce.items():
        mark = f"）: {g} の値段が"
        # 品不足でないターン（t0 の前と、倉庫の荷で抜けた間）は値段を見る
        normal = [t for t in range(gate.START, leave - tail) if t not in short_turns[g]]
        for lo, hi in _spans(normal):
            priced += [m for m in recorder.failures(start=lo, end=hi) if mark in m]
        notes += [f"品不足 T{t0}〜: {m}" for m in recorder.failures(start=t0) if mark in m]
        if len(short_turns[g]) < leave - t0:
            notes.append(
                f"{g} は倉庫の荷で品不足を抜けたターンがある: 品不足のターン {len(short_turns[g])}"
            )
    print("\n".join(notes))
    if fac.FUEL in scarce and (fuel_run := _last_run(short_turns[fac.FUEL], leave)) is not None:
        _check_fuel_shortage(farms, fuel_run, leave)
    priced += after
    # 島でまだ作っていない原料（鉱山が建つ前の鉄鉱石など）は、初めの倉庫の荷が尽きると値段が上がる。
    # 鉱山を使う工場が先に建つと起こる（標準の計画の順の弱さ、main でもある）ので、その原料の値段は
    # 島で初めて作ってから MERCHANT_CALM ターン後まで見ない（2026-11-13 本人のカード「試験を直す」）
    for good, turn in first_made.items():
        if turn <= 1:
            continue
        mark = f"）: {good} の値段が"
        calm = turn + MERCHANT_CALM + 1
        priced = [m for m in priced if mark not in m]
        if calm <= leave - tail - 1:
            priced += [m for m in recorder.failures(start=calm, end=leave - tail - 1) if mark in m]
        late = recorder.failures(start=max(calm, leave + MERCHANT_CALM))
        priced += [m for m in late if mark in m]
    failures = [m for m in recorder.failures() if any(h in m for h in hard) and "の値段が" not in m]
    failures += [m for m in priced if "の値段が" in m]
    assert [m for m in failures if not any(f"）: {g} の値段が" in m for g in food)] == []


def _makers(defs, island) -> dict[str, list[tuple]]:
    """財ごとの、島の建った作り手の様子（施設, 止まった訳, 稼働率, 止めた原料の手元, 全力で
    1 ターンに要るその原料）。止まった訳が原料の品切れでなければ、手元と要る量は 0。"""
    out: dict[str, list[tuple]] = {}
    for f in island.facilities:
        if not f.built:
            continue
        made = [g for g, q in fac.outputs_of(defs, f).items() if q > 0]
        if not made:
            continue
        head, good = stops.split(f.status)
        have = need = 0
        if head == stops.INPUT and good:
            have = stock.total(f.stock, good)
            need = fac.recipe_of(defs, f)["inputs"].get(good, 0)
        for g in made:
            out.setdefault(g, []).append((f.kind, f.status, f.operating_rate, have, need))
    return out


def _merchant_window(offered: dict[int, dict[str, int]], leave: int) -> tuple[int, int]:
    """商人の不在を見る窓のターン数と、その根拠（商人が去る前に、どれかの財で売りのない間が続いた
    最長のターン数）。窓はふだんの空白の最長より 1 ターン長い。この世界の商人は去るまで毎ターン
    来るので、ふだんの空白は 0 になる（多島の世界の来訪の間隔も、その世界の記録から同じ形で
    決める）。"""
    blank = 0
    goods = {g for sale in offered.values() for g in sale}
    for g in goods:
        turns = sorted(t for t, sale in offered.items() if t < leave and sale.get(g, 0) > 0)
        blank = max([blank] + [b - a - 1 for a, b in zip(turns, turns[1:], strict=False)])
    return blank + 1, blank


def _dead_goods(defs, resources, open_goods: set[str]) -> set[str]:
    """島で手に入らない財: 外から入らず（open_goods にない）、島の資源で作れる作り方がない財。
    作り方の原料が 1 つでも手に入らなければ、その作り方では作れない。島の資源が要る作り方（油井の
    原油）は、資源がなければ使えない。原料が輪になっている所は、作れる側に倒す（行き止まりだけを拾う）。"""
    recipes: dict[str, list] = {}
    for fdef in defs.facilities.values():
        for recipe in fdef.get("recipes", {}).values():
            for g in recipe.get("outputs", {}):
                recipes.setdefault(g, []).append(recipe)
    memo: dict[str, bool] = {}

    def obtainable(good: str, path: frozenset[str]) -> bool:
        if good in open_goods:
            return True
        if good in memo:
            return memo[good]
        if good in path:
            return True
        inner = path | {good}
        found = any(
            r.get("requires_resource") in (None, *resources)
            and all(obtainable(q, inner) for q in r.get("inputs", {}))
            for r in recipes.get(good, [])
        )
        if not path:
            memo[good] = found  # 輪の途中の答え（作れる側に倒した）は残さない
        return found

    return {g for g in defs.goods if not obtainable(g, frozenset())}


def _dead_end_turns(
    defs,
    resources,
    offered: dict[int, dict[str, int]],
    held: dict[int, set[str]],
    makers: dict[int, dict[str, list[tuple]]],
    window: int,
) -> dict[str, dict[int, tuple[str, list[tuple]]]]:
    """原料の行き止まりで作れない財と、そのターン（財 → ターン → (止めた原料, 作り手の様子)）。

    当てるのは次がそろったターンだけ（2026-11-18 掲示板の会議）:
    ① その財も止めた原料も、島に作る手段がない（_dead_goods。今の生産が 0 なだけでは当てない）
    ② 商人の不在は window ターンの窓で見る（窓の間に商人の売りがあれば手に入る）。国の倉庫に荷が
       あっても手に入る
    ③ 島の建った作り手がみな、その原料の品切れ（施設の止まった訳 input:原料）で全力を下回り、
       手元のその原料が全力の 1 ターン分にも足りない。人手・電力・お金のせいの止まりは当てない
    """
    out: dict[str, dict[int, tuple[str, list[tuple]]]] = {}
    cache: dict[frozenset[str], set[str]] = {}
    for t in sorted(makers):
        came = {g for s in range(t - window + 1, t + 1) for g, q in offered.get(s, {}).items() if q}
        open_goods = frozenset(came | held.get(t, set()))
        if open_goods not in cache:
            cache[open_goods] = _dead_goods(defs, resources, set(open_goods))
        dead = cache[open_goods]
        for g, rows in makers[t].items():
            if g not in dead:
                continue
            inputs = {stops.split(status)[1] for _, status, *_ in rows}
            if all(
                stops.split(status)[0] == stops.INPUT
                and stops.split(status)[1] in dead
                and rate < fac.FULL
                and have < need
                for _, status, rate, have, need in rows
            ):
                out.setdefault(g, {})[t] = ("・".join(sorted(inputs)), rows)
    return out


def _excuse_dead_end(rec, initial: dict[str, int], stuck: dict) -> list[str]:
    """原料の行き止まりで作れない間の値段の記録を、値段の目安から外し（rec.price_ratio から抜く）、
    構造化した記録（値段の外れ・止めた原料・作り手の稼働率と手元・回復の有無）を返す。"""
    low, high = gate.PRICE_RANGE
    notes = []
    for g, by_turn in sorted(stuck.items()):
        ratios = rec.price_ratio.get(g, {})
        taken = {t: ratios.pop(t) for t in sorted(by_turn) if t in ratios}
        out = [r for r in taken.values() if not low <= r <= high]
        turns = sorted(by_turn)
        rows = [row for t in turns for row in by_turn[t][1]]
        rates = [rate / fac.FULL for _, _, rate, _, _ in rows]
        haves = [have for *_, have, _ in rows]
        inputs = sorted({by_turn[t][0] for t in turns})
        last = turns[-1]
        later = sorted(t for t in ratios if t > last)
        if later:
            end = taken.get(last, ratios.get(later[0]))
            recovery = (
                f"供給が戻った後 T{later[0]}〜T{later[-1]} の比 {ratios[later[0]]:.2f}→"
                f"{ratios[later[-1]]:.2f}（行き止まりの終わり {end or 0:.2f}）"
            )
        else:
            recovery = "終わりまで戻らず"
        notes.append(
            f"原料の行き止まり {g}: T{turns[0]}〜T{last}（{len(turns)} ターン）、止めた原料 "
            f"{'・'.join(inputs)}、初値 {initial.get(g, 0)}、外れ {len(out)} ターン・最大 "
            f"{max(taken.values(), default=0):.2f} 倍、作り手の稼働率 {min(rates):.3f}〜"
            f"{max(rates):.3f}・原料の手元 {min(haves)}〜{max(haves)}、{recovery}"
        )
    return notes


def _shortage_from(
    offered: dict[int, dict[str, int]], needed: dict[int, dict[str, int]], good: str, leave: int
) -> int | None:
    """商人が去る（leave）前のターンまで、商人の売りの予定が島の施設の全力で要る量を続けて下回る
    ようになった最初のターン（GT-46）。商人が去る前のターンに下回っていなければ None。"""
    start = None
    for t in range(leave - 1, 0, -1):
        need = needed.get(t, {}).get(good, 0)
        if need <= 0 or offered.get(t, {}).get(good, 0) >= need:
            break
        start = t
    return start


def _sellable(defs, config, island, goods) -> dict[str, int]:
    """国の倉庫の、売りに出せる荷（1000 分の 1）: 備蓄の放出の決まり（Market._release_orders）と
    同じく、放出が止まっていれば 0、工事・インフラ・設備の直しの資材と国の穀物の備蓄と定めた
    売り買いの品目は 0、軍の取り置きを引く。荷のない財は入れない。"""
    if not island.policy.warehouse_release:
        return {}
    skip = Market._construction_goods(defs) | set(standing.settings(island))
    skip.add(config.get("food_reserve")["good"])
    keep = autoplan._military_keep(island, config)
    out = {}
    for g in goods:
        if g in skip:
            continue
        q = stock.total(island.warehouse, g) - keep.get(g, 0)
        if q > 0:
            out[g] = q
    return out


def _shortage_turns(
    stored: dict[int, dict[str, int]],
    needed: dict[int, dict[str, int]],
    good: str,
    start: int,
    leave: int,
) -> set[int]:
    """品不足のターン（GT-46、会議 #4429・#4433・#4437）: start で品不足に入り、国の倉庫の売りに
    出せる荷がそのターンの要る量の WAREHOUSE_TURNS ターン分以上のターンが WAREHOUSE_RUN ターン
    続いたら、そのターンから抜ける。抜けた後は、足りないターンが 1 回あればそのターンから入る。
    要る量が NEED_MIN 未満のターンは判定せず、状態を変えない。"""
    short, run, out = True, 0, set()
    for t in range(start, leave):
        need = needed.get(t, {}).get(good, 0)
        if need >= NEED_MIN:
            held = stored.get(t, {}).get(good, 0)
            enough = held >= WAREHOUSE_TURNS * need
            run = run + 1 if short and enough else 0
            if short and run >= WAREHOUSE_RUN:
                short, run = False, 0
            elif not short and not enough:
                short = True
        if short:
            out.add(t)
    return out


def _spans(turns: list[int]) -> list[tuple[int, int]]:
    """続いたターンの並びを (始め, 終わり) に分ける。"""
    spans: list[list[int]] = []
    for t in turns:
        if spans and t == spans[-1][1] + 1:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    return [(a, b) for a, b in spans]


def _last_run(turns: set[int], leave: int) -> int | None:
    """去る前のターン（leave − 1）まで続く品不足の並びの始め。去る前に品不足でなければ None。"""
    if leave - 1 not in turns:
        return None
    t = leave - 1
    while t - 1 in turns:
        t -= 1
    return t


def _check_fuel_shortage(
    farms: dict[int, list[tuple[int, int, int, int]]], start: int, leave: int
) -> None:
    """燃料の品不足の間の確かめ（GT-46）: 燃料の足りない農場があり、足りない分の畑は手作業に戻って
    その農場の要る人手が増える（機械の傷みの方が大きい農場は、もう傷みの分が手作業なので増えない）。
    品不足が進むと手作業の畑の割合が増える。燃料を買った量と手元で賄えていること（在庫がマイナスに
    ならない）は bad_stock で見ている。"""
    window = [t for t in range(start, leave) if farms.get(t)]
    rows = [(t, *row) for t in window for row in farms[t]]
    lacking = [r for r in rows if r[1] < fac.FULL]
    binding = [r for r in lacking if r[1] < r[2]]  # 燃料の手元が機械の設備より少ない

    def manual(t: int) -> float:
        kept = sum(min(share, machines) for share, machines, _, _ in farms[t])
        return 1 - kept / (fac.FULL * len(farms[t]))

    assert window, "燃料の品不足の間に、燃料を使う機械化した農場がない"
    print(
        f"燃料の品不足 T{start}〜T{leave - 1}: 農場とターン {len(rows)}、"
        f"燃料の足りない {len(lacking)}、そのうち燃料が手作業の割合を決めた {len(binding)}、"
        f"手作業の畑の割合 {manual(window[0]):.3f} → {manual(window[-1]):.3f}"
    )
    print(
        "  例（ターン, 燃料の手元, 機械, 人手, 燃料が足りたときの人手）:",
        binding[:3] or lacking[:3],
    )
    assert lacking, "商人の燃料が要る量を下回ったのに、燃料の足りない農場がない"
    assert all(w >= full for *_, w, full in lacking), "燃料が足りないのに要る人手が減った"
    assert any(w > full for *_, w, full in binding), "燃料の足りない畑が手作業に戻っていない"
    assert manual(window[-1]) > manual(window[0]), "品不足が進んでも手作業の畑が増えない"


def test_same_seed_same_result(game):
    a = make_world(game, 3, ["coal", "iron_ore"])
    b = make_world(game, 3, ["coal", "iron_ore"])
    run_with_plan(game, a, 60)
    run_with_plan(game, b, 60)
    assert world_digest(a) == world_digest(b)


def test_island_order_does_not_change_the_result(game):
    a = generate_world(4, 3, game.config, game.defs)
    b = generate_world(4, 3, game.config, game.defs)
    b.islands = {i: b.islands[i] for i in sorted(b.islands, reverse=True)}
    run_with_plan(game, a, 30)
    run_with_plan(game, b, 30)
    assert world_digest(a) == world_digest(b)


def test_saving_and_loading_does_not_change_the_result(game):
    straight = generate_world(5, 2, game.config, game.defs)
    run_with_plan(game, straight, 40)
    saved = generate_world(5, 2, game.config, game.defs)
    run_with_plan(game, saved, 20)
    # サーバーのバックアップと同じく JSON に書いて読み直してから続ける
    loaded = world_from_dict(json.loads(json.dumps(world_to_dict(saved), ensure_ascii=False)))
    # 標準の計画は前のターンの統計と市場の記録を見る。サーバーではこれらは状態の JSON ではなく
    # 表（StatRecord・MarketRecord）にあり、ルールの段は見ないので、ここでは写して続ける
    for iid, island in loaded.islands.items():
        island.stats = dict(saved.islands[iid].stats)
        island.markets = list(saved.islands[iid].markets)
    saved = loaded
    run_with_plan(game, saved, 20)
    assert world_digest(saved) == world_digest(straight)


@pytest.mark.skipif(
    not os.environ.get("HAKONIWA_FULL_GATE"), reason="50島×300ターン。HAKONIWA_FULL_GATE=1 で回す"
)
def test_phase2_gate_50_islands(game):
    world = generate_world(1, 50, game.config, game.defs)
    recorder = gate.Recorder(world, game.defs, game.config)
    run_with_plan(game, world, TURNS, lambda _: recorder.record(world))
    assert recorder.failures() == []
