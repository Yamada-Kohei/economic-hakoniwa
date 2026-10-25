"""島の種類ごとに全画面と全 API を開く試験（やり直しの報告の I3、W-34。段6 B4で部隊・艦隊・占領・
反乱軍のいる世界を足した）。

世界は tests/server/busy_world.py が作る（建国直後・人のいない島・銀行のない島・計画経済・戦争中・
占領・反乱軍・革命のあとの選び直し待ち）。島ごとに、持ち主・ほかの国の人・ログインしていない人の
3 人で、島の下にある画面と読むだけの API を全部開き、500 や壊れたページが出ないことを確かめる。
新しい画面を足すと、下の名前の一覧に入れるまで `test_every_island_url_is_listed` が外れる。
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import get_resolver, reverse

from server.world import models as m
from tests.server.busy_world import PASSWORD, build

pytestmark = pytest.mark.django_db

# 島の番号だけで開ける画面と API（名前 → 追加の引数）。
ISLAND_PAGES = [
    "island",
    "turn_log",
    "island_economy",
    "island_market",
    "island_chain",
    "island_facilities",
    "island_fiscal",
    "island_finance",
    "island_trade",
    "island_fx",
    "island_politics",
    "island_plan",
    "island_diplomacy",
    "island_military",
    "island_country",
    "diary",
]
ISLAND_APIS = [
    "api_island_map",
    "api_island_economy",
    "api_island_economy_overview",
    "api_island_finance",
    "api_island_trade",
    "api_island_fx",
    "api_island_politics",
    "api_island_plan",
    "api_island_military",
    "api_island_diplomacy",
    "api_island_country",
    "api_island_summary",
    "api_island_changes",
    "api_island_log",
    "api_island_facilities",
    "api_island_fiscal",
    "api_island_market_table",
    "api_island_chain",
    "api_country_diary",
]
# 引数の要る API（下の `_extra_urls` が開く）
ISLAND_APIS_WITH_ARGS = ["api_tile", "api_island_market", "api_facility_book"]
# 画像など、中身を JSON や HTML で確かめない島の下の道
ISLAND_IMAGES = ["island_flag", "monument_image", "api_country_flag", "api_monument_image"]
# 書き込む道（GET では開かない）
ISLAND_WRITES = [
    "api_country_edit",
    "api_country_note",
    "api_country_note_hide",
    "api_orders",
    "api_order_check",
]
# 持ち主ではない人には断ってよい道（この名前だけは 403 と 404 を許す）
OWNER_ONLY = {"diary", "api_country_diary", "api_island_chain", "api_facility_book"}

KNOWN = set(ISLAND_PAGES + ISLAND_APIS + ISLAND_APIS_WITH_ARGS + ISLAND_IMAGES + ISLAND_WRITES)


def _names_under_islands() -> set[str]:
    out: set[str] = set()

    def walk(patterns, prefix=""):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif p.name and "<int:island_id>" in prefix + str(p.pattern):
                out.add(p.name)

    walk(get_resolver().url_patterns)
    return out


def test_every_island_url_is_listed():
    """島の下に新しい画面や API を足したら、この試験の一覧（開く・画像・書き込む）に入れる。"""
    assert _names_under_islands() - KNOWN == set(), "一覧にない島の道があります"
    assert KNOWN - _names_under_islands() == set(), "一覧にあるのに道がない名前があります"


def _clients(kinds: dict[str, int]) -> dict[str, dict[str, Client]]:
    """島の種類 → {持ち主, ほかの人, ログインなし} の Client。"""
    out: dict[str, dict[str, Client]] = {}
    for kind in kinds:
        owner = Client()
        assert owner.login(username=f"owner-{kind}", password=PASSWORD)
        stranger = Client()
        other = "owner-rival" if kind != "rival" else "owner-player"
        assert stranger.login(username=other, password=PASSWORD)
        out[kind] = {"owner": owner, "stranger": stranger, "anonymous": Client()}
    return out


def _extra_urls(island: m.Island) -> list[tuple[str, str]]:
    """引数の要る読む API の URL（名前, URL）。首都・占領されたマス・反乱軍のマスのマスを開く。"""
    urls = []
    middle = island.size // 2
    cells = {(0, 0), (middle, middle)}
    for tile in island.tiles.all():
        if (tile.ext or {}).get("occupied"):
            cells.add((tile.x, tile.y))
    for cell in sorted(cells)[:6]:
        urls.append(("api_tile", reverse("api_tile", args=[island.id, cell[0], cell[1]])))
    urls.append(("api_island_market", reverse("api_island_market", args=[island.id, "food"])))
    first = island.facilities.order_by("number").first()
    if first is not None:
        urls.append(
            ("api_facility_book", reverse("api_facility_book", args=[island.id, first.number]))
        )
    return urls


def _check(res, name: str, viewer: str) -> str | None:
    """応答の問題（なければ None）。"""
    if res.status_code >= 500:
        return f"{res.status_code}"
    if res.status_code != 200:
        if viewer != "owner" and name in OWNER_ONLY and res.status_code in (401, 403, 404):
            return None
        if name in ISLAND_IMAGES and res.status_code == 404:
            return None
        if name in ("api_tile", "api_facility_book") and res.status_code == 404:
            return None  # 外のマスや見えない施設は 404 でよい
        return f"status {res.status_code}"
    if name in ISLAND_IMAGES:
        return None
    body = res.content.decode("utf-8", "replace")
    if "json" in res["Content-Type"]:
        try:
            json.loads(body)
        except ValueError as e:
            return f"壊れた JSON: {e}"
        return None
    for leak in ("{{", "{%", "Traceback", "Exception Value", "TemplateSyntaxError"):
        if leak in body:
            return f"ページに {leak!r} が出ている"
    return None


def _open_everything(kinds: dict[str, int]) -> tuple[int, list[str]]:
    clients = _clients(kinds)
    problems: list[str] = []
    opened = 0
    for kind, island_id in kinds.items():
        island = m.Island.objects.get(pk=island_id)
        urls = [(n, reverse(n, args=[island_id])) for n in ISLAND_PAGES + ISLAND_APIS]
        urls += [("island_flag", reverse("island_flag", args=[island_id]))]
        urls += _extra_urls(island)
        for viewer, client in clients[kind].items():
            for name, url in urls:
                opened += 1
                problem = _check(client.get(url), name, viewer)
                if problem:
                    problems.append(f"{kind}（{island_id}）/ {viewer} / {name} {url}: {problem}")
    return opened, problems


def test_every_island_screen_opens_for_every_kind_of_island_and_viewer():
    kinds = build()
    opened, problems = _open_everything(kinds)
    assert opened > 500
    assert not problems, "\n" + "\n".join(problems)


def test_the_screens_still_open_while_the_war_and_the_civil_war_go_on():
    """部隊・艦隊・占領・反乱軍のいる世界を、数ターン進めながら開き直す。"""
    from server.turns.services import run_turn

    kinds = build(warm_turns=1, war_turns=1)
    for _ in range(3):
        run_turn(force=True)
        _, problems = _open_everything(kinds)
        assert not problems, "\n" + "\n".join(problems)


# --- ログインしないと見られない所（段6 B4、反映したあとに確かめられなかった 3 つ） -------------


def test_the_diary_opens_for_its_owner_and_is_a_404_for_others():
    kinds = build(warm_turns=1, war_turns=1)
    clients = _clients(kinds)
    for kind, island_id in kinds.items():
        url = reverse("diary", args=[island_id])
        mine = clients[kind]["owner"].get(url)
        assert mine.status_code == 200, (kind, mine.status_code)
        assert "日誌" in mine.content.decode()
        for viewer in ("stranger", "anonymous"):
            assert clients[kind][viewer].get(url).status_code == 404, (kind, viewer)


def test_the_letter_notice_is_on_every_page_of_a_logged_in_owner_only():
    kinds = build(warm_turns=1, war_turns=1)
    clients = _clients(kinds)
    for kind, island_id in kinds.items():
        for url in (reverse("island", args=[island_id]), reverse("world")):
            mine = clients[kind]["owner"].get(url).content.decode()
            assert 'id="letter-notice"' in mine and f'data-island="{island_id}"' in mine
            for viewer in ("stranger", "anonymous"):
                page = clients[kind][viewer].get(url).content.decode()
                assert (
                    f'data-island="{island_id}"' not in page.split('id="letter-notice"')[-1][:200]
                )
    anonymous = Client().get(reverse("world")).content.decode()
    assert 'id="letter-notice"' not in anonymous


def test_the_admin_signup_record_page_has_the_extra_signups_field():
    from django.contrib.auth.models import User

    from server.accounts.models import SignupRecord

    user = User.objects.create_superuser("admin", password=PASSWORD)
    player = User.objects.create_user("someone", password=PASSWORD)
    SignupRecord.objects.create(user=player, ip_hash="x" * 64)
    client = Client()
    client.force_login(user)
    listing = client.get("/admin/accounts/signuprecord/").content.decode()
    assert "追加で登録できる人数" in listing
    record = SignupRecord.objects.get()
    detail = client.get(f"/admin/accounts/signuprecord/{record.pk}/change/")
    assert detail.status_code == 200 and "追加で登録できる人数" in detail.content.decode()
