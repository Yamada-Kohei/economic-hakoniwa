"""家計の層。

層は game.yaml の household.layers で決めます（一般の労働者 general、資産を持つ層 owners、
フェーズ2-3 からの技術者の層 engineers）。share を書いた層（資産を持つ層）は住民のその割合の人数を
島全体として持ち、働かずに配当と預金の利子で暮らします。民営の施設を持たない経済体制（計画経済）の
島には、資産を持つ層を置きません。workers を書いた層（技術者の層）は、島全体の働き手の人数
（economy.workers）を家族ごと持ちます（人数 ÷ workers.yaml の share。働き手 0.5 なら2倍）。
住民が減ると、働き手も同じ割合で減ります。残りの住民は一般の層です。

層ごとの状態（economy.households[層]）:
- persons: 人数
- share: 住民に対する割合（share か workers を持つ層だけ）
- workers: 層の働き手の種類（workers を持つ層だけ）
- income: 直近のターンの可処分所得（賃金 − 所得税 ＋ 失業手当 ＋ 預金の利子 ＋ 配当）。古い順
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL, household, layers
from hakoniwa_core.facilities import ENGINEER
from hakoniwa_core.state import OWNER_PRIVATE, IslandState
from hakoniwa_core.units import BASIS, round_money


def has_private_owners(defs: Defs, island: IslandState) -> bool:
    """民営の施設がありうる経済体制か（資産を持つ層を置くか）。"""
    economy = defs.regimes.get(island.regime.get("economy", ""), {})
    return economy.get("owner") == OWNER_PRIVATE


def new_worker_layer(kind: str) -> dict[str, Any]:
    """島全体の働き手 kind の層の初めの状態。"""
    return {"persons": 0, "share": 0.0, "workers": kind, "income": []}


def init_layers(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """層を作り、人数を住民の数から決める。"""
    population = sum(t.population for t in island.tiles)
    households: dict[str, dict[str, Any]] = {}
    for name, spec in layers(config).items():
        if "workers" in spec:
            households[name] = new_worker_layer(spec["workers"])
        elif "share" in spec:
            if not has_private_owners(defs, island):
                continue
            households[name] = {"persons": 0, "share": spec["share"], "income": []}
        else:
            households[name] = {"persons": 0, "income": []}
    island.economy.households = households
    split_persons(island, defs, population)


def family_of(defs: Defs, kind: str, count: int) -> int:
    """働き手 count 人の家族を合わせた人数（count ÷ workers.yaml の share）。"""
    share = defs.workers[kind].get("share", 1.0)
    return round_money(count / share + 0.5) if share > 0 else count


def split_persons(island: IslandState, defs: Defs, population: int) -> dict[str, int]:
    """住民 population を層に割り振り、前のターンからの人数の変化を返す。

    住民が前より減っていれば、島全体の働き手（技術者）も同じ割合で減らす。
    """
    households = island.economy.households
    before = sum(h["persons"] for h in households.values())
    changes = {}
    fixed = 0
    for name in sorted(households):
        h = households[name]
        if "workers" in h:
            kind = h["workers"]
            count = island.economy.workers.get(kind, 0)
            if 0 <= population < before and count > 0:
                shrunk = count * population // before
                island.economy.workers[kind] = shrunk
                if kind == ENGINEER:
                    recount_engineers(island, count, 0, count - shrunk)
                count = shrunk
            persons = min(max(0, population - fixed), family_of(defs, kind, count))
            h["share"] = persons / population if population > 0 else 0.0
        elif "share" in h:
            persons = round_money(population * h["share"] + 0.5)
        else:
            continue
        changes[name] = persons - h["persons"]
        h["persons"] = persons
        fixed += persons
    general = households[GENERAL]
    changes[GENERAL] = population - fixed - general["persons"]
    general["persons"] = population - fixed
    return changes


def set_workers(island: IslandState, defs: Defs, kind: str, count: int) -> None:
    """島全体の働き手 kind の人数を count にし、層の人数を割り振り直して預金を一緒に移す
    （大学で育った人・引退した人）。"""
    island.economy.workers[kind] = max(0, count)
    population = sum(h["persons"] for h in island.economy.households.values())
    move_people(island, split_persons(island, defs, population))


def recount_engineers(island: IslandState, pool: int, grown: int, retired: int) -> None:
    """育成・引退と住民の減少で変わった技術者の数を、雇用の段で数えた「働いている技術者」と
    「仕事のない技術者」（このターンの記録に出す）に映す。減った人は両方から人数の割合で抜け、
    育った人は仕事のない側に入る（映さないと、働いている人が総数を超えるターンが出る）。"""
    scratch = island.scratch
    if "engineers_employed" not in scratch:
        return
    employed = scratch["engineers_employed"]
    left = scratch.get("engineers_left", 0)
    out = min(employed, retired * employed // pool) if pool > 0 else 0
    scratch["engineers_employed"] = employed - out
    scratch["engineers_left"] = max(0, left - (retired - out)) + grown


def move_people(island: IslandState, changes: dict[str, int]) -> None:
    """層を移った人の預金を、元の層の1人あたりの額で一緒に移す（仕訳で付け替える）。

    share（と workers）の層の人数が増えた分は一般の層から、減った分は一般の層へ移ったと考える。
    """
    for name in sorted(changes):
        if name == GENERAL or not changes[name]:
            continue
        moved = changes[name]
        source, target = (GENERAL, name) if moved > 0 else (name, GENERAL)
        persons = island.economy.households[source]["persons"] + abs(moved)
        if persons <= 0:
            continue
        payer = household(source)
        amount = ledger.money(island, payer) * abs(moved) // persons
        ledger.pay(island, payer, household(target), amount, "layer_transfer")


def add_income(island: IslandState, layer: str, amount: int) -> None:
    """このターンの層の可処分所得に足す（所得税は負で足す）。"""
    if amount:
        income = island.scratch.setdefault("income", {})
        income[layer] = income.get(layer, 0) + amount


def close_income(island: IslandState, keep: int) -> None:
    """ターンの終わりに、このターンの可処分所得を層ごとの記録に足す。"""
    income = island.scratch.get("income", {})
    for name, h in island.economy.households.items():
        h["income"] = [*h["income"], income.get(name, 0)][-keep:]


def layer_of(entity: str) -> str | None:
    """主体の名前が家計の層なら層の名前。"""
    return entity[3:] if entity.startswith("hh:") else None


def consumption(island: IslandState, layer: str, config: GameConfig, inflation: float) -> int:
    """層が使う額: C = c1 × Ȳ + c2 × M（名前付きの式 consumption の既定）。

    Ȳ は直近のターンの可処分所得の平均（記録がなければこのターンの所得）、M は層の預金。
    c1 は預金の実質金利（預金金利 − 物価上昇率）が1%高いごとに c1_rate_effect 下げる
    （既定の値から ± c1_range）。
    """
    spec = layers(config)[layer]
    settings = config.get("household")
    h = island.economy.households[layer]
    history = h["income"][-settings["income_turns"] :]
    if history:
        income = sum(history) / len(history)
    else:
        income = island.scratch.get("income", {}).get(layer, 0)
    real_rate = island.policy.deposit_rate / BASIS - inflation
    shift = settings["c1_rate_effect"] * real_rate * 100
    limit = settings["c1_range"]
    c1 = spec["c1"] - max(-limit, min(limit, shift))
    deposits = ledger.money(island, household(layer))
    return max(0, round_money(c1 * max(0.0, income) + settings["c2"] * deposits))
