"""フェーズ3-3 の画面と API: 貿易の画面の産業政策と援助、圏の決まり、為替の画面の国際収支の内訳・
為替と資本取引の政策・外国の国債、国債の一覧の外国の持ち分、統計の順位の画面、得意産業と称号、命令の
説明と統計の表示名。"""

from __future__ import annotations

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.trade import abroad, swap
from server.economy.models import Account
from server.game import get_game
from server.orders.models import Order
from server.turns.services import run_turn
from server.web import display, reports
from server.world import models as m
from tests.server.test_phase1 import _login
from tests.server.test_trade import _trading_world

pytestmark = pytest.mark.django_db


def _set_policy(island: m.Island, **fields) -> None:
    policy = dict(island.policy)
    policy.update(fields)
    m.Island.objects.filter(pk=island.pk).update(policy=policy)
    island.refresh_from_db()


# --- 貿易の画面 ---------------------------------------------------------------------------


def test_trade_page_shows_policy_and_aid_forms_to_the_owner(client, world):
    own, second, third = _trading_world(client)
    _set_policy(
        own,
        tariffs={"coal": 1250},
        export_subsidies={"steel": 7},
        import_quotas={"coal": 2500},
        embargoes={str(third.id): ["coal"]},
    )
    _set_policy(second, embargoes={str(own.id): []})
    data = client.get(f"/api/islands/{own.id}/trade").json()
    rules = data["policy"]
    goods = {g["good"]: g for g in rules["goods"]}
    assert goods["coal"]["tariff"] == 12.5 and goods["coal"]["quota"] == 62.5
    assert goods["steel"]["subsidy"] == 0.3 and goods["steel"]["tariff"] is None
    assert rules["embargoes"] == [
        {"partner": third.id, "partner_name": third.name, "goods": ["石炭"]}
    ]
    assert rules["embargoed_by"] == [
        {"partner": second.id, "partner_name": second.name, "goods": []}
    ]
    assert {"no_tariff", "fixed"} <= set(data["blocs"][0])
    html = client.get(f"/islands/{own.id}/trade/").content.decode()
    for kind in ("set_tariff", "set_export_subsidy", "set_import_quota", "set_embargo", "send_aid"):
        assert f'data-order="{kind}"' in html
    assert "この国に禁輸している国" in html and "圏内の関税" in html
    client.logout()
    data = client.get(f"/api/islands/{own.id}/trade").json()
    assert "policy" not in data
    html = client.get(f"/islands/{own.id}/trade/").content.decode()
    assert 'id="trade-policy"' not in html


def test_bloc_rules_show_to_members_and_the_representative_sets_them(client, world):
    own, second, _ = _trading_world(client)
    w = m.World.objects.get()
    bloc = w.ext["trade"]["blocs"][0]
    bloc["no_tariff"] = True
    bloc["fixed"] = {"coal": 640}
    w.save()
    view = client.get("/api/world/trade").json()["blocs"][0]
    assert view["no_tariff"] is True
    assert view["fixed"] == [{"good": "coal", "name": "石炭", "unit": "トン", "price": 25.6}]
    html = client.get(f"/islands/{own.id}/trade/").content.decode()
    assert "圏の市場で買った荷に関税をかけない決まり" in html and "石炭" in html
    # 代表は、圏内の関税と固定価格を決める欄を持つ
    second.owner = _login(client, "rep")
    second.save()
    html = client.get(f"/islands/{second.id}/trade/").content.decode()
    assert 'name="no_tariff"' in html and 'name="fixed_good"' in html


def test_goods_aid_contract_is_marked(client, world):
    own, second, _ = _trading_world(client)
    Order.objects.create(
        island=second,
        seq=1,
        kind="send_aid",
        payload={"partner": own.id, "good": "coal", "quantity": 1, "turns": 3},
    )
    run_turn(force=True)
    contracts = client.get(f"/api/islands/{own.id}/trade").json()["contracts"]
    aid = [c for c in contracts if c["aid"]]
    assert aid and aid[0]["price"] == 0 and aid[0]["side"] == "buy"
    assert "物の援助" in client.get(f"/islands/{own.id}/trade/").content.decode()


# --- 為替の画面 ---------------------------------------------------------------------------


def test_fx_page_shows_balance_of_payments_to_everyone(client, world):
    own, _, _ = _trading_world(client)
    data = client.get(f"/api/islands/{own.id}/fx").json()
    bop = data["bop"]["stats"]
    assert {"bop.current", "bop.goods", "bop.financial", "bop.assets.fx"} <= set(bop)
    assert bop["bop.current"] == bop["bop.financial"]
    assert data["bop"]["labels"]["bop.income"].startswith("経常収支・所得収支")
    assert "fx.reserves" in data["stats"]
    client.logout()
    res = client.get(f"/islands/{own.id}/fx/")
    html = res.content.decode()
    assert "経常収支と金融収支の内訳" in html and "経常収支（1ターン）" in html
    assert 'id="fx-policy"' not in html and 'id="foreign-bonds"' not in html
    assert "policy" not in client.get(f"/api/islands/{own.id}/fx").json()


def test_fx_page_shows_policy_and_foreign_bonds_to_the_owner(client, world):
    own, second, third = _trading_world(client)
    rate = 10_000
    _set_policy(
        own,
        capital_controls="",
        fx_intervention={str(second.id): -300},
        fx_peg={str(second.id): rate},
        foreign_bonds={"gov": {str(second.id): 500}},
    )
    ext = own.ext
    ext["trade"][abroad.KEY] = {
        "cb": {str(second.id): [4000, 3900, 3950]},
        "bank:1": {str(second.id): [1000, 1000, 990]},
        "bank:2": {str(second.id): [500, 500, 495]},
    }
    m.Island.objects.filter(pk=own.pk).update(ext=ext)
    data = client.get(f"/api/islands/{own.id}/fx").json()
    rules = data["policy"]
    defs = get_game().defs
    regime = own.regime["economy"]
    assert rules["capital_controls"] == defs.regimes[regime]["capital_controls"]
    assert rules["capital_default"] is True and rules["cb_independent"] is False
    assert rules["interventions"] == [
        {"currency": second.id, "name": second.names["currency"], "amount": 300, "side": "売る"}
    ]
    [peg] = rules["pegs"]
    band = get_game().config.get("fx.peg_band")
    assert peg["rate"] == 1.0 and peg["low"] == pytest.approx(1 - band)
    [order] = rules["bond_orders"]
    assert order["holder_name"] == "政府" and order["amount"] == 500 and order["side"] == "買う"
    assert {c["id"] for c in rules["currencies"]} == {second.id}
    assert {i["id"] for i in rules["issuers"]} == {second.id, third.id}
    bonds = {b["holder"]: b for b in data["foreign_bonds"]}
    assert bonds["cb"]["face"] == 4000 and bonds["cb"]["value"] == 3950
    assert bonds["banks"]["face"] == 1500 and bonds["banks"]["holder_name"] == "市中銀行"
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    for kind in ("set_fx_intervention", "set_fx_peg", "set_capital_controls", "set_foreign_bonds"):
        assert f'data-order="{kind}"' in html
    assert 'data-heading="為替の命令"' in html and "外国の国債" in html
    # 中央銀行が独立していれば、介入と固定相場の欄は出さない
    _set_policy(own, cb_independent=True)
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    assert 'data-order="set_fx_peg"' not in html and 'data-order="set_foreign_bonds"' in html


def test_fx_page_shows_swaps_to_the_owner(client, world):
    """中央銀行どうしの融通: 借りた国と、決済通貨を作って貸した国（圏の代表）の画面。"""
    own, second, _ = _trading_world(client)
    for island, table, other in ((own, swap.OWED, second), (second, swap.LENT, own)):
        ext = island.ext
        ext["trade"][table] = {str(second.id): {str(other.id): 1200}}
        m.Island.objects.filter(pk=island.pk).update(ext=ext)
    data = client.get(f"/api/islands/{own.id}/fx").json()
    assert {"fx.swap.borrowed", "fx.swap.lent"} <= set(data["stats"])
    assert {"bop.assets.swaps", "bop.liabilities.swaps"} <= set(data["bop"]["stats"])
    view = data["swaps"]
    [row] = view["owed"]
    assert row["lender"] == second.id and row["other_name"] == second.name
    assert row["amount"] == 1200 and row["issued"] is True and row["rate"] >= 1.0
    assert view["lent"] == [] and view["issuer_room"] is None
    assert view["borrowed"] == row["own"] > 0
    assert view["quota"] == pytest.approx(get_game().config.get("fx.swap_quota") * 100)
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    assert 'id="swaps"' in html and "発行した国が作って貸した分" in html
    # 貸した側（決済通貨を発行した国）
    second.owner = _login(client, "rep")
    second.save()
    view = client.get(f"/api/islands/{second.id}/fx").json()["swaps"]
    [row] = view["lent"]
    assert row["borrower"] == own.id and row["issued"] is True and row["own"] == 1200
    assert view["issued"] == 1200 and view["owed"] == []
    html = client.get(f"/islands/{second.id}/fx/").content.decode()
    assert "自国の通貨を作って貸した分" in html and "自国の通貨を作って貸している額" in html
    client.logout()
    html = client.get(f"/islands/{second.id}/fx/").content.decode()
    assert 'id="swaps"' not in html
    assert "swaps" not in client.get(f"/api/islands/{second.id}/fx").json()


def test_bond_list_shows_foreign_holders(client, world):
    own, second, _ = _trading_world(client)
    bond = own.bonds.order_by("number").first()
    assert bond is not None
    holder = ledger.nonresident(second.id, ledger.CB)
    name = f"{holder}/{ledger.bond_item(bond.number)}/{ledger.GOV}"
    Account.objects.create(island=own, name=name, balance=700)
    row = next(r for r in reports.bond_rows(own, "", True) if r["number"] == bond.number)
    assert row["foreign"] == display.money_text(700, "")
    assert {"name": f"{second.name}の中央銀行", "where": "", "amount": row["foreign"]} in (
        row["holders"]
    )
    assert reports._holder_name(ledger.nonresident(second.id, "bank:3"), {}) == (
        f"{second.name}の銀行"
    )
    html = client.get(f"/islands/{own.id}/fiscal/").content.decode()
    assert "うち外国" in html and f"{second.name}の中央銀行" in html


# --- 順位と得意産業 -----------------------------------------------------------------------


def test_rankings_page_ranks_countries_in_one_currency(client, world):
    own, second, third = _trading_world(client)
    w = m.World.objects.get()
    w.ext["rankings"] = {"titles": {"steel": second.id}, "turn": w.turn}
    w.save()
    ext = second.ext
    ext["experience"] = {"steel": [24_000, 0], "mining": [5_000, 0]}
    m.Island.objects.filter(pk=second.pk).update(ext=ext)
    res = client.get("/rankings/")
    assert res.status_code == 200
    data = res.context["data"]
    assert data["base"] == own.id and data["base_name"] == own.names["currency"]
    names = [c["name"] for c in data["categories"]]
    assert names == ["gdp", "gdp_per_person", "population", "happiness", "trade.exports"]
    population = data["categories"][2]["rows"]
    values = [
        m.StatRecord.objects.get(island_id=r["island"], turn=w.turn, name="population").value
        for r in population
    ]
    assert values == sorted(values, reverse=True) and population[0]["rank"] == 1
    steel = next(i for i in data["industries"] if i["industry"] == "steel")
    assert steel["holder"] == second.id and steel["title"] == "鋼都"
    assert steel["top"][0] == {"rank": 1, "island": second.id, "name": second.name, "turns": 24.0}
    html = res.content.decode()
    assert "統計の順位" in html and "鋼都" in html and 'href="/rankings/"' in html
    # 通貨を選べる。知らない番号は既定に戻す
    assert client.get(f"/rankings/?base={second.id}").context["data"]["base"] == second.id
    assert client.get("/rankings/?base=999").context["data"]["base"] == own.id
    assert client.get("/rankings/?base=x").context["data"]["base"] == own.id
    client.logout()
    assert client.get("/rankings/").context["data"]["base"] == min(
        i.id for i in m.Island.objects.all()
    )


def test_rankings_page_without_a_world(client, db):
    res = client.get("/rankings/")
    assert res.status_code == 200 and res.context["data"] is None


def test_island_and_economy_pages_show_strengths_and_titles(client, world):
    island = m.Island.objects.order_by("id").first()
    ext = island.ext
    ext["experience"] = {"steel": [36_000, 1000], "textile": [12_000, 0], "food": [0, 0]}
    m.Island.objects.filter(pk=island.pk).update(ext=ext)
    w = m.World.objects.get()
    w.ext["rankings"] = {"titles": {"steel": island.id}}
    w.save()
    html = client.get(f"/islands/{island.id}/economy/").content.decode()
    assert "得意産業と称号" in html and "鋼都" in html and "経験 36 ターン分" in html
    html = client.get(f"/islands/{island.id}/").content.decode()
    assert "得意産業" in html and "鉄鋼・繊維" in html and "鋼都" in html


# --- 命令の説明と統計の表示名 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "payload", "text"),
    [
        ("set_tariff", {"good": "coal", "rate": 12.5}, "関税: 石炭 12.5%"),
        ("set_tariff", {"good": "coal", "rate": 0}, "関税をやめる: 石炭"),
        ("set_export_subsidy", {"good": "coal", "amount": 750}, "輸出補助: 石炭 1トンあたり 30"),
        (
            "set_import_quota",
            {"good": "coal", "quantity": 2.5},
            "輸入の割り当て: 石炭 1ターン 62.5 トンまで",
        ),
        ("set_import_quota", {"good": "coal"}, "輸入の割り当てをやめる: 石炭"),
        ("set_capital_controls", {"mode": "official"}, "資本取引の制限: 政府と中央銀行だけ"),
        ("set_fx_peg", {"currency": 2, "rate": 0}, "固定相場をやめる: "),
    ],
)
def test_describe_policy_orders(world, kind, payload, text):
    assert display.describe_order(kind, payload).startswith(text)


def test_describe_policy_orders_with_countries(world):
    first, second = m.Island.objects.order_by("id")[:2]
    currency = second.names["currency"]
    cases = [
        ("set_embargo", {"partner": second.id}, f"禁輸を始める: {second.name}（すべての財）"),
        (
            "set_embargo",
            {"partner": second.id, "good": "coal", "lift": True},
            f"禁輸をやめる: {second.name}（石炭）",
        ),
        (
            "send_aid",
            {"partner": second.id, "amount": 500},
            f"援助: {second.name}に 500（自国通貨）",
        ),
        (
            "send_aid",
            {"partner": second.id, "good": "coal", "quantity": 2, "turns": 6},
            f"援助: {second.name}に石炭を毎ターン 50 トン（6 ターン）",
        ),
        (
            "set_fx_intervention",
            {"currency": second.id, "amount": -100},
            f"為替の介入: {currency}を毎ターン 100 売る",
        ),
        (
            "set_fx_peg",
            {"currency": second.id, "rate": 1.25},
            f"固定相場: {currency} 1 = 自国通貨 1.25",
        ),
        (
            "set_foreign_bonds",
            {"issuer": second.id, "holder": "cb", "amount": 300},
            f"外国の国債の売り買い: 中央銀行が{second.name}の国債を毎ターン 300 買う",
        ),
        (
            "bloc_settings",
            {"bloc": 9, "no_tariff": True, "fixed_good": "coal", "fixed_price": 500},
            "圏内の関税をなくす・石炭の値段を1トンあたり 20 に固定",
        ),
    ]
    for kind, payload, text in cases:
        assert text in display.describe_order(kind, payload), kind


def test_policy_orders_are_accepted(client, world):
    island = m.Island.objects.order_by("id").first()
    user = _login(client)
    island.owner = user
    island.save()
    run_turn(force=True)  # 貿易の仕組みを置く
    other = m.Island.objects.exclude(pk=island.pk).order_by("id").first()
    orders = [
        ("set_tariff", {"good": "coal", "rate": 10}),
        ("set_export_subsidy", {"good": "coal", "amount": 5}),
        ("set_import_quota", {"good": "coal", "quantity": 3}),
        ("set_embargo", {"partner": other.id, "good": "coal"}),
        ("send_aid", {"partner": other.id, "amount": 10}),
        ("set_capital_controls", {"mode": "closed"}),
        ("set_fx_intervention", {"currency": other.id, "amount": 0}),
        ("set_foreign_bonds", {"issuer": other.id, "holder": "gov", "amount": 0}),
        ("bloc_settings", {"bloc": 1, "no_tariff": True}),
    ]
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    bad = {"orders": [{"kind": "set_capital_controls", "payload": {"mode": "open"}}]}
    res = client.put(f"/api/islands/{island.id}/orders", bad, content_type="application/json")
    assert res.status_code == 400


def test_stat_labels_for_phase3_3(world):
    island = m.Island.objects.order_by("id").first()
    for name in (
        "trade.tariffs",
        "trade.export_subsidies",
        "aid.received",
        "bop.current",
        "bop.liabilities.bonds",
        "fx.reserves",
        "fx.swap.borrowed",
        "fx.swap.lent",
        "bop.assets.swaps",
        "bop.liabilities.swaps",
        "foreign_bonds.banks",
        "bonds.foreign",
        "titles",
    ):
        assert reports.stat_label(name)[0] != name, name
    assert reports.stat_label("experience.steel") == (
        "鉄鋼の経験（標準の施設が全力で動いたターン数）",
        "",
    )
    assert reports.stat_label("experience_rank.steel") == ("鉄鋼の経験の世界の順位", " 位")
    assert reports.stat_label("experience.unknown") == ("experience.unknown", "")
    assert reports.stat_label(f"fx.intervened.{island.id}")[0].startswith("介入と固定相場")
