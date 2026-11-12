"""画面と知らせ（UI-62〜UI-70）の境界と、画面・API・MCP・要約の一致。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.contrib import admin
from django.core.cache import cache
from django.test import Client

from hakoniwa_core.units import to_basis
from server.accounts import keys
from server.game import get_game
from server.turns.models import TurnLog
from server.web import display, overview
from server.world import models as m
from server.world.admin import IslandAdmin
from server.world.repository import WorldRepository
from tests.server.test_ai_players import _keyed, _players, _set_turn, _tool
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_calls():
    cache.clear()
    yield
    cache.clear()


def _answer(ai, tool: str, **arguments):
    text, error = _tool(ai, tool, **arguments)
    assert not error, text
    return json.loads(text)


def test_notable_changes_pick_by_size_and_skip_unobserved(world):
    """単位の違う指標を目安で比べ、未観測や GDP に対して小さい残高差を除く。"""
    (owner, _, _), (island, _, _) = _players(world)
    before = {
        "population": 1000,
        "gdp": 1000,
        "food.ratio": 1,
        "treasury": 0,
        "firms.bankrupt": 0,
    }
    now = {
        "population": 1040,
        "gdp": 1150,
        "food.ratio": 0.9,
        "treasury": 50,
        "firms.bankrupt": 5,
        "happiness": 99,
    }
    rows = overview.notable_changes(now, before, "¤")
    assert [row["key"] for row in rows] == ["firms.bankrupt", "population", "gdp"]
    assert rows[1] == {
        "key": "population",
        "label": "人口",
        "before": "1,000人",
        "now": "1,040人",
        "page": "economy",
    }
    assert overview.notable_changes({"happiness": 99}, {}, "¤") == []
    assert overview.notable_changes({"gdp": 1000, "treasury": 50}, before, "¤") == []

    # 率は0〜1の割合、幸福度は表示の数。目安ちょうどを含み、直下を除く。
    thresholds = {
        "population": (1000, 10),
        "unemployment": (0.1, 0.01),
        "food.ratio": (0.8, 0.05),
        "power.shortage": (0.1, 0.05),
        "happiness": (50, 3),
        "gdp": (1000, 50),
        "inflation": (0.1, 0.02),
        "treasury": (2000, 200),
        "debt": (2000, 200),
        "firms.bankrupt": (0, 1),
    }
    for key, (value, threshold) in thresholds.items():
        old = {key: value, "gdp": 1000} if key != "gdp" else {key: value}
        new = {**old, key: value + threshold}
        assert [r["key"] for r in overview.notable_changes(new, old, "¤")] == [key]
        assert overview.notable_changes({**old, key: value + threshold * 0.99}, old, "¤") == []

    island.stats.all().delete()
    _stats(island, 19, before)
    _stats(island, 20, now)
    _set_turn(world, 20)
    _, token = keys.issue(owner)
    ai = _keyed(token)
    summary = ai.get(f"/api/islands/{island.id}/summary").json()
    expected = overview.notable_changes(now, before, island.names.get("currency_symbol", ""))
    assert summary["notable_changes"] == expected
    assert (
        ai.get(f"/api/islands/{island.id}/changes", {"since": 19}).json()["notable_changes"]
        == expected
    )
    assert _answer(ai, "summary", island=island.id)["notable_changes"] == expected
    assert _answer(ai, "changes", island=island.id, since=19)["notable_changes"] == expected


def test_turn_log_filters_by_kind_and_facility(world):
    """マスのない運送業を種類で絞り、非公開の行は条件に一致しても返さない。"""
    (owner, visitor, _), (island, _, _) = _players(world)
    island.logs.all().delete()
    for kind, facility, extra in (
        ("facility_started", "transport", {}),
        ("facility_restarted", "transport", {}),
        ("facility_started", "farm", {"x": 1, "y": 1}),
        ("defense_set", "transport", {}),
    ):
        TurnLog.objects.create(
            island=island, turn=4, kind=kind, args={"facility": facility, **extra}
        )
    params = {"from": 4, "to": 4, "kind": "facility_started", "facility": "transport"}
    public = Client().get(f"/api/islands/{island.id}/log", params).json()
    assert len(public["entries"]) == 1
    row = public["entries"][0]
    assert row["facility"] == "transport"
    assert row["facility_name"] == get_game().defs.name("facilities", "transport")
    assert "tile" not in row
    assert (
        Client()
        .get(f"/api/islands/{island.id}/log", {**params, "kind": "defense_set"})
        .json()["entries"]
        == []
    )
    _, token = keys.issue(visitor)
    ai = _keyed(token)
    assert _answer(ai, "turn_log", island=island.id, **params) == public
    assert (
        _answer(ai, "turn_log", island=island.id, **{**params, "kind": "defense_set"})["entries"]
        == []
    )
    mine = Client()
    mine.force_login(owner)
    assert (
        len(
            mine.get(f"/api/islands/{island.id}/log", {**params, "kind": "defense_set"}).json()[
                "entries"
            ]
        )
        == 1
    )
    page = mine.get(f"/islands/{island.id}/log/").content.decode()
    assert 'name="facility"' in page and 'name="x"' in page and 'name="y"' in page


def test_chain_is_public_without_hidden_facilities(world):
    """匿名・発見前・発見後・持ち主で同じ可視性の判定を使い、私有の帳簿を出さない。"""
    (owner, visitor, _), (island, other, _) = _players(world)
    farm = island.facilities.filter(kind="farm").order_by("number").first()
    assert farm is not None
    farm.name = "隠した農場"
    farm.ext = {**farm.ext, "hidden": True, "found": [other.id]}
    farm.status = "input:coal"
    farm.built = True
    farm.save()
    _stats(island, 1, {"production.grain": 100})
    public = Client().get(f"/api/islands/{island.id}/chain").json()
    mine = Client()
    mine.force_login(owner)
    owned = mine.get(f"/api/islands/{island.id}/chain").json()

    def count(data):
        grain = next(g for tier in data["tiers"] for g in tier["goods"] if g["good"] == "grain")
        return sum(row["built"] for row in grain["makers"] if row["kind"] == "farm")

    assert count(owned) == count(public) + 1
    assert "隠した農場" not in json.dumps(public, ensure_ascii=False)
    assert "隠した農場" in json.dumps(owned, ensure_ascii=False)
    for field in ("accounts", "books", "traders", "fleet", "buyer", "seller"):
        assert f'"{field}"' not in json.dumps(public)
    page = Client().get(f"/islands/{island.id}/chain/").content.decode()
    assert "品目ごとの鎖" in page and "隠した農場" not in page
    facilities = Client().get(f"/islands/{island.id}/facilities/").content.decode()
    assert f"/islands/{island.id}/chain/" in facilities
    _, token = keys.issue(visitor)
    ai = _keyed(token)
    discovered = ai.get(f"/api/islands/{island.id}/chain").json()
    assert count(discovered) == count(owned)
    assert _answer(ai, "report", island=island.id, page="chain") == discovered


def test_resumed_island_is_told_about_caretaker_treaties(world, monkeypatch):
    """休止期間内に当該島の代理人が結んだ分だけ、復帰後の所定期間に知らせる。"""
    (owner, _, _), (island, other, _) = _players(world)
    model_admin = IslandAdmin(m.Island, admin.site)
    monkeypatch.setattr(model_admin, "message_user", lambda *args, **kwargs: None)
    selected = m.Island.objects.filter(pk=island.pk)
    _set_turn(world, 8)
    model_admin.make_inactive(None, selected)
    island.refresh_from_db()
    assert island.inactive and island.ext["inactive_from"] == 8
    _set_turn(world, 12)
    model_admin.make_active(None, selected)
    island.refresh_from_db()
    assert not island.inactive and island.ext["resumed_turn"] == 12
    treaties = []
    for number, start, caretaker in (
        (1, 9, island.id),
        (2, 10, island.id),
        (3, 7, island.id),
        (4, 13, island.id),
        (5, 10, other.id),
    ):
        treaties.append(
            {
                "id": number,
                "title": f"条約{number}",
                "parties": [island.id, other.id],
                "proposer": other.id,
                "clauses": [{"type": "ceasefire"}],
                "created": start,
                "start": start,
                "state": "in_force",
                "caretaker": caretaker,
            }
        )
    world.ext = {**world.ext, "diplomacy": {"treaties": treaties, "wars": []}}
    world.save(update_fields=["ext"])
    repository = WorldRepository(world)
    state = repository.load()
    state.islands[island.id].ext["roundtrip_marker"] = True
    repository.save(state)
    island.refresh_from_db()
    assert island.ext["inactive_from"] == 8 and island.ext["resumed_turn"] == 12
    assert island.ext["roundtrip_marker"] is True
    _, token = keys.issue(owner)
    ai = _keyed(token)
    notice = "休止の間に、停戦・講和を代わりに 2 件結んでいます（外交の画面）"
    assert notice in ai.get(f"/api/islands/{island.id}/summary").json()["alerts"]
    assert notice in _answer(ai, "summary", island=island.id)["alerts"]
    assert notice not in Client().get(f"/api/islands/{island.id}/summary").json()["alerts"]
    browser = Client()
    browser.force_login(owner)
    assert notice in browser.get(f"/islands/{island.id}/home/").content.decode()
    diplomacy = ai.get(f"/api/islands/{island.id}/diplomacy").json()
    assert diplomacy["treaties"][0]["caretaker"]
    assert _answer(ai, "report", island=island.id, page="diplomacy") == diplomacy
    assert (
        "休止中に代わりに結んだ" in browser.get(f"/islands/{island.id}/diplomacy/").content.decode()
    )
    _set_turn(world, 12 + overview.GUIDE_TURNS - 1)
    assert notice in ai.get(f"/api/islands/{island.id}/summary").json()["alerts"]
    _set_turn(world, 12 + overview.GUIDE_TURNS)
    assert notice not in ai.get(f"/api/islands/{island.id}/summary").json()["alerts"]


def test_capped_yield_is_marked_and_alerted(world):
    """上限ちょうどの区分だけを公開し、画面・要約・MCP の知らせをそろえる。"""
    (_, visitor, _), (island, _, _) = _players(world)
    maximum = get_game().config.get("bonds.max_yield")
    cap = to_basis(maximum)
    island.economy = {**island.economy, "finance": {"yields": [cap - 1, cap, 0, cap]}}
    island.save(update_fields=["economy"])
    public = Client()
    fiscal = public.get(f"/api/islands/{island.id}/fiscal").json()["fiscal"]
    summary = public.get(f"/api/islands/{island.id}/summary").json()
    finance = public.get(f"/api/islands/{island.id}/finance").json()
    for data in (fiscal, summary, finance):
        assert data["yield_capped"] is True
        assert data["yield_capped_buckets"] == [1, 3]
        assert [r["yield_capped"] for r in data["yields"]] == [False, True, False, True]
    notice = (
        f"国債の利回りが上限の {maximum * 100:g}% に張り付いています。"
        "市場が求める利回りはこれより高いかもしれません"
    )
    assert notice in summary["alerts"]
    assert notice in public.get(f"/islands/{island.id}/home/").content.decode()
    assert "上限" in public.get(f"/islands/{island.id}/fiscal/").content.decode()
    _, token = keys.issue(visitor)
    ai = _keyed(token)
    assert _answer(ai, "report", island=island.id, page="fiscal")["fiscal"] == fiscal
    assert _answer(ai, "summary", island=island.id)["yield_capped_buckets"] == [1, 3]
    island.economy["finance"]["yields"] = [cap - 1] * 4
    island.save(update_fields=["economy"])
    ordinary = public.get(f"/api/islands/{island.id}/summary").json()
    assert ordinary["yield_capped"] is False and ordinary["yield_capped_buckets"] == []
    assert notice not in ordinary["alerts"]


def test_shipping_shows_tons_and_fuel_heading(world):
    """半隻分の稼働も重さと割合で示し、石炭と石油燃料の欄を区別する。"""
    (owner, _, _), (island, _, _) = _players(world)
    game = get_game()
    capacity = game.config.get("trade.ship_capacity")
    island.facilities.filter(kind="shipping").delete()
    number = island.facilities.order_by("-number").first().number + 1
    shipping = m.Facility.objects.create(
        island=island,
        number=number,
        kind="shipping",
        x=-20,
        y=-20,
        built=True,
        economy={"fleet": [[0, 2000]], "busy": {"1": capacity * 500}},
    )
    for good in ("coal", "fuel"):
        m.Stock.objects.create(island=island, facility=shipping, good=good, quantity=10000)
    _, token = keys.issue(owner)
    ai = _keyed(token)
    data = ai.get(f"/api/islands/{island.id}/trade").json()
    row = data["fleet"]["shipping"]
    assert row["ships"] == 2
    assert row["capacity_tons"] == display.tons(capacity * 2000)
    assert row["busy_tons"] == display.tons(capacity * 500)
    assert row["free_tons"] == display.tons(capacity * 1500)
    assert row["busy_share"] == pytest.approx(0.25)
    assert _answer(ai, "report", island=island.id, page="trade")["fleet"]["shipping"] == row
    browser = Client()
    browser.force_login(owner)
    page = browser.get(f"/islands/{island.id}/trade/").content.decode()
    assert "航海に使っている量" in page and "空いている量" in page and "25%" in page
    assert "隻分" not in page
    assert "船の燃料: 石炭" in page and "船の燃料: 石油燃料" in page
    assert game.defs.name("goods", "fuel") == "石油燃料"
    help_page = browser.get("/help/").content.decode()
    turns = game.config.get("founding.power_fuel_turns")
    assert f"発電所は使う石炭を{turns}か月分持って建国します" in help_page.replace(" ", "")


def test_island_map_has_reload_notice(client, world):
    """モジュールが読み込まれなくても表示できる知らせと再読込操作をページに置く。"""
    island = m.Island.objects.order_by("id").first()
    page = client.get(f"/islands/{island.id}/").content.decode()
    notice = re.search(r'<div\b[^>]*id="map-load-notice"[^>]*>(.*?)</div>', page, re.S)
    assert notice is not None and "hidden" in notice.group(0)
    assert "地図を読み込めませんでした。再読み込みしてください" in notice.group(1)
    assert 'id="map-reload"' in notice.group(1)
    assert page.index('id="map-load-notice"') < page.index('id="island-map"')
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", page, re.S)
    fallback = [
        body for attrs, body in scripts if "module" not in attrs and "map-load-notice" in body
    ]
    assert len(fallback) == 1
    assert "15000" in fallback[0] and "dataset.ready" in fallback[0]
    assert "location.reload()" in fallback[0]


def test_construction_wage_messages_explain_funding():
    """公営は年予算の月割りと財政、民営は会社の資金を案内する。"""
    from hakoniwa_core.log import format_log

    defs = get_game().defs
    args = {"x": 1, "y": 2, "facility": "farm"}
    public = format_log(defs, "construction_no_wages", args)
    private = format_log(defs, "construction_no_wages_private", args)
    assert "今月の公共事業費（年の予算の月割り）を使い切った" in public
    assert "財政の画面で公共事業費を上げると進みます" in public
    assert "会社の資金が足りない" in private and "会社の資金が増えると進みます" in private
    assert "公共事業費" not in private


def test_steel_industry_name_and_title_are_distinct(world):
    """鉄鋼業の分類と鋼鉄の品目、称号の文を読み分けられる。"""
    defs = get_game().defs
    assert defs.name("industries", "steel") == "鉄鋼業"
    assert defs.name("goods", "steel") == "鋼鉄"
    island = m.Island.objects.first()
    log = TurnLog.objects.create(
        island=island, turn=1, kind="title_gained", args={"industry": "steel"}
    )
    text = display.log_entries([log])[0]["text"]
    assert "鉄鋼業" in text and "鋼都" in text and "steel" not in text
    source = Path("server/static/js/help-chain.js").read_text(encoding="utf-8")
    assert "'鉄鋼業'" in source and "'鉄鋼'" not in source


def test_requested_wording_and_fuel_contexts(client, world):
    """指定された用語だけを直し、発電と海運の燃料一般を保つ。"""
    from hakoniwa_core.log import format_log

    help_page = client.get("/help/").content.decode()
    for text in (
        "命令で売り買いできます",
        "1回の売り買いの命令",
        "電力と日持ちの短い物の備蓄",
        "家計は電力と",
        "そのターンの外交の処理から効きます",
        "古い版から続く島は移し替え",
        "輸送船は石油燃料と石炭のどちらでも動き",
    ):
        assert text in help_page
    defs = get_game().defs
    assert "石油燃料" in format_log(defs, "unit_no_fuel", {"unit": 1})
    assert "石油燃料" in format_log(defs, "fleet_no_fuel", {"unit": 1})
    assert "貿易の処理に回しました" in format_log(defs, "trade_request_sent", {"order": "buy"})
    assert "貿易の処理で行えませんでした" in format_log(
        defs, "trade_request_failed", {"order": "buy", "reason": "no_money"}
    )
    island = m.Island.objects.first()
    politics = client.get(f"/islands/{island.id}/politics/").content.decode()
    assert "この状態が続くと反乱軍が現れて内戦になります" in politics
    assert "反乱はフェーズ4" not in politics
    top = Path("server/web/top.yaml").read_text(encoding="utf-8")
    assert "サービスの仕事と人口の増え方を見直しました（バランス調整）。" in top


def test_report_tables_keep_full_width_and_numeric_alignment(world):
    """持ち主の表と命令を縦に並べ、数の欄を右寄せ・折返しなしにする。"""
    from pathlib import Path

    (owner, _, _), (island, _, _) = _players(world)
    mine = Client()
    mine.force_login(owner)
    _stats(island, 1, {"policy_rate": 0.03, "population": 1000})
    for page in ("market", "fiscal", "finance", "trade"):
        response = mine.get(f"/islands/{island.id}/{page}/")
        assert response.status_code == 200
        assert b'class="report-main"' in response.content
    finance = mine.get(f"/islands/{island.id}/finance/").content.decode()
    assert re.search(r'<td class="num">[\d.]+%</td>', finance)
    assert "<td>" + display.bond_trade_text(0) + "</td>" in finance
    css = Path("server/static/css/site.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr) 340px" not in css
    assert ".report-orders:empty { display: none; }" in css
    assert "table.market-table th, table.market-table td { white-space: nowrap; }" in css
    assert "table td.num, table th.num { text-align: right; white-space: nowrap; }" in css
    assert "table td.numeric-with-note { white-space: normal; }" in css
    assert ".numeric-value { white-space: nowrap; }" in css


def test_farm_selected_recipe_build_estimate_and_standing_labels(world):
    """選択方式の人手と現行の建設費、倉庫の買い・売り・備蓄の量の見出し。"""
    from hakoniwa_core import facilities as fac
    from server.web import api

    game = get_game()
    farm = api._facility_def("farm", game.defs.facilities["farm"])
    manual = farm["recipes"]["luxury_crop"]
    mechanized = farm["recipes"]["luxury_crop_mechanized"]
    assert manual["workers"] == 500
    assert mechanized["workers"] == fac.workers_needed(game.defs, "farm", "luxury_crop_mechanized")
    assert mechanized["workers"] < manual["workers"]
    # 段階の作り方は初めからその段階で建ち、段階までの工事の工期と資材を足す（W-257）
    assert "初めから機械化の段階で建ちます" in mechanized["build_hint"]
    turns, materials = fac.build_spec(game.defs, "farm", "luxury_crop_mechanized")
    assert mechanized["construction"]["turns"] == turns
    assert mechanized["construction"]["materials"] == api._shown_amounts(materials)
    assert "construction" not in manual
    panel = Path("server/static/js/island/panel.js").read_text(encoding="utf-8")
    assert "this.buildInfo(form.elements.facility.value, form.elements.recipe?.value)" in panel
    orders = Path("server/static/js/island/orders.js").read_text(encoding="utf-8")
    for text in ("1ターンに買う量", "1ターンに売る量", "備蓄の目標量"):
        assert text in orders


def test_warehouse_standing_noscript_label_and_start_explanation(world):
    """JavaScript のない画面にも汎用の量の見出しと次ターンからの説明を出す。"""
    (owner, _, _), (island, _, _) = _players(world)
    mine = Client()
    mine.force_login(owner)
    page = mine.get(f"/islands/{island.id}/market/grain/").content.decode()  # 品目のページの箱
    fallback = re.search(r"<noscript>(.*?)</noscript>", page, re.S).group(1)
    assert "1ターンの売り買いの量／備蓄の目標量" in fallback
    assert "置いた次のターンから動きます" in fallback


def test_duplicate_letters_are_rejected_across_screen_api_and_mcp(world):
    """送信元・宛先・件名・本文が同じ公電は入口を変えても1分間は1通だけ届く。"""
    from server.letters.models import Letter

    (owner, _, _), (island, other, third) = _players(world)
    client = Client()
    client.force_login(owner)
    payload = {"to": other.id, "subject": "会談", "body": "相談します"}
    assert client.post("/letters/new/", payload).status_code == 302
    duplicate = client.post("/api/letters", payload, content_type="application/json")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "同じ公電を続けて送ろうとしています"
    page = client.post("/letters/new/", payload).content.decode()
    assert "同じ公電を続けて送ろうとしています" in page
    assert "js/letter-send.js" in page and "data-letter-send" in page
    _, token = keys.issue(owner)
    answer, error = _tool(_keyed(token), "send_letter", **payload)
    assert error and "同じ公電を続けて送ろうとしています" in answer
    assert Letter.objects.count() == 1
    assert TurnLog.objects.filter(island=other, kind="letter_received").count() == 1
    for changed in ({"to": third.id}, {"subject": "別件"}, {"body": "別の本文"}):
        response = client.post(
            "/api/letters", {**payload, **changed}, content_type="application/json"
        )
        assert response.status_code == 201, response.content


def test_duplicate_letter_window_includes_one_minute_and_hidden_letters(world, monkeypatch):
    """非表示・既読でも重複を防ぎ、ちょうど1分後まで拒み、その後は送れる。"""
    from datetime import timedelta

    from server.letters import services as letter_services

    (owner, _, _), (island, other, _) = _players(world)
    first = letter_services.send_letter(owner, island, other.id, " 件名 ", " 本文 ")
    first.hidden = True
    first.save(update_fields=["hidden"])
    fixed = first.sent_at + timedelta(minutes=1)
    monkeypatch.setattr(letter_services.timezone, "now", lambda: fixed)
    with pytest.raises(letter_services.LetterError, match="同じ公電"):
        letter_services.send_letter(owner, island, other.id, "件名", "本文")
    fixed += timedelta(microseconds=1)
    second = letter_services.send_letter(owner, island, other.id, "件名", "本文")
    assert second.pk != first.pk


@pytest.mark.parametrize(("quantity", "price"), [(500, 1), (1, 500)])
def test_treaty_contract_shown_units_survive_signing_and_shipment(world, quantity, price):
    """条約の500トン指定と1トン500指定を、署名後の契約・送り出しまで確かめる。"""
    from server.turns.services import run_turn
    from tests.server.test_diplomacy import _orders as treaty_orders
    from tests.server.test_diplomacy import _players as treaty_players

    (seller_client, seller), (buyer_client, buyer), _ = treaty_players(world)
    run_turn(force=True)
    clause = {
        "type": "contract",
        "seller": seller.id,
        "good": "building_materials",
        "quantity": quantity,
        "price": price,
        "currency": buyer.id,
        "turns": 1,
        "delivery": "warehouse",
    }
    treaty_orders(
        seller_client, seller, ("propose_treaty", {"partner": buyer.id, "clauses": [clause]})
    )
    pending = seller.orders.get(status="pending").payload["clauses"][0]
    assert pending["quantity"] == quantity / 25
    assert pending["price"] == price * 25
    run_turn(force=True)
    [offer] = buyer_client.get(f"/api/islands/{buyer.id}/diplomacy").json()["incoming"]
    assert offer["clauses"][0]["terms"]["quantity"] == quantity
    assert offer["clauses"][0]["terms"]["price"] == price
    treaty_orders(buyer_client, buyer, ("sign_treaty", {"treaty": offer["id"]}))
    run_turn(force=True)
    [contract] = buyer_client.get(f"/api/islands/{buyer.id}/trade").json()["contracts"]
    assert contract["quantity"] == quantity and contract["price"] == price
    current = m.World.objects.get(pk=world.pk)
    [raw] = current.ext["trade"]["contracts"]
    assert raw["qty"] == round(quantity / 25 * 1000)
    m.Stock.objects.create(
        island=seller, good="building_materials", made_turn=current.turn, quantity=100_000
    )
    run_turn(force=True)
    [contract] = buyer_client.get(f"/api/islands/{buyer.id}/trade").json()["contracts"]
    assert contract["quantity"] == quantity
    assert contract["shipped"] == quantity, contract
    current.refresh_from_db()
    [shipment] = [s for s in current.ext["trade"]["shipments"] if s["kind"] == "contract"]
    assert display.amount(sum(q for _, q in shipment["batches"]), "building_materials") == quantity
    while current.turn < shipment["arrive"]:
        run_turn(force=True)
        current.refresh_from_db()
    arrived = buyer.logs.filter(kind="cargo_arrived", args__good="building_materials")
    assert (
        sum(display.amount(round(e.args["amount"] * 1000), "building_materials") for e in arrived)
        == quantity
    )


@pytest.mark.parametrize(
    "symbol, prefix", [("RYO", "RYO "), ("R2", "R2 "), ("¤", "¤"), ("$", "$"), ("", "")]
)
def test_currency_symbols_separate_alphanumeric_amounts(symbol, prefix):
    assert display.money_text(12145145, symbol) == f"{prefix}12,145,145"
    assert display.money_text(-12145, symbol) == f"-{prefix}12,145"
    assert display.price_text("grain", 1, symbol).startswith(prefix)
    assert display.stat_text("price.grain", 1.25, "money", symbol).startswith(prefix)
    assert display.stat_text("trade.value", 12145, f"money:{symbol}") == f"{prefix}12,145"


def test_log_grade_names_and_existing_enum_names_are_localized(world):
    """実際に保存する段階の名前を画面・API・MCPで日本語にする。"""
    (owner, _, _), (island, _, _) = _players(world)
    island.logs.all().delete()
    for kind in ("grade_started", "grade_done"):
        for grade in ("manual", "mechanized", "modern", "grade_mechanized"):
            TurnLog.objects.create(
                island=island,
                turn=1,
                kind=kind,
                args={"x": 21, "y": 19, "facility": "farm", "grade": grade},
            )
    entries = display.log_entries(island.logs.order_by("id"))
    for row in entries:
        assert any(name in row["text"] for name in ("手作業", "機械化", "近代化"))
        assert not re.search(r"[A-Za-z_]", row["text"])
    assert entries[0]["tile"] == (21, 19)
    assert entries[0]["facility"] == "farm"
    assert island.logs.first().args["grade"] == "manual"
    _, token = keys.issue(owner)
    ai = _keyed(token)
    api = ai.get(f"/api/islands/{island.id}/log", {"from": 1, "to": 1}).json()
    assert _answer(ai, "turn_log", island=island.id, **{"from": 1, "to": 1}) == api
    assert not any("mechanized" in row["text"] for row in api["entries"])


def test_all_literal_turn_log_templates_format_without_internal_names():
    """全ての文字列で指定されたログの種類を調べ、表示名の差し込み漏れを検出する。"""
    import ast
    import string
    from pathlib import Path
    from types import SimpleNamespace

    defs = get_game().defs
    kinds = set()
    for path in (Path(__file__).resolve().parents[2] / "hakoniwa_core").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "LogEntry"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                kinds.add(node.args[0].value)
    names = {
        "facility": "farm",
        "good": "grain",
        "grade": "mechanized",
        "industry": "steel",
        "terrain": "plain",
        "level": "village",
        "order": "build",
        "reason": "reason_invalid_payload",
        "recipe": "farm_default",
        "infra": "road",
        "climate": "temperate",
        "army": "infantry",
        "politics": "democracy",
        "head_of_state": "republic",
        "economy": "market",
        "before": 1,
        "title": "鉄の国",
        "goods": ["grain"],
        "industries": ["steel"],
        "clauses": ["non_aggression"],
        "terms": [["short", 100]],
        "terms_before": [["short", 100]],
        "term": "short",
        "axis": "none",
        "field": "none",
        "accounting": "accounting_treasury",
        "zone": "zone_industrial",
        "item": "budget_construction",
        "entry": "bank_entry_free",
        "unsold": "bond_unsold_cut",
        "unsold_before": "bond_unsold_cut",
        "method": "resolution_bailout",
        "subsidy": "subsidy_production",
        "mode": "immigration_open",
        "holder": "bond_holder_gov",
        "building": "none",
        "policy": "large_store_none",
        "clause": "clause_non_aggression",
        "why": "none",
    }
    assert len(kinds) > 200
    for kind in sorted(kinds):
        fields = {
            field
            for _, field, _, _ in string.Formatter().parse(defs.name("messages", kind))
            if field
        }
        args = {field: names.get(field, 1) for field in fields}
        row = display.log_entries([SimpleNamespace(turn=1, kind=kind, args=args)])[0]
        assert "{" not in row["text"], (kind, row["text"])
        assert not re.search(
            r"grade_|mechanized|farm_default|resolution_|bank_entry_|immigration_|large_store_",
            row["text"],
        ), (kind, row["text"])


def test_industrial_zone_hint_distinguishes_missing_from_unsuitable(world):
    from hakoniwa_core.state import ZONE_INDUSTRIAL

    (_, _, _), (island, _, _) = _players(world)
    island.economy = {
        **island.economy,
        "finance": {"entry_block": {"facility": "steel_mill", "reason": "no_zone"}},
    }
    island.save(update_fields=["economy"])
    state = {"starting_count": 0, "stopped_count": 0}
    island.tiles.update(zone="")
    steps = overview.next_steps(island, {}, state, [], [], [])
    text = next(s["text"] for s in steps if s["key"] == "industrial_zone")
    assert "民営の工場は工業団地のマスにしか" in text
    island.tiles.filter(id=island.tiles.first().id).update(zone=ZONE_INDUSTRIAL)
    steps = overview.next_steps(island, {}, state, [], [], [])
    text = next(s["text"] for s in steps if s["key"] == "industrial_zone")
    assert "工業団地に、民営の" in text and "地形・資源・通勤圏" in text
    assert get_game().defs.name("facilities", "steel_mill") in text
    assert "民営の工場は工業団地のマスにしか" not in text


def test_every_facility_status_has_owner_hint_and_summary_matches(world):
    from hakoniwa_core.state import Facility

    defs = get_game().defs
    for key in defs.locales["ja"]["messages"]:
        if key.startswith("status_") and not key.startswith("status_construction_"):
            cause = key.removeprefix("status_")
            facility = Facility(id=1, kind="farm", x=1, y=1, built=True, status=cause)
            assert display.status_hint(facility), cause
    (owner, _, _), (island, _, _) = _players(world)
    row = island.facilities.filter(kind="farm").first()
    row.built = True
    row.status = "target"
    row.operating_rate = 0
    row.save()
    mine = overview.facility_state(island, True)
    stopped = next(f for f in mine["stopped"] if f["number"] == row.number)
    assert "国の倉庫" in stopped["hint"] and "二国間契約の申し込み" in stopped["hint"]
    assert "補助金の変更" in stopped["hint"]
    assert all("hint" not in f for f in overview.facility_state(island, False)["stopped"])
    client = Client()
    client.force_login(owner)
    page = client.get(f"/islands/{island.id}/facilities/").content.decode()
    assert stopped["hint"] in page


def test_template_money_prefix_and_raw_chart_symbol(world, monkeypatch):
    """テンプレートの金額連結にも空白を入れ、グラフへ渡す通貨記号は元のままにする。"""
    from server.web import trade as trade_views

    (owner, _, _), (island, _, _) = _players(world)
    island.names = {**island.names, "currency_symbol": "RYO"}
    island.save(update_fields=["names"])
    data = trade_views.fx_data(island, True, "RYO", history=False)
    data["swaps"].update(
        borrowed=1234, room=5678, quota=30, issued=42, issuer_room=987, issuer_share=15
    )
    monkeypatch.setattr(trade_views, "fx_data", lambda *args, **kwargs: data)
    client = Client()
    client.force_login(owner)
    page = client.get(f"/islands/{island.id}/fx/").content.decode()
    assert "RYO 1,234" in page and "RYO 5,678" in page and "RYO 987" in page
    assert 'data-currency="RYO"' in page
    assert 'class="num numeric-with-note"' in page
    assert 'class="numeric-value">RYO 5,678' in page
    island.names["currency_symbol"] = "$"
    island.save(update_fields=["names"])
    page = client.get(f"/islands/{island.id}/fx/").content.decode()
    assert "$1,234" in page and "$ 1,234" not in page


def test_log_recipe_names_use_all_defined_recipes():
    """電子機器の方式から標準へ戻すログも、locale未定義の方式名を出さない。"""
    from types import SimpleNamespace

    defs = get_game().defs
    for facility, spec in defs.facilities.items():
        if len(spec.get("recipes", {})) <= 1:
            continue
        for recipe in spec["recipes"]:
            internal = f"{facility}_{recipe}"
            for kind in ("recipe_changed", "recipe_changed_private"):
                args = {"facility": facility, "recipe": internal, "x": 1, "y": 2}
                [entry] = display.log_entries([SimpleNamespace(turn=1, kind=kind, args=args)])
                assert defs.recipe_name(facility, recipe) in entry["text"]
                assert internal not in entry["text"]
                assert args["recipe"] == internal
