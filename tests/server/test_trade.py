"""貿易と為替の画面と API（フェーズ3-1）: 世界の貿易圏・港・航路（荷の中身は自国の荷だけ）、島の貿易
（契約・商社・船団・海外の商人は持ち主だけ）、為替と国際収支（外貨の持ち高と非居住者の預金の内訳は
持ち主だけ）、貿易の命令の説明、画面・島の画面のリンク・世界マップ・手引き。"""

from __future__ import annotations

import pytest

from hakoniwa_core import ledger
from server.economy.models import Account
from server.game import get_game
from server.orders.models import Order
from server.turns.services import run_turn
from server.web import display, reports
from server.world import models as m
from tests.server.test_phase1 import _login, _own_island

pytestmark = pytest.mark.django_db


def _trading_world(client) -> tuple[m.Island, m.Island, m.Island]:
    """2つ目の島が貿易圏「北の圏」を作り、持ち主の島（1つ目）が命令の API で加盟し、2つ目の島が
    持ち主の島に石炭を売る契約を申し込んだ世界（3ターン回す）。(自国, 圏の代表, ほかの島)。"""
    own = _own_island(client)
    second, third = m.Island.objects.exclude(pk=own.pk).order_by("id")
    run_turn(force=True)  # 移し替えの段が港・海運業・商社を置く
    Order.objects.create(island=second, seq=1, kind="create_bloc", payload={"name": "北の圏"})
    run_turn(force=True)
    body = {"orders": [{"kind": "join_bloc", "payload": {"bloc": 1}}]}
    res = client.put(f"/api/islands/{own.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    contract = {
        "partner": own.id,
        "side": "sell",
        "good": "coal",
        "quantity": 2,
        "price": 500,
        "currency": second.id,
        "turns": 12,
    }
    Order.objects.create(island=second, seq=1, kind="propose_contract", payload=contract)
    run_turn(force=True)
    for island in (own, second, third):
        island.refresh_from_db()
    return own, second, third


def _shipment(sid: int, source: int, target: int, good: str, qty: int, turn: int) -> dict:
    return {
        "id": sid,
        "kind": "bloc",
        "ref": 1,
        "from": source,
        "to": target,
        "facility": None,
        "good": good,
        "batches": [[0, qty]],
        "load": qty,  # 重さ 1 トンの財（1000 分の 1 トン）
        "loaded": turn,
        "arrive": turn + 2,
    }


# --- 世界 ---------------------------------------------------------------------------------


def test_world_trade_shows_blocs_ports_and_routes(client, world):
    own, second, third = _trading_world(client)
    data = client.get("/api/world/trade").json()
    assert [b["name"] for b in data["blocs"]] == ["北の圏"]
    bloc = data["blocs"][0]
    assert bloc["rep"] == second.id and bloc["currency"] == second.id
    assert bloc["currency_name"] == second.names["currency"]
    assert [x["id"] for x in bloc["members"]] == sorted([own.id, second.id])
    assert {"good", "name", "unit", "price", "volume", "demand", "supply"} <= set(bloc["goods"][0])
    islands = {i["id"]: i for i in data["islands"]}
    assert islands[own.id]["blocs"] == [1] and islands[third.id]["blocs"] == []
    with_port = set(
        m.Facility.objects.filter(kind="port", built=True).values_list("island_id", flat=True)
    )
    assert with_port and {i for i, x in islands.items() if x["port"]} == with_port
    assert data["routes"] == []

    # 運ぶ途中の荷: 自国が受け取る荷だけ中身を見せ、ほかの島どうしの荷は船の数と重さだけ
    w = m.World.objects.get()
    w.ext["trade"]["shipments"] = [
        _shipment(101, second.id, own.id, "coal", 5000, w.turn),
        _shipment(102, second.id, third.id, "steel", 20000, w.turn),
        _shipment(103, second.id, third.id, "steel", 20000, w.turn),
    ]
    w.save()
    routes = {(r["from"], r["to"]): r for r in client.get("/api/world/trade").json()["routes"]}
    mine = routes[(second.id, own.id)]
    assert mine["mine"] is True
    assert mine["cargo"] == [
        {"good": "coal", "name": "石炭", "unit": "トン", "amount": 5.0, "arrive": w.turn + 2}
    ]
    others = routes[(second.id, third.id)]
    assert "cargo" not in others and "mine" not in others
    capacity = get_game().config.get("trade.ship_capacity")
    assert others["shipments"] == 2 and others["load"] == 40.0
    assert others["ships"] == round(40 / capacity, 2)

    client.logout()  # ログインしていない人には、どの荷の中身も見せない
    routes = client.get("/api/world/trade").json()["routes"]
    assert len(routes) == 2 and not any("cargo" in r for r in routes)


def test_world_page_renders(client, world):
    _trading_world(client)
    html = client.get("/world/").content.decode()
    assert 'data-trade="/api/world/trade"' in html and 'id="world-trade"' in html


# --- 島の貿易 -----------------------------------------------------------------------------

OWNER_KEYS = {
    "other_blocs",
    "contracts",
    "traders",
    "fleet",
    "shipments",
    "merchant",
    "partners",
    "goods",
    "cancel_turns",
}


def test_trade_api_for_others_shows_only_country_numbers(client, world):
    own, second, _ = _trading_world(client)
    client.logout()
    data = client.get(f"/api/islands/{own.id}/trade").json()
    assert data["mine"] is False and data["started"] is True
    assert not OWNER_KEYS & set(data)
    assert set(data["stats"]) <= {"trade.exports", "trade.imports", "trade.balance", "ships"}
    assert "ships" in data["stats"]
    assert [b["name"] for b in data["blocs"]] == ["北の圏"]
    assert "goods" not in data["blocs"][0] and "rate" not in data["blocs"][0]


def test_trade_api_for_owner(client, world):
    own, second, third = _trading_world(client)
    config = get_game().config
    data = client.get(f"/api/islands/{own.id}/trade").json()
    assert data["mine"] is True and OWNER_KEYS <= set(data)
    assert "trade.unpaid" in data["stats"]

    bloc = data["blocs"][0]
    assert bloc["is_rep"] is False and bloc["rep_inactive"] is False
    assert bloc["rate"] > 0 and bloc["applicants"] == [] and bloc["leaving"] is False
    coal = next(g for g in bloc["goods"] if g["good"] == "coal")
    assert {"price", "price_own", "home_price", "gap"} <= set(coal)
    assert data["other_blocs"] == []

    # 2つ目の島からの申し込み: こちらは買う側で、受けられる
    [contract] = data["contracts"]
    assert contract["state"] == "proposed" and contract["state_name"] == "申し込み中"
    assert contract["side"] == "buy" and contract["partner"] == second.id
    assert contract["proposed_by_me"] is False and contract["can_accept"] is True
    assert contract["quantity"] == 2.0 and contract["price"] == 500
    assert contract["currency_name"] == second.names["currency"]

    assert data["traders"] and {"number", "name", "goods"} <= set(data["traders"][0])
    fleet = data["fleet"]
    assert fleet["shipping"]["ships"] == config.get("trade.start_ships")
    assert fleet["shipping"]["fuel"][0]["good"] == "coal"
    assert fleet["port"]["capacity"] == config.get("trade.port_capacity")
    assert fleet["port"]["fee"] > 0

    merchant = data["merchant"]
    turn = m.World.objects.get().turn
    since = merchant["since"]
    assert merchant["active"] is True
    assert merchant["turns_left"] == since + config.get("foreign_merchant.turns") - (turn + 1)
    assert {g["good"] for g in merchant["goods"]} == set(config.get("foreign_merchant.goods"))
    assert {p["id"] for p in data["partners"]} == {second.id, third.id}
    assert "coal" in {g["good"] for g in data["goods"]}
    assert "electricity" not in {g["good"] for g in data["goods"]}  # 運べない財

    # 申し込んだ側からは、売る側で、受けられない（取り下げられる）
    second.owner = _login(client, "other")
    second.save()
    [contract] = client.get(f"/api/islands/{second.id}/trade").json()["contracts"]
    assert contract["side"] == "sell" and contract["proposed_by_me"] is True
    assert contract["can_accept"] is False and contract["can_cancel"] is True
    # 代表の島の持ち主には、自分の圏の設定（代表）の欄が出る
    html = client.get(f"/islands/{second.id}/trade/").content.decode()
    assert 'data-order="bloc_settings"' in html and 'data-order="transfer_bloc"' in html


def test_representative_on_break_can_be_taken_over(client, world):
    own, second, _ = _trading_world(client)
    m.Island.objects.filter(pk=second.pk).update(inactive=True)
    data = client.get(f"/api/islands/{own.id}/trade").json()
    assert data["blocs"][0]["rep_inactive"] is True
    assert 'data-order="take_over_bloc"' in client.get(f"/islands/{own.id}/trade/").content.decode()


# --- 為替 ---------------------------------------------------------------------------------


def test_fx_api_for_owner_and_others(client, world):
    own, second, third = _trading_world(client)
    # 外貨の持ち高（中央銀行と商社）と、2つ目の島の政府が持つ自国通貨（非居住者の預金）を置く
    trader = own.facilities.filter(kind="trading").first()
    ext = own.ext
    ext["trade"]["fx"] = {
        "cb": {str(second.id): [3000, 2900]},
        f"fac:{trader.number}": {str(second.id): [1000, 1000]},
    }
    m.Island.objects.filter(pk=own.pk).update(ext=ext)
    name = ledger.nonresident_account(ledger.nonresident(second.id, ledger.GOV))
    Account.objects.create(island=own, name=name, balance=1234)

    data = client.get(f"/api/islands/{own.id}/fx").json()
    [rate] = data["rates"]
    assert rate["currency"] == second.id and rate["rate"] > 0 and rate["inverse"] > 0
    # 2つ目の島が申し込んだ契約も、2つ目の島の通貨で払う
    assert rate["uses"] == ["貿易圏「北の圏」の決済通貨", "契約（1 番）の通貨"]
    assert [c["currency"] for c in data["cross"]] == [third.id]
    assert data["band"] == get_game().config.get("fx.band")
    assert {"trade.balance", "fx.holdings", "money.nonresident"} <= set(data["stats"])
    history = data["history"]
    key = f"fx.{second.id}"
    assert key in history["series"] and history["units"][key] == "fx"
    assert second.names["currency"] in history["labels"][key]
    assert "trade.balance" in history["series"]

    holdings = {h["holder"]: h for h in data["holdings"]}
    assert set(holdings) == {"cb", "facilities"}
    [cb] = holdings["cb"]["currencies"]
    assert cb["amount"] == 3000 and cb["book_value"] == 2900
    assert cb["value"] == 3000 * round(rate["rate"] * 10000) // 10000
    assert holdings["facilities"]["name"] == "商社など（合計）"
    assert data["nonresident"] == [
        {
            "island": second.id,
            "name": second.name,
            "gov": 1234,
            "cb": 0,
            "facilities": 0,
            "total": 1234,
        }
    ]

    client.logout()
    data = client.get(f"/api/islands/{own.id}/fx?from=0").json()
    assert data["mine"] is False and data["rates"]
    assert "holdings" not in data and "nonresident" not in data
    assert data["history"]["from"] == 0


# --- 画面 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["trade", "fx"])
def test_trade_pages_for_owner_and_others(client, world, page):
    own, _, _ = _trading_world(client)
    url = f"/islands/{own.id}/{page}/"
    res = client.get(url)
    assert res.status_code == 200 and res.context["mine"] is True
    html = res.content.decode()
    assert f'href="{url}" aria-current="page"' in html
    if page == "trade":
        assert 'id="orders"' in html and 'data-heading="貿易の命令"' in html
        for kind in ("leave_bloc", "accept_contract", "cancel_contract", "create_bloc"):
            assert f'data-order="{kind}"' in html
        assert "propose_contract" in html and 'id="merchant"' in html
    else:
        assert 'id="holdings"' in html and f'data-names="fx.{own.id + 1}' in html
    client.logout()
    res = client.get(url)
    assert res.status_code == 200 and res.context["mine"] is False
    html = res.content.decode()
    assert "他国の島のため" in html and 'id="orders"' not in html
    assert "data-order=" not in html and 'id="holdings"' not in html


def test_island_pages_link_to_trade_and_fx(client, world):
    island = m.Island.objects.order_by("id").first()
    for url in (f"/islands/{island.id}/", f"/islands/{island.id}/finance/"):
        html = client.get(url).content.decode()
        assert f'href="/islands/{island.id}/trade/"' in html
        assert f'href="/islands/{island.id}/fx/"' in html
    # 貿易を始める前の島でも開ける
    assert client.get(f"/islands/{island.id}/trade/").status_code == 200
    assert client.get(f"/islands/{island.id}/fx/").status_code == 200
    assert client.get(f"/api/islands/{island.id}/trade").json()["started"] is False


def test_help_explains_trade_and_fx(client, world):
    config = get_game().config
    html = client.get("/help/").content.decode()
    assert "貿易と為替（フェーズ3-1）" in html
    assert f"{config.get('foreign_merchant.turns')} ターンのあいだ" in html
    assert f"最後の {config.get('foreign_merchant.last_turns')} ターン" in html
    assert f"1ターンに {config.get('fx.band') * 100:g}% までしか動きません" in html
    assert "二国間契約の申し込み" in html  # 命令の表は定義データから


# --- 命令の説明と統計の表示名 ---------------------------------------------------------------

ISLAND_KEYS = ("currency", "approve", "to", "partner")  # 命令の中身のうち、島の番号を持つ項目


@pytest.mark.parametrize(
    ("kind", "payload", "text"),
    [
        ("create_bloc", {"name": "北の圏"}, "貿易圏を作る: 「北の圏」（誰でも入れる）"),
        ("create_bloc", {"name": "南", "open": False}, "貿易圏を作る: 「南」（加盟は承認制）"),
        ("join_bloc", {"bloc": 2}, "貿易圏に入る: 貿易圏 2 番"),
        ("leave_bloc", {"bloc": 2}, "貿易圏を抜ける: 貿易圏 2 番"),
        ("take_over_bloc", {"bloc": 2}, "貿易圏の代表を引き継ぐ: 貿易圏 2 番"),
        (
            "bloc_settings",
            {"bloc": 1, "open": False, "currency": 1, "approve": 2},
            "貿易圏の設定: 貿易圏 1 番 加盟は承認制・決済通貨をサンプル島1通貨に・"
            "サンプル島2の加盟を承認",
        ),
        ("transfer_bloc", {"bloc": 1, "to": 3}, "貿易圏の代表を譲る: 貿易圏 1 番 → サンプル島3"),
        (
            "propose_contract",
            {
                "partner": 2,
                "side": "buy",
                "good": "coal",
                "quantity": 2.5,
                "price": 900,
                "turns": 6,
            },
            "二国間契約の申し込み: サンプル島2に石炭を毎ターン 2.5 トン買う"
            "（1トン 900・自国通貨・6 ターン）",
        ),
        ("accept_contract", {"contract": 4}, "二国間契約を受ける: 契約 4 番"),
        ("cancel_contract", {"contract": 4}, "二国間契約をやめる: 契約 4 番"),
    ],
)
def test_describe_trade_orders(world, kind, payload, text):
    # 島の番号は「サンプル島n」の n で書き、本当の番号に置き換える
    # （PostgreSQL では、テストごとに番号が 1 から始まるとは限らない）
    payload = {
        key: m.Island.objects.get(name=f"サンプル島{value}").id if key in ISLAND_KEYS else value
        for key, value in payload.items()
    }
    assert display.describe_order(kind, payload) == text


def test_describe_bloc_orders_by_name(world):
    """貿易圏が世界の貿易の状態にあれば、命令の説明は番号でなく名前で出す。"""
    world.ext = {**(world.ext or {}), "trade": {"blocs": [{"id": 2, "name": "北の圏"}]}}
    world.save()
    assert display.describe_order("join_bloc", {"bloc": 2}) == "貿易圏に入る: 「北の圏」"
    assert display.describe_order("leave_bloc", {"bloc": 3}) == "貿易圏を抜ける: 貿易圏 3 番"


def test_trade_orders_are_accepted(client, world):
    """貿易の命令はどれも並べられる（決められるかは貿易の段で決まり、ログに出る）。"""
    island = _own_island(client)
    other = m.Island.objects.exclude(pk=island.pk).order_by("id").first()
    orders = [
        ("create_bloc", {"name": "北の圏", "open": True}),
        ("join_bloc", {"bloc": 1}),
        ("bloc_settings", {"bloc": 1, "currency": island.id}),
        ("transfer_bloc", {"bloc": 1, "to": other.id}),
        ("take_over_bloc", {"bloc": 1}),
        ("leave_bloc", {"bloc": 1}),
        (
            "propose_contract",
            {
                "partner": other.id,
                "side": "sell",
                "good": "coal",
                "quantity": 1,
                "price": 1,
                "turns": 3,
            },
        ),
        ("accept_contract", {"contract": 1}),
        ("cancel_contract", {"contract": 1}),
    ]
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    assert res.json()["warnings"] == []
    bad = {"orders": [{"kind": "bloc_settings", "payload": {"bloc": 1}}]}  # 変える項目がない
    res = client.put(f"/api/islands/{island.id}/orders", bad, content_type="application/json")
    assert res.status_code == 400


def test_stat_labels_for_trade_and_fx(world):
    island = m.Island.objects.order_by("id").first()
    currency = island.names["currency"]
    assert reports.stat_label(f"fx.{island.id}") == (
        f"為替レート（{currency} 1 が自国通貨でいくらか）",
        "fx",
    )
    assert reports.stat_label(f"fx.volume.{island.id}")[0] == f"為替の売買量（{currency}の額）"
    assert reports.stat_label(f"fx.dealt.{island.id}")[0].startswith("中央銀行が買った外貨")
    assert reports.stat_label("fx.holdings")[1] == "money"
    assert reports.stat_label("trade.balance") == ("貿易収支（輸出 − 輸入）", "money")
    assert reports.stat_label("fx.unknown") == ("fx.unknown", "")
    assert display.stat_text("fx.2", 1.5, "fx") == "1.5000"
    assert display.stat_text("ships", 2.0, " 隻") == "2 隻"
