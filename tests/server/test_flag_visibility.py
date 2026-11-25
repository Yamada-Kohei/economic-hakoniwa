"""案5の先行分: ランキング画面と外交画面の国名の横に国旗を出す。

旗の画像は既存の /islands/<id>/flag（上げた PNG・なければ仮旗）を使う。外交の部品
（hall/war_list.html・hall/treaty_details.html）は国際会議場と共用なので、旗は外交画面から
読み込んだときだけ出し、会議場の画面は変えない。
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.turns.services import run_turn
from server.world import models as m
from tests.server.test_trade import _trading_world

pytestmark = pytest.mark.django_db


def _flag_img(island_id: int) -> str:
    return f'<img class="table-flag" src="/islands/{island_id}/flag"'


def _players(world):
    """サンプルの島 3 つに持ち主を付ける（test_diplomacy と同じ段取り）。"""
    out = []
    for island in m.Island.objects.order_by("id"):
        user = User.objects.create_user(f"p{island.id}", password="<redacted>")
        island.owner = user
        island.regime = {**island.regime, "politics": "authoritarian"}
        island.save()
        client = Client()
        client.force_login(user)
        out.append((client, island))
    return out


def _orders(client, island, *orders):
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders], "confirm": True}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content
    return res.json()


def test_rankings_page_shows_flags_next_to_country_names(client, world):
    own, second, third = _trading_world(client)
    html = client.get("/rankings/").content.decode()
    for island in (own, second, third):
        assert _flag_img(island.id) in html


def test_diplomacy_page_shows_flags_in_wars_and_treaties(world):
    (ca, a), (cb, b), _ = _players(world)
    _orders(
        ca,
        a,
        ("declare_war", {"target": b.id}),
        ("propose_treaty", {"partner": b.id, "clauses": [{"type": "non_aggression"}]}),
    )
    run_turn(force=True)
    html = ca.get(f"/islands/{a.id}/diplomacy/").content.decode()
    # 戦争の当事国と、条約の当事国の両方に旗が出る
    assert _flag_img(a.id) in html and _flag_img(b.id) in html


def test_hall_page_does_not_show_flags(world):
    (ca, a), (cb, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id}))
    run_turn(force=True)
    html = Client().get("/hall/").content.decode()
    assert a.name in html and "table-flag" not in html
