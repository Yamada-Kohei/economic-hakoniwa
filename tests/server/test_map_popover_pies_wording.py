"""島の画面のマスの情報の窓・統計の円グラフ・表示の言葉（2026-10-19 の改善事項）。

- マスの情報は、押したマスのそばの小さな窓（ポップオーバー）に出し、右上の × で閉じる（UI-34）。
- 全体を分けた割合を見る統計は円グラフでも出す（UI-35）。
- 表示の言葉は統計の正式な言い方にそろえる（UI-36）。
"""

from __future__ import annotations

import json
import re

from hakoniwa_core import facilities as fac
from server.accounts import keys
from server.game import get_game
from server.web import display, reports
from tests.server.test_ai_players import _keyed
from tests.server.test_ai_players import played as played  # noqa: F401  （試験の部品を使い回す）


def test_island_page_shows_tile_info_in_a_popover(client, played):
    """マスの情報は地図の中の窓（閉じるボタン付き）に入り、横の欄はない。地図で選んで出す命令の
    入口は、窓の外（地図の上）に持ち主にだけ出す。"""
    _, (alice, _, _), (a, b, _) = played
    client.force_login(alice)
    page = client.get(f"/islands/{a.id}/").content.decode()
    popover = page[page.index('id="tile-popover"') :]
    assert popover.index('id="tile-panel"') < popover.index("</section>")
    assert "data-close" in popover and "hidden" in popover[: popover.index(">")]
    assert '<aside id="tile-panel"' not in page
    assert 'id="mark-tools"' in page
    other = client.get(f"/islands/{b.id}/").content.decode()
    assert 'id="tile-popover"' in other and 'id="mark-tools"' not in other


def test_report_pages_offer_pie_charts(client, played):
    """経済の画面は階級ごとの人口、財政の画面は歳入と歳出の内訳、施設の画面は産業ごとの従業者数を
    円グラフで出す。産業ごとの従業者数は誰にでも見せ、API（と MCP の report）にも同じ値がある。"""
    _, (alice, bob, _), (a, _, _) = played
    client.force_login(bob)  # ほかの国の人
    economy = client.get(f"/islands/{a.id}/economy/").content.decode()
    pie = re.search(r'data-pie="([^"]+)"', economy).group(1).split(",")
    assert sorted(pie) == sorted(f"layer.{layer}.persons" for layer in reports.LAYER_NAMES)
    fiscal = client.get(f"/islands/{a.id}/fiscal/").content.decode()
    assert 'data-pie="fiscal.in.*"' in fiscal and 'data-pie="spent.*"' in fiscal

    page = client.get(f"/islands/{a.id}/facilities/").content.decode()
    assert 'data-slices="industry-workers"' in page
    shown = json.loads(re.search(r'id="industry-workers"[^>]*>(.*?)</script>', page).group(1))
    _, token = keys.issue(bob)
    data = _keyed(token).get(f"/api/islands/{a.id}/facilities").json()
    assert data["industry_workers"] == shown and shown
    housing = fac.housing_kind(get_game().config)
    working = sum(f.workers for f in a.facilities.filter(built=True).exclude(kind=housing))
    assert sum(r["value"] for r in shown) == working
    assert [r["value"] for r in shown] == sorted((r["value"] for r in shown), reverse=True)


def test_labels_use_formal_statistical_terms():
    """家計の層は階級の名前、働ける人の全体は労働力人口、人口の増減と移民は統計の言葉。流れの量は
    「（1ターン）」と書き、「このターン」とは書かない。"""
    assert set(reports.LAYER_NAMES.values()) == {"労働者階級", "資本家階級", "技術者", "失業者"}
    expected = {
        "labor": "労働力人口",
        "employed": "就業者",
        "subsistence": "自給農家",
        "unemployment": "完全失業率",
        "population.births": "出生数（1ターン）",
        "population.deaths": "死亡数（餓死を除く。1ターン）",
        "population.starved": "餓死者数（1ターン）",
        "services.workers": "商業・サービス業就業者数",
        "migration.abroad": "在外自国民",
        "migration.foreign_born": "在留外国人",
    }
    for name, label in expected.items():
        assert reports.stat_label(name)[0] == label
    titles = [c["title"] for c in reports.economy_charts()]
    assert "国際人口移動（1ターン）" in titles and "出生数と死亡数（1ターン）" in titles
    labels = [label for _, label, _ in [*display.SUMMARY_STATS, *reports._EXTRA_STATS]]
    labels += [suffix for suffix, _ in reports.LAYER_FIELDS.values()]
    assert not [label for label in labels if "このターン" in label]
