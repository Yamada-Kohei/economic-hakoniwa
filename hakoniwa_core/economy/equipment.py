"""設備の傷みと直し（バランス調整の回6。設計書 5 施設と企業 FM-11・FM-12）。

物を作る施設（作り方に産出物のある、マスを持つ施設。農場・鉱山・工場・発電所・造船所など）は、
建てたときの資材（定義の construction.materials）を設備として持ちます（``economy["equipment"]``:
資材 → 残りの量。1000 分の 1）。書かれていなければ新品（定義の量そのまま）です。店・港・銀行・
商社・役所・学校・警察・運送業・海運業は、回6 では設備を持ちません。

- 傷み（決算の段）: 資材ごとに、年 equipment.wear の割合（書いていない資材は
  equipment.default_wear）の 12 分の 1 を毎ターン減らします。使っても使わなくても同じ速さです
  （現実は使うほど傷む）。
- 設備の割合（``economy["condition"]``、1000 分の 1）: 資材ごとの「残り ÷ 定義の量」の一番小さい
  もの。稼働率・雇う人数・原料を買う量の上限になります（機械が1割傷めば1割作れない）。
- 直す（市場の段）: 直したい割合 L =「操業目標 + market.target_step」（全力まで）に足りない資材を、
  工事の資材と同じ形で買い、買った分をそのまま設備に入れて設備の額に足します（GDP では工事、つまり
  設備への投資）。
  - 国営の施設（国庫直轄も独立採算も）は、国が公共事業費（construction）から払い、国の倉庫の資材を
    先に使います。採算は比べません。
  - 民営の施設は、全力の変動費の利益の 12 ターン分が、設備の「利子 + 傷み」の年の費用
    （資材の値打ち ×（market.initial_prices.capital_rate + 傷みの割合））以上のときだけ、
    自分の預金から直します。下回れば直さずに傷むに任せ、設備の割合が equipment.scrap を切ったら
    閉じます（参入と撤退の段）。
  - 差し押さえ中・閉鎖中の施設は直しません。
- 帳簿（決算の段）: 設備を持つ施設の減価償却は、帳簿の設備の値打ち（設備の額 − 償却済み）×
  このターンに傷んだ資材の値打ち ÷ 傷む前の設備の値打ち（今の値段で）です。
"""

from __future__ import annotations

from collections.abc import Mapping

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import OWNER_STATE, Facility
from hakoniwa_core.units import money_of, round_money

EQUIPMENT = "equipment"  # 施設の economy: 設備の資材 → 残りの量（1000 分の 1）
CONDITION = "condition"  # 施設の economy: 設備の割合（1000 分の 1。書いていなければ新品）
NOTICED = "equipment_noticed"  # 施設の ext: 国営の設備が傷んだと知らせた（戻るまで出さない）
FULL = fac.FULL


def kit(defs: Defs, kind: str) -> Mapping[str, int]:
    """新品の設備（定義の建設の資材。1000 分の 1）。"""
    return defs.facilities[kind]["construction"].get("materials", {})


def kit_of(defs: Defs, f: Facility) -> Mapping[str, int]:
    """施設 f の新品の設備（建て増した施設は規模の分だけ多い。段6 A3）。農場は、段階を上げる工事で
    足した機械や用水路の資材も設備に入る（W-190）。"""
    size = fac.size_of(f)
    base = kit(defs, f.kind)
    if fac.GRADE in f.ext:
        base = dict(base)
        for good, qty in fac.grade_kit(defs, f).items():
            base[good] = base.get(good, 0) + qty
    return base if size == 1 else {g: q * size for g, q in base.items()}


def _makes_goods(defs: Defs, kind: str) -> bool:
    return any(r.get("outputs") for r in defs.facilities[kind].get("recipes", {}).values())


def has_equipment(defs: Defs, config: GameConfig, f: Facility) -> bool:
    """傷む設備を持つ施設か（建ち終わった、マスを持つ、物を作る施設）。"""
    return (
        f.built
        and fac.on_map(f)
        and not fac.is_housing(config, f)
        and not fac.is_tileless(defs, f.kind)
        and _makes_goods(defs, f.kind)
        and any(q > 0 for q in kit_of(defs, f).values())
    )


def holdings(defs: Defs, f: Facility) -> dict[str, int]:
    """今の設備（資材 → 残りの量）。書いていなければ新品。"""
    found = f.economy.get(EQUIPMENT) or {}
    # 書いていない資材（定義に足した資材など）は新品とする
    return {g: found.get(g, q) for g, q in sorted(kit_of(defs, f).items()) if q > 0}


def machine_goods(defs: Defs, f: Facility) -> set[str]:
    """機械として別に傷みを見る設備の資材（農場の機械。傷むと畑が手作業に戻る。FM-51）。"""
    return fac.machine_goods(defs, f) if fac.GRADE in f.ext else set()


def _ratio(defs: Defs, f: Facility, have: Mapping[str, int]) -> int:
    """設備の割合。機械は入れない（機械が傷んでも、稼働率の上限でなく、畑が手作業に戻る形で表す）。"""
    machines = machine_goods(defs, f)
    return min(
        (
            fac.ratio(have.get(g, 0), q)
            for g, q in kit_of(defs, f).items()
            if q > 0 and g not in machines
        ),
        default=FULL,
    )


def _machine_ratio(defs: Defs, f: Facility, have: Mapping[str, int]) -> int:
    machines = machine_goods(defs, f)
    return min(
        (
            fac.ratio(have.get(g, 0), q)
            for g, q in kit_of(defs, f).items()
            if g in machines and q > 0
        ),
        default=FULL,
    )


def condition(f: Facility) -> int:
    """設備の割合（1000 分の 1）。設備を持たない施設と新品は 100%。"""
    return f.economy.get(CONDITION, FULL)


def _store(defs: Defs, f: Facility, have: dict[str, int]) -> None:
    f.economy[EQUIPMENT] = have
    f.economy[CONDITION] = _ratio(defs, f, have)
    if machine_goods(defs, f):
        f.economy[fac.MACHINES] = _machine_ratio(defs, f, have)


def needs_repair(defs: Defs, config: GameConfig, f: Facility) -> bool:
    """設備を直したいか: 設備の割合が直したい割合に届かないか、機械が新品でない（機械は傷んだ分だけ
    人手が戻るので、操業目標でなく新品まで直す）。"""
    return condition(f) < level(config, f) or (
        bool(machine_goods(defs, f)) and fac.machine_ratio(f) < FULL
    )


def wear_rate(config: GameConfig, good: str) -> float:
    """資材 good の年の傷みの割合。"""
    settings = config.get("equipment")
    return settings["wear"].get(good, settings["default_wear"])


def value(have: Mapping[str, int], prices: Mapping[str, int]) -> int:
    return sum(money_of(prices.get(g, 0), q) for g, q in have.items())


def yearly_wear(defs: Defs, config: GameConfig, kind: str, prices: Mapping[str, int]) -> float:
    """新品の設備が1年に傷む値打ち（今の値段で）。"""
    return sum(
        money_of(prices.get(g, 0), q) * wear_rate(config, g) for g, q in kit(defs, kind).items()
    )


def yearly_cost(defs: Defs, config: GameConfig, f: Facility, prices: Mapping[str, int]) -> float:
    """設備を直して持ち続ける年の費用: 新品の設備が1年に傷む値打ちと、今直す代金の利子
    （もう払った設備の利子は数えない）。直すかどうかの比べに使う。"""
    rate = config.get("market.initial_prices.capital_rate")
    cost = value(wants(defs, config, f), prices)
    return yearly_wear(defs, config, f.kind, prices) * fac.size_of(f) + cost * rate


def wear(defs: Defs, config: GameConfig, f: Facility, prices: Mapping[str, int]) -> tuple[int, int]:
    """1ターン分傷ませ、(傷んだ値打ち, 傷む前の値打ち) を返す（今の値段で）。"""
    have = holdings(defs, f)
    before = value(have, prices)
    worn: dict[str, int] = {}
    for good, qty in have.items():
        gone = min(qty, round_money(qty * wear_rate(config, good) / YEAR + 0.5))
        worn[good] = gone
        have[good] = qty - gone
    _store(defs, f, have)
    return value(worn, prices), before


def level(config: GameConfig, f: Facility) -> int:
    """直したい設備の割合: 操業目標に market.target_step を足した分（全力まで）。"""
    step = round_money(config.get("market.target_step") * FULL + 0.5)
    return min(FULL, f.economy.get("target", FULL) + step)


def wants(
    defs: Defs, config: GameConfig, f: Facility, room: Mapping[str, int] | None = None
) -> dict[str, int]:
    """直したい割合まで直すのに要る資材（資材 → 量）。room（資材 → 手に入る量）を渡すと、一番
    足りない資材で届く割合までにする（設備の割合は一番少ない資材で決まるので、ほかの資材だけ
    買っても直らない）。"""
    goal = level(config, f)
    have = holdings(defs, f)
    machines = machine_goods(defs, f)
    if room is not None:
        reach = min(
            (
                fac.ratio(have.get(g, 0) + room.get(g, 0), q)
                for g, q in kit_of(defs, f).items()
                if g not in machines
            ),
            default=FULL,
        )
        goal = min(goal, reach)
    out = {}
    for good, qty in sorted(kit_of(defs, f).items()):
        if good in machines:  # 機械は新品まで（手に入る量まで）
            need = qty - have.get(good, 0)
            if room is not None:
                need = min(need, room.get(good, 0))
        else:
            need = fac.scale(qty, goal) - have.get(good, 0)
        if need > 0:
            out[good] = need
    return out


def shortfall(defs: Defs, f: Facility) -> dict[str, int]:
    """新品まで直すのに要る資材。"""
    have = holdings(defs, f)
    return {g: q - have.get(g, 0) for g, q in kit_of(defs, f).items() if q > have.get(g, 0)}


def worthwhile(
    defs: Defs, config: GameConfig, f: Facility, profit: int, prices: Mapping[str, int]
) -> bool:
    """民営の持ち主が直すか: 全力の変動費の利益 profit の1年分が、直して持ち続ける年の費用
    （yearly_cost）以上。値段は直近の平均を渡す（一時の安値で閉じない）。国営は採算を比べずに直す。"""
    if f.owner == OWNER_STATE:
        return True
    return profit * YEAR >= yearly_cost(defs, config, f, prices)


def install(defs: Defs, f: Facility, good: str, qty: int, cost: int) -> None:
    """直す資材 qty を設備に入れ、代金 cost を設備の額に足す。"""
    if qty <= 0:
        return
    have = holdings(defs, f)
    have[good] = have.get(good, 0) + qty
    _store(defs, f, have)
    f.economy["capital"] = f.economy.get("capital", 0) + cost


def keep(f: Facility) -> dict[str, int]:
    """作り直す前の施設の経済の状態から、引き継ぐ設備（差し押さえた施設の売却など）。"""
    return {k: f.economy[k] for k in (EQUIPMENT, CONDITION, fac.MACHINES) if k in f.economy}


def expand(defs: Defs, f: Facility, added: int = 1) -> None:
    """建て増し（段6 A3）: 新しく足した規模の分の新品の設備を、今の設備に足す。f の規模は呼ぶ
    前に上げておく（今の設備は上げる前の規模の分）。"""
    if EQUIPMENT not in f.economy:
        return  # 書いていなければ新品のまま
    have = dict(f.economy[EQUIPMENT])
    for good, qty in kit(defs, f.kind).items():
        have[good] = have.get(good, 0) + qty * added
    _store(defs, f, have)
