"""生産調整中のわけ（W-250）と、生産停止中（買い注文不足）の状態の分け方（UI-84・UI-93）。

施設の状態と、市場の段が残した数（売れ行き・見込み・費用の下限・在庫）と、直近の市場の記録と約定を
1島の DB に直に置き、画面（地図の窓・施設の一覧・帳簿）・JSON API・MCP の道具・国の要約に同じ
わけが出るかを見る。ターンは回さない。
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from hakoniwa_core import facilities as fac
from hakoniwa_core.founding import found_island
from hakoniwa_core.modules.market import COST_FLOOR
from server.accounts import keys
from server.economy.models import MarketRecord, Trade
from server.game import get_game
from server.web import display, overview, slowdown
from server.world import models as m
from server.world.repository import create_island, facility_from_row
from tests.core.test_founding import plan_for
from tests.server.test_ai_players import _keyed, _tool

pytestmark = pytest.mark.django_db

OFFERED, SOLD, PRICED_OUT = 10_000, 4_000, 3_000  # 1000 分の 1 単位
EXPECTED, HELD = 4_000, 20_000


def _maker(state):
    """産出物が1つで、電力を作らない施設（目標の見直しが市場の売れ行きで決まる）。"""
    game = get_game()
    power = game.config.get("power.good")
    for f in state.facilities:
        if not f.built or fac.is_housing(game.config, f) or f.kind not in game.defs.facilities:
            continue
        outputs = {g: q for g, q in fac.outputs_of(game.defs, f).items() if q > 0}
        if len(outputs) == 1 and power not in outputs:
            return f, next(iter(outputs))
    raise AssertionError("産出物が1つの施設がない")


@pytest.fixture
def slowed():
    """作る量を下げて作り続けている施設（安い輸入と、費用に届かない付け値で売れ残った場面）。"""
    cache.clear()
    game = get_game()
    world = m.World.objects.create(turn=2, seed=1)
    state = found_island(game.defs, game.config, plan_for(game, economy="mixed"), 0, 0, 0)
    f, good = _maker(state)
    price = state.economy.prices[good]
    f.status, f.operating_rate = "target", 400
    f.economy.update(
        target=400,
        sales={good: [OFFERED, SOLD, PRICED_OUT]},
        expected={good: EXPECTED},
        **{COST_FLOOR: {good: price * 2}},  # 市場の値段は費用の半分
    )
    f.stock = {good: [[1, HELD]]}
    user = User.objects.create_user("slow-owner", password="a-long-pass-123")
    island = create_island(world, state, owner=user)
    MarketRecord.objects.create(
        island=island, turn=1, good=good, price=price, volume=9_000, demand=9_000, supply=15_000
    )
    Trade.objects.create(
        island=island,
        turn=1,
        good=good,
        buyer="hh:general",
        seller="merchant",
        quantity=5_000,
        amount=price * 5,
    )
    row = island.facilities.get(number=f.id)
    _, token = keys.issue(user, "slowdown-test")
    browser = Client()
    browser.force_login(user)
    return island, row, good, browser, _keyed(token)


def test_slowdown_reasons_follow_the_target_rule(slowed):
    """わけは目標の見直しの決まりに合わせる: 付け値が費用に届かない・安い輸入・売れ残り・在庫。"""
    island, row, good, _, _ = slowed
    why = slowdown.explain(slowdown.Facts(island), facility_from_row(row))
    assert why is not None
    assert why["stopped"] is False and why["target"] == 40 and why["good"] == good
    kinds = [r["kind"] for r in why["reasons"]]
    assert kinds == ["below_cost", "imports", "unsold", "stock"]
    assert why["text"] == why["reasons"][0]["text"]
    assert "買い手の付け値が費用に届きません" in why["text"]
    texts = {r["kind"]: r["text"] for r in why["reasons"]}
    assert "海外の商人" in texts["imports"]
    held = display.amount(HELD, good)
    assert f"約 {HELD / EXPECTED:.1f} ターン分" in texts["stock"] and f"{held:,g}" in texts["stock"]
    # 打てる手は主なわけ（費用・輸入）に合わせる
    assert "生産補助" in why["hint"] and "二国間契約の申し込み" in why["hint"]
    assert "海外の商人の売り値は島の側では変えられません" in why["hint"]
    assert texts["unsold"].startswith("そのほかに ")


def test_slowdown_without_cost_gap_or_imports(slowed):
    """市場に買えなかった買い手がいて値段が費用以上なら、高い希望価格の売れ残りは売れたと見る
    （目標を下げるわけに出さない）。輸入がなければ輸入のわけも出さない。"""
    island, row, good, _, _ = slowed
    Trade.objects.filter(island=island).delete()
    MarketRecord.objects.filter(island=island).update(demand=12_000)
    row.economy[COST_FLOOR] = {good: 1}
    row.save(update_fields=["economy"])
    why = slowdown.explain(slowdown.Facts(island), facility_from_row(row))
    kinds = [r["kind"] for r in why["reasons"]]
    assert "below_cost" not in kinds and "priced_out" not in kinds and "imports" not in kinds
    assert kinds[-1] == "stock"


def test_price_cap_below_cost_is_the_only_reason(slowed):
    """混合経済で値段の上限が費用より低く、値段が上限に張り付いていれば、売れる見込みを 0 にする
    決まりのわけだけを出し、手は上限の見直しと生産補助にする。"""
    island, row, good, _, _ = slowed
    price = island.economy["prices"][good]
    island.policy = {**island.policy, "price_cap": {good: price}}
    island.save(update_fields=["policy"])
    row.accounting = "self_supporting"
    row.save(update_fields=["accounting"])
    why = slowdown.explain(slowdown.Facts(island), facility_from_row(row))
    assert [r["kind"] for r in why["reasons"]] == ["price_cap"]
    assert "値段の上限（公共料金など）の変更" in why["hint"] and "生産補助" in why["hint"]


def test_stopped_by_sales_is_its_own_state_with_the_stop_mark(slowed):
    """0 まで下げて止まった施設は「生産停止中（買い注文不足）」。作り続けているうちは
    「生産調整中」。止まった方は国の要約の止まった施設（地図の赤い点と同じ数え方）に入る。"""
    island, row, _, _, _ = slowed
    assert display.status_text(facility_from_row(row)) == "生産調整中"
    row.operating_rate = 0
    row.economy["target"] = 0
    row.save(update_fields=["operating_rate", "economy"])
    f = facility_from_row(row)
    assert display.status_text(f) == "生産停止中（買い注文不足）"
    state = overview.facility_state(island, True)
    stopped = next(s for s in state["stopped"] if s["number"] == row.number)
    assert stopped["status_text"] == "生産停止中（買い注文不足）"
    assert stopped["slowdown"]["stopped"] is True
    assert stopped["hint"] == stopped["slowdown"]["hint"]
    assert all(s["number"] != row.number for s in state["slowdown"])
    row.economy["closing"] = 1  # 閉鎖の途中は買い注文不足ではない
    row.save(update_fields=["economy"])
    f = facility_from_row(row)
    assert display.status_text(f) == "生産停止中（閉鎖・差し押さえ）"
    assert slowdown.explain(slowdown.Facts(island), f)["reasons"][0]["kind"] == "closing"


def test_slowdown_matches_screen_json_summary_and_mcp(slowed):
    """地図の窓（マスの API）・施設の一覧と帳簿・国の要約・MCP の道具に同じわけを出す（UI-30）。
    他国の人には出さない。"""
    island, row, good, browser, ai = slowed
    why = slowdown.explain(slowdown.Facts(island), facility_from_row(row))
    tile = ai.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    assert tile["facility"]["slowdown"] == why
    assert tile["facility"]["hint"] == why["hint"]
    page = browser.get(f"/islands/{island.id}/facilities/")
    item = next(r for r in page.context["table"]["rows"] if r["number"] == row.number)
    assert item["slowdown"] == why and item["hint"] == why["hint"]
    html = page.content.decode()
    assert why["text"] in html and why["hint"] in html
    facilities = ai.get(f"/api/islands/{island.id}/facilities").json()
    listed = next(r for r in facilities["table"]["rows"] if r["number"] == row.number)
    assert listed["slowdown"] == why
    book = ai.get(f"/api/islands/{island.id}/facilities/{row.number}/book").json()
    assert book["slowdown"] == why
    summary = ai.get(f"/api/islands/{island.id}/summary").json()
    entry = next(s for s in summary["facilities"]["slowdown"] if s["number"] == row.number)
    assert entry["slowdown"] == why and entry["operating_rate"] == 40
    assert summary["facilities"]["slowdown_count"] >= 1
    for tool, args, expected in (
        ("summary", {}, entry["slowdown"]),
        ("tile", {"x": row.x, "y": row.y}, why),
        ("report", {"page": "facilities"}, why),
    ):
        text, failed = _tool(ai, tool, island=island.id, **args)
        assert not failed, text
        data = json.loads(text)
        if tool == "summary":
            got = next(s for s in data["facilities"]["slowdown"] if s["number"] == row.number)
        elif tool == "tile":
            got = data["facility"]
        else:
            got = next(r for r in data["table"]["rows"] if r["number"] == row.number)
        assert got["slowdown"] == expected
    other = Client()
    tile = other.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    assert "slowdown" not in tile["facility"]
    assert "slowdown" not in overview.facility_state(island, False)
    cache.clear()
