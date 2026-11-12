"""ホームの「今月すべきこと」（UI-76・UI-77）と国のまとめの貿易の 1 行（W-247 (8)）。

もとからある値（止まった施設・品不足・失業率・破綻した銀行・条約の提案・二国間契約・貿易圏・体制の
選び直し・注意・次にすること）を DB に置き、ホーム・国の要約の JSON・MCP の summary に同じ並びで
出ること、急ぐ順・行き先・数の上限、持ち主だけに出すことを確かめる。
"""

from __future__ import annotations

import html
import json

import pytest
from django.core.cache import cache
from django.test import Client

from hakoniwa_core.trade import KEY as TRADE_KEY
from server.accounts import keys
from server.game import get_game
from server.orders.models import Order
from server.web import overview, todo
from server.world import models as m
from tests.server.test_ai_players import _keyed, _players, _set_turn, _tool
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_calls():
    cache.clear()
    yield
    cache.clear()


def _summary(token: str, island: m.Island) -> dict:
    return _keyed(token).get(f"/api/islands/{island.id}/summary").json()


def _mcp(token: str, island: m.Island) -> dict:
    text, error = _tool(_keyed(token), "summary", island=island.id)
    assert not error, text
    return json.loads(text)


def _stop(row: m.Facility) -> None:
    row.built = True
    row.status = "workers"
    row.operating_rate = 0
    row.save()


def _signals(world, island: m.Island, other: m.Island) -> dict:
    """島に、今月すべきことの元になる値を 1 つずつ置く（建国から 1 年を過ぎた島）。"""
    _set_turn(world, overview.GUIDE_TURNS + 5)
    turn = world.turn
    farm = island.facilities.filter(kind="farm").order_by("number").first()
    _stop(farm)
    bank = island.facilities.filter(kind=get_game().config.get("banking.facility")).first()
    bank.economy = {**(bank.economy or {}), "failed": turn}
    bank.save()
    economy = island.economy or {}
    least = get_game().config.get("records.shortage")["turns"]
    # 続いたターン数の多い順。知らせのターン数に届かない財は出さない
    shortage = {"wood": least, "grain": least + 2, "coal": least - 1}
    island.economy = {**economy, "ext": {**economy.get("ext", {}), "shortage": shortage}}
    island.save(update_fields=["economy"])
    _stats(island, turn - 2, {"unemployment": 0.04})
    _stats(island, turn - 1, {"unemployment": 0.04 + todo.UNEMPLOYMENT_RISE})
    treaty = {
        "id": 7,
        "title": "不可侵条約",
        "parties": [other.id, island.id],
        "proposer": other.id,
        "clauses": [{"type": "non_aggression"}],
        "created": turn - 1,
        "state": "proposed",
    }
    contract = {
        "id": 3,
        "seller": other.id,
        "buyer": island.id,
        "proposer": other.id,
        "good": "wood",
        "qty": 50_000,
        "price": 20,
        "currency": other.id,
        "turns": 12,
        "state": "proposed",
        "created": turn - 1,
        "start": 0,
        "end": 0,
        "delivered": 0,
        "paid": 0,
        "short": 0,
    }
    ext = world.ext or {}
    world.ext = {
        **ext,
        "diplomacy": {"treaties": [treaty], "wars": []},
        TRADE_KEY: {**(ext.get(TRADE_KEY) or {}), "contracts": [contract]},
    }
    world.save(update_fields=["ext"])
    return {"farm": farm, "bank": bank, "treaty": treaty, "contract": contract}


def test_each_signal_is_a_row_in_urgency_order_with_a_link_to_act(world):
    """銀行・条約・契約・止まった施設・品不足・失業率の行が、決めた急ぐ順に並び、それぞれ打てる手の
    ある所へ飛ぶ。同じことを言う「注意」の件数の文は、1 件ずつの行に代わる。"""
    (owner, _, _), (island, other, _) = _players(world)
    made = _signals(world, island, other)
    _, token = keys.issue(owner)
    data = _summary(token, island)
    rows = data["todo"]
    kinds = [r["kind"] for r in rows]
    picked = [k for k in kinds if k in todo.RANK]
    assert picked == sorted(picked, key=todo.RANK.__getitem__)  # 急ぐ順
    wanted = ["bank", "treaty", "contract", "stopped", "shortage", "shortage", "unemployment"]
    assert [k for k in kinds if k in set(wanted)] == wanted
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    base = f"/islands/{island.id}"
    farm, bank = made["farm"], made["bank"]
    assert by_kind["bank"][0]["url"] == f"{base}/facilities/{bank.number}/"
    assert "破綻" in by_kind["bank"][0]["text"]
    assert by_kind["treaty"][0]["url"] == f"{base}/diplomacy/#proposals"
    assert "不可侵条約" in by_kind["treaty"][0]["text"]
    assert by_kind["contract"][0]["url"] == f"{base}/trade/#contracts"
    assert other.name in by_kind["contract"][0]["text"]
    assert (
        "木材" in by_kind["contract"][0]["text"]
        and "こちらが買う" in by_kind["contract"][0]["text"]
    )
    assert any(r["url"] == f"{base}/facilities/{farm.number}/" for r in by_kind["stopped"])
    assert [r["url"] for r in by_kind["shortage"]] == [
        f"{base}/market/grain/",
        f"{base}/market/wood/",
    ]
    assert by_kind["unemployment"][0]["url"] == f"{base}/fiscal/"
    assert all(r["link_text"] for r in rows if r["url"])
    # 件数だけの注意（止まった施設・条約の提案）は、今月すべきことでは 1 件ずつの行に代わる
    assert any("止まっている施設が" in a for a in data["alerts"])
    assert any("返事をしていない条約の提案" in a for a in data["alerts"])
    texts = [r["text"] for r in rows]
    assert not any(t.startswith(("止まっている施設が", "返事をしていない条約")) for t in texts)


def test_long_lists_are_capped_and_answered_orders_drop_rows(world):
    """止まった施設は CAP 件までと「ほか N か所」の 1 行。契約・条約・銀行は、返事や処理の命令を
    命令の列に入れたら出さない（条約の件数の注意も出さない）。"""
    (owner, _, _), (island, other, _) = _players(world)
    made = _signals(world, island, other)
    rows = list(island.facilities.exclude(kind__in=["housing", "bank"]).order_by("number"))
    for row in rows[: todo.CAP + 2]:
        _stop(row)
    stopped = overview.facility_state(island, True)["stopped_count"]
    assert stopped > todo.CAP
    _, token = keys.issue(owner)
    lines = [r for r in _summary(token, island)["todo"] if r["kind"] == "stopped"]
    assert len(lines) == todo.CAP + 1
    assert lines[-1]["text"] == f"ほかに止まっている施設が {stopped - todo.CAP} か所あります"
    assert lines[-1]["url"] == f"/islands/{island.id}/facilities/"

    bank = made["bank"]
    for seq, (kind, payload) in enumerate(
        [
            ("accept_contract", {"contract": made["contract"]["id"]}),
            ("refuse_treaty", {"treaty": made["treaty"]["id"]}),
            ("resolve_bank", {"x": bank.x, "y": bank.y, "method": "bailout"}),
        ],
        start=1,
    ):
        Order.objects.create(island=island, seq=seq, kind=kind, payload=payload)
    kinds = {r["kind"] for r in _summary(token, island)["todo"]}
    assert not kinds & {"contract", "treaty", "bank"}


def test_only_the_owner_sees_the_list_and_screen_json_and_mcp_match(world):
    """今月すべきことは持ち主にだけ。ホームの行と国の要約の JSON・MCP の todo は同じ。ほかの人の
    ホームは今までどおり注意を出す。"""
    (owner, visitor, _), (island, other, _) = _players(world)
    _signals(world, island, other)
    _, token = keys.issue(owner)
    rows = _summary(token, island)["todo"]
    assert rows and _mcp(token, island)["todo"] == rows
    browser = Client()
    browser.force_login(owner)
    page = browser.get(f"/islands/{island.id}/home/").content.decode()
    assert 'id="todo"' in page and "今月すべきこと" in page
    shown = page.split('id="todo"')[1].split("</section>")[0]
    for r in rows:
        assert html.escape(r["text"]) in shown
        assert f'href="{r["url"]}"' in shown
    # ほかの人と、ログインしていない人には出さない
    _, other_token = keys.issue(visitor)
    assert "todo" not in _summary(other_token, island)
    assert "todo" not in _mcp(other_token, island)
    assert "todo" not in Client().get(f"/api/islands/{island.id}/summary").json()
    for client in (Client(), _logged_in(visitor)):
        page = client.get(f"/islands/{island.id}/home/").content.decode()
        assert "今月すべきこと" not in page and 'id="todo"' not in page
        assert "止まっている施設が" in page  # 誰にでも出す注意は今までどおり


def _logged_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_rare_but_important_things_are_surfaced_when_needed(world):
    """UI-77: 体制の選び直し待ち・代表をしている貿易圏への加盟の申し込み・どの圏にも入っていない
    ことは、もとの値が要ると言うときだけ出す。"""
    (owner, _, third_owner), (island, other, third) = _players(world)
    _set_turn(world, overview.GUIDE_TURNS + 5)
    island.ext = {
        **(island.ext or {}),
        "politics": {
            **((island.ext or {}).get("politics") or {}),
            "reselect": {"from": "democracy"},
            "transition": {"since": world.turn - 1, "until": world.turn + 6},
        },
    }
    island.save(update_fields=["ext"])
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
    ext = world.ext or {}
    world.ext = {**ext, TRADE_KEY: {**(ext.get(TRADE_KEY) or {}), "blocs": [bloc]}}
    world.save(update_fields=["ext"])
    _, token = keys.issue(owner)
    rows = {r["kind"]: r for r in _summary(token, island)["todo"]}
    assert rows["regime"]["url"] == f"/islands/{island.id}/military/"
    assert "体制を選び直します" in rows["regime"]["text"]
    assert rows["bloc_applicants"]["url"] == f"/islands/{island.id}/trade/#blocs"
    assert "南の海" in rows["bloc_applicants"]["text"] and "1 件" in rows["bloc_applicants"]["text"]
    assert "bloc" not in rows  # 圏に入っている
    _, third_token = keys.issue(third_owner)
    third_rows = {r["kind"]: r for r in _summary(third_token, third)["todo"]}
    assert third_rows["bloc"]["url"] == f"/islands/{third.id}/trade/#other-blocs"
    assert "regime" not in third_rows and "bloc_applicants" not in third_rows
    # 体制の選び直し待ちは飢え・戦争の次、圏の案内はいちばん下の方
    assert todo.RANK["regime"] < todo.RANK["bank"] < todo.RANK["bloc"]


def test_first_year_steps_replace_the_matching_alert(world):
    """建国から 1 年は「次にすること」の行が今月すべきことに入り、同じ種類の注意の行に代わる。
    ホームには「次にすること」の言葉も出る。"""
    (owner, _, _), (island, _, _) = _players(world)
    _stats(island, world.turn, {"food.ratio": 0.5})
    _, token = keys.issue(owner)
    data = _summary(token, island)
    assert any(s["key"] == "food" for s in data["next_steps"])
    food = [r for r in data["todo"] if r["kind"] == "food"]
    step = next(s for s in data["next_steps"] if s["key"] == "food")
    assert [r["text"] for r in food] == [step["text"]] and food[0]["url"] == step["url"]
    assert any(a.startswith("食料が足りていません") for a in data["alerts"])
    assert data["todo"][0]["kind"] == "first_update"
    page = _logged_in(owner).get(f"/islands/{island.id}/home/").content.decode()
    assert "次にすること" in page


def test_trade_line_is_on_the_home_and_in_the_summary(world):
    """国のまとめに貿易の 1 行（輸出・輸入と、そのうち海外の商人に払った額）。誰にでも出す。"""
    (_, _, _), (island, _, _) = _players(world)
    _stats(
        island,
        world.turn,
        {"population": 1000, "trade.exports": 1200, "trade.imports": 3400, "trade.merchant": 900},
    )
    public = Client()
    line = public.get(f"/api/islands/{island.id}/summary").json()["trade"]
    assert line["exports"] == 1200 and line["imports"] == 3400 and line["merchant"] == 900
    assert line["text"] == (
        f"輸出 {line['exports_text']}・輸入 {line['imports_text']}"
        f"（うち海外の商人に払った額 {line['merchant_text']}）"
    )
    assert "3,400" in line["imports_text"]
    page = public.get(f"/islands/{island.id}/home/").content.decode()
    assert html.escape(line["text"]) in page and "貿易（1ターン）" in page
    # 地図の画面は変えない
    assert "貿易（1ターン）" not in public.get(f"/islands/{island.id}/").content.decode()
