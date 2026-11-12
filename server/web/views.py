"""画面（テンプレート）。島の地図と命令の入力は JavaScript が JSON API（api.py）を使って動かす。"""

from __future__ import annotations

from django.conf import settings as django_settings
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from hakoniwa_core import facilities as fac
from hakoniwa_core.trade import contracts, tradable_goods
from server.accounts import keys
from server.country import png as flag_png
from server.country import services as country_services
from server.country.models import Flag, MonumentImage
from server.game import get_game
from server.letters.services import own_island
from server.turns.models import TurnLog
from server.web import (
    chain,
    chain_map,
    counter,
    country,
    diplomacy,
    display,
    founding,
    military,
    overview,
    rankings,
    reports,
    trade,
)
from server.web import (
    news as news_views,
)
from server.web import politics as politics_views
from server.web import top as top_page
from server.world.models import Facility, Island, World
from server.world.services import grid_size


def top(request):
    """島の一覧（トップ）: GDP の順の表（前のターンからの順位の上下つき）と世界の様子・為替の
    相場表に、個人サイトの品（アクセスカウンタ・キリ番とキリ番ゲッター様・閲覧中の人数・最近の
    出来事・歴代の記録・更新履歴・管理人より・リンク集・当サイトのバナー）を添える。"""
    world = World.objects.order_by("id").first()
    own = None
    if request.user.is_authenticated:
        own = Island.objects.filter(owner=request.user).first()
    context: dict = {"world": world, "own": own, "counter": counter.count_visit(request)}
    context["counter_svg"] = counter.seven_segment(context["counter"].number)
    context["getters"] = counter.getters()
    context["banner_html"] = top_page.banner_html(request)
    context.update(top_page.site_notes())
    if world is not None:
        base = rankings.default_base(world, own)
        islands = trade.directory(world)
        context.update(
            rankings.top_data(world, base, islands),
            columns=rankings.TABLE_COLUMNS,
            table_note=rankings.TABLE_NOTE,
            base_name=islands.get(base, {}).get("currency", "") if base is not None else "",
            records=rankings.records(world, islands),
            recent=top_page.recent_events(world),
        )
    return render(request, "web/top.html", context)


def island_page(request, island_id: int):
    island = get_object_or_404(Island.objects.select_related("owner", "world"), pk=island_id)
    defs = get_game().defs
    turn, stats = display.latest_stats(island)
    currency = (island.names or {}).get("currency_symbol", "")
    founded = (island.ext or {}).get("founded_turn", 0)
    mine = request.user.is_authenticated and island.owner_id == request.user.id
    logs = display.visible_logs(TurnLog.objects.filter(island=island), mine, island).order_by(
        "-turn", "id"
    )
    last_turn = logs.values_list("turn", flat=True).first()
    regime = [
        {
            "axis": display.message(f"axis_{defs.regimes[r]['axis']}"),
            "name": defs.name("regimes", r),
        }
        for r in (island.regime or {}).values()
        if r in defs.regimes
    ]
    alerts, next_steps = overview.island_alerts(island, mine)
    context = {
        "island": island,
        "mine": mine,
        "ai": keys.is_ai_island(island),
        "stats_turn": turn,
        "summary": display.summary(stats, currency),
        # 気をつけたほうがよいこと（国の要約の alerts と同じ文。飢えている層とそのわけも）
        "alerts": alerts,
        # 建国から1年、持ち主に出す「次にすること」（国の要約の next_steps と同じ）
        "next_steps": next_steps,
        "strengths": rankings.island_strengths(island, stats),
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
        "show_guide": island.world.turn - founded < overview.GUIDE_TURNS,
        "page": "island",
        "plan_label": politics_views.page_label(island),
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
        "plan_label": politics_views.page_label(island),
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
        strengths=rankings.island_strengths(island, stats),
    )
    return render(request, "web/economy.html", context)


def market_page(request, island_id: int):
    """市場: 財ごとの値段と売買の量、選んだ財の推移とこのターンの約定、買えなかった施設。"""
    island, context, stats = _report(request, island_id, "market", "市場")
    rows = reports.market_table(island, stats, context["currency"], context["mine"])
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
    施設、止まっている工場が待っているもの。ほかの国には見える施設だけ。"""
    island, context, stats = _report(request, island_id, "chain", "産業の鎖")
    viewer = own_island(request.user)
    context["chain"] = chain.chain_view(
        island, stats, context["currency"], context["mine"], viewer.id if viewer else None
    )
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
    "services.workers",
    "services.sales",
]


def facilities_page(request, island_id: int):
    """施設: 自国の全施設の損益・預金・借入と帳簿の推移。他国は国全体の統計と企業の一覧だけ。"""
    island, context, stats = _report(request, island_id, "facilities", "施設")
    context["stats"] = reports.stat_rows(stats, FACILITY_STATS, context["currency"])
    context["chart_names"] = "facilities,construction,firms.private"
    context["towns"] = reports.town_rows(island)  # 町ごとの働く人（誰にでも。地図と同じ中身）
    context["industry_workers"] = reports.industry_workers(island)  # 円グラフ（誰にでも）
    context["fuel_reserves"] = display.fuel_reserves(island) if context["mine"] else []
    if context["mine"]:
        context["table"] = reports.facility_rows(island, context["currency"])
    else:
        context["note"] = "他国の島のため、国全体の統計と企業の一覧だけを表示しています。"
        context["firms"] = reports.firm_rows(
            island, display.island_for_naming(island), context["currency"], mine=False
        )
    return render(request, "web/facilities.html", context)


def fiscal_page(request, island_id: int):
    """財政: 国庫の収入と支出、予算と使った額、税率、国債の一覧。持ち主には命令の入力も。"""
    island, context, stats = _report(request, island_id, "fiscal", "財政")
    context.update(
        fiscal=reports.fiscal_view(island, stats, context["currency"], context["mine"]),
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
    for row in context["stats"]:
        if row["key"].startswith("yield."):
            bucket = int(row["key"].removeprefix("yield."))
            row["yield_capped"] = bucket in data["yield_capped_buckets"]
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
    """為替と国際収支: 使っている通貨のレートと推移、貿易収支と国際収支の内訳。持ち主には外貨の
    持ち高と、自国通貨を持つ外国の主体（非居住者の預金）の内訳、為替と資本取引の政策と外国の国債も。"""
    island, context, stats = _report(request, island_id, "fx", "為替と国際収支")
    data = trade.fx_data(island, context["mine"], context["currency"], history=False)
    context.update(
        fx=data,
        stats=reports.stat_rows(stats, trade.FX_STATS, context["currency"]),
        bop=reports.stat_rows(stats, trade.BOP_STATS, context["currency"]),
        rate_names=",".join(trade.rate_names(island)),
        # 為替の売買量は通貨ごとに1枚のグラフに（縦軸にその通貨の記号を付ける。W-70）
        volume_charts=[
            {"name": f"fx.volume.{n.removeprefix('fx.')}", "currency": display.currency_name(n[3:])}
            for n in trade.rate_names(island)
        ],
        band=f"{data['band'] * 100:g}",
    )
    if not context["mine"]:
        context["note"] = OTHERS_NOTE
    return render(request, "web/fx.html", context)


# 政治の画面に出す統計
PLAN_STATS = [
    "plan.queue",
    "plan.forced_saving",
    "plan.subsidy",
    "plan.subsidy_unpaid",
    "trade.merchant_gap",
    "trade.merchant_premium",
    "trade.price_gap_subsidies",
    "labor.mobilized",
]


def politics_page(request, island_id: int):
    """政治（フェーズ3-4）: 体制・政情とその内訳・支持率・議席・次の選挙・採決の履歴・報道と集会の
    扱い・混乱期間。持ち主には政治の命令（報道と集会・議会の解散・憲法改正・経済体制の変更・労働の
    動員・国家元首の名前）の入力欄も。"""
    island, context, _ = _report(request, island_id, "politics", "政治")
    context["politics"] = politics_views.politics_data(island, context["mine"])
    if not context["mine"]:
        context["note"] = OTHERS_NOTE
    return render(request, "web/politics.html", context)


def diplomacy_page(request, island_id: int):
    """外交（フェーズ4-1）: 戦争の状態・効いている条約・条約の違反の記録と、終わった条約。持ち主には
    返事待ちの条約の提案と、宣戦・条約・署名・断り・取り下げ・破棄の命令の入力欄も。"""
    island, context, _ = _report(request, island_id, "diplomacy", "外交")
    context["diplomacy"] = diplomacy.diplomacy_data(island, context["mine"])
    return render(request, "web/diplomacy.html", context)


def military_page(request, island_id: int):
    """軍（フェーズ4-2）: 兵と国防費・部隊・艦隊・基地と陣地（持ち主）。ほかの国には兵の総数と
    国防費と見えた艦隊だけ。値は JSON API の /military と同じ（military.py の brief）。"""
    island, context, _ = _report(request, island_id, "military", "軍")
    viewer = country_services.own_island(request.user)
    context["military"] = military.brief(island, context["mine"], viewer.id if viewer else None)
    return render(request, "web/military.html", context)


def news_page(request):
    """世界の近況: 全部の国の主な出来事（建国・選挙・憲法の改正・災害・倒産・条約・戦争など）。"""
    world = World.objects.order_by("id").first()
    if world is None:
        return render(request, "web/news.html", {"entries": [], "page": None})
    data = news_views.news_data(world, request.GET.get("page"))
    paginator = Paginator(range(data["count"]), news_views.PAGE_SIZE)
    return render(
        request,
        "web/news.html",
        {**data, "page": paginator.get_page(data["page"])},
    )


def country_page(request, island_id: int):
    """国の公開ページ（フェーズ4-1）: 国旗・名前・元首・体制・通貨・人口・紹介文・年表（国ごとの
    紀年の日付）・来訪者の一言。持ち主には紹介文・紀年・国旗の入力欄、ほかの国を持つ人には一言の
    入力欄も。"""
    island, context, _ = _report(request, island_id, "country", "国の紹介")
    viewer = country_services.own_island(request.user)
    if request.method == "POST":
        try:
            _country_post(request, island, viewer, context["mine"])
        except country_services.CountryError as e:
            context["error"] = e.text
        else:
            return redirect("island_country", island.id)
    context["country"] = country.country_data(island, context["mine"], viewer)
    return render(request, "web/country.html", context)


def diary_page(request, island_id: int):
    """国の日誌（W-37）: 持ち主だけが読み書きする非公開のメモ欄。ほかの人には 404。"""
    island, context, _ = _report(request, island_id, "diary", "日誌")
    if not context["mine"]:
        raise Http404
    if request.method == "POST":
        try:
            country_services.set_diary(island, request.POST.get("text", ""))
        except country_services.CountryError as e:
            context["error"] = e.text
        else:
            return redirect(f"{reverse('diary', args=[island.id])}?saved=1")
    context.update(
        diary=country_services.diary_data(island),
        diary_max=country_services.DIARY_MAX,
        saved=request.GET.get("saved") == "1",
    )
    return render(request, "web/diary.html", context)


def _country_post(request, island: Island, viewer: Island | None, mine: bool) -> None:
    """国の公開ページの入力欄（action で見分ける）を受ける。持ち主だけが国自身の欄を書き換える。"""
    action = request.POST.get("action", "")
    if action == "note":
        country_services.post_note(request.user, island, request.POST.get("text", ""))
        return
    if not mine:
        raise country_services.CountryError("この国の持ち主だけが書き換えられます", 403)
    if action == "intro":
        country_services.set_intro(island, request.POST.get("intro", ""))
    elif action == "era":
        country_services.set_era(island, request.POST.get("kind", ""), request.POST.get("name"))
    elif action == "new_era":
        country_services.new_era(island, request.POST.get("name"))
    elif action == "flag":
        upload = request.FILES.get("flag")
        if upload is None:
            raise country_services.CountryError("PNG の画像を選んでください")
        country_services.set_flag(
            island, upload.read(django_settings.HAKONIWA_FLAG_MAX_KB * 1024 + 1)
        )
    elif action == "flag_clear":
        country_services.clear_flag(island)
    elif action == "monument_image":
        upload = request.FILES.get("image")
        if upload is None:
            raise country_services.CountryError("PNG の画像を選んでください")
        country_services.set_monument_image(
            island,
            request.POST.get("number"),
            upload.read(django_settings.HAKONIWA_FLAG_MAX_KB * 1024 + 1),
        )
    elif action == "monument_image_clear":
        country_services.clear_monument_image(island, request.POST.get("number"))
    elif action == "note_hide":
        country_services.hide_note(island, request.POST.get("note"))
    else:
        raise country_services.CountryError("何をするのか分かりません")


def island_flag(request, island_id: int):
    """国旗の画像。上げた PNG。上げていない（管理者が隠した）国は、国から決まる仮の旗（SVG）。"""
    island = get_object_or_404(Island.objects.only("id", "name"), pk=island_id)
    flag = Flag.objects.filter(island=island, hidden=False).first()
    if flag is not None:
        body, kind = bytes(flag.png), "image/png"
    else:
        body, kind = flag_png.provisional_flag(island.id, island.name).encode(), "image/svg+xml"
    response = HttpResponse(body, content_type=kind)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    response["Cache-Control"] = "public, max-age=300"
    return response


def monument_image(request, island_id: int, number: int):
    """自作の記念碑の絵。上げた PNG。上げていない（管理者が隠した）ときは仮の絵（SVG）。"""
    island = get_object_or_404(Island.objects.only("id", "name"), pk=island_id)
    get_object_or_404(
        Facility.objects.only("id"), island=island, number=number, kind="monument_custom"
    )
    image = MonumentImage.objects.filter(island=island, number=number, hidden=False).first()
    if image is not None:
        body, kind = bytes(image.png), "image/png"
    else:
        body = flag_png.provisional_monument(island.id, number).encode()
        kind = "image/svg+xml"
    response = HttpResponse(body, content_type=kind)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    response["Cache-Control"] = "public, max-age=300"
    return response


def plan_page(request, island_id: int):
    """計画（フェーズ3-4）: 計画経済の公定価格・生産目標と達成率・重点産業・財ごとの行列と、混合
    経済の値段の上限と下限。持ち主には命令の入力欄も。"""
    island, context, stats = _report(request, island_id, "plan", "")
    context["title"] = context["plan_label"] or "計画"
    context.update(
        plan=politics_views.plan_data(island, context["mine"], stats),
        stats=reports.stat_rows(stats, PLAN_STATS, context["currency"]),
    )
    if not context["mine"]:
        context["note"] = OTHERS_NOTE
    return render(request, "web/plan.html", context)


def turn_log(request, island_id: int):
    """ターンログ。ターンごとに区切り、新しいターンから並べる。"""
    island = get_object_or_404(Island, pk=island_id)
    mine = request.user.is_authenticated and island.owner_id == request.user.id
    turns = (
        display.visible_logs(TurnLog.objects.filter(island=island), mine, island)
        .order_by("-turn")
        .values_list("turn", flat=True)
        .distinct()
    )
    page = Paginator(list(turns), 10).get_page(request.GET.get("page"))
    entries = display.log_entries(
        display.visible_logs(TurnLog.objects.filter(island=island), mine, island)
        .filter(turn__in=list(page))
        .order_by("-turn", "id")
    )
    groups: dict[int, list] = {}
    for entry in entries:
        groups.setdefault(entry["turn"], []).append(entry)
    return render(
        request,
        "web/log.html",
        {
            "island": island,
            "mine": mine,
            "page": page,
            "groups": sorted(groups.items(), reverse=True),
        },
    )


def rankings_page(request):
    """統計の順位（フェーズ3-3）: 部門ごとの順位と、産業ごとの称号と上位の国。お金の額は、選んだ
    通貨（?base=島の番号。決めなければ見る人の国の通貨）に今のレートで直して比べる。"""
    world = World.objects.order_by("id").first()
    data = None
    if world is not None:
        own = None
        if request.user.is_authenticated:
            own = Island.objects.filter(owner=request.user, world=world).first()
        try:
            base = int(request.GET.get("base", ""))
        except ValueError:
            base = None
        if base is None or not world.islands.filter(pk=base).exists():
            base = rankings.default_base(world, own)
        data = rankings.ranking_data(world, base)
    return render(request, "web/rankings.html", {"world": world, "data": data})


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
    return render(request, "web/help.html", _help_context())


def help_start_page(request):
    """初めて遊ぶ人向けの入門。詳しい決まりは help_page（詳しい仕組み）にある。"""
    game = get_game()
    return render(
        request,
        "web/help_start.html",
        {
            "statuses": [
                {"name": status_help_name(key), "text": text} for key, text in STATUS_HELP
            ],
            "commute": game.config.get("labor.commute_radius"),
            "base_admin": game.config.get("orders.base_admin_power"),
            "power_fuel_turns": game.config.get("founding.power_fuel_turns"),
            "careful": _careful_help(game.config),
        },
    )


def _careful_help(config) -> dict:
    """入門の「軽く動かさないほうがいい数値」に出す数（W-255）。"""

    def percent(name: str) -> str:
        return f"{config.get(name) * 100:g}"

    return {
        "indexation": percent("labor.indexation"),
        "max_raise": percent("labor.max_raise"),
        "haircut": percent("bonds.haircut"),
        "default_premium": percent("bonds.default_premium"),
        "default_decay": config.get("bonds.default_decay_turns"),
        "default_exclusion": config.get("bonds.default_exclusion_turns"),
        "default_crunch": config.get("banking.default_crunch_turns"),
        "default_stability": -config.get("politics.events.default"),
        "default_stability_turns": config.get("politics.events.default_turns"),
        "exit_turns": config.get("firms.exit_loss_turns"),
        "entries": config.get("firms.entries_per_turn"),
        "cb_lock_turns": config.get("central_bank.switch_lock_turns"),
        "transition_turns": config.get("politics.transition.turns"),
        "working_years": config.get("education.working_years"),
        "grievance_max": config.get("politics.press.grievance_max"),
    }


# 入門の「施設の状態の言葉」（名前は messages.yaml の status_<key> から引く。stop_ で始まるものは
# そのままの名前。UI-84）
STATUS_HELP = (
    ("starting", "建ったばかりで、次の更新で動き出します。止まっているわけではありません。"),
    ("construction", "工事中です。人手・資材・お金が足りないと、かっこの中にわけが出ます。"),
    ("workers", "通える範囲に働き手が足りません。町の近くに建てるか、町が育つのを待ちます。"),
    ("engineers", "技術者が足りない分だけ稼働が下がります（いなくても6割は動く）。"),
    ("input", "原料が品切れで買えません。原料を作る施設を建てるか、輸入します。"),
    ("input_price", "原料が高くて採算が合いません。原料の供給が増えると戻ります。"),
    ("funds", "施設のお金が足りず原料を買えません。独立採算の施設は預金と融資を見ます。"),
    ("wages", "賃金を払うお金が足りません。売上と賃金・仕入れの釣り合いを見ます。"),
    ("power", "発電量が足りません。発電所の燃料か、発電所の数を見ます。"),
    ("no_grid", "発電所のある送電網につながっていません。送電線か送電網の工事でつなぎます。"),
    (
        "target",
        "売れ行きに合わせて作る量を減らしながら作り続けています。不具合ではありません。"
        "地図の窓に、目標を下げたわけと打てる手が出ます。",
    ),
    (
        "stop_target",
        "売れ行きに合わせて作る量を 0 まで下げ、止まっています（地図に赤い点）。"
        "地図の窓に、わけと打てる手が出ます。",
    ),
    ("budget", "国庫の同じ項目の年の予算が足りません。予算を増やすか、優先度を上げます。"),
    ("staff_plan", "人員計画で決めた人数の枠で人が足りません。計画の画面で枠を見直します。"),
    ("equipment", "設備が傷んで作れる量が減っています。国営なら公共事業費で直します。"),
    ("halted", "災害で一時止まっています。時間がたつと戻ります。"),
    ("public_price", "学校・大学の教材が高く、教育費で買えません。教育費を見直します。"),
    ("no_demand", "発電所の送電網に電気を使う所がありません。送電線で町や施設につなぎます。"),
    ("power_price", "電気料金が発電の費用に届きません。燃料の値段か、電気料金の上限を見ます。"),
    ("recipe_change", "生産方式を切り替えている間です。終わると新しい方式で動きます。"),
    ("cargo", "運送業・海運業が、運ぶ荷の量に合わせて動いています。不具合ではありません。"),
    ("business", "銀行が、預金と融資の大きさに合わせて動いています。不具合ではありません。"),
)


def status_help_name(key: str) -> str:
    """入門の施設の状態の言葉の名前（画面と同じ文。stop_ で始まるものは止まったときの文）。"""
    return display.message(key if key.startswith("stop_") else f"status_{key}")


def terms_page(request):
    """利用規約（W-215）。記録を持つ日数・時間は設定から出す。"""
    return render(
        request,
        "web/terms.html",
        {
            "remember_days": get_game().config.get("accounts.remember_days"),
            "throttle_hours": django_settings.HAKONIWA_THROTTLE_KEEP_HOURS,
        },
    )


# AI で遊ぶの手引きが hk.py の取り方として出す場所（リポジトリは公開）
HK_PY_URL = (
    "https://raw.githubusercontent.com/Yamada-Kohei/economic-hakoniwa/main/tools/playtest/hk.py"
)


def help_ai_page(request):
    """AI（Claude・ChatGPT など）で遊ぶ人向けの手引き: 鍵の作り方・AI ごとのつなぎ方・頼み方。"""
    interval_minutes = get_game().config.get("turn.interval_minutes")
    base_url = request.build_absolute_uri("/").rstrip("/")
    return render(
        request,
        "web/help_ai.html",
        {
            "base_url": base_url,
            "mcp_url": base_url + "/mcp",
            "hk_url": HK_PY_URL,
            "interval_hours": f"{interval_minutes / 60:g}" if interval_minutes else "",
            "calls_per_minute": django_settings.HAKONIWA_AI_CALLS_PER_MINUTE,
            "mark_turns": keys.MARK_TURNS,
            "max_keys": keys.MAX_KEYS,
        },
    )


def help_chain_page(request):
    """産業のつながりの手引き: 施設の作り方から品目どうしのつながりを図と表で見せる（UI-61）。"""
    return render(request, "web/help_chain.html", {"chain": chain_map.chain_data()})


def help_fx_page(request):
    """為替の効き方と打てる手の手引き（W-252）。数は game.yaml の fx・banking などから。"""
    config = get_game().config

    def percent(name: str) -> str:
        return f"{config.get(name) * 100:g}"

    return render(
        request,
        "web/help_fx.html",
        {
            "fx": {
                "band": percent("fx.band"),
                # 毎ターン同じ向きに値幅いっぱい動いたときの 1 年（12 ターン）の動き
                "band_year": f"{((1 + config.get('fx.band')) ** 12 - 1) * 100:.0f}",
                "keep_turns": config.get("fx.keep_turns"),
                "reserve_share": percent("fx.reserve_share"),
                "swap_quota": percent("fx.swap_quota"),
                "swap_issuer_share": percent("fx.swap_issuer_share"),
                "swap_spread": percent("fx.swap_spread"),
                "peg_band": percent("fx.peg_band"),
                "bank_share": percent("banking.foreign_share"),
                "bank_step": percent("banking.foreign_step"),
                "bank_margin": percent("banking.foreign_margin"),
                "trader_cut": percent("trade.trader_cut"),
                "merchant_turns": config.get("foreign_merchant.turns"),
                "cb_lock_turns": config.get("central_bank.switch_lock_turns"),
            },
        },
    )


def help_hakoniwa_page(request):
    """箱庭を遊んだことのある人向けの手引き。数は同じ手引きの変数を使う。"""
    return render(request, "web/help_hakoniwa.html", _help_context())


def _help_context() -> dict:
    game = get_game()
    defs = game.defs
    interval_minutes = game.config.get("turn.interval_minutes")
    facilities = [
        {
            "name": defs.name("facilities", fid),
            "terrain": "・".join(defs.name("terrain", t) for t in f["terrain"]),
            "notes": _placement_notes(f),
            "workers": f["workers"],
            "recipes": [
                {
                    "id": rid,
                    "name": defs.recipe_name(fid, rid),
                    "inputs": display.goods_list(r["inputs"]),
                    "outputs": display.goods_list(r["outputs"]),
                    "resource": defs.name("goods", r["requires_resource"])
                    if r.get("requires_resource")
                    else "",
                }
                for rid, r in f["recipes"].items()
                if not r.get("grade")  # 農場の段階の方式は、段階の説明にまとめる
            ],
        }
        for fid, f in defs.facilities.items()
        if f.get("buildable", True)
    ]
    orders = [
        {"name": defs.name("orders", oid), "cost": o["admin_cost"]}
        for oid, o in defs.orders.items()
    ]
    return {
        "facilities": facilities,
        "orders": orders,
        "commute": game.config.get("labor.commute_radius"),
        "interval_hours": f"{interval_minutes / 60:g}" if interval_minutes else "",
        "base_admin": game.config.get("orders.base_admin_power"),
        "max_admin": game.config.get("orders.max_admin_power"),
        "power_fuel_turns": game.config.get("founding.power_fuel_turns"),
        "admin_per_office": _admin_people(defs),
        # 金融と企業（フェーズ2-2）の説明に出す数
        "arrears_limit": game.config.get("loans.arrears_limit"),
        "sale_turns": game.config.get("firms.sale_turns"),
        "sale_cut_turns": game.config.get("firms.sale_cut_turns"),
        "site_turns": game.config.get("firms.site_work")["turns"],
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
                "water": f"{s.get('water', 0) * 100:g}",
                "land_short": bool(s.get("land_short")),
                "materials": display.goods_list(s.get("materials", {})),
            }
            for sid, s in ((sid, defs.settlements[sid]) for sid in defs.settlement_levels())
        ],
        "units": [
            {"name": defs.name("goods", g), "unit": defs.unit_name(g)}
            for g in display.goods_in_order(defs)
            if defs.unit_name(g)
        ],
        "grades": _grade_help(game),
        "trade": _trade_help(game),
        "diplomacy": _diplomacy_help(game),
        "awards": _awards_help(game),
        **_per_person_help(game),
        **_goods_help(game),
    }


def _grade_help(game) -> list[dict]:
    """手引きの「施設の建て増しと農場の段階」に出す、段階ごとの人手と収穫の割合と工事の資材（W-190）。"""
    defs = game.defs
    out = []
    for kind, fdef in defs.facilities.items():
        for step, raw in zip(fdef.get("grade_steps", ()), fdef.get("grades", ()), strict=True):
            out.append(
                {
                    "facility": defs.name("facilities", kind),
                    "name": display.message(f"grade_{step['name']}"),
                    "labor": f"{raw['labor'] * 100:g}",
                    "output": f"{raw['output'] * 100:g}",
                    "materials": display.goods_list(step["materials"]),
                    "turns": step["turns"],
                    "machines": step["machines"],
                    "chemicals": bool(raw.get("scale_inputs")),
                }
            )
    return out


def _per_person_help(game) -> dict:
    """手引きの「資源の単位」に出す、1人1ターンの食料と家庭の電力（見せる単位。W-30）。"""
    config, defs = game.config, game.defs
    food = config.get("population.food_per_person") * defs.weight_scale()
    power = defs.shown_amount(config.get("power.good"), config.get("power.household_use"))
    return {
        "food_per_person": f"{food:g}",
        "power_per_person": f"{power:g}",
        "weight_scale": f"{defs.weight_scale():g}",
    }


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
        # 衣料は持ち物（LB-44）: 1年に傷む割合と、必需品の予算に対する上限
        "clothing": {
            "wear": percent(config.get("household.clothing.wear")),
            "share": percent(config.get("household.clothing.share")),
        },
        # 売れ残りの値引き（MK-41。アウトレット）
        "clearance": {
            "ratio": f"{config.get('market.clearance_ratio'):g}",
            "turns": config.get("market.clearance_turns"),
            "floor": percent(config.get("market.clearance_floor")),
        },
        "trucks": {
            "drivers": config.get("freight.trucks.drivers"),
            "share": percent(config.get("freight.trucks.max_share")),
        },
        "shortage": {
            "share": percent(config.get("records.shortage.share")),
            "turns": config.get("records.shortage.turns"),
        },
        # 食べ物の表: 1 単位で何人前か・食事の質・日持ち（質の低い順）
        "foods": [
            {
                "name": defs.name("goods", g),
                "nutrition": f"{gdef['food']['nutrition']:g}",
                "quality": f"{gdef['food'].get('quality', 0):g}",
                "shelf_life": gdef.get("shelf_life"),
            }
            for g, gdef in sorted(
                ((g, d) for g, d in defs.goods.items() if d.get("food")),
                key=lambda item: (item[1]["food"].get("quality", 0), item[0]),
            )
        ],
        "food_reserve_turns": config.get("food_reserve.turns"),
    }


def _people(n: int) -> str:
    """人数を「5 万」「1,000」のように（1 万以上は万で数える）。"""
    if n >= 10000 and n % 10000 == 0:
        return f"{n // 10000:,} 万"
    return f"{n:,}"


def _awards_help(game) -> dict:
    """手引きの「国のページ・記念碑・賞」に出す数（game.yaml の awards・monuments）。人数は
    世界の大きさの倍を掛けた後の値（hakoniwa_core/scale.py）で、倍 20 の世界では 20 倍（W-211）。"""
    config = game.config
    years = lambda turns: f"{turns / 12:g}"  # noqa: E731
    return {
        "prosperity": "・".join(_people(int(n)) for n in config.get("awards.prosperity")),
        "peace_kills": _people(int(config.get("awards.peace_kills"))),
        "calm_years": years(config.get("awards.calm_turns")),
        "calamity_count": config.get("awards.calamity_count"),
        "founding_years": years(config.get("monuments.founding_turns")),
        "founding_turns": config.get("monuments.founding_turns"),
        "peace_years": years(config.get("monuments.peace_turns")),
        "peace_turns": config.get("monuments.peace_turns"),
        "cenotaph_dead": _people(int(config.get("monuments.cenotaph_dead"))),
        "ledger_years": years(config.get("monuments.ledger_turns")),
        "ledger_turns": config.get("monuments.ledger_turns"),
        "verification_turns": config.get("monuments.verification_turns"),
    }


def _diplomacy_help(game) -> dict:
    """手引きの「外交と条約」に出す数（game.yaml の diplomacy）。"""
    config = game.config
    return {
        **{k: config.get(f"diplomacy.{k}") for k in diplomacy.RULES},
        "violation_premium": f"{config.get('diplomacy.violation_premium') * 100:g}",
        "violation_premium_max": f"{config.get('diplomacy.violation_premium_max') * 100:g}",
        "hall_per_day": django_settings.HAKONIWA_HALL_PER_DAY,
    }


def _trade_help(game) -> dict:
    """手引きの「貿易と為替」に出す数（game.yaml の trade・fx・foreign_merchant と定義データ）。"""
    config, defs = game.config, game.defs
    settings = config.get("foreign_merchant")
    return {
        "ship_speed": config.get("trade.ship_speed"),
        "ship_capacity": config.get("trade.ship_capacity") * defs.weight_scale(),
        "ship_life": config.get("trade.ship_life"),
        "start_ships": config.get("trade.start_ships"),
        "fuel": "・".join(defs.name("goods", g) for g in config.get("trade.ship_fuel")),
        "port_capacity": config.get("trade.port_capacity") * defs.weight_scale(),
        "trader_cut": f"{config.get('trade.trader_cut') * 100:g}",
        "trader_max": defs.facilities[config.get("trade.trader_facility")].get("max_per_island"),
        "bloc_band": f"{config.get('trade.bloc_band') * 100:g}",
        "cancel_turns": config.get("trade.contract_cancel_turns"),
        "proposal_turns": contracts.KEEP_ENDED,
        "fx_band": f"{config.get('fx.band') * 100:g}",
        "merchant_turns": settings["turns"],
        "merchant_taper_from": settings["taper_from"],
        "merchant_multiplier": f"{settings['multiplier']:g}",
        "merchant_buy_multiplier": f"{settings.get('buy_multiplier', 0):g}",
        "merchant_goods": "・".join(defs.name("goods", g) for g in settings["goods"]),
        "tradable": "・".join(defs.name("goods", g) for g in tradable_goods(defs)),
        # 船の前注文（FX-60）: 手付け・建造の間に払う割合と、取り消すまでの遅れ
        "order_deposit": f"{config.get('trade.order_deposit') * 100:g}",
        "order_progress": f"{config.get('trade.order_progress') * 100:g}",
        "order_late_turns": config.get("trade.order_late_turns"),
    }


def _admin_people(defs) -> int | None:
    """行政力 1 に要る公務員の数（admin_power を持つ施設の係数から）。"""
    rates = [f["admin_power"] for f in defs.facilities.values() if f.get("admin_power")]
    return round(1 / max(rates)) if rates else None


def _placement_notes(f) -> str:
    defs = get_game().defs
    notes = []
    if f.get("zone"):
        notes.append(f"{defs.name('messages', 'zone_' + f['zone'])}に指定したマスのみ")
    if f.get("site") == "town":  # 町のマスに入る店。数は町の大きさで決まる（回7）
        shops = [
            f"{defs.name('settlements', level)} {s['shops']}"
            for level, s in defs.settlements.items()
            if s.get("shops")
        ]
        notes.append(f"町のマスに入る（1つの町に{'・'.join(shops)}まで）")
    if f.get("coastal"):
        notes.append("海に面したマスのみ")
    if f.get("near_capital") is not None:
        notes.append(f"首都から {f['near_capital']} マス以内")
    if f.get("max_per_island") is not None:
        notes.append(f"1国 {f['max_per_island']} か所まで")
    return "・".join(notes)
