"""画面の暦の表示: ある時点のターンを「箱庭暦N年M月（ターン T）」と書く（ターンの数も残す）。"""

from __future__ import annotations

import json
import re

import pytest

from server.turns.services import run_turn
from server.web import display
from server.world import models as m

pytestmark = pytest.mark.django_db


def test_turn_text_follows_the_world_calendar():
    assert display.turn_text(1) == "箱庭暦1年1月（ターン 1）"
    assert display.turn_text(383) == "箱庭暦32年11月（ターン 383）"
    assert display.turn_text(0) == "箱庭暦の始まる前（ターン 0）"


def test_pages_show_dates_with_turns(client, world):
    run_turn(force=True)
    island = m.Island.objects.order_by("id").first()
    top = client.get("/").content.decode()
    assert "箱庭暦1年1月（ターン 1）" in top
    log = client.get(f"/islands/{island.id}/log/").content.decode()
    assert "<h2>箱庭暦1年1月（ターン 1）</h2>" in log
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert "箱庭暦1年1月（ターン 1）の時点" in page
    for name in ("economy", "fiscal", "finance", "market", "facilities", "trade", "fx", "politics"):
        html = client.get(f"/islands/{island.id}/{name}/").content.decode()
        assert "箱庭暦1年1月（ターン 1）" in html, name


def test_pages_carry_calendar_settings_for_scripts(client, world):
    """画面の JavaScript（js/calendar.js）が読む暦の設定。1年のターン数は dates.py の値。"""
    html = client.get("/").content.decode()
    found = re.search(
        r'<script id="hakoniwa-calendar" type="application/json">(.*?)</script>', html
    )
    assert found
    settings = json.loads(found.group(1))
    assert settings == display.calendar_settings()
    assert settings["name"] == "箱庭暦" and settings["first_year"] == 1
    assert settings["turns_per_year"] == 12
    assert settings["with_turn"] == "{date}（ターン {turn}）"
