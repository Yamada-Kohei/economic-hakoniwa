"""画面（テンプレート）。島の地図と命令の入力は JavaScript が JSON API（api.py）を使って動かす。"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from hakoniwa_core import facilities as fac
from hakoniwa_core.trade import contracts, tradable_goods
from server.game import get_game
from server.turns.models import TurnLog
from server.web import chain, display, founding, reports, trade
from server.world.models import Island, World
from server.world.services import grid_size

GUIDE_TURNS = 12  # 建国からこのターン数の間、島の画面に「はじめの手順」を出す


def top(request):
    world = World.objects.order_by("id").first()
    islands = world.islands.select_related("owner").order_by("id") if world else []
    own = None
    if request.user.is_authenticated:
        own = Island.objects.filter(owner=request.user).first()
    return render(request, "web/top.html", {"world": world, "islands": islands, "own": own})


def island_page(request, island_id: int):
    island = get_object_or_404(Island.objects.select_related("owner", "world"), pk=island_id)
    defs = get_game().defs
    turn, stats = display.latest_stats(island)
    currency = (island.names or {}).get("currency_symbol", "")
    founded = (island.ext or {}).get("founded_turn", 0)
    logs = TurnLog.objects.filter(island=island).order_by("-turn", "id")
    last_turn = logs.values_list("turn", flat=True).first()
    regime = [
        {
            "axis": display.message(f"axis_{defs.regimes[r]['axis']}"),
            "name": defs.name("regimes", r),
        }
        for r in (island.regime or {}).values()
        if r in defs.regimes
    ]
    context = {
        "island": island,
        "mine": request.user.is_authenticated and island.owner_id == request.user.id,
        "stats_turn": turn,
        "summary": display.summary(stats, currency),
        "goods": display.goods_table(stats, display.latest_markets(island, turn), currency),
        "regime": regime,
        "resources": [defs.name("goods", g) for g in island.resources or []],
        "climate": _climate_name(island),
        # 建設の欄で、この島で使えない施設や生産方式を見分けるため
        "resource_ids": island.resources or [],
        "producible": sorted(fac.producible_goods(defs, island.resources or [])),
        "capital_name": (island.names or {}).get("capital", ""),
        "logs": display.log_entries(logs.filter(turn=last_turn)) if last_turn is not None else [],
        "log_turn": last_turn,
        "show_guide": island.world.turn - founded < GUIDE_TURNS,
        "page": "island",
    }
    return render(request, "web/island.html", context)


def _climate_name(island: Island) -> str:
    """島の気候帯の表示名（フェーズ3-2。気候のない島は空）。"""
    zone = (island.ext or {}).get("climate")
    return get_game().defs.name("climate", zone) if zone else ""


# --- 島の統計の画面（経済・市場・施設・財政・金融・貿易・為替） ----------------------------
# グラフは JavaScript（static/js/reports/）が統計の API を読んで描く。施設・金融・貿易・為替の中身は
# 島の持ち主だけに出し、ほかの人には国全体の統計だけを出す。財政・金融・貿易の命令は、今の命令の列に
# 足す。


def _report(request, island_id: int, page: str, title: str) -> tuple[Island, dict, dict]:
    """統計の画面に共通の値（島・持ち主か・直近の統計のターン・通貨記号・見出しのリンク）。"""
    island = get_object_or_404(Island.objects.select_related("owner", "world"), pk=island_id)
    turn, stats = display.latest_stats(island)
    context = {
        "island": island,
        "mine": request.user.is_authenticated and island.owner_id == request.user.id,
        "stats_turn": turn,
        "currency": (island.names or {}).get("currency_symbol", ""),
        "page": page,
        "title": title,
    }
    return island, context, stats


def economy_page(request, island_id: int):
    """経済: 主な指標の直近の値と、その推移のグラフ。"""
    island, context, stats = _report(request, island_id, "economy", "経済")
    currency = context["currency"]
    charts = [{**c, "names": ",".join(c["names"])} for c in reports.economy_charts()]
    context.update(
        tiles=reports.stat_rows(reports.with_fiscal(stats), reports.ECONOMY_TILES, currency),
        summary=display.summary(stats, currency),
        charts=charts,
        choices=reports.stat_choices(island, context["mine"]),
    )
    return render(request, "web/economy.html", context)


def market_page(request, island_id: int):
    """市場: 財ごとの値段と売買の量、選んだ財の推移とこのターンの約定、買えなかった施設。"""
    island, context, stats = _report(request, island_id, "market", "市場")
    rows = reports.market_table(island, stats, context["currency"])
    goods = [r["good"] for r in rows]
    chosen = request.GET.get("good")
    context.update(
        rows=rows,
        good=chosen if chosen in goods else (goods[0] if goods else ""),
        market_turn=reports.market_turn(island),
    )
    if context["mine"]:
        context["short"] = reports.short_facilities(island, display.island_for_naming(island))
    else:
        context["note"] = "約定の相手と、原料を買えずに止まった施設は、島の持ち主だけが見られます。"
    return render(request, "web/market.html", context)


def chain_page(request, island_id: int):
    """産業の鎖（フェーズ3-2）: 品目ごとの作った量・使った量・輸出入・値段の動きと、作る施設と使う
    施設、止まっている工場が待っているもの。自国だけ。"""
    island, context, stats = _report(request, island_id, "chain", "産業の鎖")
    if context["mine"]:
        context["chain"] = chain.chain_view(island, stats, context["currency"])
    else:
        context["note"] = "産業の鎖は、島の持ち主だけが見られます。"
    return render(request, "web/chain.html", context)


OTHERS_NOTE = "他国の島のため、国全体の統計だけを表示しています。"

# 他国の島の施設の画面に出す、国全体の施設の統計
FACILITY_STATS = [
    "facilities",
    "construction",
    "firms.private",
    "firms.private_sites",
    "profit",
    "firms.entered",
    "firms.bankrupt",
    "firms.closed",
    "firms.abandoned",
]


def facilities_page(request, island_id: int):
    """施設: 自国の全施設の損益・預金・借入と帳簿の推移。他国は国全体の統計だけ。"""
    island, context, stats = _report(request, island_id, "facilities", "施設")
    context["stats"] = reports.stat_rows(stats, FACILITY_STATS, context["currency"])
    context["chart_names"] = "facilities,construction,firms.private"
    if context["mine"]:
        context["table"] = reports.facility_rows(island, context["currency"])
    else:
        context["note"] = OTHERS_NOTE
    return render(request, "web/facilities.html", context)


def fiscal_page(request, island_id: int):
    """財政: 国庫の収入と支出、予算と使った額、税率、国債の一覧。持ち主には命令の入力も。"""
    island, context, stats = _report(request, island_id, "fiscal", "財政")
    context.update(
        fiscal=reports.fiscal_view(island, stats, context["currency"]),
        bonds=reports.bond_rows(island, context["currency"], context["mine"]),
    )
    return render(request, "web/fiscal.html", context)


def finance_page(request, island_id: int):
    """金融: 中央銀行と金利・国債の利回り・融資の統計。持ち主には銀行の一覧・破綻処理・命令も。"""
    island, context, stats = _report(request, island_id, "finance", "金融")
    data = reports.finance_data(island, context["mine"], context["currency"])
    banks = data.get("banks", [])
    context.update(
        stats=reports.stat_rows(stats, list(data["stats"]), context["currency"]),
        central_bank=data["central_bank"],
        banks=banks,
        bank_ratio_names=",".join(f"bank.{b['number']}.capital_ratio" for b in banks),
    )
    if not context["mine"]:
        context["note"] = OTHERS_NOTE
    return render(request, "web/finance.html", context)


def trade_page(request, island_id: int):
    """貿易: 貿易圏・二国間契約・商社・船団と港・運ぶ途中の荷・海外の商人。持ち主には貿易の命令も。
    他国は国全体の数（輸出・輸入・貿易収支・船の数）と貿易圏の加盟だけ。"""
    island, context, stats = _report(request, island_id, "trade", "貿易")
    data = trade.trade_data(island, context["mine"], context["currency"], stats)
    names = trade.TRADE_STATS if context["mine"] else trade.PUBLIC_TRADE_STATS
    context.update(trade=data, stats=reports.stat_rows(stats, names, context["currency"]))
    if not context["mine"]:
        context["note"] = (
            "他国の島のため、国全体の数と貿易圏の加盟だけを表示しています"
            "（契約・商社・船団・荷の中身は島の持ち主だけが見られます）。"
        )
    return render(request, "web/trade.html", context)


def fx_page(request, island_id: int):
    """為替と国際収支: 使っている通貨のレートと推移、貿易収支。持ち主には外貨の持ち高と、自国通貨を
    持つ外国の主体（非居住者の預金）の内訳も。"""
    island, context, stats = _report(request, island_id, "fx", "為替と国際収支")
    data = trade.fx_data(island, context["mine"], context["currency"], history=False)
    context.update(
        fx=data,
        stats=reports.stat_rows(stats, trade.FX_STATS, context["currency"]),
        rate_names=",".join(trade.rate_names(island)),
        band=f"{data['band'] * 100:g}",
    )
    if not context["mine"]:
        context["note"] = OTHERS_NOTE
    return render(request, "web/fx.html", context)


def turn_log(request, island_id: int):
    """ターンログ。ターンごとに区切り、新しいターンから並べる。"""
    island = get_object_or_404(Island, pk=island_id)
    turns = (
        TurnLog.objects.filter(island=island)
        .order_by("-turn")
        .values_list("turn", flat=True)
        .distinct()
    )
    page = Paginator(list(turns), 10).get_page(request.GET.get("page"))
    entries = display.log_entries(
        TurnLog.objects.filter(island=island, turn__in=list(page)).order_by("-turn", "id")
    )
    groups: dict[int, list] = {}
    for entry in entries:
        groups.setdefault(entry["turn"], []).append(entry)
    return render(
        request,
        "web/log.html",
        {"island": island, "page": page, "groups": sorted(groups.items(), reverse=True)},
    )


def world_map(request):
    """世界マップ: 区画を並べ、島のある区画にはその島へのリンクを置く。"""
    world = World.objects.order_by("id").first()
    rows: list[list[Island | None]] = []
    if world is not None:
        islands = list(world.islands.select_related("owner"))
        grid = max([grid_size(world)] + [max(i.wx, i.wy) + 1 for i in islands])
        at = {(i.wx, i.wy): i for i in islands}
        rows = [[at.get((x, y)) for x in range(grid)] for y in range(grid)]
    own_id = None
    if request.user.is_authenticated:
        own_id = Island.objects.filter(owner=request.user).values_list("id", flat=True).first()
    return render(request, "web/world.html", {"world": world, "rows": rows, "own_id": own_id})


def help_page(request):
    game = get_game()
    defs = game.defs
    facilities = [
        {
            "name": defs.name("facilities", fid),
            "terrain": "・".join(defs.name("terrain", t) for t in f["terrain"]),
            "notes": _placement_notes(f),
            "workers": f["workers"],
            "recipes": [
                {
                    "name": defs.recipe_name(fid, rid),
                    "inputs": display.goods_list(r["inputs"]),
                    "outputs": display.goods_list(r["outputs"]),
                    "resource": defs.name("goods", r["requires_resource"])
                    if r.get("requires_resource")
                    else "",
                }
                for rid, r in f["recipes"].items()
            ],
        }
        for fid, f in defs.facilities.items()
        if f.get("buildable", True)
    ]
    orders = [
        {"name": defs.name("orders", oid), "cost": o["admin_cost"]}
        for oid, o in defs.orders.items()
    ]
    return render(
        request,
        "web/help.html",
        {
            "facilities": facilities,
            "orders": orders,
            "commute": game.config.get("labor.commute_radius"),
            "base_admin": game.config.get("orders.base_admin_power"),
            "max_admin": game.config.get("orders.max_admin_power"),
            "admin_per_office": _admin_people(defs),
            # 金融と企業（フェーズ2-2）の説明に出す数
            "arrears_limit": game.config.get("loans.arrears_limit"),
            "foreclosure_turns": game.config.get("firms.foreclosure_turns"),
            "cb_lock_turns": game.config.get("central_bank.switch_lock_turns"),
            # 国債の債務不履行の罰
            "default_premium": f"{game.config.get('bonds.default_premium') * 100:g}",
            "default_decay": game.config.get("bonds.default_decay_turns"),
            "default_exclusion": game.config.get("bonds.default_exclusion_turns"),
            "default_crunch": game.config.get("banking.default_crunch_turns"),
            "settlements": [
                {
                    "name": defs.name("settlements", sid),
                    "capacity": s["capacity"],
                    "materials": display.goods_list(s.get("materials", {})),
                }
                for sid, s in ((sid, defs.settlements[sid]) for sid in defs.settlement_levels())
            ],
            "units": [
                {"name": defs.name("goods", g), "unit": defs.unit_name(g)}
                for g in defs.goods
                if defs.unit_name(g)
            ],
            "trade": _trade_help(game),
            **_goods_help(game),
        },
    )


def _goods_help(game) -> dict:
    """手引きの「品目と自然条件」（フェーズ3-2）に出す数。"""
    config, defs = game.config, game.defs
    percent = lambda v: f"{v * 100:g}"  # noqa: E731
    necessity = config.get("household.necessity")
    return {
        "climates": [
            {"name": defs.name("climate", z), "text": founding.climate_text(z)}
            for z in config.get("climate.zones")
        ],
        "fertility": [f"{v:g}" for v in config.get("climate.fertility")],
        "necessity": {"share": percent(necessity["share"]), "floor": percent(necessity["floor"])},
        "cars": {
            "wear": percent(config.get("household.cars.wear")),
            "share": percent(config.get("household.cars.share")),
        },
        "trucks": {
            "drivers": config.get("freight.trucks.drivers"),
            "share": percent(config.get("freight.trucks.max_share")),
        },
        "shortage": {
            "share": percent(config.get("records.shortage.share")),
            "turns": config.get("records.shortage.turns"),
        },
    }


def _trade_help(game) -> dict:
    """手引きの「貿易と為替」に出す数（game.yaml の trade・fx・foreign_merchant と定義データ）。"""
    config, defs = game.config, game.defs
    settings = config.get("foreign_merchant")
    return {
        "ship_speed": config.get("trade.ship_speed"),
        "ship_capacity": config.get("trade.ship_capacity"),
        "ship_life": config.get("trade.ship_life"),
        "start_ships": config.get("trade.start_ships"),
        "fuel": "・".join(defs.name("goods", g) for g in config.get("trade.ship_fuel")),
        "port_capacity": config.get("trade.port_capacity"),
        "trader_cut": f"{config.get('trade.trader_cut') * 100:g}",
        "trader_max": defs.facilities[config.get("trade.trader_facility")].get("max_per_island"),
        "bloc_band": f"{config.get('trade.bloc_band') * 100:g}",
        "cancel_turns": config.get("trade.contract_cancel_turns"),
        "proposal_turns": contracts.KEEP_ENDED,
        "fx_band": f"{config.get('fx.band') * 100:g}",
        "merchant_turns": settings["turns"],
        "merchant_last": settings["last_turns"],
        "merchant_multiplier": f"{settings['multiplier']:g}",
        "merchant_last_multiplier": f"{settings['last_multiplier']:g}",
        "merchant_goods": "・".join(defs.name("goods", g) for g in settings["goods"]),
        "tradable": "・".join(defs.name("goods", g) for g in tradable_goods(defs)),
    }


def _admin_people(defs) -> int | None:
    """行政力 1 に要る公務員の数（admin_power を持つ施設の係数から）。"""
    rates = [f["admin_power"] for f in defs.facilities.values() if f.get("admin_power")]
    return round(1 / max(rates)) if rates else None


def _placement_notes(f) -> str:
    notes = []
    if f.get("coastal"):
        notes.append("海に面したマスのみ")
    if f.get("near_capital") is not None:
        notes.append(f"首都から {f['near_capital']} マス以内")
    if f.get("max_per_island") is not None:
        notes.append(f"1国 {f['max_per_island']} か所まで")
    return "・".join(notes)
