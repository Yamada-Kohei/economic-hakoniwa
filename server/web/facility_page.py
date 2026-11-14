"""施設のページ（UI-72・UI-73）: 1 つの施設の「見る」所と「打てる手」の箱の中身。

画面（views.facility_page）と JSON API（api.facility）が同じ中身を使う。持ち主には帳簿・借入・
この施設に出ている命令・前のターンの結果・出せる命令を出す。ほかの人とログインしていない人には、
地図のマスの窓と同じ公開の中身だけを出す。隠した施設（MI-62）は、見える国のほかには無いものとして
扱う（None）。町（住宅地）は施設の一覧と同じく施設のページを持たない。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Max

from hakoniwa_core import facilities as fac
from server.economy.models import FacilityBook
from server.game import get_game
from server.turns.models import TurnLog
from server.web import display
from server.world import models as m
from server.world.repository import facility_from_row

# 施設のマス（x, y）を指して出す命令。打てる手の箱に出す順。工事・区域・送電線・住宅・地名・部隊の
# 移動など、場所を見て決める命令は地図に残す（UI-78）
FACILITY_ORDERS = (
    "build",  # 同じマスに同じ施設を建てる: 建て増し・農場の次の段階への工事
    "set_priority",
    "change_recipe",
    "set_accounting",
    "nationalize",
    "privatize",
    "set_subsidy",
    "emergency_loan",
    "resolve_bank",
    "rename_facility",
    "demolish",
)
RESULTS_MAX = 10  # 前のターンの結果に出す行の数


def _goods(items: list[dict[str, Any]]) -> str:
    return "、".join(
        f"{g['name']} {g['amount']}{' ' + g['unit'] if g['unit'] else ''}" for g in items
    )


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def view_rows(info: dict[str, Any]) -> list[dict[str, str]]:
    """「見る」の表の行（見出しと文）。地図のマスの窓（static/js/island/panel.js）と同じ中身。"""
    rows: list[tuple[str, str]] = [
        ("種類", info["kind_name"]),
        ("所有", f"{info['owner_name']}・{info['accounting_name']}"),
    ]
    if info.get("firm_name"):
        rows.append(("企業", info["firm_name"]))
    if info.get("size", 1) > 1 or info.get("expanding"):
        more = f"（建て増しの工事 {info['expanding']} 件）" if info.get("expanding") else ""
        rows.append(("規模", f"{info['size']}{more}"))
    if info.get("grade_name"):
        rows.append(("段階", info["grade_name"]))
    if info.get("recipe_name"):
        rows.append(("生産方式", info["recipe_name"]))
    if not info["built"]:
        if info.get("private_site"):
            rows.append(("建設", "民間の資本が自分の資金で建設中"))
        if "progress" in info:
            rows.append(("建設進捗", f"{_pct(info['progress'])}（工期 {info['turns']} ターン）"))
        rows.append(("建設労働者", f"{info['workers']:,} / {info.get('workers_needed', 0):,} 人"))
        if info.get("materials"):
            rows.append(("建設資材", _goods(info["materials"])))
    elif info.get("workers_needed") is not None and not info.get("monument"):
        rows.append(("従業者", f"{info['workers']:,} / {info['workers_needed']:,} 人"))
        rows.append(("稼働率", _pct(info["operating_rate"])))
        if info.get("equipment") is not None:
            worn = "（傷んだ分は作れません）" if info["equipment"] < 1 else ""
            rows.append(("設備", _pct(info["equipment"]) + worn))
        if info.get("wage") is not None:
            rows.append(("賃金（1人1ターン）", f"{info['wage']:,}"))
        if info.get("engineers_needed"):
            rows.append(("うち技術者", f"{info['engineers']:,} / {info['engineers_needed']:,} 人"))
        if info.get("engineers_general"):
            rows.append(("一般の仕事に就いた技術者", f"{info['engineers_general']:,} 人"))
        if info.get("education"):
            rows.append(("教育", info["education"]))
        if info.get("inputs"):
            rows.append(("投入（1ターン）", _goods(info["inputs"])))
        if info.get("outputs"):
            rows.append(("産出（1ターン）", _goods(info["outputs"])))
        if info.get("transition"):
            rows.append(("作付けの切り替え", info["transition"]["text"]))
    if info.get("status_text"):
        rows.append(("状態", info["status_text"]))
    if info.get("halted_until") is not None:
        rows.append(("停止期間", f"{display.turn_text(info['halted_until'])}まで"))
    if info.get("closing") is not None:
        sale = info.get("closing_sale")
        text = f"{display.turn_text(info['closing'])}から閉鎖中。"
        if sale:
            text += (
                f"居抜きで売りに出しています（売り値 {sale['price']}）。"
                f"{display.turn_text(sale['until'])}までに売れなければマスを空けます"
            )
        rows.append(("閉鎖", text))
    if info.get("foreclosed"):
        s = info["foreclosed"]
        how = "動かしながら" if s["running"] else "止めたまま"
        rows.append(("管財人", f"{s['bank']}が{how}売りに出しています。売り値 {s['price']}"))
    if info.get("stock"):
        rows.append(("施設在庫", _goods(info["stock"])))
    for fuel in info.get("fuel_reserves") or []:
        months = "使用量未観測" if fuel["months"] is None else f"約 {fuel['months']:.1f} か月分"
        rows.append((f"{fuel['name']}の備え", f"{fuel['amount']} {fuel['unit']}（{months}）"))
    return [{"label": label, "text": text} for label, text in rows]


def settings_rows(info: dict[str, Any]) -> list[str]:
    """動いている設定（命令で変えて今も効いているもの）の文。"""
    out = []
    if info.get("priority_changed") and info.get("priority") is not None:
        out.append(f"優先度 {info['priority']}（命令で変えた値）")
    if info.get("recipe_next_name"):
        out.append(f"次のターンに生産方式を{info['recipe_next_name']}に切り替えます")
    out += [f"補助金: {s}" for s in info.get("subsidies") or []]
    if (info.get("bank") or {}).get("emergency"):
        out.append(f"緊急の貸し出しの上限: {info['bank']['emergency']}")
    return out


def recipe_label(kind: str, recipe: str, name: str) -> str:
    """生産方式の名前に効果を添える（人手の割合・教材の育ち）。地図の建設の欄（static/js/island/
    recipes.js の recipeLabel）と同じ書き方。"""
    r = get_game().defs.facilities[kind]["recipes"].get(recipe, {})
    parts = [name]
    labor = r.get("labor", 1.0)
    if labor < 1:
        parts.append(f"人手 {round(labor * 100) / 10:g} 割")
    if recipe == "digital" and r.get("education_scale") is not None:
        parts.append(f"育ち +{round((r['education_scale'] - 1) * 1000) / 10:g}%")
    return "・".join(parts)


def _recipe_form(info: dict[str, Any], kind: str) -> dict[str, Any]:
    """生産方式の切り替えの欄に出す候補（効果を添えた名前）と、添える注意（電子機器を使う作り方が
    あるか・民営で画面から替えられないか）。"""
    recipes = get_game().defs.facilities[kind]["recipes"]
    return {
        "choices": [
            {**c, "label": recipe_label(kind, c["recipe"], c["name"])}
            for c in info.get("recipe_choices") or []
        ],
        "electronics": any("electronics" in (r.get("inputs") or {}) for r in recipes.values()),
        # 民営の施設は持ち主の企業が替える（画面からは替えられない）
        "private": info.get("owner") == "private" and len(recipes) > 1,
    }


def _actions(info: dict[str, Any], f: Any, capital: Any) -> list[str]:
    """この施設に今出せる命令の種類（FACILITY_ORDERS の順）。マスを持たない施設は出せない。"""
    if not fac.on_map(f):
        return []
    can = {
        "build": "expand" in info,
        "set_priority": info.get("priority") is not None,
        "change_recipe": bool(info.get("recipe_choices")),
        "set_accounting": "accounting_target" in info,
        "nationalize": info.get("ownership_target") == "nationalize",
        "privatize": info.get("ownership_target") == "privatize",
        "set_subsidy": bool(info.get("subsidy_target")),
        "emergency_loan": bool(info.get("emergency_loan")),
        "resolve_bank": bool(info.get("resolve_methods")),
        "rename_facility": True,
        "demolish": tuple(capital or ()) != (f.x, f.y),
    }
    return [kind for kind in FACILITY_ORDERS if can[kind]]


def _orders_here(island: m.Island, f: Any) -> tuple[list[dict], list[dict]]:
    """この施設のマスを指す、待っている命令と、前のターンに実行した命令の結果。"""
    here = island.orders.filter(kind__in=FACILITY_ORDERS, payload__x=f.x, payload__y=f.y)
    labels = display.facility_labels(island)  # 生産方式の変更の行き先を施設の種類から引く（#12）
    pending = [
        {"kind": o.kind, "text": display.describe_order(o.kind, o.payload, labels)}
        for o in here.filter(status="pending").order_by("seq", "id")
    ]
    done = island.orders.exclude(status="pending")
    last = done.aggregate(turn=Max("executed_turn"))["turn"]
    results = []
    if last is not None:
        for o in (
            here.exclude(status="pending").filter(executed_turn=last).order_by("seq")[:RESULTS_MAX]
        ):
            results.append(
                {
                    "turn": o.executed_turn,
                    "status": o.status,
                    "text": display.describe_order(o.kind, o.payload, labels),
                    "reason": display.message(o.reason, **(o.reason_args or {}))
                    if o.reason
                    else "",
                }
            )
    return pending, results


def _logs_here(island: m.Island, x: int, y: int) -> list[dict[str, Any]]:
    """前のターンのターンログのうち、この施設のマスの行。"""
    logs = TurnLog.objects.filter(island=island)
    last = logs.aggregate(turn=Max("turn"))["turn"]
    if last is None:
        return []
    rows = logs.filter(turn=last, args__x=x, args__y=y).order_by("id")[:RESULTS_MAX]
    return display.log_entries(rows)


def facility_data(
    island: m.Island, number: int, mine: bool, viewer: int | None
) -> dict[str, Any] | None:
    """施設のページの中身。施設が無い・町・見る人に見えない施設なら None（画面と API は 404）。"""
    game = get_game()
    row = island.facilities.filter(number=number).first()
    if row is None or row.kind == fac.housing_kind(game.config):
        return None
    if not display.sees_facility(row, mine, viewer):
        return None
    state = display.island_for_naming(island)
    info = display.facility_info(row, state, mine)
    f = facility_from_row(row)
    x, y = fac.position(f)  # 町の中の店は町のマス
    data: dict[str, Any] = {
        "number": number,
        "name": info["name"],
        "kind_name": info["kind_name"],
        "on_map": x >= 0 and y >= 0,
        "x": x,
        "y": y,
        "mine": mine,
        "facility": info,
        "rows": view_rows(info),
    }
    if not mine:
        return data
    pending, results = _orders_here(island, f) if fac.on_map(f) else ([], [])
    data.update(
        hint=info.get("hint", ""),
        has_books=FacilityBook.objects.filter(island=island, facility=number).exists(),
        settings=settings_rows(info),
        orders=pending,
        results=results,
        logs=_logs_here(island, x, y) if data["on_map"] else [],
        actions=_actions(info, f, state.capital),
        recipe_form=_recipe_form(info, row.kind),
    )
    return data
