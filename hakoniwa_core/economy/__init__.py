"""お金の経済（フェーズ2）の共通の部品。

段（hakoniwa_core/modules/ の market・labor・accounts など）はここを通して、施設や家計が
お金を払えるかを調べ、払い、帳簿と統計に残します。お金そのものは hakoniwa_core/ledger.py
だけが動かします。

主体（お金を持つもの）と、施設のお金を誰が払うか:

| 施設 | 払う主体 |
|---|---|
| 独立採算の施設（運送業を含む） | 施設 ``fac:<番号>`` |
| 銀行 | 銀行 ``bank:<番号>`` |
| 国庫直轄の施設 | 政府（支出の項目 state_facilities） |
| 公務員の施設（中央省庁など） | 政府（支出の項目 civil_servants） |
| 工事中の施設（国の工事） | 政府（支出の項目 construction） |
| 工事中の民営工場（民間の参入。フェーズ2-2） | 施設 ``fac:<番号>``（出資と建設資金の融資） |
"""

from __future__ import annotations

from dataclasses import dataclass

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_PRIVATE,
    Facility,
    IslandState,
)

# 政府の支出の項目（game.yaml の fiscal.payment_order に並べる）
CONSTRUCTION = "construction"
CIVIL_SERVANTS = "civil_servants"
STATE_FACILITIES = "state_facilities"
UNEMPLOYMENT = "unemployment"
BONDS = "bonds"
WAREHOUSE = "warehouse"
SUBSIDIES = "subsidies"
EDUCATION = "education"  # 教育費（学校と大学。フェーズ2-3）
SECURITY = "security"  # 治安費（警察。フェーズ3-4）

GENERAL = "general"  # 一般の労働者の層
OWNERS = "owners"  # 資産を持つ層
ENGINEERS = "engineers"  # 技術者の層（フェーズ2-3）


@dataclass(frozen=True)
class Payer:
    """お金を払う側。政府が払うときは支出の項目 item も持つ。"""

    entity: str
    item: str | None = None


def household(layer: str) -> str:
    """家計の層の主体の名前。"""
    return f"hh:{layer}"


def facility_entity(number: int) -> str:
    return f"fac:{number}"


def is_bank(config: GameConfig, f: Facility) -> bool:
    return f.kind == config.get("banking.facility")


def is_transport(config: GameConfig, f: Facility) -> bool:
    return f.kind == config.get("freight.facility")


def is_public_service(defs: Defs, f: Facility) -> bool:
    """公務員の施設（賃金を国庫が払い、物を作らない）。"""
    return bool(defs.facilities[f.kind].get("public_service"))


def default_accounting(defs: Defs, island: IslandState, kind: str, owner: str) -> str:
    """建てるときの会計の形。公務員の施設はいつも国庫直轄、民営は独立採算、国営は経済体制の既定
    （regimes.yaml の state_accounting）。"""
    if defs.facilities[kind].get("public_service"):
        return ACCOUNTING_TREASURY
    if owner == OWNER_PRIVATE:
        return ACCOUNTING_SELF
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    return regime.get("state_accounting", ACCOUNTING_TREASURY)


def is_self_supporting(f: Facility) -> bool:
    return f.built and f.accounting == ACCOUNTING_SELF


def is_stopped(f: Facility) -> bool:
    """閉鎖の途中か差し押さえ中で、人を雇わず物を作らない施設か（フェーズ2-2）。"""
    return "closing" in f.economy or "foreclosed" in f.economy


def is_private_site(f: Facility) -> bool:
    """民間が自分のお金で建てている工事（民営工場の参入。フェーズ2-2）。"""
    return not f.built and bool(f.ext.get("private_entry"))


def entity_of(config: GameConfig, f: Facility) -> str:
    """施設のお金を持つ主体（独立採算なら施設、銀行なら銀行、ほかは政府）。"""
    if is_bank(config, f) and f.built:
        return ledger.bank_entity(f.id)
    if is_self_supporting(f) or is_private_site(f):
        return facility_entity(f.id)
    return ledger.GOV


def payer_of(defs: Defs, config: GameConfig, f: Facility) -> Payer:
    """施設の賃金・仕入れ・電気代を払う側。"""
    if is_private_site(f):
        return Payer(facility_entity(f.id))
    if not f.built:
        return Payer(ledger.GOV, CONSTRUCTION)
    if is_bank(config, f):
        return Payer(ledger.bank_entity(f.id))
    if is_self_supporting(f):
        return Payer(facility_entity(f.id))
    if is_public_service(defs, f):  # 学校と大学は教育費（定義の budget）
        return Payer(ledger.GOV, defs.facilities[f.kind].get("budget", CIVIL_SERVANTS))
    return Payer(ledger.GOV, STATE_FACILITIES)


def owner_entity(f: Facility) -> str:
    """施設の持ち主（出資と配当の相手）。民営は資産を持つ層、国営は政府。"""
    return household(OWNERS) if f.owner == OWNER_PRIVATE else ledger.GOV


def layers(config: GameConfig) -> dict[str, dict]:
    """家計の層の設定（game.yaml の household.layers）。"""
    return config.get("household.layers")


def active_layers(island: IslandState) -> list[str]:
    """この島にいる家計の層（人数の記録がある層）。名前の順。"""
    return sorted(island.economy.households)
