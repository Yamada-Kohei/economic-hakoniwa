"""家計の層。

層は game.yaml の household.layers で決めます（フェーズ2-1 は一般の労働者 general と資産を持つ層
owners。技術者の層は大学と一緒にフェーズ2-3 で足します）。share を書いた層（資産を持つ層）は
住民のその割合の人数を島全体として持ち、働かずに配当と預金の利子で暮らします。残りの住民は
一般の層です。民営の施設を持たない経済体制（計画経済）の島には、資産を持つ層を置きません。

層ごとの状態（economy.households[層]）:
- persons: 人数
- share: 住民に対する割合（share を持つ層だけ）
- income: 直近のターンの可処分所得（賃金 − 所得税 ＋ 失業手当 ＋ 預金の利子 ＋ 配当）。古い順
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL, household, layers
from hakoniwa_core.state import OWNER_PRIVATE, IslandState
from hakoniwa_core.units import BASIS, round_money


def has_private_owners(defs: Defs, island: IslandState) -> bool:
    """民営の施設がありうる経済体制か（資産を持つ層を置くか）。"""
    economy = defs.regimes.get(island.regime.get("economy", ""), {})
    return economy.get("owner") == OWNER_PRIVATE


def init_layers(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """層を作り、人数を住民の数から決める。"""
    population = sum(t.population for t in island.tiles)
    households: dict[str, dict[str, Any]] = {}
    for name, spec in layers(config).items():
        if "share" in spec:
            if not has_private_owners(defs, island):
                continue
            households[name] = {"persons": 0, "share": spec["share"], "income": []}
        else:
            households[name] = {"persons": 0, "income": []}
    island.economy.households = households
    split_persons(island, population)


def split_persons(island: IslandState, population: int) -> dict[str, int]:
    """住民 population を層に割り振り、前のターンからの人数の変化を返す。"""
    households = island.economy.households
    changes = {}
    fixed = 0
    for name in sorted(households):
        h = households[name]
        if "share" in h:
            persons = round_money(population * h["share"] + 0.5)
            changes[name] = persons - h["persons"]
            h["persons"] = persons
            fixed += persons
    general = households[GENERAL]
    changes[GENERAL] = population - fixed - general["persons"]
    general["persons"] = population - fixed
    return changes


def move_people(island: IslandState, changes: dict[str, int]) -> None:
    """層を移った人の預金を、元の層の1人あたりの額で一緒に移す（仕訳で付け替える）。

    share の層の人数が増えた分は一般の層から、減った分は一般の層へ移ったと考える。
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
