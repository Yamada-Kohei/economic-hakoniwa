"""家計の層。

層は game.yaml の household.layers で決めます（一般の労働者 general、資産を持つ層 owners、
フェーズ2-3 からの技術者の層 engineers、バランス調整の回3 からの失業している家計 jobless）。
share を書いた層（資産を持つ層）は住民のその割合の人数を島全体として持ち、働かずに配当と預金の
利子で暮らします。民営の施設を持たない経済体制（計画経済）の島には、資産を持つ層を置きません。
workers を書いた層（技術者の層）は、島全体の働き手の人数（economy.workers）を家族ごと持ちます
（人数 ÷ workers.yaml の share。働き手 0.5 なら2倍）。住民が減ると、働き手も同じ割合で減ります。
jobless を書いた層（失業している家計）は、一般の労働者のうち仕事のない人（村で自給の農業をしている
人を除く）とその家族です。人数は賃金の段の初めに、雇用の段の結果から数え直し（set_jobless）、
仕事を失った人と見つけた人の預金を1人あたりの額で一緒に動かします。失業手当はこの層に入り、
仕事のある家計とは所得・預金・食べた量を別に持ちます（現実の飢饉では、仕事と所得を失った人から
飢える。Codex E03）。残りの住民は一般の層です。

層ごとの状態（economy.households[層]）:
- persons: 人数
- share: 住民に対する割合（share か workers を持つ層だけ）
- workers: 層の働き手の種類（workers を持つ層だけ）
- income: 直近のターンの可処分所得（賃金 − 所得税 ＋ 失業手当 ＋ 預金の利子 ＋ 配当）。古い順
- jobless: 失業している家計の層の印（jobless を書いた層だけ）
- fed: 前のターンに食べられた割合（1000 分の 1。人口の段が書く）。足りなかった層は、次の市場で
  値幅の上を買い上がる（modules/market）
- hunger: 飢えの蓄え（栄養の不足が何か月分たまったか。人口の段）
- cap: 資産を持つ層の人数の上限（財産と所得で暮らせる人数。LB-42）。人口の段で数え直し、人数を
  割り振るどの場面でも「住民の割合」とこの上限の小さいほうにする。ないときは上限なし
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL, JOBLESS, household, layers, needs
from hakoniwa_core.facilities import ENGINEER
from hakoniwa_core.state import OWNER_PRIVATE, IslandState
from hakoniwa_core.units import BASIS, round_money, to_basis

SUPPORT = "family_support"  # island.scratch: 層 → 支え合いで受け取り、食べ物に先に使う額（回7）
CAP = "cap"  # 資産を持つ層の人数の上限（LB-42）
# 資産を持つ層が受けた家族の支えの記録（層の状態。所得の記録と同じターン数。暮らせる人数の式で
# だけ所得から引く。LB-42。W-280）
SUPPORTED = "supported"
# 1人1ターンの食べ物の額を数えるときの人数（1人分だと額の端数が大きいので、まとめて数えて割る）
FOOD_SAMPLE = 1000


def has_private_owners(defs: Defs, island: IslandState) -> bool:
    """民営の施設がありうる経済体制か（資産を持つ層を置くか）。"""
    economy = defs.regimes.get(island.regime.get("economy", ""), {})
    return economy.get("owner") == OWNER_PRIVATE


def new_worker_layer(kind: str) -> dict[str, Any]:
    """島全体の働き手 kind の層の初めの状態。"""
    return {"persons": 0, "share": 0.0, "workers": kind, "income": []}


def new_jobless_layer() -> dict[str, Any]:
    """失業している家計の層の初めの状態（人数は賃金の段で数える）。"""
    return {"persons": 0, "income": [], "jobless": True}


def init_layers(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """層を作り、人数を住民の数から決める。"""
    population = sum(t.population for t in island.tiles)
    households: dict[str, dict[str, Any]] = {}
    for name, spec in layers(config).items():
        if "workers" in spec:
            households[name] = new_worker_layer(spec["workers"])
        elif spec.get("jobless"):
            households[name] = new_jobless_layer()
        elif "share" in spec:
            if not has_private_owners(defs, island):
                continue
            households[name] = {"persons": 0, "share": spec["share"], "income": []}
        else:
            households[name] = {"persons": 0, "income": []}
    island.economy.households = households
    split_persons(island, defs, population)


def ensure_jobless(island: IslandState, config: GameConfig) -> bool:
    """回3 より前の島に、失業している家計の層を置く（人数は次の賃金の段で数える）。置いたら真。"""
    households = island.economy.households
    if not households:
        return False
    added = False
    for name, spec in layers(config).items():
        if spec.get("jobless") and name not in households:
            households[name] = new_jobless_layer()
            added = True
    return added


def jobless_layer(island: IslandState) -> str:
    """失業手当を受け取り、仕事を失った人が入る層（失業している家計の層がなければ一般の層）。"""
    return JOBLESS if JOBLESS in island.economy.households else GENERAL


def set_jobless(island: IslandState, persons: int) -> int:
    """失業している家計の人数を persons にし、仕事を失った人・見つけた人の預金を、元の層の
    1人あたりの額で一緒に動かす。一般の層より多くは移さない。動いた人数（増えた分が正）を返す。"""
    households = island.economy.households
    if JOBLESS not in households:
        return 0
    jobless, general = households[JOBLESS], households[GENERAL]
    pool = jobless["persons"] + general["persons"]
    target = max(0, min(persons, pool))
    moved = target - jobless["persons"]
    if moved:
        jobless["persons"] = target
        general["persons"] = pool - target
        move_people(island, {JOBLESS: moved})
    return moved


def family_of(defs: Defs, kind: str, count: int) -> int:
    """働き手 count 人の家族を合わせた人数（count ÷ workers.yaml の share）。"""
    share = defs.workers[kind].get("share", 1.0)
    return round_money(count / share + 0.5) if share > 0 else count


def split_persons(island: IslandState, defs: Defs, population: int) -> dict[str, int]:
    """住民 population を層に割り振り、前のターンからの人数の変化を返す。

    住民が前より減っていれば、島全体の働き手（技術者）も同じ割合で減らす。失業している家計の人数は
    賃金の段で数えた人数のまま（亡くなった人と国を出た人は、呼ぶ前にその層から引いておく）で、
    残りの住民に収まらない分だけ一般の層に戻す。
    """
    households = island.economy.households
    before = sum(h["persons"] for h in households.values())
    changes = {}
    fixed = 0
    for name in sorted(households):
        h = households[name]
        if "workers" in h:
            kind = h["workers"]
            count = island.economy.workers.get(kind, 0)
            if 0 <= population < before and count > 0:
                shrunk = count * population // before
                island.economy.workers[kind] = shrunk
                if kind == ENGINEER:
                    recount_engineers(island, count, 0, count - shrunk)
                count = shrunk
            persons = min(max(0, population - fixed), family_of(defs, kind, count))
            h["share"] = persons / population if population > 0 else 0.0
        elif "share" in h:
            persons = share_target(h, population)
            if h.get(CAP) is not None:
                persons = min(persons, h[CAP])
        else:
            continue
        changes[name] = persons - h["persons"]
        h["persons"] = persons
        fixed += persons
    for name in sorted(households):
        h = households[name]
        if h.get("jobless"):
            persons = max(0, min(h["persons"], population - fixed))
            changes[name] = persons - h["persons"]
            h["persons"] = persons
            fixed += persons
    general = households[GENERAL]
    changes[GENERAL] = population - fixed - general["persons"]
    general["persons"] = population - fixed
    return changes


def share_target(h: dict[str, Any], population: int) -> int:
    """share を書いた層の、住民の割合どおりの人数。"""
    return round_money(population * h["share"] + 0.5)


def pledged(island: IslandState, layer: str) -> int:
    """層が株を担保に借りている額の残高。"""
    from hakoniwa_core.economy import banks

    return sum(
        banks.outstanding(island, x)
        for x in banks.loans_by_borrower(island, household(layer))
        if x.purpose == banks.PLEDGE
    )


def owners_room(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    layer: str,
    income: float | None = None,
) -> int | None:
    """資産を持つ層が財産と所得で暮らせる人数（LB-42）。上限がなければ None。

    1人が使える額（層の資産の所得の直近の平均 I + 取り崩す割合 c2 × 預金、の1人あたり）が、暮らしの
    物差し T に届く人数。T は一般の層の1人あたりの所得の直近の平均 g と、1人1ターンのいちばん安い
    食べ物の額（food_cost。税と運賃を含む）の大きい方（自給の村が多く所得税の高い島では g が食べ物の
    額を下回り、資産の収入で食べ物も買えない人が「暮らせる」と数えられた。W-280）。c2 は利子では
    なく、財産から1ターンに取り崩して使う割合（年4%ほど）。

    層を出る人は預金だけを1人あたりで持ち出し、株と株を担保にした借入 P は層に残る。預金のうち
    借入の返済の取り置き R = min(預金, P) も層に残るので、持ち出せる1人あたりの預金を m とすると、
    釣り合う人数 = (I + c2 × R) ÷ (T − c2 × m)。層が0人のときの m は、戻ってくる一般の層の1人
    あたりの預金。分母が 0 以下（預金の取り崩しだけで T に届く）なら上限なし。

    株の値打ち（会社の純資産）は数えない。株を売る市場も、暮らしのために株を担保に借りる道もなく
    （担保の借入は出資のときだけ）、株が暮らしのお金になるのは配当として所得に入った分だけ。前は
    c2 × 株の値打ちを使える額に数え、配当のない会社の帳簿の値打ちで暮らせる人数がふくらんで上限が
    外れた（W-280）。受けた家族の支え（LB-35）も資産の所得ではないので I から引く（支えを受ける
    ほど資本家階級に残れる循環を切る。所得の記録そのものは変えない）。

    income を渡さなければ層の所得の記録の平均（支えを引く）で、記録がなければ（建国のターン）上限
    なし。
    """
    households = island.economy.households
    h = households[layer]
    keep = config.get("household.income_turns")
    if income is None:
        history = h["income"][-keep:]
        if not history:
            return None
        given = h.get(SUPPORTED, [])[-len(history) :]
        income = (sum(history) - sum(given)) / len(history)
    general = households.get(GENERAL)
    if general is None or general["persons"] <= 0:
        return None
    general_history = general["income"][-keep:]
    if not general_history:
        return None
    per_general = sum(general_history) / len(general_history) / general["persons"]
    c2 = wealth_propensity(config, layer)
    deposits = ledger.money(island, household(layer))
    debt = pledged(island, layer)
    kept = min(deposits, debt)
    if h["persons"] > 0:
        movable = (deposits - kept) / h["persons"]
    else:
        movable = ledger.money(island, household(GENERAL)) / general["persons"]
    living = max(per_general, food_cost(island, defs, config, FOOD_SAMPLE) / FOOD_SAMPLE)
    denominator = living - c2 * movable
    if denominator <= 0:
        return None
    numerator = income + c2 * kept
    return max(0, int(numerator // denominator))


def update_owners_cap(island: IslandState, defs: Defs, config: GameConfig, population: int) -> None:
    """人口の段で、資産を持つ層の人数の上限（CAP）を数え直す（LB-42。亡くなった人を引いた後、
    人数を割り振り直す前に呼ぶ）。

    暮らせる人数が今の人数より少なければ、上限をその人数にする（多い人はこの段で一般の層へ移る）。
    上限がある間に暮らせる人数が今の人数より多くなれば、住民の割合の人数までの差の
    household.layers.<層>.regrow ずつ上限を上げ（事業を起こし株を買い増すには時間がかかる。行き来で
    預金が漏れないように少しずつ）、住民の割合に届いたら上限を外す。上限のない層は、暮らせる人数が
    住民の割合を下回るまで上限を持たない。
    """
    for name, spec in layers(config).items():
        h = island.economy.households.get(name)
        if h is None or "share" not in h:
            continue
        # 計画経済から移ったばかりで所得の記録がまだない層（上限を持って置いた。politics の
        # _add_owners）は、所得 0 として数える
        income = 0 if h.get(CAP) is not None and not h["income"] else None
        room = owners_room(island, defs, config, name, income)
        target = share_target(h, population)
        if room is None:
            h.pop(CAP, None)
            continue
        persons = h["persons"]
        if room < persons:
            h[CAP] = room
            continue
        if h.get(CAP) is None:
            if room >= target:
                continue
            h[CAP] = persons
        goal = min(room, target)
        step = -(-max(0, goal - persons) * to_basis(spec.get("regrow", 1.0)) // BASIS)
        cap = persons + step
        if cap >= target:
            h.pop(CAP, None)
        else:
            h[CAP] = cap


def food_cost(island: IslandState, defs: Defs, config: GameConfig, persons: int) -> int:
    """persons 人の1ターンの食べ物を、前のターンの値段でいちばん安く買う額（消費税と家計の運賃を
    含む）。値段のない島では 0。"""
    from hakoniwa_core.economy import plan as planning
    from hakoniwa_core.units import QUANTITY_SCALE, money_of, to_milli

    if persons <= 0:
        return 0
    need = to_milli(persons * config.get("population.food_per_person"))
    prices = island.economy.prices
    rate = island.economy.freight.get("household", 0)
    best = 0
    for good, gdef in sorted(defs.goods.items()):
        if "food" not in gdef or prices.get(good, 0) <= 0:
            continue
        qty = -(-need * QUANTITY_SCALE // to_milli(gdef["food"]["nutrition"]))
        # 計画経済の島は家計が払う小売の値段で（PL-60。ほかの島は消費税の率）
        tax = planning.rate_of(island, config, good)
        amount = money_of(prices[good], qty) * (BASIS + rate + tax) // BASIS
        if best == 0 or amount < best:
            best = amount
    return best


def living_keep(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """資産を持つ層が出資・買い取りの前に手元に残す暮らしの分（FM-58。2026-11-15）: 次の市場の段で
    使う見込みの額（式 consumption）と、層の人数分の1ターンの食べ物の額（food_cost）の多い方に、
    株を担保にした借入の次のターンの返済（pledge_service）を足した額。

    資本家階級の所得（配当・利子）は決算の段より後に入り、次のターンの市場の段の買い物は今ある
    預金から払う。預金を丸ごと出資に回すと、次のターンに食べ物を買えずに飢えた（計画経済から
    移った島で、出資のたびに1ターンおきに飢えの蓄えがたまった。2026-11-14 の PR #416・#417 の
    試験）。現実の家計も、来月の暮らしの分を残した貯蓄から事業に出す。"""
    from hakoniwa_core.economy import OWNERS
    from hakoniwa_core.economy.stats import inflation

    h = island.economy.households.get(OWNERS)
    if h is None:
        return 0
    service = pledge_service(island, OWNERS)
    if h["persons"] <= 0:
        return service
    plan = consumption(island, OWNERS, config, inflation(island.economy))
    return max(plan, food_cost(island, defs, config, h["persons"])) + service


def pledge_service(island: IslandState, layer: str) -> int:
    """層が株を担保にした借入の、次のターンの利子と元金の返済（銀行の段は市場の段の後・配当の
    前なので、これも今の預金から払う）。"""
    from hakoniwa_core.economy import banks

    return sum(
        ledger.interest(banks.outstanding(island, x), x.rate)
        + min(banks.outstanding(island, x), x.installment)
        for x in banks.loans_by_borrower(island, household(layer))
        if x.purpose == banks.PLEDGE
    )


def owners_spare(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """資産を持つ層が出資・買い取りに使える預金: 使えるお金（取り置きを除く）から暮らしの分
    （living_keep）を引いた額。"""
    from hakoniwa_core.economy import OWNERS, Payer, funds

    if OWNERS not in island.economy.households:
        return 0
    have = funds.available(island, Payer(household(OWNERS)))
    return max(0, have - living_keep(island, defs, config))


def _founder_brings(island: IslandState, defs: Defs, config: GameConfig | None) -> int:
    """一般の層から資産を持つ層へ移る1人が出資に出せる預金: 一般の層の1人あたりの預金から、
    その人の1ターンの食べ物の額を残した分（FM-58。移った人も次のターンに食べる）。config が
    None（設定のない命令の前の確かめ）なら食べ物の分を引かない（実行のときに引いて見直す）。"""
    general = island.economy.households[GENERAL]
    if general["persons"] <= 0:
        return 0
    per = ledger.money(island, household(GENERAL)) // general["persons"]
    food = food_cost(island, defs, config, 1) if config is not None else 0
    return max(0, per - food)


def draw_founders(
    island: IslandState, defs: Defs, config: GameConfig, need: int, available: int
) -> int:
    """資産を持つ層の出資に使える額 available（暮らしの分を引いた owners_spare）が need に足りな
    ければ、足りない分を一般の層の預金から出す人を、その預金と一緒に資産を持つ層へ移す（働く人の貯蓄からの
    起業。FM-58）。1人が出すのは1人あたりの預金から自分の食べ物の分を残した額（_founder_brings）。
    移る人は住民の割合の人数まで（founders_room）。移した人数を返す。"""
    from hakoniwa_core.economy import OWNERS

    short = need - available
    households = island.economy.households
    if short <= 0 or OWNERS not in households:
        return 0
    owners, general = households[OWNERS], households[GENERAL]
    if general["persons"] <= 0:
        return 0
    brings = _founder_brings(island, defs, config)
    if brings <= 0:
        return 0
    population = sum(x["persons"] for x in households.values())
    room = min(general["persons"], max(0, share_target(owners, population) - owners["persons"]))
    moved = min(room, -(-short // brings))
    if moved <= 0:
        return 0
    owners["persons"] += moved
    general["persons"] -= moved
    if owners.get(CAP) is not None:
        owners[CAP] = max(owners[CAP], owners["persons"])
    move_people(island, {OWNERS: moved})
    return moved


def founders_room(island: IslandState, defs: Defs, config: GameConfig | None) -> int:
    """一般の層から資産を持つ層へ移って出資できる預金（FM-58。住民の割合の人数まで、1人が自分の
    食べ物の分を残して出せる額で。_founder_brings）。"""
    from hakoniwa_core.economy import OWNERS, Payer, funds

    households = island.economy.households
    if OWNERS not in households:
        return 0
    owners, general = households[OWNERS], households[GENERAL]
    if general["persons"] <= 0:
        return 0
    population = sum(x["persons"] for x in households.values())
    room = min(general["persons"], max(0, share_target(owners, population) - owners["persons"]))
    if room <= 0:
        return 0
    spare = funds.available(island, Payer(household(GENERAL)))
    return min(spare, _founder_brings(island, defs, config) * room)


def set_workers(island: IslandState, defs: Defs, kind: str, count: int) -> None:
    """島全体の働き手 kind の人数を count にし、層の人数を割り振り直して預金を一緒に移す
    （大学で育った人・引退した人）。"""
    island.economy.workers[kind] = max(0, count)
    population = sum(h["persons"] for h in island.economy.households.values())
    move_people(island, split_persons(island, defs, population))


def recount_engineers(island: IslandState, pool: int, grown: int, retired: int) -> None:
    """育成・引退と住民の減少で変わった技術者の数を、雇用の段で数えた「働いている技術者」と
    「仕事のない技術者」（このターンの記録に出す）に映す。減った人は両方から人数の割合で抜け、
    育った人は仕事のない側に入る（映さないと、働いている人が総数を超えるターンが出る）。"""
    scratch = island.scratch
    if "engineers_employed" not in scratch:
        return
    employed = scratch["engineers_employed"]
    left = scratch.get("engineers_left", 0)
    out = min(employed, retired * employed // pool) if pool > 0 else 0
    scratch["engineers_employed"] = employed - out
    scratch["engineers_left"] = max(0, left - (retired - out)) + grown


def move_people(island: IslandState, changes: dict[str, int]) -> None:
    """層を移った人の預金と住宅ローンを、元の層の1人あたりの額で一緒に移す（仕訳で付け替える）。

    share（と workers）の層の人数が増えた分は一般の層から、減った分は一般の層へ移ったと考える。
    飢えの蓄え（hunger）も、移った人の分を人数で混ぜる（人口の段）。
    """
    households = island.economy.households
    for name in sorted(changes):
        if name == GENERAL or not changes[name]:
            continue
        moved = changes[name]
        source, target = (GENERAL, name) if moved > 0 else (name, GENERAL)
        blend_hunger(households[source], households[target], abs(moved))
        persons = households[source]["persons"] + abs(moved)
        if persons <= 0:
            continue
        payer = household(source)
        money = ledger.money(island, payer)
        if "share" in households[source]:
            # 資産を持つ層を出る人は、株を担保にした借入の返済の取り置きを残す（LB-42）
            money -= min(money, pledged(island, source))
        amount = money * abs(moved) // persons
        ledger.pay(island, payer, household(target), amount, "layer_transfer")
        from hakoniwa_core.economy import banks  # 住宅ローンも一緒に移す（回3）

        banks.move_mortgages(island, payer, household(target), abs(moved), persons)
        _move_bought(island, source, target, abs(moved), persons)
        needs.move_clothes(households[source], households[target], abs(moved), persons)


def _move_bought(island: IslandState, source: str, target: str, moved: int, persons: int) -> None:
    """このターンに市場の段で買った物（island.scratch の household_goods）のうち、層を移った
    moved 人の分を、元の層の1人あたりの量で移った先へ移す（2026-11-10）。市場の段の後に層を移った
    人（参入の段で資本家階級へ移って出資した人、人口の段で一般の層へ戻った人）の食べ物が元の層に
    残り、移った先の層だけが食べられない（飢えの蓄えがたまる）ことがあった。persons は移る前の
    元の層の人数。"""
    bought = island.scratch.get("household_goods")
    if not bought or source not in bought or persons <= 0:
        return
    goods = bought[source]
    into = bought.setdefault(target, {})
    for good in sorted(goods):
        qty = goods[good] * moved // persons
        if qty > 0:
            goods[good] -= qty
            into[good] = into.get(good, 0) + qty


def blend_hunger(source: dict[str, Any], target: dict[str, Any], moved: int) -> None:
    """層を移った moved 人の飢えの蓄えを、移った先の層の蓄えに人数で混ぜる（人数は移した後）。

    これまでに耐えたいちばん大きい蓄え（hunger_endured）も同じく混ぜる。
    """
    stayed = max(0, target["persons"] - moved)
    total = stayed + moved
    if total <= 0:
        return
    for key in ("hunger", "hunger_endured"):
        carried = source.get(key, 0.0)
        held = target.get(key, 0.0)
        if carried or held:
            target[key] = round((held * stayed + carried * moved) / total, 4)


def add_income(island: IslandState, layer: str, amount: int) -> None:
    """このターンの層の可処分所得に足す（所得税は負で足す）。"""
    if amount:
        income = island.scratch.setdefault("income", {})
        income[layer] = income.get(layer, 0) + amount


def close_income(island: IslandState, keep: int) -> None:
    """ターンの終わりに、このターンの可処分所得を層ごとの記録に足す。"""
    income = island.scratch.get("income", {})
    support = island.scratch.get(SUPPORT, {})
    for name, h in island.economy.households.items():
        h["income"] = [*h["income"], income.get(name, 0)][-keep:]
        if "share" in h:  # 資産を持つ層: 受けた支えを別に持つ（暮らせる人数の式でだけ引く。LB-42）
            given = [*h.get(SUPPORTED, []), support.get(name, 0)][-keep:]
            if any(given):
                h[SUPPORTED] = given
            else:
                h.pop(SUPPORTED, None)


def layer_of(entity: str) -> str | None:
    """主体の名前が家計の層なら層の名前。"""
    return entity[3:] if entity.startswith("hh:") else None


def propensity(config: GameConfig, layer: str, real_rate: float) -> float:
    """層の c1: 既定の値から、預金の実質金利が1%高いごとに c1_rate_effect 下げる（± c1_range）。"""
    settings = config.get("household")
    shift = settings["c1_rate_effect"] * real_rate * 100
    limit = settings["c1_range"]
    return layers(config)[layer]["c1"] - max(-limit, min(limit, shift))


def wealth_propensity(config: GameConfig, layer: str) -> float:
    """層の c2（預金から毎ターン使う割合）: 層に c2 を書いていればその値、なければ household.c2。

    資本家階級（上位1%）は財産から年3〜5%しか取り崩さないので、働く層（household.c2）と分ける
    （2026-11-03）。"""
    spec = layers(config)[layer]
    return spec["c2"] if "c2" in spec else config.get("household.c2")


def consumption(island: IslandState, layer: str, config: GameConfig, inflation: float) -> int:
    """層が使う額: C = c1 × Ȳ + c2 × M（名前付きの式 consumption の既定）。

    Ȳ は直近のターンの可処分所得の平均（記録がなければこのターンの所得）、M は層の財産（wealth。
    働く層は預金、資本家階級は預金 + 株の値打ち − 株を担保にした借入）。
    c1 は預金の実質金利（預金金利 − 物価上昇率）が1%高いごとに c1_rate_effect 下げる
    （既定の値から ± c1_range）。c2 は層ごと（wealth_propensity）。
    """
    settings = config.get("household")
    h = island.economy.households[layer]
    if h["persons"] <= 0:  # 0人の層（資本家階級。LB-42）は物を買わない
        return 0
    history = h["income"][-settings["income_turns"] :]
    if history:
        income = sum(history) / len(history)
    else:
        income = island.scratch.get("income", {}).get(layer, 0)
    c1 = propensity(config, layer, island.policy.deposit_rate / BASIS - inflation)
    c2 = wealth_propensity(config, layer)
    return max(0, round_money(c1 * max(0.0, income) + c2 * wealth(island, config, layer)))


# island.scratch: このターンの資産を持つ層の株の値打ち − 株を担保にした借入
SHARES_WORTH = "owners_shares_worth"


def wealth(island: IslandState, config: GameConfig, layer: str) -> int:
    """層が取り崩すもとの財産: 預金。資産を持つ層（share を書いた層。資本家階級）は、持っている
    民営の企業の株の値打ち（純資産。companies.owners_collateral）から株を担保にした借入の残高を
    引いた分も足す（現実の資本家も財産全体から取り崩す。2026-11-03）。株の値打ちはターンに一度
    だけ数える。"""
    deposits = ledger.money(island, household(layer))
    if "share" not in layers(config)[layer]:
        return deposits
    shares = island.scratch.get(SHARES_WORTH)
    if shares is None:
        from hakoniwa_core.economy.companies import owners_collateral

        shares = owners_collateral(island) - pledged(island, layer)
        island.scratch[SHARES_WORTH] = shares
    return max(0, deposits + shares)
