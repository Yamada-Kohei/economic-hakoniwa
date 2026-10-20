"""フェーズ4-1 ④: 記念碑の碑文・自作の記念碑の絵・国の公開ページと JSON API。

核の条件と段は tests/core/test_monuments.py。ここでは、建てたときの記録から碑文を作ること、
足した文が碑文に付くこと、自作の記念碑の絵（国旗と同じ守りと隠し方）、画面と API の見え方を
確かめる。
"""

from __future__ import annotations

import base64
import json

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from server.country import png, services
from server.country.models import MonumentImage
from server.turns.services import run_turn
from server.web import monuments
from server.world import models as m
from tests.server.test_country import make_png

pytestmark = pytest.mark.django_db


@pytest.fixture
def player(world):
    m.World.objects.update(turn=80)
    island = m.Island.objects.order_by("id").first()
    user = User.objects.create_user("p1", password="a-long-pass-123")
    island.owner = user
    island.ext = {**island.ext, "founded_turn": 1}
    island.save(update_fields=["owner", "ext"])
    client = Client()
    client.force_login(user)
    return client, island


def put_monument(island, kind, number, facts=None, note="", built=True):
    tile = (
        m.Tile.objects.filter(island=island, terrain="plain")
        .exclude(zone="office")
        .order_by("x", "y")
        .first()
    )
    return m.Facility.objects.create(
        island=island,
        number=number,
        kind=kind,
        x=tile.x + number % 7,
        y=tile.y,
        built=built,
        ext={"monument": {"facts": facts or {}, "turn": 70, "note": note}},
    )


def test_inscriptions_are_made_from_the_record_made_when_built(player):
    _, island = player
    cases = {
        "monument_monolith": ({}, "石の碑"),
        "monument_founding": ({"founded": 1, "leader": "賢王"}, "賢王の下で建国"),
        "monument_peace": ({"turns": 130}, "130 ターン（10 年）"),
        "monument_battle": ({"country": "隣国", "turn": 9, "dead": 12}, "隣国との戦争"),
        "monument_cenotaph": ({"cause": "hunger", "dead": 1500}, "飢えで亡くなった 1500 人"),
        "monument_leader": ({"leader": "大統領"}, "大統領"),
        "monument_tower": ({}, "繁栄"),
        "monument_ledger": ({"turn": 1, "kind": "capital_injection", "amount": 1234}, "出資"),
        "monument_ledger_none": ({}, "借方と貸方は、いつも等しい"),
    }
    for kind, (facts, word) in cases.items():
        text = monuments.auto_text(island, kind.removesuffix("_none"), facts)
        assert word in text, (kind, text)
        assert "{" not in text


def test_verification_reads_the_stats_of_month_fifty(player):
    _, island = player
    assert "人口" not in monuments.auto_text(island, "monument_verification", {})
    for name, value in (("population", 4321), ("employed", 2000), ("gdp", 987654)):
        m.StatRecord.objects.create(island=island, turn=51, name=name, value=value)
    text = monuments.auto_text(island, "monument_verification", {})
    assert "Codex" in text and "建国 50 か月目" in text
    assert "4,321 人" in text and "2,000 人" in text and "987,654" in text


def test_worker_statue_names_the_title(player):
    _, island = player
    text = monuments.auto_text(island, "monument_worker", {"industry": "agriculture", "turn": 40})
    assert "称号" in text and "{" not in text


def test_tile_info_and_country_page_show_the_inscription_and_the_note(player):
    client, island = player
    f = put_monument(
        island, "monument_founding", 9001, {"founded": 1, "leader": "賢王"}, "海を見て\n待つ"
    )
    info = client.get(f"/api/islands/{island.id}/tiles/{f.x},{f.y}").json()["facility"]
    assert "賢王" in info["monument"]["text"] and info["monument"]["note"] == ["海を見て", "待つ"]
    data = client.get(f"/api/islands/{island.id}/country").json()
    assert [x["number"] for x in data["monuments"]] == [9001]
    page = client.get(f"/islands/{island.id}/country/").content.decode()
    assert "賢王" in page and "海を見て" in page and "<script" not in page.split("monuments")[1]
    assert client.get(f"/api/islands/{island.id}/summary").json()["country"]["monuments"] == 1


def test_a_note_is_escaped_in_the_page(player):
    client, island = player
    put_monument(island, "monument_monolith", 9002, note="<b>x</b>")
    page = client.get(f"/islands/{island.id}/country/").content.decode()
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page


def test_unfinished_monument_is_listed_as_under_construction(player):
    client, island = player
    put_monument(island, "monument_monolith", 9003, built=False)
    row = client.get(f"/api/islands/{island.id}/country").json()["monuments"][0]
    assert row["built"] is False
    assert client.get(f"/api/islands/{island.id}/summary").json()["country"]["monuments"] == 0


def test_custom_monument_image_is_png_or_provisional_and_can_be_hidden(player):
    client, island = player
    put_monument(island, "monument_custom", 9004)
    url = f"/islands/{island.id}/monuments/9004/image"
    res = Client().get(url)
    assert res["Content-Type"] == "image/svg+xml" and b"<svg" in res.content
    assert "sandbox" in res["Content-Security-Policy"]
    services.set_monument_image(island, 9004, make_png())
    res = Client().get(url)
    assert res["Content-Type"] == "image/png" and res.content.startswith(png.SIGNATURE)
    MonumentImage.objects.update(hidden=True)  # 管理者が隠した
    assert Client().get(url)["Content-Type"] == "image/svg+xml"
    # 同じ画像を上げ直しても隠したまま。別の画像なら外れる
    services.set_monument_image(island, 9004, make_png())
    assert MonumentImage.objects.get().hidden
    services.set_monument_image(island, 9004, make_png(40, 40))
    assert not MonumentImage.objects.get().hidden
    assert Client().get(f"/islands/{island.id}/monuments/9999/image").status_code == 404


def test_only_a_custom_monument_takes_an_image(player):
    _, island = player
    put_monument(island, "monument_monolith", 9005)
    with pytest.raises(services.CountryError):
        services.set_monument_image(island, 9005, make_png())
    with pytest.raises(services.CountryError):
        services.set_monument_image(island, 123456, make_png())
    with pytest.raises(services.CountryError):
        services.set_monument_image(island, "x", make_png())


def test_monument_image_api_and_page_form_need_the_owner(player):
    client, island = player
    put_monument(island, "monument_custom", 9006)
    path = f"/api/islands/{island.id}/monuments/9006/image"
    body = json.dumps({"png_base64": base64.b64encode(make_png()).decode()})
    res = client.put(path, body, content_type="application/json")
    assert res.status_code == 200, res.content
    assert res.json()["monuments"][0]["image"].endswith("/9006/image")
    other = Client()
    assert other.put(path, body, content_type="application/json").status_code == 403
    upload = SimpleUploadedFile("a.png", make_png(48, 48), content_type="image/png")
    res = client.post(
        f"/islands/{island.id}/country/",
        {"action": "monument_image", "number": "9006", "image": upload},
    )
    assert res.status_code == 302 and MonumentImage.objects.get().width == 48
    res = client.post(
        f"/islands/{island.id}/country/", {"action": "monument_image_clear", "number": "9006"}
    )
    assert res.status_code == 302 and not MonumentImage.objects.exists()
    assert client.delete(path).status_code == 200
    bad = client.put(path, json.dumps({"png_base64": "!!"}), content_type="application/json")
    assert bad.status_code == 400


def test_build_note_reaches_the_inscription_end_to_end(player):
    client, island = player
    tile = (
        m.Tile.objects.filter(island=island, terrain="plain")
        .exclude(zone="office")
        .order_by("x", "y")
        .first()
    )
    check = f"/api/islands/{island.id}/orders/check"
    ok = {"facility": "monument_monolith", "x": tile.x, "y": tile.y, "note": "一行目\n二行目"}

    def post(payload):
        body = json.dumps({"kind": "build", "payload": payload})
        return client.post(check, body, content_type="application/json").json()

    assert post(ok)["ok"] is True
    assert post({**ok, "note": "あ" * 41})["ok"] is False  # 1 行は 40 字まで
    assert post({**ok, "note": "a\nb\nc"})["ok"] is False  # 2 行まで
    # 命令を置いてターンを進めると、足した文つきの記念碑が建つ（工事中として国のページに載る）
    body = json.dumps({"orders": [{"kind": "build", "payload": ok}]})
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content
    run_turn(m.World.objects.get())
    row = client.get(f"/api/islands/{island.id}/country").json()["monuments"][0]
    assert row["kind"] == "monument_monolith" and row["note"] == ["一行目", "二行目"]
