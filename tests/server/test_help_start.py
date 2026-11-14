"""手引きの入門（/help/start/）と、画面ごとの「この画面の手引き」のリンク。"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from server.game import get_game
from server.web import display
from server.web.context import SCREEN_HELP
from server.web.views import STATUS_HELP, status_help_name


@pytest.mark.django_db
def test_start_guide_links_both_ways_and_names_the_statuses(client, world):
    """入門と詳しい仕組みは互いにリンクし、施設の状態の言葉は画面と同じ名前で出る。"""
    start = client.get("/help/start/").content.decode()
    general = client.get("/help/").content.decode()
    assert 'href="/help/"' in start and 'href="/help/start/"' in general
    for key, _text in STATUS_HELP:
        assert status_help_name(key) in start
    # 作る量を 0 まで下げて止まったときの言葉も、画面と同じ名前で出す（UI-84）
    assert display.message("stop_target") == "生産停止中（買い注文不足）"
    assert display.message("stop_target") in start
    assert "工業団地・オフィス街の指定" in start  # 市場・混合経済の最初の命令
    assert 'href="/help/start/"' in client.get("/").content.decode()  # 見出しの「手引き」は入門へ


@pytest.mark.django_db
def test_start_guide_links_point_at_real_sections(client, world):
    """入門の「詳しく」と、画面ごとの手引きのリンクは、手引きにある節を指す。"""
    pages = {name: client.get(reverse(name)).content.decode() for name in ("help", "help_start")}
    targets = re.findall(r'href="/help/#([a-z-]+)"', pages["help_start"])
    targets = [("help", a) for a in targets] + [t for t in SCREEN_HELP.values() if t[1]]
    for page, anchor in targets:
        assert f'id="{anchor}"' in pages[page], (page, anchor)


@pytest.mark.django_db
def test_screens_show_their_help_link(client, world):
    """画面の上に、その画面の節へのリンクが出る（手引きの画面には出さない）。"""
    assert (
        'class="screen-help"><a href="/help/#statistics"'
        in client.get("/rankings/").content.decode()
    )
    assert "screen-help" not in client.get("/help/").content.decode()


@pytest.mark.django_db
def test_mcp_guide_reads_the_start_guide(client, world):
    from server.web import mcp

    assert "start" in mcp.TOOLS_BY_NAME["guide"].properties["which"]["enum"]


@pytest.mark.django_db
def test_start_guide_says_facility_art_shows_grade_and_size(client, world):
    """入門の地図の行（#368 の絵）: 施設の絵が段階・作り方・規模で変わることを書く。"""
    start = client.get("/help/start/").content.decode()
    assert "施設の絵は、農場の段階（手作業・機械化・近代化）と作物" in start


@pytest.mark.django_db
def test_guides_follow_the_seven_entries_of_the_island_screens(client, world):
    """手引き（UI-71）: 画面の見取りは 7 つの入口と命令の列の帯で並べ、上のメニューの名前は
    今の名前（公電・世界）で書く。"""
    start = client.get("/help/start/").content.decode()
    general = client.get("/help/").content.decode()
    hakoniwa = client.get("/help/hakoniwa/").content.decode()
    for entry in ("ホーム", "地図", "施設", "資源・市場", "貿易", "経済", "国", "命令の列"):
        assert f"<tr><th>{entry}</th>" in start, entry
    assert "ホーム・地図・施設・「資源・市場」・貿易・経済・国の7つの入口" in general
    for page in (start, general, hakoniwa):
        assert "上の「外交」" not in page and "メニューの「外交」" not in page
        assert "島の画面の上" not in page


@pytest.mark.django_db
def test_guides_say_orders_go_one_or_two_at_a_time_and_queue_only_for_absence(client, world):
    """入門の「結果を見てから手を打つ」と、命令を max_queue 件まで並べられることが食い違わない
    （ふだんは 1〜2 手ずつ、何日か見に来られないときだけ前もって並べる）。"""
    n = get_game().config.get("orders.max_queue")
    start = client.get("/help/start/").content.decode()
    general = client.get("/help/").content.decode()
    assert f"命令の列には {n} 件まで前もって並べられますが" in start
    assert "何日か見に来られないとき" in start
    assert f"列には {n} 件まで前もって並べられます" in general
