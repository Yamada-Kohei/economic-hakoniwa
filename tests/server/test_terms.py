"""利用規約の画面（W-215）と、手引きの賞・記念碑の数（W-211）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import scale
from server.web import views


@pytest.mark.django_db
def test_terms_page_tells_that_the_admin_may_read_letters(client, world):
    page = client.get("/terms/").content.decode()
    assert "利用規約" in page
    assert "管理者は公電" in page and "読むことがあります" in page
    assert "30 日" in page  # accounts.remember_days
    assert 'href="/terms/"' in client.get("/").content.decode()  # どの画面の下にもある


@pytest.mark.django_db
def test_signup_page_links_the_terms(client, world):
    page = client.get("/accounts/signup/").content.decode()
    assert 'href="/terms/"' in page and "利用規約に同意して登録する" in page


@pytest.mark.django_db
def test_award_numbers_follow_the_world_scale(client, world, monkeypatch):
    """賞と慰霊碑の人数は世界の大きさの倍を掛けた値（倍 20 の世界で 5 万と書かない）。"""
    page = client.get("/help/").content.decode()
    assert "人口が 5 万・10 万・20 万 人に届いたとき" in page
    assert "1,000 人以上が亡くなった" in page
    from django.conf import settings

    from hakoniwa_core.game import load_game

    monkeypatch.delenv(scale.ENV, raising=False)
    help_data = views._awards_help(load_game(settings.HAKONIWA_GAME_CONFIG, 20))
    assert help_data["prosperity"] == "100 万・200 万・400 万"
    assert help_data["cenotaph_dead"] == "2 万"
