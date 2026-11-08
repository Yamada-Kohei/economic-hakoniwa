"""画面と知らせ（UI-62〜UI-69）の境界と、画面・API・MCP・要約の一致。"""

from __future__ import annotations

import json
import re

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
    assert f"/islands/{island.id}/chain/" in Client().get(f"/islands/{island.id}/").content.decode()
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
    assert notice in browser.get(f"/islands/{island.id}/").content.decode()
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
    assert notice in public.get(f"/islands/{island.id}/").content.decode()
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
