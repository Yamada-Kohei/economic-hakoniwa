"""フェーズ4-1 ③: 国の公開ページ（国旗・紹介文・国ごとの紀年・年表・来訪者の一言）、国旗の PNG の
検査、放置された国の管理画面の扱い。"""

from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from server.accounts import keys
from server.country import png, services
from server.country.models import Flag, Note, Profile
from server.turns.models import TurnLog
from server.turns.services import run_turn
from server.world import models as m

pytestmark = pytest.mark.django_db


def make_png(width=32, height=20, *, extra=b"", interlace=0, bad_data=False, color=2, depth=8):
    """検査に通る小さな PNG（ベタ塗り）を作る。extra は IDAT の前に足すチャンク。"""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    row = (width * channels * depth + 7) // 8 + 1
    raw = (b"\x00" + b"\x7f" * (row - 1)) * height
    if bad_data:
        raw = raw[:-3]
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return (
        png.SIGNATURE
        + chunk(b"IHDR", ihdr)
        + extra
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def chunk_of(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


# --- PNG の検査 -------------------------------------------------------------------------------


def test_png_accepts_a_valid_png_and_reports_its_size():
    data = make_png(48, 32)
    cleaned, width, height = png.clean(data, 100 * 1024, 512)
    assert (width, height) == (48, 32)
    assert cleaned == data  # 余計なチャンクがなければそのまま


@pytest.mark.parametrize("color,depth", [(0, 8), (0, 1), (2, 16), (4, 8), (6, 8)])
def test_png_accepts_other_color_types(color, depth):
    png.clean(make_png(color=color, depth=depth), 100 * 1024, 512)


def test_png_strips_text_and_exif_chunks():
    extra = chunk_of(b"tEXt", b"Comment\x00<script>alert(1)</script>") + chunk_of(b"eXIf", b"x")
    cleaned, _, _ = png.clean(make_png(extra=extra), 100 * 1024, 512)
    assert b"tEXt" not in cleaned and b"script" not in cleaned and b"eXIf" not in cleaned
    assert cleaned == make_png()


@pytest.mark.parametrize(
    "data,word",
    [
        (b"GIF89a" + b"x" * 40, "PNG の画像ではありません"),
        (make_png()[:-5], "途中で切れています"),
        (make_png(interlace=1), "インターレース"),
        (make_png(width=8, height=8), "16〜512"),
        (make_png(width=600, height=20), "16〜512"),
        (make_png(bad_data=True), "長さ"),
    ],
)
def test_png_rejects_bad_files(data, word):
    with pytest.raises(png.PngError) as e:
        png.clean(data, 100 * 1024, 512)
    assert word in e.value.text


def test_png_rejects_a_broken_crc_and_oversize_files():
    data = bytearray(make_png())
    data[20] ^= 0xFF  # IHDR の中身を壊す
    with pytest.raises(png.PngError, match="CRC"):
        png.clean(bytes(data), 100 * 1024, 512)
    with pytest.raises(png.PngError, match="KB まで"):
        png.clean(make_png(), 10, 512)


def test_png_does_not_inflate_past_the_declared_size():
    """宣言した大きさよりずっと大きく開ける圧縮（圧縮爆弾）は、開ききる前に断る。"""
    width = height = 16
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    bomb = zlib.compress(b"\x00" * 50_000_000)
    data = (
        png.SIGNATURE + chunk_of(b"IHDR", ihdr) + chunk_of(b"IDAT", bomb) + chunk_of(b"IEND", b"")
    )
    with pytest.raises(png.PngError, match="長さ"):
        png.clean(data, 100 * 1024, 512)


def test_provisional_flag_is_stable_and_differs_between_countries():
    a, b = png.provisional_flag(1, "ア国"), png.provisional_flag(2, "イ国")
    assert a == png.provisional_flag(1, "ア国")
    assert a.startswith("<svg") and "script" not in a
    assert a != b


# --- 紀年 -------------------------------------------------------------------------------------


@pytest.fixture
def players(world):
    """持ち主のいる島 2 つ。世界は 20 ターン目（国ごとの紀年が始まっているころ）。"""
    m.World.objects.update(turn=20)
    out = []
    for island in m.Island.objects.order_by("id")[:2]:
        user = User.objects.create_user(f"p{island.id}", password="a-long-pass-123")
        island.owner = user
        island.save(update_fields=["owner"])
        client = Client()
        client.force_login(user)
        out.append((client, island))
    return out


def test_eras_format_dates_per_country(players):
    _, island = players[0]
    island.ext = {**island.ext, "founded_turn": 5}
    island.save(update_fields=["ext"])
    world_text = services.date_text(island, None, 14)
    assert world_text.endswith("2年2月")  # 世界の暦では 2 年目の 2 月（ターン 14）
    founding = services.set_era(island, "founding")
    assert services.date_text(island, founding, 5) == "建国紀元1年5月"
    assert services.date_text(island, founding, 17) == "建国紀元2年5月"
    assert services.date_text(island, founding, 2) == services.date_text(island, None, 2)  # 建国前
    regnal = services.set_era(island, "regnal", "天明")
    assert services.date_text(island, regnal, 5) == "天明元年5月"
    assert services.date_text(island, regnal, 17) == "天明2年5月"
    island.world.turn = 30
    after = services.new_era(island, "大正")
    assert [e["name"] for e in after.eras] == ["天明", "大正"]
    assert services.date_text(island, after, 29) == "天明3年5月"
    assert services.date_text(island, after, 30) == "大正元年6月"
    assert services.date_text(island, after, 42) == "大正2年6月"


def test_era_rules(players):
    _, island = players[0]
    with pytest.raises(services.CountryError, match="元号の名前"):
        services.set_era(island, "regnal")
    with pytest.raises(services.CountryError, match="8 字まで"):
        services.set_era(island, "regnal", "あ" * 9)
    with pytest.raises(services.CountryError, match="どれか"):
        services.set_era(island, "lunar")
    services.set_era(island, "regnal", "天明")
    island.world.turn = 9
    with pytest.raises(services.CountryError, match="同じ元号"):
        services.new_era(island, "天明")
    services.set_era(island, "world")  # 戻しても元号の一覧は残り、また元号にできる
    assert services.set_era(island, "regnal").eras[0]["name"] == "天明"


# --- 画面と API -------------------------------------------------------------------------------


def test_country_page_shows_the_public_profile(players):
    (ca, a), (cb, b) = players
    a.names = {**a.names, "leader": "山田太郎"}
    a.save(update_fields=["names"])
    services.set_intro(a, "海に面した小さな国です。")
    html = Client().get(f"/islands/{a.id}/country/").content.decode()
    assert a.name in html and "山田太郎" in html and "海に面した小さな国です。" in html
    assert f"/islands/{a.id}/flag" in html and "仮の旗です" in html
    assert 'name="intro"' not in html and 'name="text"' not in html  # 見るだけの人には入力欄なし
    assert 'name="text"' in cb.get(f"/islands/{a.id}/country/").content.decode()
    mine = ca.get(f"/islands/{a.id}/country/").content.decode()
    assert 'name="intro"' in mine and 'name="flag"' in mine and 'name="text"' not in mine


def test_owner_edits_through_the_page(players):
    (ca, a), (cb, b) = players
    url = f"/islands/{a.id}/country/"
    res = ca.post(url, {"action": "intro", "intro": "  由来と国是  "})
    assert res.status_code == 302
    assert Profile.objects.get(island=a).intro == "由来と国是"
    ca.post(url, {"action": "era", "kind": "regnal", "name": "天明"})
    assert Profile.objects.get(island=a).era_kind == "regnal"
    ca.post(url, {"action": "new_era", "name": "大正"})
    assert [e["name"] for e in Profile.objects.get(island=a).eras] == ["天明", "大正"]
    upload = SimpleUploadedFile("f.png", make_png(), content_type="image/png")
    assert ca.post(url, {"action": "flag", "flag": upload}).status_code == 302
    assert Flag.objects.get(island=a).width == 32
    page = ca.get(url).content.decode()
    assert "仮の旗です" not in page and "国旗を外す" in page
    # 持ち主でない人は書き換えられない
    res = cb.post(url, {"action": "intro", "intro": "のっとり"})
    assert res.status_code == 200 and "持ち主だけ" in res.content.decode()
    assert Profile.objects.get(island=a).intro == "由来と国是"
    bad = SimpleUploadedFile("f.png", b"not a png", content_type="image/png")
    res = ca.post(url, {"action": "flag", "flag": bad})
    assert res.status_code == 200 and "PNG の画像ではありません" in res.content.decode()
    ca.post(url, {"action": "flag_clear"})
    assert not Flag.objects.filter(island=a).exists()


def test_flag_image_is_the_png_or_the_provisional_svg(players):
    (_, a), _ = players
    res = Client().get(f"/islands/{a.id}/flag")
    assert res["Content-Type"] == "image/svg+xml" and b"<svg" in res.content
    assert (
        "sandbox" in res["Content-Security-Policy"] and res["X-Content-Type-Options"] == "nosniff"
    )
    services.set_flag(a, make_png())
    res = Client().get(f"/islands/{a.id}/flag")
    assert res["Content-Type"] == "image/png" and res.content.startswith(png.SIGNATURE)
    Flag.objects.filter(island=a).update(hidden=True)  # 管理者が隠すと仮の旗
    assert Client().get(f"/islands/{a.id}/flag")["Content-Type"] == "image/svg+xml"
    info = Client().get(f"/api/islands/{a.id}/country").json()["flag"]
    assert info["provisional"] and "hidden_by_admin" not in info


def test_api_edit_flag_and_notes(players):
    (ca, a), (cb, b) = players
    put = lambda c, path, body: c.put(path, json.dumps(body), content_type="application/json")  # noqa: E731
    res = put(ca, f"/api/islands/{a.id}/country/edit", {"intro": "紹介", "new_era": "天明"})
    assert res.status_code == 200, res.content
    data = res.json()
    assert data["intro"] == "紹介" and data["era"]["kind"] == "regnal"
    assert data["era"]["now"].startswith("天明") and data["era"]["choices"]
    assert put(cb, f"/api/islands/{a.id}/country/edit", {"intro": "x"}).status_code == 403
    assert put(ca, f"/api/islands/{a.id}/country/edit", {}).status_code == 400
    assert put(ca, f"/api/islands/{a.id}/country/edit", {"intro": "あ" * 601}).status_code == 400

    body = {"png_base64": base64.b64encode(make_png()).decode()}
    res = put(ca, f"/api/islands/{a.id}/flag", body)
    assert res.status_code == 200 and not res.json()["flag"]["provisional"]
    res = ca.put(f"/api/islands/{a.id}/flag", make_png(40, 40), content_type="image/png")
    assert res.status_code == 200 and Flag.objects.get(island=a).width == 40
    assert put(ca, f"/api/islands/{a.id}/flag", {"png_base64": "###"}).status_code == 400
    assert put(cb, f"/api/islands/{a.id}/flag", body).status_code == 403
    assert ca.delete(f"/api/islands/{a.id}/flag").json()["flag"]["provisional"]

    post = lambda c, path, body: c.post(path, json.dumps(body), content_type="application/json")  # noqa: E731
    res = post(cb, f"/api/islands/{a.id}/country/notes", {"text": "いい国ですね"})
    assert res.status_code == 201
    assert post(ca, f"/api/islands/{a.id}/country/notes", {"text": "自分に"}).status_code == 400
    assert post(Client(), f"/api/islands/{a.id}/country/notes", {"text": "x"}).status_code == 403
    assert post(cb, f"/api/islands/{a.id}/country/notes", {"text": "あ" * 81}).status_code == 400
    shown = Client().get(f"/api/islands/{a.id}/country").json()
    assert [n["text"] for n in shown["notes"]] == ["いい国ですね"]
    assert shown["notes"][0]["from"]["id"] == b.id and "can_note" in shown
    assert "choices" not in shown["era"]  # 持ち主でない人には選択肢を出さない


def test_notes_are_rate_limited_and_hidden_by_admin(players, settings):
    settings.HAKONIWA_COUNTRY_NOTES_PER_DAY = 2
    (ca, a), (cb, b) = players
    user = b.owner
    for i in range(2):
        services.post_note(user, a, f"一言{i}")
    with pytest.raises(services.CountryError) as e:
        services.post_note(user, a, "多すぎ")
    assert e.value.status == 429
    Note.objects.filter(text="一言0").update(hidden=True)
    texts = [n["text"] for n in Client().get(f"/api/islands/{a.id}/country").json()["notes"]]
    assert texts == ["一言1"]


def test_hidden_intro_is_not_shown_but_the_owner_is_told(players):
    (ca, a), _ = players
    services.set_intro(a, "不適切な文")
    Profile.objects.filter(island=a).update(intro_hidden=True)
    assert Client().get(f"/api/islands/{a.id}/country").json()["intro"] == ""
    mine = ca.get(f"/api/islands/{a.id}/country").json()
    assert mine["intro_hidden_by_admin"] and mine["intro_edit"] == "不適切な文"
    assert "管理者が非表示にしました" in ca.get(f"/islands/{a.id}/country/").content.decode()
    services.set_intro(a, "書き直し")  # 書き直すと、また見える
    assert Client().get(f"/api/islands/{a.id}/country").json()["intro"] == "書き直し"


def test_timeline_uses_the_countrys_era_and_includes_hall_events(players):
    (_, a), (_, b) = players
    run_turn(force=True)
    TurnLog.objects.create(
        island=a, turn=2, kind="island_renamed", args={"old": "ア", "new": "新ア"}
    )
    TurnLog.objects.create(
        island=a,
        turn=3,
        kind="war_declared",
        args={"hall": True, "a": a.name, "b": b.name, "turn": 4, "text": ""},
    )
    TurnLog.objects.create(island=a, turn=3, kind="order_failed", args={})  # 年表に載らない種類
    services.set_era(a, "regnal", "天明")
    data = Client().get(f"/api/islands/{a.id}/country").json()
    kinds = [row["text"] for row in data["timeline"]]
    assert any("宣戦" in t for t in kinds)
    assert all(row["date"].startswith("天明") for row in data["timeline"] if row["turn"] >= 1)
    assert any(line.startswith("* 天明") for line in data["timeline_text"].splitlines())
    assert not any("order_failed" in t for t in kinds)
    # 持ち主でない人の国の年表は、その国の紀年で書く
    other = Client().get(f"/api/islands/{b.id}/country").json()
    assert not any(row["date"].startswith("天明") for row in other["timeline"])


def test_summary_and_changes_carry_the_country_page(players):
    (ca, a), (cb, b) = players
    services.set_intro(a, "紹介")
    services.post_note(b.owner, a, "こんにちは")
    summary = ca.get(f"/api/islands/{a.id}/summary").json()
    assert summary["country"]["intro"] == "紹介" and summary["country"]["notes"] == 1
    assert summary["country"]["flag"]["provisional"]
    changes = ca.get(f"/api/islands/{a.id}/changes?since=19").json()
    assert [n["text"] for n in changes["visitor_notes"]] == ["こんにちは"]
    assert "visitor_notes" not in cb.get(f"/api/islands/{a.id}/changes?since=19").json()


def test_mcp_tools_for_the_country_page(players, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 10_000
    (ca, a), (cb, b) = players
    _, token = keys.issue(a.owner, "test")
    ai = Client(HTTP_AUTHORIZATION=f"Bearer {token}")

    def call(name, **arguments):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        message["params"] = {"name": name, "arguments": arguments}
        res = ai.post("/mcp", json.dumps(message), content_type="application/json")
        result = res.json()["result"]
        return result["content"][0]["text"], result["isError"]

    text, error = call("country_set", intro="AI の国です", era={"kind": "regnal", "name": "令和"})
    assert not error, text
    assert json.loads(text)["era"]["kind"] == "regnal"
    text, error = call("flag_set", png_base64=base64.b64encode(make_png()).decode())
    assert not error and not json.loads(text)["flag"]["provisional"]
    text, error = call("flag_set", clear=True)
    assert not error and json.loads(text)["flag"]["provisional"]
    text, error = call("flag_set", png_base64="bm90IGEgcG5n")
    assert error and "PNG" in text
    text, error = call("country_note", island=b.id, text="よろしく")
    assert not error, text
    assert Note.objects.get(island=b).text == "よろしく"
    text, _ = call("report", page="country")
    assert json.loads(text)["intro"] == "AI の国です"


# --- 管理画面 ---------------------------------------------------------------------------------


def test_admin_hides_flags_intros_and_notes(admin_client, players):
    (_, a), (_, b) = players
    flag = services.set_flag(a, make_png())
    services.set_intro(a, "不適切")
    note = services.post_note(b.owner, a, "不適切な一言")
    for model, path, obj in (
        ("flag", "country/flag", flag),
        ("profile", "country/profile", Profile.objects.get(island=a)),
        ("note", "country/note", note),
    ):
        assert admin_client.get(f"/admin/{path}/").status_code == 200
        res = admin_client.post(f"/admin/{path}/", {"action": "hide", "_selected_action": [obj.pk]})
        assert res.status_code == 302, model
    flag.refresh_from_db()
    note.refresh_from_db()
    assert flag.hidden and note.hidden and Profile.objects.get(island=a).intro_hidden
    assert admin_client.get(f"/admin/country/flag/{flag.pk}/change/").status_code == 200


def test_admin_handles_abandoned_countries(admin_client, players, world):
    (_, a), (_, b) = players
    page = admin_client.get("/admin/world/island/")
    assert page.status_code == 200
    html = page.content.decode()
    assert "命令が途絶えたターン数" in html and "最後のログイン" in html
    # 命令を最後に実行したターンからの数（実行済みの命令がなければ建国から）
    a.orders.create(seq=1, kind="set_tax", status="done", executed_turn=world.turn)
    world.turn += 7
    world.save(update_fields=["turn"])
    from server.world.admin import IslandAdmin

    admin = IslandAdmin(m.Island, admin_site=None)
    rows = {i.id: i for i in admin.get_queryset(_Request())}
    assert admin.idle_turns(rows[a.id]) == 7
    assert admin.idle_turns(rows[b.id]) >= 7
    # 休止中にする・休止を解く・持ち主を外す（島は消えない）
    action = {"action": "make_inactive", "_selected_action": [a.id]}
    admin_client.post("/admin/world/island/", action)
    a.refresh_from_db()
    assert a.inactive
    admin_client.post("/admin/world/island/", {**action, "action": "make_active"})
    a.refresh_from_db()
    assert not a.inactive
    admin_client.post("/admin/world/island/", {**action, "action": "release_owner"})
    a.refresh_from_db()
    assert a.owner is None and m.Island.objects.filter(pk=a.pk).exists()


class _Request:
    user = None
    GET: dict = {}


# --- 見直しで足した場面 ---------------------------------------------------------------------


def test_png_bomb_is_refused_without_inflating_it():
    import tracemalloc

    ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    bomb = zlib.compress(b"\x00" * 60_000_000)
    data = (
        png.SIGNATURE + chunk_of(b"IHDR", ihdr) + chunk_of(b"IDAT", bomb) + chunk_of(b"IEND", b"")
    )
    tracemalloc.start()
    with pytest.raises(png.PngError):
        png.clean(data, 200 * 1024, 512)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 5_000_000  # 宣言した大きさ（16×16 の数 KB）を超えては開かない


def test_png_is_rebuilt_in_a_fixed_order_with_one_idat():
    base = make_png(24, 24)
    idat_body = zlib.compress((b"\x00" + b"\x10" * (24 * 3)) * 24)
    half = len(idat_body) // 2
    ihdr = struct.pack(">IIBBBBB", 24, 24, 8, 2, 0, 0, 0)
    split = (
        png.SIGNATURE
        + chunk_of(b"IHDR", ihdr)
        + chunk_of(b"IDAT", idat_body[:half])
        + chunk_of(b"tEXt", b"a\x00b")
        + chunk_of(b"IDAT", idat_body[half:])
        + chunk_of(b"IEND", b"")
    )
    cleaned, _, _ = png.clean(split, 100 * 1024, 512)
    assert cleaned.count(b"IDAT") == 1 and b"tEXt" not in cleaned
    assert png.clean(cleaned, 100 * 1024, 512)[0] == cleaned  # 作り直したものは、また通る
    assert base.startswith(png.SIGNATURE)


def test_png_rejects_trailing_data_bad_filters_and_bad_palettes():
    ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    rows = (b"\x00" + b"\x7f" * 48) * 16

    def build(idat: bytes, *extra: bytes) -> bytes:
        parts = [chunk_of(b"IHDR", ihdr), *extra, chunk_of(b"IDAT", idat), chunk_of(b"IEND", b"")]
        return png.SIGNATURE + b"".join(parts)

    with pytest.raises(png.PngError, match="長さ"):
        png.clean(build(zlib.compress(rows) + b"garbage"), 100 * 1024, 512)
    bad_filter = (b"\x09" + b"\x7f" * 48) + rows[49:]
    with pytest.raises(png.PngError, match="壊れて"):
        png.clean(build(zlib.compress(bad_filter)), 100 * 1024, 512)
    with pytest.raises(png.PngError, match="色の表"):
        png.clean(build(zlib.compress(rows), chunk_of(b"PLTE", b"\x00\x00")), 100 * 1024, 512)


def test_edit_is_all_or_nothing(players):
    (ca, a), _ = players
    services.set_era(a, "regnal", "天明")
    services.set_intro(a, "元の文")
    body = {"intro": "新しい文", "new_era": "天明"}  # 同じ元号なので断られる
    res = ca.put(
        f"/api/islands/{a.id}/country/edit", json.dumps(body), content_type="application/json"
    )
    assert res.status_code == 400
    assert Profile.objects.get(island=a).intro == "元の文"


def test_hidden_stays_hidden_until_the_content_changes(players):
    (_, a), _ = players
    data = make_png()
    services.set_flag(a, data)
    services.set_intro(a, "不適切な文")
    Flag.objects.filter(island=a).update(hidden=True)
    Profile.objects.filter(island=a).update(intro_hidden=True)
    assert services.set_flag(a, data).hidden  # 同じ画像を上げ直しても隠したまま
    assert services.set_intro(a, "不適切な文").intro_hidden
    assert not services.set_flag(a, make_png(40, 40)).hidden  # 変えれば、また見てもらえる
    assert not services.set_intro(a, "直した文").intro_hidden


def test_text_rules(players):
    (_, a), (_, b) = players
    long_lines = "\r\n".join(["あ" * 50] * 11)  # CRLF のまま数えると 610 字、LF なら 599 字
    assert services.set_intro(a, long_lines).intro.count("\r") == 0
    for bad in ("\x00x", "a‮b", "a\x1bb", "\ud800"):
        with pytest.raises(services.CountryError, match="使えない文字"):
            services.set_intro(a, bad)
        with pytest.raises(services.CountryError, match="使えない文字"):
            services.post_note(b.owner, a, bad)
    with pytest.raises(services.CountryError, match="空白"):
        services.set_era(a, "regnal", "天 明")
    with pytest.raises(services.CountryError, match="使えない文字"):
        services.set_era(a, "regnal", "天\n明")


def test_new_era_without_an_era_starts_from_the_founding(players):
    (_, a), _ = players
    a.ext = {**a.ext, "founded_turn": 5}
    a.save(update_fields=["ext"])
    profile = services.new_era(a, "天明")
    assert profile.eras == [{"name": "天明", "start_turn": 5}]
    assert services.date_text(a, profile, 5) == "天明元年5月"
    services.new_era(a, "大正")
    assert len(Profile.objects.get(island=a).eras) == 2


def test_notes_per_target_limit_and_owner_can_hide(players):
    (ca, a), (cb, b) = players
    for i in range(services.NOTES_PER_TARGET):
        services.post_note(b.owner, a, f"一言{i}")
    with pytest.raises(services.CountryError) as e:
        services.post_note(b.owner, a, "連投")
    assert e.value.status == 429
    note = Note.objects.filter(island=a).first()
    res = cb.delete(f"/api/islands/{a.id}/country/notes/{note.id}")
    assert res.status_code == 403  # 持ち主でない人は隠せない
    assert ca.delete(f"/api/islands/{a.id}/country/notes/{note.id}").status_code == 200
    note.refresh_from_db()
    assert note.hidden
    assert ca.delete(f"/api/islands/{a.id}/country/notes/{note.id}9").status_code == 404
    res = ca.post(
        f"/islands/{a.id}/country/", {"action": "note_hide", "note": Note.objects.first().id}
    )
    assert res.status_code == 302


def test_timeline_shows_diplomacy_for_both_sides(players):
    (_, a), (_, b) = players
    TurnLog.objects.create(
        island=a, turn=3, kind="war_declared", args={"country": b.name, "turn": 4}
    )
    TurnLog.objects.create(
        island=b, turn=3, kind="war_declared_on", args={"country": a.name, "turn": 4}
    )
    for island, word in ((a, "宣戦を布告しました"), (b, "宣戦を布告されました")):
        data = Client().get(f"/api/islands/{island.id}/country").json()
        assert any(word in row["text"] for row in data["timeline"])


def test_admin_warns_when_pausing_a_country_at_war(admin_client, players, world):
    (_, a), (_, b) = players
    world.ext = {
        **world.ext,
        "diplomacy": {"wars": [{"id": 1, "attacker": a.id, "defender": b.id, "state": "war"}]},
    }
    world.save(update_fields=["ext"])
    res = admin_client.post(
        "/admin/world/island/",
        {"action": "make_inactive", "_selected_action": [a.id]},
        follow=True,
    )
    assert "戦争中・停戦中の島" in res.content.decode()


def test_admin_flag_preview_shows_hidden_flags_and_can_unhide(admin_client, players):
    (_, a), _ = players
    flag = services.set_flag(a, make_png())
    Flag.objects.filter(pk=flag.pk).update(hidden=True)
    page = admin_client.get(f"/admin/country/flag/{flag.pk}/change/").content.decode()
    assert "data:image/png;base64," in page
    admin_client.post("/admin/country/flag/", {"action": "show", "_selected_action": [flag.pk]})
    flag.refresh_from_db()
    assert not flag.hidden
