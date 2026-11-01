"""標準の計画: 島の様子を見て、自動で命令を並べる簡単な方針。

一括シミュレーション（関門のテスト）と、開発用のサンプルの島で、プレイヤーの代わりに使います。
ゲームのルールではないので、ここの数値は game.yaml ではなくこのファイルに置いています。

方針（毎ターン、工事中の施設が少なければ、上から見て最初に足りないものを1つ建てる。同じ物を作る
施設に動ききっていないものがあれば、それは建てない）:
1. 食料の生産が食べる量に届かなければ、農場か漁港
2. 発電の余裕が少なければ、発電所（石炭があれば火力、なければ水力）
3. 工事の資材が国の倉庫になく、島に作る施設もなければ、それを作る施設（工事が止まらないように、
   同時に進める工事の数を超えても建てる）。作る施設がどれも原料を待って止まっていれば、その原料を
   作る施設
4. 投入財の作る量が使う量に届かなければ、それを作る施設。島で作っている投入財と工事の資材は、
   前のターンの市場で売りに出た量が買いたい量に届かず、値段が初めの PRICE_HIGH 倍を超えていても
   建てる（工事の資材の使う量は作る量と使う量の流れに数えないため。フェーズ3-2）
4'. 海外の商人が持ってくる量を減らし始めたら（foreign_merchant.taper_from ターンを過ぎてから）、
   前のターンに商人から買った物のうち島で作っていない物を作る施設（投入財が島で作られている作り方だけ。
   投入財も商人から買っていれば、その投入財を作る施設が先に建つ）
5. 消費財（衣料・消費財・嗜好品）の生産が住民1人あたりの目安（household.targets）に届かないか、
   前のターンの市場で売りに出た量が買いたい量に届かなければ、それを作る施設（加工食品・肉・自動車・
   燃料も、売りに出た量が買いたい量に届かなければ作る施設）。必需品、ぜいたく品、自動車、燃料の順
5'. 海運業が輸送船を買えずにいて島に造船所がなければ、造船所を1つ（回6。鋼鉄と機械が島で作られて
   いるか貿易で手に入る島だけ）
6. 島の鉱業資源のうち、島のどの施設も使っていない物（ボーキサイト・原油など）は、それを使う施設を
   1つ建てる（輸出向け。フェーズ3-2。採る鉱山は、4 で足りない投入財として後から建つ）
7. 入っている貿易圏の前のターンの市場で、買いたい量の一部しか買えなかった物を、島の中だけで作れる
   なら作る施設（輸出向け。フェーズ3-2。鉱業資源が5つになり、石炭と鉄鉱石の両方がある島が減った
   ので、鋼鉄や機械を作れる島が圏の分も作る）
8. 生産の連鎖（鉄鉱石 → 鋼鉄 → 機械 → 新しい工場）を太くする: 製鉄所と機械工場を増やす
   （6〜8 は、失業率がふつうより高く、人手の足りない施設が少ない間だけ）
教育（フェーズ2-3）: 学校の定員が子どもの数の SCHOOL_TARGET に届かなければ、食料・発電・工事の資材の
次に学校を建てます。建っている施設（工事中を含む）が要る技術者が、島の技術者の ENGINEER_TARGET 倍を
超え、大学がなければ大学を建てます。どちらも、資材が倉庫にあるか島で作っているときだけで、同じ資材を
待っている工事があるあいだは建てません（大学は、民営工場の工事に要る分の資材を倉庫に残します）。
資材が倉庫に足りず島のどの施設も作っていなければ、先にその資材を作る施設を建てます。
投入財は、島の中で作れるか貿易で手に入れば使えると見ます（フェーズ3-2。発電所は島の中だけ）。
作り方は気候で使えるものだけで、農場と牧場は肥沃なマスを先に選びます。
4〜8 では、投入財に余裕のない施設は建てません（鉱山を増やせないのに製鉄所を増やすと、鉄鉱石の
取り合いで値段が跳ね上がるだけなので）。
ほかの工事が待っている資材を建った施設がどれも作っていなければ、それを使う施設は、その資材を作る
施設でない限り建てません（初めの倉庫の機械を発電所に使うと、製鉄所と機械工場が互いを待って止まる）。
まだ島のどの施設も作っていない工事の資材（鋼鉄と機械）は、それを作る施設を1つ建てる分を国の倉庫に
残し、発電所のほかの工事に使わず、売りにも出しません。
失業率が高い間は、同時に進める工事を1つ増やします（工事の賃金と資材の買い物が働き口になる）。
同時に進める工事の数には、資材を待って止まっている民営の工事を数えません。公共事業費が足りずに
止まっている国の工事があるあいだは、新しい工事は 3 の資材を作る施設だけにします。
お金の経済の島では、国の倉庫にある財のうち島の施設が仕入れに困っている物（建国のときの石炭など）を、
毎ターン市場に売ります（建てる命令と一緒に出します）。
民営工場が建てられる島では、島で作っていない工事の資材（機械など）も、国の工事に取っておく分を除い
て、民営の工事が待っている量か1つの工事に要る量まで売りに出します（国の倉庫の物は、売りに出さなけれ
ば民間は買えないので）。民営の工事が待っていなければ、OFFER_TURNS + 1 ターンに一度にします。
建てた施設は、陸地を通る一番短い送電線で送電網につなぎ、施設のマスと周りの陸のマスのうち交通の
整備度が infrastructure.auto_works.road_level に届かないマスに道路を引きます（フェーズ2-3。インフラ
の係数を 1 以上にする。同じ資材を待っている工事があるあいだは引きません）。
上下水道が足りずに育たない町があれば、そのマスに上下水道を引きます。

貿易（フェーズ3-1）: plan_trade が世界を見て、北と南に1つずつ誰でも入れる貿易圏を作り、貿易の
始まった島をそれぞれに加盟させます（真ん中の行の島は両方）。

お金の経済の島では、初めに失業手当を平均賃金の BENEFIT % にし、BUDGET_TURNS ターンごとに
公共事業費（国の工事とインフラの年の予算）と教育費を見直します。教育費は、学校と大学を全力で
動かす年の費用（今の賃金と、値幅の上端の教材の値段で）の EDUCATION_MARGIN 倍で、返す力の比が
FISCAL_LIMIT を超えたら公共事業費と同じ率で減らします（EDUCATION_FLOOR まで）。
住民1人あたり PUBLIC_WORKS を基本に、失業率がふつう（5%）より高ければ増やし、物価上昇率が高ければ
減らします（額を物価や GDP に合わせて動かさないので、物価が下がり続けたり上がり続けたりしにくい）。
国営の施設の設備が1年に傷む値打ち（直す分。回6）は、その上に足します。
返す力の比（中央銀行以外に払う利子 ÷ 国の収入）が FISCAL_LIMIT を超えた分に応じても減らします
（利回りが上がって利払いが膨らみ続けないように。回5 で借金の対年GDP比から替えた）。
財政の手（回5）: BUDGET_TURNS ターンごとに（公共事業費の見直しと別のターン）、国が賃金を払いきれて
いなければ、所得税と消費税を TAX_STEP ずつ上げます（TAX_MAX まで）。返す力の比が高いだけでは上げず、
公共事業費を減らします（比で税を上げると、1島の試験で消費が落ちて工場が建たなくなった。借金の重さは
回5 の後半で測り直す）。賃金を払えていて、比が FISCAL_LIMIT の半分より低く、国庫に
支出の SURPLUS_TURNS ターン分があれば、建国のときの税率まで TAX_STEP ずつ戻します。
飢饉の手（回5。設計書 12 確かめ方 GT-29）: 食べ物の充足が FAMINE_RATIO を下回った月は、国営の
農場のうち穀物以外を作っているものを穀物に替え、国の倉庫の食べ物をすべて市場に出し、足りない分の
穀物を今の値段の EMERGENCY_PRICE 倍の指値で国の倉庫に買い入れます（値段が上がれば商社が輸入する）。
国の倉庫の年の予算は、その買い入れに足りるまで上げます。ふだんの月の穀物の蓄えと入れ替えは、計画に
よらず国の穀物の備蓄（economy/reserve.py。W-186 の直し 回1）がします。
電気料金が初めの値段の POWER_CAP 倍を超えたら、そこを公共料金の上限にします。
民営工場が建てられる島（市場経済と混合経済）では、空いた工業団地のマスが ZONE_SPARE より少なければ、
町の通勤圏の空いた平地と、地下資源のある島では山を ZONE_BATCH マスまで工業団地に指定します（町の
すぐ隣は町が育つ余地として残す）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, nature, scale, stock, trade
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.defs.kinds import is_variant
from hakoniwa_core.economy import arrears, equipment, needs, services
from hakoniwa_core.economy import public_housing as pub
from hakoniwa_core.economy.prices import initial_prices, unit_cost
from hakoniwa_core.geo import components, distance, shortest_path, within
from hakoniwa_core.modules import infrastructure as public_works
from hakoniwa_core.modules.firms import private_allowed
from hakoniwa_core.modules.market import MERCHANT
from hakoniwa_core.state import (
    ORDER_PENDING,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_COMMERCIAL,
    ZONE_INDUSTRIAL,
    ZONE_NONE,
    IslandState,
    OrderState,
    WorldState,
)
from hakoniwa_core.trade import blocs as trade_blocs
from hakoniwa_core.trade import merchant as trade_merchant
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, money_of, to_milli

MAX_SITES = 2  # 同時に進める工事の数（資材を待って止まっている民営の工事は数えない）
POWER_MARGIN = 1.25  # 発電の能力を、使う量のこの倍に保つ
FOOD_MARGIN = 1.1  # 食料の生産を、食べる量のこの倍に保つ
CONSUMER_MARGIN = 1.0  # 消費財の生産を、住民1人あたりの目安のこの倍に保つ
MARKET_SHORT = 0.9  # 前のターンの市場で、売りに出た量が買いたい量のこの割合より少なければ足りない
PRICE_HIGH = 1.5  # 投入財と資材は、買いたい量に届かず値段が初めのこの倍を超えていれば足りない
BUSY_UNEMPLOYMENT = 0.1  # 失業率がこれを超えている間は、同時に進める工事を1つ増やす
MAX_MISSING_WORKERS = 1000  # 足りない労働者がこの人数（× 国の大きさの倍）より多い間は増やさない
BUDGET_TURNS = 6  # 公共事業費を見直す間隔
PUBLIC_WORKS = 600  # 公共事業費の基本（住民1人あたりの年の額）
HOUSING_MARGIN = 1.5  # 住宅費: 町の床が1年に傷む分の資材の値段に掛ける（値段の動きの余裕）
HOUSING_BACKLOG_YEARS = 2  # 住宅費: 買いきれていない手入れの資材を、この年数で買う
UNEMPLOYMENT_RESPONSE = 5.0  # 失業率がふつうより 1% 高いごとに、基本のこの % だけ増やす
INFLATION_RESPONSE = 5.0  # 物価上昇率が INFLATION_LIMIT より 1% 高いごとに、基本のこの % だけ減らす
INFLATION_LIMIT = 0.05
NATURAL_UNEMPLOYMENT = 0.05
# 返す力の比（中央銀行以外への利払い ÷ 国の収入）がこれを超えたら、歳出を削り増税する
FISCAL_LIMIT = 0.3
BURDEN_RESPONSE = 2.0  # 比が FISCAL_LIMIT より 0.1 高いごとに、公共事業費を基本の 20% 減らす
# 教育費も、比が FISCAL_LIMIT を超えた分に応じて同じ率で減らす（W-63）。借金の重い国が学校の費用も
# 切り詰める形で、予算の目安（全力で動かす費用の EDUCATION_MARGIN 倍）の半分までにとどめる
# （全力の費用の 0.65 倍ほどで、学校を閉じるほどは削らない）
EDUCATION_FLOOR = 0.5
TAX_TURN = 3  # BUDGET_TURNS の周期のうち税を見直すターン（公共事業費の見直しと重ならないように）
TAX_STEP = 2  # 一度に上げ下げする税率（ポイント）
TAX_MAX = {"income_tax": 30, "consumption_tax": 20}  # 上げる上限（%）
SURPLUS_TURNS = 6  # 国庫に支出のこのターン数分があれば、税を戻してよい
FAMINE_RATIO = 0.95  # 食べ物の充足がこれを下回った月は飢饉の手を打つ
EMERGENCY_PRICE = 1.5  # 飢饉の月の穀物の買い入れの指値（今の値段のこの倍）
GRAIN = "grain"
WORKS_RANGE = (0.2, 2.0)  # 基本に対する倍率の下限と上限
STARVED_RAISE = 0.25  # 国の工事が公共事業費を待って止まっている間、見直しのたびに増やす割合
UNDERUSED = 0.7  # 同じ物を作る施設の稼働率がこれより低い間は、増やさない
POWER_CAP = 2.0  # 電気料金が初めの値段のこの倍を超えたら、そこを公共料金の上限にする
BENEFIT = 40  # 失業手当（平均賃金に対する %）
SALE_TURNS = 2  # 国の倉庫から一度に売りに出す量（島の施設が使う量の何ターン分か）
OFFER_TURNS = 5  # 民営の工事が待っていない資材は、直近このターン数に売りに出ていなければ売りに出す
ZONE_SPARE = 3  # 空いた工業団地のマスがこれより少なければ、工業団地を足す
ZONE_BATCH = 6  # 一度に工業団地に指定するマスの数
# 連鎖を太くするときに、この順で数をそろえていく施設
GROWTH = ("steel_mill", "machinery_plant", "building_materials_plant")
# 教育とインフラ（フェーズ2-3）
SCHOOL = "school"
UNIVERSITY = "university"
SCHOOL_TARGET = 0.8  # 学校の定員を、子どもの数のこの割合に保つ
ENGINEER_TARGET = 0.8  # 要る技術者が、島の技術者のこの倍を超えたら大学を建てる
EDUCATION_MARGIN = 1.3  # 教育費を、学校と大学を全力で動かす年の費用のこの倍にする
# 標準の計画が水力発電所を建てるのは、見込める出力が全力のこの割合以上の場所だけ。それより水の少ない
# 場所は、建てられても（施設の定義の min）割に合わないので、火力発電所に回す（W-148）
WATER_MIN = 0.6
# 治安と国防（段6 A2）
SECURITY_FROM = 24  # 警察署を建て始めるターン（食料・電力・資材の施設が先）
SECURITY_MARGIN = 1.3  # 治安費を、警察署を全力で動かす年の費用のこの倍にする
POLICE = "police"
BARRACKS = "barracks"


def plan_turn(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    """ターン turn の処理の前に足す命令（種類, 中身）の並び。"""
    if any(o.status == ORDER_PENDING for o in island.orders):
        return []
    return (
        _warehouse_sales(island, defs, config)
        + _food_orders(island, defs, config, turn)
        + _plan_rest(island, defs, config, turn)
    )


def _plan_rest(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    policies = (
        _labor_policy_order(island),
        _budget_order(island, defs, config, turn),
        _tax_order(island, config, turn),
        _price_cap_order(island, defs, config),
        _zone_order(island, defs, config),
        _commercial_order(island, defs, config),
        _water_order(island, defs, config),
        _grade_order(island, defs, config),
    )
    for policy in policies:
        if policy is not None:
            return [policy]
    # 資材を待って止まっている民営の工事は数えない（それで資材を作る国の工場が建たなくなった。
    # フェーズ3-2。民営の工事をまったく数えないと、国の工事が増えて公共事業費が足りなくなり、どの
    # 工事も止まった）
    sites = [
        f
        for f in island.facilities
        if not f.built and (f.owner == OWNER_STATE or not f.ext.get("waiting"))
    ]
    limit = MAX_SITES + (1 if _hireable(island, config) > BUSY_UNEMPLOYMENT else 0)
    if len(sites) >= limit + 1:
        return []
    # 公共事業費が足りずに資材か作業員を待っている国の工事があるあいだも、新しい工事は資材を作る施設
    # だけにする（始めると、どの工事も公共事業費を分け合って進まず、インフラの維持まで止まった。
    # フェーズ3-2）
    starved = _starved(island)
    if len(sites) >= limit or starved:  # 資材が手に入らずに止まっている工事のためだけに建てる
        candidates: Iterator[tuple[str, str]] = _material_producers(island, defs, config)
    else:
        candidates = _candidates(island, defs, config, turn)
    for kind, recipe in candidates:
        if _underused(island, defs, config, kind, recipe) or not _materials_ok(
            island, defs, config, kind
        ):
            continue
        pos = _site_for(island, defs, config, kind, recipe)
        if pos is not None:
            break
    else:
        return []
    payload: dict[str, Any] = {"facility": kind, "x": pos[0], "y": pos[1]}
    if len(defs.facilities[kind]["recipes"]) > 1:
        payload["recipe"] = recipe
    orders: list[tuple[str, dict]] = [("build", payload)]
    line = _power_line(island, defs, pos)
    if line:
        orders.append(("build_power_line", {"tiles": [list(p) for p in line[:64]]}))
    road = _road_order(island, defs, config, pos)
    if road is not None:
        orders.append(road)
    return orders


def _starved(island: IslandState) -> bool:
    """公共事業費が足りずに資材か作業員を待っている国の工事があるか。"""
    return any(
        not f.built
        and f.owner == OWNER_STATE
        and (f.ext.get("short_funds") or f.ext.get("short_wages"))
        for f in island.facilities
    )


def _hireable(island: IslandState, config: GameConfig) -> float:
    """新しい施設が雇える人の、働き手に対する割合: 失業率に、商業・サービス業の店で働く人の割合を
    足す（ほかの施設は賃金にかかわらず店から引き抜ける。回4。店ができる前は、仕事のない人が
    失業者として数えられていた）。"""
    stats = island.stats
    labor = stats.get("labor", 0)
    shop = services.facility_of(island, config)
    share = shop.workers / labor if shop is not None and labor > 0 else 0.0
    return stats.get("unemployment", 0) + share


def queue_orders(island: IslandState, orders: list[tuple[str, dict]]) -> None:
    """命令を島の命令の列の後ろに足す。"""
    next_id = max((o.id for o in island.orders), default=0) + 1
    next_seq = max((o.seq for o in island.orders), default=0) + 1
    for i, (kind, payload) in enumerate(orders):
        order = OrderState(id=next_id + i, seq=next_seq + i, kind=kind, payload=payload)
        island.orders.append(order)


# --- 国の倉庫 ---------------------------------------------------------------------


def _warehouse_sales(island: IslandState, defs: Defs, config: GameConfig) -> list[tuple[str, dict]]:
    """国の倉庫にある財のうち、島の施設が仕入れに困っている物を市場に売る。

    指値は今の値段（初めの値段より安ければ初めの値段）。

    建国のときに倉庫にあった石炭などを、火力発電所が使えるようにする（工事に取っておく分と、まだ誰も
    作っていない資材を作る施設を建てる分は売らない）。
    """
    if not island.has_economy:
        return []
    need: dict[str, int] = {}
    stuck: set[str] = set()
    for f in island.facilities:
        if f.built:
            for good, qty in fac.recipe_of(defs, f)["inputs"].items():
                need[good] = need.get(good, 0) + qty
            if fac.short_of_inputs(f.status):
                stuck.add(f.status.split(":", 1)[1])
    short = {m.good for m in island.markets if m.supply < m.demand}
    reserved = _reserved_materials(island, defs)
    for good, qty in _chain_keep(island, defs, config).items():
        reserved[good] = reserved.get(good, 0) + qty
    for good, qty in _military_keep(island, config).items():
        reserved[good] = reserved.get(good, 0) + qty
    initial = initial_prices(defs, config, island.resources)
    orders = []
    sold: dict[str, int] = {}
    for good, qty in sorted(need.items()):
        spare = stock.total(island.warehouse, good) - reserved.get(good, 0)
        if spare <= 0 or (good not in stuck and good not in short):
            continue
        sold[good] = min(spare, qty * SALE_TURNS)
    for good, qty in sorted(_private_materials(island, defs).items()):
        spare = stock.total(island.warehouse, good) - reserved.get(good, 0) - sold.get(good, 0)
        if spare > 0:
            sold[good] = sold.get(good, 0) + min(spare, qty)
    for good, qty in sorted(sold.items()):
        payload = {
            "good": good,
            "side": "sell",
            "quantity": qty / QUANTITY_SCALE,
            "limit": max(island.economy.prices.get(good, 0), initial.get(good, 0)),
        }
        orders.append(("warehouse_trade", payload))
    return orders


def _military_keep(island: IslandState, config: GameConfig) -> dict[str, int]:
    """国の倉庫に残しておく軍の分: 兵の食べ物（取り置きのターン数）と、国防費で決めた備蓄の目標。
    売ると、兵が飢えるか、外国へ送る補給がなくなる。"""
    from hakoniwa_core.military import forces
    from hakoniwa_core.modules import military

    if not forces.soldiers(island) and not island.ext.get(forces.KEY, {}).get("reserve"):
        return {}
    keep = dict(island.ext.get(forces.KEY, {}).get("reserve", {}))
    food = military.food_need(island, config) * config.get("military.food_stock_turns")
    keep[military.FOOD] = max(keep.get(military.FOOD, 0), food)
    return keep


def _private_materials(island: IslandState, defs: Defs) -> dict[str, int]:
    """民営工場が建てられる島で、島で作っていない工事の資材の、売りに出す量（民営の工事が待っている
    量と、民間が建てられる施設の1つの工事に要る量の大きい方）。民営の工事が待っていない資材は、直近
    OFFER_TURNS ターンに売りに出ていなければ出す（売りに出ていることが民間に見えれば足りるので、行政
    力を毎ターンは使わない）。"""
    amounts = _private_needs(island, defs)
    if not amounts:
        return {}
    made = {g for f in island.facilities if f.built for g in fac.recipe_of(defs, f)["outputs"]}
    waiting = _reserved_materials(island, defs, OWNER_PRIVATE)
    for good, qty in waiting.items():
        amounts[good] = max(amounts.get(good, 0), qty)
    offered = island.economy.finance.get("supply", {})
    return {
        g: q
        for g, q in amounts.items()
        if g not in made and (waiting.get(g, 0) > 0 or not any(offered.get(g, [])[-OFFER_TURNS:]))
    }


def _private_needs(island: IslandState, defs: Defs) -> dict[str, int]:
    """民営工場が建てられる島で、民間が建てられる施設の1つの工事に要る資材の量（資材ごとに一番
    多い施設の量）。民営工場が建てられなければ空。"""
    amounts: dict[str, int] = {}
    for kind, fdef in sorted(defs.facilities.items()):
        if private_allowed(defs, island, kind):
            for good, qty in fdef["construction"].get("materials", {}).items():
                amounts[good] = max(amounts.get(good, 0), qty)
    return amounts


# --- 公共事業費 -------------------------------------------------------------------


def _labor_policy_order(island: IslandState) -> tuple[str, dict] | None:
    """失業手当がなければ、平均賃金の BENEFIT % にする。

    失業者の暮らしと、物の売れ行きを支える。
    """
    if not island.has_economy or island.policy.unemployment_benefit > 0:
        return None
    return ("set_labor_policy", {"unemployment_benefit": BENEFIT})


def _budget_order(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> tuple[str, dict] | None:
    """公共事業費と教育費を見直す命令（どちらも変えるほどの差がなければ None）。"""
    if not island.has_economy or turn % BUDGET_TURNS:
        return None
    stats = island.stats
    scale = 1.0
    scale += UNEMPLOYMENT_RESPONSE * (stats.get("unemployment", 0) - NATURAL_UNEMPLOYMENT)
    scale -= INFLATION_RESPONSE * max(0.0, stats.get("inflation", 0) - INFLATION_LIMIT)
    cut = BURDEN_RESPONSE * max(0.0, stats.get("bonds.burden", 0) - FISCAL_LIMIT)
    scale -= cut
    scale = min(WORKS_RANGE[1], max(WORKS_RANGE[0], scale))
    # 国営の施設の設備を直す分（新品の設備が1年に傷む値打ち）を足す（回6。直す分は公共事業費から
    # 払うので、足さないと新しい工事の分が減る）
    prices = island.economy.prices
    repairs = int(
        sum(
            equipment.yearly_wear(defs, config, f.kind, prices) * fac.size_of(f)
            for f in island.facilities
            if f.owner == OWNER_STATE and equipment.has_equipment(defs, config, f)
        )
    )
    construction = int(population_of(island) * PUBLIC_WORKS * scale) + repairs
    # 始めた国の工事があるあいだは減らさず、公共事業費を待って止まっていて国庫にお金があれば、
    # 見直しのたびに増やす（始めた工事は終わらせる。上限は倍率の上限の額。回4 で人口の急な増え方が
    # なくなると、借金の対年GDP比が上がって公共事業費が下限まで下がり、国の工事が200ターン止まった
    # まま国庫にお金がたまった）
    current = island.policy.budget.get("construction", 0)
    if current and any(not f.built and f.owner == OWNER_STATE for f in island.facilities):
        top = int(population_of(island) * PUBLIC_WORKS * WORKS_RANGE[1]) + repairs
        construction = max(construction, min(current, top))
        if _starved(island) and stats.get("treasury", 0) > current // TURNS_PER_YEAR:
            construction = max(construction, min(top, int(current * (1 + STARVED_RAISE))))
    targets = {
        "construction": construction,
        "education": int(_education_cost(island, defs, config) * max(EDUCATION_FLOOR, 1 - cut)),
        "security": _security_cost(island, defs, config),
    }
    if pub.planned(island):  # 計画経済の国だけ、国が町の手入れと建て増しの資材を買う
        targets["housing"] = _housing_cost(island, defs, config)
    payload = {}
    for item, target in targets.items():
        current = island.policy.budget.get(item, 0)
        if not (current or target):
            continue
        if not current or abs(target - current) > current // 10:
            payload[item] = target
    return ("set_budget", payload) if payload else None


def _tax_order(island: IslandState, config: GameConfig, turn: int) -> tuple[str, dict] | None:
    """所得税と消費税を上げ下げする命令（変えなければ None）。"""
    if not island.has_economy or turn % BUDGET_TURNS != TAX_TURN:
        return None
    stats = island.stats
    burden = stats.get("bonds.burden", 0.0)
    unpaid = arrears.total(island, ledger.GOV) > 0
    policy = island.policy
    current = {key: getattr(policy, key) * 100 // BASIS for key in TAX_MAX}
    if unpaid:
        target = {key: min(top, current[key] + TAX_STEP) for key, top in TAX_MAX.items()}
    else:
        spending = sum(v for k, v in island.economy.spending.items() if k != "bonds")
        if burden >= FISCAL_LIMIT / 2 or stats.get("treasury", 0) < spending * SURPLUS_TURNS:
            return None
        start = {key: int(config.get(f"fiscal.{key}") * 100) for key in TAX_MAX}
        target = {key: max(start[key], current[key] - TAX_STEP) for key in TAX_MAX}
    payload = {key: value for key, value in target.items() if value != current[key]}
    return ("set_taxes", payload) if payload else None


def _food_orders(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    """飢饉の手の命令（ふだんの月の穀物の蓄えと入れ替えは、国の穀物の備蓄 economy/reserve.py が
    する）。"""
    ratio = island.stats.get("food.ratio")
    if not island.has_economy or ratio is None or GRAIN not in island.economy.prices:
        return []
    if ratio >= FAMINE_RATIO:
        return []
    need = to_milli(island.stats.get("food.need", 0))  # 1ターンに食べる量（穀物に直さない）
    price = island.economy.prices[GRAIN]
    orders: list[tuple[str, dict]] = []
    for f in island.facilities:
        recipe = _grain_recipe(defs, island, f)
        if recipe is not None:
            orders.append(("change_recipe", {"x": f.x, "y": f.y, "recipe": recipe}))
    keep = _military_keep(island, config)  # 兵の食べ物は売らない
    for good in sorted(island.warehouse):
        qty = stock.total(island.warehouse, good) - keep.get(good, 0)
        if qty > 0 and "food" in defs.goods.get(good, {}):
            orders.append(
                (
                    "warehouse_trade",
                    {"good": good, "side": "sell", "quantity": qty / QUANTITY_SCALE},
                )
            )
    buy = round(need * (1 - ratio))
    limit = int(price * EMERGENCY_PRICE)
    if buy > 0 and limit > 0:
        budget = money_of(limit, buy) * TURNS_PER_YEAR
        if island.policy.budget.get("warehouse", 0) < budget:
            orders.append(("set_budget", {"warehouse": budget}))
        payload = {"good": GRAIN, "side": "buy", "quantity": buy / QUANTITY_SCALE, "limit": limit}
        orders.append(("warehouse_trade", payload))
    return orders


def _grain_recipe(defs: Defs, island: IslandState, f: Any) -> str | None:
    """国営の農場で穀物以外を作っていれば、替える穀物の作り方（肥料を使っていれば肥料を使う方）。"""
    if not f.built or f.owner != OWNER_STATE:
        return None
    recipes = defs.facilities[f.kind]["recipes"]
    current = recipes.get(f.recipe, {})
    if GRAIN in current.get("outputs", {}) or not current.get("grow_turns"):
        return None
    grain = [
        name
        for name, r in recipes.items()
        if GRAIN in r["outputs"] and _recipe_ok(defs, island, r, frozenset())
    ]
    same = [name for name in grain if recipes[name]["inputs"] == current["inputs"]]
    return (same or grain or [None])[0]


def _education_cost(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """学校と大学（工事中を含む）を全力で動かす年の費用 × EDUCATION_MARGIN。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    band = 1 + config.get("market.band")
    per_turn = 0
    for f in island.facilities:
        if not defs.facilities[f.kind].get("education"):
            continue
        per_turn += fac.workers_of(defs, f) * wage
        for good, qty in fac.recipe_of(defs, f)["inputs"].items():
            per_turn += money_of(int(island.economy.prices.get(good, 0) * band), qty)
    return int(per_turn * TURNS_PER_YEAR * EDUCATION_MARGIN)


def _housing_cost(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """計画経済の国が住宅費に回す年の額（W-202）: 町の床が1年に傷む分の資材の値段 × HOUSING_MARGIN
    に、買いきれていない手入れの資材の値段を HOUSING_BACKLOG_YEARS 年で買う分と、次のターンに買う
    建て増しの資材の値段の1年分を足す。建国のときの額（住民1人あたり fiscal.budget_planned）より
    下げない。値段が上がっても資材が買える額になる（固定の額のままだと、値段が上がるうちに手入れの
    資材が買えず、町が傷みきって住めなくなった）。"""
    prices = island.economy.prices
    wear = config.get("housing")["wear"]
    kind = fac.housing_kind(config)
    upkeep = 0
    for f in island.facilities:
        if f.kind != kind:
            continue
        for good, qty in fac.housing_materials(defs, fac.floor_of(defs, f)).items():
            upkeep += money_of(prices.get(good, 0), int(qty * wear))
    held = pub.state(island)

    def worth(goods: dict[str, int]) -> int:
        return sum(money_of(prices.get(g, 0), q) for g, q in goods.items())

    cost = (
        upkeep * HOUSING_MARGIN
        + worth(held["repair"]) / HOUSING_BACKLOG_YEARS
        + worth(held["want"]) * TURNS_PER_YEAR
    )
    founding = config.get("fiscal.budget_planned").get("housing", 0) * population_of(island)
    return int(max(cost, founding))


def _security_cost(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """警察署（工事中を含む）を全力で動かす年の費用 × SECURITY_MARGIN。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    workers = sum(
        fac.workers_of(defs, f) for f in island.facilities if defs.facilities[f.kind].get("police")
    )
    return int(workers * wage * TURNS_PER_YEAR * SECURITY_MARGIN)


def _price_cap_order(
    island: IslandState, defs: Defs, config: GameConfig
) -> tuple[str, dict] | None:
    """電気料金が初めの値段の POWER_CAP 倍を超えたら、そこを公共料金の上限にする（1度だけ）。"""
    power = config.get("power.good")
    if not island.has_economy or power in island.policy.price_cap:
        return None
    initial = initial_prices(defs, config, island.resources).get(power, 0)
    if initial <= 0 or island.economy.prices.get(power, 0) <= initial * POWER_CAP:
        return None
    return ("set_price_cap", {"good": power, "price": int(initial * POWER_CAP)})


# --- 何を建てるか ---------------------------------------------------------------


def _underused(island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str) -> bool:
    """同じ物を作る施設に、動ききっていない（稼働率が UNDERUSED 未満の）ものがあるか。

    売れない・人手が足りないなど、同じ物を作る施設を増やしても役に立たないので建てない（工事が
    済んだばかりで、まだ動いていない施設も数える）。発電所はいつも使われる分だけ動くので見ない
    （足りるかは発電の余裕で決める）。投入財が手に入らずに止まっている施設も見ない（投入財の
    いらない作り方なら役に立つ）。
    """
    power = config.get("power.good")
    spec = defs.facilities[kind]["recipes"][recipe]
    outputs = set(spec["outputs"]) - {power}
    made = _made(island, defs)
    for f in island.facilities:
        if f.built and outputs & set(fac.recipe_of(defs, f)["outputs"]):
            if fac.short_of_inputs(f.status):
                # 島で作っていない（輸入に頼る）投入財を待っているなら、同じ投入財を使う施設を
                # 増やしても役に立たない（フェーズ3-2）
                short = f.status.split(":", 1)[1]
                if short not in made and short in spec["inputs"]:
                    return True
                continue
            if f.operating_rate < fac.FULL * UNDERUSED or f.status == fac.STATUS_STARTING:
                return True
    return False


def _flows(island: IslandState, defs: Defs, config: GameConfig) -> tuple[dict, dict]:
    """全力で動いたときの、財ごとの作る量と使う量（工事中の施設も含める）。

    投入財が手に入らずに止まっている施設と、投入財を島の中で作れず貿易でも手に入らない施設（石炭の
    ない島の火力発電所など。国の倉庫の分を使い切れば止まる）の作る量は数えない。発電所は、貿易で
    手に入る燃料を数えない。
    """
    supply: dict[str, int] = {}
    demand: dict[str, int] = {}
    power = config.get("power.good")
    traded = _traded(island, config)
    for f in island.facilities:
        if fac.is_housing(config, f):
            continue
        recipe = fac.recipe_of(defs, f)
        stuck = f.built and fac.short_of_inputs(f.status)
        imports = frozenset() if power in recipe["outputs"] else traded
        stuck = stuck or not _recipe_ok(defs, island, recipe, frozenset(), imports)
        water = nature.water_spec(recipe)
        # 水力発電所は、川の水の量で出る分（工事中も、場所の水で見込む。W-148）
        share = (
            nature.water_factor(island, defs, water, f.x, f.y)
            if water is not None and f.x >= 0
            else 1.0
        )
        for good, qty in recipe["outputs"].items():
            supply[good] = supply.get(good, 0) + (0 if stuck else round(qty * share))
        for good, qty in recipe["inputs"].items():
            demand[good] = demand.get(good, 0) + qty
    population = sum(t.population for t in island.tiles)
    use = config.get("power.household_use") * nature.household_power(defs, island)
    demand[power] = demand.get(power, 0) + to_milli(population * use)
    return supply, demand


def population_of(island: IslandState) -> int:
    return sum(t.population for t in island.tiles)


def _count(island: IslandState, kind: str) -> int:
    return sum(1 for f in island.facilities if f.kind == kind)


def _traded(island: IslandState, config: GameConfig) -> frozenset[str]:
    """貿易で手に入る財（フェーズ3-2）: 入っている貿易圏（加盟国が2つ以上）の前のターンの市場に
    売りに出ていた財と、商社が輸入している財。"""
    if not island.has_economy or not trade.started(island):
        return frozenset()
    goods = set(trade_traders.imported(island, config))
    for view in trade_traders.views(island).values():
        if view.get("members", 0) >= 2:
            goods |= set(view.get("supply", ()))
    return frozenset(goods)


def _bloc_short(island: IslandState) -> frozenset[str]:
    """入っている貿易圏（加盟国が2つ以上）の前のターンの市場で、買いたい量の一部しか買えなかった財
    （フェーズ3-2）。"""
    if not island.has_economy or not trade.started(island):
        return frozenset()
    goods: set[str] = set()
    for view in trade_traders.views(island).values():
        if view.get("members", 0) >= 2:
            goods |= set(view.get("short", ()))
    return frozenset(goods)


def _producers(
    defs: Defs, config: GameConfig, island: IslandState, good: str
) -> Iterator[tuple[str, str]]:
    """good を作れる（この島で建てて動かせる）施設と作り方を、今の値段で原価の安い順に。

    投入財は、島の中で作れるか、貿易で手に入れば使えると見る（フェーズ3-2。どの島も鉱業資源は
    2つまでなので、自動車のように島の中だけでは作れない物がある）。島の中で作れる作り方を先に
    並べる（輸入の少ない化学品を使う消費財工場ばかり建って、消費財が出なくなったため）。発電所は、
    島の中で作れる燃料だけで見る（石炭のない島に火力発電所を増やして、輸入の石炭の取り合いに
    ならないように）。
    """
    traded = frozenset() if good == config.get("power.good") else _traded(island, config)
    prices, wage = _prices_wage(island, defs, config)
    found = []
    for kind, fdef in defs.facilities.items():
        if fdef.get("buildable") is False:
            continue
        for rname, recipe in fdef["recipes"].items():
            if is_variant(rname, recipe):  # 標準の計画は電子機器・農場の段階を見ない
                continue
            if good in recipe["outputs"] and _feasible(defs, island, kind, rname, traded):
                inside = _feasible(defs, island, kind, rname)  # 輸入に頼らない作り方を先に
                cost = unit_cost(defs, config, kind, recipe, prices, wage)
                found.append((not inside, cost, kind, rname))
    for _, _, kind, rname in sorted(found):
        yield kind, rname


def _users(
    defs: Defs, config: GameConfig, island: IslandState, good: str
) -> Iterator[tuple[str, str]]:
    """good を投入財に使う施設と作り方のうち、島の中だけで動かせる物を、原価の安い順に（発電所は
    除く。電力は島の外に売れないので）。"""
    power = config.get("power.good")
    prices, wage = _prices_wage(island, defs, config)
    found = []
    for kind, fdef in defs.facilities.items():
        if fdef.get("buildable") is False:
            continue
        for rname, recipe in fdef["recipes"].items():
            if is_variant(rname, recipe):
                continue
            if (
                good in recipe["inputs"]
                and power not in recipe["outputs"]
                and _feasible(defs, island, kind, rname)
            ):
                found.append((unit_cost(defs, config, kind, recipe, prices, wage), kind, rname))
    for _, kind, rname in sorted(found):
        yield kind, rname


def _prices_wage(island: IslandState, defs: Defs, config: GameConfig) -> tuple[dict, int]:
    """原価の見込みに使う値段と賃金（建国の前は初めの値段）。"""
    if island.has_economy:
        return island.economy.prices, island.economy.average_wage
    return initial_prices(defs, config, island.resources), config.get("labor.initial_wage")


def _feasible(
    defs: Defs, island: IslandState, kind: str, rname: str, traded: frozenset[str] = frozenset()
) -> bool:
    """この島の資源と気候で、この作り方の投入財をどれも島の中で作れるか（元の原料までたどる）、
    traded の財（貿易で手に入る物）か。"""
    return _recipe_ok(defs, island, defs.facilities[kind]["recipes"][rname], frozenset(), traded)


def _recipe_ok(
    defs: Defs,
    island: IslandState,
    recipe: Any,
    seen: frozenset[str],
    traded: frozenset[str] = frozenset(),
) -> bool:
    resource = recipe.get("requires_resource")
    if resource is not None and resource not in island.resources:
        return False
    if not nature.usable(defs, island, recipe):  # 気候で使えない（フェーズ3-2）
        return False
    return all(_makeable(defs, island, good, seen, traded) for good in recipe["inputs"])


def _makeable(
    defs: Defs,
    island: IslandState,
    good: str,
    seen: frozenset[str],
    traded: frozenset[str] = frozenset(),
) -> bool:
    if good in traded:
        return True
    if good in seen:
        return False
    return any(
        _recipe_ok(defs, island, recipe, seen | {good}, traded)
        for fdef in defs.facilities.values()
        if fdef.get("buildable") is not False
        for recipe in fdef["recipes"].values()
        if good in recipe["outputs"]
    )


def _reserved_materials(
    island: IslandState, defs: Defs, owner: str = OWNER_STATE
) -> dict[str, int]:
    """持ち主が owner の工事中の施設が、これから使う資材のうち手元にない量（国の倉庫の資材を使える
    のは国の工事だけ。民営の工事は市場で買う）。"""
    reserved: dict[str, int] = {}
    for f in island.facilities:
        if not f.built and f.owner == owner:
            construction = defs.facilities[f.kind]["construction"]
            done = f.progress / fac.total_progress(defs, f)
            for good, qty in construction.get("materials", {}).items():
                left = qty - int(qty * done) - stock.total(f.stock, good)
                reserved[good] = reserved.get(good, 0) + max(0, left)
    return reserved


def _material_producers(
    island: IslandState, defs: Defs, config: GameConfig
) -> Iterator[tuple[str, str]]:
    """工事の資材のうち、国の倉庫に足りず、島に作る施設（工事中も含む）もない物を作る施設。

    作る施設があっても、どれも原料が手に入らずに止まっていて、その原料を作る施設がなければ、原料を
    作る施設（建材工場が石材を待っているのに採石場が閉まった島の採石場）。工事が資材を待って
    同時に進める工事の数がいっぱいでも、これは建てる（資材が出てこないまま工事が詰まるので）。
    """
    made = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    for good, qty in sorted(_reserved_materials(island, defs).items()):
        if stock.total(island.warehouse, good) >= qty:
            continue
        if good not in made:
            yield from _producers(defs, config, island, good)
            continue
        makers = [
            f for f in island.facilities if f.built and good in fac.recipe_of(defs, f)["outputs"]
        ]
        if makers and all(fac.short_of_inputs(f.status) for f in makers):
            short = {f.status.split(":", 1)[1] for f in makers}
            for need in sorted(short - made):
                yield from _producers(defs, config, island, need)


def _materials_ok(island: IslandState, defs: Defs, config: GameConfig, kind: str) -> bool:
    """工事の資材が、倉庫にあるか（ほかの工事の残りの分と、連鎖を始める分を除いて）、島の中で作れるか。

    ほかの工事が待っている資材を建った施設がどれも作っていなければ、島で作れるはずでも、その資材を
    作る施設（を建てる工事）でない限り建てない。製鉄所と機械工場の工事は互いの鋼鉄と機械を待つので、
    初めの倉庫の分をほかの工事に使うと、連鎖がいつまでも始まらない。同じわけで、まだ誰も作っていない
    資材を作る施設を建てる分（_chain_keep）も、その施設と発電所のほかには使わない（電気はどの施設も
    使うので、発電所は先にする）。
    """
    reserved = _reserved_materials(island, defs)
    made = _made(island, defs)
    outputs = {g for r in defs.facilities[kind]["recipes"].values() for g in r["outputs"]}
    first = _missing_materials(island, defs) | {config.get("power.good")}
    keep = {} if outputs & first else _chain_keep(island, defs, config)
    short = {g for g, qty in reserved.items() if stock.total(island.warehouse, g) < qty}
    helps = bool(outputs & short)
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0) - keep.get(good, 0)
        if have >= qty or good in made:
            continue
        if keep.get(good, 0) > 0 or (reserved.get(good, 0) > 0 and not helps):
            return False
        if not _makeable(defs, island, good, frozenset(), _traded(island, config)):
            return False
    return True


def _missing_materials(island: IslandState, defs: Defs) -> set[str]:
    """工事の資材のうち、島のどの施設（工事中を含む）も作らない物。"""
    present = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    needed = {
        g for fdef in defs.facilities.values() for g in fdef["construction"].get("materials", {})
    }
    return needed - present


def _chain_keep(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """国の倉庫に残しておく資材: 工事の資材のうち島のどの施設（工事中を含む）も作らない物ごとに、
    それを作る施設を1つ建てる分の資材のうち、建った施設が作っていない物の量（製鉄所の機械と機械工場の
    鋼鉄）。島で作れない物は残さない。"""
    made = _made(island, defs)
    keep: dict[str, int] = {}
    for good in sorted(_missing_materials(island, defs)):
        for kind, _ in _producers(defs, config, island, good):
            for g, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
                if g not in made:
                    keep[g] = max(keep.get(g, 0), qty)
            break
    return keep


def _made(island: IslandState, defs: Defs) -> set[str]:
    """建った施設が作っている財。"""
    return {
        good for f in island.facilities if f.built for good in fac.recipe_of(defs, f)["outputs"]
    }


def _candidates(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> Iterator[tuple[str, str]]:
    """建てたい施設と作り方を、建てたい順に。"""
    supply, demand = _flows(island, defs, config)
    power = config.get("power.good")
    stats = island.stats

    food_supply = sum(
        qty * defs.goods[g]["food"]["nutrition"]
        for g, qty in supply.items()
        if "food" in defs.goods[g]
    )
    food_supply -= sum(qty for g, qty in demand.items() if "food" in defs.goods[g])
    if food_supply < to_milli(stats.get("food.need", 0)) * FOOD_MARGIN:
        if _count(island, "fishing_port") * 2 < _count(island, "farm"):
            yield ("fishing_port", "default")
        yield ("farm", "default")

    if supply.get(power, 0) < demand.get(power, 0) * POWER_MARGIN:
        yield from _producers(defs, config, island, power)

    yield from _material_producers(island, defs, config)

    yield from _education(island, defs, config)

    yield from _security(island, defs, config, turn)

    def spare(kind: str, rname: str) -> bool:
        """投入財に余裕がある（作る量が、今使う量とこの施設の使う量に届く）か。

        余裕がなければ建てない（投入財の取り合いで値段が上がるだけなので。投入財を作る施設は
        先に候補に挙がる）。まだ島で作っていない投入財は見ない（使う施設が先にできて、足りない
        投入財として作る施設が候補に挙がる）。電力は発電の余裕で別に決める。
        """
        recipe = defs.facilities[kind]["recipes"][rname]
        return all(
            supply.get(good, 0) == 0 or supply[good] >= demand.get(good, 0) + qty
            for good, qty in recipe["inputs"].items()
            if good != power
        )

    making = {g for f in island.facilities for g in fac.recipe_of(defs, f)["outputs"]}
    traded = _traded(island, config)
    at_hand = making | traded
    for good in sorted(demand, key=lambda g: (defs.goods[g].get("tier", 9), g)):
        if good != power and supply.get(good, 0) < demand[good]:
            # 島のどこでも作っておらず貿易でも手に入らない投入財（組み立て工場の部品など）は、作る
            # 施設の投入財が手元にあれば、余裕がなくても1つ目を建てる（使う施設だけ建って止まらない
            # ように。回7 後半）
            first = good not in at_hand
            yield from (
                c
                for c in _producers(defs, config, island, good)
                if spare(*c) or (first and _inputs_at_hand(defs, config, *c, at_hand))
            )

    markets = {m.good: m for m in island.markets}
    people = population_of(island)
    # 島で作っている投入財と工事の資材も、前のターンの市場で売りに出た量が買いたい量に届かず、値段が
    # 初めの PRICE_HIGH 倍を超えていれば、作る施設を増やす（工事の資材の使う量は上の流れに数えない
    # ため。フェーズ3-2: 消費財工場が鋼鉄を使う作り方に替わって木材の施設が増えず、工事の木材で
    # 値段が初めの4倍を超えた）
    initial = initial_prices(defs, config, island.resources)
    for good in sorted(markets, key=lambda g: (defs.goods[g].get("tier", 9), g)):
        record = markets[good]
        if (
            good != power
            and good in making
            and not defs.goods[good].get("consumer")
            and record.supply < record.demand
            and record.price > initial.get(good, 0) * PRICE_HIGH
        ):
            yield from (c for c in _producers(defs, config, island, good) if spare(*c))

    # 海外の商人が持ってくる量を減らし始めたら（foreign_merchant.taper_from ターンを過ぎてから）、
    # 前のターンに商人から買った物のうち島で作っていない物を作る施設を建てる。投入財も商人から買って
    # いれば、その投入財を作る施設が先に建つ（1つの島の 300 ターンの試験: 商人が帰ってから機械を
    # 作る施設が建つまで機械が島から消え、値段が初めの4倍を超えた）
    if 0 < trade_merchant.taper(island, config, turn) < 1.0:
        bought = {t.good for t in island.trades if t.seller == MERCHANT}
        for good in sorted(bought - making, key=lambda g: (defs.goods[g].get("tier", 9), g)):
            yield from (
                c
                for c in _producers(defs, config, island, good)
                if _inputs_at_hand(defs, config, *c, making)
            )

    # 作り方の投入財が手元にあるか。部品のように島で作っていなくても、作る施設の投入財が手元にある
    # 物は手に入ると見る（組み立て工場と部品工場を組で建てる。回7 後半: 自動車工場が部品工場を待ち、
    # 部品工場が自動車工場を待って、どちらも建たなかった）
    def ready(kind: str, rname: str) -> bool:
        inputs = defs.facilities[kind]["recipes"][rname]["inputs"]
        return all(
            good in at_hand
            or (
                not defs.goods[good].get("consumer")
                and any(
                    _inputs_at_hand(defs, config, *c, at_hand)
                    for c in _producers(defs, config, island, good)
                )
            )
            for good in inputs
            if good != power
        )

    for good in _consumer_order(defs, config):
        if good != power:
            record = markets.get(good)
            short = record is not None and record.supply < record.demand * MARKET_SHORT
            # 1人あたりの目安（衣料・消費財・嗜好品。household.targets。フェーズ3-2）
            target = people * needs.target_of(config, good)
            few = target > 0 and supply.get(good, 0) < to_milli(target * CONSUMER_MARGIN)
            if short or few:
                # 島でまだ作っていない物は、投入財に余裕がなくても1つ目を建てる（投入財の工場は、
                # 足りない投入財として後から候補に挙がる。フェーズ3-2 の自動車と衣料）
                first = good not in making
                # 自動車は、投入財（鋼鉄など）を島で作っているか貿易で手に入る島だけ（鋼鉄のない島に
                # 建てると、工事の鋼鉄と機械を作る施設まで国が建て始め、公共事業費が足りずにどの
                # 工事も止まった。フェーズ3-2）
                durable = defs.goods[good].get("need") == needs.DURABLE
                yield from (
                    c
                    for c in _producers(defs, config, island, good)
                    if (first or spare(*c)) and (not durable or ready(*c))
                )

    # 海運業が輸送船を買えずにいて、島に造船所がなければ、造船所を1つ（回6。どの世界も造船所を
    # 建てず、初めに配った船が寿命になると貿易が止まった）
    ship = config.get("trade.ship_good")
    record = markets.get(ship)
    if record is not None and record.supply < record.demand and _count(island, "shipyard") == 0:
        yield from (
            c
            for c in _producers(defs, config, island, ship)
            if _inputs_at_hand(defs, config, *c, at_hand)
        )

    # 人手の足りない施設が多いか、雇える人（失業者と店で働く人）が少ないうちは増やさない
    # （住宅が育って人が移ってくるのを待つ）
    power = config.get("power.good")
    missing = sum(
        max(0, fac.labor_demand(defs, f, turn, power) - f.workers)
        for f in island.facilities
        if not services.is_services(config, f)
    )
    too_few = _hireable(island, config) < NATURAL_UNEMPLOYMENT
    if missing > MAX_MISSING_WORKERS * scale.people(config) or too_few:
        return
    # 島の鉱業資源を、島のどの施設（工事中を含む）も使っていなければ、使う施設を1つ（輸出向け）
    used = {g for f in island.facilities for g in fac.recipe_of(defs, f)["inputs"]}
    # 鉱脈の資源（銅など。world.veins）は、買い手が軍だけの間は掘らない（設計書 MI-44。掘ると
    # 買い手のいない精錬所と鉱山が建ち、鉱山の上限 2 を使って鉄鉱石の鉱山が建たなくなる）
    veins = set(config.get("world.veins"))
    for resource in sorted(set(island.resources) - used - veins):
        yield from (c for c in _users(defs, config, island, resource) if spare(*c))
    # 貿易圏で買いたい人が残った物を、島の中だけで作れるなら作る（輸出向け）
    short = _bloc_short(island) - {power}
    for good in sorted(short, key=lambda g: (defs.goods[g].get("tier", 9), g)):
        yield from (
            c
            for c in _producers(defs, config, island, good)
            if _feasible(defs, island, *c) and spare(*c)
        )
    for kind in sorted(GROWTH, key=lambda k: (_count(island, k), GROWTH.index(k))):
        for rname in defs.facilities[kind]["recipes"]:
            if _feasible(defs, island, kind, rname) and spare(kind, rname):
                yield (kind, rname)
                break


def _inputs_at_hand(
    defs: Defs, config: GameConfig, kind: str, rname: str, goods: set[str] | frozenset[str]
) -> bool:
    """作り方の投入財（電力を除く）が、どれも goods（島で作っている物と、貿易で手に入る物）に
    あるか。"""
    power = config.get("power.good")
    inputs = defs.facilities[kind]["recipes"][rname]["inputs"]
    return all(g in goods for g in inputs if g != power)


def _consumer_order(defs: Defs, config: GameConfig) -> list[str]:
    """家計の買う物を、作る施設を建てたい順に: 必需品、ぜいたく品（household.luxury_weights の重い
    順）、自動車、ほか（燃料）。名前の順だと自動車工場が消費財工場より先に建ち、鋼鉄と機械を
    待つ高い工事に公共事業費が回って、消費財が足りないままになった（フェーズ3-2）。"""
    rank = {needs.NECESSITY: 0, needs.LUXURY: 1, needs.DURABLE: 2}
    goods = [g for g, d in defs.goods.items() if d.get("consumer")]
    return sorted(
        goods,
        key=lambda g: (rank.get(defs.goods[g].get("need"), 3), -needs.luxury_weight(config, g), g),
    )


def _education(island: IslandState, defs: Defs, config: GameConfig) -> Iterator[tuple[str, str]]:
    """学校の定員が足りなければ学校、技術者が足りなくて大学がなければ大学（お金の経済の島だけ）。

    同じ資材を待っている工事があるあいだは建てない（学校と大学が建材を使い、原料や資材の工場の工事が
    後回しになって、消費財と木材が足りなくなったため）。工事の資材が倉庫に足りず、島のどの施設も
    作っていなければ、先にその資材を作る施設を建てる（建材を作らない島に大学が建たず、技術者が引退で
    減り続けて、工場が6割ほどしか動かなくなったため）。
    """
    if not island.has_economy:
        return
    if SCHOOL in defs.facilities:
        seats = sum(
            defs.facilities[f.kind].get("education", {}).get("capacity", 0) * fac.size_of(f)
            for f in island.facilities
        )
        children = population_of(island) * config.get("education.children")
        if seats < children * SCHOOL_TARGET:
            yield from _build_or_supply(island, defs, config, SCHOOL)
    if UNIVERSITY in defs.facilities and _count(island, UNIVERSITY) == 0:
        needed = sum(fac.engineers_of(defs, f) for f in island.facilities)
        if needed > island.economy.workers.get(fac.ENGINEER, 0) * ENGINEER_TARGET:
            keep = _private_needs(island, defs)
            yield from _build_or_supply(island, defs, config, UNIVERSITY, keep)


def _security(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> Iterator[tuple[str, str]]:
    """警察署を1つ（SECURITY_FROM ターンから）。工事の資材の扱いは学校と同じ（資材が倉庫に足りず
    島で作っていなければ、先にその資材を作る施設）。

    平時に兵舎と武器工場は建てない。武器工場は武器の原料（鉄鋼・機械・化学品）を平時から食い、
    建てた島の鉄鋼と機械が品切れになった（段6 A2 の測り）。兵は反乱の前触れが続くか宣戦されたとき
    plan_war が集める。
    """
    if not island.has_economy or turn < SECURITY_FROM:
        return
    if POLICE in defs.facilities and _count(island, POLICE) == 0:
        yield from _build_or_supply(island, defs, config, POLICE)


def _build_or_supply(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    kind: str,
    keep: dict[str, int] | None = None,
) -> Iterator[tuple[str, str]]:
    """kind を建てる。資材が倉庫に足りず島のどの施設（工事中を含む）も作っていなければ、代わりに
    その資材を作る施設。同じ資材を待っている工事があるか、資材が手元にない（_in_hand）なら建てない。"""
    present = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    lacking = [
        good
        for good, qty in sorted(defs.facilities[kind]["construction"].get("materials", {}).items())
        if good not in present and stock.total(island.warehouse, good) < qty
    ]
    if lacking:
        for good in lacking:
            yield from _producers(defs, config, island, good)
    elif not public_works.materials_waited(island, _materials_of(defs, kind)) and _in_hand(
        island, defs, kind, keep
    ):
        yield (kind, "default")


def _materials_of(defs: Defs, kind: str) -> set[str]:
    return set(defs.facilities[kind]["construction"].get("materials", {}))


def _in_hand(
    island: IslandState, defs: Defs, kind: str, keep: dict[str, int] | None = None
) -> bool:
    """工事の資材が、国の倉庫にある（ほかの工事の残りの分と keep を除いて）か、建った施設が島で
    作っているか。

    島で作れるだけで建てると、初めの倉庫の資材を学校や大学が使い、資材を作る施設や、島で作れない
    資材（機械など）を待つ民営工場の工事が止まる。
    """
    reserved = _reserved_materials(island, defs)
    made = _made(island, defs)
    keep = keep or {}
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0) - keep.get(good, 0)
        if have < qty and good not in made:
            return False
    return True


# --- インフラ ---------------------------------------------------------------------


def _road_order(
    island: IslandState, defs: Defs, config: GameConfig, pos: tuple[int, int]
) -> tuple[str, dict] | None:
    """pos とその周りの陸のマスのうち、交通の整備度が届かないマスに道路を引く（選び方は役所の
    決まった事業と同じ。modules/infrastructure.road_tiles）。"""
    kinds = defs.infra_kinds("transport")
    if not kinds or not island.has_economy:
        return None
    tiles = public_works.road_tiles(island, defs, config, pos)
    if not tiles:
        return None
    return ("build_infrastructure", {"kind": kinds[0], "tiles": [list(t) for t in tiles]})


def _water_order(island: IslandState, defs: Defs, config: GameConfig) -> tuple[str, dict] | None:
    """上下水道の足りない町のマスに、上下水道を引く（選び方は役所の決まった事業と同じ。
    modules/infrastructure.water_tiles）。"""
    kinds = defs.infra_kinds("water")
    if not kinds or not island.has_economy:
        return None
    tiles = public_works.water_tiles(island, defs, config)
    if not tiles:
        return None
    return ("build_infrastructure", {"kind": kinds[0], "tiles": [list(t) for t in tiles[:64]]})


# --- 工業団地 ---------------------------------------------------------------------


def _zone_order(island: IslandState, defs: Defs, config: GameConfig) -> tuple[str, dict] | None:
    """民営工場が建てられる島で、空いた工業団地が少なければ、町の通勤圏に工業団地を足す。"""
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    if not island.has_economy or regime.get("private_entry", "none") == "none":
        return None
    capital = island.capital
    if capital is None:
        return None
    occupied = island.facilities_by_tile()
    spare = [t for t in island.tiles if t.zone == ZONE_INDUSTRIAL and (t.x, t.y) not in occupied]
    if len(spare) >= ZONE_SPARE:
        return None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    if not homes:
        return None
    radius = config.get("labor.commute_radius")
    terrains = {"plain"} | ({"mountain"} if island.resources else set())
    picks = []
    for t in island.tiles:
        pos = (t.x, t.y)
        if t.zone != ZONE_NONE or pos in occupied or pos == capital or t.terrain not in terrains:
            continue
        near = min(distance(pos, h) for h in homes)
        if near <= 1 or near > radius:
            continue
        picks.append(((near, distance(pos, capital)), t.terrain, pos))
    picks.sort()
    has_mountain = any(t.terrain == "mountain" for t in spare)
    mountains = [] if has_mountain else [p for _, k, p in picks if k == "mountain"][:1]
    plains = [p for _, k, p in picks if k == "plain"]
    tiles = [list(p) for p in mountains + plains[: ZONE_BATCH - len(mountains)]]
    if not tiles:
        return None
    return ("set_zone", {"zone": "industrial", "tiles": tiles})


def _commercial_order(
    island: IslandState, defs: Defs, config: GameConfig
) -> tuple[str, dict] | None:
    """都市（settlements.yaml の city）以上に育った町があり、空いた商業地のマスがなければ、いちばん
    人の多い町の隣の空いた平地を1マス商業地に指定する（百貨店が建つ所。回7）。"""
    if not island.has_economy or "department_store" not in defs.facilities:
        return None
    occupied = island.facilities_by_tile()
    if any(t.zone == ZONE_COMMERCIAL and (t.x, t.y) not in occupied for t in island.tiles):
        return None
    housing = fac.housing_kind(config)
    city = defs.settlements.get("city", {}).get("min_population", 0)
    towns = sorted(
        (f for f in island.facilities if f.kind == housing and fac.on_map(f)),
        key=lambda f: (-island.tile(f.x, f.y).population, f.y, f.x),
    )
    if not towns or island.tile(towns[0].x, towns[0].y).population < city:
        return None
    big = (towns[0].x, towns[0].y)
    for pos in within(island, big, 2):
        t = island.tile(*pos)
        empty = pos not in occupied and t.population == 0
        if t.zone == ZONE_NONE and empty and t.terrain == "plain":
            return ("set_zone", {"zone": "commercial", "tiles": [list(pos)]})
    return None


# --- どこに建てるか ---------------------------------------------------------------


def _site_for(
    island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str
) -> tuple[int, int] | None:
    """住宅の通勤圏の中で首都に近いマス。開発を止めたマスと、住宅の育つ余地はなるべく避ける。
    通勤圏の中に同じ作り方の国営の同じ施設があれば、新しいマスより先にそれを建て増す（土地を
    詰めて使う。段6 A3）。"""
    capital = island.capital
    if capital is None:
        return None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    radius = fac.commute_radius(defs, config, kind)
    spec = defs.facilities[kind]["recipes"][recipe]
    fertile = bool(spec.get("factors", {}).get("fertility"))
    water = nature.water_spec(spec)
    best, best_key = None, None
    for pos in within(island, capital, island.size):
        if island.tile(*pos).zone != ZONE_NONE:
            continue
        if fac.placement_problem(defs, island, kind, *pos, recipe, expand=True) is not None:
            continue
        target = fac.expansion_target(island, kind, *pos)
        if target is not None and fac.grade_steps(defs, kind):
            continue  # 農場の段階を上げる工事は、場所選びでなく _grade_order が決める（FM-52）
        if target is not None and (
            target.recipe != recipe or not _labor_for_expansion(island, defs, kind, pos, radius)
        ):
            continue
        # 水力発電所は、水が WATER_MIN 未満の場所には建てず、水の多い場所を先に（0.1 刻み。W-148）
        wet = 0
        if water is not None:
            level = nature.water_factor(island, defs, water, pos[0], pos[1], weather=False)
            if level < WATER_MIN:
                continue
            wet = -int(level * 10)
        near = min((distance(pos, h) for h in homes), default=island.size)
        # 肥沃度の効く作り方（農場と牧場）は、肥沃なマスを先に（0.1 刻み。フェーズ3-2）
        rich = -nature.fertility(island.tile(*pos)) // 100 if fertile else 0
        new = target is None
        # 住宅の隣は、建て増しでも避ける（汚染は規模の分だけ増える）。その次に建て増しを先に
        key = (max(0, near - radius), near <= 1, new, wet, rich, distance(pos, capital))
        if best_key is None or key < best_key:
            best, best_key = pos, key
    return best


# 国営の農場の段階（W-190。FM-52）: 機械化は、失業率が自然失業率のこの割合を下回り、国の施設で
# 雇いきれない人数が働き手のこの割合を超えているとき（人手が足りない）。近代化は、機械化した農場が
# あり、食料の足りない国で新しい農場の平地もないとき
GRADE_SCARCE = 0.5
GRADE_UNFILLED = 0.02


def _grade_order(island: IslandState, defs: Defs, config: GameConfig) -> tuple[str, dict] | None:
    """国営の農場の段階を 1 つ上げる命令（FM-52）。段階の工事は 1 島に 1 つずつ。上げる先の資材が
    倉庫にも島の施設の産出にもなければ上げない。"""
    if not island.has_economy:
        return None
    farms = [
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if fac.grade_steps(defs, f.kind) and f.built and f.owner == OWNER_STATE and fac.on_map(f)
    ]
    if not farms or any(fac.pending_expansions(island, f) for f in farms):
        return None
    made = _made(island, defs)
    stats = island.stats
    labor = stats.get("labor", 0)
    unfilled = sum(
        max(0, fac.workers_of(defs, f) - f.workers) for f in island.facilities if f.built
    )
    scarce = (
        labor > 0
        and stats.get("unemployment", 1.0) < config.get("labor.natural_unemployment") * GRADE_SCARCE
        and unfilled > labor * GRADE_UNFILLED
    )
    hungry = (
        stats.get("food.ratio", 1.0) < 1.0
        and _site_for(island, defs, config, "farm", "default") is None
    )
    for f in farms:
        if fac.grade_problem(defs, island, f) is not None:
            continue
        grade = fac.grade_of(f)
        step = fac.grade_steps(defs, f.kind)[grade]
        if not (scarce if grade == 0 else hungry):
            continue
        if any(
            stock.total(island.warehouse, g) < q and g not in made
            for g, q in step["materials"].items()
        ):
            continue
        # 機械化は燃料が島で作られるか国の倉庫にあるときだけ（燃料のない島の機械は手作業のまま働く）
        if grade == 0 and fac.FUEL not in made and stock.total(island.warehouse, fac.FUEL) <= 0:
            continue
        return ("build", {"facility": f.kind, "x": f.x, "y": f.y})
    return None


def _labor_for_expansion(
    island: IslandState, defs: Defs, kind: str, pos: tuple[int, int], radius: int
) -> bool:
    """建て増す施設の通勤圏に、規模 1 つ分の働き手の半分以上の雇われていない人がいるか（段6 A3）。
    いなければ建て増しても雇えずに動かない（_flows は全力で動く見込みで数えるので、通勤圏の人手が
    尽きた施設を建て増し続けて、投入財が品切れになった。測りの戦争の場面）。新しいマスに建てる。"""
    idle = 0
    for x, y in within(island, pos, radius):
        t = island.tile(x, y)
        if t.population:
            idle += max(0, fac.labor_force(defs, island, t) - t.ext.get("employed", 0))
    return idle * 2 >= defs.facilities[kind]["workers"]


def _power_line(island: IslandState, defs: Defs, pos: tuple[int, int]) -> list[tuple[int, int]]:
    """pos を首都の送電網につなぐ送電線のマス（施設のあるマスを除く）。"""
    capital = island.capital
    if capital is None:
        return []
    nodes = {(f.x, f.y) for f in island.facilities} | {pos}
    nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
    grid = next((set(g) for g in components(island, nodes) if capital in g), {capital})
    if pos in grid:
        return []
    occupied = island.facilities_by_tile()
    path = shortest_path(island, grid, pos, lambda p: fac.is_land(defs, island.tile(*p).terrain))
    if path is None:
        return []
    return [p for p in path if p not in occupied and p != pos]


# --- 貿易（フェーズ3-1） ---------------------------------------------------------------

BLOC_NAMES = ("北の貿易圏", "南の貿易圏")


def plan_trade(
    world: WorldState, defs: Defs, config: GameConfig
) -> dict[int, list[tuple[str, dict]]]:
    """貿易圏の標準の計画（島ごとの命令）。島ごとの plan_turn の後に、世界を見て並べる。

    貿易の始まった島を世界マップの縦の位置で北と南に分け（真ん中の行の島は両方）、どちらにも圏が
    なければ、その側で人口の一番多い島が誰でも入れる圏を作ります（決済通貨はその島の通貨）。圏が
    できたら、その側の島で入っていないものが加盟します。契約は結びません。
    """
    ready = [
        i
        for i in world.island_ids()
        if world.islands[i].has_economy and trade.started(world.islands[i])
    ]
    if len(ready) < 2:
        return {}
    rows = sorted(world.islands[i].wy for i in ready)
    middle = rows[len(rows) // 2]
    sides = (
        [i for i in ready if world.islands[i].wy <= middle],
        [i for i in ready if world.islands[i].wy >= middle],
    )
    existing = {b["name"]: b for b in trade_blocs.blocs(world)}
    waiting = {  # 前の加盟の命令がまだ済んでいない島（行政力が足りず繰り越したときなど）
        i
        for i in ready
        if any(o.kind == "join_bloc" and o.status == ORDER_PENDING for o in world.islands[i].orders)
    }
    plans: dict[int, list[tuple[str, dict]]] = {}
    for name, members in zip(BLOC_NAMES, sides, strict=True):
        if len(members) < 2:
            continue
        bloc = existing.get(name)
        if bloc is None:
            if any(
                o.kind == "create_bloc"
                for i in members
                for o in world.islands[i].orders
                if o.status == ORDER_PENDING
            ):
                continue
            founder = max(members, key=lambda i: (population_of(world.islands[i]), -i))
            plans.setdefault(founder, []).append(("create_bloc", {"name": name, "open": True}))
            continue
        for i in members:
            if i in waiting:
                continue
            if i not in bloc["members"] and i not in bloc["applicants"]:
                plans.setdefault(i, []).append(("join_bloc", {"bloc": bloc["id"]}))
    return plans


# --- 戦争（フェーズ4-3。設計書 MI-44） --------------------------------

DEFENSE_SHARE = 0.005  # 宣戦されたとき（反乱の前触れが続くときも）、住民のこの割合を兵の目標にする
DEFENSE_MIN = 1  # 兵の目標の下限（歩兵の部隊の数。兵の数は army.yaml の infantry の soldiers）
WAR_ORDERS = ("sign_treaty", "set_defense", "recruit", "form_unit")


def plan_war(
    world: WorldState, defs: Defs, config: GameConfig
) -> dict[int, list[tuple[str, dict]]]:
    """宣戦されたときの標準の計画（島ごとの命令）。標準の計画は自分から宣戦せず、核施設も建てない。

    戦争の相手がいる島と、内戦の起きている島（反乱の前触れが続く島を含む）は、1 ターンに 1 つずつ
    次を進めます: 相手が出した停戦と講和（戦争のあいだに結べる条項だけの条約）に署名する → 兵舎が
    なければ建てる → 国防費を決める → 兵を集める → 兵舎に歩兵を編成する。戦争も反乱の前触れもない島
    は何もしません（平時の守りは警察署。平時の兵は置かない）。
    サーバーの持ち主のいない島はこの計画を使いません（測りと試験だけで動く）。
    """
    from hakoniwa_core.diplomacy import clauses as cl
    from hakoniwa_core.diplomacy import treaties, war
    from hakoniwa_core.military import civil_war

    plans: dict[int, list[tuple[str, dict]]] = {}
    for i in world.island_ids():
        island = world.islands[i]
        if not island.has_economy:
            continue
        threatened = bool(
            war.open_wars_of(world, i)
            or civil_war.alarmed(world, island, config.get("military.civil_war.trigger_turns"))
        )
        if not threatened:
            continue  # 反乱の前触れが続く島と内戦の島も備える（MI-40）
        if any(o.status == ORDER_PENDING and o.kind in WAR_ORDERS for o in island.orders):
            continue  # 前の手がまだ済んでいない（行政力が足りず繰り越したときなど）
        for t in treaties.treaties(world):
            if (
                threatened
                and t["state"] == treaties.PROPOSED
                and i in t["parties"]
                and t["proposer"] != i
                and str(i) not in t["signed"]
                and set(treaties.kinds(t)) <= set(cl.WARTIME)
            ):
                plans[i] = [("sign_treaty", {"treaty": t["id"]})]
                break
        else:
            floor = DEFENSE_MIN * defs.army["infantry"]["soldiers"]
            target = max(floor, int(population_of(island) * DEFENSE_SHARE))
            order = defense_order(island, world, defs, config, target)
            if order is not None:
                plans[i] = [order]
    return plans


def fed(island: IslandState, defs: Defs, config: GameConfig) -> bool:
    """兵が食べる物（food）を、島で作っているか貿易で手に入れられるか。兵は food しか食べないので、
    ない島に兵を置くと、飢えて士気が落ち、逃げ出す（4-2 の決まり。台帳 W-174 で広げるか見る）。
    測りの戦争の場面が攻める側を選ぶのに使う。"""
    from hakoniwa_core.modules import military

    return military.FOOD in _made(island, defs) or military.FOOD in _traded(island, config)


def defense_order(
    island: IslandState, world: WorldState, defs: Defs, config: GameConfig, target: int
) -> tuple[str, dict] | None:
    """兵を target 人まで備える次の 1 手: 兵舎を建てる → 国防費を決める → 兵を集める → 歩兵を編成
    する。測りの戦争の場面（warscene）の攻める側も、これを使う。"""
    from hakoniwa_core.military import army, forces

    barracks = [f for f in island.facilities if f.kind == BARRACKS]
    ready = [f for f in barracks if f.built]
    if not barracks:
        if any(
            o.kind == "build"
            and o.status == ORDER_PENDING
            and o.payload.get("facility") == BARRACKS
            for o in island.orders
        ):
            return None  # 標準の計画が同じターンに建てる
        pos = _site_for(island, defs, config, BARRACKS, "default")
        if pos is None:
            return None
        return ("build", {"facility": BARRACKS, "x": pos[0], "y": pos[1]})
    if any(o.kind == "build" and o.status == ORDER_PENDING for o in island.orders):
        return None
    budget = island.policy.budget
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    # 兵の賃金と食べ物に、兵の年の賃金の 2.8 倍（食べ物の買い注文は指値の 2 倍の値段で取り置くので、
    # 賃金のほかに賃金と同じほどの額が要る）、装備の買い入れに 1.2 倍
    annual = wage * target * 12 * 4
    set_now = budget.get(forces.BUDGET_PERSONNEL, 0) + budget.get(forces.BUDGET_PROCUREMENT, 0)
    if set_now * 4 < annual * 3:  # 目標が増えた（宣戦された）か、賃金が上がって足りなくなった
        return ("set_defense", {"annual": annual, "personnel_share": 70})
    soldiers = forces.soldiers(island)
    if soldiers < target:
        return ("recruit", {"count": target - soldiers})
    units = army.land_units_of(world, island.id)
    free = soldiers - sum(u["soldiers"] for u in units)
    spec = defs.army["infantry"]
    for f in ready:
        if free >= spec["soldiers"] and army.housed(world, defs, island.id, f.x, f.y) + spec[
            "soldiers"
        ] <= army.barracks_at(island, defs, f.x, f.y):
            return ("form_unit", {"army": "infantry", "x": f.x, "y": f.y})
    return None
