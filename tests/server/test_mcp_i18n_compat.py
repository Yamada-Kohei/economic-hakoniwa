"""MCP の文言を gettext へ移したあとも、見える文が変わらないことの固定。

多言語の抜き出し（docs/i18n.md、掲示板 #6203 の12）で server/web/mcp.py の
文言を gettext / gettext_lazy へ移した。訳が無い間は、既定の言語（日本語）で
移す前とまったく同じ文が出なければならない。ここでは、変数を埋める文・
一覧を「・」でつなぐ文・道具の説明など、移し替えで崩れやすい所を固定する。
文言を意図して変えるときは、この試験の期待値も一緒に直すこと。
"""

from __future__ import annotations

import pytest

from server.web import mcp


def _tools_by_name():
    result = mcp.handle(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    return {tool["name"]: tool for tool in result["result"]["tools"]}


def test_tool_titles_and_descriptions_unchanged():
    tools = _tools_by_name()
    assert tools["whoami"]["title"] == "自分"
    assert tools["whoami"]["description"] == (
        "アカウント・自分の島（番号・国名・AI の印）・世界のターンと日付・次の更新時刻・"
        "未読の公電の数。"
    )
    assert tools["changes"]["title"] == "前回からの差分"
    assert tools["changes"]["description"] == (
        "ターン since より後に起きたこと: ターンログ（24 ターン分まで）・変わった指標・実行した"
        "命令の結果・届いた公電。前に読んだターンを since に渡す。"
    )
    # 道具の説明の中で、一覧（OVERLAYS の名前）を「・」でつないで組み立てる所。
    assert tools["map"]["description"] == (
        "島の文字の地図（地形・町・施設・首都）と、施設ごとの番号・名前・稼働率・状態のわけ。"
        "overlay で画面の「重ねて表示」と同じ地図: "
        "population・happiness・power・pollution・zone・freight・works"
        "・infra:<インフラの種類>（例 infra:road）。"
    )
    # guide の which の説明（引数の説明も gettext 側に移っている）。
    which = tools["guide"]["inputSchema"]["properties"]["which"]
    assert which["description"] == (
        "general（既定）は詳しい仕組み、start は入門、hakoniwa は従来の箱庭との違い、"
        "ai は AI で遊ぶ人向け、terms は利用規約"
    )


def test_report_description_built_from_reports_unchanged():
    # report の説明は REPORTS の中身を「／」と「: 」でつないで組み立てる。
    description = _tools_by_name()["report"]["description"]
    assert description.startswith(
        "画面と同じ中身を JSON で: economy: 経済（主な指標と国勢の今の値・得意産業と称号・"
    )
    assert description.endswith(
        "／orders: 命令の列・行政力・今の政策（税・予算・金融）・最近の命令の結果"
    )
    for key in mcp.REPORTS:
        assert f"／{key}: " in description or description.startswith(
            f"画面と同じ中身を JSON で: {key}: "
        )


def test_initialize_text_unchanged():
    result = mcp.handle(
        None,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
    )
    info = result["result"]
    assert info["serverInfo"]["title"] == "経済版箱庭諸国"
    assert info["instructions"].startswith(
        "経済版箱庭諸国（島国の経済を育てるターン制のゲーム。1ターン＝1か月）を、"
        "AI 用の鍵のアカウントとして\n遊ぶための道具です。"
    )


def test_error_messages_with_variables_unchanged():
    # 知らない道具の名前を埋める文。
    result = mcp.handle(
        None,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
    )
    assert result["error"]["message"] == "道具 nope はありません"
    # 知らない方法の名前を埋める文。
    result = mcp.handle(None, {"jsonrpc": "2.0", "id": 3, "method": "fly", "params": {}})
    assert result["error"]["message"] == "fly には答えられません"
    # 要る引数の名前を「・」でつないで埋める文。
    answer = mcp._call_tool(None, {"name": "changes", "arguments": {}})
    assert answer["isError"] is True
    assert answer["content"][0]["text"] == "since が要ります"
    # 選べる page の一覧を「・」でつないで埋める文。
    with pytest.raises(mcp.ToolError) as caught:
        mcp._report(None, {"page": "nope"})
    assert str(caught.value) == (
        "page は economy・market・facilities・fiscal・finance・trade・fx・politics・"
        "diplomacy・military・country・plan・chain・orders のどれかです"
    )
    with pytest.raises(mcp.ToolError) as caught:
        mcp._defs(None, {"section": "nope"})
    assert str(caught.value) == (
        "section は orders・facilities・goods・terrain・infrastructure・settlements・"
        "regimes・axes・mining・army・founding のどれかです"
    )


def _overlay_data():
    size = 4
    return {
        "size": size,
        "terrain": ["plain"] * (size * size),
        "population": [0, 5, 50, 500] + [0] * 12,
        "happiness": [None, 10.0, 55.0, 95.0] + [None] * 12,
        "power": [0, 500, 1000, 1200] + [0] * 12,
        "power_line": [False] * 16,
        "pollution": [0, 1, 5, 12] + [0] * 12,
        "zone": [None, "industrial", "office", "commercial"] + [None] * 12,
        "freight": [None, 1.0, 2.5, 9.9] + [None] * 12,
        "freight_max": 5.0,
        "infra_queue": [[1, 0, 0]],
        "facilities": [{"built": False, "x": 1, "y": 1}],
    }


def test_overlay_legends_with_numbers_unchanged():
    data = _overlay_data()
    # 数の桁区切りを整えてから埋める文。
    _, legend = mcp._overlay(data, "population")
    assert legend == [
        "人口の段（0: 1〜2 人、1: 3〜9 人、2: 10〜29 人、3: 30〜99 人、4: 100〜299 人、"
        "5: 300〜999 人、6: 1,000〜2,999 人、7: 3,000〜9,999 人、8: 10,000〜29,999 人、"
        "9: 30,000 人以上）"
    ]
    _, legend = mcp._overlay(data, "power")
    assert legend == [
        "住民のいるマスが受け取れた電力の割合（0〜9 は 10% 刻み、* は足りている）。= は送電線のマス"
    ]
    # 割合（:g の形）を整えてから埋める文。
    _, legend = mcp._overlay(data, "freight")
    assert legend == [
        "拠点（首都と大きな町）まで運ぶときの運賃の率。この島でいちばん高いマス"
        "（0.025%）を 9 とした10段。x は上限（0.05%）に届くマス"
    ]


def test_confirm_hints_and_truncation_unchanged():
    # 確かめの文は gettext_lazy の値だが、道具の答え（JSON）に入るときは str で確定する。
    assert str(mcp.CONFIRM_HINT) == (
        "まだ並べていません。confirm の命令（index は送った列の位置）の notes を読み、"
        "このままでよければ同じ orders に confirm: true を付けて set_orders を出し直して"
        "ください。違っていたら直して出し直してください"
    )
    assert str(mcp.ADD_CONFIRM_HINT) == (
        "まだ足していません。confirm の notes を読み、このままでよければ同じ kind と "
        "payload に confirm: true を付けて add_order を出し直してください"
    )
    dumped = mcp._dump({"saved": False, "next": str(mcp.CONFIRM_HINT)})
    assert "まだ並べていません" in dumped
    # 長い答えを切るときの文（上限の数を埋める）。
    text = mcp._text("あ" * (mcp.TEXT_MAX + 1))
    assert text.endswith(
        f"\n…（長いので {mcp.TEXT_MAX} 字で切りました。範囲を絞って読んでください）"
    )
