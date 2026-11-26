"""倭国さんの画面の案 4 点（#34 の 5、台帳 P2）: 国名の下の文字列の国勢への統合、
ホームの国勢は今月の数字だけ、作り替え中の状態欄の折り返し、値段の規制の置き場所。"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def _stats(island: m.Island) -> None:
    for name, value in (("population", 12000), ("population.births", 30), ("gdp", 5000)):
        m.StatRecord.objects.create(island=island, turn=1, name=name, value=value)


def test_home_moves_headline_into_summary_table(client, world):
    """国名の下の 1 行はホームでは国勢の表に寄り、地図では今までどおり見出しに出る。"""
    island = _own_island(client)
    island.resources = ["coal"]
    island.save()
    _stats(island)
    home = client.get(f"/islands/{island.id}/home/").content.decode()
    # 国勢の表の行として出る（持ち主・地下資源・時点は見出しの日付）
    assert "<th>持ち主</th>" in home and "<th>地下資源</th>" in home
    assert '<h2>国勢 <small class="heading-date">' in home
    # 見出しの下の 1 行はホームには無い
    header = home.split('<div class="island-header">', 1)[1].split("</div>", 1)[0]
    assert '<p class="muted">' not in header
    # 地図では見出しの 1 行が残る
    page = client.get(f"/islands/{island.id}/").content.decode()
    header = page.split('<div class="island-header">', 1)[1].split("</div>", 1)[0]
    assert '<p class="muted">' in header and "地下資源" in header


def test_home_summary_shows_only_this_months_numbers(client, world):
    """ホームの国勢は今月の数字だけ。全指標は経済の画面の「国勢の一覧」に出る。"""
    island = _own_island(client)
    _stats(island)
    home = client.get(f"/islands/{island.id}/home/").content.decode()
    assert "<th>人口</th>" in home and "<th>出生数（1ターン）</th>" not in home
    economy = client.get(f"/islands/{island.id}/economy/").content.decode()
    assert 'id="summary"' in economy and "<th>出生数（1ターン）</th>" in economy
    # ホームから国勢の一覧へ行ける
    assert f'href="/islands/{island.id}/economy/#summary"' in home


def test_plan_tab_lives_under_market_group(world):
    """計画／値段の規制のタブは、入口「資源・市場」の下に出る（国には出ない）。"""
    from server.web import island_nav

    island = m.Island.objects.order_by("id").first()
    shown = island_nav.nav(island.id, "plan", plan_label="値段の規制", mine=True)
    assert [t["name"] for t in shown["tabs"]] == ["市場", "値段の規制"]
    assert shown["groups"][3]["current"]  # 資源・市場
    shown = island_nav.nav(island.id, "politics", plan_label="値段の規制", mine=True)
    assert "値段の規制" not in [t["name"] for t in shown["tabs"]]


def test_transition_note_wraps_in_facility_table():
    """作付けの切り替えの文は、施設の一覧の状態欄で折り返す（表が横に伸びない）。"""
    css = Path("server/static/css/site.css").read_text(encoding="utf-8")
    assert "table.facilities .transition-note" in css
    block = css.split("table.facilities .transition-note", 1)[1].split("}", 1)[0]
    assert "white-space: normal" in block and "max-width: 30em" in block
    template = Path("server/templates/web/facilities.html").read_text(encoding="utf-8")
    assert 'class="transition-note">{{ f.transition }}' in template
