"""兵と軍の維持（フェーズ4-2 の②。設計書 15-military の MI-07〜14・MI-53）。

兵は軍が雇う公務の働き手です。兵はマスの人口から抜いて島全体の数（`island.ext["military"]`）として持ち
（MI-53）、家計の食べ物の数にも雇用の段の働き手にも数えません。志願は失業者から先に集まり、徴兵は人口に
比例してくじで集めます。賃金は国防費の `defense_personnel` から払い、払えない分は未払い賃金に
残します（MI-13・MI-14）。食べ物は国の倉庫から食べ、足りない分は国として市場で買います（MI-10）。

状態（`state_of`）:
- soldiers: 兵の数（志願 volunteers と徴兵 conscripts の合計）
- morale / training: 士気と練度（1万分の1。全員の平均）
- late: 賃金が遅れているターン数、hungry: 食べ物が足りなかったか
- new: このターンに入ったばかりの兵の数（食べ物の買い入れが間に合わず、入ったターンは家で食べる）
- volunteer_wage / conscript_wage: 賃金の倍率（島の平均賃金に対する率、1万分の1）
部隊と艦隊に分けた持ち方は 4-2 の③で足します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import BASIS, to_basis

KEY = "military"
BUDGET_PERSONNEL = "defense_personnel"  # 兵の賃金と食べ物（予算の項目）
BUDGET_PROCUREMENT = "defense_procurement"  # 装備の買い入れと編成（予算の項目。③以降）
BUDGETS = (BUDGET_PERSONNEL, BUDGET_PROCUREMENT)


def state_of(island: IslandState, config: GameConfig | None = None) -> dict[str, Any]:
    """島の軍の状態（なければ置く）。config を渡すと賃金の倍率の初めの値を置く。"""
    state = island.ext.setdefault(KEY, {})
    state.setdefault("volunteers", 0)
    state.setdefault("conscripts", 0)
    state.setdefault("morale", 0)
    state.setdefault("training", 0)
    state.setdefault("late", 0)
    state.setdefault("hungry", False)
    state.setdefault("new", 0)
    if config is not None:
        state.setdefault("volunteer_wage", to_basis(config.get("military.volunteer_wage")))
        state.setdefault("conscript_wage", to_basis(config.get("military.conscript_wage")))
    return state


def record_purchase(
    island: IslandState, turn: int, good: str, wanted: int, filled: int, why: str | None
) -> bool:
    """国として買う注文（兵の食べ物・弾薬・装備。MI-10・W-184）の、このターンの結果を残す。wanted は
    買いたかった量、filled は約定した量（1000 分の 1 単位）、why は足りなかったわけ（budget・
    treasury・limit・no_sellers・few_sellers。足りていれば None）。同じ財の注文が 2 つあれば足す。
    足りないわけが新しく出た（前のターンは足りていたか、わけが違う）ときだけ True を返す
    （ログに出す）。"""
    state = state_of(island)
    rows = state.setdefault("purchases", {})
    row = rows.get(good)
    if row is None:
        row = rows[good] = {"turn": turn, "wanted": 0, "filled": 0, "why": None}
    row["wanted"] += wanted
    row["filled"] += filled
    if why is not None:
        row["why"] = why
    seen = state.setdefault("purchase_short", {})
    if why is None:
        if row["why"] is None:
            seen.pop(good, None)
        return False
    new = seen.get(good) != why
    seen[good] = why
    return new


def soldiers(island: IslandState) -> int:
    """兵の数。"""
    state = island.ext.get(KEY)
    return state["volunteers"] + state["conscripts"] if state else 0


def population_of(island: IslandState) -> int:
    """マスの住民の数（兵は含まない）。"""
    return sum(t.population for t in island.tiles if t.population > 0)


# --- 人の出入り（MI-53） --------------------------------------------------------------


def take_people(island: IslandState, count: int, keep: int) -> int:
    """マスの住民から count 人を、人口に比例して抜く（各マスに keep 人は残す）。抜いた数を返す。"""
    pools = [(i, t.population - keep) for i, t in enumerate(island.tiles) if t.population > keep]
    room = sum(p for _, p in pools)
    count = min(count, room)
    if count <= 0:
        return 0
    given: dict[int, int] = {}
    left = count
    for i, pool in pools:  # 切り捨てた分は、あとで余りの大きい順に配る
        share = count * pool // room
        given[i] = share
        left -= share
    for i, pool in sorted(pools, key=lambda p: (-(count * p[1] % room), p[0])):
        if left <= 0:
            break
        if given[i] < pool:
            given[i] += 1
            left -= 1
    for i, n in given.items():
        island.tiles[i].population -= n
    return count


def return_people(island: IslandState, count: int) -> int:
    """兵を家計に戻す。住民のいるマスへ、人口に比例して戻す。戻した人数を返す。"""
    pools = [(i, t.population) for i, t in enumerate(island.tiles) if t.population > 0]
    whole = sum(p for _, p in pools)
    if count <= 0 or whole <= 0:
        return 0
    given, left = {}, count
    for i, pool in pools:
        given[i] = count * pool // whole
        left -= given[i]
    for i, _ in sorted(pools, key=lambda p: (-(count * p[1] % whole), p[0])):
        if left <= 0:
            break
        given[i] += 1
        left -= 1
    for i, n in given.items():
        island.tiles[i].population += n
    return count


def _blend(old: int, old_n: int, new: int, new_n: int) -> int:
    """兵の数で重みを付けた平均（士気と練度）。"""
    whole = old_n + new_n
    return (old * old_n + new * new_n) // whole if whole else 0


# --- 志願・徴兵・除隊（命令） -----------------------------------------------------------


def volunteer_limit(
    island: IslandState, config: GameConfig, unemployed: int, wage_ratio: int
) -> int:
    """志願で今集められる人数。失業者は軍の賃金に関わらず集まり、賃金が島の平均賃金以上なら、
    働いている人からも人口の military.recruit_share まで集まる（賃金が低いと集まらない。MI-08）。"""
    state = island.ext.get(KEY, {})
    room = max(0, _room(island, config) - soldiers(island))
    if wage_ratio >= BASIS:
        employed_pool = int(population_of(island) * config.get("military.recruit_share"))
        pool = max(unemployed, employed_pool)
    else:
        pool = unemployed
    return min(room, max(0, pool - state.get("new", 0)))  # 1 ターンの上限（入ったばかりの分を引く）


def _room(island: IslandState, config: GameConfig) -> int:
    """兵の数の上限（住民と兵の合計に対する military.max_share）。"""
    whole = population_of(island) + soldiers(island)
    return int(whole * config.get("military.max_share"))


def recruit(island: IslandState, config: GameConfig, count: int, unemployed: int) -> int:
    """志願の兵を count 人まで集める。集めた人数を返す。"""
    state = state_of(island, config)
    wanted = min(count, volunteer_limit(island, config, unemployed, state["volunteer_wage"]))
    got = take_people(island, wanted, config.get("military.keep_per_tile"))
    _add(state, got, volunteer=True, config=config)
    return got


def conscript(island: IslandState, config: GameConfig, count: int) -> int:
    """徴兵で count 人まで集める（1 回に人口の military.conscript_share まで）。集めた数を返す。"""
    state = state_of(island, config)
    room = max(0, _room(island, config) - soldiers(island))
    cap = int(population_of(island) * config.get("military.conscript_share")) - state["new"]
    got = take_people(island, max(0, min(count, room, cap)), config.get("military.keep_per_tile"))
    _add(state, got, volunteer=False, config=config)
    return got


def _add(state: dict[str, Any], got: int, volunteer: bool, config: GameConfig) -> None:
    if got <= 0:
        return
    old = state["volunteers"] + state["conscripts"]
    start = to_basis(config.get("military.morale.start"))
    state["morale"] = _blend(state["morale"], old, start, got)
    state["training"] = _blend(state["training"], old, 0, got)
    state["volunteers" if volunteer else "conscripts"] += got
    state["new"] += got


def discharge(island: IslandState, count: int, conscripts_first: bool = True) -> int:
    """兵を count 人まで除隊させ、家計に戻す。徴兵した兵から先に帰す。除隊させた人数を返す。"""
    state = state_of(island)
    count = min(count, soldiers(island))
    if count <= 0 or population_of(island) <= 0:  # 戻す先のマスがなければ除隊できない
        return 0
    order = ("conscripts", "volunteers") if conscripts_first else ("volunteers", "conscripts")
    left = count
    for key in order:
        n = min(left, state[key])
        state[key] -= n
        left -= n
    return return_people(island, count)


def lose(island: IslandState, count: int) -> int:
    """兵が亡くなる・逃げる分を、徴兵した兵から先に引く（除隊と違い、家計には戻さない）。"""
    state = state_of(island)
    count = min(count, soldiers(island))
    left = count
    for key in ("conscripts", "volunteers"):
        n = min(left, state[key])
        state[key] -= n
        left -= n
    return count
