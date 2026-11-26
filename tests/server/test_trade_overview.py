"""貿易の概要とタブ（W-247 の (5)(6)(7)）。

(5) 貿易の画面の初めのページは概要（入っている貿易圏・輸出入と収支・運ぶ途中の荷・契約の数）で、
詳しい一覧は契約・貿易圏・船と港・海外の商人のタブに分ける。(6) いますべきことは、ホームの今月
すべきことの貿易の行と同じ。(7) よく使う契約の申し込みは概要の打てる手の箱に置き、産業政策と援助は
契約のタブの奥に畳む。画面・JSON API・MCP は同じ中身を出す（UI-30）。
"""

from __future__ import annotations

import json
import re

import pytest
from django.core.cache import cache
from django.test import Client

from hakoniwa_core.trade import KEY as TRADE_KEY
from server.accounts import keys
from server.orders.models import Order
from server.web import display, todo
from tests.server.test_ai_players import _keyed, _players, _tool
from tests.server.test_home_todo import _signals, _summary
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db

TABS = ["概要", "契約", "貿易圏", "船と港", "海外の商人", "為替"]


@pytest.fixture(autouse=True)
def _clear_calls():
    cache.clear()
    yield
    cache.clear()


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _tabs(html: str) -> list[str]:
    nav = re.search(r'<nav class="island-subnav".*?</nav>', html, re.S).group(0)
    return re.findall(r"<a [^>]*>([^<]+)</a>", nav)


def _section(html: str, ident: str) -> str:
    return re.search(rf'<section [^>]*id="{ident}".*?</section>', html, re.S).group(0)


def _shipment(sid: int, source: int, target: int, load: int, turn: int) -> dict:
    return {
        "id": sid,
        "kind": "bloc",
        "ref": 1,
        "from": source,
        "to": target,
        "facility": None,
        "good": "coal",
        "batches": [[0, load]],
        "load": load,
        "loaded": turn,
        "arrive": turn + 2,
    }


def _world_with_offer(world):
    """持ち主の島に、ほかの島から契約の申し込みが来ていて、輸入の荷が 1 つ海の上にある世界。"""
    (owner, other_owner, third_owner), (island, other, third) = _players(world)
    _signals(world, island, other)
    world.refresh_from_db()
    ext = world.ext
    ext[TRADE_KEY]["shipments"] = [
        _shipment(1, other.id, island.id, 3000, world.turn),
        _shipment(2, island.id, other.id, 1000, world.turn),
    ]
    world.save(update_fields=["ext"])
    _stats(
        island,
        world.turn - 1,
        {"trade.exports": 1200, "trade.imports": 3400, "trade.balance": -2200},
    )
    return owner, island, other, other_owner


def test_overview_first_and_details_in_tabs(world):
    """(5) 概要には貿易圏・輸出入と収支・運ぶ途中の荷・契約の数だけを出し、詳しい一覧はタブに。"""
    owner, island, other, _ = _world_with_offer(world)
    browser = _client(owner)
    data = browser.get(f"/api/islands/{island.id}/trade").json()
    o = data["overview"]
    assert o["blocs"] == []
    assert [r["key"] for r in o["rows"]] == ["trade.exports", "trade.imports", "trade.balance"]
    assert o["contracts"] == {"active": 0, "offered": 1, "waiting": 0, "short": 0}
    assert o["shipments"] == {
        "count": 2,
        "export_tons": display.tons(1000),
        "import_tons": display.tons(3000),
    }
    page = browser.get(f"/islands/{island.id}/trade/").content.decode()
    overview = _section(page, "trade-overview")
    assert "どの貿易圏にも入っていません" in overview and "貿易収支" in overview
    assert "相手からの申し込み 1 件" in overview and "（2 件）" in overview
    assert f'href="/islands/{island.id}/trade/contracts/#contracts"' in overview
    # 詳しい一覧は概要に出さない
    for table in ("contracts-table", "traders-table", "merchant-table", "other-blocs-table"):
        assert table not in page, table
    contracts = browser.get(f"/islands/{island.id}/trade/contracts/").content.decode()
    assert "contracts-table" in contracts and other.name in contracts
    assert 'id="other-blocs"' in browser.get(f"/islands/{island.id}/trade/blocs/").content.decode()
    fleet = browser.get(f"/islands/{island.id}/trade/fleet/").content.decode()
    assert 'id="traders"' in fleet and 'id="fleet"' in fleet
    assert 'id="merchant"' in browser.get(f"/islands/{island.id}/trade/merchant/").content.decode()
    # 前の URL の欄（#contracts など）は、移ったタブへ送る
    moved = re.search(r"data-moved='([^']+)'", page).group(1)
    assert json.loads(moved)["#contracts"] == f"/islands/{island.id}/trade/contracts/"
    assert json.loads(moved)["#merchant"] == f"/islands/{island.id}/trade/merchant/"


def test_overview_stats_are_rows_of_the_overview_table(world):
    """貿易の数（統計の行）は概要の表の行として出し、別の表やアコーディオンに分けない。
    統計が無いときは「未集計です。」の注記だけを残す。"""
    owner, island, _, _ = _world_with_offer(world)
    page = _client(owner).get(f"/islands/{island.id}/trade/").content.decode()
    overview = _section(page, "trade-overview")
    assert overview.count("<table") == 1 and "<details" not in overview
    assert "貿易の数をすべて見る" not in page and "未集計です。" not in overview
    table = re.search(r'<table class="info trade-overview">.*?</table>', overview, re.S).group(0)
    # 圏の行・概要の行（輸出・輸入・収支）・荷の行・契約の行のあとに、統計の行が続く
    assert table.count("<tr>") >= 9


def test_what_to_do_now_is_the_home_todo_trade_rows(world):
    """(6) いますべきことは、ホームの今月すべきことのうち貿易の種類の行と同じ。返事の命令を入れると
    消える。持ち主だけ。"""
    owner, island, other, other_owner = _world_with_offer(world)
    _, token = keys.issue(owner)
    home = [r for r in _summary(token, island)["todo"] if r["kind"] in todo.TRADE_KINDS]
    assert [r["kind"] for r in home] == ["contract"]
    data = _keyed(token).get(f"/api/islands/{island.id}/trade").json()
    assert data["todo"] == home
    page = _client(owner).get(f"/islands/{island.id}/trade/").content.decode()
    box = _section(page, "todo")
    assert "いますべきこと" in box and other.name in box
    assert f'href="/islands/{island.id}/trade/contracts/#contracts"' in box
    assert page.index('id="todo"') < page.index('id="trade-overview"')  # 上に目立たせる
    # MCP の report も同じ行
    text, error = _tool(_keyed(token), "report", island=island.id, page="trade")
    assert not error and json.loads(text)["todo"] == home
    # 受ける命令を入れたら消える
    Order.objects.create(island=island, seq=1, kind="accept_contract", payload={"contract": 3})
    assert _keyed(token).get(f"/api/islands/{island.id}/trade").json()["todo"] == []
    page = _client(owner).get(f"/islands/{island.id}/trade/").content.decode()
    assert 'id="todo"' not in page and "いま貿易で急いですることはありません" in page
    # ほかの国の人には出さない
    others = _client(other_owner).get(f"/api/islands/{island.id}/trade").json()
    assert "todo" not in others and "contracts" not in others["overview"]
    page = _client(other_owner).get(f"/islands/{island.id}/trade/").content.decode()
    assert 'id="todo"' not in page and 'id="actions"' not in page


def test_bloc_rows_link_to_the_bloc_tab(world):
    """どの貿易圏にも入っていない・代表の圏に加盟の申し込みがある、の行は貿易圏のタブへ。"""
    (owner, _, third_owner), (island, other, third) = _players(world)
    third.ext = {
        **(third.ext or {}),
        TRADE_KEY: {**(third.ext or {}).get(TRADE_KEY, {}), "since": 1},
    }
    third.save(update_fields=["ext"])
    bloc = {
        "id": 1,
        "name": "南の海",
        "rep": island.id,
        "members": [island.id],
        "open": False,
        "currency": island.id,
        "applicants": [other.id],
    }
    world.ext = {
        **(world.ext or {}),
        TRADE_KEY: {**(world.ext or {}).get(TRADE_KEY, {}), "blocs": [bloc]},
    }
    world.save(update_fields=["ext"])
    data = _client(owner).get(f"/api/islands/{island.id}/trade").json()
    assert data["overview"]["blocs"] == [{"id": 1, "name": "南の海", "is_rep": True}]
    [row] = data["todo"]
    assert row["kind"] == "bloc_applicants"
    assert row["url"] == f"/islands/{island.id}/trade/blocs/#blocs"
    [row] = _client(third_owner).get(f"/api/islands/{third.id}/trade").json()["todo"]
    assert row["kind"] == "bloc" and row["url"] == f"/islands/{third.id}/trade/blocs/#other-blocs"
    page = _client(owner).get(f"/islands/{island.id}/trade/").content.decode()
    assert "南の海</a> （代表）" in _section(page, "trade-overview")


def test_contract_offer_in_the_action_box_and_policy_folded_away(world):
    """(7) 契約の申し込みは概要の打てる手の箱に、関税・補助・割り当て・禁輸・援助は契約のタブの
    畳んだ欄に。箱には出ている貿易の命令も出す。"""
    owner, island, other, _ = _world_with_offer(world)
    payload = {"partner": other.id, "side": "sell", "good": "coal", "quantity": 2, "price": 500}
    Order.objects.create(island=island, seq=1, kind="propose_contract", payload=payload)
    browser = _client(owner)
    data = browser.get(f"/api/islands/{island.id}/trade").json()
    assert data["orders"]["pending"] == [display.describe_order("propose_contract", payload)]
    page = browser.get(f"/islands/{island.id}/trade/").content.decode()
    box = _section(page, "actions")
    assert 'class="panel action-box"' in box and 'data-order="propose_contract"' in box
    assert data["orders"]["pending"][0] in box and 'id="orders"' in box
    for kind in ("set_tariff", "set_export_subsidy", "set_import_quota", "set_embargo", "send_aid"):
        assert f'data-order="{kind}"' not in page, kind
    contracts = browser.get(f"/islands/{island.id}/trade/contracts/").content.decode()
    folded = re.search(
        r'<details class="panel trade-back" id="trade-policy">.*?</details>', contracts, re.S
    )
    assert folded and " open" not in folded.group(0).split(">")[0]
    for kind in ("set_tariff", "set_export_subsidy", "set_import_quota", "set_embargo", "send_aid"):
        assert f'data-order="{kind}"' in folded.group(0), kind
    assert 'data-order="propose_contract"' not in contracts
    assert f'href="/islands/{island.id}/trade/#actions"' in contracts


def test_tabs_for_the_owner_and_for_others(world):
    """貿易の入口のタブ。中身が持ち主だけのタブ（契約・船と港・海外の商人）はほかの国の人に出さず、
    URL で開いたときは持ち主だけと書く。"""
    (owner, other_owner, _), (island, _, _) = _players(world)
    page = _client(owner).get(f"/islands/{island.id}/trade/").content.decode()
    assert _tabs(page) == TABS
    visitor = _client(other_owner)
    page = visitor.get(f"/islands/{island.id}/trade/").content.decode()
    assert _tabs(page) == ["概要", "貿易圏", "為替"]
    for tab in ("contracts", "fleet", "merchant"):
        res = visitor.get(f"/islands/{island.id}/trade/{tab}/")
        html = res.content.decode()
        assert res.status_code == 200 and "このタブの中身は島の持ち主だけが見られます" in html
        assert "data-order=" not in html and 'id="orders"' not in html
        assert 'aria-current="page"' in re.search(
            r'<nav class="island-subnav".*?</nav>', html, re.S
        ).group(0)


def test_trade_by_good_pies_on_the_overview(world):
    """品目ごとの輸出と輸入の額（export.<財>・import.<財>。W-72）を、貿易の概要の推移に円グラフで
    出す。円グラフの切れの名前は品目の名前（頭の「輸出・」は見出しで足りるので画面が外す）。"""
    owner, island, _, _ = _world_with_offer(world)
    _stats(island, 5, {"export.grain": 300.0, "export.freight": 20.0, "import.food": 120.0})
    browser = _client(owner)
    page = browser.get(f"/islands/{island.id}/trade/").content.decode()
    assert 'data-pie="export.*"' in page and 'data-pie="import.*"' in page
    data = browser.get(
        f"/api/islands/{island.id}/economy", {"names": "export.*,import.*", "from": 1, "to": 5}
    ).json()
    assert data["labels"]["export.grain"] == "輸出・穀物"
    assert data["labels"]["import.food"] == "輸入・食品"
    assert data["labels"]["export.freight"] == "輸出・他国に貸した船の運賃"
    assert data["series"]["export.grain"][-1] == 300


def test_trade_partner_stats_table(world):
    """相手国ごとの輸出・輸入（#5455）: 統計 partner.* から、集計ターンと直近 1 年（12 ターン）の
    表を貿易の概要の下に出し、API（MCP と同じ trade_data）にも同じ行を出す。圏の市場の分も
    実際の相手の国ごとに出し（#5605）、行には経路ごとの直近 1 年の内訳を添える。主な品目は
    直近 1 年の額の大きい順。"""
    owner, island, other, _ = _world_with_offer(world)
    turn = world.turn - 1
    _stats(
        island,
        turn,
        {
            f"partner.{other.id}.exports": 500.0,
            f"partner.{other.id}.exports.grain": 500.0,
            f"partner.{other.id}.imports": 100.0,
            f"partner.{other.id}.imports.coal": 100.0,
            f"partner_route.{other.id}.exports.bloc": 500.0,
            f"partner_route.{other.id}.imports.contract": 100.0,
            "partner.merchant.imports": 50.0,
            "partner.merchant.imports.food": 50.0,
            "partner_route.merchant.imports.merchant": 50.0,
        },
    )
    _stats(
        island,
        turn - 3,
        {
            f"partner.{other.id}.exports": 300.0,
            f"partner.{other.id}.exports.grain": 300.0,
            f"partner_route.{other.id}.exports.bloc": 300.0,
        },
    )
    _stats(island, turn - 20, {f"partner.{other.id}.exports": 9000.0})  # 1 年より前は数えない
    browser = _client(owner)
    data = browser.get(f"/api/islands/{island.id}/trade").json()
    rows = {row["partner"]: row for row in data["partner_stats"]}
    assert rows[str(other.id)]["name"] == other.name
    # 新しい記録では「圏の市場」の行は出ず、相手の国の行に経路の内訳として出る
    assert "bloc" not in rows
    assert rows["merchant"]["name"] == "海外の商人"
    # 集計ターンの列はそのターンだけ、直近 1 年の列は 12 ターンの合計（20 ターン前は入らない）
    assert "500" in rows[str(other.id)]["exports_turn"]
    assert "800" in rows[str(other.id)]["exports_year"]
    assert [g["good"] for g in rows[str(other.id)]["goods"]] == ["grain", "coal"]
    # 経路の内訳は直近 1 年の合計で、額の大きい順
    routes = {r["route"]: r for r in rows[str(other.id)]["routes"]}
    assert routes["bloc"]["label"] == "圏の市場" and "800" in routes["bloc"]["exports"]
    assert routes["contract"]["label"] == "二国間契約" and "100" in routes["contract"]["imports"]
    # 表の注用に、輸出・輸入の片側ずつの内訳も出す（その側の額が無い経路は除く）
    route_exports = rows[str(other.id)]["route_exports"]
    assert [r["label"] for r in route_exports] == ["圏の市場"]
    assert "800" in route_exports[0]["amount"]
    assert [r["label"] for r in rows[str(other.id)]["route_imports"]] == ["二国間契約"]
    assert "100" in rows[str(other.id)]["route_imports"][0]["amount"]
    assert [r["label"] for r in rows["merchant"]["route_imports"]] == ["海外の商人"]
    page = browser.get(f"/islands/{island.id}/trade/").content.decode()
    section = _section(page, "trade-partners")
    assert "貿易の相手国" in section and "圏の市場（相手は混ざる）" not in section
    assert "経路" in section and "圏の市場" in section and "二国間契約" in section
    assert other.name in section and "穀物" in section
    # 経路の注は相手の名前の欄ではなく、直近 1 年の金額の欄の 2 行目に小さく出す
    assert f"<th>{other.name}</th>" in section
    assert '<small class="muted route-note">' in section
    # 金額の桁が長くスマホ幅に収まらないので、表は横スクロールの入れ物で包む
    assert '<div class="table-scroll">' in section
