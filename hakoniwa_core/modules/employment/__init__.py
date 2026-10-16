"""通勤と雇用: 施設に働き手を割り当てる段。

各施設は、操業目標の分だけ（工事中は手元の資材で進められる分だけ）雇いたい人数を決め、払える
賃金の分までに抑えます。そのうえで次の順に雇います。

1. 前のターンに雇っていた人を雇い続ける。雇いたい人数より多ければ、遠いマスの人から減らす。
2. 賃金の高い施設から順に（同じ賃金なら優先順位の高い順、番号の小さい順）、足りない人数を
   通勤圏（既定3マス）の失業者から、近いマスの順に雇う。
3. それでも足りなければ、通勤圏の中で賃金が labor.poach_gap 以上安い施設の働き手を、安い施設から
   引き抜く。1つの施設から引き抜かれるのは1ターンに働き手の labor.poach_limit まで。

技術者（フェーズ2-3）は島全体の人数として持ち、通勤圏を見ずに島のどこの施設にも通います。施設の
定義の技術者の数（workers の engineer）に、雇いたい人数の割合を掛けた分を技術者の席とし、2 の前に、
前のターンの技術者を雇い続けたうえで、技術者の賃金の高い施設から順に割り当てます。埋まらなかった
技術者の席は一般の労働者の席にします（技術者が足りない分、稼働率の上限が下がる。
facilities.engineer_cap）。3 の後に余った技術者は、まだ埋まらない一般の席に、賃金の高い施設から
島のどこへでも就きます（賃金は一般のもの）。

インフラの公共事業（modules/infrastructure）は、維持の分を施設より先に、新しい工事の分を施設の
後に、作業員の賃金と次のターンの資材の代金の見込みを取り置き、作業員は施設が雇った後に残った
失業者から、島のどこからでも雇います（引き抜かない）。

工事の作業員と公務員は前のターンの島の平均賃金で雇います。工事は、作業員の賃金に続けて、この
ターンの市場で買う資材の代金の見込みも取り置きます（公共事業費は、賃金と資材の両方に優先度の高い
工事から充てる。hakoniwa_core/modules/market の SiteFunds）。運送業は通勤圏を見ず島のどこからでも、
前のターンの運賃 × freight.labor_share ÷ 賃金 の人数を雇い、足りない割合だけ運賃を割り増します
（フェーズ3-2 から、燃料のあるトラックで替えた運転手の分は雇わない。economy/trucks.py）。
港・海運業・商社（フェーズ3-1）は、扱った荷の量と船の数に合わせて雇います（hakoniwa_core/trade）。
労働の動員（全体主義。フェーズ3-4）をしている島では、最後に残った失業者を、物を作る国営の施設の
空いた席に島のどこからでも、動員の賃金で割り当てます（_mobilize）。
施設ごと・マスごとに雇った人数は、施設の economy["staff"] に残します。雇われなかった人は失業し、
村の住民は自給の農業に戻ります（人口の段で扱う）。

賃金の遅配（バランス調整の回3。economy/arrears.py）: 雇う前に、前のターンまでの未払い賃金と未払いの
税を、使えるお金の分だけ先に払います。そのうえで払える賃金の分より雇いたい人数が多い動いている施設
では、前のターンに働いていた人のうち払えない分も、払われないまま働き続けます（economy["unpaid"]。
賃金の段で未払い賃金になる）。未払いが続いた月数 m（economy["arrears_months"]）に応じて、そのうち
labor.arrears_quit × (m + 1) の割合が毎ターン辞めます（すぐ全員は辞めない）。
"""

from __future__ import annotations

import math

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.economy import (
    Payer,
    arrears,
    banks,
    freight,
    funds,
    is_bank,
    is_public_service,
    is_stopped,
    is_transport,
    payer_of,
    trucks,
)
from hakoniwa_core.geo import distance, within
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules.market import SITE_HOLDS, SiteFunds
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState
from hakoniwa_core.trade import staff
from hakoniwa_core.trade import staff as staff_module
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
        if island.has_economy:
            settle_arrears(island)
        labor = {(t.x, t.y): fac.labor_force(defs, island, t) for t in island.tiles if t.population}
        free = dict(labor)
        everywhere = within(island, island.capital or (0, 0), island.size)
        plans = []
        for f in island.facilities:
            wage = self._wage(island, ctx, f)
            if wage is None:
                f.workers = 0
                f.ext.pop("short_wages", None)
                continue
            f.economy["wage"] = wage
            plans.append((f, wage))
        plans.sort(key=lambda p: (-p[1], -fac.priority_of(defs, config, p[0]), p[0].id))
        staff: dict[int, dict[Pos, int]] = {}
        wanted: dict[int, int] = {}
        seats: dict[int, int] = {}  # 技術者の席
        sites: SiteFunds | None = None
        works_wage = self._site_wage(island, ctx)
        # インフラの維持は施設より先に取り置く（施設の工事の賃金で公共事業費を使い切らないように）
        infrastructure.plan(island, ctx, upkeep=True, wage=works_wage)
        # 雇いたい人数を決め、その賃金を取り置く（高い賃金の施設から）
        unpaid: dict[int, int] = {}  # 払えないまま働き続ける人（賃金の遅配。回3）
        quit_rate = config.get("labor.arrears_quit")
        for f, wage in plans:
            demand = self._wanted(island, ctx, f, wage)
            payer = payer_of(defs, config, f)
            engineers, extra = self._engineer_terms(island, ctx, f, wage)
            per_worker = wage + self._power_per_worker(island, ctx, f) + extra
            want = demand
            money = funds.available(island, payer)
            if per_worker > 0:
                want = min(want, money // per_worker)
            want = max(0, want)
            unpaid[f.id] = 0
            # 年の予算の月割りで止まっている（お金はある）ときは遅配にしない
            short = money >= funds.available(island, payer, capped=False)
            if want < demand and short and f.built and island.has_economy:
                # 前のターンに働いていた人は、払えない分もすぐには辞めない（未払いが続いた月数に
                # 応じて辞める）
                stay = max(0, min(demand, sum(staff_of(f).values())) - want)
                share = min(1.0, quit_rate * (f.economy.get("arrears_months", 0) + 1))
                unpaid[f.id] = stay - min(stay, math.ceil(stay * share))
            wanted[f.id] = want + unpaid[f.id]
            seats[f.id] = 0
            if engineers:
                need = fac.workers_needed(defs, f.kind)
                seats[f.id] = engineers * want // need
            if seats[f.id]:
                f.economy["engineer_wage"] = fac.engineer_wage_of(config, f, wage)
            cost = want * wage + seats[f.id] * (fac.engineer_wage_of(config, f, wage) - wage)
            funds.commit(island, payer, cost)
            # 賃金を払うお金が足りずに雇いたい分を雇えなかったことは、施設の状態に出す（人手不足と
            # 分けて、国庫が空で止まった施設が「労働者不足」と出ないように）
            if want < demand:
                f.ext["short_wages"] = True
            else:
                f.ext.pop("short_wages", None)
            if not f.built and island.has_economy:
                # 工事は、市場で買う資材の代金も続けて取り置く（作業員の賃金と資材の代金の両方に、
                # 優先度の高い工事から公共事業費を充てる）
                sites = sites or SiteFunds(island, ctx)
                hold = min(sites.cost(f, want), funds.available(island, payer))
                funds.commit(island, payer, hold)
                island.scratch.setdefault(SITE_HOLDS, []).append((payer, hold))
        infrastructure.plan(island, ctx, upkeep=False, wage=works_wage)
        # 0. 技術者を割り当て、埋まらなかった技術者の席を一般の席にする
        engineers = self._assign_engineers(island, ctx, plans, seats)
        general = {fid: wanted[fid] - engineers.get(fid, 0) for fid in wanted}
        # 1. 雇い続ける（遠いマスの人から減らす）
        for f, _ in plans:
            keep: dict[Pos, int] = {}
            left = general[f.id]
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
            missing = general[f.id] - sum(staff[f.id].values())
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
        # 4. 余った技術者を、まだ埋まらない一般の席に島のどこからでも就かせる
        spare = self._spare_engineers(island, plans, general, staff, engineers)
        # 5. インフラの作業員を、残った失業者から島のどこからでも雇う
        employed: dict[Pos, int] = dict(infrastructure.hire(island, free, everywhere))
        # 6. 労働の動員（全体主義）: 残った失業者を、国営の施設の空いた席に島のどこからでも
        #    割り当てる
        mobilized = self._mobilize(island, ctx, plans, staff, engineers, spare, free, employed)
        # 雇えなかった分の賃金の取り置きを戻し、結果を書く
        unpaid_total = employed_total = 0
        for f, wage in plans:
            mine = {p: n for p, n in staff[f.id].items() if n > 0}
            hired = sum(mine.values())
            skilled, spared = engineers.get(f.id, 0), spare.get(f.id, 0)
            f.workers = hired + skilled + spared + mobilized.get(f.id, 0)
            f.economy["staff"] = {_key(p): n for p, n in sorted(mine.items())}
            f.economy["wanted"] = wanted[f.id]
            f.economy["poached"] = poached.get(f.id, 0)
            for key, value in (
                ("engineers", skilled),
                ("engineers_wanted", seats[f.id]),
                ("engineers_general", spared),
            ):
                if value:
                    f.economy[key] = value
                else:
                    f.economy.pop(key, None)
            # 払えないまま働く人（遅配）は、雇えた人のうち賃金を取り置いた席を超えた分
            paid_seats = general[f.id] - unpaid[f.id]
            unhired = max(0, paid_seats - hired - spared)
            funds.release(island, payer_of(defs, config, f), unhired * wage)
            owed = max(0, hired + spared - paid_seats)
            if owed:
                f.economy["unpaid"] = owed
                f.economy["arrears_months"] = f.economy.get("arrears_months", 0) + 1
                unpaid_total += owed
            else:
                f.economy.pop("unpaid", None)
                f.economy.pop("arrears_months", None)
            employed_total += f.workers
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
        # 賃金が払われないまま働いている人の割合（幸福度の減点。人口の段）
        island.scratch["arrears_share"] = unpaid_total / employed_total if employed_total else 0.0
        if unpaid_total:
            island.stats["arrears.workers"] = unpaid_total

    # --- 労働の動員（フェーズ3-4） -----------------------------------------------------

    @staticmethod
    def _mobilize(
        island: IslandState,
        ctx: StageContext,
        plans: list[tuple[Facility, int]],
        staff: dict[int, dict[Pos, int]],
        engineers: dict[int, int],
        spare: dict[int, int],
        free: dict[Pos, int],
        employed: dict[Pos, int],
    ) -> dict[int, int]:
        """動員している島で、残った失業者（村で自給の農業をしている人を除く）を、物を作る国営の
        施設の空いた席（定義の人数 − 雇えた人数）に、優先順位の高い施設から島のどこからでも、
        動員の賃金（最低賃金。決めていなければ平均賃金の politics.mobilization.wage）で割り当てる。
        施設ごとの人数を返し、economy["mobilized"] と ["mobilized_wage"] に残す（賃金の段で
        払う）。"""
        defs, config = ctx.defs, ctx.config
        for f, _ in plans:
            f.economy.pop("mobilized", None)
            f.economy.pop("mobilized_wage", None)
        if not island.has_economy or politics.mobilized_share(island) <= 0:
            return {}
        share = fac.labor_share(defs, island)
        idle = {}
        for pos, count in free.items():
            farming = int(subsistence_persons(defs, island, island.tile(*pos)) * share)
            if count - farming > 0:
                idle[pos] = count - farming
        if not idle:
            return {}
        average = island.economy.average_wage or config.get("labor.initial_wage")
        wage = island.policy.minimum_wage or round(
            average * config.get("politics.mobilization.wage")
        )
        out: dict[int, int] = {}
        chosen = [
            f
            for f, _ in plans
            if f.built
            and f.owner == OWNER_STATE
            and fac.on_map(f)
            and not is_public_service(defs, f)
            and not is_bank(config, f)
            and staff_module.wanted(config, defs, f) is None
            and fac.recipe_of(defs, f).get("outputs")
        ]
        chosen.sort(key=lambda f: (-fac.priority_of(defs, config, f), f.id))
        for f in chosen:
            hired = sum(staff[f.id].values()) + engineers.get(f.id, 0) + spare.get(f.id, 0)
            room = fac.workers_needed(defs, f.kind) - hired
            if room <= 0:
                continue
            payer = payer_of(defs, config, f)
            room = min(room, funds.available(island, payer) // max(1, wage))
            taken = 0
            for pos in sorted(idle, key=lambda p: (Employment._dist(f, p), p[1], p[0])):
                take = min(idle[pos], room - taken)
                if take <= 0:
                    continue
                idle[pos] -= take
                free[pos] -= take
                employed[pos] = employed.get(pos, 0) + take
                taken += take
                if taken >= room:
                    break
            if taken:
                funds.commit(island, payer, taken * wage)
                out[f.id] = taken
                f.economy["mobilized"] = taken
                f.economy["mobilized_wage"] = wage
        island.stats["labor.mobilized"] = sum(out.values())
        return out

    # --- 技術者 ----------------------------------------------------------------------

    @staticmethod
    def _engineer_terms(
        island: IslandState, ctx: StageContext, f: Facility, wage: int
    ) -> tuple[int, int]:
        """施設の全力の技術者の数と、技術者の賃金が一般より高い分を働き手1人あたりにした額。

        技術者を雇うのは、お金の経済のある島の、建っていてマスを持つ公務でない施設だけ。
        """
        defs = ctx.defs
        if not island.has_economy or not f.built or is_public_service(defs, f):
            return 0, 0
        engineers = fac.engineers_needed(defs, f.kind)
        if engineers <= 0:
            return 0, 0
        extra = max(0, fac.engineer_wage_of(ctx.config, f, wage) - wage)
        return engineers, -(-extra * engineers // fac.workers_needed(defs, f.kind))

    def _assign_engineers(
        self,
        island: IslandState,
        ctx: StageContext,
        plans: list[tuple[Facility, int]],
        seats: dict[int, int],
    ) -> dict[int, int]:
        """技術者を席に割り当てる。前のターンの人を雇い続けてから、技術者の賃金の高い施設から順に。

        埋まらなかった席の分は、技術者の賃金のうち一般より高い分の取り置きを戻す（一般の席になる）。
        """
        config = ctx.config
        pool = island.economy.workers.get(fac.ENGINEER, 0)
        out: dict[int, int] = {}
        for f, _ in plans:
            keep = min(f.economy.get("engineers", 0), seats[f.id], pool)
            if keep > 0:
                out[f.id] = keep
                pool -= keep
        ranked = sorted(
            (p for p in plans if seats[p[0].id] > out.get(p[0].id, 0)),
            key=lambda p: (
                -fac.engineer_wage_of(config, p[0], p[1]),
                -fac.priority_of(ctx.defs, config, p[0]),
                p[0].id,
            ),
        )
        for f, _ in ranked:
            take = min(seats[f.id] - out.get(f.id, 0), pool)
            if take > 0:
                out[f.id] = out.get(f.id, 0) + take
                pool -= take
        for f, wage in plans:
            empty = seats[f.id] - out.get(f.id, 0)
            extra = fac.engineer_wage_of(config, f, wage) - wage
            if empty > 0 and extra > 0:
                funds.release(island, payer_of(ctx.defs, config, f), empty * extra)
        island.scratch["engineers_left"] = pool
        return out

    @staticmethod
    def _spare_engineers(
        island: IslandState,
        plans: list[tuple[Facility, int]],
        general: dict[int, int],
        staff: dict[int, dict[Pos, int]],
        engineers: dict[int, int],
    ) -> dict[int, int]:
        """余った技術者を、一般の労働者で埋まらなかった席に就かせる（賃金の高い施設から）。"""
        pool = island.scratch.get("engineers_left", 0)
        out: dict[int, int] = {}
        for f, _ in plans:
            if pool <= 0:
                break
            missing = general[f.id] - sum(staff[f.id].values())
            take = min(max(0, missing), pool)
            if take > 0:
                out[f.id] = take
                pool -= take
        island.scratch["engineers_left"] = pool
        island.scratch["engineers_employed"] = sum(engineers.values()) + sum(out.values())
        return out

    # --- 施設ごとの決まり ------------------------------------------------------------

    @staticmethod
    def _wage(island: IslandState, ctx: StageContext, f: Facility) -> int | None:
        """この施設の賃金。人を雇わない施設は None。"""
        config = ctx.config
        if fac.is_housing(config, f) or is_stopped(f):
            return None
        if not f.built or is_public_service(ctx.defs, f):
            return Employment._site_wage(island, ctx)
        if "wage" not in f.economy:
            f.economy["wage"] = island.economy.average_wage or config.get("labor.initial_wage")
        return f.economy["wage"]

    @staticmethod
    def _site_wage(island: IslandState, ctx: StageContext) -> int:
        """工事の作業員と公務員の賃金: 前のターンの島の平均賃金（最低賃金より下げない）。"""
        wage = island.economy.average_wage or ctx.config.get("labor.initial_wage")
        return max(wage, island.policy.minimum_wage)

    @staticmethod
    def _wanted(island: IslandState, ctx: StageContext, f: Facility, wage: int) -> int:
        config = ctx.config
        if is_transport(config, f):
            # トラック（フェーズ3-2）で替えた運転手の分は雇わない
            drivers = freight.payroll(island, config) // max(1, wage)
            if not f.built:
                return drivers
            trucks.commission(island, config, f)
            replaced = trucks.drive(island, config, f, drivers)
            if replaced:
                f.economy["drivers_replaced"] = replaced
            else:
                f.economy.pop("drivers_replaced", None)
            return drivers - replaced
        cargo = staff.wanted(config, ctx.defs, f)  # 港・海運業・商社は扱う荷に合わせて（3-1）
        if cargo is not None:
            return cargo
        if f.built and is_bank(config, f):  # 銀行は預金と融資の大きさに合わせて
            return banks.staff_wanted(island, config, ctx.defs, f)
        return fac.labor_demand(ctx.defs, f, ctx.turn, config.get("power.good"))

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


def settle_arrears(island: IslandState) -> None:
    """未払い賃金と未払いの税を、払い手ごとに使えるお金の分だけ払う（雇う前に先に払う。回3）。
    政府は支出の項目 arrears から（予算の段で先に取り置く）。"""
    issuers = sorted(
        {
            holder
            for name in island.accounts
            for holder, item, kind in [ledger.parse(name)]
            if item == ledger.ISSUED and kind in (arrears.WAGE_ARREARS, arrears.TAX_ARREARS)
        }
    )
    for issuer in issuers:
        payer = Payer(ledger.GOV, arrears.ARREARS) if issuer == ledger.GOV else Payer(issuer)
        arrears.settle(island, payer)


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
