"""産業政策と国際金融の政策（フェーズ3-3）: 関税・輸出補助・輸入の割り当て・禁輸、為替の介入と
固定相場、資本取引の制限、外国の国債の売り買い。

どれも政策 ``island.policy`` の欄に持ち、命令の段で変わって、同じターンの貿易の段（為替・圏の市場・
荷下ろし）と国債の段から効きます。財・通貨・島の番号は文字列のキーです。

- 関税（tariffs。財 → 率）: 商社が圏の市場で買った荷を下ろすとき、「約定の値段 × 為替 × 率」を国庫に
  払う（契約で国の倉庫に入る荷と、海外の商人にはかからない。圏の代表が「圏内の関税をなくす」と決めた
  圏の市場で買った荷にもかからない）。商社は関税を見込んで指値を下げる。
- 輸出補助（export_subsidies。財 → 1 単位あたりの額）: 商社が圏の市場で売れたとき、国庫の補助金の
  予算（subsidies）から商社に払う。商社は補助を見込んで島で高く買い、圏で安く売れる。
- 輸入の割り当て（import_quotas。財 → 1ターンの量）: 商社の圏の市場の買い注文を、島全体でそこまでに
  する（契約の分は数えない）。
- 禁輸（embargoes。相手の島 → 財、空ならすべての財）: 圏の市場でその国との組を割り当てず、その国との
  契約（援助を含む）は届けない。どちらかの国が禁輸していれば止まる。
- 為替の介入（fx_intervention。通貨 → 毎ターン買う額、マイナスは売る）と固定相場（fx_peg。通貨 →
  レート）: hakoniwa_core/trade/fx.py。中央銀行が独立しているときは決められない。
- 資本取引（capital_controls）: free（自由）・official（政府と中央銀行だけ）・closed（禁止）。
  "" は経済体制の既定（regimes.yaml の capital_controls）。外国の国債を持てる者と、自国の国債を
  外国の誰が買えるかを決める（modules/bonds）。
- 外国の国債（foreign_bonds。持ち手 gov・cb → 発行した島 → 毎ターン買う額面、マイナスは売る）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.trade import currency_label
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, to_basis

NOTES = "trade_notes"  # island.scratch: 国の名前を入れてから貿易の段がログに書く知らせ
NAMED = ("partner", "issuer")  # 島の番号を国の名前にする引数（currency は通貨の名前にする）
FREE = "free"
OFFICIAL = "official"
CLOSED = "closed"
CONTROLS = (FREE, OFFICIAL, CLOSED)
HOLDERS = ("gov", "cb")  # 命令で外国の国債を売り買いする持ち手


# --- 政策の値 -----------------------------------------------------------------------


def capital_controls(island: IslandState, defs: Defs) -> str:
    """資本取引の制限（決めていなければ経済体制の既定）。"""
    if island.policy.capital_controls:
        return island.policy.capital_controls
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    return str(regime.get("capital_controls", OFFICIAL))


def may_hold_foreign(island: IslandState, defs: Defs, entity: str) -> bool:
    """島の主体 entity が外国の国債を持てるか（政府と中央銀行は禁止でなければ持てる。銀行は
    自由のときだけ）。"""
    mode = capital_controls(island, defs)
    if mode == CLOSED:
        return False
    if entity in HOLDERS:
        return True
    return mode == FREE


def tariff_rate(island: IslandState, good: str) -> int:
    """財の関税率（1万分の1）。"""
    return island.policy.tariffs.get(good, 0)


def export_subsidy(island: IslandState, good: str) -> int:
    """財 1 単位あたりの輸出補助。"""
    return island.policy.export_subsidies.get(good, 0)


def quota(island: IslandState, good: str) -> int | None:
    """財の1ターンの輸入の上限（1000 分の 1 単位。決めていなければ None）。"""
    return island.policy.import_quotas.get(good)


def embargoes(island: IslandState, partner: int, good: str) -> bool:
    """島が相手の国の財 good を禁輸しているか。"""
    goods = island.policy.embargoes.get(str(partner))
    return goods is not None and (not goods or good in goods)


def blocked(world: WorldState, a: int, b: int, good: str) -> bool:
    """島 a と b の間の財 good の取引が、どちらかの国の禁輸で止まっているか。"""
    if a == b:
        return False
    first, second = world.islands.get(a), world.islands.get(b)
    return (first is not None and embargoes(first, b, good)) or (
        second is not None and embargoes(second, a, good)
    )


def with_tariff(value: int, rate: int) -> int:
    """値段 value に関税 rate（1万分の1）を足した値。"""
    return value * (BASIS + rate) // BASIS


# --- 命令 ---------------------------------------------------------------------------


def note(island: IslandState, kind: str, args: dict[str, Any]) -> None:
    """ほかの国や通貨の名前が要るログ: 命令の段は世界を読めないので、貿易の段で名前を入れて書く
    （write_notes）。"""
    island.scratch.setdefault(NOTES, []).append([kind, args])


def write_notes(world: WorldState, island: IslandState) -> None:
    for kind, args in island.scratch.pop(NOTES, []):
        shown = dict(args)
        for key in NAMED:
            if isinstance(shown.get(key), int):
                other = world.islands.get(shown[key])
                shown[key] = other.name if other is not None else str(shown[key])
        if isinstance(shown.get("currency"), int):
            shown["currency"] = currency_label(world, shown["currency"])
        island.log.append(LogEntry(kind, shown))


def _check_economy(island: IslandState) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def _check_good(payload: dict[str, Any], defs: Defs) -> str:
    good = payload.get("good")
    if good not in defs.goods or "freight_weight" not in defs.goods[good]:
        raise OrderFailed("reason_not_tradable")
    return str(good)


def check_tariff(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    _check_good(payload, defs)


def set_tariff(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """関税（%。0 でやめる）。"""
    good, rate = payload["good"], to_basis(payload["rate"] / 100)
    if rate > 0:
        island.policy.tariffs[good] = rate
        island.log.append(LogEntry("tariff_set", {"good": good, "rate": rate * 100 / BASIS}))
    else:
        island.policy.tariffs.pop(good, None)
        island.log.append(LogEntry("tariff_removed", {"good": good}))


def check_export_subsidy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    _check_good(payload, defs)


def set_export_subsidy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """輸出補助（1 単位あたりの額。0 でやめる）。"""
    good, amount = payload["good"], int(payload["amount"])
    if amount > 0:
        island.policy.export_subsidies[good] = amount
        island.log.append(LogEntry("export_subsidy_set", {"good": good, "amount": amount}))
    else:
        island.policy.export_subsidies.pop(good, None)
        island.log.append(LogEntry("export_subsidy_removed", {"good": good}))


def check_import_quota(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    _check_good(payload, defs)


def set_import_quota(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """輸入の割り当て（1ターンの量。quantity を省くとやめる）。"""
    good = payload["good"]
    if payload.get("quantity") is None:
        island.policy.import_quotas.pop(good, None)
        island.log.append(LogEntry("import_quota_removed", {"good": good}))
        return
    qty = round(payload["quantity"] * QUANTITY_SCALE)
    island.policy.import_quotas[good] = qty
    island.log.append(LogEntry("import_quota_set", {"good": good, "amount": qty / QUANTITY_SCALE}))


def check_embargo(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    if payload.get("partner") == island.id:
        raise OrderFailed("reason_contract_partner")
    if payload.get("good") is not None:
        _check_good(payload, defs)


def set_embargo(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """禁輸を始める・やめる（lift: true）。good を省くとすべての財。相手の国は名前でログに出すので、
    貿易の段で国の名前に直す（ここでは番号）。"""
    key = str(payload["partner"])
    good = payload.get("good")
    table = island.policy.embargoes
    if not payload.get("lift"):
        if good is None:
            table[key] = []
        elif key not in table or table[key]:  # すべての財を止めているなら、そのまま
            table[key] = sorted({*table.get(key, []), good})
    elif good is None:
        table.pop(key, None)
    elif table.get(key):
        rest = [g for g in table[key] if g != good]
        if rest:
            table[key] = rest
        else:
            table.pop(key)
    args: dict[str, Any] = {"partner": int(payload["partner"])}
    if good is not None:
        args["good"] = good
    kind = "embargo_set" if not payload.get("lift") else "embargo_lifted"
    note(island, kind + ("_good" if good is not None else ""), args)


def check_capital_controls(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    if payload.get("mode") == capital_controls(island, defs):
        raise OrderFailed("reason_already_done")


def set_capital_controls(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    island.policy.capital_controls = payload["mode"]
    island.log.append(LogEntry("capital_controls_set", {"mode": f"capital_{payload['mode']}"}))


def _check_cb(island: IslandState, payload: dict[str, Any]) -> None:
    _check_economy(island)
    if island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")
    if payload.get("currency") == island.id:
        raise OrderFailed("reason_own_currency")


def check_fx_intervention(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_cb(island, payload)


def set_fx_intervention(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """中央銀行が毎ターン通貨 currency を amount 買う（マイナスは売る。0 でやめる）。"""
    key, amount = str(payload["currency"]), int(payload["amount"])
    if amount:
        island.policy.fx_intervention[key] = amount
    else:
        island.policy.fx_intervention.pop(key, None)
    args = {"currency": int(payload["currency"]), "amount": abs(amount)}
    kind = "fx_intervention_buy" if amount > 0 else "fx_intervention_sell"
    note(island, kind if amount else "fx_intervention_stopped", args)


def check_fx_peg(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_cb(island, payload)
    if not payload.get("rate") and str(payload.get("currency")) not in island.policy.fx_peg:
        raise OrderFailed("reason_not_pegged")


def set_fx_peg(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """固定相場を始める（rate は相手の通貨 1 が自国通貨でいくらか）・やめる（rate 0）。"""
    key = str(payload["currency"])
    rate = round(payload.get("rate", 0) * BASIS)
    args: dict[str, Any] = {"currency": int(payload["currency"])}
    if rate > 0:
        island.policy.fx_peg[key] = rate
        island.policy.fx_intervention.pop(key, None)  # 固定のあいだは介入の額を使わない
        note(island, "fx_peg_set", {**args, "rate": rate / BASIS})
    else:
        island.policy.fx_peg.pop(key, None)
        note(island, "fx_peg_released", args)


def check_foreign_bonds(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_economy(island)
    holder = payload.get("holder")
    if holder not in HOLDERS or payload.get("issuer") == island.id:
        raise OrderFailed("reason_invalid_payload")
    if holder == "cb" and island.policy.cb_independent:
        raise OrderFailed("reason_cb_independent")
    if payload.get("amount", 0) > 0 and not may_hold_foreign(island, defs, holder):
        raise OrderFailed("reason_capital_closed")


def set_foreign_bonds(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """政府か中央銀行が、外国の国債を毎ターン amount（額面。マイナスは売る。0 でやめる）
    売り買いする。"""
    holder, key, amount = payload["holder"], str(payload["issuer"]), int(payload["amount"])
    table = island.policy.foreign_bonds.setdefault(holder, {})
    if amount:
        table[key] = amount
    else:
        table.pop(key, None)
        if not table:
            island.policy.foreign_bonds.pop(holder, None)
    args = {
        "holder": f"bond_holder_{holder}",
        "issuer": int(payload["issuer"]),
        "amount": abs(amount),
    }
    kind = "foreign_bonds_buy" if amount > 0 else "foreign_bonds_sell"
    note(island, kind if amount else "foreign_bonds_stopped", args)


ORDERS = {
    "set_tariff": (set_tariff, check_tariff),
    "set_export_subsidy": (set_export_subsidy, check_export_subsidy),
    "set_import_quota": (set_import_quota, check_import_quota),
    "set_embargo": (set_embargo, check_embargo),
    "set_capital_controls": (set_capital_controls, check_capital_controls),
    "set_fx_intervention": (set_fx_intervention, check_fx_intervention),
    "set_fx_peg": (set_fx_peg, check_fx_peg),
    "set_foreign_bonds": (set_foreign_bonds, check_foreign_bonds),
}
