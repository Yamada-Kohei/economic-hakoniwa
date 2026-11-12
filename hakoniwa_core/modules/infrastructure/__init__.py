"""インフラ（フェーズ2-3）: 工事の命令と、公共事業（インフラの工事と維持）、傷みの段。

公共事業は施設ではなく、島に1つの「公共事業」としてまとめて動かします。毎ターン、先に維持
（傷んだ分を目標まで戻す。施設と町のマスとその隣から）、次に工事（並んだ順。1マスは1ターンに
「100% ÷ 工期」まで）の量を決め、資材と人手はその量に1マス分の費用（infrastructure.yaml）を
掛けた分です。

- 雇用の段（modules/employment）: 維持の分は施設より先に、工事の分は施設の後に、作業員の賃金と
  次のターンの資材の代金の見込みを公共事業費（construction）から取り置く（賃金は前のターンに雇いに
  行けた人数 reach まで）。作業員は施設が雇った後、店が雇う前に、残った失業者から通勤圏を見ずに
  島のどこからでも、島の平均賃金で雇い、足りなければ店の働き手を引き抜く。取り置いた人数を超えて
  雇えた分は、そのとき残っている公共事業費で払う。雇いたい人数に届かなければ、わけ（人手・
  公共事業費）を工事の状況に出す。
- 工事の段（modules/construction）: 雇えた人手と手元の資材の少ない方の割合だけ整備度を上げる。
  進まないまま2ターン続いたら、わけ（公共事業費・人手・資材）をログで知らせる。
- 賃金の段（modules/labor）: 作業員の賃金を国庫から払う。
- 市場の段（modules/market）: 次のターンの資材を、国の倉庫にある分を先に使い、足りない分を市場で
  値幅の上端の指値で買う（運賃の率は家計と同じ）。維持の分は施設の工事より先に、新しい工事の分は
  後に注文する。
- インフラの段（この段。町の段の後）: 整備度を表の傷みの分だけ下げ、統計を残す。役所の決まった
  事業（W-186 回4。_auto_works）: 命令の工事がなくても、上下水道が足りずに育たない町・人が密に
  住むのに上下水道が半分に届かない町と、施設と町のまわりの交通の整備度が
  infrastructure.auto_works.road_level に届かないマスに、1ターンに1か所分ずつ工事を並べる。並べる
  のは
  公共事業費の月割りから、このターンに使った額と、並んだ工事の次のターンの見込みを引いて、その
  1か所分の費用が残るときだけ。役所が並べた工事は、遊ぶ人が命令で並べた工事の後に進め、資材は国の
  倉庫から使わずに市場で買う（倉庫は遊ぶ人の備え）。命令 set_public_works で止められる。

資材・運賃・作業員の賃金は、施設の工事と同じく工事の部門（GDP の投資）に数えます。インフラは
政府の資産として記帳しません。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, politics, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import CONSTRUCTION, Payer, funds
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.facilities import is_land, producible_goods
from hakoniwa_core.geo import NEIGHBORS8, within
from hakoniwa_core.ledger import GOV
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import from_milli, money_of

WORKS = "works"  # island.scratch: このターンの公共事業の計画と雇えた人手
UPKEEP = "works_upkeep"  # island.scratch: 維持の仕事の一覧（整備度か工事の列が変わったら捨てる）
MATERIALS = "works_materials"  # island.scratch: 種類ごとに、決まった資材（True）か代わりの資材か
AUTO = "auto"  # economy.infra: 役所が並べた工事 [種類, x, y]（命令の工事の後に進める）
AUTO_OFF = "auto_off"  # economy.infra: 命令 set_public_works で役所の決まった事業を止めている
REACH = "reach"  # economy.infra: 前のターンに作業員を雇いに行ったときの失業者の数（取り置きの目安）
STAFF = (
    "staff"  # economy.infra: 前のターンの作業員（マス "x,y" → 人数。計画経済は雇い続ける。PL-59）
)
UPKEEP_PRIORITY = "upkeep_priority"  # economy.infra: 維持の優先度（命令 set_public_works。PL-59）
BUILD_PRIORITY = "build_priority"  # economy.infra: 新しい工事の優先度
PAYER = Payer(GOV, CONSTRUCTION)
# 新しい工事が進まないまま、このターン数続いたらわけをログで知らせる（その後は進むまで出さない）
STALL_TURNS = 2


@dataclass
class Item:
    """公共事業の1マス・1種類分の仕事。"""

    kind: str
    x: int
    y: int
    amount: int  # 上げる整備度（1000 分の 1）
    upkeep: bool  # 維持か（False は新しい工事）
    labor: int = 0  # 要る人手（1000 分の 1 人）
    materials: dict[str, int] = field(default_factory=dict)


def works_state(island: IslandState) -> dict[str, Any]:
    """公共事業の状態（economy.infra）: 工事の列 queue、工事の資材 stock、止まったターン数など。"""
    return island.economy.infra


def works_stock(island: IslandState) -> dict[str, list[list[int]]]:
    return works_state(island).setdefault("stock", {})


class Works:
    """島の公共事業の仕事の見積もり。"""

    def __init__(self, island: IslandState, ctx: StageContext) -> None:
        self.island, self.ctx, self.defs = island, ctx, ctx.defs
        # 体制の工期の倍率（権威主義は公共事業が速い。フェーズ3-4）
        self.turns_factor = politics.trait(island, ctx.defs, "works_turns", 1.0)

    # --- 1マス分の費用 -------------------------------------------------------------

    def materials(self, kind: str) -> dict[str, int]:
        """1マスを 0% から 100% にする資材。手に入らない財があれば fallback を使う。

        手に入るのは、島で作れて（地下資源）、建った施設が前のターンに作ったか、国の倉庫と工事の
        資材に1マス分ある財です（製鉄所が閉まって鋼鉄がなくなった島は、送電網を木の柱で直す。送電網に
        つながっていない製鉄所は、木の柱の送電網でつなぐ）。ターンの途中で変わらないように、ターンの
        初めに使うときに決めて island.scratch に置きます。
        """
        spec = self.defs.infrastructure[kind]
        if "fallback" not in spec:
            return dict(spec["materials"])
        chosen = self.island.scratch.setdefault(MATERIALS, {})
        if kind not in chosen:
            producible = producible_goods(self.defs, self.island.resources)
            chosen[kind] = all(
                good in producible and self._available(good, qty)
                for good, qty in spec["materials"].items()
            )
        return dict(spec["materials"] if chosen[kind] else spec["fallback"])

    def _available(self, good: str, qty: int) -> bool:
        island = self.island
        for f in island.facilities:
            if f.built and f.operating_rate > 0 and good in fac.recipe_of(self.defs, f)["outputs"]:
                return True
        held = stock.total(island.warehouse, good) + stock.total(works_stock(island), good)
        return held >= qty

    def step(self, kind: str) -> int:
        """1マスを1ターンに上げられる整備度の上限（100% ÷ 工期。工期は体制の倍率を掛ける）。"""
        turns = self.defs.infrastructure[kind]["turns"] * self.turns_factor
        return math.ceil(infra.FULL / max(0.1, turns))

    def wear(self, kind: str) -> int:
        return round(self.defs.infrastructure[kind]["wear"] * infra.FULL)

    def cost(self, item: Item) -> Item:
        """item の量に1マス分の資材と人手を掛ける。"""
        item.labor = self.defs.infrastructure[item.kind]["labor"] * item.amount
        item.materials = {
            g: -(-q * item.amount // infra.FULL) for g, q in self.materials(item.kind).items()
        }
        return item

    # --- 仕事の一覧 -----------------------------------------------------------------

    def upkeep(self, ahead: bool = False) -> list[Item]:
        """維持の仕事（整備度が目標より低いマス）。施設と町のマスとその隣から先に、y, x の順。

        ahead なら、次のターンの分（このターンの終わりの傷みを足した分）を見積もる。工事の列に
        並んでいるマスと種類も、ほかのマスと同じく目標までは維持で戻す（新しい工事が受け持つのは目標
        から 100% までの分。IN-28）。整備度か工事の列が変わるまでは同じなので、island.scratch に
        置いて使い回す（forget_upkeep で捨てる）。
        """
        island = self.island
        cache = island.scratch.setdefault(UPKEEP, {})
        if ahead in cache:
            return cache[ahead]
        near: set[tuple[int, int]] = set()
        for f in island.facilities:
            if fac.on_map(f):
                near.add((f.x, f.y))
                near.update((f.x + dx, f.y + dy) for dx, dy in NEIGHBORS8)
        items = []
        for tile in island.tiles:
            if not tile.infra:
                continue
            for kind in sorted(tile.infra):
                if kind not in self.defs.infrastructure:
                    continue
                target = infra.keep(tile, kind)
                now = infra.level(tile, kind)
                if ahead:
                    now = max(0, now - self.wear(kind))
                if now < target:
                    amount = min(target - now, self.step(kind))
                    items.append(Item(kind, tile.x, tile.y, amount, True))
        items.sort(key=lambda i: ((i.x, i.y) not in near, i.y, i.x, i.kind))
        cache[ahead] = [self.cost(i) for i in items]
        return cache[ahead]

    def build(self, ahead: bool = False, ordered_only: bool = False) -> list[Item]:
        """新しい工事の仕事（工事の列の順。役所が並べた工事は、命令で並べた工事の後）。

        ahead なら、次のターンの分（このターンの終わりの傷みを足した分）を見積もる。傷みの分を
        買い足さないと、仕上がりの近いマスが毎ターン傷みの分だけ足りずに 100% に届かない。
        ordered_only なら、命令で並べた工事だけ。受け持つのは維持の目標から 100% までの分で、目標
        までは維持の仕事（upkeep）が戻す（IN-28。0% から引くマスは目標が 0 なので全部）。1マスが
        1ターンに上がるのは、維持と合わせて step まで。
        """
        items = []
        auto = {tuple(e) for e in works_state(self.island).get(AUTO, [])}
        queue = infra.queue(self.island)
        ordered = [e for e in queue if tuple(e) not in auto]
        if not ordered_only:
            ordered += [e for e in queue if tuple(e) in auto]
        for kind, x, y in ordered:
            if kind not in self.defs.infrastructure:
                continue
            tile = self.island.tile(x, y)
            now = infra.level(tile, kind)
            if ahead and now > 0:
                now = max(0, now - self.wear(kind))
            keep = infra.keep(tile, kind)
            step = self.step(kind)
            amount = min(infra.FULL - max(now, keep), step - min(step, max(0, keep - now)))
            if amount > 0:
                items.append(self.cost(Item(kind, x, y, amount, False)))
        return items


def forget_upkeep(island: IslandState) -> None:
    """使い回している維持の仕事の一覧を捨てる（整備度か工事の列を変えたとき）。"""
    island.scratch.pop(UPKEEP, None)


def _fit(item: Item, have: dict[str, int]) -> int:
    """手元の資材 have で進められる量（1000 分の 1）。"""
    amount = item.amount
    for good, need in item.materials.items():
        if need > 0 and have.get(good, 0) < need:
            amount = min(amount, have.get(good, 0) * item.amount // need)
    return amount


def _scale(works: Works, item: Item, amount: int) -> Item:
    return works.cost(Item(item.kind, item.x, item.y, amount, item.upkeep))


# --- 雇用の段から -------------------------------------------------------------------


def priority(island: IslandState, config: GameConfig, upkeep: bool) -> int:
    """維持（upkeep）か新しい工事の優先度。命令 set_public_works で国ごとに変えられ、決めて
    いなければ
    infrastructure.upkeep_priority・build_priority。資材の買い注文の順と、計画経済の島で作業員を雇う
    位置（PL-59）に使う。"""
    key = UPKEEP_PRIORITY if upkeep else BUILD_PRIORITY
    found = works_state(island).get(key)
    return found if found is not None else config.get(f"infrastructure.{key}")


def kept_staff(island: IslandState) -> dict[tuple[int, int], int]:
    """前のターンの作業員（マスごと）。"""
    out = {}
    for key, n in works_state(island).get(STAFF, {}).items():
        x, y = key.split(",")
        out[(int(x), int(y))] = n
    return out


def plan(island: IslandState, ctx: StageContext, upkeep: bool, wage: int, keep: int = 0) -> int:
    """雇用の段: このターンの維持（upkeep）か新しい工事の量を、手元の工事の資材と公共事業費で決め、
    作業員の賃金と、次のターンに市場で買う資材の代金の見込みを取り置く。取り置いた見込みは市場の段で
    戻す（_holds）。雇いたい人数を返す。

    賃金を取り置くのは、前のターンに公共事業が雇いに行ったときに残っていた失業者の数（economy.infra
    の reach）までです。雇えない作業員の賃金で公共事業費の月割りを埋めて、後ろの施設の工事と新しい
    工事を止めないように。それを超えて雇えた分は、雇うとき（hire）に残っているお金で払います。
    keep は雇い続ける前のターンの作業員の数（計画経済の島。PL-59）で、賃金はその分まで必ず取り置く
    （reach がそれより少なくても）。"""
    if not island.has_economy:
        return 0
    works = Works(island, ctx)
    state = island.scratch.setdefault(
        WORKS,
        {
            "items": [],
            "labor": 0,
            "held": 0,
            "wage": wage,
            "have": stock.totals(works_stock(island)),
            "reach": works_state(island).get(REACH),
        },
    )
    have = state["have"]
    cap = state["reach"]
    if cap is not None and keep:
        cap = state["reach"] = max(cap, keep)

    def holdable(labor: int) -> int:  # labor（1000 分の 1 人）を雇うのに賃金を取り置く人数
        workers = -(-labor // 1000)
        return workers if cap is None else min(workers, cap)

    left = funds.available(island, PAYER)
    planned = before = state["labor"]
    held = state["held"]
    why = ""
    candidates = works.upkeep() if upkeep else works.build()
    for item in candidates:
        amount = _fit(item, have)
        if amount < item.amount and not why:
            why = "materials"
            if not upkeep or infra.queued(island, item.kind, item.x, item.y):
                # 資材を待っている並んだ工事がある（人手やお金で止まっても残す。並んだマスの
                # 目標までの分は維持の仕事。IN-28）
                state["short_materials"] = True
        if amount <= 0:
            continue
        item = _scale(works, item, amount)
        if (holdable(planned + item.labor) - held) * wage > left:
            # 公共事業費が足りなければ、払える人数の分まで
            room = (held + left // max(1, wage)) * 1000 - planned
            labor = works.defs.infrastructure[item.kind]["labor"]
            amount = min(amount, room // labor) if labor > 0 else amount
            why = "funds"
            if amount <= 0:
                break
            item = _scale(works, item, amount)
        # 手元の資材は、雇える見込みの人手で進められる分だけ使う見込みにする（雇えない仕事に資材を
        # 割り当てて、後ろの工事を「資材が足りない」にしないように。実際の進みは工事の段で決まる）
        share = 1.0
        if cap is not None and item.labor > 0:
            share = min(1.0, max(0, cap * 1000 - planned) / item.labor)
        for good, qty in item.materials.items():
            have[good] = have.get(good, 0) - math.ceil(qty * share)
        state["items"].append(item)
        planned += item.labor
        if why == "funds":
            break
    hold = holdable(planned) - held
    funds.commit(island, PAYER, hold * wage)
    state["held"] = held + hold
    workers = -(-planned // 1000) - -(-state["labor"] // 1000)
    state["labor"] = planned
    if upkeep:
        # 維持の分（hire を維持と新しい工事に分けて呼ぶとき）。新しい工事を先に見た（計画経済で
        # 新しい工事の優先度が高い）ときも、維持で足した分だけを数える
        state["upkeep_labor"] = state.get("upkeep_labor", 0) + planned - before
    else:
        state["why"] = why
    hold = min(_material_cost(island, ctx, works, upkeep, have), funds.available(island, PAYER))
    funds.commit(island, PAYER, hold)
    state.setdefault("holds", []).append(hold)
    return workers


def _material_cost(
    island: IslandState, ctx: StageContext, works: Works, upkeep: bool, have: dict[str, int]
) -> int:
    """次のターンの仕事に市場で買う資材の代金の見込み（工事の資材の指値、家計の運賃の率で）。

    国の倉庫の在庫と、前のターンに売りに出ていた量までで見積もる（施設の工事の SiteFunds と同じ）。
    """
    from hakoniwa_core.economy.prices import material_limit
    from hakoniwa_core.units import money_of, part, to_basis

    have = {g: max(0, q) for g, q in have.items()}  # このターンの仕事に使った後に残る見込み
    if not upkeep:  # 新しい工事の見積もりでは、次のターンの維持の分が先に使う
        for item in works.upkeep(ahead=True):
            for good, qty in item.materials.items():
                have[good] = max(0, have.get(good, 0) - qty)
    items = works.upkeep(ahead=True) if upkeep else works.build(ahead=True)
    want: dict[str, int] = {}
    for item in items:
        for good, qty in item.materials.items():
            want[good] = want.get(good, 0) + qty
    warehouse = stock.totals(island.warehouse)
    supply: dict[str, int] = {}
    for f in island.facilities:
        for good, entry in f.economy.get("sales", {}).items():
            supply[good] = supply.get(good, 0) + entry[0]
    width = to_basis(ctx.config.get("market.band"))
    rate = island.economy.freight.get("household", 0)
    total = 0
    for good, qty in sorted(want.items()):
        need = qty - have.get(good, 0) - warehouse.get(good, 0)
        buy = min(need, supply.get(good, 0))
        if buy <= 0:
            continue
        amount = money_of(material_limit(island, ctx.config, good, width), buy)
        total += amount + part(amount, rate)
    return total


def hire(
    island: IslandState,
    free: dict[tuple[int, int], int],
    order: list,
    poach: Callable[[int], dict[tuple[int, int], int]] | None = None,
    poachable: int = 0,
    goal: int | None = None,
    final: bool = True,
    kept: dict[tuple[int, int], int] | None = None,
) -> dict:
    """雇用の段（施設の後、店の前）: 残った失業者から、島のどこからでも作業員を雇う。足りなければ
    poach(足りない人数) で店から引き抜く（ほかの施設と同じく、店の働き手は国の工事に回せる。
    引き抜けるのは poachable 人まで）。

    計画経済の島（PL-59）は、維持と新しい工事をそれぞれの優先度の位置で分けて呼ぶ（先の呼び出しは
    goal にそこまでの人数を渡し final を偽にする）。kept（雇い続けた前のターンの作業員。keep）は
    初めの呼び出しで渡す。人数とお金は呼び出しをまたいで足していき、final の呼び出しで締める。

    賃金を取り置いた人数を超えて雇えるのは、まだ使える公共事業費の分まで。雇えなかった分の取り置きを
    戻し、雇いに行けた人数（失業者と引き抜ける人）を次のターンの取り置きの目安（reach）に残し、
    マスごとの人数を返す。雇いたい人数に届かなければ、わけ（人手か公共事業費か）を工事の状況に書く
    （資材の足りない工事もあれば、それも残す。materials_waited）。
    """
    state = island.scratch.get(WORKS)
    if not state:
        return {}
    want = -(-state["labor"] // 1000)
    goal = want if goal is None else min(goal, want)
    wage = state["wage"]
    staff: dict[tuple[int, int], int] = state.setdefault("staff", {})
    for pos, n in (kept or {}).items():
        staff[pos] = staff.get(pos, 0) + n
        state["hired"] = state.get("hired", 0) + n
    hired = state.get("hired", 0)
    paid = state.setdefault("paid", state.get("held", want))  # 賃金を取り置いた人数
    target = goal
    if goal > paid and wage > 0:
        target = min(goal, paid + funds.available(island, PAYER) // wage)
    pool = sum(free.get(pos, 0) for pos in order)
    state["reach_now"] = max(state.get("reach_now", 0), hired + pool + poachable)
    left = target - hired
    for pos in order:
        if left <= 0:
            break
        take = min(free.get(pos, 0), left)
        if take > 0:
            staff[pos] = staff.get(pos, 0) + take
            free[pos] -= take
            left -= take
    if left > 0 and poach is not None:
        for pos, take in poach(left).items():
            staff[pos] = staff.get(pos, 0) + take
            left -= take
    hired = target - left
    state["hired"] = hired
    if hired > paid:
        funds.commit(island, PAYER, (hired - paid) * wage)
        paid = hired
    state["paid"] = paid
    if not final:
        return dict(staff)
    if hired < paid:
        funds.release(island, PAYER, (paid - hired) * wage)
        state["paid"] = hired
    reach = state["reach_now"]
    works_state(island)[REACH] = reach
    kept_now = {f"{x},{y}": n for (x, y), n in sorted(staff.items()) if n > 0}
    if kept_now:
        works_state(island)[STAFF] = kept_now
    else:
        works_state(island).pop(STAFF, None)
    if hired < want:  # 人が足りなければ、お金で止めたかにかかわらず人手
        state["why"] = "workers" if hired < target or reach < want else "funds"
    return dict(staff)


def split(island: IslandState) -> tuple[int, int]:
    """このターンの公共事業の (維持の作業員の人数, 全部の人数)。"""
    state = island.scratch.get(WORKS) or {}
    return -(-state.get("upkeep_labor", 0) // 1000), -(-state.get("labor", 0) // 1000)


def keep(island: IslandState, free: dict[tuple[int, int], int]) -> dict[tuple[int, int], int]:
    """計画経済の島（PL-59）: 前のターンの作業員を、このターンに要る人数まで、賃金を払える分だけ
    雇い続ける（free から取る。マスの y, x の順）。雇い続けた人数をマスごとに返し、hire の kept に
    渡す。今までは毎月失業者に戻り、人手の足りない施設が先に雇って抱え、工事に戻らなかった。"""
    state = island.scratch.get(WORKS)
    if not state:
        return {}
    want = -(-state["labor"] // 1000)
    wage = state["wage"]
    room = want
    if wage > 0:
        room = min(want, state.get("held", 0) + funds.available(island, PAYER) // wage)
    out: dict[tuple[int, int], int] = {}
    for pos, n in sorted(kept_staff(island).items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if room <= 0:
            break
        take = min(n, free.get(pos, 0), room)
        if take > 0:
            out[pos] = take
            free[pos] -= take
            room -= take
    return out


def holds(island: IslandState) -> list[int]:
    """雇用の段で取り置いた、次のターンの資材の代金の見込み（市場の段で戻す）。"""
    state = island.scratch.get(WORKS) or {}
    out = state.get("holds", [])
    state["holds"] = []
    return out


def payroll(island: IslandState) -> tuple[int, int]:
    """このターンの作業員の (人数, 賃金)。"""
    state = island.scratch.get(WORKS) or {}
    return state.get("hired", 0), state.get("wage", 0)


# --- 工事の段から -------------------------------------------------------------------


def progress(island: IslandState, ctx: StageContext) -> None:
    """雇えた人手と手元の資材の分だけ整備度を上げる（維持から先に人手を回す）。"""
    if not island.has_economy:
        return
    state = island.scratch.get(WORKS) or {"items": [], "hired": 0}
    held = works_stock(island)
    labor = state.get("hired", 0) * 1000
    built = done_any = 0
    works = Works(island, ctx)
    queued = {(k, x, y) for k, x, y in infra.queue(island)}
    raised: set[tuple[str, int, int]] = set()  # このターンに新しい工事が上げたマス
    for item in state["items"]:
        tile = island.tile(item.x, item.y)
        key = (item.kind, item.x, item.y)
        before = infra.level(tile, item.kind)
        # 同じマスの維持と新しい工事（IN-28）: 維持は目標まで。新しい工事が先に進んで目標より下を
        # 埋めた（計画経済で新しい工事の優先度が高い）マスは、後の維持が残りを 100% へ上げる。
        # 人手と資材は、上げられる分だけ使う
        up = item.upkeep and key not in raised
        room = (infra.keep(tile, item.kind) if up else infra.FULL) - before
        share = min(item.amount, max(0, room))
        if item.labor > 0 and share > 0:
            got = min(-(-item.labor * share // item.amount), labor)
            labor -= got
            share = min(share, item.amount * got // item.labor)
        for good, qty in item.materials.items():
            if qty > 0:
                share = min(share, stock.total(held, good) * item.amount // qty)
        if share <= 0:
            continue
        used = _scale(works, item, share).materials
        for good, qty in sorted(used.items()):
            stock.take(held, good, qty)
        if up:
            infra.set_level(tile, item.kind, min(infra.keep(tile, item.kind), before + share))
        else:
            infra.set_level(tile, item.kind, before + share)
            if not item.upkeep:
                raised.add(key)
        if not item.upkeep or key in queued:  # 並んだマスが進んだ（維持の分でも）
            built += 1
        done_any += 1
    queue = infra.queue(island)
    queue[:] = [
        [k, x, y]
        for k, x, y in queue
        if k in ctx.defs.infrastructure and infra.level(island.tile(x, y), k) < infra.FULL
    ]
    _forget_auto(island)
    forget_upkeep(island)
    _watch(island, state, bool(queue), built > 0, planning.is_planned(island, ctx.defs))
    if done_any:
        from hakoniwa_core.economy import freight

        freight.forget(island)  # 道のりが変わるので、運賃の率を計算し直す


def _watch(
    island: IslandState, state: dict[str, Any], waiting: bool, moved: bool, planned: bool = False
) -> None:
    """並んだ工事が進まないまま続いたら、わけをログで知らせる（1度だけ）。計画経済の島で人手が
    足りないときは、人員計画で人を空けられることも知らせる（PL-59）。"""
    works = works_state(island)
    if not waiting or moved:
        works.pop("stalled", None)
        works.pop("why", None)
        works.pop("short_materials", None)
        return
    why = state.get("why") or ""
    if not why and state.get("short_materials"):  # 並んだマスの維持の資材が足りない（IN-28）
        why = "materials"
    if not why:
        wanted = -(-state.get("labor", 0) // 1000)
        why = "workers" if state.get("hired", 0) < wanted else "funds"
    turns = works.get("stalled", 0) + 1
    works["stalled"] = turns
    works["why"] = why
    if state.get("short_materials"):  # 人手や公共事業費で止まっていても、資材の足りない工事がある
        works["short_materials"] = True
    else:
        works.pop("short_materials", None)
    if turns == STALL_TURNS:
        island.log.append(LogEntry(f"infrastructure_stalled_{why}", {}))
        if why == "funds":  # 年の予算と国庫のどちらで止まっているか
            island.log.append(LogEntry(f"funds_limit_{funds.limit_of(island, PAYER)}", {}))
        if why == "workers" and planned:
            island.log.append(LogEntry("infrastructure_stalled_staff_plan", {}))


# --- 市場の段から -------------------------------------------------------------------


def wants(
    island: IslandState, ctx: StageContext, upkeep: bool, ordered_only: bool = False
) -> dict[str, int]:
    """次のターンの維持（upkeep）か新しい工事に要る資材のうち、手元にない分（国の倉庫から使う前）。

    手元の工事の資材は、維持の分に先に充てる。ordered_only なら、新しい工事は命令で並べた分だけ。
    """
    if not island.has_economy:
        return {}
    works = Works(island, ctx)
    have = stock.totals(works_stock(island))
    if not upkeep:  # 維持の分を先に引いておく
        for item in works.upkeep(ahead=True):
            for good, qty in item.materials.items():
                have[good] = have.get(good, 0) - qty
    need: dict[str, int] = {}
    items = works.upkeep(ahead=True) if upkeep else works.build(True, ordered_only)
    for item in items:
        for good, qty in item.materials.items():
            need[good] = need.get(good, 0) + qty
    out = {}
    for good, qty in sorted(need.items()):
        missing = qty - max(0, have.get(good, 0))
        if missing > 0:
            out[good] = missing
    return out


def supply_from_warehouse(island: IslandState, ctx: StageContext, upkeep: bool) -> None:
    """次のターンの仕事に要る資材を、国の倉庫にある分から工事の資材に移す。役所が並べた工事の
    資材は倉庫から使わない（市場で買う。倉庫は遊ぶ人の備え）。"""
    from hakoniwa_core.economy import standing

    held = works_stock(island)
    for good, qty in sorted(wants(island, ctx, upkeep, ordered_only=True).items()):
        stock.put_batches(
            held,
            good,
            stock.take_batches(island.warehouse, good, standing.available(island, good, qty)),
        )


def received(island: IslandState, good: str, batches: list[list[int]]) -> None:
    """市場で買えた資材を工事の資材に入れる。"""
    stock.put_batches(works_stock(island), good, batches)


# --- 段 ---------------------------------------------------------------------------


class Infrastructure(IslandStage):
    """インフラの段: 整備度を傷みの分だけ下げ、統計を残す。"""

    name = "infrastructure"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs = ctx.defs
        from hakoniwa_core.economy import freight

        forget_upkeep(island)
        freight.forget_paths(island)
        wear = {k: round(v["wear"] * infra.FULL) for k, v in defs.infrastructure.items()}
        totals = dict.fromkeys(defs.infrastructure, 0)
        land = 0
        for tile in island.tiles:
            if not is_land(defs, tile.terrain):
                continue
            land += 1
            for kind in list(tile.infra):
                if kind not in defs.infrastructure:
                    continue
                now = infra.level(tile, kind)
                if island.has_economy and now > 0:
                    infra.set_level(tile, kind, now - wear[kind], infra.keep(tile, kind))
                totals[kind] += infra.level(tile, kind)
        for kind, total in sorted(totals.items()):
            island.stats[f"infra.{kind}"] = round(total / land / 10, 2) if land else 0.0
        island.stats["infra.queued"] = len(infra.queue(island))
        hired, _ = payroll(island)
        island.stats["works.workers"] = hired
        held = stock.totals(works_stock(island))
        for good, qty in held.items():
            island.stats[f"works.stock.{good}"] = from_milli(qty)
        if island.has_economy:
            _auto_works(island, ctx)
        island.stats["infra.auto_queued"] = len(works_state(island).get(AUTO, []))


# --- 役所の決まった事業（W-186 回4） ---------------------------------------------------


def _forget_auto(island: IslandState) -> None:
    """仕上がって列から外れた工事を、役所が並べた工事の一覧からも外す。"""
    state = works_state(island)
    if AUTO not in state:
        return
    queue = {tuple(e) for e in infra.queue(island)}
    state[AUTO] = [e for e in state[AUTO] if tuple(e) in queue]
    if not state[AUTO]:
        del state[AUTO]


def materials_waited(island: IslandState, goods: set[str]) -> bool:
    """goods のどれかを待っている施設の工事か、資材を待っているインフラの工事があるか。"""
    if any(not f.built and goods & set(f.ext.get("waiting", ())) for f in island.facilities):
        return True
    works = island.economy.infra
    waiting = works.get("why") == "materials" or works.get("short_materials")
    return bool(waiting) and works.get("stalled", 0) > 0


def road_tiles(
    island: IslandState, defs: Defs, config: GameConfig, pos: tuple[int, int]
) -> list[tuple[int, int]]:
    """pos とその周りの陸のマスのうち、交通の整備度が infrastructure.auto_works.road_level に
    届かず、まだ並んでいないマス。同じ資材を待っている工事があるあいだは空（道路の石材と建材を、施設の
    工事に回す）。"""
    kinds = defs.infra_kinds("transport")
    if not kinds:
        return []
    goods = {
        good
        for kind in kinds
        for key in ("materials", "fallback")
        for good in defs.infrastructure[kind].get(key, {})
    }
    if materials_waited(island, goods):
        return []
    level = round(config.get("infrastructure.auto_works.road_level") * infra.FULL)
    tiles = []
    for x, y in within(island, pos, 1):
        tile = island.tile(x, y)
        if not is_land(defs, tile.terrain) or infra.transport(defs, tile) >= level:
            continue
        if not infra.queued(island, kinds[0], x, y):
            tiles.append((x, y))
    return tiles


def water_tiles(island: IslandState, defs: Defs, config: GameConfig) -> list[tuple[int, int]]:
    """上下水道が足りずに育たない町（印 water_short）のマスと、人が密に住むのに上下水道が半分に
    届かない町（人数が population.deaths.crowd の半分以上。汚れた水から病気が広がり、亡くなる人が
    増える。回4）のマスのうち、まだ並んでいないマス（y, x の順）。"""
    kinds = defs.infra_kinds("water")
    if not kinds:
        return []
    housing = fac.housing_kind(config)
    crowded = config.get("population.deaths")["crowd"] // 2

    def wanted(f) -> bool:
        tile = island.tile(f.x, f.y)
        water = infra.water(defs, tile)
        if f.ext.get("water_short"):
            return water < infra.FULL
        return tile.population >= crowded and water < infra.FULL // 2

    return [
        (f.x, f.y)
        for f in sorted(island.facilities, key=lambda f: (f.y, f.x))
        if f.kind == housing and not infra.queued(island, kinds[0], f.x, f.y) and wanted(f)
    ]


def _cost(island: IslandState, works: Works, item: Item, wage: int) -> int:
    """仕事 item の資材（今の島の値段）と作業員の賃金の額。"""
    prices = island.economy.prices
    total = item.labor * wage // 1000
    for good, qty in item.materials.items():
        total += money_of(prices.get(good, 1), qty)
    return total


def _auto_works(island: IslandState, ctx: StageContext) -> None:
    """役所の決まった事業: 公共事業費の月割りに残りがあれば、上下水道か道路の工事を1か所分並べる。

    残り = 月割り − このターンに公共事業費から使った額 − 並んだ工事と維持の次のターンの見込み
    （作業員はこのターンの賃金、資材は工事の資材に持っていない分を今の値段で）。1か所分（その町の
    マスか、施設・町のまわりのマス）のうち、維持の目標から仕上げるまでの費用（目標までは維持の工事が
    戻すので数えない。IN-28）を工期で割った1ターン分が残りに収まるマスまでを、順に並べる。"""
    state = works_state(island)
    if state.get(AUTO_OFF):
        return
    cap = funds.fiscal_state(island)["caps"].get(CONSTRUCTION)
    wage = (island.scratch.get(WORKS) or {}).get("wage") or island.economy.average_wage
    if not cap or wage <= 0:
        return
    defs, config = ctx.defs, ctx.config
    works = Works(island, ctx)
    used = funds.fiscal_state(island)["used"].get(CONSTRUCTION, 0)
    labor = sum(i.labor for i in works.upkeep(ahead=True) + works.build(ahead=True))
    prices = island.economy.prices
    buying = 0
    for upkeep in (True, False):
        for good, qty in wants(island, ctx, upkeep).items():
            buying += money_of(prices.get(good, 1), qty)
    room = cap - used - labor * wage // 1000 - buying
    if room <= 0:
        return
    sites: list[tuple[str, list[tuple[int, int]]]] = []
    water = defs.infra_kinds("water")
    if water:
        sites += [(water[0], [pos]) for pos in water_tiles(island, defs, config)]
    roads = defs.infra_kinds("transport")
    if roads:  # 施設と町のまわり（y, x の順に、引くマスの残る最初の1か所）
        places = sorted(
            {(f.x, f.y) for f in island.facilities if fac.on_map(f) and f.built},
            key=lambda p: (p[1], p[0]),
        )
        for pos in places:
            tiles = road_tiles(island, defs, config, pos)
            if tiles:
                sites.append((roads[0], tiles))
                break
    for kind, tiles in sites:
        picked = []
        for x, y in tiles:
            tile = island.tile(x, y)
            left = infra.FULL - max(infra.level(tile, kind), infra.keep(tile, kind))
            if left <= 0:  # 目標が 100% のマスは、維持の工事が戻す
                continue
            whole = works.cost(Item(kind, x, y, left, False))
            monthly = _cost(island, works, whole, wage) * works.step(kind) // infra.FULL
            if monthly > room:
                break
            room -= monthly
            picked.append((x, y))
        if not picked:
            continue
        tiles = picked
        count = infra.enqueue(island, kind, tiles)
        if count:
            state.setdefault(AUTO, []).extend(
                [kind, x, y] for x, y in tiles if [kind, x, y] not in state.get(AUTO, [])
            )
            forget_upkeep(island)
            from hakoniwa_core.economy import freight

            freight.forget_paths(island)
            island.log.append(LogEntry("public_works_auto", {"infra": kind, "count": count}))
        return


# --- 命令 -------------------------------------------------------------------------


def _check_tiles(island: IslandState, payload: dict[str, Any], defs: Defs, kind: str) -> None:
    for x, y in payload["tiles"]:
        if not island.in_bounds(x, y):
            raise OrderFailed("reason_out_of_bounds")
    if all(
        not is_land(defs, island.tile(x, y).terrain)
        or infra.level(island.tile(x, y), kind) >= infra.FULL
        or infra.queued(island, kind, x, y)
        for x, y in payload["tiles"]
    ):
        raise OrderFailed("reason_already_done")


def check_build_infrastructure(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["kind"] not in defs.infrastructure:
        raise OrderFailed("reason_unknown_infrastructure")
    _check_tiles(island, payload, defs, payload["kind"])


def build_infrastructure(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """インフラの工事を列に並べる（海のマスと、もう 100% のマスは並べない）。

    お金の経済がまだない島（フェーズ1の島）は、すぐに 100% にする。
    """
    lay(island, ctx.defs, payload["kind"], [tuple(t) for t in payload["tiles"]])


def lay(island: IslandState, defs: Defs, kind: str, tiles: list[tuple[int, int]]) -> int:
    """命令 build_infrastructure と build_power_line の中身。並べた（引いた）マスの数を返す。"""
    from hakoniwa_core.economy import freight

    land = [(x, y) for x, y in tiles if is_land(defs, island.tile(x, y).terrain)]
    forget_upkeep(island)
    freight.forget_paths(island)
    if island.has_economy:
        count = infra.enqueue(island, kind, land)
        island.log.append(LogEntry("infrastructure_ordered", {"infra": kind, "count": count}))
        return count
    count = 0
    for x, y in land:
        tile = island.tile(x, y)
        if infra.level(tile, kind) < infra.FULL:
            infra.set_level(tile, kind, infra.FULL)
            count += 1
    island.log.append(LogEntry("infrastructure_built", {"infra": kind, "count": count}))
    return count


def set_public_works(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """役所の決まった事業を始める・止める（止めても、もう並べた工事はそのまま進める）。維持と新しい
    工事の優先度（upkeep_priority・build_priority）も書いたものだけ変える（PL-59。資材の買い注文の
    順と、計画経済の島で作業員を雇う位置）。"""
    state = works_state(island)
    if "auto" in payload:
        if payload["auto"]:
            state.pop(AUTO_OFF, None)
            island.log.append(LogEntry("public_works_on", {}))
        else:
            state[AUTO_OFF] = True
            island.log.append(LogEntry("public_works_off", {}))
    for key in (UPKEEP_PRIORITY, BUILD_PRIORITY):
        if key in payload:
            state[key] = payload[key]
    if UPKEEP_PRIORITY in payload or BUILD_PRIORITY in payload:
        args = {
            "upkeep": priority(island, ctx.config, True),
            "build": priority(island, ctx.config, False),
        }
        island.log.append(LogEntry("public_works_priority", args))


def register(registry: Registry) -> None:
    registry.add_stage(Infrastructure())
    registry.add_order(OrderHandler("set_public_works", execute=set_public_works))
    registry.add_order(
        OrderHandler(
            "build_infrastructure",
            execute=build_infrastructure,
            check=check_build_infrastructure,
        )
    )
