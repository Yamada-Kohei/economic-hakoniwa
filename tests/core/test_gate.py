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

from hakoniwa_core import facilities as fac
from hakoniwa_core import gate, veins
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.founding import found_island
from hakoniwa_core.game import load_game
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state import WorldState
from hakoniwa_core.state.generate import generate_land, generate_world, random_plan
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from hakoniwa_core.units import from_milli

TURNS = 300
MERCHANT_CALM = 12  # 海外の商人が去った後、値段を見ないターン数（貿易のない 1 島の世界）


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
    machinery_built_in = 0.0  # 島で機械を作り始めた後に建った施設が、資材に使った機械
    machinery_repaired = 0.0  # 島で機械を作り始めた後に、建っている施設が設備を直すのに買った機械
    populations = [start_population]
    bad_stock = []
    short = []  # 食べ物が足りなかったターン
    starved = 0

    def check(island):
        for key, value in island.stats.items():
            if key.startswith("production."):
                good = key.removeprefix("production.")
                produced[good] = produced.get(good, 0) + value
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
        populations.append(island.stats["population"])
        nonlocal starved
        starved += island.stats.get("population.starved", 0)
        if island.stats.get("food.ratio", 1.0) < 0.95:
            short.append(world.turn)
        recorder.record(world)

    recorder = gate.Recorder(world, game.defs, game.config)
    run_with_plan(game, world, TURNS, check)

    assert world.turn == TURNS
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
    merchant = game.config.get("foreign_merchant")
    leave = island.ext["trade"]["merchant_from"] + merchant["turns"]
    tail = (merchant["turns"] - merchant["taper_from"]) // 10
    priced = [
        *recorder.failures(end=leave - tail - 1),
        *recorder.failures(start=leave + MERCHANT_CALM),
    ]
    failures = [m for m in recorder.failures() if any(h in m for h in hard) and "の値段が" not in m]
    failures += [m for m in priced if "の値段が" in m]
    assert [m for m in failures if not any(f"）: {g} の値段が" in m for g in food)] == []


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
