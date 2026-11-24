"""計画経済の輸出の計画（PL-62・FX-77。2026-11-13、計画経済の電気不足の案 3）。

国営の商社が輸入に使えるのは輸出で得た外貨だけなのに、輸出は生産目標の売れ残りだけだったので、
石炭も機械も作れない島は燃料と設備の資材を買う外貨がなくなり、発電所が止まっていた。そこで国が
要る輸入の額を見積もり、その外貨を稼ぐ輸出の量を生産目標に足す（現実のソ連の国家計画委員会の
外貨の計画と同じ形）。貿易の段の終わり（圏の市場と商社の狙う量の見直しの後）に、計画経済の島ごとに
次のターンの計画を決める（update）。

- 要る外貨（通貨ごと）: 外貨を使う順の 1 段の財（goods.yaml の fx_priority=1。燃料と主食）の「商社が
  輸入できた量 ＋ 島の行列（商社自身の輸出の買いを除き、前のターンに圏で売りに出た量まで）」と、
  国営施設の設備に使う財（外貨を使う順によらない。木材など）の同じ量を 1 ターンに傷む分までにした
  量を、直近 planning.demand_turns ターンで平均し、一番安く輸入できる圏の値段で、その圏の通貨で
  数える（本人の表の 3-1。消費財と直し残しは入れない）。自国の通貨の圏は外貨が要らないので数えない。
- 稼ぐ額 ＝ 要る外貨 ＋（蓄えの目標 − 商社の持つ外貨）÷ planning.export.draw_turns − 売れ残りの輸出
  の稼ぎ（直近の平均）。蓄えの目標 ＝ 要る外貨 × planning.export.reserve_turns（本人の B）。0 未満
  は 0。前のターンに価格差の補給金を払いきれなかった島は、前のターンの計画の分の稼ぎより上げない。
- 候補: 国営施設が作っていて余り（傷みを除いた全力の産出 − 国内の生産目標。前のターンに目標に届か
  なかった財は作れた量 − 国内の生産目標）のある財のうち、その通貨の圏で値段があり、島に行列がなく、
  行列のある財の原料でもなく、商社が輸入していない財。圏の値段が直近の平均より trade.bloc_band
  以上下がっている財は外す。外貨の効率（圏の値段を自国通貨に直した額 − 作るのに輸入する原料の額）
  ÷ 原価 の高い順に、余り・圏で売れる見込み（直近の平均の売れた量 ＋ 圏の買えなかった量の分け前。
  FX-54）・残りの稼ぐ額の一番小さい量を積む。扱える重さは、1 段の財の輸入の見込み（持つ外貨で
  買える分）を先に取り置いた残り。
- 命令 set_export_plan（本人の表の 2-2）: 財ごとに量（余りまで。稼ぐ額と売れる見込みでは縮めない）か
  輸出しない（0）を名指しできる。名指しの分の稼ぎを稼ぐ額から引いた残りを自動で選ぶ。
- 計画の量は前のターンの量から trade.target_speed の割合だけ新しい量に近づけ（商社の狙う量と同じ
  形）、圏で売れ残って島の市場に戻した荷の量を引く。値下がり・効率・稼ぐ額で選ばなかった財は同じ
  割合で 0 へ減らし、余りがない・行列がある・輸入している・輸出しないと名指しした財はすぐ外す。
  生産目標に足すのは、計画の量から商社がもう持っている輸出の荷を引いた量（plan.export_planned）。
- 商社: 財ごとの計画の量を、その財を輸出している（いなければ荷の少ない）動いている商社に分け、
  ``traders.PLAN_X`` に置く。どの商社にも扱える重さを超えては分けない。商社は島の市場に計画の分を
  別の注文で出し（扱える重さは計画の分に先に充てる）、その注文は食料も国営施設と同じ順位で配られる
  （本人の表の 1-2。modules/market）。生産目標の需要の履歴からは除く。
- 蓄え（本人の B）: 蓄えが目標より少ない間は、外貨を使う順の planning.export.reserve_rank（既定 3:
  原料と部品・消費財）から下の段の輸入に目標を超える分しか使わない（reserve_of。modules/trade の
  _fx_ahead）。国営施設の設備に使う財（essential）は使える。
- 中央銀行の外貨は使わない。

島の状態は ``island.ext["plan"]["export"]``:
- plan: 財 → 計画の量（1000 分の 1）、named: 命令で名指しした財 → 量（0 は輸出しない）
- need・earn・reserve・held: 通貨の番号の文字列 → 要る外貨・稼ぐ額・蓄えの目標・持つ外貨（この
  ターン）
- needs: 通貨 → {"fuel": 1 段の分, "equipment": 設備の分}（画面の内訳）
- rows: 財 → {currency, price, eff, sold}（画面）、bloc: 財 → 輸出する圏の番号
- unsold: 通貨 → 売れ残りの輸出の稼ぎの履歴、planned: 通貨 → 前のターンの計画の分の稼ぎ
- wants: 財 → 要る外貨に数えた量の履歴、sold: 財 → 圏で売れた量の履歴、cargo: 財 → 商社の持つ
  輸出の荷、essential: 蓄えを使ってよい設備の資材
- idle: 稼ぐ額があるのに候補のないターンの数、noticed: そのことを知らせた
- deposit: 主体 → 通貨 → 自国の中央銀行に預けている外貨の量（預けた分の請求。FX-83）
- loan: 主体 → 通貨 → 中央銀行が融通で借りて国営の商社に渡した外貨のうち、商社がまだ中央銀行に
  戻していない量（国の中のメモ。FX-86）

蓄えの目標を超えた外貨は自国の中央銀行に預ける（planning.export.deposit_surplus。FX-83）: 貿易の段の
終わり（計画を決めた後）に、国営の商社の主体ごとの持ち高が蓄えの目標の分（reserve_of）を超える分を
中央銀行に移し、次のターンの為替の段の後に、預けた分を中央銀行の持ち高の範囲で戻す（deposit_surplus・
withdraw_deposits）。為替の段の間は中央銀行の持ち高なので、融通の貸し手の余り（swap.lenders）に入る。
貸し出されて中央銀行に残っていない分は請求のまま残り、返されたら戻す。新しい数は置かない（目標は今の
蓄えの目標）。現実では、ソ連の外国貿易銀行が余ったドルを西側の銀行に預け、それが貸し出された形
（ユーロダラー）。

外貨の足りない計画の島は、優先する輸入のために融通を借りる（planning.export.borrow_rank。
FX-86。掲示板 W-299 の 2）: 圏の市場の前に、国営の商社の外貨を使う順が borrow_rank 段まで
（1 燃料と主食、2 設備。reserve_rank より上だけ）の輸入の注文の額が持ち高を超える分を、島の
中央銀行が融通（swap.draw）で借り、商社に渡す（borrow_for_imports）。市場の島の融通は為替の段で
先に済んでいる。順 1 の分を全部の計画の島で先に借り、残りで順 2 を借りる。借りた分は、その段
より下の財の輸入には使わない（modules/trade の _fx_ahead）。借りられる枠・貸し手・利子・返し方
は今の融通のまま（新しい数は置かない）。枠で借りられなかった分は注文に出ず（輸入の圧縮）、
止まった枠の種類（quota＝借り手の枠 fx.swap_quota、supply＝貸し手の余りと発行した島の枠）を
記録する。商社は、蓄えの目標を超えた外貨をまず中央銀行に戻し（メモを減らす。国の中の付け
替え）、残りを預ける。中央銀行は、持ち高が手元に置く分を超えたら融通を返す。現実では、コメコン
の国際経済協力銀行が振替ルーブルの決済で赤字の加盟国に短期の信用を与え、黒字国の余りが赤字国
の燃料・主食・設備の輸入に回った形。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR
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


def state_of(island: IslandState) -> dict[str, Any]:
    return planning.state_of(island).setdefault(KEY, {})


def plans(island: IslandState) -> dict[str, int]:
    """財 → 輸出の計画の量（次のターン）。"""
    return island.ext.get(planning.KEY, {}).get(KEY, {}).get("plan", {})


def named(island: IslandState) -> dict[str, int]:
    """命令で名指しした財 → 量（0 は輸出しない）。"""
    return island.ext.get(planning.KEY, {}).get(KEY, {}).get("named", {})


def essential(island: IslandState, good: str) -> bool:
    """財 good が、外貨を使う順の 3 段以下でも蓄え（本人の B）を使ってよい設備の資材か（国営施設の
    設備に使う財。木材など。本人の表の 3-1）。"""
    return good in island.ext.get(planning.KEY, {}).get(KEY, {}).get("essential", ())


def reserve_of(
    island: IslandState, config: GameConfig, entity: str, currency: int, turn: int | None = None
) -> int:
    """本人の B: 主体 entity の通貨 currency の持ち高のうち、外貨を使う順の reserve_rank 段から下の
    輸入に使わず残す額（蓄えの目標を、島の動いている国営の商社の持ち高の比で分けた分。持ち高まで）。
    計画経済でない島と自国の通貨は 0。"""
    if currency == island.id or not traders.is_planned(island):
        return 0
    target = island.ext.get(planning.KEY, {}).get(KEY, {}).get("reserve", {}).get(str(currency), 0)
    if target <= 0:
        return 0
    mine = fxmoney.holding(island, entity, currency)
    if mine <= 0:
        return 0
    total = sum(fxmoney.holding(island, e, currency) for e in _entities(island, config, turn))
    return min(mine, target * mine // max(1, total, mine))


def _entities(island: IslandState, config: GameConfig, turn: int | None = None) -> list[str]:
    """島の国営の商社のお金を持つ主体（重なりなし。turn を渡すと動いている商社だけ）。"""
    out: list[str] = []
    for f in traders_of(island, config, turn):
        if f.owner != OWNER_STATE:
            continue
        e = entity_of(config, f)
        if e not in out:
            out.append(e)
    return out


DEPOSIT = "deposit"
LOAN = "loan"
# island.stats の頭（FX-86）: want.<段>・got.<段>・short.<段>（自国通貨）、stop.<枠>（回数）、
# service（債務の支払いに中央銀行へ回した額。案 2）
BORROW = "fx.plan_borrow"


def deposit_surplus(
    world: WorldState, island: IslandState, config: GameConfig, turn: int | None = None
) -> None:
    """FX-83: 国営の商社の主体ごとに、外貨の持ち高のうち蓄えの目標の分（reserve_of）を超える分を
    自国の中央銀行に預ける（預けた量を請求として残す）。計画経済でない島と設定が切れているときは
    何もしない。"""
    if not traders.is_planned(island):
        return
    deposit = bool(config.get("planning.export.deposit_surplus"))
    loans = state_of(island).get(LOAN, {})
    if not deposit and not loans:
        return
    claims = state_of(island).setdefault(DEPOSIT, {})
    for entity in _entities(island, config, turn):
        for key, cur in sorted(fxmoney.holdings(island).get(entity, {}).items()):
            currency = int(key)
            if currency == island.id or currency not in world.islands:
                continue
            over = cur[0] - reserve_of(island, config, entity, currency, turn)
            if over <= 0:
                continue
            over -= _pay_loan(world, island, entity, currency, over)  # 借りた分を先に戻す（FX-86）
            if over <= 0 or not deposit:
                continue
            value = fxmoney.cost_of(island, entity, currency, over)
            fxmoney.transfer(
                world,
                currency,
                (island.id, entity),
                (island.id, ledger.CB),
                over,
                "fx_deposit",
                value,
            )
            mine = claims.setdefault(entity, {})
            mine[key] = mine.get(key, 0) + over
    if not any(claims.values()):
        state_of(island).pop(DEPOSIT, None)
    if not loans:
        state_of(island).pop(LOAN, None)


def _pay_loan(
    world: WorldState, island: IslandState, entity: str, currency: int, amount: int
) -> int:
    """FX-86: 国営の商社が、中央銀行から渡された融通の外貨を amount まで中央銀行に戻す（国の中の付け
    替え）。戻した量を返す。"""
    loans = state_of(island).get(LOAN, {})
    mine = loans.get(entity, {})
    key = str(currency)
    pay = min(amount, mine.get(key, 0))
    if pay <= 0:
        return 0
    value = fxmoney.cost_of(island, entity, currency, pay)
    fxmoney.transfer(
        world, currency, (island.id, entity), (island.id, ledger.CB), pay, "fx_plan_loan", value
    )
    mine[key] -= pay
    if mine[key] <= 0:
        del mine[key]
    if not mine:
        loans.pop(entity, None)
    return pay


def pay_to_central_bank(
    world: WorldState, island: IslandState, entity: str, currency: int, amount: int
) -> int:
    """FX-86 の案 2: 国営の商社の主体 entity が、融通の債務の支払いのために外貨 amount を
    中央銀行に回す（まず中央銀行から渡された融通の外貨のメモを減らし、残りはそのまま回す。国の
    中の付け替え）。回した量を返す。"""
    amount = min(amount, fxmoney.holding(island, entity, currency))
    if amount <= 0:
        return 0
    moved = _pay_loan(world, island, entity, currency, amount)
    rest = amount - moved
    if rest > 0:
        value = fxmoney.cost_of(island, entity, currency, rest)
        fxmoney.transfer(
            world,
            currency,
            (island.id, entity),
            (island.id, ledger.CB),
            rest,
            "fx_plan_loan",
            value,
        )
    if not state_of(island).get(LOAN):
        state_of(island).pop(LOAN, None)
    return amount


def borrow_for_imports(
    world: WorldState,
    island: IslandState,
    config: GameConfig,
    entity: str,
    currency: int,
    amount: int,
) -> tuple[int, str | None]:
    """FX-86: 島の中央銀行が通貨 currency を融通で amount まで借り、国営の商社の主体 entity に渡す。
    (渡した量, 借りきれなかったときの枠の種類 "quota" か "supply") を返す。"""
    from hakoniwa_core.trade import swap  # swap は economy を読むので、ここで読む

    if amount <= 0:
        return 0, None
    room = swap.available(world, config, island.id, currency)
    why = None
    if room < amount:
        quota = swap.quota_room(island, config)
        quota_fx = None if quota is None else fxrates.to_foreign(island, currency, quota)
        why = "quota" if quota_fx is not None and quota_fx <= room else "supply"
    got = swap.draw(world, config, island.id, currency, min(amount, room)) if room > 0 else 0
    if got > 0:
        value = fxmoney.cost_of(island, ledger.CB, currency, got)
        fxmoney.transfer(
            world, currency, (island.id, ledger.CB), (island.id, entity), got, "fx_plan_loan", value
        )
        mine = state_of(island).setdefault(LOAN, {}).setdefault(entity, {})
        mine[str(currency)] = mine.get(str(currency), 0) + got
    return got, why


def loaned(island: IslandState) -> dict[str, int]:
    """通貨 → 中央銀行から国営の商社に渡した融通の外貨のうち、まだ戻していない量の合計（FX-86）。"""
    out: dict[str, int] = {}
    for mine in island.ext.get(planning.KEY, {}).get(KEY, {}).get(LOAN, {}).values():
        for key, amount in mine.items():
            out[key] = out.get(key, 0) + amount
    return out


def withdraw_deposits(world: WorldState, island: IslandState) -> None:
    """FX-83: 預けた外貨を、中央銀行の持ち高の範囲で主体に戻す（貸し出されて残っていない分は請求の
    まま）。為替の段の後に呼ぶ。計画経済でなくなった島も、残っている請求は戻す。"""
    state = island.ext.get(planning.KEY, {}).get(KEY, {})
    claims = state.get(DEPOSIT)
    if not claims:
        return
    for entity in sorted(claims):
        mine = claims[entity]
        for key in sorted(mine):
            currency = int(key)
            take = min(mine[key], fxmoney.holding(island, ledger.CB, currency))
            if take > 0:
                value = fxmoney.cost_of(island, ledger.CB, currency, take)
                fxmoney.transfer(
                    world,
                    currency,
                    (island.id, ledger.CB),
                    (island.id, entity),
                    take,
                    "fx_deposit",
                    value,
                )
                mine[key] -= take
            if mine[key] <= 0:
                del mine[key]
        if not mine:
            del claims[entity]
    if not claims:
        state.pop(DEPOSIT, None)


def deposited(island: IslandState) -> dict[str, int]:
    """通貨 → 中央銀行に預けている外貨の量の合計（請求。FX-83）。"""
    out: dict[str, int] = {}
    for mine in island.ext.get(planning.KEY, {}).get(KEY, {}).get(DEPOSIT, {}).values():
        for key, amount in mine.items():
            out[key] = out.get(key, 0) + amount
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
    _keep_sold(state, config, seen)
    needs, parts = _needs(world, island, defs, config, found, records, seen)
    held = {
        c: sum(fxmoney.holding(island, e, int(c)) for e in _entities(island, config, turn))
        for c in needs
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
    goals, kept = _choose(
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
    # 今回選ばなかった財も、候補に残っていれば（値下がり・効率・稼ぐ額で外れただけなら）0 へ同じ
    # 割合で減らす（作る量を1ターンで大きく変えない）。余りがない・行列がある・輸入している・
    # 名指しで 0 の財はすぐ外す。圏で売れ残って島に戻した荷は、次のターンの量から引く（行って戻る
    # だけにしない）
    speed = config.get("trade.target_speed")
    before = state.get("plan", {})
    names = named(island)
    plan: dict[str, int] = {}
    for good in sorted(set(before) | set(goals)):
        if good not in goals and good not in kept:
            continue
        goal = goals.get(good, 0)
        qty = goal if good in names else _approach(before.get(good, 0), goal, speed)
        qty -= seen["returned"].get(good, 0)
        if qty > 0:
            plan[good] = qty
    state["plan"] = plan
    state["bloc"] = {g: found[g].export_bloc for g in plan if found[g].export_bloc is not None}
    state["need"], state["needs"], state["earn"] = needs, parts, earn
    state["reserve"], state["held"] = reserve, held
    state["planned"] = _planned_value(island, found, plan)
    state["cargo"] = _cargo(island, config, plan)
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
    for key in (
        "plan",
        "bloc",
        "need",
        "needs",
        "earn",
        "reserve",
        "held",
        "planned",
        "rows",
        "cargo",
        "essential",
    ):
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


def _keep_sold(state: dict[str, Any], config: GameConfig, seen: Mapping[str, Any]) -> None:
    """財ごとの圏で売れた量の履歴（圏で売れる見込みを直近の平均で見る。1ターンの売れ方で計画を
    跳ねさせない）。"""
    keep = config.get("planning.demand_turns")
    history = state.setdefault("sold", {})
    exported = seen["exported"]
    for good in sorted(set(history) | set(exported)):
        history[good] = [*history.get(good, []), exported.get(good, 0)][-keep:]
        if not any(history[good]):
            del history[good]


def _average(history: list[int] | None) -> int:
    return sum(history) // len(history) if history else 0


def _cargo(island: IslandState, config: GameConfig, plan: Mapping[str, int]) -> dict[str, int]:
    """計画の財ごとに、商社が手元と運ぶ途中に持っている輸出の荷（生産目標から引く。もう作って
    ある分を作り直させない）。"""
    out: dict[str, int] = {}
    for f in traders_of(island, config):
        for good, item in traders.goods_of(f).items():
            if good in plan and item.get("dir") == traders.EXPORT and item["held"][0] > 0:
                out[good] = out.get(good, 0) + item["held"][0]
    return out


def _needs(
    world: WorldState,
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    found: Mapping[str, traders.Prospect],
    records: Mapping[str, Any],
    seen: Mapping[str, Any],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """要る外貨（通貨の番号の文字列 → 額）と、その内訳（1 段の分と設備の分）。

    財ごとの量（輸入できた量 ＋ 島の行列。行列は前のターンに圏で売りに出ていた量まで）は直近
    planning.demand_turns ターンの平均にする（1ターンの行列の跳ねで稼ぐ額を跳ねさせない）。設備
    の資材は、外貨を使う順によらず国営施設の設備に使う財（木材など）を 1 ターンに傷む分まで数え、
    その財を ``essential`` に置く（蓄えを使ってよい。_fx_ahead）。"""
    state = state_of(island)
    wear = _monthly_wear(island, defs, config)
    keep = config.get("planning.demand_turns")
    history = state.setdefault("wants", {})
    needs: dict[str, int] = {}
    parts: dict[str, dict[str, int]] = {}
    essential: list[str] = []
    wants: dict[str, int] = {}
    for good in tradable_goods(defs):
        rank = fx_priority(defs, good)
        kit = good in wear
        if rank > 2 and not kit:
            continue
        p = found.get(good)
        if p is None or p.import_bloc is None or p.import_currency in (None, island.id):
            continue
        queue = traders.home_unmet(island, records.get(good))  # 商社の輸出の買いは除く
        bloc = blocs_.find(world, p.import_bloc)
        if bloc is not None:
            queue = min(queue, bloc.get("supply", {}).get(good, 0))
        qty = seen["imported"].get(good, 0) + queue
        if rank != 1:  # 設備の資材は 1 ターンに傷む分まで（直し残しは入れない）
            qty = min(qty, wear.get(good, 0))
        wants[good] = qty
        if kit and rank > 2:
            essential.append(good)
    for good in sorted(set(history) | set(wants)):
        history[good] = [*history.get(good, []), wants.get(good, 0)][-keep:]
        if not any(history[good]):
            del history[good]
    for good in sorted(history):
        qty = _average(history[good])
        p = found.get(good)
        if qty <= 0 or p is None or p.import_currency in (None, island.id):
            continue
        value = money_of(p.import_price, qty)
        c = str(p.import_currency)
        needs[c] = needs.get(c, 0) + value
        part = parts.setdefault(c, {"fuel": 0, "equipment": 0})
        part["fuel" if fx_priority(defs, good) == 1 else "equipment"] += value
    state["essential"] = essential
    return needs, parts


def _monthly_wear(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """国営施設の設備が 1 ターンに傷む量（資材 → 量）。"""
    out: dict[str, float] = {}
    for f in island.facilities:
        if f.owner != OWNER_STATE or not gear.has_equipment(defs, config, f):
            continue
        for good, qty in gear.kit_of(defs, f).items():
            out[good] = out.get(good, 0.0) + qty * gear.wear_rate(config, good) / TURNS_PER_YEAR
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
) -> tuple[dict[str, int], set[str]]:
    """財 → このターンに決めた計画の量（近づける前）と、候補に残った財（値下がり・効率・稼ぐ額で
    選ばなかった財を含む。計画から外すときに 0 へ近づける）。名指しを先に、残りを効率の順に。"""
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
        traders.capacity(config, f) - traders.first_import_weight(island, defs, f) for f in working
    )
    feeds = _queued_inputs(island, defs, queues)
    sold_history = state.get("sold", {})
    kept: set[str] = set()
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
        domestic = planning.domestic_goal(island, config, good) or 0
        room = cap - domestic
        made = island.stats.get(f"production.{good}")
        if achieved.get(good, 1.0) < 1 and made is not None:
            # 前のターンに目標に届かなかった（原料・人・電気が足りない）: 作れた量を上限に見る
            # （達成率は目標に対する割合なので、全力の産出に掛けると作れる量を見誤る）
            room = min(room, round_money(made * QUANTITY_SCALE) - domestic)
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
            kept.add(good)
            candidates.append((-eff, good, room, price, c))
            continue
        bloc = blocs_.find(world, p.export_bloc)
        if queues.get(good, 0) > 0 or good in feeds:  # 行列のある財とその原料は国内が先
            continue
        kept.add(good)
        if eff <= 0:
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
        sellable = _average(sold_history.get(good)) + share
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
    return goals, kept


def _queued_inputs(island: IslandState, defs: Defs, queues: Mapping[str, int]) -> set[str]:
    """島に行列のある財を作る国営施設の原料（輸出に回さず国内の生産に使う）。"""
    out: set[str] = set()
    for f in island.facilities:
        if not f.built or f.owner != OWNER_STATE:
            continue
        recipe = fac.recipe_of(defs, f)
        if any(queues.get(g, 0) > 0 for g in recipe.get("outputs", {})):
            out.update(recipe.get("inputs", {}))
    return out


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
    荷の一番軽い商社が受け持つ）。どの商社にも、扱える重さ（1 段の財の輸入の見込みを取り置いた
    残り）を超えては分けず、超えた分は荷の軽いほかの商社に回す。"""
    for f in traders_of(island, config):
        for item in traders.goods_of(f).values():
            item.pop(traders.PLAN_X, None)
    if not working:
        return
    room = {
        f.id: traders.capacity(config, f) - traders.first_import_weight(island, defs, f)
        for f in working
    }
    for good, qty in sorted(plan.items()):
        p = found.get(good)
        if p is None or p.export_bloc is None:
            continue
        unit = unit_weight(defs, good)
        sellers = [
            f for f in working if traders.goods_of(f).get(good, {}).get("dir") == traders.EXPORT
        ]
        # 輸入の荷（手元と運ぶ途中）のない商社だけが、この財を輸出の向きに替えられる
        free = [
            f
            for f in working
            if f not in sellers
            and (
                traders.goods_of(f).get(good, {}).get("dir") != traders.IMPORT
                or (
                    traders.goods_of(f)[good]["held"][0] <= 0
                    and traders.goods_of(f)[good].get("transit", 0) <= 0
                )
            )
        ]
        free.sort(key=lambda f: (-room[f.id], f.id))
        if not sellers and free:
            sellers = [free.pop(0)]
        left = qty
        each = qty // max(1, len(sellers))
        for i, f in enumerate([*sellers, *free]):
            if left <= 0:
                break
            share = left if i >= len(sellers) - 1 else min(left, each)
            if unit > 0:
                share = min(share, max(0, room[f.id]) * QUANTITY_SCALE // unit)
            if share <= 0:
                continue
            item = traders.entry(f, good)
            item["dir"] = traders.EXPORT
            item["bx"] = p.export_bloc
            item[traders.PLAN_X] = share
            room[f.id] -= share * unit // QUANTITY_SCALE
            left -= share


def _clear_traders(island: IslandState, config: GameConfig) -> None:
    for f in traders_of(island, config):
        for item in traders.goods_of(f).values():
            item.pop(traders.PLAN_X, None)
