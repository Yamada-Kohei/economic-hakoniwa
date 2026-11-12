"""島の画面の入口（UI-71）: 7 つの入口と中のタブ、ホームと地図の分け方、ヘッダーの 6 つ。"""

from __future__ import annotations

import re

import pytest

from server.web import island_nav
from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db

ENTRIES = ["ホーム", "地図", "施設", "資源・市場", "貿易", "経済", "国"]


def _entries(html: str) -> list[str]:
    nav = re.search(r'<nav class="island-nav".*?</nav>', html, re.S).group(0)
    return re.findall(r"<a [^>]*>([^<]+)</a>", nav)


def _current(html: str, cls: str) -> str | None:
    nav = re.search(rf'<nav class="{cls}".*?</nav>', html, re.S)
    if nav is None:
        return None
    found = re.search(r'aria-current="page">([^<]+)</a>', nav.group(0))
    return found.group(1) if found else None


def test_seven_entries_and_tabs(client, world):
    island = _own_island(client)
    pages = {
        f"/islands/{island.id}/home/": ("ホーム", None),
        f"/islands/{island.id}/": ("地図", None),
        f"/islands/{island.id}/chain/": ("施設", "産業の鎖"),
        f"/islands/{island.id}/market/": ("資源・市場", None),
        f"/islands/{island.id}/fx/": ("貿易", "為替"),
        f"/islands/{island.id}/finance/": ("経済", "金融"),
        f"/islands/{island.id}/military/": ("国", "軍"),
    }
    for url, (entry, tab) in pages.items():
        html = client.get(url).content.decode()
        assert _entries(html) == ENTRIES, url
        assert _current(html, "island-nav") == entry, url
        assert _current(html, "island-subnav") == tab, url
    # 国の入口は外交と軍が先頭。日誌は持ち主だけ
    html = client.get(f"/islands/{island.id}/diplomacy/").content.decode()
    tabs = re.search(r'<nav class="island-subnav".*?</nav>', html, re.S).group(0)
    names = re.findall(r"<a [^>]*>([^<]+)</a>", tabs)
    assert names[:2] == ["外交の状態", "軍"] and "日誌" in names
    other = m.Island.objects.exclude(pk=island.id).order_by("id").first()
    html = client.get(f"/islands/{other.id}/diplomacy/").content.decode()
    assert "日誌" not in re.search(r'<nav class="island-subnav".*?</nav>', html, re.S).group(0)


def test_home_and_map_are_split(client, world):
    island = _own_island(client)
    home = client.get(f"/islands/{island.id}/home/").content.decode()
    assert "国勢" in home and 'id="recent-changes"' in home and 'id="island-map"' not in home
    assert f'href="/islands/{island.id}/orders/"' in home  # 命令のページ（UI-74）
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="island-map"' in page and 'id="orders"' in page and "国勢" not in page


def test_header_has_six_links_and_world_tabs(client, world):
    island = _own_island(client)
    html = client.get("/world/").content.decode()
    header = re.search(r'<header class="site-header">.*?</header>', html, re.S).group(0)
    assert f'href="/islands/{island.id}/home/">自国</a>' in header
    for label in ("世界", "公電", "掲示板", "手引き", "アカウント"):
        assert label in header
    for gone in ("順位", "世界の近況", "国際会議場"):
        assert f">{gone}</a>" not in header
    assert _current(html, "island-subnav world-nav") == "世界マップ"
    html = client.get("/rankings/").content.decode()
    assert _current(html, "island-subnav world-nav") == "順位"


def test_nav_skips_plan_and_diary_when_absent():
    groups = dict(island_nav.GROUPS)
    assert [g for g, _ in island_nav.GROUPS] == ENTRIES
    assert groups["国"][0][0] == "diplomacy"
