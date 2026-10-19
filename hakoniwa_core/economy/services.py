"""商業・サービス業（バランス調整。2026-10-15 にセビーリャさんが選んだ「サービスの仕事」）。

家計は所得の一部を、商店・飲食・理美容・娯楽・修理などのサービスに使います。サービスはその場で作って
その場で使う物で、持ち越せず（goods.yaml の shelf_life: 0）、島の外へも運べません。島に1つの
商業・サービス業（マスを持たない施設。game.yaml の services.facility）が、住民の住む町で人を雇って
出します。

- 買い方: 家計の層は、必需品（食料・衣料）を買った残りのうち household.services の割合をサービスに
  使います。実質の使う額（1人あたり、物価指数で割る）が base_income のとき share で、倍になるごとに
  rise ずつ増え、半分になるごとに減ります（floor〜max）。所得が上がるほどサービスに使う割合が増え、
  所得の低い国では食べ物に、高い国ではサービスに多く使う形になります。
- 店の場所: 住民は、通勤圏（labor.commute_radius）の中でいちばん人の多い集落（同じなら近い方）で
  サービスを買います。その集落が店の場所（中心）で、雇う人数は中心ごとに、買いに来る住民の数で
  割り振ります。村の人は近くの町へ買いに行くので、町に店が集まります。人は中心の通勤圏から、前の
  ターンの人を雇い続けてから、ほかの施設が雇った後に残った失業者を近いマスの順に雇います。店は
  ほかの施設から引き抜かず、ほかの施設は賃金にかかわらず店から引き抜けます（多くの小さな店は、ほかに
  仕事のない人の受け皿になる。設計の「ほかの施設は店から引き抜けます」）。
- 量と値段: 働く人1人が1ターンに services.per_worker のサービスを出します。市場の段で家計に売り、
  値段は板寄せで決まります（売れ残りはそのターンのうちに消えます）。初めの値段は「賃金 ÷ 1人の量」
  に上乗せ（原価＋1割。economy/prices.py）です。
- 雇う人数: 直近の売上（その値段で買いたかったのに買えなかった分も、そのターンの値段で足す）×
  services.labor_share ÷ 賃金 を目安に、前のターンの人数から market.sales_smoothing の割合だけ
  近づけます。売上が増えれば人を増やし、店が余って値段が下がれば減らします。計画経済の島では、公定
  価格での需要（買えなかった分も含む）に planning.target_markup を足した量の分だけ雇います。
- 人の動き: 中心で雇いきれなかった席は、人手の足りない施設と同じく、人を呼び、町を育てます
  （modules/population の vacancies）。
- 持ち主: 運送業と同じく経済体制の既定です（市場経済と混合経済は民営、計画経済は国営）。
  多くの小さな店の集まりなので、倒産と撤退はしません（modules/failures と firms）。
"""

from __future__ import annotations

import math
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import OWNERS, books, default_accounting
from hakoniwa_core.geo import within
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState
from hakoniwa_core.units import money_of, round_money, to_milli

Pos = tuple[int, int]
# 商業・サービス業の位置。マスを持たない施設は負の位置に置き、サーバーは島の中で位置が重ならない
# ように保存するので、運送業の (-1, -1) と海運業の (-2, -2) と別の位置にする
SERVICES_POS = (-3, -3)
CENTERS = "services_centers"  # island.scratch: 住んでいるマス → 店の場所（中心）のマス
SEATS = "seats"  # 施設の economy: 次のターンに雇う人数の目安
QUOTAS = "centers"  # 施設の economy: 中心ごとの [割り振った人数, 雇えた人数]
# 施設の economy: 雇う人数の目安のうち、直売・屋台で小売りを売る人数（回7）
RETAIL_SEATS = "retail_seats"
# island.scratch: 直売・屋台で売った小売りの量と代金（economy/retail.py の DIRECT と同じ）
DIRECT = "retail_direct"


def settings(config: GameConfig) -> dict[str, Any]:
    return config.get("services")


def good_of(config: GameConfig) -> str:
    return settings(config)["good"]


def is_services(config: GameConfig, f: Facility) -> bool:
    return f.kind == settings(config)["facility"]


def facility_of(island: IslandState, config: GameConfig) -> Facility | None:
    return next((f for f in island.facilities if is_services(config, f)), None)


def unit_price(config: GameConfig, wage: float) -> int:
    """賃金 wage のときの、サービス1単位の原価＋上乗せ（初めの値段と公定価格の解き方と同じ）。"""
    markup = 1 + config.get("market.initial_prices.markup")
    return max(1, round_money(wage / settings(config)["per_worker"] * markup + 0.5))


def prepare(island: IslandState, defs: Defs, config: GameConfig) -> Facility | None:
    """お金の経済の島に、まだなければ商業・サービス業を置く（建国したばかりの島も、前のフェーズ
    から続く島も、移し替えの段で1度だけ）。置いたときは、サービスの値段を今の平均賃金から決め直す
    （物価指数で直した初めの値段では、賃金の伸びと合わないため）。"""
    if not island.has_economy:
        return None
    found = facility_of(island, config)
    if found is not None:
        return found
    kind = settings(config)["facility"]
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    state = regime.get("owner", OWNER_STATE) == OWNER_STATE or kind in regime.get("state_owned", ())
    owner = OWNER_STATE if state else OWNER_PRIVATE
    if owner == OWNER_PRIVATE and OWNERS not in island.economy.households:
        owner = OWNER_STATE
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=SERVICES_POS[0],
        y=SERVICES_POS[1],
        owner=owner,
        accounting=default_accounting(defs, island, kind, owner),
        built=True,
    )
    assign_serial(island, f)
    island.facilities.append(f)
    good = good_of(config)
    price = unit_price(config, island.economy.average_wage or config.get("labor.initial_wage"))
    island.economy.prices[good] = price
    island.economy.price_history[good] = [price]
    return f


def budget_share(config: GameConfig, income: float) -> float:
    """必需品の残りのうちサービスに使う割合。income は実質の使う額の base_income に対する倍率。"""
    spec = config.get("household.services")
    if income <= 0:
        return spec["floor"]
    value = spec["share"] + spec["rise"] * math.log2(income)
    return max(spec["floor"], min(spec["max"], value))


# --- 店の場所と雇い方 ---------------------------------------------------------------------


def centers(island: IslandState, config: GameConfig) -> dict[Pos, Pos]:
    """住んでいるマスごとの、サービスを買いに行く集落のマス（通勤圏の中でいちばん人の多い集落。
    同じなら近い方、同じ距離なら y, x の順）。ターンの中では island.scratch に置いて使い回す。"""
    cached = island.scratch.get(CENTERS)
    if cached is not None:
        return cached
    radius = config.get("labor.commute_radius")
    homes = {(f.x, f.y) for f in island.facilities if fac.is_housing(config, f) and fac.on_map(f)}
    out: dict[Pos, Pos] = {}
    for tile in island.tiles:
        if tile.population <= 0:
            continue
        pos = (tile.x, tile.y)
        best: tuple[int, Pos] | None = None
        for near in within(island, pos, radius):
            if near != pos and near not in homes:
                continue
            people = island.tile(*near).population
            if best is None or people > best[0]:
                best = (people, near)
        out[pos] = best[1] if best is not None else pos
    island.scratch[CENTERS] = out
    return out


def quotas(island: IslandState, config: GameConfig, seats: int) -> dict[Pos, int]:
    """雇う人数 seats を、中心ごとに買いに来る住民の数で割り振る（端数は大きい順に1人ずつ）。"""
    weights: dict[Pos, int] = {}
    for pos, center in centers(island, config).items():
        weights[center] = weights.get(center, 0) + island.tile(*pos).population
    total = sum(weights.values())
    if seats <= 0 or total <= 0:
        return {}
    out = {c: seats * w // total for c, w in weights.items()}
    left = seats - sum(out.values())
    order = sorted(weights, key=lambda c: (-(seats * weights[c] % total), c[1], c[0]))
    for c in order[:left]:
        out[c] += 1
    return {c: n for c, n in out.items() if n > 0}


def _order(plan: dict[Pos, int]) -> list[Pos]:
    return sorted(plan, key=lambda c: (-plan[c], c[1], c[0]))


def keep(
    island: IslandState,
    config: GameConfig,
    f: Facility,
    plan: dict[Pos, int],
    previous: dict[Pos, int],
    free: dict[Pos, int],
) -> dict[Pos, int]:
    """前のターンに雇っていた人（previous。マスごと）を、中心ごとの人数 plan まで、中心に近いマスの
    人から雇い続ける（free から引く）。マスごとの雇った人数を返す。"""
    radius = config.get("labor.commute_radius")
    left_over = dict(previous)
    staff: dict[Pos, int] = {}
    for center in _order(plan):
        need = plan[center]
        for pos in within(island, center, radius):
            if need <= 0:
                break
            take = min(left_over.get(pos, 0), need, free.get(pos, 0))
            if take > 0:
                left_over[pos] -= take
                free[pos] -= take
                staff[pos] = staff.get(pos, 0) + take
                need -= take
        f.economy.setdefault(QUOTAS, {})[_key(center)] = [plan[center], plan[center] - need]
    return staff


def recount(
    island: IslandState,
    config: GameConfig,
    f: Facility,
    plan: dict[Pos, int],
    staff: dict[Pos, int],
) -> None:
    """ほかの施設に引き抜かれた後の、中心ごとの雇えた人数を数え直す（keep と同じ順に、残った人を
    中心に近いマスから割り当てる）。"""
    radius = config.get("labor.commute_radius")
    record = f.economy.setdefault(QUOTAS, {})
    left = dict(staff)
    for center in _order(plan):
        entry = record.setdefault(_key(center), [plan[center], 0])
        need = entry[1]
        for pos in within(island, center, radius):
            if need <= 0:
                break
            take = min(left.get(pos, 0), need)
            if take > 0:
                left[pos] -= take
                need -= take
        entry[1] -= need


def hire(
    island: IslandState,
    config: GameConfig,
    f: Facility,
    plan: dict[Pos, int],
    staff: dict[Pos, int],
    free: dict[Pos, int],
) -> None:
    """中心ごとに足りない人数を、中心の通勤圏の失業者から近いマスの順に雇う（staff に足す）。"""
    radius = config.get("labor.commute_radius")
    record = f.economy.setdefault(QUOTAS, {})
    for center in _order(plan):
        entry = record.setdefault(_key(center), [plan[center], 0])
        need = entry[0] - entry[1]
        for pos in within(island, center, radius):
            if need <= 0:
                break
            take = min(free.get(pos, 0), need)
            if take > 0:
                free[pos] -= take
                staff[pos] = staff.get(pos, 0) + take
                need -= take
        entry[1] = entry[0] - need


def staff_by_tile(island: IslandState, config: GameConfig) -> dict[Pos, int]:
    """店で働く人の、住んでいるマスごとの人数（雇用の段の結果）。ほかの施設は賃金にかかわらず
    店から引き抜けるので、民営工場の参入と自動の計画は、この人たちも雇える人に数える。"""
    f = facility_of(island, config)
    out: dict[Pos, int] = {}
    if f is None:
        return out
    for key, count in f.economy.get("staff", {}).items():
        if count > 0:
            x, y = key.split(",")
            out[(int(x), int(y))] = count
    return out


def vacancies(island: IslandState, config: GameConfig) -> list[tuple[Pos, int]]:
    """中心ごとの、雇いきれなかった人数（雇用の段の結果）。"""
    f = facility_of(island, config)
    if f is None:
        return []
    found = []
    for key, (planned, hired) in sorted(f.economy.get(QUOTAS, {}).items()):
        if planned > hired:
            x, y = key.split(",")
            found.append(((int(x), int(y)), planned - hired))
    return found


def _key(pos: Pos) -> str:
    return f"{pos[0]},{pos[1]}"


# --- 作る・計画する ------------------------------------------------------------------------


def produce(island: IslandState, config: GameConfig, defs: Defs, turn: int) -> int:
    """このターンに出せるサービスの量（働く人 × 1人の量。1000 分の 1）を施設の在庫に置く（市場の段の
    初め。売れ残りは市場の段の終わりに消える）。"""
    f = facility_of(island, config)
    if f is None or not f.built or "foreclosed" in f.economy:
        return 0
    good = good_of(config)
    seats = f.economy.get(SEATS, 0)
    # 直売・屋台で小売りを売る人（回7。前のターンに店で買いきれなかった小売りの分）は
    # サービスを出さない
    selling = f.workers * f.economy.get(RETAIL_SEATS, 0) // seats if seats > 0 else 0
    qty = to_milli((f.workers - min(f.workers, selling)) * settings(config)["per_worker"])
    f.operating_rate = fac.ratio(f.workers, seats) if seats > 0 else 0
    if qty > 0:
        stock.add(f.stock, good, qty, turn, defs.goods)
        books.stock_moved(island, f.id, good, qty)
    return qty


def plan(island: IslandState, config: GameConfig, planned: bool, unmet: int = 0) -> None:
    """市場の後に、次のターンに雇う人数の目安（economy["seats"]）を決める。unmet は、約定した値段
    でも買いたかったのに買えなかった量（1000 分の 1。modules/market の _services_unmet）。"""
    f = facility_of(island, config)
    if f is None:
        return
    spec = settings(config)
    good = spec["good"]
    record = next((m for m in island.markets if m.good == good), None)
    if record is None:
        sold = unmet = 0
        price = island.economy.prices.get(good, 0)
    else:
        sold, price = record.volume, record.price
    wage = f.economy.get("wage") or island.economy.average_wage
    wage = wage or config.get("labor.initial_wage")
    per = spec["per_worker"]
    # 店で買いきれなかった小売りを直売・屋台で売った量と代金（回7。economy/retail.py）
    direct_qty, direct_paid = island.scratch.get(DIRECT, (0, 0))
    if planned:
        markup = 1 + config.get("planning.target_markup")
        wanted = (sold + unmet) * markup / per / 1000
        selling = direct_qty * markup / 1000
    else:
        wanted = money_of(price, sold + unmet) * spec["labor_share"] / wage
        selling = direct_paid * spec["labor_share"] / wage
    wanted += selling
    seats = f.economy.get(SEATS, 0)
    smoothing = config.get("market.sales_smoothing")
    f.economy[SEATS] = max(0, round_money(seats + (wanted - seats) * smoothing + 0.5))
    share = selling / wanted if wanted > 0 else 0.0
    f.economy[RETAIL_SEATS] = round_money(f.economy[SEATS] * share)
    if not f.economy[RETAIL_SEATS]:
        f.economy.pop(RETAIL_SEATS)
    stats = island.stats
    stats["services.workers"] = f.workers
    stats["services.sold"] = sold / 1000
    stats["services.sales"] = money_of(price, sold)
    # 作った量は売れた量（売れ残りはその場で消える）。実質の生産と、関門の「作った島」に数える
    if sold > 0:
        stats[f"production.{good}"] = sold / 1000
    stats["services.price"] = price
    stats["services.seats"] = f.economy[SEATS]


def next_payments(f: Facility) -> int:
    """次のターンの賃金の見込み（雇う人数の目安 × 賃金。運転資金の融資と配当の残し方に使う）。"""
    return max(f.economy.get(SEATS, 0), f.economy.get("wanted", 0)) * f.economy.get("wage", 0)
