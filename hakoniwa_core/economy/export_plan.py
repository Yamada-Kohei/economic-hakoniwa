"""計画経済の輸出の計画（PL-62・FX-77。2026-11-13、計画経済の電気不足の案 3）。

国営の商社が輸入に使えるのは輸出で得た外貨だけなのに、輸出は生産目標の売れ残りだけだったので、
石炭も機械も作れない島は燃料と設備の資材を買う外貨がなくなり、発電所が止まっていた。そこで国が
要る輸入の額を見積もり、その外貨を稼ぐ輸出の量を生産目標に足す（現実のソ連の国家計画委員会の
外貨の計画と同じ形）。貿易の段の終わり（圏の市場と商社の狙う量の見直しの後）に、計画経済の島ごとに
次のターンの計画を決める（update）。

- 要る外貨（通貨ごと）: 外貨を使う順の 1 段の財（goods.yaml の fx_priority=1。燃料と主食）の「商社が
  輸入できた量 ＋ 島の市場の行列」と、国営施設の設備の資材（fx_priority=2）の「輸入できた量 ＋
  行列」を 1 ターンに傷む分までにした量を、一番安く輸入できる圏の値段で、その圏の通貨で数える
  （本人の表の 3-1。消費財と直し残しは入れない）。自国の通貨の圏は外貨が要らないので数えない。
- 稼ぐ額 ＝ 要る外貨 ＋（蓄えの目標 − 商社の持つ外貨）÷ planning.export.draw_turns − 売れ残りの輸出
  の稼ぎ（直近の平均）。蓄えの目標 ＝ 要る外貨 × planning.export.reserve_turns（本人の B）。0 未満
  は 0。前のターンに価格差の補給金を払いきれなかった島は、前のターンの計画の分の稼ぎより上げない。
- 候補: 国営施設が作っていて余った設備（傷みを除いた全力の産出 × 前のターンの達成率 − 国内の生産
  目標）のある財のうち、その通貨の圏で値段があり、島に行列がなく、商社が輸入していない財。圏の値段が直近
  の平均より trade.bloc_band 以上下がっている財は外す。外貨の効率（圏の値段を自国通貨に直した額 − 作
  るのに輸入する原料の額）÷ 原価 の高い順に、余り・圏で売れる見込み（FX-54）・残りの稼ぐ額の一番小さ
  い量を積む。扱える重さは、1 段の財の輸入の見込みを先に取り置いた残り。
- 命令 set_export_plan（本人の表の 2-2）: 財ごとに量（余りまで。稼ぐ額と売れる見込みでは縮めない）か
  輸出しない（0）を名指しできる。名指しの分の稼ぎを稼ぐ額から引いた残りを自動で選ぶ。
- 計画の量は前のターンの量から trade.target_speed の割合だけ新しい量に近づけ（商社の狙う量と同じ
  形）、圏で売れ残って島の市場に戻した荷の量を引く。
- 商社: 財ごとの計画の量を、その財を輸出している（いなければ荷の少ない）動いている商社に分け、
  ``traders.PLAN_X`` に置く。商社は島の市場に計画の分を別の注文で出し、その注文は食料も国営施設と
  同じ順位で配られる（本人の表の 1-2。modules/market）。生産目標の需要の履歴からは除く。
- 蓄え（本人の B）: 蓄えが目標より少ない間は、外貨を使う順の 3・4 段の輸入に目標を超える分しか使わな
  い（reserve_of。modules/trade の _fx_ahead）。
- 中央銀行の外貨は使わない。

島の状態は ``island.ext["plan"]["export"]``:
- plan: 財 → 計画の量（1000 分の 1）、named: 命令で名指しした財 → 量（0 は輸出しない）
- need・earn・reserve・held: 通貨の番号の文字列 → 要る外貨・稼ぐ額・蓄えの目標・持つ外貨（この
  ターン）
- needs: 通貨 → {"fuel": 1 段の分, "equipment": 設備の分}（画面の内訳）
- rows: 財 → {currency, price, eff, sold}（画面）、bloc: 財 → 輸出する圏の番号
- unsold: 通貨 → 売れ残りの輸出の稼ぎの履歴、planned: 通貨 → 前のターンの計画の分の稼ぎ
- idle: 稼ぐ額があるのに候補のないターンの数、noticed: そのことを知らせた
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import entity_of
from hakoniwa_core.economy import equipment as gear
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.state import OWNER_STATE, IslandState, LogEntry, WorldState
from hakoniwa_core.trade import blocs as blocs_
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import fx_priority, tradable_goods, traders, traders_of, unit_weight
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, money_of, round_money, to_basis

KEY = planning.EXPORT
IDLE_NOTICE = 3  # 稼ぐ額があるのに候補のないターンがこれだけ続いたら1度だけ知らせる
YEAR = 12


def state_of(island: IslandState) -> dict[str, Any]:
    return planning.state_of(island).setdefault(KEY, {})


def plans(island: IslandState) -> dict[str, int]:
    """財 → 輸出の計画の量（次のターン）。"""
    return island.ext.get(planning.KEY, {}).get(KEY, {}).get("plan", {})


def named(island: IslandState) -> dict[str, int]:
    """命令で名指しした財 → 量（0 は輸出しない）。"""
    return island.ext.get(planning.KEY, {}).get(KEY, {}).get("named", {})


def reserve_of(island: IslandState, config: GameConfig, entity: str, currency: int) -> int:
    """本人の B: 主体 entity の通貨 currency の持ち高のうち、外貨を使う順の 3・4 段の輸入に使わず
    残す額（蓄えの目標を、島の国営の商社の持ち高の比で分けた分。持ち高まで）。計画経済でない島と
    自国の通貨は 0。"""
    if currency == island.id or not traders.is_planned(island):
        return 0
    target = island.ext.get(planning.KEY, {}).get(KEY, {}).get("reserve", {}).get(str(currency), 0)
    if target <= 0:
        return 0
    mine = fxmoney.holding(island, entity, currency)
    if mine <= 0:
        return 0
    total = sum(fxmoney.holding(island, e, currency) for e in _entities(island, config))
    return min(mine, target * mine // max(1, total, mine))


def _entities(island: IslandState, config: GameConfig) -> list[str]:
    """島の国営の商社のお金を持つ主体（重なりなし）。"""
    out: list[str] = []
    for f in traders_of(island, config):
        if f.owner != OWNER_STATE:
            continue
        e = entity_of(config, f)
        if e not in out:
            out.append(e)
    return out


# --- このターンの様子（商社の狙う量の見直しの前に読む） --------------------------------


def observe(island: IslandState, config: GameConfig) -> dict[str, Any]:
    """このターンの商社の記録（state_of(f)["turn"]。update_targets が消す前に読む）から、財ごとの
    輸入して島で売れた量・圏で売れた量と稼ぎ・圏で売れ残って島に戻した量を数える。"""
    imported: dict[str, int] = {}
    exported: dict[str, int] = {}
    returned: dict[str, int] = {}
    proceeds: dict[str, dict[str, int]] = {}
    for f in traders_of(island, config):
        turn = traders.state_of(f).get("turn", {})
        goods = traders.goods_of(f)
        for good, qty in turn.get("sold", {}).items():
            side = goods.get(good, {}).get("dir")
            into = returned if side == traders.EXPORT else imported
            into[good] = into.get(good, 0) + qty
        for good, qty in turn.get("exported", {}).items():
            exported[good] = exported.get(good, 0) + qty
        for good, by in turn.get("proceeds", {}).items():
            row = proceeds.setdefault(good, {})
            for c, amount in by.items():
                row[c] = row.get(c, 0) + amount
    return {"imported": imported, "exported": exported, "returned": returned, "proceeds": proceeds}


def shares(world: WorldState) -> dict[tuple[int, str], int]:
    """(圏, 財) → 前のターンに計画のあった島の計画の量の和（圏の買えなかった量を計画の比で
    分ける）。"""
    out: dict[tuple[int, str], int] = {}
    for island in world.islands.values():
        state = island.ext.get(planning.KEY, {}).get(KEY, {})
        where = state.get("bloc", {})
        for good, qty in state.get("plan", {}).items():
            b = where.get(good)
            if b is not None and qty > 0:
                out[(b, good)] = out.get((b, good), 0) + qty
    return out


# --- 計画を決める ---------------------------------------------------------------------


def update(
    world: WorldState,
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    turn: int,
    seen: Mapping[str, Any],
    bloc_unmet: Mapping[tuple[int, str], int],
    bloc_sellers: Mapping[tuple[int, str], int],
    planned_by_bloc: Mapping[tuple[int, str], int],
) -> None:
    """貿易の段の終わりに、計画経済の島の次のターンの輸出の計画を決め、商社に分ける。"""
    if not traders.is_planned(island):
        _clear_traders(island, config)
        return
    state = state_of(island)
    found = traders.prospects(island, defs, config)
    records = {m.good: m for m in island.markets}
    working = traders_of(island, config, turn)
    if not found or not working:
        _forget(state)
        _assign(island, defs, config, {}, found, working)
        return
    _keep_unsold(state, config, seen)
    needs, parts = _needs(island, defs, config, found, records, seen)
    held = {
        c: sum(fxmoney.holding(island, e, int(c)) for e in _entities(island, config)) for c in needs
    }
    reserve_turns = config.get("planning.export.reserve_turns")
    draw = max(1, config.get("planning.export.draw_turns"))
    short_gap = bool(island.scratch.get(traders.PRICE_GAP_SHORT))
    earn: dict[str, int] = {}
    reserve: dict[str, int] = {}
    for c, need in sorted(needs.items()):
        reserve[c] = round_money(need * reserve_turns)
        history = state.get("unsold", {}).get(c, [])
        unsold = sum(history) // len(history) if history else 0
        value = need + (reserve[c] - held.get(c, 0)) // draw - unsold
        if short_gap:  # 補給金を払いきれなかった: 前のターンの計画の分の稼ぎより上げない
            value = min(value, state.get("planned", {}).get(c, 0))
        earn[c] = max(0, value)
    goals = _choose(
        world,
        island,
        defs,
        config,
        found,
        seen,
        earn,
        working,
        bloc_unmet,
        bloc_sellers,
        planned_by_bloc,
    )
    # 計画の量は前のターンの量から trade.target_speed の割合だけ近づける（名指しは近づけない）。
    # 圏で売れ残って島に戻した荷は、次のターンの量から引く（行って戻るだけにしない）
    speed = config.get("trade.target_speed")
    before = state.get("plan", {})
    names = named(island)
    plan: dict[str, int] = {}
    for good in sorted(set(before) | set(goals)):
        goal = goals.get(good, 0)
        qty = goal if good in names else _approach(before.get(good, 0), goal, speed)
        qty -= seen["returned"].get(good, 0)
        if qty > 0 and good in goals:
            plan[good] = qty
    state["plan"] = plan
    state["bloc"] = {g: found[g].export_bloc for g in plan if found[g].export_bloc is not None}
    state["need"], state["needs"], state["earn"] = needs, parts, earn
    state["reserve"], state["held"] = reserve, held
    state["planned"] = _planned_value(island, found, plan)
    _notice(island, state, earn, plan)
    _assign(island, defs, config, plan, found, working)
    # 画面と達成率に使う生産目標も、新しい計画の量で書き直す
    goals_now = planning.state_of(island).setdefault("goals", {})
    for good in sorted(set(goals_now) | set(plan)):
        goal = planning.goal_of(island, config, good)
        if goal:
            goals_now[good] = goal
        else:
            goals_now.pop(good, None)
    island.stats["plan.export_goods"] = len(plan)
    island.stats["plan.export_earn"] = sum(
        fxrates.to_own(island, int(c), v) for c, v in earn.items()
    )


def _forget(state: dict[str, Any]) -> None:
    """港がない・圏に入っていない・商社がない: 計画はなく、今までどおり売れ残りだけを輸出する。"""
    for key in ("plan", "bloc", "need", "needs", "earn", "reserve", "held", "planned", "rows"):
        state.pop(key, None)


def _keep_unsold(state: dict[str, Any], config: GameConfig, seen: Mapping[str, Any]) -> None:
    """このターンの売れ残りの輸出の稼ぎ（圏で売れた量のうち計画の量を超えた分の稼ぎ）を通貨ごとの
    履歴に足す。"""
    plan = state.get("plan", {})
    got: dict[str, int] = {}
    for good, by in seen["proceeds"].items():
        sold = seen["exported"].get(good, 0)
        if sold <= 0:
            continue
        extra = max(0, sold - plan.get(good, 0))
        for c, amount in by.items():
            got[c] = got.get(c, 0) + amount * extra // sold
    keep = config.get("planning.demand_turns")
    history = state.setdefault("unsold", {})
    for c in sorted(set(history) | set(got)):
        history[c] = [*history.get(c, []), got.get(c, 0)][-keep:]
        if not any(history[c]):
            del history[c]


def _needs(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    found: Mapping[str, traders.Prospect],
    records: Mapping[str, Any],
    seen: Mapping[str, Any],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """要る外貨（通貨の番号の文字列 → 額）と、その内訳（1 段の分と設備の分）。"""
    wear = _monthly_wear(island, defs, config)
    needs: dict[str, int] = {}
    parts: dict[str, dict[str, int]] = {}
    for good in tradable_goods(defs):
        rank = fx_priority(defs, good)
        if rank > 2:
            continue
        p = found.get(good)
        if p is None or p.import_bloc is None or p.import_currency in (None, island.id):
            continue
        record = records.get(good)
        queue = max(0, record.demand - record.volume) if record is not None else 0
        qty = seen["imported"].get(good, 0) + queue
        if rank == 2:  # 設備の資材は 1 ターンに傷む分まで（直し残しは入れない）
            qty = min(qty, wear.get(good, 0))
        if qty <= 0:
            continue
        value = money_of(p.import_price, qty)
        c = str(p.import_currency)
        needs[c] = needs.get(c, 0) + value
        part = parts.setdefault(c, {"fuel": 0, "equipment": 0})
        part["fuel" if rank == 1 else "equipment"] += value
    return needs, parts


def _monthly_wear(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """国営施設の設備が 1 ターンに傷む量（資材 → 量）。"""
    out: dict[str, float] = {}
    for f in island.facilities:
        if f.owner != OWNER_STATE or not gear.has_equipment(defs, config, f):
            continue
        for good, qty in gear.kit_of(defs, f).items():
            out[good] = out.get(good, 0.0) + qty * gear.wear_rate(config, good) / YEAR
    return {g: round_money(q) for g, q in out.items() if q > 0}


def _choose(
    world: WorldState,
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    found: Mapping[str, traders.Prospect],
    seen: Mapping[str, Any],
    earn: Mapping[str, int],
    working: list[Any],
    bloc_unmet: Mapping[tuple[int, str], int],
    bloc_sellers: Mapping[tuple[int, str], int],
    planned_by_bloc: Mapping[tuple[int, str], int],
) -> dict[str, int]:
    """財 → このターンに決めた計画の量（近づける前）。名指しを先に、残りを効率の順に。"""
    state = state_of(island)
    plan_state = planning.state_of(island)
    queues = plan_state.get("queues", {})
    achieved = plan_state.get("achieved", {})
    caps = planning.capacities(island, defs, config)
    costs = plan_state.get("costs", {})
    prices = planning.official(island)
    imported = traders.imported(island, config)
    names = named(island)
    before = state.get("plan", {})
    band = to_basis(config.get("trade.bloc_band"))
    recent = config.get("planning.demand_turns")
    weight_left = sum(
        config.get("trade.trader_capacity") * QUANTITY_SCALE - traders.first_import_weight(defs, f)
        for f in working
    )
    rows: dict[str, dict[str, Any]] = {}
    candidates: list[tuple[float, str, int, int, str]] = []  # (−効率, 財, 余り, 値段, 通貨)
    for good in tradable_goods(defs):
        p = found.get(good)
        if p is None or p.export_bloc is None or p.export_currency is None:
            continue
        if p.export_currency == island.id or names.get(good) == 0:
            continue
        cap = caps.get(good, 0)
        if cap <= 0:
            continue
        rate = min(BASIS, round(achieved.get(good, 1.0) * BASIS))  # 達成率は割合（1 が 100%）
        domestic = planning.domestic_goal(island, config, good) or 0
        room = cap * rate // BASIS - domestic
        if room <= 0:
            continue
        if good in imported:
            continue
        price = traders.views(island).get(str(p.export_bloc), {}).get("prices", {}).get(good, 0)
        if price <= 0:
            continue
        own = price * fxrates.rate(island, p.export_currency) // BASIS
        net = own - _imported_inputs(island, defs, good, found, imported)
        cost = costs.get(good) or prices.get(good) or 0
        eff = net / cost if cost > 0 else 0.0
        c = str(p.export_currency)
        rows[good] = {"currency": p.export_currency, "price": price, "eff": round(eff, 4)}
        if names.get(good, 0) > 0:  # 名指しした財は、行列・値下がり・効率では外さない
            candidates.append((-eff, good, room, price, c))
            continue
        bloc = blocs_.find(world, p.export_bloc)
        if queues.get(good, 0) > 0 or eff <= 0:
            continue
        if bloc is not None and _fallen(bloc, good, price, band, recent):
            continue
        candidates.append((-eff, good, room, price, c))
    goals: dict[str, int] = {}
    left = dict(earn)
    # 名指しした量（余りと扱える重さまで。稼ぐ額と売れる見込みでは縮めない）
    for _, good, room, price, c in sorted(candidates):
        want = names.get(good, 0)
        if want <= 0:
            continue
        qty = _fit(defs, good, min(want, room), weight_left)
        if qty <= 0:
            continue
        goals[good] = qty
        weight_left -= qty * unit_weight(defs, good) // QUANTITY_SCALE
        left[c] = left.get(c, 0) - money_of(price, qty)
    # 名指ししていない財を、効率の高い順に稼ぐ額まで
    for _, good, room, price, c in sorted(candidates):
        if good in names or left.get(c, 0) <= 0:
            continue
        p = found[good]
        key = (p.export_bloc, good)
        unmet = bloc_unmet.get(key, 0)
        mine, total = before.get(good, 0), planned_by_bloc.get(key, 0)
        if mine > 0 and total > 0:
            share = unmet * mine // total
        else:
            share = unmet // (bloc_sellers.get(key, 0) + 1)
        sellable = seen["exported"].get(good, 0) + share
        qty = min(room, sellable, left[c] * QUANTITY_SCALE // price)
        qty = _fit(defs, good, qty, weight_left)
        if qty <= 0:
            continue
        goals[good] = qty
        weight_left -= qty * unit_weight(defs, good) // QUANTITY_SCALE
        left[c] -= money_of(price, qty)
    for good, row in rows.items():
        row["sold"] = seen["exported"].get(good, 0)
    state["rows"] = {g: rows[g] for g in sorted(set(goals) | set(before)) if g in rows}
    return goals


def _fallen(bloc: Mapping[str, Any], good: str, price: int, band: int, recent: int) -> bool:
    """圏の値段が直近の平均より band（1万分の1）以上下がっているか（売り込みで値段を崩し続け
    ない）。"""
    history = bloc.get("history", {}).get(good, [])[-recent:]
    if not history:
        return False
    average = sum(history) / len(history)
    return price * BASIS < average * (BASIS - band)


def _imported_inputs(
    island: IslandState,
    defs: Defs,
    good: str,
    found: Mapping[str, traders.Prospect],
    imported: set[str],
) -> int:
    """財 good を 1 単位作るのに輸入する原料の額（自国通貨。国営施設の今の作り方で）。"""
    for f in sorted(island.facilities, key=lambda f: f.id):
        if not f.built or f.owner != OWNER_STATE:
            continue
        recipe = fac.recipe_of(defs, f)
        out = recipe.get("outputs", {}).get(good, 0)
        if out <= 0:
            continue
        total = 0
        for need, qty in recipe.get("inputs", {}).items():
            p = found.get(need)
            if need not in imported or p is None or p.import_currency is None:
                continue
            value = p.import_price * fxrates.rate(island, p.import_currency) // BASIS
            total += value * qty // out
        return total
    return 0


def _fit(defs: Defs, good: str, qty: int, weight_left: int) -> int:
    """扱える重さの残りに収まる量。"""
    unit = unit_weight(defs, good)
    if unit <= 0:
        return max(0, qty)
    return max(0, min(qty, max(0, weight_left) * QUANTITY_SCALE // unit))


def _approach(value: int, goal: int, speed: float) -> int:
    if goal == value:
        return value
    step = round_money(abs(goal - value) * speed) or 1
    return value + step if goal > value else max(0, value - step)


def _planned_value(
    island: IslandState, found: Mapping[str, traders.Prospect], plan: Mapping[str, int]
) -> dict[str, int]:
    """計画の分の稼ぎの見込み（通貨 → 額）。"""
    out: dict[str, int] = {}
    for good, qty in plan.items():
        p = found.get(good)
        if p is None or p.export_currency is None:
            continue
        price = traders.views(island).get(str(p.export_bloc), {}).get("prices", {}).get(good, 0)
        c = str(p.export_currency)
        out[c] = out.get(c, 0) + money_of(price, qty)
    return out


def _notice(
    island: IslandState, state: dict[str, Any], earn: Mapping[str, int], plan: Mapping[str, int]
) -> None:
    """稼ぐ額があるのに候補がない（余った設備がない・圏に買い手がない）ターンが続いたら1度だけ知らせる。"""
    if any(v > 0 for v in earn.values()) and not plan:
        state["idle"] = state.get("idle", 0) + 1
        if state["idle"] >= IDLE_NOTICE and not state.get("noticed"):
            island.log.append(LogEntry("export_plan_stuck", {}))
            state["noticed"] = True
    else:
        state.pop("idle", None)
        state.pop("noticed", None)


def _assign(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    plan: Mapping[str, int],
    found: Mapping[str, traders.Prospect],
    working: list[Any],
) -> None:
    """財ごとの計画の量を動いている商社に分ける（その財を輸出している商社で等分。いなければ輸出の
    荷の一番軽い商社が受け持つ）。"""
    for f in traders_of(island, config):
        for item in traders.goods_of(f).values():
            item.pop(traders.PLAN_X, None)
    if not working:
        return
    load = {f.id: 0 for f in working}
    for good, qty in sorted(plan.items()):
        p = found.get(good)
        if p is None or p.export_bloc is None:
            continue
        sellers = [
            f for f in working if traders.goods_of(f).get(good, {}).get("dir") == traders.EXPORT
        ]
        if not sellers:
            free = [
                f
                for f in working
                if traders.goods_of(f).get(good, {}).get("dir") != traders.IMPORT
                or traders.goods_of(f)[good]["held"][0] <= 0
            ]
            if not free:
                continue
            sellers = [min(free, key=lambda f: (load[f.id], f.id))]
        each, rest = divmod(qty, len(sellers))
        for i, f in enumerate(sellers):
            item = traders.entry(f, good)
            item["dir"] = traders.EXPORT
            item["bx"] = p.export_bloc
            item[traders.PLAN_X] = each + (1 if i < rest else 0)
            load[f.id] += item[traders.PLAN_X] * unit_weight(defs, good) // QUANTITY_SCALE


def _clear_traders(island: IslandState, config: GameConfig) -> None:
    for f in traders_of(island, config):
        for item in traders.goods_of(f).values():
            item.pop(traders.PLAN_X, None)
