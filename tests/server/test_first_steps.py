"""建国直後の「次にすること」と、稼働準備中の施設を止まりと分けて数えること（W-219）。

プレイヤーの声「起動して何をやれば良いのか分かりにくい」から。建国直後の島の画面は、長い手順の
説明と「止まっている施設が N か所」（中身は全部、最初の更新で動き出す稼働準備中）を出していた。
"""

from __future__ import annotations

import json

from hakoniwa_core import facilities as fac
from hakoniwa_core.state import ZONE_INDUSTRIAL
from server.accounts import keys
from server.orders.models import Order
from server.turns.services import run_turn
from server.web import overview
from tests.server.test_ai_players import _keyed, _players, _set_turn, _tool


def test_fresh_island_says_what_to_do_first_and_does_not_call_starting_facilities_stopped(
    client, world
):
    """建国直後: 稼働準備中は止まりに数えず、「次にすること」に初めの更新・工業団地・命令の欄を
    出す。島の画面・国の要約（JSON・MCP）で同じ中身。ほかの国の人には出さない。"""
    (alice, bob, _), (a, _, _) = _players(world)
    starting = a.facilities.filter(built=True, status=fac.STATUS_STARTING).count()
    assert starting > 0
    _, token = keys.issue(alice)
    data = _keyed(token).get(f"/api/islands/{a.id}/summary").json()
    assert data["facilities"]["starting_count"] == starting
    assert data["facilities"]["stopped_count"] == 0
    assert not any("止まっている施設" in x for x in data["alerts"])
    steps = data["next_steps"]
    assert [s["key"] for s in steps] == ["first_update", "industrial_zone", "orders"]
    assert f"{starting} か所" in steps[0]["text"]
    assert steps[1]["url"].endswith("#island-map") and steps[2]["url"].endswith("#orders")
    text, error = _tool(_keyed(token), "summary")
    assert not error and json.loads(text)["next_steps"] == steps

    client.force_login(alice)
    page = client.get(f"/islands/{a.id}/").content.decode()
    assert "次にすること" in page and "工業団地の指定" in page
    assert "止まっている施設が" not in page

    _, other = keys.issue(bob)
    assert "next_steps" not in _keyed(other).get(f"/api/islands/{a.id}/summary").json()
    client.force_login(bob)
    assert "次にすること" not in client.get(f"/islands/{a.id}/").content.decode()


def test_next_steps_follow_the_island_and_stop_after_the_first_year(world):
    """工業団地を指定し命令を入れると、その行は消える。更新の後は初めの更新の行も消える。
    建国から GUIDE_TURNS を過ぎたら何も出さない。"""
    (alice, _, _), (a, _, _) = _players(world)
    tile = a.tiles.filter(zone="").first()
    tile.zone = ZONE_INDUSTRIAL
    tile.save(update_fields=["zone"])
    Order.objects.create(island=a, seq=1, kind="rename_island", payload={"name": "一手目の国"})
    _, token = keys.issue(alice)
    keys_now = [
        s["key"] for s in _keyed(token).get(f"/api/islands/{a.id}/summary").json()["next_steps"]
    ]
    assert keys_now == ["first_update"]
    run_turn(force=True)
    keys_after = [
        s["key"] for s in _keyed(token).get(f"/api/islands/{a.id}/summary").json()["next_steps"]
    ]
    assert "first_update" not in keys_after and len(keys_after) <= overview.NEXT_STEPS_MAX
    world.refresh_from_db()
    _set_turn(world, (a.ext or {}).get("founded_turn", 0) + overview.GUIDE_TURNS)
    assert _keyed(token).get(f"/api/islands/{a.id}/summary").json()["next_steps"] == []
