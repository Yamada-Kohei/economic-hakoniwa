"""物価指数・物価上昇率・平均賃金と、その履歴。

物価指数は家計が買う財（goods.yaml の consumer: true）の値段の連鎖指数です。重みは直近の
ターン（game.yaml の records.cpi_weight_turns）に家計が買った量（記録がまだなければ、この
ターンに買った量）で、建国のときの指数が 100 です。
物価上昇率（年率）は、12 ターン前の指数との比です。12 ターン分の記録がないうちは 0 とします。
"""

from __future__ import annotations

from collections.abc import Mapping

from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import EconomyState

BASE_CPI = 100.0


def basket(defs: Defs) -> list[str]:
    """物価指数に入れる財。"""
    return sorted(g for g, d in defs.goods.items() if d.get("consumer"))


def update_cpi(
    economy: EconomyState,
    defs: Defs,
    config: GameConfig,
    prices: Mapping[str, int],
    bought: Mapping[str, int],
) -> float:
    """このターンの値段 prices と家計が買った量 bought で指数を足し、指数を返す。"""
    keep = config.get("records.history_turns")
    weight_turns = config.get("records.cpi_weight_turns")
    goods = basket(defs)
    previous = {g: economy.price_history.get(g, [prices.get(g, 0)])[-1] for g in goods}
    weights = {g: sum(economy.bought_history.get(g, [])[-weight_turns:]) for g in goods}
    if not any(weights.values()):  # 買った記録がまだない（初めのターン）: このターンに買った量
        weights = {g: bought.get(g, 0) for g in goods}
    if not any(weights.values()):
        weights = dict.fromkeys(goods, 1)
    old = sum(weights[g] * previous[g] for g in goods)
    new = sum(weights[g] * prices.get(g, 0) for g in goods)
    last = economy.cpi_history[-1] if economy.cpi_history else BASE_CPI
    cpi = last * new / old if old > 0 else last
    economy.cpi_history = [*economy.cpi_history, cpi][-keep:]
    for g in sorted(prices):
        economy.price_history[g] = [*economy.price_history.get(g, []), prices[g]][-keep:]
    for g in goods:
        history = [*economy.bought_history.get(g, []), bought.get(g, 0)]
        economy.bought_history[g] = history[-keep:]
    return cpi


def inflation(economy: EconomyState) -> float:
    """直近12ターンの物価上昇率（年率）。記録が足りなければ 0。"""
    history = economy.cpi_history
    if len(history) <= YEAR or history[-1 - YEAR] <= 0:
        return 0.0
    return history[-1] / history[-1 - YEAR] - 1


def real_wage_change(economy: EconomyState) -> float:
    """実質賃金（平均賃金 ÷ 物価指数）の1年前からの変化の割合。記録が足りなければ 0。"""
    wages, cpi = economy.wage_history, economy.cpi_history
    if len(wages) <= YEAR or len(cpi) <= YEAR:
        return 0.0
    before = wages[-1 - YEAR] / cpi[-1 - YEAR]
    now = wages[-1] / cpi[-1]
    return now / before - 1 if before > 0 else 0.0
