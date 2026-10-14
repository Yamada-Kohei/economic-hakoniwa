"""サーバーでの銀行・国債・企業（フェーズ2-2）: 命令の説明、金融の政策の表示、命令の受け付け、
マスの欄の借入・銀行・差し押さえ、融資の保存。"""

from __future__ import annotations

import pytest

from hakoniwa_core.economy import banks, facility_entity
from server.economy.models import Bond
from server.game import get_game
from server.turns.services import run_turn
from server.web import display
from server.world import models as m
from server.world.repository import WorldRepository
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("kind", "payload", "text"),
    [
        (
            "set_zone",
            {"zone": "industrial", "tiles": [[1, 2], [3, 4]]},
            "工業団地・オフィス街の指定: 工業団地 2 マス",
        ),
        (
            "set_zone",
            {"zone": "office", "tiles": [[1, 2]]},
            "工業団地・オフィス街の指定: オフィス街 1 マス",
        ),
        (
            "set_zone",
            {"zone": "none", "tiles": [[1, 2]]},
            "工業団地・オフィス街の指定の解除: 1 マス",
        ),
        (
            "set_subsidy",
            {"kind": "production", "good": "steel", "rate": 10, "turns": 12},
            "補助金の変更: 生産補助・鋼鉄 10% 12 ターン",
        ),
        (
            "set_subsidy",
            {"kind": "capital", "good": "steel", "rate": 2.5, "turns": 24},
            "補助金の変更: 設備投資補助・鋼鉄 2.5% 24 ターン",
        ),
        (
            "set_subsidy",
            {"kind": "bailout", "x": 3, "y": 4, "rate": 0, "turns": 1},
            "補助金の変更: 救済補助・(3, 4) の施設をやめる",
        ),
        ("nationalize", {"x": 1, "y": 2}, "国有化 (1, 2)"),
        ("privatize", {"x": 1, "y": 2}, "民営化 (1, 2)"),
        (
            "set_monetary_policy",
            {"policy_rate": 2.5, "bond_trade": -1000},
            "金融政策の変更: 政策金利 2.5%・中央銀行が国債を毎ターン 1,000 売る",
        ),
        (
            "set_monetary_policy",
            {"bond_trade": 5000},
            "金融政策の変更: 中央銀行が国債を毎ターン 5,000 買う",
        ),
        ("set_central_bank_independence", {"independent": True}, "中央銀行の独立: 独立させる"),
        (
            "set_central_bank_independence",
            {"independent": False},
            "中央銀行の独立: 政府の下に戻す",
        ),
        (
            "set_bond_policy",
            {
                "unsold": "cut",
                "issue": 1_000_000,
                "issue_term": "bond_5y",
                "underwrite": 5000,
                "terms": {"bond_1y": 1, "bond_10y": 3},
            },
            "国債の政策の変更: 売れ残りは支出を減らす・追加の入札 1,000,000（5年国債）・"
            "中央銀行の直接の引き受け 5,000・期間の割合（1年国債 25%、10年国債 75%）",
        ),
        ("set_bond_payment", {"stop": True}, "国債の利払いの停止・再開: 止める（債務不履行）"),
        ("set_bond_payment", {"stop": False}, "国債の利払いの停止・再開: 再開する"),
        (
            "set_bank_policy",
            {"entry": "license", "deposit_insurance": -1},
            "銀行の政策の変更: 民間銀行の参入 許可制・預金保護の上限 無制限",
        ),
        (
            "set_bank_policy",
            {"deposit_insurance": 50000},
            "銀行の政策の変更: 預金保護の上限 1人あたり 50,000",
        ),
        ("grant_bank_license", {}, "銀行の免許"),
        (
            "emergency_loan",
            {"x": 1, "y": 2, "amount": 50000},
            "銀行への緊急の貸し出し: (1, 2) 上限 50,000",
        ),
        (
            "emergency_loan",
            {"x": 1, "y": 2, "amount": 0},
            "銀行への緊急の貸し出し: (1, 2) 取り消し",
        ),
        (
            "resolve_bank",
            {"x": 1, "y": 2, "method": "transfer"},
            "破綻した銀行の処理: (1, 2) 引き継ぎ",
        ),
    ],
)
def test_describe_finance_orders(kind, payload, text):
    assert display.describe_order(kind, payload) == text


def test_summary_shows_finance_stats():
    stats = {
        "deposit_rate": 1.0,
        "loans": 12345,
        "loans.arrears": 0,
        "banks": 2,
        "firms.bankrupt": 1,
    }
    rows = {r["key"]: r["text"] for r in display.summary(stats, "H")}
    assert rows == {
        "deposit_rate": "1.00%",
        "loans": "H12,345",
        "loans.arrears": "H0",
        "banks": "2",
        "firms.bankrupt": "1",
    }


def test_policy_view_has_finance_policy(client, world):
    island = _own_island(client)  # 市場経済の島
    url = f"/api/islands/{island.id}/orders"
    policy = client.get(url).json()["policy"]
    assert policy["has_economy"] is True and policy["planned"] is False
    assert policy["policy_rate"] == 3.0 and policy["cb_independent"] is False
    assert policy["cb_bond_trade"] == 0 and policy["cb_unlock_turn"] is None
    assert policy["bond_unsold"] == "underwrite"
    assert policy["bond_unsold_choices"] == {
        "underwrite": "中央銀行が引き受け",
        "cut": "支出を減らす",
    }
    assert policy["bond_terms"]["bond_1y"] == {"name": "1年国債", "turns": 12, "share": 100.0}
    assert policy["bond_terms"]["bond_10y"]["share"] == 0
    # 参入の政策を決めていなければ、経済体制の既定を添える
    assert policy["bank_entry"] == "" and policy["bank_entry_default"] == "free"
    assert policy["bank_entry_choices"]["license"] == "許可制"
    assert policy["deposit_insurance"] == 30000
    assert policy["deposit_insurance_text"] == "1人あたり 30,000"
    assert policy["subsidies"] == []

    subsidy = {"kind": "production", "good": "steel", "rate": 1000, "until": 12}
    m.Island.objects.filter(pk=island.pk).update(
        policy={**island.policy, "subsidies": [subsidy], "cb_independent": True}
    )
    policy = client.get(url).json()["policy"]
    assert policy["cb_independent"] is True
    assert policy["subsidies"] == [
        {
            "text": "生産補助・鋼鉄 10%（ターン 12 まで）",
            "stop": {"kind": "production", "good": "steel", "rate": 0, "turns": 1},
        }
    ]


def _market_island_sites(island: m.Island) -> dict:
    """命令の試しに使う、市場経済の島の銀行・民営の施設・空いた平地のマス。"""
    bank = island.facilities.filter(kind=get_game().config.get("banking.facility")).first()
    private = (
        island.facilities.filter(owner="private", built=True, x__gte=0)
        .exclude(kind__in=["bank", "housing"])
        .first()
    )
    used = set(island.facilities.values_list("x", "y"))
    free = [
        t
        for t in island.tiles.filter(terrain="plain", population=0).order_by("y", "x")
        if (t.x, t.y) not in used and (t.x, t.y) != (island.capital_x, island.capital_y)
    ]
    return {"bank": bank, "private": private, "free": free[0]}


def test_finance_orders_are_accepted(client, world):
    """フェーズ2-2 の命令をすべて並べられる（今の島で通らない命令は警告だけ）。"""
    island = _own_island(client)
    sites = _market_island_sites(island)
    bank, private, free = sites["bank"], sites["private"], sites["free"]
    farm = island.facilities.filter(kind="farm", owner="private", built=True).last()
    farm.owner = "state"
    farm.save()
    orders = [
        ("set_zone", {"zone": "industrial", "tiles": [[free.x, free.y]]}),
        ("set_subsidy", {"kind": "production", "good": "steel", "rate": 10, "turns": 12}),
        ("set_subsidy", {"kind": "bailout", "x": private.x, "y": private.y, "rate": 5, "turns": 6}),
        ("nationalize", {"x": private.x, "y": private.y}),
        ("privatize", {"x": farm.x, "y": farm.y}),
        ("set_monetary_policy", {"policy_rate": 2.5, "bond_trade": 1000}),
        ("set_central_bank_independence", {"independent": True}),
        ("set_bond_policy", {"unsold": "cut", "terms": {"bond_1y": 1, "bond_5y": 1}}),
        ("set_bond_payment", {"stop": False}),
        ("set_bank_policy", {"entry": "license", "deposit_insurance": -1}),
        ("grant_bank_license", {}),
        ("emergency_loan", {"x": bank.x, "y": bank.y, "amount": 10000}),
        ("resolve_bank", {"x": bank.x, "y": bank.y, "method": "bailout"}),
    ]
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    data = res.json()
    assert [o["kind"] for o in data["orders"]] == [k for k, _ in orders]
    # 破綻していない銀行の処理だけは、今の島では通らない
    assert data["warnings"] == [{"index": 12, "text": "その銀行は破綻していません"}]
    texts = [o["text"] for o in data["orders"]]
    assert texts[0] == "工業団地・オフィス街の指定: 工業団地 1 マス"
    assert texts[11] == f"銀行への緊急の貸し出し: ({bank.x}, {bank.y}) 上限 10,000"

    # 形の誤りは受け付けない
    bad = {"orders": [{"kind": "set_bank_policy", "payload": {"entry": "anyone"}}]}
    assert client.put(url, bad, content_type="application/json").status_code == 400


def test_bond_payment_needs_bonds(client, world):
    """国債の利払いの停止は、国債の銘柄があるときだけ通る（命令の確かめは国債も読む）。"""
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": "set_bond_payment", "payload": {"stop": True}}]}
    island.bonds.all().delete()
    warnings = client.put(url, body, content_type="application/json").json()["warnings"]
    assert warnings == [{"index": 0, "text": "国債がありません"}]
    Bond.objects.create(island=island, number=1, issued=0, term=12, rate=300, face=1000)
    assert client.put(url, body, content_type="application/json").json()["warnings"] == []


def test_zone_order_runs_and_shows_on_tile(client, world):
    island = _own_island(client)
    free = _market_island_sites(island)["free"]
    url = f"/api/islands/{island.id}/orders"
    body = {
        "orders": [
            {"kind": "set_zone", "payload": {"zone": "industrial", "tiles": [[free.x, free.y]]}}
        ]
    }
    assert client.put(url, body, content_type="application/json").status_code == 200
    run_turn(force=True)
    tile = client.get(f"/api/islands/{island.id}/tiles/{free.x},{free.y}").json()
    assert tile["zone"] == "industrial"
    data = client.get(f"/api/islands/{island.id}/map").json()
    assert data["zone"][free.y * island.size + free.x] == "industrial"


def test_tile_api_bank_and_actions(client, world):
    """銀行のマスには帳簿と緊急の貸し出しを、破綻したら処理の選び方を出す。"""
    island = m.Island.objects.order_by("id").first()
    sites = _market_island_sites(island)
    bank, private = sites["bank"], sites["private"]
    run_turn(force=True)
    url = f"/api/islands/{island.id}/tiles/{bank.x},{bank.y}"
    info = client.get(url).json()["facility"]
    assert {"deposits", "loans", "net_worth", "capital_ratio", "deposit_rate"} <= set(info["bank"])
    assert info["bank"]["turn"] == 1 and info["bank"]["regulatory_ratio"] == "8%"
    assert info["emergency_loan"] is True
    assert "resolve_methods" not in info and "ownership_target" not in info
    assert "subsidy_target" not in info
    assert info["books"]["deposit_label"] == "準備預金"

    bank.refresh_from_db()
    bank.economy = {**bank.economy, "failed": 1}
    bank.save()
    info = client.get(url).json()["facility"]
    assert info["bank"]["failed"] == 1 and info["bank"]["failed_reason"] == "capital"
    assert info["resolve_methods"] == {
        "bailout": "救済",
        "transfer": "引き継ぎ",
        "liquidation": "清算",
    }

    # 民営の施設には国有化と補助金を出す
    data = client.get(f"/api/islands/{island.id}/tiles/{private.x},{private.y}").json()
    facility = data["facility"]
    assert facility["ownership_target"] == "nationalize"
    assert facility["ownership_target_name"] == "国有化"
    assert facility["subsidy_target"] is True and facility["subsidies"] == []


def test_tile_api_ownership_and_seizure(client, world):
    """民営化できる国営施設には民営化を出し、差し押さえ・閉鎖の途中ならそれを出す。"""
    island = m.Island.objects.order_by("id").first()
    farm = island.facilities.filter(kind="farm", owner="private", built=True).first()
    url = f"/api/islands/{island.id}/tiles/{farm.x},{farm.y}"
    farm.owner = "state"
    farm.save()
    assert client.get(url).json()["facility"]["ownership_target"] == "privatize"

    bank = island.facilities.filter(kind="bank").first()
    farm.owner = "private"
    farm.economy = {
        **farm.economy,
        "closing": 3,
        "foreclosed": {"bank": f"bank:{bank.number}", "since": 2, "price": 1500},
    }
    farm.save()
    info = client.get(url).json()["facility"]
    assert info["closing"] == 3
    until = 2 + get_game().config.get("firms.foreclosure_turns")
    assert info["foreclosed"]["since"] == 2 and info["foreclosed"]["until"] == until
    assert info["foreclosed"]["price"].endswith("1,500")
    assert info["foreclosed"]["bank"]


def test_loans_round_trip_and_show_on_tile(client, world):
    """融資は保存して読み直しても同じで、借り手の施設のマスに出る。"""
    repo = WorldRepository(world)
    state = repo.load()
    island = state.islands[min(state.islands)]
    lender = banks.bank_list(island)[0]
    f = next(
        f
        for f in island.facilities
        if f.built and f.accounting == "self_supporting" and f.kind == "farm"
    )
    borrower = facility_entity(f.id)
    loan = banks.lend(island, lender, borrower, 50_000, banks.WORKING, 6, state.turn, 550)
    assert loan is not None
    loan.arrears = 1
    repo.save(state)

    again = WorldRepository(m.World.objects.get(pk=world.pk)).load()
    loaded = again.islands[island.id]
    assert loaded.loans == island.loans
    assert loaded.economy.loan_seq == island.economy.loan_seq
    assert banks.outstanding(loaded, loaded.loans[0]) == 50_000
    assert loaded.accounts == island.accounts

    data = client.get(f"/api/islands/{island.id}/tiles/{f.x},{f.y}").json()
    shown = data["facility"]["loans"]
    assert len(shown) == 1
    assert shown[0]["purpose"] == "運転資金" and shown[0]["arrears"] == 1
    assert shown[0]["balance"].endswith("50,000") and shown[0]["rate"] == "5.50%"


def test_banks_of_the_same_kind_have_distinct_names_and_positions(client, world):
    """国が建てた銀行が2つあっても名前が分かれ（地名に番号）、金融の画面にマスの位置が出る。
    債務不履行で新しい融資を止めている銀行と、銀行が国債を買わない期間も出す。"""
    island = _own_island(client)
    config = get_game().config
    first = island.facilities.filter(kind=config.get("banking.facility")).order_by("number")[0]
    second = m.Facility.objects.get(pk=first.pk)
    second.pk = None
    second.number = island.facilities.order_by("-number")[0].number + 1
    block = config.get("naming.place_block")
    used = set(island.facilities.values_list("x", "y"))
    x0, y0 = first.x // block * block, first.y // block * block
    second.x, second.y = next(  # 同じ地名の区画の空いたマス
        (x, y) for y in range(y0, y0 + block) for x in range(x0, x0 + block) if (x, y) not in used
    )
    second.ext = {**(first.ext or {}), "serial": (first.ext or {}).get("serial", 1) + 1}
    turn = island.world.turn
    second.economy = {**(first.economy or {}), "crunch_until": turn + 10}
    second.save()
    data = client.get(f"/api/islands/{island.id}/finance").json()
    rows = {(b["x"], b["y"]): b for b in data["banks"]}
    a, b = rows[(first.x, first.y)], rows[(second.x, second.y)]
    assert a["name"] != b["name"] and "第二" in b["name"]
    assert "crunch_until" not in a and b["crunch_until"] == turn + 9
    html = client.get(f"/islands/{island.id}/finance/").content.decode()
    assert f"({second.x}, {second.y})" in html
    assert f"ターン {turn + 9} まで新しい融資を止めています" in html

    economy = island.economy
    economy.setdefault("finance", {})["default_turn"] = turn
    m.Island.objects.filter(pk=island.pk).update(economy=economy)
    policy = client.get(f"/api/islands/{island.id}/orders").json()["policy"]
    exclusion = config.get("bonds.default_exclusion_turns")
    assert policy["default_turn"] == turn
    assert policy["bonds_closed_until"] == turn + exclusion - 1
    assert policy["default_premium"] == 15 and policy["default_crunch"] == 36


def test_private_banks_in_the_same_place_have_distinct_names(client, world):
    """民間の銀行が同じ地名の区画にいくつあっても、2つ目から地名に番号が付く（Codex の報告）。"""
    island = _own_island(client)
    config = get_game().config
    first = island.facilities.filter(kind=config.get("banking.facility")).order_by("number")[0]
    assert first.owner == "private"
    block = config.get("naming.place_block")
    used = set(island.facilities.values_list("x", "y"))
    x0, y0 = first.x // block * block, first.y // block * block
    free = [(x, y) for y in range(y0, y0 + block) for x in range(x0, x0 + block)]
    free = [p for p in free if p not in used]
    number = island.facilities.order_by("-number")[0].number
    added = []
    for i, (x, y) in enumerate(free[:3], start=1):
        bank = m.Facility.objects.get(pk=first.pk)
        bank.pk, bank.number, bank.x, bank.y = None, number + i, x, y
        bank.owner = "private"
        bank.ext = {**(first.ext or {}), "serial": 10 + i}
        bank.economy = {}
        bank.save()
        added.append((x, y))
    data = client.get(f"/api/islands/{island.id}/finance").json()
    names = {(b["x"], b["y"]): b["name"] for b in data["banks"]}
    shown = [names[(first.x, first.y)], *(names[p] for p in added)]
    assert len(set(shown)) == 4  # 「首都銀行」が4つ並ばない
    assert all(n in name for n, name in zip(("第二", "第三", "第四"), shown[1:], strict=True))


def test_help_describes_default_penalties(client, world):
    html = client.get("/help/").content.decode()
    exclusion = get_game().config.get("bonds.default_exclusion_turns")
    assert f"{exclusion} ターンのあいだは銀行が国債を買わず" in html
