"""島の統計の画面（経済・市場・施設・財政・金融、フェーズ2-3）とその API: 統計の系列、市場の推移と
約定、施設の帳簿、金融。持ち主と他国の人で見せる中身が変わること。地図とマスの欄のインフラ。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.economy.models import FacilityBook, MarketRecord, Trade
from server.game import get_game
from server.web import display
from server.world import models as m
from tests.server.test_phase1 import _login, _own_island

pytestmark = pytest.mark.django_db

PAGES = ["economy", "market", "facilities", "fiscal", "finance"]


def _stats(island: m.Island, turn: int, values: dict[str, float]) -> None:
    m.StatRecord.objects.bulk_create(
        [m.StatRecord(island=island, turn=turn, name=n, value=v) for n, v in values.items()]
    )


def _first_facility(island: m.Island, kind: str | None = None) -> m.Facility:
    rows = island.facilities.exclude(kind="housing").order_by("number")
    return rows.filter(kind=kind).first() if kind else rows.first()


def test_stat_records_are_indexed_by_name():
    indexes = [tuple(i.fields) for i in m.StatRecord._meta.indexes]
    assert ("island", "name", "turn") in indexes


# --- 統計の系列 ------------------------------------------------------------------------


def test_economy_series_with_derived_and_wildcard(client, world):
    island = _own_island(client)
    for turn, gdp in [(1, 100.0), (2, 120.0), (3, 130.0)]:
        _stats(island, turn, {"gdp": gdp, "price.food": gdp / 10})
    _stats(
        island,
        3,
        {
            "fiscal.in.tax_income": 100,
            "fiscal.in.bond_issue": 1000,  # 借入は財政の収支に数えない
            "fiscal.out.wage": 30,
            "fiscal.out.bond_redemption": 500,
            "layer.general.income": 1000,
            "layer.general.persons": 10,
        },
    )
    url = f"/api/islands/{island.id}/economy"
    names = "gdp,price.*,fiscal.balance,fiscal.revenue,layer.general.income_per_person,nothing"
    data = client.get(url, {"names": names}).json()
    assert data["mine"] is True
    assert data["turns"] == [1, 2, 3]
    assert data["series"]["gdp"] == [100.0, 120.0, 130.0]
    assert data["series"]["price.food"] == [10.0, 12.0, 13.0]
    assert data["series"]["fiscal.balance"] == [None, None, 70.0]
    assert data["series"]["fiscal.revenue"][-1] == 100.0
    assert data["series"]["layer.general.income_per_person"] == [None, None, 100.0]
    assert "nothing" not in data["series"]
    assert data["units"]["gdp"] == "money" and data["units"]["price.food"] == "money"
    assert data["labels"]["fiscal.balance"] == "財政の収支"
    # 範囲を決める
    data = client.get(url, {"names": "gdp", "from": 2, "to": 2}).json()
    assert data["turns"] == [2] and data["series"]["gdp"] == [120.0]
    # 名前を決めなければ経済の画面のグラフの統計
    data = client.get(url).json()
    assert "gdp" in data["series"]


def test_economy_series_hides_bank_details_from_others(client, world):
    island = m.Island.objects.order_by("id").first()
    _stats(island, 1, {"gdp": 1.0, "bank.1.deposits": 5.0, "bank.1.loans": 2.0})
    url = f"/api/islands/{island.id}/economy"
    data = client.get(url, {"names": "gdp,bank.*,bank.1.loans"}).json()
    assert data["mine"] is False
    assert set(data["series"]) == {"gdp"}
    island.owner = _login(client)
    island.save()
    data = client.get(url, {"names": "gdp,bank.*"}).json()
    assert data["series"]["bank.1.deposits"] == [5.0]
    assert data["labels"]["bank.1.deposits"].startswith("銀行（1 番）")


# --- 市場 -------------------------------------------------------------------------------


def _market(island: m.Island) -> m.Facility:
    buyer = _first_facility(island)
    _stats(island, 4, {"gdp": 1.0})
    for turn, price in [(3, 100), (4, 110)]:
        MarketRecord.objects.create(
            island=island,
            turn=turn,
            good="food",
            price=price,
            volume=2000,
            demand=3000,
            supply=2000,
        )
    Trade.objects.create(
        island=island,
        turn=4,
        good="food",
        buyer="hh:general",
        seller=f"fac:{buyer.number}",
        quantity=2000,
        amount=220,
    )
    return buyer


def test_market_api_shows_trades_only_to_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    seller = _market(island)
    url = f"/api/islands/{island.id}/market/food"
    data = client.get(url).json()
    assert data["turns"] == [3, 4] and data["price"] == [100, 110]
    assert data["volume"] == [2.0, 2.0] and data["latest"]["demand"] == 3.0
    assert "trades" not in data and "short" not in data
    island.owner = _login(client)
    island.save()
    data = client.get(url).json()
    [trade] = data["trades"]
    assert trade["buyer_name"] == "家計（労働者階級）"
    assert trade["seller"] == f"fac:{seller.number}" and trade["seller_name"]
    assert trade["quantity"] == 2.0 and trade["price"] == 110
    assert client.get(f"/api/islands/{island.id}/market/unknown_good").status_code == 404


def test_market_page_table(client, world):
    island = _own_island(client)
    _market(island)
    page = client.get(f"/islands/{island.id}/market/")
    assert page.status_code == 200
    rows = {r["good"]: r for r in page.context["rows"]}
    assert rows["food"]["change"] == "+10.0%"


# --- 施設の帳簿 -----------------------------------------------------------------------------


def test_facility_book_is_only_for_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    f = _first_facility(island)
    _stats(island, 2, {"gdp": 1.0})
    for turn in (1, 2):
        FacilityBook.objects.create(
            island=island,
            turn=turn,
            facility=f.number,
            items={"sales": 100 * turn, "wages": 40, "profit": 60},
            deposit=10 * turn,
        )
    url = f"/api/islands/{island.id}/facilities/{f.number}/book"
    assert client.get(url).status_code == 403
    _login(client, "other")
    assert client.get(url).status_code == 403
    island.owner = _login(client)
    island.save()
    data = client.get(url).json()
    assert data["turns"] == [1, 2]
    assert data["items"]["sales"] == [100, 200]
    assert data["signs"]["wages"] == -1 and data["labels"]["sales"] == "売上"
    assert data["deposit"] == [10, 20]
    assert client.get(f"/api/islands/{island.id}/facilities/9999/book").status_code == 404


# --- 金融 ---------------------------------------------------------------------------------


def test_finance_api_shows_banks_only_to_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    _stats(island, 1, {"policy_rate": 3.0, "money.deposits": 1000.0, "gdp": 1.0})
    url = f"/api/islands/{island.id}/finance"
    data = client.get(url).json()
    assert data["stats"]["policy_rate"] == 3.0 and "gdp" not in data["stats"]
    assert "policy_rate" in data["central_bank"]
    assert "banks" not in data and "policy" not in data
    island.owner = _login(client)
    island.save()
    data = client.get(url).json()
    assert data["banks"] and data["banks"][0]["name"]
    assert "policy_rate" in data["policy"]


# --- 画面 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_report_pages_for_owner_and_others(client, world, page):
    island = m.Island.objects.order_by("id").first()
    _stats(island, 1, {"gdp": 1.0, "fiscal.in.tax_income": 10.0, "fiscal.out.wage": 20.0})
    url = f"/islands/{island.id}/{page}/"
    res = client.get(url)
    assert res.status_code == 200
    html = res.content.decode()
    assert 'aria-current="page"' in html
    assert 'id="orders"' not in html  # 他国の人には命令の欄を出さない
    island.owner = _login(client)
    island.save()
    res = client.get(url)
    assert res.status_code == 200
    assert res.context["mine"] is True
    if page in ("fiscal", "finance"):
        assert 'id="orders"' in res.content.decode()


def test_economy_page_with_a_layer_of_nobody(client, world):
    # 誰もいない層（1人あたりの所得が出せない）があっても、経済の画面が開く
    island = m.Island.objects.order_by("id").first()
    _stats(island, 1, {"gdp": 1.0, "layer.general.income": 0.0, "layer.general.persons": 0.0})
    assert client.get(f"/islands/{island.id}/economy/").status_code == 200


def test_owner_only_sections(client, world):
    island = m.Island.objects.order_by("id").first()
    _stats(island, 1, {"gdp": 1.0})
    facilities = client.get(f"/islands/{island.id}/facilities/").content.decode()
    finance = client.get(f"/islands/{island.id}/finance/").content.decode()
    assert "他国の島のため" in facilities and "data-book=" not in facilities
    assert 'id="banks"' not in finance
    island.owner = _login(client)
    island.save()
    FacilityBook.objects.create(
        island=island, turn=1, facility=_first_facility(island).number, items={"profit": -5}
    )
    facilities = client.get(f"/islands/{island.id}/facilities/").content.decode()
    finance = client.get(f"/islands/{island.id}/finance/").content.decode()
    assert "他国の島のため" not in facilities and "data-book=" in facilities
    assert 'id="banks"' in finance


def test_fiscal_page_balance_excludes_bonds(client, world):
    island = _own_island(client)
    _stats(
        island,
        1,
        {
            "fiscal.in.tax_income": 100.0,
            "fiscal.in.bond_issue": 50.0,
            "fiscal.out.wage": 130.0,
            "spent.education": 7.0,
        },
    )
    fiscal = client.get(f"/islands/{island.id}/fiscal/").context["fiscal"]
    assert [r["kind"] for r in fiscal["revenue"]] == ["tax_income"]
    assert [r["kind"] for r in fiscal["financing"]] == ["bond_issue"]
    assert fiscal["deficit"] is True
    assert fiscal["spending"][0]["name"].startswith("賃金（")
    assert "education" in [b["item"] for b in fiscal["budget"]]


def test_island_page_links_to_reports(client, world):
    island = m.Island.objects.order_by("id").first()
    html = client.get(f"/islands/{island.id}/").content.decode()
    for page in PAGES:
        assert f'href="/islands/{island.id}/{page}/"' in html


# --- 地図とマスの欄のインフラ ----------------------------------------------------------------


def test_map_api_infra_and_freight(client, world):
    island = m.Island.objects.order_by("id").first()
    tile = island.tiles.exclude(terrain="sea").order_by("y", "x").first()
    tile.infra = {"road": [600, 800], "power": [100, 100]}  # 送電網 10% は送電線にならない
    tile.save()
    island.economy = {**(island.economy or {}), "infra": {"queue": [["rail", tile.x, tile.y]]}}
    island.save()
    data = client.get(f"/api/islands/{island.id}/map").json()
    i = tile.y * island.size + tile.x
    assert set(data["infra"]) == set(get_game().defs.infrastructure)
    assert data["infra"]["road"][i] == 60 and data["infra"]["power"][i] == 10
    assert data["power_line"][i] == 0
    assert data["infra_queue"] == [["rail", tile.x, tile.y]]
    assert "freight" not in data
    tile.infra = {"power": 1}  # フェーズ1・2 の送電線
    tile.save()
    data = client.get(f"/api/islands/{island.id}/map", {"freight": 1}).json()
    assert data["power_line"][i] == 1 and data["infra"]["power"][i] == 100
    assert len(data["freight"]) == island.size**2 and data["freight_max"] > 0
    assert any(r is not None for r in data["freight"])


def test_tile_api_infra_and_education(client, world):
    island = m.Island.objects.order_by("id").first()
    school = _first_facility(island, "school")
    tile = island.tiles.get(x=school.x, y=school.y)
    tile.infra = {"water": [500, 500]}
    tile.save()
    queue = [["road", 0, 0], ["road", school.x, school.y]]
    island.economy = {**(island.economy or {}), "infra": {"queue": queue, "why": "labor"}}
    island.save()
    data = client.get(f"/api/islands/{island.id}/tiles/{school.x},{school.y}").json()
    assert data["infra"] == [{"kind": "water", "name": "上下水道", "level": 50.0, "keep": 50.0}]
    assert data["works"] == [{"kind": "road", "name": "道路", "position": 2}]
    assert data["works_total"] == 2 and data["works_why"]
    assert data["facility"]["education"] == "子どもの定員 3,000 人"


def test_engineers_info():
    fdef = {"staff": {"general": 100, "engineer": 10}}
    f = SimpleNamespace(economy={"engineers": 4, "engineers_wanted": 10, "engineer_wage": 900})
    assert display.engineers_info(f, fdef) == {
        "engineers_needed": 10,
        "engineers": 4,
        "engineers_wanted": 10,
        "engineer_wage": 900,
    }
    general = SimpleNamespace(economy={"engineers_general": 3})
    assert display.engineers_info(general, {"staff": {"general": 50}}) == {"engineers_general": 3}
    assert display.education_text({"engineers": 10}) == "1ターンに技術者を 10 人育てる"


def test_describe_build_infrastructure():
    text = display.describe_order(
        "build_infrastructure", {"kind": "road", "tiles": [[1, 2], [3, 4]]}
    )
    assert text.endswith(": 道路 2 マス")
