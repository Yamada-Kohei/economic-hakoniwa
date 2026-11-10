"""通勤と雇用: 施設に働き手を割り当てる段。

各施設は、操業目標の分だけ（工事中は手元の資材で進められる分だけ）雇いたい人数を決め、払える
賃金の分までに抑えます。そのうえで次の順に雇います。

1. 前のターンに雇っていた人を雇い続ける。雇いたい人数より多ければ、遠いマスの人から減らす。
2. 賃金の高い施設から順に（同じ賃金なら優先順位の高い順、番号の小さい順）、足りない人数を
   通勤圏（既定3マス）の失業者から、近いマスの順に雇う。
3. それでも足りなければ、通勤圏の中で賃金が labor.poach_gap 以上安い施設と商業・サービス業の店の
   働き手を、安い施設から引き抜く。1つの施設から引き抜かれるのは1ターンに働き手の labor.poach_limit
   まで。

技術者（フェーズ2-3）は島全体の人数として持ち、通勤圏を見ずに島のどこの施設にも通います。施設の
定義の技術者の数（workers の engineer）に、雇いたい人数の割合を掛けた分を技術者の席とし、2 の前に、
前のターンの技術者を雇い続けたうえで、技術者の賃金の高い施設から順に割り当てます。埋まらなかった
技術者の席は一般の労働者の席にします（技術者が足りない分、稼働率の上限が下がる。
facilities.engineer_cap）。3 の後に余った技術者は、まだ埋まらない一般の席に、賃金の高い施設から
島のどこへでも就きます（賃金は一般のもの）。

インフラの公共事業（modules/infrastructure）は、維持の分を施設より先に、新しい工事の分を施設の
後に、作業員の賃金と次のターンの資材の代金の見込みを取り置きます。賃金を取り置くのは、前のターンに
雇いに行けた人数（infrastructure の reach）までです（雇えない作業員の賃金で公共事業費を埋めて、
後ろの工事を止めないように）。作業員は、施設が雇った後、店が雇う前に、残った失業者から島のどこから
でも雇い、足りなければ店の働き手を引き抜きます（_hire_works。ほかの施設と同じく、1つの店から
1ターンに labor.poach_limit まで）。

工事の作業員と公務員は前のターンの島の平均賃金で雇います。工事は、作業員の賃金に続けて、この
ターンの市場で買う資材の代金の見込みも取り置きます（公共事業費は、賃金と資材の両方に優先度の高い
工事から充てる。hakoniwa_core/modules/market の SiteFunds）。運送業は通勤圏を見ず島のどこからでも、
前のターンの運賃 × freight.labor_share ÷ 賃金 の人数を雇い、足りない割合だけ運賃を割り増します
（フェーズ3-2 から、燃料のあるトラックで替えた運転手の分は雇わない。economy/trucks.py）。
港・海運業・商社（フェーズ3-1）は、扱った荷の量と船の数に合わせて雇います（hakoniwa_core/trade）。
商業・サービス業（バランス調整）は、売上で決めた人数を、住民がサービスを買いに行く集落（中心）ごとに
割り振り、1 で中心の通勤圏の人を雇い続け、ほかの施設が 2 と 3 で雇った後に、中心の通勤圏に残った
失業者を雇います。店は引き抜かず、ほかの施設は賃金にかかわらず店から引き抜けます（多くの小さな店は、
ほかに仕事のない人の受け皿になる。economy/services.py）。
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
from dataclasses import dataclass
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.allocation import proportional
from hakoniwa_core.economy import (
    CONSTRUCTION,
    GENERAL,
    Payer,
    arrears,
    banks,
    dismissal,
    freight,
    funds,
    is_bank,
    is_public_service,
    is_stopped,
    is_transport,
    payer_of,
    services,
    trucks,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.geo import distance, within
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules.market import FACILITY_HOLDS, SITE_HOLDS, SiteFunds, input_cost
from hakoniwa_core.modules.population import FED, subsistence_persons
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState
from hakoniwa_core.trade import staff
from hakoniwa_core.trade import staff as staff_module
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, money_of, part, to_basis

Pos = tuple[int, int]
FIELDS_KEY = "back_to_fields"  # island.ext: 畑に戻った村の働き手の割合（回4）


def _key(pos: Pos) -> str:
    return f"{pos[0]},{pos[1]}"


def _take(free: dict[Pos, int], fields: dict[Pos, int], pos: Pos, want: int) -> int:
    """マス pos の働き手を want 人まで雇う（free から、足りなければ畑に戻った人 fields から。
    食べ物を作る施設だけが fields を渡す）。雇えた人数を返す。"""
    take = min(want, free.get(pos, 0) + fields.get(pos, 0))
    if take <= 0:
        return 0
    used = min(take, free.get(pos, 0))
    free[pos] = free.get(pos, 0) - used
    if take > used:
        fields[pos] -= take - used
    return take


def back_to_fields(island: IslandState, ctx: StageContext, labor: dict[Pos, int]) -> dict[Pos, int]:
    """食べ物が買えずに、仕事を休んで自分の畑で食べる分を作る村の働き手（マスごと。回4）。

    働いても買える食べ物がなければ、村の人は畑に戻ります（現実の飢饉でも、土地のある農家は自分の
    畑と蓄えで食いつなぎ、亡くなるのは多くが食べ物を買って暮らす人でした）。前のターンに一般の家計が
    食べられなかった割合の人が、村（自給の農業ができる集落）で畑に戻ります（体が耐えられる少しの
    不足（population.hunger.tolerance）までなら戻りません）。食べ物が買えるようになっても、畑に戻った人は1ターンに
    labor.back_to_work の割合ずつ仕事に戻ります（蒔いた畑を収穫するまで）。畑に戻った人は、食べ物を
    作れる施設（農場・漁港・牧場・食品工場。facilities.food_maker）のほかには勤めません。勤めなかった人は
    村で自給の農業をし、市場で食料を買いません（modules/population の subsistence_persons）。
    市場に食べ物が1つも出ていない間は、仕事に戻りません。
    """
    defs, config = ctx.defs, ctx.config
    state = island.ext.get(FIELDS_KEY, 0.0)
    fed = island.economy.households.get(GENERAL, {}).get(FED, fac.FULL)
    short = max(0.0, 1 - fed / fac.FULL)
    if short <= config.get("population.hunger.tolerance"):
        short = 0.0
    # 市場に食べ物が1つも出ていなければ、畑に戻った人は仕事に戻らない（みなが畑に戻って市場で買う
    # 人がいないと、食べられなかった人もいないように見えるため）
    supply = island.economy.finance.get("supply", {})  # 財ごとの売りに出た量（前のターンまで）
    offered = any(h and h[-1] > 0 for g, h in supply.items() if "food" in defs.goods.get(g, {}))
    back = config.get("labor.back_to_work") if offered else 0.0
    share = max(short, state * (1 - back))
    if share < 0.001:
        island.ext.pop(FIELDS_KEY, None)
        return {}
    island.ext[FIELDS_KEY] = round(share, 4)
    out = {}
    for pos, force in labor.items():
        tile = island.tile(*pos)
        if not defs.settlements[fac.settlement_of(defs, tile.population)].get("subsistence"):
            continue
        count = int(force * share)
        if count > 0:
            out[pos] = count
    if out:
        island.stats["labor.back_to_fields"] = sum(out.values())
    return out


def _pos(key: str) -> Pos:
    x, y = key.split(",")
    return int(x), int(y)


ATTRITION = "attrition"  # f.economy: 人を抱える施設の、辞める人の端数（W-198）
STAFF_CAPPED = planning.STAFF_CAPPED  # f.ext: 人員の枠で、生産目標に要る人を雇えなかった（PL-59）
SHORT_MATERIALS = funds.SHORT_MATERIALS
PENDING_MATERIALS = "pending_material_holds"  # island.scratch: 雇用の段の終わりに取り置く材料の代金
STAFF_LIMITS = "staff_limits"  # island.scratch: 施設の番号 → このターンの人員の枠の人数（動員も）


@dataclass
class _Terms:
    """1つの施設の、雇いたい人数と取り置きの結果（雇用の段の中だけ）。"""

    f: Facility
    wage: int
    payer: Payer
    need: int  # 生産目標などに要る人数（抱える人を足す前。人員の枠で切った後）
    demand: int  # 雇いたい人数（抱える人・解雇の決まりで残す人を足し、人員の枠で切った後）
    kept: int  # 払えないときに遅配で残る人の上限
    engineers: int  # 全力の技術者の数
    per_worker: int  # 1人あたりの賃金・電気代・技術者の上乗せ
    power: int = 0  # 1人あたりの電気代（per_worker の中。取り置かず、材料の取り置きから残す）
    want: int = 0  # 賃金を取り置いた人数
    unpaid: int = 0  # 払えないまま働き続ける人（賃金の遅配。回3）
    quits: int = 0  # 遅配が続いて自分から辞める人
    seats: int = 0  # 技術者の席


BOUND_PLAN = "plan"  # 雇いたい人数の下限: 計画経済の国営施設の抱え込み（PL-57）
BOUND_DISMISSAL = "dismissal"  # 雇いたい人数の下限: 解雇の決まり（LB-43）


def staff_of(f: Facility) -> dict[Pos, int]:
    return {_pos(k): v for k, v in f.economy.get("staff", {}).items() if v > 0}


class Employment(IslandStage):
    name = "employment"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        if island.has_economy:
            settle_arrears(island)
        planned_now = planning.is_planned(island, defs)
        for f in island.facilities:
            fac.refresh_fuel(defs, f)  # 燃料の足りない機械の畑は手作業で（FM-51）
            fac.refresh_fertilizer(defs, f)  # 肥料の足りない畑は肥料なしで（FM-53）
            if planning.STAFF_CAP in f.ext and (not planned_now or f.owner != OWNER_STATE):
                # 計画経済をやめた・民営化したら人員の枠は消える（雇わない施設の枠も。PL-59）
                f.ext.pop(planning.STAFF_CAP, None)
                f.ext.pop(STAFF_CAPPED, None)
        share = fac.labor_share(defs, island)
        labor = {
            (t.x, t.y): fac.labor_force(defs, island, t, share)
            for t in island.tiles
            if t.population and not t.ext.get("occupied")  # 占領されたマスの人は通えない（MI-36）
        }
        free = dict(labor)
        # 食べ物が買えずに畑に戻った村の働き手（食べ物を作る施設のほかには勤めない。回4）
        fields = back_to_fields(island, ctx, labor)
        for pos, count in fields.items():
            free[pos] -= count
        everywhere = within(island, island.capital or (0, 0), island.size)
        plans = []
        for f in island.facilities:
            wage = self._wage(island, ctx, f)
            if wage is None:
                if is_stopped(f):
                    # 閉鎖に入った・動かさない差し押さえ: 割合によらず全員を解雇する（LB-43）
                    prior = f.workers - f.economy.get("mobilized", 0)
                    self._record_dismissals(
                        island, ctx, f, (prior, f.economy.get("engineers", 0)), 0, 0, {}
                    )
                f.economy.pop(dismissal.HELD, None)
                f.economy.pop(dismissal.CARRY, None)
                f.economy.pop(dismissal.NEEDS, None)
                f.workers = 0
                f.ext.pop("short_wages", None)
                continue
            f.economy["wage"] = wage
            plans.append((f, wage))
        planned = planning.is_planned(island, defs)
        # 雇う順: 賃金の高い順（同じなら優先度の高い順、番号の順）。計画経済の島は優先度の高い順
        # （同じなら賃金の高い順、番号の順。賃金は国が決めた数字で、人を引く力を表さない。PL-59）
        plans.sort(key=lambda p: self._hire_key(ctx, planned, p))
        staff: dict[int, dict[Pos, int]] = {}
        works_wage = self._site_wage(island, ctx)
        # 雇いたい人数を決める（取り置く前に、施設ごとに）
        needs: dict[int, int] = {}  # 操業目標などに要る人数（抱える人を足す前）
        bounds: dict[int, str] = {}  # 雇いたい人数にかけた下限の種類（_bounds）
        before: dict[int, tuple[int, int]] = {}  # 前のターンの人数（動員を除く）と技術者の席の人数
        terms: dict[int, _Terms] = {}
        # 解雇の決まりで、操業目標に要る人数を超えて雇い続けている（抱えている）人数
        spare: dict[int, int] = {}
        for f, wage in plans:
            before[f.id] = (
                f.workers - f.economy.get("mobilized", 0),
                f.economy.get("engineers", 0),
            )
            # 技術者の席と、賃金の式が見る人手の足りなさ（economy["wanted"]）は、操業目標に要る人数
            # （needs）のまま（解雇の決まりで抱える施設の技術者の席は、抱えた人数で見る）
            needs[f.id] = self._wanted(island, ctx, f, wage)
            demand, bounds[f.id] = self._bounds(island, ctx, f, needs[f.id], planned)
            # 払えないときに遅配で残る人の上限: 人を抱える計画経済の国営施設（PL-57）は抱えた人を
            # 遅配にせず手放す。解雇の決まりの施設（LB-43）は、割合の上限までは遅配で残す
            kept = demand if bounds[f.id] == BOUND_DISMISSAL else min(needs[f.id], demand)
            if bounds[f.id] == BOUND_DISMISSAL and demand > needs[f.id]:
                spare[f.id] = demand - needs[f.id]
            engineers, extra = self._engineer_terms(island, ctx, f, wage)
            power = self._power_per_worker(island, ctx, f)
            terms[f.id] = _Terms(
                f,
                wage,
                payer_of(defs, config, f),
                min(needs[f.id], demand),
                demand,
                kept,
                engineers,
                wage + power + extra,
                power,
            )
        # 賃金（と工事の資材・国庫の施設の材料）を取り置く。順は _reserve_steps
        self._reserve_all(island, ctx, plans, terms, planned, works_wage)
        wanted = {fid: t.want + t.unpaid for fid, t in terms.items()}
        seats = {fid: t.seats for fid, t in terms.items()}  # 技術者の席
        unpaid = {fid: t.unpaid for fid, t in terms.items()}  # 払えないまま働き続ける人（回3）
        quits = {fid: t.quits for fid, t in terms.items() if t.quits}  # 遅配で自分から辞める人
        # 0. 技術者を割り当て、埋まらなかった技術者の席を一般の席にする
        engineers = self._assign_engineers(island, ctx, plans, seats)
        general = {fid: wanted[fid] - engineers.get(fid, 0) for fid in wanted}
        # 1. 雇い続ける（遠いマスの人から減らす）
        shops: dict[int, dict[Pos, int]] = {}  # 商業・サービス業の中心ごとの人数
        for f, _ in plans:
            if services.is_services(config, f):
                shops[f.id] = services.quotas(island, config, general[f.id])
                f.economy[services.QUOTAS] = {}
                staff[f.id] = services.keep(island, config, f, shops[f.id], staff_of(f), free)
                continue
            keep: dict[Pos, int] = {}
            left = general[f.id]
            feeds = fac.food_maker(defs, f.kind)
            for pos, count in sorted(
                staff_of(f).items(), key=lambda kv: (self._dist(f, kv[0]), kv[0][1], kv[0][0])
            ):
                take = _take(free, fields if feeds else {}, pos, min(count, left))
                if take > 0:
                    keep[pos] = take
                    left -= take
            staff[f.id] = keep
        # 計画経済の島は、公共事業も前のターンの作業員を雇い続ける（PL-59。賃金は取り置いた分の中）
        kept_works = self._keep_works(island, free) if planned and island.has_economy else {}
        start = {fid: sum(s.values()) for fid, s in staff.items()}
        poached: dict[int, int] = {}
        limit = config.get("labor.poach_limit")
        gap = config.get("labor.poach_gap")
        by_id = {f.id: (f, wage) for f, wage in plans}
        shop_ids = [fid for _, fid in sorted((w, f.id) for f, w in plans if f.id in shops)]
        hire_works = self._works_hirer(
            island,
            ctx,
            plans,
            shops,
            free,
            everywhere,
            staff,
            shop_ids,
            start,
            poached,
            limit,
            kept_works,
            planned,
        )
        # 2. 失業者を雇う 3. 安い施設と店から引き抜く（店は最後に雇う）。国の公共事業（インフラの
        #    工事と維持）の作業員は施設の後、店の前に雇う（計画経済の島は優先度の位置で。PL-59）
        for entry in self._hire_sequence(island, ctx, plans, planned):
            if isinstance(entry, str):
                hire_works(entry)
                continue
            f, wage = entry
            if f.id in shops:
                continue
            missing = general[f.id] - sum(staff[f.id].values())
            if missing <= 0:
                continue
            area = everywhere if self._island_wide(ctx, f) else self._area(island, ctx, f)
            feeds = fac.food_maker(defs, f.kind)
            for pos in area:
                if missing <= 0:
                    break
                take = _take(free, fields if feeds else {}, pos, missing)
                if take > 0:
                    staff[f.id][pos] = staff[f.id].get(pos, 0) + take
                    missing -= take
            if missing <= 0:
                continue
            reach = set(area)
            # 計画経済の島では、賃金の差で引き抜けるのは優先度が相手以上の施設だけ（PL-59）
            mine = fac.priority_of(defs, config, f)
            # 抱えている余った人（解雇の決まり）は、人手の足りない施設が賃金の差によらず先に雇える。
            # 仕事のない席にいる人は、ほかに仕事があれば自分から移る（手当はない）。抱えた人が
            # 働かずにいる横で、ほかの施設が人を雇えず、足りない品を島の外から買っていた（X870 の
            # 比べの銀行の救済。2026-11-11）。現実の在籍出向・人手の足りない所への移り
            for _, vid in sorted(
                (other_wage, other.id)
                for other, other_wage in plans
                if spare.get(other.id, 0) > 0
                and other.id != f.id
                and (not planned or mine >= fac.priority_of(defs, config, other))
            ):
                if missing <= 0:
                    break
                for pos in sorted(
                    (p for p in staff[vid] if p in reach),
                    key=lambda p: (self._dist(f, p), p[1], p[0]),
                ):
                    take = min(staff[vid][pos], missing, spare[vid])
                    if take <= 0:
                        continue
                    staff[vid][pos] -= take
                    staff[f.id][pos] = staff[f.id].get(pos, 0) + take
                    poached[vid] = poached.get(vid, 0) + take
                    spare[vid] -= take
                    missing -= take
                    if missing <= 0 or spare[vid] <= 0:
                        break
            if missing <= 0:
                continue
            victims = sorted(
                (
                    (other_wage, other.id)
                    for other, other_wage in plans
                    if other.id != f.id
                    and (
                        other.id in shops
                        or (
                            other_wage <= wage * (1 - gap)
                            and (not planned or mine >= fac.priority_of(defs, config, other))
                        )
                    )
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
        employed = hire_works(None)
        # 店は、ほかの施設と公共事業が雇った後に残った失業者を雇う（引き抜かれた分を数え直してから）
        for fid, plan in shops.items():
            shop, _ = by_id[fid]
            if poached.get(fid):
                services.recount(island, config, shop, plan, staff[fid])
            services.hire(island, config, shop, plan, staff[fid], free)
        # 4. 余った技術者を、まだ埋まらない一般の席に島のどこからでも就かせる
        spare = self._spare_engineers(island, plans, general, staff, engineers)
        # 5. 労働の動員（全体主義）: 残った失業者を、国営の施設の空いた席に島のどこからでも
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
            f.economy["wanted"] = min(wanted[f.id], needs[f.id])
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
            self._record_dismissals(island, ctx, f, before[f.id], wanted[f.id], seats[f.id], quits)
            if bounds[f.id] == BOUND_DISMISSAL:
                # 抱えた人は操業目標に要る人数を超えた人（原料や電力が届かずに休んでいる人は、休業
                # 手当の人で、抱えた人に数えない）
                goal = fac.labor_demand(defs, f, ctx.turn)
                held = max(0, f.workers - mobilized.get(f.id, 0) - max(needs[f.id], goal))
                f.economy[dismissal.HELD] = held
                island.stats["labor.held"] = island.stats.get("labor.held", 0) + held
            else:
                f.economy.pop(dismissal.HELD, None)
            employed_total += f.workers
            for pos, n in mine.items():
                employed[pos] = employed.get(pos, 0) + n
            if is_transport(config, f):
                self._surcharge(island, ctx, wanted[f.id], hired)
        # 順に取り置くうちに、先の施設が雇いきれなかった席の取り置きが戻る。払えずに遅配になった
        # 席は、戻ったお金で払える分を払う（国庫にお金があるのに、雇えない席に取り置いたせいで
        # 遅配になるのを避ける。賃金の高い施設から取り置くので、後ろの施設ほど遅配になりやすい）
        for f, wage in plans:
            owed = f.economy.get("unpaid", 0)
            if owed <= 0 or wage <= 0:
                continue
            payer = payer_of(defs, config, f)
            cover = min(owed, funds.available(island, payer) // wage)
            if cover <= 0:
                continue
            funds.commit(island, payer, cover * wage)
            unpaid_total -= cover
            if owed > cover:
                f.economy["unpaid"] = owed - cover
            else:
                f.economy.pop("unpaid", None)
                f.economy.pop("arrears_months", None)
        # 国庫の項目の組の材料の代金は、賃金と遅配を埋めた後に取り置く（LB-45）
        self._hold_materials(island)
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

    # --- 賃金の取り置き ---------------------------------------------------------------

    @staticmethod
    def _hire_key(ctx: StageContext, planned: bool, p: tuple[Facility, int]) -> tuple:
        f, wage = p
        priority = fac.priority_of(ctx.defs, ctx.config, f)
        return (-priority, -wage, f.id) if planned else (-wage, -priority, f.id)

    def _reserve_all(
        self,
        island: IslandState,
        ctx: StageContext,
        plans: list[tuple[Facility, int]],
        terms: dict[int, _Terms],
        planned: bool,
        works_wage: int,
    ) -> None:
        """雇いたい人の賃金と、工事の資材・国庫の施設の材料の代金を、次の順に取り置く。

        - 計画経済でない島: インフラの維持 → 国営の施設の設備を直す資材（回6）→ 国庫の項目から払う
          建て終わった施設（項目は支払いの順 fiscal.payment_order、中は優先度の高い組から。LB-45）
          → ほかの施設（雇う順）→ 新しいインフラの工事。
        - 計画経済の島（PL-59）: 優先度の高い順。同じ優先度の中は、国庫の項目の組 → ほかの施設 →
          公共事業（維持は infrastructure.priority の upkeep、新しい工事は build の位置。直す資材は
          維持の後）。取り置く順が賃金の順のままだと、優先度の高い工事が「公共事業費が足りず」で
          雇えない。

        国庫の項目の組は _reserve_shared で、同じ優先度の施設が要る額に比例して分ける（LB-45）。"""
        defs, config = ctx.defs, ctx.config
        economy = island.has_economy
        order = funds.items(config.get("fiscal.payment_order"))

        def rank(t: _Terms) -> int:
            item = t.payer.item or ""
            return order.index(item) if item in order else len(order)

        groups: dict[tuple[str, int], list[_Terms]] = {}
        singles: list[_Terms] = []
        for f, _ in plans:
            t = terms[f.id]
            if economy and f.built and t.payer.entity == ledger.GOV:
                key = (t.payer.item or "", fac.priority_of(defs, config, f))
                groups.setdefault(key, []).append(t)
            else:
                singles.append(t)
        steps: list[tuple[tuple, str, Any]] = []
        upkeep = infrastructure.priority(island, config, True)
        build = infrastructure.priority(island, config, False)
        if planned:
            for (_, priority), members in groups.items():
                steps.append(((-priority, 0, rank(members[0]), 0), "group", members))
            for i, t in enumerate(singles):
                steps.append(((-fac.priority_of(defs, config, t.f), 1, 0, i), "one", t))
            steps.append(((-upkeep, 2, 0, 0), "upkeep", None))
            steps.append(((-upkeep, 2, 0, 1), "repairs", None))
            steps.append(((-build, 2, 1, 0), "build", None))
        else:
            steps.append(((0, 0, 0, 0), "upkeep", None))
            steps.append(((0, 1, 0, 0), "repairs", None))
            for (_, priority), members in groups.items():
                steps.append(((1, rank(members[0]), -priority, 0), "group", members))
            for i, t in enumerate(singles):
                steps.append(((2, i, 0, 0), "one", t))
            steps.append(((3, 0, 0, 0), "build", None))
        steps.sort(key=lambda step: step[0])
        keep = sum(infrastructure.kept_staff(island).values()) if planned else 0
        sites = SiteFunds(island, ctx) if economy else None
        shared = [t.f for members in groups.values() for t in members]
        rates = freight.rates_of(island, defs, config, shared) if shared else {}
        for _, kind, arg in steps:
            if kind in ("upkeep", "build"):
                infrastructure.plan(
                    island, ctx, upkeep=kind == "upkeep", wage=works_wage, keep=keep
                )
            elif kind == "repairs":
                if sites is None:
                    continue
                # 国営の施設の設備を直す資材の代金も、新しい工事の賃金より先に取り置く（回6）
                payer = Payer(ledger.GOV, CONSTRUCTION)
                hold = min(sites.repairs(), funds.available(island, payer))
                if hold > 0:
                    funds.commit(island, payer, hold)
                    island.scratch.setdefault(SITE_HOLDS, []).append((payer, hold))
            elif kind == "group":
                self._reserve_shared(island, ctx, arg, rates)
            else:
                self._reserve_one(island, ctx, arg, sites)

    def _reserve_one(
        self, island: IslandState, ctx: StageContext, t: _Terms, sites: SiteFunds | None
    ) -> None:
        """1つの施設の賃金を、払い手の使えるお金の分まで取り置く（工事は資材の代金も続けて）。"""
        money = funds.available(island, t.payer)
        want = t.demand
        if t.per_worker > 0:
            want = min(want, money // t.per_worker)
        # 年の予算の月割りで止まっている（お金はある）ときは遅配にしない
        short = money >= funds.available(island, t.payer, capped=False)
        self._settle(island, ctx, t, max(0, want), short, None)
        if not t.f.built and sites is not None:
            # 工事は、市場で買う資材の代金も続けて取り置く（作業員の賃金と資材の代金の両方に、
            # 優先度の高い工事から公共事業費を充てる）
            hold = min(sites.cost(t.f, t.want), funds.available(island, t.payer))
            funds.commit(island, t.payer, hold)
            island.scratch.setdefault(SITE_HOLDS, []).append((t.payer, hold))

    def _reserve_shared(
        self,
        island: IslandState,
        ctx: StageContext,
        group: list[_Terms],
        rates: dict[int, int],
    ) -> None:
        """国庫の同じ項目から払う、同じ優先度の建て終わった施設（LB-45）の取り置き。

        要る額（雇いたい人数の賃金・電気代・技術者の上乗せと、材料の代金の見込み）の合計が項目の
        使える額を超えるときは、要る額に比例して同じ割合で分け、分けた額 ÷ 1人あたりの額まで雇う。
        電気代は人数を抑えるのに使うだけで取り置かない（今までと同じ）。材料の見込みは施設ごとに
        取り置き、市場の段でその施設の買い注文の直前に戻す（FACILITY_HOLDS）。国庫の残りで止まって
        いる（賃金の遅配に進む）ときは、賃金を先に分け、材料は残った分だけ。人を抱える施設（PL-57）と
        解雇の決まりで残す人（LB-43）は、生産目標に要る人数で分け、残す人の賃金は項目に残りが
        あるときだけ取り置く。"""
        payer = group[0].payer
        money = funds.available(island, payer)
        treasury = money >= funds.available(island, payer, capped=False)
        for t in group:
            t.f.ext.pop(SHORT_MATERIALS, None)
        materials = {
            t.f.id: input_cost(island, ctx, t.f, rates.get(t.f.id, 0)) if t.need > 0 else 0
            for t in group
        }

        def per_worker(t: _Terms) -> int:
            if treasury or t.need <= 0:
                return t.per_worker
            return t.per_worker + -(-materials[t.f.id] // t.need)

        def mats(t: _Terms, workers: int) -> int:
            return materials[t.f.id] * workers // t.need if t.need > 0 else 0

        weights = [
            (t.f.id, t.need * t.per_worker + (0 if treasury else mats(t, t.need))) for t in group
        ]
        whole = sum(w for _, w in weights)
        rationed = whole > money
        shares = proportional(money, weights)
        if rationed and payer.item:
            # 要る額に対して分けた割合（項目ごとに一番低い組。国の要約に出す）
            key = f"budget_share.{payer.item}"
            ratio = round(money / whole, 3)
            island.stats[key] = min(ratio, island.stats.get(key, ratio))
        want: dict[int, int] = {}
        left = money
        for t in group:
            n = t.need
            if rationed and per_worker(t) > 0:
                n = min(n, shares[t.f.id] // per_worker(t))
            want[t.f.id] = max(0, n)
            left -= want[t.f.id] * t.per_worker + (0 if treasury else mats(t, want[t.f.id]))
        if not rationed:
            for t in group:
                more = t.demand - want[t.f.id]
                if more > 0 and t.per_worker > 0:
                    more = min(more, max(0, left) // t.per_worker)
                if more > 0:
                    want[t.f.id] += more
                    left -= more * t.per_worker
        budget = None if treasury else payer.item
        for t in group:
            self._settle(island, ctx, t, want[t.f.id], treasury, budget)
        if not island.has_economy:
            return
        asks = [(t.f.id, mats(t, min(t.want, t.need))) for t in group]
        asks = [(fid, amount) for fid, amount in asks if amount > 0]
        if not asks:
            return
        # 材料の取り置きは、雇用の段の終わり（遅配を埋めた後）にする（_hold_materials）。賃金を先に
        power = sum(t.want * t.power for t in group)
        pending = island.scratch.setdefault(PENDING_MATERIALS, [])
        pending.append((payer, asks, rationed, power, budget, {t.f.id: t.f for t in group}))

    @staticmethod
    def _hold_materials(island: IslandState) -> None:
        """国庫の項目の組の材料の代金の見込みを取り置く（LB-45。市場の段でその施設の買い注文の直前に
        戻す）。遅配を埋めた後の使えるお金から、雇った人の電気代（生産の段で買う）を残した分を、
        要る額に比例して分ける。年の予算の月割りで材料を削った施設は、原料が足りないわけに項目の
        名前を出す（short_materials）。"""
        pending = island.scratch.pop(PENDING_MATERIALS, [])
        power: dict[Payer, int] = {}
        for payer, _, _, watts, _, _ in pending:
            power[payer] = power.get(payer, 0) + watts
        holds = island.scratch.setdefault(FACILITY_HOLDS, {})
        for payer, asks, rationed, watts, budget, members in pending:
            avail = max(0, funds.available(island, payer) - power[payer])
            power[payer] -= watts
            squeezed = rationed or sum(a for _, a in asks) > avail
            for fid, amount in proportional(avail, asks).items():
                if amount > 0:
                    funds.commit(island, payer, amount)
                    holds[fid] = (payer, amount, squeezed)
            for fid, ask in asks:
                got = holds.get(fid, (payer, 0, False))[1]
                if budget and got < ask:
                    members[fid].ext[SHORT_MATERIALS] = budget

    def _settle(
        self,
        island: IslandState,
        ctx: StageContext,
        t: _Terms,
        want: int,
        treasury: bool,
        budget: str | None,
    ) -> None:
        """雇う人数 want の賃金（技術者の席の上乗せを含む）を取り置き、遅配で残る人と技術者の席を
        決める。treasury は国庫（独立採算は手元）の残りで止まっているか（偽なら年の予算の月割り）。
        """
        defs, config = ctx.defs, ctx.config
        f = t.f
        t.want, t.unpaid, t.quits = want, 0, 0
        if want < t.demand and treasury and f.built and island.has_economy:
            # 前のターンに働いていた人は、払えない分もすぐには辞めない（未払いが続いた月数に
            # 応じて辞める）。抱えた人（W-198）は遅配にせず手放す（国が払えなければ抱えない）
            stay = max(0, min(t.kept, sum(staff_of(f).values())) - want)
            share = min(
                1.0, config.get("labor.arrears_quit") * (f.economy.get("arrears_months", 0) + 1)
            )
            t.quits = min(stay, math.ceil(stay * share))
            t.unpaid = stay - t.quits
        t.seats = 0
        if t.engineers:
            t.seats = t.engineers * min(want, t.kept) // fac.workers_of(defs, f)
        engineer_wage = fac.engineer_wage_of(config, f, t.wage) if t.seats else t.wage
        if t.seats:
            f.economy["engineer_wage"] = engineer_wage
        funds.commit(island, t.payer, want * t.wage + t.seats * (engineer_wage - t.wage))
        # 賃金を払うお金が足りずに雇いたい分を雇えなかったことは、施設の状態に出す（人手不足と
        # 分けて、国庫が空で止まった施設が「労働者不足」と出ないように）。年の予算の月割りで減らした
        # ときは項目の名前を残す（LB-45）。抱えた人を手放しただけなら、払えなかったとは出さない
        if want < t.need:
            f.ext["short_wages"] = budget or True
        else:
            f.ext.pop("short_wages", None)

    # --- 公共事業の作業員 ----------------------------------------------------------

    @staticmethod
    def _keep_works(island: IslandState, free: dict[Pos, int]) -> dict[Pos, int]:
        return infrastructure.keep(island, free)

    def _hire_sequence(
        self,
        island: IslandState,
        ctx: StageContext,
        plans: list[tuple[Facility, int]],
        planned: bool,
    ) -> list:
        """失業者を雇う順。施設の (施設, 賃金) と公共事業の印（"works"・"first"・"last"）を並べる。

        計画経済でない島は、施設の後に公共事業をまとめて（店の前）。計画経済の島（PL-59）は、
        維持と新しい工事をそれぞれの優先度の位置に置く（同じ優先度なら施設が先）。"""
        if not planned:
            return [*plans, "works"]
        config = ctx.config
        works = sorted(
            [
                infrastructure.priority(island, config, True),
                infrastructure.priority(island, config, False),
            ],
            reverse=True,
        )
        marks = [(works[0], "first"), (works[1], "last")]
        out: list = []
        i = 0
        for f, wage in plans:
            priority = fac.priority_of(ctx.defs, config, f)
            while i < len(marks) and marks[i][0] > priority:
                out.append(marks[i][1])
                i += 1
            out.append((f, wage))
        out += [mark for _, mark in marks[i:]]
        return out

    def _works_hirer(
        self,
        island: IslandState,
        ctx: StageContext,
        plans: list[tuple[Facility, int]],
        shops: dict[int, dict[Pos, int]],
        free: dict[Pos, int],
        everywhere: list[Pos],
        staff: dict[int, dict[Pos, int]],
        shop_ids: list[int],
        start: dict[int, int],
        poached: dict[int, int],
        limit: float,
        kept: dict[Pos, int],
        planned: bool,
    ):
        """公共事業の作業員を雇う関数を返す。呼ぶたびに、残った失業者から島のどこからでも雇い、足りな
        ければ店（安い店から順に shop_ids）の働き手を引き抜く（撤去などで空いた人を店が先に雇って、
        国の工事が止まらないように。ほかの施設と同じく、1つの店から1ターンに引き抜けるのは前の
        ターンの働き手の labor.poach_limit まで）。印 "first" は優先度の高いほうの分（維持か新しい
        工事）まで、"works"・"last"・None は全部を雇って締め、マスごとの作業員の人数を返す。

        計画経済の島（PL-59）は、施設の求人が通勤圏に届かないマスの人から先に雇う（首都に近い
        マスから取ると、町の施設が人手不足のまま遠い町に失業者が残る）。"""
        config = ctx.config

        def room(fid: int) -> int:
            return max(0, int(start.get(fid, 0) * limit) - poached.get(fid, 0))

        def from_shops(missing: int) -> dict[Pos, int]:
            taken: dict[Pos, int] = {}
            for fid in shop_ids:
                left = room(fid)
                for pos in sorted(staff[fid], key=lambda p: (p[1], p[0])):
                    take = min(staff[fid][pos], missing, left)
                    if take <= 0:
                        continue
                    staff[fid][pos] -= take
                    taken[pos] = taken.get(pos, 0) + take
                    poached[fid] = poached.get(fid, 0) + take
                    missing -= take
                    left -= take
                    if missing <= 0 or left <= 0:
                        break
                if missing <= 0:
                    break
            return taken

        order = everywhere
        if planned:
            covered: set[Pos] = set()
            for f, _ in plans:
                if f.id not in shops and not self._island_wide(ctx, f):
                    covered.update(self._area(island, ctx, f))
            order = [p for p in everywhere if p not in covered] + [
                p for p in everywhere if p in covered
            ]
        done: dict[str, dict[Pos, int]] = {}
        first = [True]

        def hire(mark: str | None) -> dict[Pos, int]:
            if "staff" in done:
                return done["staff"]
            goal, final = None, True
            if mark == "first":
                upkeep, total = infrastructure.split(island)
                ahead = infrastructure.priority(island, config, True) >= infrastructure.priority(
                    island, config, False
                )
                goal, final = (upkeep if ahead else total - upkeep), False
            got = infrastructure.hire(
                island,
                free,
                order,
                poach=from_shops,
                poachable=sum(room(fid) for fid in shop_ids),
                goal=goal,
                final=final,
                kept=kept if first[0] else None,
            )
            first[0] = False
            if final:
                done["staff"] = dict(got)
                return done["staff"]
            return got

        return hire

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
            room = fac.workers_of(defs, f) - hired
            cap = island.scratch.get(STAFF_LIMITS, {}).get(f.id)
            if cap is not None:  # 動員で入れる人も人員の枠まで（PL-59）
                room = min(room, cap - hired)
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
        engineers = fac.engineers_of(defs, f)
        if engineers <= 0:
            return 0, 0
        extra = max(0, fac.engineer_wage_of(ctx.config, f, wage) - wage)
        return engineers, -(-extra * engineers // fac.workers_of(defs, f))

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
        if fac.is_housing(config, f) or is_stopped(f) or f.ext.get("occupied"):
            return None  # 占領されたマスの施設は、働き手を雇わない（MI-36）
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
        if services.is_services(config, f):  # 商業・サービス業は売上に合わせて（バランス調整）
            return f.economy.get(services.SEATS, 0)
        cargo = staff.wanted(config, ctx.defs, f)  # 港・海運業・商社は扱う荷に合わせて（3-1）
        if cargo is not None:
            return cargo
        if f.built and is_bank(config, f):  # 銀行は預金と融資の大きさに合わせて
            return banks.staff_wanted(island, config, ctx.defs, f)
        return fac.labor_demand(ctx.defs, f, ctx.turn, config.get("power.good"))

    def _bounds(
        self,
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        need: int,
        planned: bool | None = None,
    ) -> tuple[int, str]:
        """雇いたい人数 need に下限と上限をかける所（1か所にまとめる）。かけた後の人数と、下限の
        種類（BOUND_PLAN・BOUND_DISMISSAL・""）を返す。

        - 計画経済の島の国営施設（PL-57）: 前のターンの人から辞める人の分だけ減らす（抱え込み）。
        - それ以外の解雇の決まりにかかる施設（LB-43）: 前のターンの人数から、決まりの割合と自然に
          辞める人の分までしか減らさない（economy/dismissal.py）。閉鎖に入った施設は雇わない（_wage）。
        - 上限: 計画経済の人員の枠（PL-59。plan.staff_limit）。枠で生産目標に要る人を雇えない施設は
          ext["staff_capped"] に残す（状態に「人員の枠」と出す）。"""
        demand, kind = self._floor(island, ctx, f, need)
        if planned is None:
            planned = planning.is_planned(island, ctx.defs)
        cap = planning.staff_limit(island, ctx.defs, ctx.config, f) if planned else None
        if cap is None:
            if not planned or f.owner != OWNER_STATE:
                f.ext.pop(planning.STAFF_CAP, None)  # 民営化・計画経済をやめたら枠は消える
            f.ext.pop(STAFF_CAPPED, None)
            island.scratch.get(STAFF_LIMITS, {}).pop(f.id, None)
            return demand, kind
        island.scratch.setdefault(STAFF_LIMITS, {})[f.id] = cap
        if need > cap:
            f.ext[STAFF_CAPPED] = True
        else:
            f.ext.pop(STAFF_CAPPED, None)
        return min(demand, cap), kind

    def _floor(
        self, island: IslandState, ctx: StageContext, f: Facility, need: int
    ) -> tuple[int, str]:
        """雇いたい人数の下限（_bounds）。"""
        defs, config = ctx.defs, ctx.config
        if self._hoards(island, ctx, f):
            return max(need, self._kept(ctx, f)), BOUND_PLAN
        if not dismissal.covered(island, defs, config, f, ctx.turn):
            f.economy.pop(dismissal.CARRY, None)
            f.economy.pop(dismissal.NEEDS, None)
            return need, ""
        prior = f.workers - f.economy.get("mobilized", 0)
        least, carry = dismissal.floor(island, defs, config, f, prior)
        # 手当の月数のうちにまた要る人は解雇せずに抱える（雇い直すより安い）
        hold = min(prior, dismissal.recent_peak(island, defs, config, f, need))
        if need >= max(least, hold):  # 減らす速さの上限にも、抱える人数にもかからない
            f.economy.pop(dismissal.CARRY, None)
            return need, BOUND_DISMISSAL
        if hold >= least:  # 抱える人数で決まる（減らす速さの端数は残さない）
            f.economy.pop(dismissal.CARRY, None)
            return hold, BOUND_DISMISSAL
        f.economy[dismissal.CARRY] = round(carry, 4)
        return least, BOUND_DISMISSAL

    @staticmethod
    def _record_dismissals(
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        before: tuple[int, int],
        kept: int,
        seats: int,
        quits: dict[int, int],
    ) -> None:
        """解雇の決まりにかかる施設が、自然に辞める人と遅配で辞める人を超えて減らした人（解雇した人）を
        economy["dismissed"]（技術者の席の人は ["dismissed_engineers"]）に残す。手当は賃金の段で払う
        （LB-43）。kept は施設が雇い続けるつもりだった人数（引き抜かれた人は解雇でない）。"""
        defs, config = ctx.defs, ctx.config
        f.economy.pop("dismissed", None)
        f.economy.pop("dismissed_engineers", None)
        prior, prior_engineers = before
        if prior <= 0 or not dismissal.covered(island, defs, config, f, ctx.turn):
            return
        cut = prior - dismissal.natural_leavers(config, prior) - quits.get(f.id, 0) - kept
        if cut <= 0:
            return
        skilled = prior_engineers - dismissal.natural_leavers(config, prior_engineers) - seats
        skilled = max(0, min(cut, skilled))
        f.economy["dismissed"] = cut - skilled
        if skilled:
            f.economy["dismissed_engineers"] = skilled
        island.stats["labor.dismissed"] = island.stats.get("labor.dismissed", 0) + cut

    @staticmethod
    def _hoards(island: IslandState, ctx: StageContext, f: Facility) -> bool:
        """人を抱える施設か（plan.hoards。人数を荷・売上・預金で決める施設と、止まった施設は除く）。"""
        config = ctx.config
        if not planning.hoards(island, ctx.defs, f) or fac.is_halted(f, ctx.turn):
            return False
        if is_transport(config, f) or services.is_services(config, f) or is_bank(config, f):
            return False
        return staff.wanted(config, ctx.defs, f) is None

    @staticmethod
    def _kept(ctx: StageContext, f: Facility) -> int:
        """人を抱える施設が雇い続ける人数: 前のターンに働いていた人（動員した人を除く）から、辞める
        人の分（月 planning.attrition。端数は economy["attrition"] にためる）を引いた数。設備の
        傷んだ分を除いた全力の人数まで。"""
        before = f.workers - f.economy.get("mobilized", 0)
        leave = before * ctx.config.get("planning.attrition") + f.economy.get(ATTRITION, 0.0)
        f.economy[ATTRITION] = leave - int(leave)
        full = fac.scale(fac.workers_of(ctx.defs, f), fac.equipment_cap(f))
        return max(0, min(full, before - int(leave)))

    @staticmethod
    def _power_per_worker(island: IslandState, ctx: StageContext, f: Facility) -> int:
        """全力で動いたときの電気代を、働き手1人あたりにした額（値幅の上端の値段で）。"""
        if not f.built:
            return 0
        power = ctx.config.get("power.good")
        use = fac.recipe_of(ctx.defs, f)["inputs"].get(power, 0)
        workers = fac.workers_of(ctx.defs, f)
        if not use or not workers:
            return 0
        price = island.economy.prices.get(power, 0)
        high = price + part(price, to_basis(ctx.config.get("market.band")))
        return -(-money_of(high, use) // workers)

    @staticmethod
    def _island_wide(ctx: StageContext, f: Facility) -> bool:
        return is_transport(ctx.config, f) or not fac.located(f)

    @staticmethod
    def _area(island: IslandState, ctx: StageContext, f: Facility) -> list[Pos]:
        return within(island, fac.position(f), fac.commute_radius(ctx.defs, ctx.config, f.kind))

    @staticmethod
    def _dist(f: Facility, pos: Pos) -> int:
        return distance(fac.position(f), pos) if fac.located(f) else 0

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
    if not fac.located(f) or is_transport(ctx.config, f):
        tiles = [t for t in island.tiles if t.population]
    else:
        area = within(island, fac.position(f), fac.commute_radius(defs, ctx.config, f.kind))
        tiles = [island.tile(*p) for p in area if island.tile(*p).population]
    share = fac.labor_share(defs, island)
    labor = idle = 0
    for t in tiles:
        force = fac.labor_force(defs, island, t, share)
        free = max(0, force - t.ext.get("employed", 0))
        labor += force
        idle += max(0, free - int(subsistence_persons(defs, island, t, share) * share))
    return idle / labor if labor else 0.0


def register(registry: Registry) -> None:
    registry.add_stage(Employment())
