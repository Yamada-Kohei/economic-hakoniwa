"""インフラ（フェーズ2-3）: 工事の命令と、公共事業（インフラの工事と維持）、傷みの段。

公共事業は施設ではなく、島に1つの「公共事業」としてまとめて動かします。毎ターン、先に維持
（傷んだ分を目標まで戻す。施設と町のマスとその隣から）、次に工事（並んだ順。1マスは1ターンに
「100% ÷ 工期」まで）の量を決め、資材と人手はその量に1マス分の費用（infrastructure.yaml）を
掛けた分です。

- 雇用の段（modules/employment）: 維持の分は施設より先に、工事の分は施設の後に、作業員の賃金と
  次のターンの資材の代金の見込みを公共事業費（construction）から取り置く。作業員は施設が雇った
  後に残った失業者から、通勤圏を見ずに島のどこからでも、島の平均賃金で雇う（引き抜かない）。
- 工事の段（modules/construction）: 雇えた人手と手元の資材の少ない方の割合だけ整備度を上げる。
  進まないまま2ターン続いたら、わけ（公共事業費・人手・資材）をログで知らせる。
- 賃金の段（modules/labor）: 作業員の賃金を国庫から払う。
- 市場の段（modules/market）: 次のターンの資材を、国の倉庫にある分を先に使い、足りない分を市場で
  値幅の上端の指値で買う（運賃の率は家計と同じ）。維持の分は施設の工事より先に、新しい工事の分は
  後に注文する。
- インフラの段（この段。町の段の後）: 整備度を表の傷みの分だけ下げ、統計を残す。

資材・運賃・作業員の賃金は、施設の工事と同じく工事の部門（GDP の投資）に数えます。インフラは
政府の資産として記帳しません。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import CONSTRUCTION, Payer, funds
from hakoniwa_core.facilities import is_land, producible_goods
from hakoniwa_core.geo import NEIGHBORS8
from hakoniwa_core.ledger import GOV
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import from_milli

WORKS = "works"  # island.scratch: このターンの公共事業の計画と雇えた人手
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
        self._made: set[str] | None = None

    # --- 1マス分の費用 -------------------------------------------------------------

    def materials(self, kind: str) -> dict[str, int]:
        """1マスを 0% から 100% にする資材。島で作れない財があれば fallback を使う。"""
        spec = self.defs.infrastructure[kind]
        if "fallback" in spec:
            if self._made is None:
                self._made = producible_goods(self.defs, self.island.resources)
            if not set(spec["materials"]) <= self._made:
                return dict(spec["fallback"])
        return dict(spec["materials"])

    def step(self, kind: str) -> int:
        """1マスを1ターンに上げられる整備度の上限（100% ÷ 工期）。"""
        return -(-infra.FULL // self.defs.infrastructure[kind]["turns"])

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
        並んでいるマスと種類は、新しい工事の方で上げるので数えない。
        """
        island = self.island
        near: set[tuple[int, int]] = set()
        for f in island.facilities:
            if fac.on_map(f):
                near.add((f.x, f.y))
                near.update((f.x + dx, f.y + dy) for dx, dy in NEIGHBORS8)
        queued = {(k, x, y) for k, x, y in infra.queue(island)}
        items = []
        for tile in island.tiles:
            for kind in sorted(tile.infra):
                if kind not in self.defs.infrastructure or (kind, tile.x, tile.y) in queued:
                    continue
                target = infra.keep(tile, kind)
                now = infra.level(tile, kind)
                if ahead:
                    now = max(0, now - self.wear(kind))
                if now < target:
                    amount = min(target - now, self.step(kind))
                    items.append(Item(kind, tile.x, tile.y, amount, True))
        items.sort(key=lambda i: ((i.x, i.y) not in near, i.y, i.x, i.kind))
        return [self.cost(i) for i in items]

    def build(self, ahead: bool = False) -> list[Item]:
        """新しい工事の仕事（工事の列の順）。

        ahead なら、次のターンの分（このターンの終わりの傷みを足した分）を見積もる。傷みの分を
        買い足さないと、仕上がりの近いマスが毎ターン傷みの分だけ足りずに 100% に届かない。
        """
        items = []
        for kind, x, y in infra.queue(self.island):
            if kind not in self.defs.infrastructure:
                continue
            now = infra.level(self.island.tile(x, y), kind)
            if ahead and now > 0:
                now = max(0, now - self.wear(kind))
            if now < infra.FULL:
                amount = min(infra.FULL - now, self.step(kind))
                items.append(self.cost(Item(kind, x, y, amount, False)))
        return items


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


def plan(island: IslandState, ctx: StageContext, upkeep: bool, wage: int) -> int:
    """雇用の段: このターンの維持（upkeep）か新しい工事の量を、手元の工事の資材と公共事業費で決め、
    作業員の賃金と、次のターンに市場で買う資材の代金の見込みを取り置く。取り置いた見込みは市場の段で
    戻す（_holds）。雇いたい人数を返す。"""
    if not island.has_economy:
        return 0
    works = Works(island, ctx)
    state = island.scratch.setdefault(
        WORKS,
        {"items": [], "labor": 0, "wage": wage, "have": stock.totals(works_stock(island))},
    )
    have = state["have"]
    left = funds.available(island, PAYER)
    planned = state["labor"]
    hired_before = -(-planned // 1000)
    why = ""
    candidates = works.upkeep() if upkeep else works.build()
    for item in candidates:
        amount = _fit(item, have)
        if amount < item.amount and not why:
            why = "materials"
        if amount <= 0:
            continue
        item = _scale(works, item, amount)
        workers = -(-(planned + item.labor) // 1000) - hired_before
        if workers * wage > left:  # 公共事業費が足りなければ、払える人数の分まで
            room = (hired_before + left // max(1, wage)) * 1000 - planned
            labor = works.defs.infrastructure[item.kind]["labor"]
            amount = min(amount, room // labor) if labor > 0 else amount
            why = "funds"
            if amount <= 0:
                break
            item = _scale(works, item, amount)
        for good, qty in item.materials.items():
            have[good] = have.get(good, 0) - qty
        state["items"].append(item)
        planned += item.labor
        if why == "funds":
            break
    workers = -(-planned // 1000) - hired_before
    funds.commit(island, PAYER, workers * wage)
    state["labor"] = planned
    if not upkeep:
        state["why"] = why
    hold = min(_material_cost(island, ctx, works, upkeep, have), funds.available(island, PAYER))
    funds.commit(island, PAYER, hold)
    state.setdefault("holds", []).append(hold)
    return workers


def _material_cost(
    island: IslandState, ctx: StageContext, works: Works, upkeep: bool, have: dict[str, int]
) -> int:
    """次のターンの仕事に市場で買う資材の代金の見込み（値幅の上端、家計の運賃の率で）。

    国の倉庫の在庫と、前のターンに売りに出ていた量までで見積もる（施設の工事の SiteFunds と同じ）。
    """
    from hakoniwa_core.market import auction
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
        _, high = auction.band(island.economy.prices.get(good, 1), width)
        amount = money_of(high, buy)
        total += amount + part(amount, rate)
    return total


def hire(island: IslandState, free: dict[tuple[int, int], int], order: list) -> dict:
    """雇用の段の終わり: 施設が雇った後に残った失業者から、島のどこからでも作業員を雇う。

    雇えなかった分の賃金の取り置きを戻し、マスごとの人数を返す。
    """
    state = island.scratch.get(WORKS)
    if not state:
        return {}
    want = -(-state["labor"] // 1000)
    staff: dict[tuple[int, int], int] = {}
    left = want
    for pos in order:
        if left <= 0:
            break
        take = min(free.get(pos, 0), left)
        if take > 0:
            staff[pos] = take
            free[pos] -= take
            left -= take
    hired = want - left
    state["hired"] = hired
    state["staff"] = staff
    funds.release(island, PAYER, left * state["wage"])
    return staff


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
    for item in state["items"]:
        share = item.amount
        if item.labor > 0:
            got = min(item.labor, labor)
            labor -= got
            share = item.amount * got // item.labor
        for good, qty in item.materials.items():
            if qty > 0:
                share = min(share, stock.total(held, good) * item.amount // qty)
        if share <= 0:
            continue
        used = _scale(works, item, share).materials
        for good, qty in sorted(used.items()):
            stock.take(held, good, qty)
        tile = island.tile(item.x, item.y)
        before = infra.level(tile, item.kind)
        if item.upkeep:
            infra.set_level(tile, item.kind, min(infra.keep(tile, item.kind), before + share))
        else:
            infra.set_level(tile, item.kind, before + share)
            built += 1
        done_any += 1
    queue = infra.queue(island)
    queue[:] = [
        [k, x, y]
        for k, x, y in queue
        if k in ctx.defs.infrastructure and infra.level(island.tile(x, y), k) < infra.FULL
    ]
    _watch(island, state, bool(queue), built > 0)
    if done_any:
        from hakoniwa_core.economy import freight

        freight.forget(island)  # 道のりが変わるので、運賃の率を計算し直す


def _watch(island: IslandState, state: dict[str, Any], waiting: bool, moved: bool) -> None:
    """並んだ工事が進まないまま続いたら、わけをログで知らせる（1度だけ）。"""
    works = works_state(island)
    if not waiting or moved:
        works.pop("stalled", None)
        works.pop("why", None)
        return
    why = state.get("why") or ""
    if not why:
        wanted = -(-state.get("labor", 0) // 1000)
        why = "workers" if state.get("hired", 0) < wanted else "funds"
    turns = works.get("stalled", 0) + 1
    works["stalled"] = turns
    works["why"] = why
    if turns == STALL_TURNS:
        island.log.append(LogEntry(f"infrastructure_stalled_{why}", {}))


# --- 市場の段から -------------------------------------------------------------------


def wants(island: IslandState, ctx: StageContext, upkeep: bool) -> dict[str, int]:
    """次のターンの維持（upkeep）か新しい工事に要る資材のうち、手元にない分（国の倉庫から使う前）。

    手元の工事の資材は、維持の分に先に充てる。
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
    for item in works.upkeep(ahead=True) if upkeep else works.build(ahead=True):
        for good, qty in item.materials.items():
            need[good] = need.get(good, 0) + qty
    out = {}
    for good, qty in sorted(need.items()):
        missing = qty - max(0, have.get(good, 0))
        if missing > 0:
            out[good] = missing
    return out


def supply_from_warehouse(island: IslandState, ctx: StageContext, upkeep: bool) -> None:
    """次のターンの仕事に要る資材を、国の倉庫にある分から工事の資材に移す。"""
    held = works_stock(island)
    for good, qty in sorted(wants(island, ctx, upkeep).items()):
        stock.put_batches(held, good, stock.take_batches(island.warehouse, good, qty))


def received(island: IslandState, good: str, batches: list[list[int]]) -> None:
    """市場で買えた資材を工事の資材に入れる。"""
    stock.put_batches(works_stock(island), good, batches)


# --- 段 ---------------------------------------------------------------------------


class Infrastructure(IslandStage):
    """インフラの段: 整備度を傷みの分だけ下げ、統計を残す。"""

    name = "infrastructure"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs = ctx.defs
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
    land = [(x, y) for x, y in tiles if is_land(defs, island.tile(x, y).terrain)]
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


def register(registry: Registry) -> None:
    registry.add_stage(Infrastructure())
    registry.add_order(
        OrderHandler(
            "build_infrastructure",
            execute=build_infrastructure,
            check=check_build_infrastructure,
        )
    )
