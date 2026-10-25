"""画面に出す文と数の組み立て。ルールの計算はせず、保存した値と定義データの表示名を使う。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from django.db.models import Q, Sum
from django.utils import timezone

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, ledger
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import (
    books,
    companies,
    entity_of,
    equipment,
    facility_entity,
    is_bank,
    is_private_site,
    is_self_supporting,
    retail,
)
from hakoniwa_core.log import format_log, format_turn
from hakoniwa_core.military import PRIVATE_LOGS
from hakoniwa_core.modules.banking import ENTRY as BANK_ENTRY
from hakoniwa_core.modules.bonds import CUT, UNDERWRITE
from hakoniwa_core.modules.failures import METHODS as RESOLUTION_METHODS
from hakoniwa_core.naming import facility_name
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_STATE,
    Facility,
    IslandState,
    Policy,
)
from hakoniwa_core.state.serialize import build
from hakoniwa_core.trade import KEY as TRADE_KEY
from hakoniwa_core.trade import currency_labels
from hakoniwa_core.units import BASIS, from_milli
from server.economy.models import Account, FacilityBook, Loan, MarketRecord
from server.game import get_game
from server.letters.services import LETTER_LOG
from server.world import models as m
from server.world.repository import facility_from_row, island_from_row


def message(key: str, **args: Any) -> str:
    game = get_game()
    return format_log(game.defs, key, args, calendar=game.calendar)


# 称号の文（ターンログ）。文に称号の名前 {title} を足して書く
TITLE_LOGS = {"title_gained", "title_lost", "title_lost_any"}


def title_name(industry: str) -> str:
    """産業の称号の名前（史実のあだ名。messages.yaml の title_name_<産業>）。なければ「〜の国」。"""
    key = f"title_name_{industry}"
    name = get_game().defs.name("messages", key)
    return f"{get_game().defs.name('industries', industry)}の国" if name == key else name


def turn_text(turn: int) -> str:
    """ある時点のターンを、世界の暦の日付で書く（「箱庭暦32年11月（ターン 383）」）。"""
    game = get_game()
    return format_turn(game.defs, turn, game.calendar)


def turn_schedule(world) -> dict[str, Any]:
    """更新の予定（U07）: 一時停止・更新中・次の更新時刻と、サーバーの今の時刻・予定を過ぎたか・
    更新の間隔。画面（turn_schedule.html）・JSON API・MCP が同じ値を読む。"""
    now = timezone.now()
    nxt = world.next_turn_at
    state = "paused" if world.paused else "updating" if world.updating_since else "scheduled"
    if state == "scheduled" and nxt is None:
        state = "unscheduled"
    elif state == "scheduled" and now > nxt:
        state = "late"
    return {
        "state": state,
        "paused": world.paused,
        "updating_since": world.updating_since.isoformat() if world.updating_since else None,
        "next_turn_at": nxt.isoformat() if nxt else None,
        "server_now": now.isoformat(),
        "interval_minutes": get_game().config.get("turn.interval_minutes"),
    }


def calendar_settings() -> dict[str, Any]:
    """画面の JavaScript が日付を書くのに使う、世界の暦（hakoniwa_core/dates.py）とその文面。"""
    game = get_game()
    defs, calendar = game.defs, game.calendar
    return {
        "name": calendar.name,
        "first_year": calendar.first_year,
        "turns_per_year": TURNS_PER_YEAR,
        "date": defs.name("messages", "calendar_date"),
        "before": defs.name("messages", "calendar_before"),
        "with_turn": defs.name("messages", "calendar_turn"),
    }


def amount(milli: int, good: str) -> float:
    """財 good の 1000 分の 1 単位の量を、見せる量に（display_scale を掛け、小数第1位まで）。"""
    return round(get_game().defs.shown_amount(good, from_milli(milli)), 1)


def shown(good: str, value: float) -> float:
    """財 good の量（1 単位で数えた数。統計の値など）を、見せる量に（小数第1位まで）。"""
    return round(get_game().defs.shown_amount(good, value), 1)


def price(good: str, value: float) -> float:
    """財 good の 1 単位あたりの値段を、見せる単位 1 つあたりの値段に。"""
    return get_game().defs.shown_price(good, value)


def price_text(good: str, value: float, currency: str = "") -> str:
    """財 good の 1 単位あたりの値段を、見せる単位 1 つあたりの値段の文に（安ければ小数も）。"""
    shown_price = price(good, value)
    if isinstance(shown_price, float) and not shown_price.is_integer():
        return f"{'-' if shown_price < 0 else ''}{currency}{abs(shown_price):,.1f}"
    return money_text(int(shown_price), currency)


def island_for_naming(row: m.Island) -> IslandState:
    """自動の施設名を作るのに要るところだけを持った島の状態（マスは読まない）。

    同じ種類の施設が同じ名前になるときは地名に番号を付ける（「オーパス第二銀行公社」）ので、施設の
    並びは読む（読まないと、どの施設も番号のない同じ名前になる）。
    """
    island = island_from_row(row)
    island.facilities = [facility_from_row(f) for f in row.facilities.order_by("number")]
    return island


def _firm_name(island: IslandState, defs: Any, f: Facility) -> str:
    """施設の属する企業の名前（回7。なければ ""）。"""
    number = f.economy.get(companies.FIRM)
    if number is None or companies.record(island, int(number)) is None:
        return ""
    return companies.display_name(island, defs, int(number))


def facility_info(row: m.Facility, island: IslandState, mine: bool = False) -> dict[str, Any]:
    """マスの欄の施設。だれにでも見える姿（種類・人手・稼働率・状態・在庫・補助金・閉鎖と差し押さえ）
    と、持ち主にだけ帳簿・借入・銀行の帳簿と、命令の入力に使う値（mine のとき）。"""
    game = get_game()
    defs, config = game.defs, game.config
    f: Facility = facility_from_row(row)
    fdef = defs.facilities.get(f.kind, {})
    info: dict[str, Any] = {
        "number": f.id,
        "kind": f.kind,
        "kind_name": defs.name("facilities", f.kind),
        "name": facility_name(defs, config, island, f) if fdef else f.kind,
        "custom_name": f.name,
        "owner": f.owner,
        "owner_name": message(f"owner_{f.owner}"),
        "accounting_name": message(f"accounting_{f.accounting}"),
        "firm_name": _firm_name(island, defs, f),
        "built": f.built,
        "workers": f.workers,
        "operating_rate": f.operating_rate / fac.FULL,
        "size": fac.size_of(f),  # 規模（段6 A3。建て増した数 + 1）
        "expanding": fac.pending_expansions(island, f),
        "priority": fac.priority_of(defs, config, f) if fdef else None,
        "priority_changed": f.priority is not None,
        "status": f.status,
        "status_text": status_text(f),
        "stock": [
            {
                "good": good,
                "name": defs.name("goods", good),
                "amount": amount(qty, good),
                "unit": defs.unit_name(good),
            }
            for good, qty in row.stocks.values("good")
            .annotate(qty=Sum("quantity"))
            .order_by("good")
            .values_list("good", "qty")
        ],
    }
    if not fdef:
        return info
    from server.web import monuments  # 記念碑の碑文は display を使うので、ここで読み込む

    note = monuments.detail(row.island, row)  # 記念碑なら、碑文と自作の絵
    if note is not None:
        info["monument"] = note
    recipes = fdef["recipes"]
    if len(recipes) > 1:
        info["recipe"] = f.recipe
        info["recipe_name"] = defs.recipe_name(f.kind, f.recipe)
    if f.kind == fac.housing_kind(config):
        level = f.ext.get("level") or defs.settlement_levels()[0]
        info["level"] = level
        info["level_name"] = defs.name("settlements", level)
        info["capacity"] = defs.settlements[level]["capacity"]
        # 町の商店（回7）: 入っている数と、町の段階で入る数
        info["shops"] = fac.shops_at(island, fac.position(f))
        info["shop_slots"] = fac.shop_slots(defs, f)
        levels = defs.settlement_levels()
        if levels.index(level) + 1 < len(levels):
            following = levels[levels.index(level) + 1]
            info["next_level_name"] = defs.name("settlements", following)
            info["next_materials"] = goods_list(defs.settlements[following].get("materials", {}))
        return info
    # 会計方式の変更の命令を出せる施設には、変えた先を添える（マスの欄はそのときだけ入力欄を出す）
    target = accounting_target(island, f) if mine else None
    if target is not None:
        info["accounting_target"] = target
        info["accounting_target_name"] = message(f"accounting_{target}")
    if f.economy.get("wage"):
        info["wage"] = f.economy["wage"]
    info.update(engineers_info(f, fdef))
    info.update(equipment_info(defs, config, f))
    if fdef.get("education"):
        info["education"] = education_text(fdef["education"], fac.size_of(f))
    if mine and f.built and len(recipes) > 1:
        # 生産方式の切り替え（フェーズ3-2。国営の施設だけ。次の市場の段で切り替わる）
        choices = recipe_choices(island, f)
        if choices:
            info["recipe_choices"] = choices
        pending = f.ext.get(fac.RECIPE_NEXT)
        if pending in recipes:
            info["recipe_next_name"] = defs.recipe_name(f.kind, pending)
    if f.built:
        info["workers_needed"] = fac.workers_of(defs, f)
        # 投入と産出は今の作り方で。畑を作り替えている途中なら、収穫している畑を混ぜた作り方（前の
        # 作物の畑の分も含む。蒔いて育っている畑は何も出さない）と、畑の内訳（Codex の試し遊び）
        recipe = fac.recipe_of(defs, f) if f.recipe in recipes else {}
        info["inputs"] = goods_list(recipe.get("inputs", {}))
        info["outputs"] = goods_list(recipe.get("outputs", {}))
        if fac.FIELDS in f.economy:
            info["transition"] = crop_transition(f)
    else:
        construction = fdef["construction"]
        info["workers_needed"] = construction.get("labor", 0)
        # 工事全体のうち進んだ割合（progress は 1000 で1ターン分）
        total = fac.total_progress(defs, f)
        info["progress"] = f.progress / total
        info["turns"] = -(-total // fac.FULL)
        if f.ext.get("repair"):
            info["repair"] = True  # 暴動で壊れて直している（フェーズ3-4）
        info["materials"] = goods_list(construction.get("materials", {}))
    if f.ext.get("halted_until") is not None:
        info["halted_until"] = f.ext["halted_until"]
    if is_private_site(f):
        info["private_site"] = True  # 民間の資本が自分のお金で建てている
    if f.built:
        currency = (island.names or {}).get("currency_symbol", "")
        if mine:  # 施設の帳簿は持ち主だけ（帳簿の API と施設の画面と同じ）
            info["books"] = facility_books(row, f, currency)
        if island.has_economy:
            info.update(facility_finance(row, f, island, currency, mine))
    return info


def crop_transition(f: Facility) -> dict[str, Any]:
    """畑を作り替えている途中の農場の畑の内訳: 前の作物の畑（割合・要る原料・取れる物）、蒔いて
    育てている畑（割合・収穫の始まるターン）と、作り替えが済む見込みのターン（蒔いた畑の最後の収穫。
    これから蒔く分は含まない）。"""
    defs = get_game().defs
    recipes = defs.facilities[f.kind]["recipes"]
    fields = [
        {
            "recipe": name,
            "recipe_name": defs.recipe_name(f.kind, name),
            "share": share / fac.FULL,
            "inputs": goods_list(recipes[name].get("inputs", {})),
            "outputs": goods_list(recipes[name].get("outputs", {})),
        }
        for name, share in sorted(f.economy[fac.FIELDS].items())
        if name != f.recipe and share > 0
    ]
    sown = [
        {
            "recipe_name": defs.recipe_name(f.kind, name),
            "share": share / fac.FULL,
            "ready_turn": turn,
            "ready_date": turn_text(turn),
        }
        for name, share, turn in sorted(f.economy.get(fac.SOWN, []), key=lambda row: row[2])
    ]
    parts = [
        f"{x['recipe_name']}の畑 {x['share'] * 100:.0f}%"
        + (f"（{'・'.join(g['name'] for g in x['inputs'])}が要る）" if x["inputs"] else "")
        for x in fields
    ]
    parts += [
        f"蒔いた{x['recipe_name']} {x['share'] * 100:.0f}%（{x['ready_date']}から収穫）"
        for x in sown
    ]
    return {
        "fields": fields,
        "sown": sown,
        "ready_turn": max((x["ready_turn"] for x in sown), default=None),
        "text": "作り替えの途中: " + "、".join(parts) if parts else "作り替えの途中",
    }


def equipment_info(defs: Any, config: Any, f: Facility) -> dict[str, Any]:
    """施設の設備の割合（回6。傷んだ分は作れない。1 が新品）。設備を持たない施設は出さない。"""
    if not equipment.has_equipment(defs, config, f):
        return {}
    return {"equipment": round(equipment.condition(f) / fac.FULL, 3)}


def engineers_info(f: Facility, fdef: Any) -> dict[str, Any]:
    """施設の技術者（フェーズ2-3）: 全力で動くのに要る数・雇えた数・技術者の席・賃金。

    技術者の要らない施設では、一般の仕事に回った技術者がいるときだけ出す。
    """
    out: dict[str, Any] = {}
    needed = fdef.get("staff", {}).get(fac.ENGINEER, 0) * fac.size_of(f)  # 規模の分（段6 A3）
    if needed:
        out["engineers_needed"] = needed
        out["engineers"] = f.economy.get("engineers", 0)
        out["engineers_wanted"] = f.economy.get("engineers_wanted", 0)
        if f.economy.get("engineer_wage"):
            out["engineer_wage"] = f.economy["engineer_wage"]
    if f.economy.get("engineers_general"):
        out["engineers_general"] = f.economy["engineers_general"]
    return out


def education_text(spec: Any, size: int = 1) -> str:
    """学校と大学の働き（定義の education）の文。稼働率を掛ける前の数（規模 size の分。段6 A3）。"""
    parts = []
    if spec.get("capacity"):
        parts.append(f"子どもの定員 {spec['capacity'] * size:,} 人")
    if spec.get("engineers"):
        parts.append(f"1ターンに技術者を {spec['engineers'] * size:,} 人育てる")
    return "・".join(parts)


def infra_view(infra_value: dict[str, Any], queue: list[list[Any]], x: int, y: int) -> dict:
    """マスのインフラ（フェーズ2-3）: 種類ごとの整備度と維持の目標（%）、並んでいる工事。

    infra_value はマスの infra の欄、queue は島の工事の列（[種類, x, y] の並び）。
    """
    defs = get_game().defs
    tile = _InfraTile(infra_value or {})
    levels = []
    for kind in defs.infrastructure:
        level, target = infra.level(tile, kind), infra.keep(tile, kind)
        if level or target:
            levels.append(
                {
                    "kind": kind,
                    "name": defs.name("infrastructure", kind),
                    "level": round(level * 100 / infra.FULL, 1),
                    "keep": round(target * 100 / infra.FULL, 1),
                }
            )
    works = [
        {"kind": kind, "name": defs.name("infrastructure", kind), "position": i + 1}
        for i, (kind, qx, qy) in enumerate(queue)
        if (qx, qy) == (x, y)
    ]
    return {"infra": levels, "works": works, "power_line": infra.carries_power(defs, tile)}


class _InfraTile:
    """インフラの読み書きの関数（hakoniwa_core.infra）に渡す、infra の欄だけを持ったマス。"""

    def __init__(self, value: dict[str, Any]) -> None:
        self.infra = value


def carries_power(infra_value: dict[str, Any]) -> bool:
    """マスの infra の欄が送電網として電力を通すか（地図の送電線の表示）。"""
    return infra.carries_power(get_game().defs, _InfraTile(infra_value or {}))


def infra_levels(infra_value: dict[str, Any]) -> dict[str, int]:
    """マスの種類ごとの整備度（%、整数）。地図の重ね表示に使う。"""
    tile = _InfraTile(infra_value or {})
    return {
        kind: round(infra.level(tile, kind) * 100 / infra.FULL)
        for kind in get_game().defs.infrastructure
    }


def works_why(island_economy: dict[str, Any]) -> str:
    """インフラの工事が止まっているわけ（公共事業費・人手・資材）の文。進んでいれば ""。"""
    why = (island_economy or {}).get("infra", {}).get("why")
    return message(f"infrastructure_stalled_{why}") if why else ""


# お金が足りないだけの確かめの失敗（命令は出せて、出したときに「国庫が足りません」と知らせる）
MONEY_SHORT = ("reason_treasury_short", "reason_owners_short")


def order_ok(
    island: IslandState, f: Facility, kind: str, payload: dict[str, Any], money: bool = True
) -> bool:
    """施設 f への命令がコアの確かめ（check_order）を通るか。money が False なら、お金が足りない
    だけの失敗は通ったことにする（命令の欄を出し、出したときにわけを見せる）。

    確かめは島の施設の並びからマスの施設を探すので、この施設だけを持たせた島で聞く。
    """
    game = get_game()
    probe = replace(island, facilities=[f])
    result = check_order(game.defs, game.registry, probe, kind, payload)
    return result.ok or (not money and result.reason in MONEY_SHORT)


def accounting_target(island: IslandState, f: Facility) -> str | None:
    """会計方式の変更の命令（set_accounting）で変えられる先の会計方式。命令が通らない施設は None。

    お金の経済が始まる前の島では会計方式に意味がないので出さない。銀行のお金はいつも銀行が持つので、
    国営銀行にも出さない。
    """
    if not island.has_economy or is_bank(get_game().config, f):
        return None
    target = ACCOUNTING_SELF if f.accounting == ACCOUNTING_TREASURY else ACCOUNTING_TREASURY
    if not order_ok(island, f, "set_accounting", {"x": f.x, "y": f.y, "accounting": target}):
        return None
    return target


def recipe_choices(island: IslandState, f: Facility) -> list[dict[str, Any]]:
    """生産方式の変更の命令（change_recipe）で切り替えられる先（今の方式と、切り替えを待っている
    方式を除く）。命令が通らない施設（民営・気候や地下資源で使えないなど）は空。"""
    defs = get_game().defs
    out = []
    for name, recipe in defs.facilities[f.kind]["recipes"].items():
        if name in (f.recipe, f.ext.get(fac.RECIPE_NEXT)):
            continue
        if not order_ok(island, f, "change_recipe", {"x": f.x, "y": f.y, "recipe": name}):
            continue
        out.append(
            {
                "recipe": name,
                "name": defs.recipe_name(f.kind, name),
                "inputs": goods_list(recipe.get("inputs", {})),
                "outputs": goods_list(recipe.get("outputs", {})),
            }
        )
    return out


def ownership_target(island: IslandState, f: Facility) -> str | None:
    """出せる持ち主の変更の命令（nationalize・privatize）。どちらも通らなければ None。"""
    if f.x < 0 or f.y < 0:
        return None
    for kind in ("nationalize", "privatize"):
        if order_ok(island, f, kind, {"x": f.x, "y": f.y}, money=False):
            return kind
    return None


def facility_finance(
    row: m.Facility, f: Facility, island: IslandState, currency: str = "", mine: bool = False
) -> dict[str, Any]:
    """施設の閉鎖・差し押さえ・補助金（だれにでも）と、借入・銀行の帳簿・出せる命令（フェーズ2-2。
    持ち主だけ。mine のとき）。"""
    game = get_game()
    config = game.config
    out: dict[str, Any] = {}
    if f.economy.get("closing") is not None:
        out["closing"] = f.economy["closing"]  # 閉鎖に入ったターン
    seized = f.economy.get("foreclosed")
    if seized:
        until = seized.get("since", 0) + config.get("firms.foreclosure_turns")
        out["foreclosed"] = {
            "bank": bank_name(row.island_id, island, seized.get("bank")),
            "since": seized.get("since"),
            "until": until,
            "price": money_text(seized.get("price", 0), currency),
        }
    if is_self_supporting(f) and not is_bank(config, f):
        # 補助金は独立採算の施設にだけ出る（銀行を除く）。出ている補助金は公表された政策
        recipe = game.defs.facilities[f.kind]["recipes"].get(f.recipe, {})
        outputs = recipe.get("outputs", {})
        out["subsidies"] = [
            subsidy_text(s)
            for s in island.policy.subsidies
            if (s.get("x"), s.get("y")) == (f.x, f.y)
            or (s["kind"] == "production" and s.get("good") in outputs)
        ]
        if mine:
            out["subsidy_target"] = True
    if not mine:
        return out
    loans = facility_loans(row, island, currency)
    if loans:
        out["loans"] = loans
    target = ownership_target(island, f)
    if target is not None:
        out["ownership_target"] = target
        out["ownership_target_name"] = game.defs.name("orders", target)
    if is_bank(config, f):
        out["bank"] = bank_view(row, f, island, currency)
        if order_ok(island, f, "emergency_loan", {"x": f.x, "y": f.y, "amount": 0}):
            out["emergency_loan"] = True
        resolve = {"x": f.x, "y": f.y, "method": RESOLUTION_METHODS[0]}
        if order_ok(island, f, "resolve_bank", resolve):
            out["resolve_methods"] = {
                method: message(f"resolution_{method}") for method in RESOLUTION_METHODS
            }
    return out


def facility_loans(
    row: m.Facility, island: IslandState, currency: str = ""
) -> list[dict[str, Any]]:
    """施設の借入（融資ごとの貸し手・使い道・残高・金利・遅れ）。"""
    borrower = facility_entity(row.number)
    loans = list(Loan.objects.filter(island_id=row.island_id, borrower=borrower).order_by("number"))
    if not loans:
        return []
    names = [ledger.account(x.lender, ledger.loan_item(x.number), borrower) for x in loans]
    balances = dict(
        Account.objects.filter(island_id=row.island_id, name__in=names).values_list(
            "name", "balance"
        )
    )
    return [
        {
            "lender": bank_name(row.island_id, island, x.lender),
            "purpose": message(f"loan_{x.purpose}"),
            "balance": money_text(balances.get(name, 0), currency),
            "rate": rate_text(x.rate),
            "arrears": x.arrears,
        }
        for x, name in zip(loans, names, strict=True)
    ]


def bank_name(island_id: int, island: IslandState, bank: str | None) -> str:
    """銀行の主体（bank:<番号>）の表示名。施設が残っていなければ「銀行」。"""
    game = get_game()
    if not bank or not ledger.is_bank(bank):
        return game.defs.name("facilities", game.config.get("banking.facility"))
    row = m.Facility.objects.filter(island_id=island_id, number=int(bank.split(":")[1])).first()
    if row is None:
        return game.defs.name("facilities", game.config.get("banking.facility"))
    return facility_name(game.defs, game.config, island, facility_from_row(row))


def bank_view(row: m.Facility, f: Facility, island: IslandState, currency: str = "") -> dict:
    """銀行の預金・融資・自己資本と比率（直近の統計）、預金金利、破綻、緊急の貸し出し。"""
    config = get_game().config
    prefix = f"bank.{f.id}."
    stats = m.StatRecord.objects.filter(island_id=row.island_id, name__startswith=prefix)
    turn = stats.order_by("-turn").values_list("turn", flat=True).first()
    values = {
        name.removeprefix(prefix): value
        for name, value in stats.filter(turn=turn).values_list("name", "value")
    }
    out: dict[str, Any] = {
        "deposit_rate": rate_text(f.economy.get("deposit_rate", island.policy.deposit_rate)),
        "regulatory_ratio": f"{config.get('banking.regulatory_ratio') * 100:g}%",
    }
    if turn is not None:
        out["turn"] = turn
        for key in ("deposits", "loans", "net_worth"):
            if key in values:
                out[key] = money_text(values[key], currency)
        ratio = values.get("capital_ratio")
        out["capital_ratio"] = f"{ratio * 100:.1f}%" if ratio is not None else ""
    failed = f.economy.get("failed")
    if failed is not None:
        out["failed"] = failed
        out["failed_reason"] = f.economy.get("failed_reason") or (
            "liquidity" if f.economy.get("liquidity_failure") == failed else "capital"
        )
    emergency = island.economy.finance.get("emergency", {}).get(ledger.bank_entity(f.id))
    if emergency:
        out["emergency"] = money_text(emergency, currency)
    # 国債の債務不履行で損をして、新しい融資を止めている最後のターン（次のターンもそうなら）
    until = f.economy.get("crunch_until")
    if until is not None and failed is None and until - 1 > row.island.world.turn:
        out["crunch_until"] = until - 1
    return out


def rate_text(rate: int) -> str:
    """1万分の1の率を % の文にする。"""
    return f"{rate * 100 / BASIS:.2f}%"


def subsidy_text(s: dict[str, Any]) -> str:
    """補助金の1件（policy.subsidies）の文。"""
    return (
        f"{message('subsidy_' + s['kind'])}・{_subsidy_target(s)} "
        f"{s.get('rate', 0) * 100 / BASIS:g}%、{_until(s.get('until'))}"
    )


def _until(turn: Any) -> str:
    """「〜まで」の文。ターンの数がなければ「ターン None まで」のように崩さない。"""
    return f"{turn_text(turn)}まで" if isinstance(turn, int) else f"ターン {turn} まで"


def _subsidy_target(s: dict[str, Any]) -> str:
    """補助金の対象（財と施設のマス）。"""
    parts = []
    if s.get("good") is not None:
        parts.append(get_game().defs.name("goods", s["good"]))
    if s.get("x") is not None and s.get("y") is not None:
        parts.append(f"({s['x']}, {s['y']}) の施設")
    return " ".join(parts)


# マスの欄に出す施設の収支（hakoniwa_core/economy/books.py の項目、表示名、収入なら 1・支出なら -1）
BOOK_ITEMS = [
    (books.SALES, "売上", 1),
    (books.PURCHASES, "仕入れ", -1),
    (books.ELECTRICITY, "電気代", -1),
    (books.FREIGHT, "運賃", -1),
    (books.INVENTORY, "在庫の増減", 1),
    (books.WAGES, "賃金", -1),
    (books.SUBSIDY, "補助金", 1),
    (books.INTEREST, "利子（受け取り − 支払い）", 1),
    (books.REALIZED, "国債の売却・償還の損益", 1),
    (books.WRITE_OFF, "貸し倒れ", -1),
    (books.DEPRECIATION, "減価償却", -1),
    (books.PROFIT, "損益", 1),
    (books.TAX, "法人税", -1),
    (books.DIVIDEND, "配当・国庫への納付", -1),
    (books.VALUATION, "国債の評価損益（売るまで損益に入れず、自己資本にだけ数える）", 1),
]
BOOK_TURNS = 10  # 損益の合計を出すターン数


def money_text(amount: int, currency: str = "") -> str:
    return f"{'-' if amount < 0 else ''}{currency}{abs(int(amount)):,}"


def facility_books(row: m.Facility, f: Facility, currency: str = "") -> dict[str, Any] | None:
    """施設の直近のターンの収支と預金・借入、直近 BOOK_TURNS ターンの損益の合計。

    帳簿がまだなければ（完成してから決算を迎えていなければ）None。
    """
    recent = list(
        FacilityBook.objects.filter(island_id=row.island_id, facility=f.id).order_by("-turn")[
            :BOOK_TURNS
        ]
    )
    if not recent:
        return None
    last = recent[0]
    rows = [
        {"label": label, "text": money_text(sign * last.items[key], currency)}
        for key, label, sign in BOOK_ITEMS
        if last.items.get(key)
    ]
    out: dict[str, Any] = {
        "turn": last.turn,
        "rows": rows,
        "profit": last.items.get(books.PROFIT, 0),
        "turns": len(recent),
        "profit_total": money_text(sum(b.items.get(books.PROFIT, 0) for b in recent), currency),
    }
    # 国庫直轄の施設は自分の預金を持たない（お金は国庫が出し入れする）
    config = get_game().config
    if entity_of(config, f) != ledger.GOV:
        out["deposit"] = money_text(last.deposit, currency)
        # 銀行のすぐに払えるお金は、中央銀行に預けた準備預金
        out["deposit_label"] = "準備預金" if is_bank(config, f) else "預金"
    if last.debt:
        out["debt"] = money_text(last.debt, currency)
    return out


# 施設の ext["halt_reason"]（止まったわけ）のうち、状態の文を分けるもの。暴動は施設の状態が
# 「riot」になる（status_riot）ので、ここには入れない
HALT_REASONS = ("war", "missile", "occupied")


def status_text(f: Facility) -> str:
    if not f.built:
        # 民営の工事でお金が足りないのは、公共事業費ではなく会社の資金
        private = "_private" if f.ext.get("private_entry") else ""
        if f.ext.get("waiting"):  # 資材が足りない（市場に売りに出ていない、お金が足りないなど）
            key = "funds" + private if f.ext.get("short_funds") else "materials"
            return message(f"status_construction_{key}", goods=f.ext["waiting"])
        if f.ext.get("short_wages"):  # 公共事業費（民営は会社の資金）が足りず、作業員を雇いきれない
            return message(f"status_construction_wages{private}")
        if f.ext.get("no_workers_turns"):
            return message("status_construction_no_workers")
        if f.ext.get("repair"):  # 暴動で壊れて直している（フェーズ3-4）
            return message("status_repair")
        return message("status_construction")
    if not f.status:
        return ""
    kind, _, good = f.status.partition(":")
    reason = f.ext.get("halt_reason")
    # 戦争・ミサイル・占領で止まったのは災害ではない
    if kind == "halted" and reason in HALT_REASONS:
        text = message(f"halt_reason_{reason}")
    else:
        text = message(f"status_{kind}")
    if good:
        text += f"（{get_game().defs.name('goods', good)}）"
    return text


def goods_list(amounts: dict[str, int]) -> list[dict[str, Any]]:
    defs = get_game().defs
    return [
        {
            "good": g,
            "name": defs.name("goods", g),
            "amount": amount(q, g),
            "unit": defs.unit_name(g),
        }
        for g, q in sorted(amounts.items())
    ]


# 命令の中身のうち、財の量と 1 単位あたりの値段の項目（W-30）。
# 種類 → (財の項目, 量の項目, 値段の項目)
ORDER_UNITS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "set_price_cap": ("good", (), ("price",)),
    "warehouse_trade": ("good", ("quantity",), ("limit",)),
    "propose_contract": ("good", ("quantity",), ("price",)),
    "set_export_subsidy": ("good", (), ("amount",)),
    "set_import_quota": ("good", ("quantity",), ()),
    "send_aid": ("good", ("quantity",), ()),
    "bloc_settings": ("fixed_good", (), ("fixed_price",)),
    "set_official_price": ("good", (), ("price",)),
    "set_price_floor": ("good", (), ("price",)),
    "load_supplies": ("good", ("quantity",), ()),
}
# 項目の下の配列の各要素に、同じ形で量と値段を持つ命令（W-182）。種類 → (配列の項目, 要素の形)。
# 条約の条項のうち契約だけが財を持つ（財のない条項は、財の項目が文字列でないので素通りする）
CLAUSE_UNITS = ("good", ("quantity",), ("price",))
ORDER_NESTED_UNITS: dict[str, tuple[str, tuple[str, tuple[str, ...], tuple[str, ...]]]] = {
    "propose_treaty": ("clauses", CLAUSE_UNITS),
}
# 項目の下に「財 → 量」の表を持つ命令（W-182）。種類 → 項目。備蓄の目標（food などの補給も財）
ORDER_GOOD_MAPS: dict[str, str] = {"set_defense": "reserve"}


def order_has_units(kind: str) -> bool:
    """命令の中身に、見せる単位で受け取る量か値段があるか。"""
    return kind in ORDER_UNITS or kind in ORDER_NESTED_UNITS or kind in ORDER_GOOD_MAPS


def _plain_number(value: float) -> float:
    return int(value) if float(value).is_integer() else value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unit_value(value: Any, scale: float, is_price: bool, to_game: bool) -> Any:
    """量か値段 1 つを、見せる単位と計算の単位のあいだで直す。数でない値はそのまま。"""
    if not _is_number(value):
        return value
    if is_price:
        return round(value * scale) if to_game else _plain_number(round(value / scale, 6))
    return round(value / scale, 9) if to_game else _plain_number(round(value * scale, 6))


def _flat_units(
    payload: Any, spec: tuple[str, tuple[str, ...], tuple[str, ...]], to_game: bool
) -> Any:
    """財の項目と量・値段の項目が同じ階層にある中身を直す。形の違う中身はそのまま返す。"""
    if not isinstance(payload, dict):
        return payload
    defs = get_game().defs
    good_key, qty_keys, price_keys = spec
    good = payload.get(good_key)
    if not isinstance(good, str) or good not in defs.goods:
        return payload
    scale = defs.display_scale(good)
    out = dict(payload)
    for key in qty_keys + price_keys:
        if key in out:
            out[key] = _unit_value(out[key], scale, key in price_keys, to_game)
    return out


def shown_clause(clause: dict[str, Any]) -> dict[str, Any]:
    """保存された条約の条項（計算の単位）の量と値段を、見せる単位に直す（W-182）。"""
    return _flat_units(clause, CLAUSE_UNITS, to_game=False)


def order_payload(kind: Any, payload: Any, to_game: bool) -> Any:
    """命令の中身の量と値段を、見せる単位と計算の単位のあいだで直す（API の出入り口。W-30・W-182）。

    to_game なら打ち込まれた中身（見せる単位）を計算の単位に、そうでなければ計算の単位の中身を見せる
    単位に。丸めないので、命令の列を読んでそのまま送り返しても同じ命令になる（値段は計算の単位の
    整数に丸めて受け取る。倍率 1 の財でも）。形の違う中身はそのまま返し、確かめ（check_order）に
    任せる。どの命令のどの項目を直すかは ORDER_UNITS・ORDER_NESTED_UNITS・ORDER_GOOD_MAPS
    （一覧に載っていない命令は直さないので、量か値段を持つ命令を足したら一覧にも足す。
    tests/server/test_display_units.py が見つける）。
    """
    if not isinstance(kind, str) or not isinstance(payload, dict):
        return payload
    out = payload
    if kind in ORDER_UNITS:
        out = _flat_units(out, ORDER_UNITS[kind], to_game)
    if kind in ORDER_NESTED_UNITS:
        key, spec = ORDER_NESTED_UNITS[kind]
        if isinstance(out.get(key), list):
            out = {**out, key: [_flat_units(item, spec, to_game) for item in out[key]]}
    if kind in ORDER_GOOD_MAPS:
        key = ORDER_GOOD_MAPS[kind]
        table = out.get(key)
        if isinstance(table, dict):
            defs = get_game().defs
            out = {
                **out,
                key: {
                    good: _unit_value(q, defs.display_scale(good), False, to_game)
                    if good in defs.goods
                    else q
                    for good, q in table.items()
                },
            }
    return out


def describe_order(kind: str, payload: dict[str, Any]) -> str:
    """命令の列に出す、命令の中身の短い説明（量と値段は見せる単位で）。"""
    defs = get_game().defs
    stored = payload  # 条約の条項の説明（clause_summary）は、計算の単位の条項を受け取る
    payload = order_payload(kind, payload, to_game=False)
    name = defs.name("orders", kind)
    where = f"({payload['x']}, {payload['y']})" if "x" in payload and "y" in payload else ""
    if kind == "build":
        facility = defs.name("facilities", payload.get("facility", ""))
        recipe = payload.get("recipe")
        if recipe and payload.get("facility") in defs.facilities:
            facility += f"・{defs.recipe_name(payload['facility'], recipe)}"
        return f"{name}: {facility} {where}"
    if kind == "build_infrastructure":
        kind_name = defs.name("infrastructure", payload.get("kind", ""))
        return f"{name}: {kind_name} {len(payload.get('tiles', []))} マス"
    if kind in ("build_power_line", "set_no_development"):
        count = len(payload.get("tiles", []))
        if kind == "set_no_development" and not payload.get("value", True):
            return f"{name}の解除: {count} マス"
        return f"{name}: {count} マス"
    if kind == "set_priority":
        return f"{name}: {where} → {payload.get('priority')}"
    if kind == "set_accounting":
        return f"{name}: {where} → {message('accounting_' + payload.get('accounting', ''))}"
    if kind == "change_recipe":
        # 命令には施設の種類がないので、その名前の生産方式を持つ施設から表示名を引く
        recipe = payload.get("recipe", "")
        owner = next((k for k, d in defs.facilities.items() if recipe in d["recipes"]), None)
        shown = defs.recipe_name(owner, recipe) if owner else recipe
        return f"{name}: {where} → {shown}"
    if kind in ("rename_island", "rename_facility", "rename_place"):
        new = payload.get("name", "")
        return f"{name}: {where} 「{new}」" if where else f"{name}: 「{new}」"
    if kind in ("set_taxes", "set_labor_policy", "set_budget"):
        parts = []
        for k, v in payload.items():
            label = message(_POLICY_LABELS.get(k, f"budget_{k}"))
            parts.append(f"{label} {v:g}%" if k in _PERCENT else f"{label} {v:,}")
        return f"{name}: " + "・".join(parts)
    if kind == "warehouse_trade":
        good = payload.get("good", "")
        side = message("side_" + payload.get("side", ""))
        qty = f"{payload.get('quantity', 0):g} {defs.unit_name(good)}" if good in defs.goods else ""
        limit = f"（指値 {payload['limit']:,}）" if payload.get("limit") else ""
        return f"{name}: {defs.name('goods', good)}を{side} {qty}{limit}"
    if kind in ("nationalize_firm", "privatize_firm"):
        return f"{name}: 企業の番号 {payload.get('firm')}"
    if kind == "set_price_cap":
        good = payload.get("good", "")
        return f"{name}: {defs.name('goods', good)} {payload.get('price', 0):,}"
    text = (
        _describe_finance(kind, payload, name, where)
        or _describe_trade(kind, payload, name)
        or _describe_politics(kind, payload, name)
        or _describe_diplomacy(kind, stored, name)
        or _describe_military(kind, payload, name)
    )
    if text:
        return text
    return f"{name} {where}".strip()


def _describe_military(kind: str, payload: dict[str, Any], name: str) -> str:
    """兵の命令（フェーズ4-2）の説明。ほかの命令は ""。"""
    if kind in ("recruit", "conscript", "discharge"):
        return f"{name}: {payload.get('count', 0):,} 人"
    if kind == "form_unit":
        defs = get_game().defs
        army = payload.get("army", "")
        label = defs.name("army", army) if army in defs.army else army
        return f"{name}: {label}（兵舎 {payload.get('x')}, {payload.get('y')}）"
    if kind == "move_unit":
        return f"{name}: 部隊 {payload.get('unit')} 番 → ({payload.get('x')}, {payload.get('y')})"
    if kind in ("disband_unit", "dig_trench", "stop_unit"):
        return f"{name}: 部隊 {payload.get('unit')} 番"
    if kind == "set_defense":
        parts = []
        if payload.get("annual") is not None:
            share = payload.get("personnel_share", 100)
            parts.append(f"年 {payload['annual']:,}（兵の分 {share}%）")
        if payload.get("volunteer_wage") is not None:
            parts.append(f"志願の賃金 {payload['volunteer_wage']:g}%")
        return f"{name}: " + "・".join(parts)
    return ""


def _describe_diplomacy(kind: str, payload: dict[str, Any], name: str) -> str:
    """外交の命令（フェーズ4-1）の説明。ほかの命令は ""。島は番号から名前にする。"""
    if kind == "declare_war":
        text = f"{name}: {island_name(payload.get('target'))}"
        return f"{text}（{payload['reason']}）" if payload.get("reason") else text
    if kind == "propose_treaty":
        kinds = "・".join(clause_summary(c) for c in payload.get("clauses", []))
        title = f"「{payload['title']}」" if payload.get("title") else ""
        return f"{name}: {island_name(payload.get('partner'))} {title}{kinds}"
    if kind in ("sign_treaty", "refuse_treaty", "withdraw_treaty", "denounce_treaty"):
        return f"{name}: 条約 {payload.get('treaty')} 番"
    return ""


def clause_summary(c: dict[str, Any]) -> str:
    """条約の条項（命令の中身か、保存された条項。どちらも計算の単位）を 1 つの文にする。経済の条項
    （フェーズ4-1 の②）は中身（誰が・何を・いくら）まで書く。島と通貨は番号から名前にする。契約の
    量と値段は見せる単位に直して書く（W-182）。"""
    kind = c.get("type")
    label = message(f"clause_{kind}")
    if kind == "bloc":
        if c.get("bloc") is not None:
            return f"{label}（{_bloc_name(c['bloc'])}に相手を入れる）"
        return f"{label}（「{c.get('name', '')}」をつくる）"
    if kind == "contract":
        defs = get_game().defs
        good = c.get("good", "")
        unit = defs.unit_name(good) if good in defs.goods else ""
        goods_name = defs.name("goods", good) if good in defs.goods else good
        currency = currency_name(c["currency"]) if "currency" in c else "売る国の通貨"
        shown = shown_clause(c)
        quantity, price = shown.get("quantity", 0), shown.get("price", 0)
        return (
            f"{label}（{island_name(c.get('seller'))}が{goods_name}を毎ターン "
            f"{quantity:g} {unit}、1{unit} {price:,}（{currency}）で {c.get('turns', 0)} ターン）"
        )
    if kind == "peg":
        return (
            f"{label}（{island_name(c.get('pegger'))}が相手の通貨 1 を"
            f" {c.get('rate', 0):g} に固定）"
        )
    if kind == "aid":
        currency = currency_name(c["currency"]) if "currency" in c else "払う国の通貨"
        return (
            f"{label}（{island_name(c.get('payer'))}が{currency} {c.get('amount', 0):,} を"
            f" {c.get('period', 1)} ターンごとに {c.get('count', 1)} 回）"
        )
    if kind == "refugees":
        return f"{label}（{island_name(c.get('host'))}が受け入れる）"
    return label


def _describe_trade(kind: str, payload: dict[str, Any], name: str) -> str:
    """貿易の命令（フェーズ3-1）の説明。ほかの命令は ""。島と通貨は番号から名前にする。"""
    defs = get_game().defs
    bloc = _bloc_name(payload["bloc"]) if "bloc" in payload else ""
    if kind == "create_bloc":
        entry = "誰でも入れる" if payload.get("open", True) else "加盟は承認制"
        return f"{name}: 「{payload.get('name', '')}」（{entry}）"
    if kind in ("join_bloc", "leave_bloc", "take_over_bloc"):
        return f"{name}: {bloc}"
    if kind == "bloc_settings":
        parts = []
        if "open" in payload:
            parts.append("誰でも入れる" if payload["open"] else "加盟は承認制")
        if "currency" in payload:
            parts.append(f"決済通貨を{currency_name(payload['currency'])}に")
        if "approve" in payload:
            parts.append(f"{island_name(payload['approve'])}の加盟を承認")
        if "no_tariff" in payload:
            parts.append("圏内の関税をなくす" if payload["no_tariff"] else "圏内の関税を認める")
        if "fixed_good" in payload:
            good = payload["fixed_good"]
            price = payload.get("fixed_price", 0)
            unit = defs.unit_name(good) if good in defs.goods else ""
            fixed = f"1{unit}あたり {price:,} に固定" if price else "の固定をやめる"
            parts.append(f"{defs.name('goods', good)}の値段を{fixed}")
        return f"{name}: {bloc} " + "・".join(parts)
    if kind == "transfer_bloc":
        return f"{name}: {bloc} → {island_name(payload.get('to'))}"
    if kind == "propose_contract":
        good = payload.get("good", "")
        unit = defs.unit_name(good) if good in defs.goods else ""
        side = SIDES.get(payload.get("side", ""), "")
        currency = currency_name(payload["currency"]) if "currency" in payload else "自国通貨"
        delivery = DELIVERIES.get(payload.get("delivery", ""), "")
        return (
            f"{name}: {island_name(payload.get('partner'))}に{defs.name('goods', good)}を"
            f"毎ターン {payload.get('quantity', 0):g} {unit}{side}"
            f"（1{unit} {payload.get('price', 0):,}・{currency}・{payload.get('turns', 0)} ターン"
            f"{'・' + delivery if delivery else ''}）"
        )
    if kind == "accept_contract" and payload.get("delivery") in DELIVERIES:
        return f"{name}: 契約 {payload.get('contract')} 番（{DELIVERIES[payload['delivery']]}）"
    if kind in ("accept_contract", "cancel_contract"):
        return f"{name}: 契約 {payload.get('contract')} 番"
    return _describe_policy(kind, payload, name)


def _describe_policy(kind: str, payload: dict[str, Any], name: str) -> str:
    """産業政策と国際金融の命令（フェーズ3-3）の説明。ほかの命令は ""。"""
    defs = get_game().defs
    good = payload.get("good", "")
    good_name = defs.name("goods", good) if good else ""
    unit = defs.unit_name(good) if good in defs.goods else ""
    if kind == "set_tariff":
        rate = payload.get("rate", 0)
        return f"{name}: {good_name} {rate:g}%" if rate else f"{name}をやめる: {good_name}"
    if kind == "set_export_subsidy":
        amount = payload.get("amount", 0)
        if not amount:
            return f"{name}をやめる: {good_name}"
        return f"{name}: {good_name} 1{unit}あたり {amount:,}"
    if kind == "set_import_quota":
        if "quantity" not in payload:
            return f"{name}をやめる: {good_name}"
        return f"{name}: {good_name} 1ターン {payload['quantity']:g} {unit}まで"
    if kind == "set_embargo":
        what = good_name or "すべての財"
        verb = "やめる" if payload.get("lift") else "始める"
        return f"{name}を{verb}: {island_name(payload.get('partner'))}（{what}）"
    if kind == "send_aid":
        partner = island_name(payload.get("partner"))
        if good:
            return (
                f"{name}: {partner}に{good_name}を毎ターン {payload.get('quantity', 0):g} {unit}"
                f"（{payload.get('turns', 0)} ターン）"
            )
        currency = currency_name(payload["currency"]) if "currency" in payload else "自国通貨"
        return f"{name}: {partner}に {payload.get('amount', 0):,}（{currency}）"
    if kind == "set_capital_controls":
        return f"{name}: {CAPITAL_CONTROLS.get(payload.get('mode', ''), '')}"
    if kind in ("set_fx_intervention", "set_foreign_bonds"):
        amount = payload.get("amount", 0)
        if kind == "set_fx_intervention":
            target = currency_name(payload.get("currency"))
        else:
            holder = HOLDERS.get(payload.get("holder", ""), "")
            target = f"{holder}が{island_name(payload.get('issuer'))}の国債"
        if not amount:
            return f"{name}をやめる: {target}"
        side = "買う" if amount > 0 else "売る"
        return f"{name}: {target}を毎ターン {abs(amount):,} {side}"
    if kind == "set_fx_peg":
        currency = currency_name(payload.get("currency"))
        rate = payload.get("rate", 0)
        if not rate:
            return f"{name}をやめる: {currency}"
        return f"{name}: {currency} 1 = 自国通貨 {rate:g}"
    return ""


def _describe_politics(kind: str, payload: dict[str, Any], name: str) -> str:
    """政治と計画の命令（フェーズ3-4）の説明。ほかの命令は ""。"""
    defs = get_game().defs
    if kind == "set_press":
        return f"{name}: {message('press_' + payload.get('mode', ''))}"
    if kind == "set_immigration":
        text = f"{name}: {message('immigration_' + payload.get('mode', ''))}"
        if payload.get("cap"):
            text += f"（1年に住民の {payload['cap'] / 100:g}% まで）"
        return text
    if kind == "set_emigration":
        text = f"{name}: {message('emigration_' + payload.get('mode', ''))}"
        if payload.get("mode") == "permit":
            text += f"（1年に住民の {payload.get('quota', 0) / 100:g}% まで）"
        return text
    if kind == "amend_constitution":
        chosen = [payload[a] for a in ("politics", "head_of_state") if a in payload]
        return f"{name}: " + "・".join(defs.name("regimes", r) for r in chosen)
    if kind == "change_economy":
        return f"{name}: {defs.name('regimes', payload.get('economy', ''))}"
    if kind == "mobilize_labor":
        return f"{name}を{'始める' if payload.get('on') else 'やめる'}"
    if kind == "rename_leader":
        return f"{name}: 「{payload.get('name', '')}」"
    if kind in ("set_official_price", "set_price_floor"):
        good = defs.name("goods", payload.get("good", ""))
        price = payload.get("price", 0)
        if not price:
            return (
                f"{name}: {good}を{'上書きをやめる' if kind == 'set_official_price' else 'なくす'}"
            )
        return f"{name}: {good} {price:,}"
    if kind == "review_official_prices":
        goods = [defs.name("goods", g) for g in payload.get("goods", [])]
        return f"{name}: " + ("・".join(goods) or "命令で決めた値段のない財すべて")
    if kind == "set_official_price_rules":
        parts = []
        if "band" in payload:
            parts.append(f"見直す幅 ±{payload['band']}%")
        if "held" in payload:
            held = "・".join(defs.name("goods", g) for g in payload["held"]) or "なし"
            parts.append(f"据え置く財 {held}")
        return f"{name}: " + "、".join(parts)
    if kind == "set_priority_industries":
        names = [defs.name("industries", i) for i in payload.get("industries", [])]
        return f"{name}: " + ("・".join(names) or "なし")
    return ""


CAPITAL_CONTROLS = {
    "free": "自由",
    "official": "政府と中央銀行だけ",
    "closed": "禁止",
}
HOLDERS = {"gov": "政府", "cb": "中央銀行"}


SIDES = {"sell": "売る", "buy": "買う"}
# 契約で届いた物の行き先（hakoniwa_core/trade/contracts.py）
DELIVERIES = {"market": "届いた物は市場に出す", "warehouse": "届いた物は国の倉庫に置く"}


def _bloc_name(bloc_id: Any) -> str:
    """貿易圏の名前（世界の貿易の状態 World.ext["trade"] から）。見つからなければ番号。"""
    world = m.World.objects.order_by("id").first()
    blocs = ((world.ext or {}).get(TRADE_KEY) or {}).get("blocs", []) if world else []
    for b in blocs:
        if b.get("id") == bloc_id:
            return f"「{b.get('name', '')}」"
    return f"貿易圏 {bloc_id} 番"


def island_name(island_id: Any) -> str:
    row = m.Island.objects.filter(pk=island_id).values_list("name", flat=True).first()
    return row if row is not None else f"島 {island_id}"


def currency_name(island_id: Any) -> str:
    """通貨（番号は島の番号）の名前。同じ名前の通貨がほかの島にもあれば国名を前に付ける。島が
    なければ番号。"""
    names = {
        i: (name, (names or {}).get("currency", name))
        for i, name, names in m.Island.objects.values_list("id", "name", "names")
    }
    try:
        key = int(island_id)
    except (TypeError, ValueError):
        key = None
    if key not in names:
        return f"通貨 {island_id}"
    return currency_labels(names)[key]


def _describe_finance(kind: str, payload: dict[str, Any], name: str, where: str) -> str:
    """銀行・国債・企業の命令（フェーズ2-2）の説明。ほかの命令は ""。"""
    defs = get_game().defs
    if kind == "set_zone":
        count = len(payload.get("tiles", []))
        if payload.get("zone") == "none":
            return f"{name}の解除: {count} マス"
        return f"{name}: {message('zone_' + payload.get('zone', ''))} {count} マス"
    if kind == "set_subsidy":
        subsidy = f"{message('subsidy_' + payload.get('kind', ''))}・{_subsidy_target(payload)}"
        if not payload.get("rate"):
            return f"{name}: {subsidy}をやめる"
        return f"{name}: {subsidy} {payload['rate']:g}% {payload.get('turns', 0)} ターン"
    if kind == "set_monetary_policy":
        parts = []
        if "policy_rate" in payload:
            parts.append(f"政策金利 {payload['policy_rate']:g}%")
        if "bond_trade" in payload:
            parts.append(bond_trade_text(payload["bond_trade"]))
        return f"{name}: " + "・".join(parts)
    if kind == "set_central_bank_independence":
        return f"{name}: " + ("独立させる" if payload.get("independent") else "政府の下に戻す")
    if kind == "set_bond_policy":
        parts = []
        if payload.get("unsold"):
            parts.append(f"売れ残りは{message('bond_unsold_' + payload['unsold'])}")
        for key, label in (("issue", "追加の入札"), ("underwrite", "中央銀行の直接の引き受け")):
            if payload.get(key):
                term = payload.get(f"{key}_term")
                term_text = f"（{defs.name('finance', term)}）" if term else ""
                parts.append(f"{label} {payload[key]:,}{term_text}")
        terms = payload.get("terms") or {}
        total = sum(terms.values())
        if total > 0:
            shares = "、".join(
                f"{defs.name('finance', t)} {share * 100 / total:.0f}%"
                for t, share in terms.items()
                if share
            )
            parts.append(f"期間の割合（{shares}）")
        return f"{name}: " + "・".join(parts) if parts else name
    if kind == "set_bond_payment":
        return f"{name}: " + ("止める（債務不履行）" if payload.get("stop") else "再開する")
    if kind == "set_bank_policy":
        parts = []
        if payload.get("entry"):
            parts.append(f"民間銀行の参入 {message('bank_entry_' + payload['entry'])}")
        if "deposit_insurance" in payload:
            parts.append(f"預金保護の上限 {insurance_text(payload['deposit_insurance'])}")
        return f"{name}: " + "・".join(parts)
    if kind == "set_retail_policy":
        parts = []
        if payload.get("large_store"):
            parts.append(f"百貨店の出店 {message('large_store_' + payload['large_store'])}")
        if payload.get("approve"):
            parts.append("出店の申請を許す")
        return f"{name}: " + "・".join(parts)
    if kind == "emergency_loan":
        amount = payload.get("amount", 0)
        return f"{name}: {where} " + (f"上限 {amount:,}" if amount else "取り消し")
    if kind == "resolve_bank":
        return f"{name}: {where} {message('resolution_' + payload.get('method', ''))}"
    return ""


def bond_trade_text(amount: int) -> str:
    """中央銀行が毎ターン国債を売り買いする額（マイナスは売る）の文。"""
    if amount > 0:
        return f"中央銀行が国債を毎ターン {amount:,} 買う"
    if amount < 0:
        return f"中央銀行が国債を毎ターン {-amount:,} 売る"
    return "中央銀行の国債の売買なし"


def insurance_text(limit: int) -> str:
    """預金保護の上限額（-1 は無制限）の文。"""
    return "無制限" if limit < 0 else f"1人あたり {limit:,}"


_PERCENT = ("income_tax", "corporate_tax", "consumption_tax", "unemployment_benefit")
# 政策の命令の中身の項目の表示名（messages.yaml のキー）
_POLICY_LABELS = {
    "income_tax": "policy_income_tax",
    "corporate_tax": "policy_corporate_tax",
    "consumption_tax": "policy_consumption_tax",
    "minimum_wage": "policy_minimum_wage",
    "unemployment_benefit": "policy_unemployment_benefit",
}


def policy_view(island) -> dict[str, Any]:
    """島の今の政策（税率と失業手当は %、予算と最低賃金はお金）。命令の入力の初めの値に使う。"""
    policy = island.policy or {}
    budget_items = get_game().config.get("fiscal.budget_items")
    budget = policy.get("budget", {})
    return {
        "income_tax": policy.get("income_tax", 0) / 100,
        "corporate_tax": policy.get("corporate_tax", 0) / 100,
        "consumption_tax": policy.get("consumption_tax", 0) / 100,
        "minimum_wage": policy.get("minimum_wage", 0),
        "unemployment_benefit": policy.get("unemployment_benefit", 0) / 100,
        "budget": {
            item: {"name": message(f"budget_{item}"), "amount": budget.get(item, 0)}
            for item in budget_items
        },
        # 公共料金の上限は見せる単位 1 つあたり（W-30）
        "price_cap": {g: price(g, p) for g, p in policy.get("price_cap", {}).items()},
        **finance_policy_view(island),
    }


def finance_policy_view(island) -> dict[str, Any]:
    """島の今の金融の政策（フェーズ2-2）。金利は %、国債の期間の割合は %。

    民間銀行の参入を決めていなければ（""）、経済体制の既定を bank_entry_default に入れる。
    """
    game = get_game()
    defs = game.defs
    p: Policy = build(Policy, island.policy or {})
    economy = island.economy or {}
    finance = economy.get("finance", {})
    regime = defs.regimes.get((island.regime or {}).get("economy", ""), {})
    entry_default = str(regime.get("bank_entry", "closed"))
    shares = p.bond_terms or {
        term: round(share * BASIS) for term, share in game.config.get("bonds.auto_terms").items()
    }
    total = sum(shares.values()) or 1
    # 中央銀行の独立を切り替えた後は、しばらく戻せない（次のターンの命令で戻せるようになるターン）
    lock = game.config.get("central_bank.switch_lock_turns")
    switched = finance.get("cb_switched")
    unlock = switched + lock if switched is not None else None
    since = finance.get("default_turn")
    exclusion = game.config.get("bonds.default_exclusion_turns")
    closed_until = since + exclusion - 1 if since is not None else -1
    return {
        "has_economy": economy.get("started") is not None,
        "planned": regime.get("owner") == OWNER_STATE,  # 計画経済（倒産も民間の参入もない）
        "policy_rate": p.policy_rate * 100 / BASIS,
        "cb_independent": p.cb_independent,
        "cb_lock_turns": lock,
        "cb_unlock_turn": unlock if unlock is not None and unlock > island.world.turn + 1 else None,
        "cb_bond_trade": p.cb_bond_trade,
        "cb_bond_trade_text": bond_trade_text(p.cb_bond_trade),
        "bond_unsold": p.bond_unsold,
        "bond_unsold_choices": {k: message(f"bond_unsold_{k}") for k in (UNDERWRITE, CUT)},
        "bond_terms": {
            term: {
                "name": defs.name("finance", term),
                "turns": t["turns"],
                "share": round(shares.get(term, 0) * 100 / total, 1),
            }
            for term, t in defs.bond_terms().items()
        },
        "bonds": island.bonds.count(),  # 国債の銘柄の数
        "default_turn": finance.get("default_turn"),  # 最後に債務不履行になったターン
        # 債務不履行の後、銀行が国債を買わない最後のターン（次のターンもそうなら）
        "bonds_closed_until": closed_until if closed_until > island.world.turn else None,
        # 債務不履行の罰（利払いと償還を止める命令の説明に出す）
        "default_premium": round(game.config.get("bonds.default_premium") * 100, 2),
        "default_decay": game.config.get("bonds.default_decay_turns"),
        "default_exclusion": game.config.get("bonds.default_exclusion_turns"),
        "default_crunch": game.config.get("banking.default_crunch_turns"),
        "bank_entry": p.bank_entry,
        "bank_entry_default": entry_default,
        "bank_entry_choices": {k: message(f"bank_entry_{k}") for k in BANK_ENTRY},
        "bank_license": bool(finance.get("bank_license")),  # 免許を出して開業を待っている
        "deposit_insurance": p.deposit_insurance,
        "deposit_insurance_text": insurance_text(p.deposit_insurance),
        # 大きな店（百貨店）の出店の規制（回7）と、許すのを待っている申請
        "large_store": p.large_store or retail.NONE,
        "large_store_choices": {
            k: message(f"large_store_{k}") for k in (retail.NONE, retail.PERMIT, retail.BAN)
        },
        "store_application": _store_application(defs, finance.get(retail.APPLICATION)),
        "subsidy_kinds": {k: message(f"subsidy_{k}") for k in ("production", "capital")},
        "subsidies": [
            {
                "text": subsidy_text(s),
                # やめる命令（率 0）を出すための対象
                "stop": {
                    "kind": s["kind"],
                    **{k: s[k] for k in ("good", "x", "y") if s.get(k) is not None},
                    "rate": 0,
                    "turns": 1,
                },
            }
            for s in p.subsidies
        ],
    }


def hidden_places(island) -> list[tuple[int, int]]:
    """隠して建てた施設（MI-62）のマス。持ち主以外には、見つかった後も出さない。"""
    return [(f.x, f.y) for f in island.facilities.filter(x__gte=0) if (f.ext or {}).get("hidden")]


def sees_facility(row, mine: bool, viewer: int | None) -> bool:
    """施設の行が、見る人に見えるか。隠した施設（MI-62）は、持ち主と、見つけた国（と同盟国）だけ。"""
    ext = row.ext or {}
    return mine or not ext.get("hidden") or (viewer is not None and viewer in ext.get("found", ()))


def visible_logs(logs, mine: bool, island=None):
    """ターンログの行のうち、見る人に見せるもの。軍の行（MI-55）と届いた公電の行（W-39）は持ち主だけ。隠した施設のマス
    （MI-62）の行も持ち主だけ。"""
    if mine:
        return logs
    logs = logs.exclude(kind__in=(*PRIVATE_LOGS, LETTER_LOG))
    if island is not None:
        for x, y in hidden_places(island):
            # x・y のキーが無い行は残す（キー無しは NULL になり、単純な exclude では消えてしまう）
            logs = logs.exclude(
                Q(args__has_key="x") & Q(args__has_key="y") & Q(args__x=x) & Q(args__y=y)
            )
    return logs


def log_entries(logs) -> list[dict[str, Any]]:
    """ターンログの行を、文面とリンク先のマス付きで。"""
    game = get_game()
    out = []
    for log in logs:
        args = log.args or {}
        if log.kind in TITLE_LOGS and isinstance(args.get("industry"), str):
            args = {**args, "title": title_name(args["industry"])}
        text = format_log(game.defs, log.kind, args, calendar=game.calendar)
        entry = {"turn": log.turn, "kind": log.kind, "text": text}
        if "x" in args and "y" in args:
            entry["tile"] = (args["x"], args["y"])
        out.append(entry)
    return out


# 島の画面に出す統計の名前と表示名（StatRecord の name）と単位。単位の good: は資源の単位
SUMMARY_STATS = [
    ("population", "人口", "人"),
    ("labor", "労働力人口", "人"),
    ("employed", "就業者", "人"),
    ("subsistence", "自給農家", "人"),
    ("unemployment", "完全失業率", "%"),
    ("happiness", "幸福度", ""),
    ("food.ratio", "食料充足率", "%"),
    ("power.capacity", "発電量", "good:electricity"),
    ("power.demand", "電力需要", "good:electricity"),
    ("admin_power", "行政力（次ターンの命令実行数）", ""),
    ("facilities", "施設数", ""),
    ("construction", "建設中の施設", ""),
    ("gdp", "GDP（1ターン）", "money"),
    ("cpi", "物価指数（建国時 100）", "index"),
    ("inflation", "物価上昇率（年率）", "%"),
    ("wage.average", "平均賃金（1人1ターン）", "money"),
    ("treasury", "国庫", "money"),
    ("debt", "国債の残高", "money"),
    ("policy_rate", "政策金利", "rate"),
    ("deposit_rate", "預金金利（銀行の平均）", "rate"),
    ("interbank_rate", "銀行間の金利", "rate"),
    ("loans", "融資の残高", "money"),
    ("loans.arrears", "うち返済が遅れている融資", "money"),
    ("banks", "銀行の数", ""),
    ("banks.failed", "銀行の破綻件数（1ターン）", ""),
    ("firms.private", "民間事業所数", ""),
    ("firms.entered", "新規参入数（1ターン）", ""),
    ("firms.bankrupt", "倒産件数（1ターン）", ""),
    ("firms.closed", "閉鎖件数（1ターン）", ""),
    ("education.level", "教育水準", "percent"),
    ("engineers", "技術者", "人"),
    ("engineers.unemployment", "技術者の失業率", "%"),
    ("power.shortage", "電力の不足率", "%"),
    # 商業・サービス業の店と人口の増減のわけ（Codex の試し遊び、ターン 250〜300）
    ("services.workers", "商業・サービス業就業者数", "人"),
    ("services.seats", "商業・サービス業の求人数", "人"),
    ("services.price", "サービスの値段（1単位）", "money"),
    ("services.sales", "サービスの売上高（1ターン）", "money"),
    ("population.births", "出生数（1ターン）", "人"),
    ("population.deaths", "死亡数（餓死を除く。1ターン）", "人"),
    ("population.starved", "餓死者数（1ターン）", "人"),
    ("migration.arrived", "入国者数（移住。帰国者を含む。1ターン）", "人"),
    ("migration.left", "出国者数（移住。1ターン）", "人"),
]


def latest_stats(island: m.Island) -> tuple[int | None, dict[str, float]]:
    last = island.stats.order_by("-turn").values_list("turn", flat=True).first()
    if last is None:
        return None, {}
    rows = island.stats.filter(turn=last).values_list("name", "value")
    return last, dict(rows)


def summary(stats: dict[str, float], currency: str = "") -> list[dict[str, Any]]:
    """国勢の表の行。currency はお金の額に付ける通貨記号。"""
    return [
        {"key": key, "label": label, "text": stat_text(key, stats[key], unit, currency)}
        for key, label, unit in SUMMARY_STATS
        if key in stats
    ]


# 船の荷の重さ（freight_weight のトン）の統計の単位と、荷 1 トンあたりのお金の統計（W-30）
WEIGHT_UNIT = " トン"
WEIGHT_PRICE_STATS = ("freight.ship_rate",)


def tons(milli: int) -> float:
    """1000 分の 1 トンの船の荷の重さを、見せるトンに（小数第1位まで）。"""
    return round(from_milli(milli) * get_game().defs.weight_scale(), 1)


def weight_price(value: float) -> float:
    """荷 1 トンあたりのお金（運賃・港の使用料）を、見せる 1 トンあたりに。"""
    shown_value = value / get_game().defs.weight_scale()
    return round(shown_value) if abs(shown_value) >= 100 else round(shown_value, 1)


def stat_value(key: str, value: float | None, unit: str) -> float | None:
    """統計の値を見せる単位に: 財の量（単位 good:<財>）は display_scale を掛け、財の値段
    （price.<財>）は見せる単位 1 つあたりに割る（W-30。統計は計算の単位のまま記録してある）。"""
    if value is None:
        return None
    defs = get_game().defs
    if unit.startswith("good:"):
        return defs.shown_amount(unit.removeprefix("good:"), value)
    if unit == WEIGHT_UNIT:  # 船の荷の重さ
        return value * defs.weight_scale()
    if key in WEIGHT_PRICE_STATS:  # 荷 1 トンあたりのお金
        return weight_price(value)
    good = key.removeprefix("price.")
    if key.startswith("price.") and good in defs.goods:
        return defs.shown_price(good, value)
    return value


def _per_unit(key: str) -> bool:
    """1 単位あたりのお金の統計か（財の値段と、荷 1 トンあたりの運賃）。"""
    return key.startswith("price.") or key in WEIGHT_PRICE_STATS


def stat_text(key: str, value: float, unit: str, currency: str = "") -> str:
    """統計の値の文。

    unit は money（お金）・index（指数）・rate（% の数で入っている率）・percent（% の数で入っている
    割合）・%（0〜1 の割合）・ratio（倍）・fx（為替レート）・good:<財>（その単位）か、数の後ろに
    付ける文字。
    """
    value = stat_value(key, value, unit)
    if unit.startswith("good:"):
        unit = " " + get_game().defs.unit_name(unit.removeprefix("good:"))
    if unit == "money":
        if abs(value) < 100 and not float(value).is_integer() and _per_unit(key):
            return f"{'-' if value < 0 else ''}{currency}{abs(value):,.1f}"  # 安い 1 単位の値段
        return money_text(round(value), currency)
    if unit == "fx":
        return f"{value:.4f}"
    if unit == "index":
        return f"{value:.1f}"
    if unit == "rate":
        return f"{value:.2f}%"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "ratio":
        return f"{value:.2f} 倍"
    if key == "inflation":
        return f"{value * 100:+.1f}%"
    if unit == "%":
        text = f"{value * 100:.0f}%"
        if value < 1 and text == "100%":  # 99.9% を 100% と出さない（食料充足率。Codex の試し遊び）
            return f"{math.floor(value * 1000) / 10:.1f}%"
        return text
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}{unit}"
    return f"{int(value):,}{unit}"


def goods_table(
    stats: dict[str, float], markets: dict[str, Any] | None = None, currency: str = ""
) -> list[dict[str, Any]]:
    """資源ごとの国の倉庫の在庫と、このターンの生産量・市場の値段と売買の量。

    markets は財 → そのターンの市場の記録（server.economy.models.MarketRecord）。
    """
    defs = get_game().defs
    markets = markets or {}
    rows = []
    for good, gdef in defs.goods.items():
        stock = stats.get(f"stock.{good}", 0)
        made = stats.get(f"production.{good}", 0)
        record = markets.get(good)
        if stock or made or (record is not None and record.volume):
            rows.append(
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "tier": gdef.get("tier"),
                    "stock": shown(good, stock),
                    "production": shown(good, made),
                    "price": price_text(good, record.price, currency) if record is not None else "",
                    "volume": amount(record.volume, good) if record is not None else "",
                }
            )
    return rows


def latest_markets(island: m.Island, turn: int | None) -> dict[str, Any]:
    """ターン turn の財ごとの市場の記録。"""
    if turn is None:
        return {}
    return {r.good: r for r in MarketRecord.objects.filter(island=island, turn=turn)}


def _store_application(defs: Any, found: dict[str, Any] | None) -> dict[str, Any] | None:
    """百貨店の出店の申請（許可制のとき）。許したものは approved。"""
    if not found:
        return None
    return {
        "turn": found["turn"],
        "facility": found["facility"],
        "name": defs.name("facilities", found["facility"]),
        "x": found["x"],
        "y": found["y"],
        "approved": bool(found.get("approved")),
    }
