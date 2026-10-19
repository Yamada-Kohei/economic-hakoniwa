"""フェーズ3-4 の画面と API: 政治の画面（体制・政情・議会・治安と報道・体制を変える）、計画の画面
（公定価格・生産目標・重点産業・値段の上限と下限）、見せる範囲、命令の説明と統計の表示名。"""

from __future__ import annotations

import pytest

from server.game import get_game
from server.turns.services import run_turn
from server.web import display, reports
from server.world import models as m
from tests.server.test_phase1 import _login

pytestmark = pytest.mark.django_db


def _own(client, economy: str | None = None, **regime) -> m.Island:
    """島を1つ自分のものにし（経済体制などを決めて）、ターンを1つ進める。"""
    island = m.Island.objects.order_by("id").first()
    if economy is not None:
        regime["economy"] = economy
    if regime:
        island.regime = {**island.regime, **regime}
    island.owner = _login(client)
    island.save()
    run_turn(force=True)
    island.refresh_from_db()
    return island


def test_politics_page_and_api_show_the_state(client, world):
    island = _own(client, "market", politics="democracy", head_of_state="republic")
    data = client.get(f"/api/islands/{island.id}/politics").json()
    assert data["started"] and data["level"] in ("calm", "demo", "riot", "rebellion")
    assert 0 <= data["stability"] <= 100 and data["parliament"] is True
    assert data["next_election"] > 0 and data["seats"] is not None
    assert [r["axis"] for r in data["regime"]] == ["politics", "head_of_state", "economy"]
    assert {p["key"] for p in data["parts"]} >= {"base", "regime"}
    assert data["can_press"] is False and data["can_rename"] is True
    assert {r["name"] for r in data["parliament_rules"]} >= {"憲法改正", "経済体制の変更"}
    html = client.get(f"/islands/{island.id}/politics/").content.decode()
    for kind in ("dissolve_parliament", "amend_constitution", "change_economy", "rename_leader"):
        assert f'data-order="{kind}"' in html
    assert 'data-order="set_press"' not in html  # 民主主義では統制できない
    assert "政情の目標の内訳" in html and "選挙と議会" in html
    # ほかの人には国全体の数だけ
    client.logout()
    data = client.get(f"/api/islands/{island.id}/politics").json()
    assert "parts" not in data and "votes" not in data and "stability" in data
    html = client.get(f"/islands/{island.id}/politics/").content.decode()
    assert 'data-order="amend_constitution"' not in html and "政情の目標の内訳" not in html


def test_politics_forms_follow_the_regime(client, world):
    island = _own(client, "planned", politics="totalitarian", head_of_state="monarchy")
    data = client.get(f"/api/islands/{island.id}/politics").json()
    assert data["can_press"] and data["can_mobilize"] and not data["can_rename"]
    assert data["monarch_age"] == get_game().config.get("politics.monarch_age")
    assert data["parliament"] is False and data["seats"] is None
    html = client.get(f"/islands/{island.id}/politics/").content.decode()
    assert 'data-order="set_press"' in html and 'data-order="mobilize_labor"' in html
    assert 'data-order="dissolve_parliament"' not in html
    assert 'data-order="rename_leader"' not in html


def test_plan_page_for_a_planned_economy(client, world):
    island = _own(client, "planned")
    nav = client.get(f"/islands/{island.id}/").content.decode()
    assert f'href="/islands/{island.id}/plan/"' in nav and ">計画<" in nav
    data = client.get(f"/api/islands/{island.id}/plan").json()
    assert data["planned"] and data["rows"]
    row = data["rows"][0]
    assert {"good", "price", "goal", "achieved", "queue", "manual", "cost", "gap", "held"} <= set(
        row
    )
    assert data["max_priority"] == get_game().config.get("planning.max_priority")
    config = get_game().config
    assert data["band"] == round(config.get("planning.review_band") * 100)
    assert [g["id"] for g in data["held"]] == [
        g for g in get_game().defs.goods if g in config.get("planning.held_goods")
    ]
    assert any(r["cost"] for r in data["rows"])  # 原価は市場の段で解いてある
    html = client.get(f"/islands/{island.id}/plan/").content.decode()
    assert 'data-order="set_official_price"' in html
    assert 'data-order="review_official_prices"' in html
    assert 'data-order="set_official_price_rules"' in html
    assert 'data-order="set_priority_industries"' in html
    assert 'data-order="set_price_floor"' not in html
    client.logout()
    data = client.get(f"/api/islands/{island.id}/plan").json()
    assert data["planned"] and "rows" not in data and "queue" in data


def test_plan_page_for_a_mixed_economy_shows_price_controls(client, world):
    island = _own(client, "mixed")
    m.Island.objects.filter(pk=island.pk).update(
        policy={**island.policy, "price_floor": {"grain": 120}, "price_cap": {"coal": 900}}
    )
    data = client.get(f"/api/islands/{island.id}/plan").json()
    assert not data["planned"] and data["price_controls"]
    assert [r["good"] for r in data["floors"]] == ["grain"]
    assert [r["good"] for r in data["caps"]] == ["coal"]
    html = client.get(f"/islands/{island.id}/plan/").content.decode()
    assert "値段の規制" in html
    assert 'data-order="set_price_floor"' in html and 'data-order="set_price_cap"' in html


def test_market_economy_has_no_plan_link(client, world):
    island = _own(client, "market")
    html = client.get(f"/islands/{island.id}/").content.decode()
    assert f"/islands/{island.id}/plan/" not in html
    assert f"/islands/{island.id}/politics/" in html


def test_politics_orders_are_accepted_and_described(client, world):
    island = _own(client, "planned", politics="totalitarian", head_of_state="republic")
    orders = [
        ("set_press", {"mode": "limited"}),
        ("mobilize_labor", {"on": True}),
        ("rename_leader", {"name": "新しい元首"}),
        ("set_official_price", {"good": "food", "price": 100}),
        ("set_priority_industries", {"industries": ["steel"]}),
        ("amend_constitution", {"politics": "authoritarian"}),
    ]
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    texts = [o["text"] for o in res.json()["orders"]]
    assert texts[0].endswith("制限")
    assert "始める" in texts[1] and "新しい元首" in texts[2]
    assert "食品" in texts[3] and "100" in texts[3]
    assert "鉄鋼" in texts[4]
    assert "権威主義" in texts[5]
    run_turn(force=True)
    island.refresh_from_db()
    assert island.names["leader"] == "新しい元首"
    assert island.ext["politics"]["press"] == "limited"
    bad = {"orders": [{"kind": "set_press", "payload": {"mode": "silence"}}]}
    res = client.put(f"/api/islands/{island.id}/orders", bad, content_type="application/json")
    assert res.status_code == 400


def test_describe_orders_for_phase3_4(world):
    cases = [
        ("change_economy", {"economy": "mixed"}, "混合経済"),
        ("mobilize_labor", {"on": False}, "やめる"),
        ("set_official_price", {"good": "food", "price": 0}, "上書きをやめる"),
        ("review_official_prices", {"goods": ["coal"]}, "石炭"),
        ("review_official_prices", {}, "すべて"),
        ("set_official_price_rules", {"band": 15, "held": ["grain"]}, "±15%"),
        ("set_official_price_rules", {"held": []}, "据え置く財 なし"),
        ("set_price_floor", {"good": "grain", "price": 0}, "なくす"),
        ("set_priority_industries", {"industries": []}, "なし"),
        ("dissolve_parliament", {}, "議会の解散"),
    ]
    for kind, payload, text in cases:
        assert text in display.describe_order(kind, payload), kind


def test_stat_labels_for_phase3_4(world):
    for name in (
        "politics.stability",
        "politics.support",
        "politics.seats",
        "politics.halted",
        "labor.mobilized",
        "plan.queue",
        "plan.forced_saving",
        "plan.subsidy",
        "plan.subsidy_unpaid",
        "trade.merchant_gap",
        "cars.households",
        "use.coal",
        "use.grain",
    ):
        label, _ = reports.stat_label(name)
        assert label != name, name


def test_politics_page_shows_the_rebuild_materials(client, world):
    island = _own(client, "market", politics="democracy", head_of_state="republic")
    ext = dict(island.ext)
    ext["politics"] = {**ext.get("politics", {}), "rebuild": {"building_materials": 5300}}
    m.Island.objects.filter(pk=island.pk).update(ext=ext)
    data = client.get(f"/api/islands/{island.id}/politics").json()
    assert data["rebuilding"] and data["rebuild"][0]["quantity"] == 132.5
    html = client.get(f"/islands/{island.id}/politics/").content.decode()
    assert "残りの資材" in html and "建材 132.5" in html


def test_plan_orders_for_official_price_rules_are_accepted(client, world):
    island = _own(client, "planned")
    orders = [
        ("set_official_price_rules", {"band": 10, "held": ["grain", "electricity"]}),
        ("review_official_prices", {"goods": ["coal"]}),  # 行政力 2（足りなければ次のターン）
    ]
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    texts = [o["text"] for o in res.json()["orders"]]
    assert "±10%" in texts[0] and "石炭" in texts[1]
    run_turn(force=True)
    run_turn(force=True)
    island.refresh_from_db()
    state = island.ext["plan"]
    assert state["band"] == 0.1 and state["held"] == ["electricity", "grain"]
    assert state["prices"]["coal"] == state["costs"]["coal"]
    bad = {"orders": [{"kind": "set_official_price_rules", "payload": {"band": 150}}]}
    res = client.put(f"/api/islands/{island.id}/orders", bad, content_type="application/json")
    assert res.status_code == 400


def test_police_station_can_be_built(client, world):
    defs = client.get("/api/defs").json()
    police = [k for k, d in get_game().defs.facilities.items() if d.get("police")]
    assert police and police[0] in defs["facilities"]
    assert defs["facilities"][police[0]]["name"] == "警察署"
