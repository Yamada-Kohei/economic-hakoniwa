"""通勤と雇用: 施設に働き手を割り当てる段。

各施設は、操業目標の分だけ（工事中は手元の資材で進められる分だけ）雇いたい人数を決め、払える
賃金の分までに抑えます。そのうえで次の順に雇います。

1. 前のターンに雇っていた人を雇い続ける。雇いたい人数より多ければ、遠いマスの人から減らす。
2. 賃金の高い施設から順に（同じ賃金なら優先順位の高い順、番号の小さい順）、足りない人数を
   通勤圏（既定3マス）の失業者から、近いマスの順に雇う。
3. それでも足りなければ、通勤圏の中で賃金が labor.poach_gap 以上安い施設の働き手を、安い施設から
   引き抜く。1つの施設から引き抜かれるのは1ターンに働き手の labor.poach_limit まで。

工事の作業員と公務員は前のターンの島の平均賃金で雇います。運送業は通勤圏を見ず島のどこからでも、
前のターンの運賃 × freight.labor_share ÷ 賃金 の人数を雇い、足りない割合だけ運賃を割り増します。
施設ごと・マスごとに雇った人数は、施設の economy["staff"] に残します。雇われなかった人は失業し、
村の住民は自給の農業に戻ります（人口の段で扱う）。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import (
    freight,
    funds,
    is_public_service,
    is_transport,
    payer_of,
)
from hakoniwa_core.geo import distance, within
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, money_of, part, to_basis

Pos = tuple[int, int]


def _key(pos: Pos) -> str:
    return f"{pos[0]},{pos[1]}"


def _pos(key: str) -> Pos:
    x, y = key.split(",")
    return int(x), int(y)


def staff_of(f: Facility) -> dict[Pos, int]:
    return {_pos(k): v for k, v in f.economy.get("staff", {}).items() if v > 0}


class Employment(IslandStage):
    name = "employment"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        labor = {(t.x, t.y): fac.labor_force(defs, island, t) for t in island.tiles if t.population}
        free = dict(labor)
        everywhere = within(island, island.capital or (0, 0), island.size)
        plans = []
        for f in island.facilities:
            wage = self._wage(island, ctx, f)
            if wage is None:
                f.workers = 0
                continue
            f.economy["wage"] = wage
            plans.append((f, wage))
        plans.sort(key=lambda p: (-p[1], -fac.priority_of(defs, config, p[0]), p[0].id))
        staff: dict[int, dict[Pos, int]] = {}
        wanted: dict[int, int] = {}
        # 雇いたい人数を決め、その賃金を取り置く（高い賃金の施設から）
        for f, wage in plans:
            want = self._wanted(island, ctx, f, wage)
            payer = payer_of(defs, config, f)
            per_worker = wage + self._power_per_worker(island, ctx, f)
            if per_worker > 0:
                want = min(want, funds.available(island, payer) // per_worker)
            want = max(0, want)
            wanted[f.id] = want
            funds.commit(island, payer, want * wage)
        # 1. 雇い続ける（遠いマスの人から減らす）
        for f, _ in plans:
            keep: dict[Pos, int] = {}
            left = wanted[f.id]
            for pos, count in sorted(
                staff_of(f).items(), key=lambda kv: (self._dist(f, kv[0]), kv[0][1], kv[0][0])
            ):
                take = min(count, left, free.get(pos, 0))
                if take > 0:
                    keep[pos] = take
                    free[pos] -= take
                    left -= take
            staff[f.id] = keep
        start = {fid: sum(s.values()) for fid, s in staff.items()}
        poached: dict[int, int] = {}
        limit = config.get("labor.poach_limit")
        gap = config.get("labor.poach_gap")
        by_id = {f.id: (f, wage) for f, wage in plans}
        # 2. 失業者を雇う 3. 安い施設から引き抜く
        for f, wage in plans:
            missing = wanted[f.id] - sum(staff[f.id].values())
            if missing <= 0:
                continue
            area = everywhere if self._island_wide(ctx, f) else self._area(island, ctx, f)
            for pos in area:
                if missing <= 0:
                    break
                take = min(free.get(pos, 0), missing)
                if take > 0:
                    staff[f.id][pos] = staff[f.id].get(pos, 0) + take
                    free[pos] -= take
                    missing -= take
            if missing <= 0:
                continue
            reach = set(area)
            victims = sorted(
                (
                    (other_wage, other.id)
                    for other, other_wage in plans
                    if other.id != f.id and other_wage <= wage * (1 - gap)
                ),
            )
            for _, vid in victims:
                if missing <= 0:
                    break
                victim, _ = by_id[vid]
                room = int(start.get(vid, 0) * limit) - poached.get(vid, 0)
                if room <= 0:
                    continue
                for pos in sorted(
                    (p for p in staff[vid] if p in reach),
                    key=lambda p: (self._dist(f, p), p[1], p[0]),
                ):
                    take = min(staff[vid][pos], missing, room)
                    if take <= 0:
                        continue
                    staff[vid][pos] -= take
                    staff[f.id][pos] = staff[f.id].get(pos, 0) + take
                    poached[vid] = poached.get(vid, 0) + take
                    missing -= take
                    room -= take
                    if missing <= 0 or room <= 0:
                        break
        # 雇えなかった分の賃金の取り置きを戻し、結果を書く
        employed: dict[Pos, int] = {}
        for f, wage in plans:
            mine = {p: n for p, n in staff[f.id].items() if n > 0}
            hired = sum(mine.values())
            f.workers = hired
            f.economy["staff"] = {_key(p): n for p, n in sorted(mine.items())}
            f.economy["wanted"] = wanted[f.id]
            f.economy["poached"] = poached.get(f.id, 0)
            funds.release(island, payer_of(defs, config, f), (wanted[f.id] - hired) * wage)
            for pos, n in mine.items():
                employed[pos] = employed.get(pos, 0) + n
            if is_transport(config, f):
                self._surcharge(island, ctx, wanted[f.id], hired)
        for tile in island.tiles:
            count = employed.get((tile.x, tile.y), 0)
            if count:
                tile.ext["employed"] = count
            else:
                tile.ext.pop("employed", None)

    # --- 施設ごとの決まり ------------------------------------------------------------

    @staticmethod
    def _wage(island: IslandState, ctx: StageContext, f: Facility) -> int | None:
        """この施設の賃金。人を雇わない施設は None。"""
        config = ctx.config
        if fac.is_housing(config, f):
            return None
        if not f.built or is_public_service(ctx.defs, f):
            wage = island.economy.average_wage or config.get("labor.initial_wage")
            return max(wage, island.policy.minimum_wage)
        if "wage" not in f.economy:
            f.economy["wage"] = island.economy.average_wage or config.get("labor.initial_wage")
        return f.economy["wage"]

    @staticmethod
    def _wanted(island: IslandState, ctx: StageContext, f: Facility, wage: int) -> int:
        if is_transport(ctx.config, f):
            return freight.payroll(island, ctx.config) // max(1, wage)
        return fac.labor_demand(ctx.defs, f, ctx.turn, ctx.config.get("power.good"))

    @staticmethod
    def _power_per_worker(island: IslandState, ctx: StageContext, f: Facility) -> int:
        """全力で動いたときの電気代を、働き手1人あたりにした額（値幅の上端の値段で）。"""
        if not f.built:
            return 0
        power = ctx.config.get("power.good")
        use = fac.recipe_of(ctx.defs, f)["inputs"].get(power, 0)
        workers = fac.workers_needed(ctx.defs, f.kind)
        if not use or not workers:
            return 0
        price = island.economy.prices.get(power, 0)
        high = price + part(price, to_basis(ctx.config.get("market.band")))
        return -(-money_of(high, use) // workers)

    @staticmethod
    def _island_wide(ctx: StageContext, f: Facility) -> bool:
        return is_transport(ctx.config, f) or not fac.on_map(f)

    @staticmethod
    def _area(island: IslandState, ctx: StageContext, f: Facility) -> list[Pos]:
        return within(island, (f.x, f.y), fac.commute_radius(ctx.defs, ctx.config, f.kind))

    @staticmethod
    def _dist(f: Facility, pos: Pos) -> int:
        return distance((f.x, f.y), pos) if fac.on_map(f) else 0

    @staticmethod
    def _surcharge(island: IslandState, ctx: StageContext, wanted: int, hired: int) -> None:
        """運送業の人手が足りない割合だけ、運賃の率を割り増す（最大 freight.max_surcharge 倍）。"""
        top = to_basis(ctx.config.get("freight.max_surcharge"))
        short = (wanted - hired) * BASIS // wanted if wanted > 0 else 0
        island.economy.freight["surcharge"] = min(top, BASIS + short)


def unemployment_rate(island: IslandState, ctx: StageContext, f: Facility) -> float:
    """施設の通勤圏の失業率（運送業とマスを持たない施設は島全体）。

    村で自給の農業をしている人は、統計と同じく失業者に数えない。
    """
    defs = ctx.defs
    if not fac.on_map(f) or is_transport(ctx.config, f):
        tiles = [t for t in island.tiles if t.population]
    else:
        area = within(island, (f.x, f.y), fac.commute_radius(defs, ctx.config, f.kind))
        tiles = [island.tile(*p) for p in area if island.tile(*p).population]
    share = fac.labor_share(defs, island)
    labor = idle = 0
    for t in tiles:
        force = fac.labor_force(defs, island, t)
        free = max(0, force - t.ext.get("employed", 0))
        labor += force
        idle += max(0, free - int(subsistence_persons(defs, island, t) * share))
    return idle / labor if labor else 0.0


def register(registry: Registry) -> None:
    registry.add_stage(Employment())
