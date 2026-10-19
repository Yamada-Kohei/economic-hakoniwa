"""企業（バランス調整の回7。設計書 5 施設と企業「企業の仕組み」）。

企業は施設の束で、主体 ``firm:<番号>`` として預金と株主を持ちます。工場ごとの採算ではなく、企業が
お金をまとめて持ち、投資と閉鎖を決めます（工場どうしの内部補助、産業をまたぐ財閥も許す）。

- 株: 株主（民営の企業は資本家階級 ``hh:owners``）が企業の株（equity/firm:n）を持ち、企業が施設の株
  （equity/fac:m）を持ちます（持株会社の形）。施設の配当は企業に入り、企業が株主に配当します。
- 施設のお金: 施設は今までどおり自分の預金（``fac:<番号>``）と借入を持ち、借入は施設ごとに付けた
  まま（担保の単位。現実では借りるのは会社で、ここは現実と違う形）です。施設は次の支払いの分を手元に
  残し、超えた分を企業に上げ、足りない施設には企業の預金から回します（資金の一本化。段
  ``firms_funds``）。運転資金や設備の直しも、銀行から借りる前に企業の預金から回します（draw）。
- 法人税は企業でまとめて払います（施設の利益を足し、赤字は企業で繰り越す。1つの会社の中の工場どうし
  の損益の通算）。
- 国営企業: 国の施設は島×業種の分類（facilities.yaml の category）ごとに1つの国営企業にまとめます。
  国営企業は名簿で、お金は今までどおり施設ごと（持ち主は政府。政府が直接支える）です。ここも現実
  （国営企業も1つの会社としてお金を持つ）と違う形です。

企業に入る施設（eligible）は、地図の上の物を作る施設と店です。銀行・運送業・商社・海運業・町は企業に
入れません。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState

FIRM = "firm"  # 施設の economy: 属する企業の番号
PREFIX = "firm:"

# 企業の記録（economy.firms[番号]）の欄
NAME = "name"
OWNER = "owner"  # private（民営）か state（国営）
FOUNDED = "founded"  # 作ったターン
SECTOR = "sector"  # 国営企業の業種の分類（民営は空）
LOSSES = "losses"  # 繰り越した赤字 [[ターン, 額], ...]
INVESTED = "invested"  # 最後に施設を建てた・買ったターン
RED = "red_turns"  # 連結の現金収支がマイナスの続いたターン数
SUPPORTED = "supported"  # このターンに施設へ回した額
# 施設の economy: ほかの施設の手元から回してもらったターンの続いた数（回7 後半）
POOLED = "pooled_turns"
POOLED_AT = "pooled_at"  # 施設の economy: 最後にほかの施設の手元から回してもらったターン


def entity(number: int) -> str:
    return f"{PREFIX}{number}"


def is_firm(name: str) -> bool:
    return name.startswith(PREFIX)


def number_of(name: str) -> int:
    return int(name[len(PREFIX) :])


def firm_of(f: Facility) -> int | None:
    """施設の属する企業の番号（なければ None）。"""
    number = f.economy.get(FIRM)
    return int(number) if number is not None else None


def record(island: IslandState, number: int) -> dict[str, Any] | None:
    return island.economy.firms.get(str(number))


def private_firm_of(island: IslandState, f: Facility) -> int | None:
    """民営の施設の属する民営の企業の番号（お金を企業で持つ企業だけ）。"""
    if f.owner != OWNER_PRIVATE:
        return None
    number = firm_of(f)
    if number is None:
        return None
    rec = record(island, number)
    if rec is None or rec[OWNER] != OWNER_PRIVATE:
        return None
    return number


def members(island: IslandState, number: int) -> list[Facility]:
    """企業の施設（番号の順）。"""
    return [f for f in island.facilities if firm_of(f) == number]


def eligible(defs: Defs, config: GameConfig, f: Facility) -> bool:
    """企業に入る施設か（地図の上の物を作る施設と店）。"""
    fdef = defs.facilities[f.kind]
    category = fdef.get("category")
    if category in ("finance", "housing", "transport", "trade", "admin", "education", "security"):
        return False
    if fdef.get("public_service"):
        return False
    if f.kind == config.get("services.facility"):
        return False
    return fac.on_map(f) or bool(fdef.get("shop"))


def sector_of(defs: Defs, kind: str) -> str:
    return defs.facilities[kind].get("category", "")


def new_firm(island: IslandState, owner: str, turn: int, sector: str = "") -> int:
    """企業を作る（お金はまだ持たない）。番号を返す。"""
    economy = island.economy
    economy.firm_seq += 1
    number = economy.firm_seq
    economy.firms[str(number)] = {
        NAME: "",
        OWNER: owner,
        FOUNDED: turn,
        SECTOR: sector,
        INVESTED: turn,
    }
    return number


def shareholder(island: IslandState, number: int) -> str:
    """企業の株主の主体（民営は資本家階級、国営は政府）。"""
    from hakoniwa_core.economy import OWNERS, household

    rec = record(island, number)
    if rec is not None and rec[OWNER] == OWNER_STATE:
        return ledger.GOV
    return household(OWNERS)


def join(island: IslandState, f: Facility, number: int) -> None:
    """施設を企業に入れる。民営の企業なら、今の持ち主が持つ施設の株を企業に移し、持ち主は同じ額の
    企業の株を持つ（株の交換）。"""
    from hakoniwa_core.economy import facility_entity

    f.economy[FIRM] = number
    rec = record(island, number)
    if rec is None or rec[OWNER] != OWNER_PRIVATE:
        return
    target = entity(number)
    fac_entity = facility_entity(f.id)
    suffix = f"/{ledger.EQUITY}/{fac_entity}"
    for name, value in sorted(island.accounts.items()):
        if not name.endswith(suffix) or ledger.is_issued(name) or value <= 0:
            continue
        holder = ledger.parse(name)[0]
        if is_firm(holder):  # 企業が出資した施設の株は、その企業が持ったまま
            continue
        ledger.transfer(island, ledger.EQUITY, fac_entity, holder, target, value, "equity_transfer")
        ledger.create(island, holder, ledger.EQUITY, target, value, "equity_transfer")


def leave(island: IslandState, f: Facility) -> None:
    """施設を企業から外す（株はそのまま。国有化・民営化・取り壊しのとき、呼ぶ側が株を動かす）。"""
    f.economy.pop(FIRM, None)


def state_firm(island: IslandState, defs: Defs, kind: str, turn: int) -> int:
    """業種の分類の国営企業（なければ作る）。"""
    sector = sector_of(defs, kind)
    for key, rec in sorted(island.economy.firms.items(), key=lambda kv: int(kv[0])):
        if rec[OWNER] == OWNER_STATE and rec.get(SECTOR) == sector:
            return int(key)
    return new_firm(island, OWNER_STATE, turn, sector)


def spare(island: IslandState, number: int) -> int:
    """企業が施設に回せるお金（企業の預金。未払いの税の分は残す）。"""
    from hakoniwa_core.economy import arrears

    name = entity(number)
    owed = arrears.total(island, name, arrears.TAX_ARREARS)
    return max(0, ledger.money(island, name) - owed)


def draw(island: IslandState, f: Facility, amount: int, ctx: Any = None) -> int:
    """民営の企業の預金から施設 f へ amount まで回す（内部補助）。回した額を返す。

    ctx（段の文脈）を渡すと（返済と未払いの賃金・税の分だけ。運転資金と設備の直しには渡さない）、
    企業の預金で足りない分を、同じ企業のほかの施設の手元のうち次の支払いの
    firms.pool_floor_turns ターン分と返済の見込みを超えた分から、多く持つ施設の順に回す
    （資金の一本化。現実の会社のキャッシュ・マネジメント。回7 後半）。施設の手元は次の支払いの
    firms.operating_turns ターン分を残して企業に上げるので、企業の預金が 0 でも、儲かる工場が
    手元を持ったまま兄弟の施設が返済に遅れ、企業ごと倒れていた。"""
    from hakoniwa_core.economy import facility_entity

    number = private_firm_of(island, f)
    if number is None or amount <= 0:
        return 0
    target = facility_entity(f.id)
    moved = min(amount, spare(island, number))
    if moved > 0:
        ledger.pay(island, entity(number), target, moved, "firm_support")
    if ctx is not None and moved < amount:
        moved += _pool(island, ctx, number, f, amount - moved)
    if moved > 0:
        rec = record(island, number)
        assert rec is not None
        rec[SUPPORTED] = rec.get(SUPPORTED, 0) + moved
    return moved


def _pool(island: IslandState, ctx: Any, number: int, f: Facility, amount: int) -> int:
    """同じ企業のほかの施設の手元の余り（次の支払いの firms.pool_floor_turns ターン分と、この
    ターンの返済の見込みを超えた分）から、施設 f へ amount まで回す。賃金・税・返済が遅れている
    施設と閉鎖の途中の施設からは回さない。回した額を返す。"""
    from hakoniwa_core.economy import Payer, arrears, banks, facility_entity, funds
    from hakoniwa_core.economy.start import next_payments

    floor_turns = ctx.config.get("firms.pool_floor_turns")
    target = facility_entity(f.id)
    rows = []
    for m in members(island, number):
        if m.id == f.id or not m.built or "foreclosed" in m.economy or "closing" in m.economy:
            continue
        name = facility_entity(m.id)
        if arrears.total(island, name) or arrears.total(island, name, arrears.TAX_ARREARS):
            continue
        if any(x.arrears > 0 for x in banks.loans_by_borrower(island, name)):
            continue
        keep = next_payments(island, ctx.defs, ctx.config, m) * floor_turns
        keep += banks.debt_service(island, name, ctx.turn)
        extra = funds.available(island, Payer(name)) - keep  # 取り置いた分は回さない
        if extra > 0:
            rows.append((-extra, m.id, name, extra))
    moved = 0
    for _, _, name, extra in sorted(rows):
        if moved >= amount:
            break
        give = min(extra, amount - moved)
        ledger.pay(island, name, target, give, "firm_support")
        moved += give
    if moved > 0 and f.economy.get(POOLED_AT) != ctx.turn:
        f.economy[POOLED] = f.economy.get(POOLED, 0) + 1
        f.economy[POOLED_AT] = ctx.turn
        island.stats["firms.pooled"] = island.stats.get("firms.pooled", 0) + moved
    return moved


def group_borrowers(island: IslandState, borrower: str) -> set[str]:
    """大口融資の規制で1つの借り手と見る主体（民営の企業の施設なら、その企業の施設すべて）。"""
    from hakoniwa_core.economy import facility_entity

    if not borrower.startswith("fac:"):
        return {borrower}
    f = island.facility(int(borrower[4:]))
    if f is None:
        return {borrower}
    number = private_firm_of(island, f)
    if number is None:
        return {borrower}
    return {facility_entity(m.id) for m in members(island, number)} | {borrower}


def display_name(island: IslandState, defs: Any, number: int) -> str:
    """企業の名前（決めていなければ、国名と業種・番号から）。"""
    rec = record(island, number)
    if rec is None:
        return ""
    if rec.get(NAME):
        return rec[NAME]
    if rec[OWNER] == OWNER_STATE:
        return f"{island.name}国営{rec.get(SECTOR, '')}"
    return f"{island.name}企業{number}"


def _relation(defs: Defs, kinds: set[str], kind: str) -> int:
    """kinds の施設と kind のつながり: 1 は kind の作る物を原料に使う（売り先。系列の下請けに
    出資する組み立ての会社）、2 は kind の原料を作る（垂直統合）、3 はつながりなし。"""
    new_in: set[str] = set()
    new_out: set[str] = set()
    for recipe in defs.facilities[kind]["recipes"].values():
        new_in |= set(recipe["inputs"])
        new_out |= set(recipe["outputs"])
    rank = 3
    for other in kinds:
        for recipe in defs.facilities[other]["recipes"].values():
            if set(recipe["inputs"]) & new_out:
                return 1
            if set(recipe["outputs"]) & new_in:
                rank = 2
    return rank


def choose_investor(
    island: IslandState, defs: Defs, kind: str, equity: int
) -> tuple[str, int | None] | None:
    """新しい施設（kind）の出資 equity を出す者: 企業の預金で出せる民営の企業のうち、同じ種類の
    施設を持つ企業 → 売り先の施設を持つ企業（系列。回7 後半）→ 原料の施設を持つ企業（垂直統合）→
    ほかの企業（産業をまたぐ財閥）の順、
    同じなら預金の多い順・番号の順。どこも出せなければ資本家階級が新しい企業を起こす（その預金で
    出せるとき）。(払う主体, 企業の番号か None) を返し、誰も出せなければ None。"""
    from hakoniwa_core.economy import OWNERS, Payer, funds, household

    best: tuple[tuple[int, int, int], int] | None = None
    for key, rec in island.economy.firms.items():
        if rec[OWNER] != OWNER_PRIVATE:
            continue
        number = int(key)
        cash = spare(island, number)
        if cash < max(1, equity):
            continue
        kinds = {f.kind for f in members(island, number)}
        if not kinds:
            continue
        rank = 0 if kind in kinds else _relation(defs, kinds, kind)
        score = (rank, -cash, number)
        if best is None or score < best[0]:
            best = (score, number)
    if best is not None:
        return entity(best[1]), best[1]
    if OWNERS not in island.economy.households:
        return None
    if funds.available(island, Payer(household(OWNERS))) >= equity:
        return household(OWNERS), None
    return None


def invested(island: IslandState, number: int | None, f: Facility, turn: int) -> None:
    """企業 number が施設 f に出資したあとに、施設を企業に入れ、投資したターンを残す。"""
    if number is None:
        return
    f.economy[FIRM] = number
    rec = record(island, number)
    if rec is not None:
        rec[INVESTED] = turn
