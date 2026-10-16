"""人が画面でできること・見られるものが、AI の入り口（MCP の道具）でもできるかを確かめる。

画面・命令・地図の重ね表示を足したり変えたりしたのに MCP を合わせ忘れると、ここが落ちる。落ちたら、
足りない中身を JSON API と MCP の道具（server/web/mcp.py）に足す（CONTRIBUTING.md の「AI の入り口」）。

画面の中身の比べは、サンプルの世界を数ターン回した島で、画面に渡す値（テンプレートの文脈）が道具の
答えのどこかに同じ値であるかを見る。空の値（その島に無いもの）は比べない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import URLResolver, get_resolver

from server.accounts import keys
from server.game import get_game
from server.turns.services import run_turn
from server.web import mcp, views
from server.world.models import Island

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]

# 画面の見せ方にだけ使う値（同じ中身は、道具の答えのほかの所にある）
PRESENTATION = {
    "island",  # 島（答えの island・summary の island）
    "mine",
    "stats_turn",
    "currency",
    "page",
    "title",
    "plan_label",
    "note",  # 他国の島で中身を出さないわけ
    "charts",  # グラフに出す統計の名前（系列は stats で読む）
    "chart_names",
    "bank_ratio_names",
    "rate_names",
    "band",  # 為替の幅の % の文（答えの band）
    "show_guide",  # はじめの手順（MCP は INSTRUCTIONS と guide）
}
# 画面の値と形が違うもの: 画面の文脈の名前 → 道具の答えの場所（. でたどる）
ALIASES = {
    "island": {
        "summary": "indicators",  # 国勢の表
        "goods": "goods",  # 財の表（市場の表と同じ財）
        "logs": "events",  # 直近のターンの出来事
        "regime": "island.regime",
    },
    "turn_log": {"groups": "entries"},
}
# 画面（URL の名前）→ 同じ中身を読む道具と引数（島の番号は後で足す）
READS = {
    "island": ("summary", {}),
    "turn_log": ("turn_log", {}),
    **{f"island_{page}": ("report", {"page": page}) for page in mcp.REPORTS if page != "orders"},
}


def _screen_names() -> set[str]:
    """人が見る画面の URL の名前（API・MCP・管理画面を除く）。"""
    names: set[str] = set()

    def walk(patterns: list[Any], prefix: str) -> None:
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                if getattr(p, "app_name", None) != "admin" and route != "api/":
                    walk(p.url_patterns, route)
            elif p.name and not route.startswith("api/") and p.name != "mcp":
                names.add(p.name)

    walk(get_resolver().url_patterns, "")
    return names


def test_every_screen_has_an_ai_way():
    screens = _screen_names()
    missing = screens - set(mcp.SCREENS) - mcp.NOT_FOR_AI
    assert not missing, (
        f"AI の入り口がない画面: {sorted(missing)}。server/web/mcp.py の SCREENS に、"
        "同じ中身を読む道具を書く（なければ道具を足す）"
    )
    stale = (set(mcp.SCREENS) | mcp.NOT_FOR_AI) - screens
    assert not stale, f"もうない画面: {sorted(stale)}"
    for screen, tools in mcp.SCREENS.items():
        assert set(tools) <= set(mcp.TOOLS_BY_NAME), screen
        if screen.startswith("island_"):
            assert screen.removeprefix("island_") in mcp.REPORTS, screen
    assert set(READS) <= set(mcp.SCREENS)


# --- 画面の中身 ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _within(small: Any, big: Any) -> bool:
    """small が big に含まれるか（辞書は small の項目が全部 big に同じ値である。並びは同じ長さ）。"""
    if isinstance(small, dict):
        return isinstance(big, dict) and all(k in big and _within(v, big[k]) for k, v in small.items())
    if isinstance(small, list):
        return (
            isinstance(big, list)
            and len(small) == len(big)
            and all(_within(a, b) for a, b in zip(small, big, strict=True))
        )
    return small == big


def _found(big: Any, small: Any) -> bool:
    """small が big のどこか（big そのものか、その中の値）に含まれるか。"""
    if _within(small, big):
        return True
    children = big.values() if isinstance(big, dict) else big if isinstance(big, list) else []
    return any(_found(child, small) for child in children)


def _words(data: Any) -> set[str]:
    """答えの中の、辞書の項目の名前と文字の値すべて（統計の名前を探すため）。"""
    if isinstance(data, dict):
        return set(data) | {w for v in data.values() for w in _words(v)}
    if isinstance(data, list):
        return {w for v in data for w in _words(v)}
    return {data} if isinstance(data, str) else set()


def _stat_names(value: Any) -> set[str] | None:
    """統計の表の行（key・label・text）なら、その統計の名前。"""
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        if all({"key", "label", "text"} <= set(r) for r in value):
            return {r["key"] for r in value}
    return None


def _ids(rows: Any) -> set[Any] | None:
    """行の一覧なら、行を見分ける値（財・統計の名前・文）の組。"""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None
    for key in ("good", "key", "text"):
        if rows and all(key in r for r in rows):
            return {r[key] for r in rows}
    return None


def _at(data: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _gaps(screen: str, context: dict[str, Any], answer: Any) -> list[str]:
    """画面の値のうち、道具の答えにないもの。"""
    gaps = []
    aliases = ALIASES.get(screen, {})
    words = _words(answer)
    for key, value in _plain(context).items():
        if key in PRESENTATION or _empty(value):
            continue
        if key in aliases:
            there = _at(answer, aliases[key])
            mine, theirs = _ids(value), _ids(there)
            if _empty(there) or (mine is not None and theirs is not None and not mine <= theirs):
                gaps.append(f"{key}（{aliases[key]}）")
            continue
        names = _stat_names(value)
        if names is not None:
            if names - words:
                gaps.append(f"{key} の統計 {sorted(names - words)}")
        elif not _found(answer, value):
            gaps.append(key)
    return gaps


@pytest.fixture
def played(world, settings):
    """持ち主と AI 用の鍵のある島 0 と、ほかの人の島 1。数ターン回して統計と帳簿を作る。"""
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 10_000
    cache.clear()
    islands = list(Island.objects.order_by("id"))
    user = User.objects.create_user("ai-player", password="a-long-pass-123")
    other = User.objects.create_user("someone", password="a-long-pass-123")
    islands[0].owner, islands[1].owner = user, other
    # 計画の画面にも中身が出るように、自分の島は計画経済、ほかの人の島は混合経済にする
    islands[0].regime = {**islands[0].regime, "economy": "planned"}
    islands[1].regime = {**islands[1].regime, "economy": "mixed"}
    islands[0].save(update_fields=["owner", "regime"])
    islands[1].save(update_fields=["owner", "regime"])
    for _ in range(3):
        run_turn(world.id)
    _, token = keys.issue(user, "test")
    browser = Client()
    browser.force_login(user)
    ai = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
    yield browser, ai, islands[0], islands[1]
    cache.clear()


def _call(ai: Client, name: str, **arguments: Any) -> tuple[str, bool]:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    message["params"] = {"name": name, "arguments": arguments}
    res = ai.post("/mcp", json.dumps(message), content_type="application/json")
    assert res.status_code == 200, res.content
    result = res.json()["result"]
    return result["content"][0]["text"], result["isError"]


def _tool(ai: Client, name: str, **arguments: Any) -> str:
    text, is_error = _call(ai, name, **arguments)
    assert not is_error, f"{name} {arguments}: {text}"
    return text


def test_screens_show_nothing_the_ai_cannot_read(played, monkeypatch):
    browser, ai, own, other = played
    captured: dict[str, Any] = {}
    render = views.render

    def capture(request, template, context=None, *args, **kwargs):
        captured.clear()
        captured.update(context or {})
        return render(request, template, context, *args, **kwargs)

    monkeypatch.setattr(views, "render", capture)
    problems = []
    for island in (own, other):
        for screen, (tool, arguments) in READS.items():
            page = screen.removeprefix("island_") if screen.startswith("island_") else ""
            url = {"island": f"/islands/{island.id}/", "turn_log": f"/islands/{island.id}/log/"}
            res = browser.get(url.get(screen) or f"/islands/{island.id}/{page}/")
            assert res.status_code == 200, screen
            text, is_error = _call(ai, tool, island=island.id, **arguments)
            answer = {} if is_error else json.loads(text)  # 持ち主だけの中身（他国の産業の鎖）
            who = "自国" if island == own else "他国"
            problems += [f"{who}の {screen}: {gap}" for gap in _gaps(screen, captured, answer)]
    assert not problems, (
        "画面にあって MCP の道具の答えにない値: "
        + "、".join(problems)
        + "。JSON API（server/web/api.py）と道具（server/web/mcp.py）に足す"
    )


def test_summary_has_what_the_island_screen_has(played):
    _, ai, own, other = played
    data = json.loads(_tool(ai, "summary"))
    assert data["island"]["resource_ids"] == own.resources
    assert set(data["island"]["resource_ids"]) <= set(data["island"]["producible"])
    assert "strengths" in data["strengths"] and "titles" in data["strengths"]
    # 移民の受け入れと出国の扱いは、政治の画面と同じく持ち主だけ
    assert set(data["politics"]["migration"]) == {"immigration", "emigration"}
    assert "migration" not in json.loads(_tool(ai, "summary", island=other.id))["politics"]


# --- 命令 -------------------------------------------------------------------------------


def test_every_order_on_screens_is_in_the_ai_defs(played):
    _, ai, _, _ = played
    kinds: set[str] = set()
    for path in (REPO / "server" / "templates").rglob("*.html"):
        kinds |= set(re.findall(r'data-order="([a-z_]+)"', path.read_text(encoding="utf-8")))
    for path in (REPO / "server" / "static" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        kinds |= set(re.findall(r'addOrder\(\s*"([a-z_]+)"', text))
        kinds |= set(re.findall(r'\bkind: "([a-z_]+)"', text))
    assert len(kinds) > 30  # 探し方が外れていないこと
    orders = json.loads(_tool(ai, "defs", section="orders"))["orders"]
    assert kinds <= set(orders), f"画面の命令で MCP の定義にないもの: {sorted(kinds - set(orders))}"
    assert set(get_game().defs.orders) == set(orders)
    for kind, spec in orders.items():  # 命令の形が分かること（set_orders は形どおりに送る）
        assert spec.get("name") and "payload" in spec and "admin_cost" in spec, kind


# --- 地図の重ね表示 -----------------------------------------------------------------------


def test_every_map_overlay_has_a_text_map(played):
    _, ai, own, _ = played
    folder = REPO / "server" / "static" / "js" / "map" / "overlays"
    ids: set[str] = set()
    for path in folder.glob("*.js"):
        ids |= set(re.findall(r'^\s*id: "([a-z_]+)"', path.read_text(encoding="utf-8"), re.M))
    assert ids == set(mcp.OVERLAYS), "画面の重ね表示と map の overlay が合わない"
    assert "infra:${kind}" in (folder / "infra.js").read_text(encoding="utf-8")
    infra = [f"infra:{kind}" for kind in get_game().defs.infrastructure]
    size = own.size
    for overlay in [*mcp.OVERLAYS, *infra]:
        lines = _tool(ai, "map", overlay=overlay).splitlines()
        rows = [line for line in lines if re.match(r"^\s*\d+ ", line)]
        assert len(rows) == size and all(len(r) == 4 + size for r in rows), overlay

    # 人口の段と電力の印が、マスの値のとおりであること
    tile = own.tiles.filter(population__gte=30).order_by("-population").first()
    assert tile is not None
    rows = [r for r in _tool(ai, "map", overlay="population").splitlines() if r[:3].strip().isdigit()]
    step = sum(1 for s in mcp.POPULATION_STEPS[1:] if tile.population >= s)
    assert rows[tile.y][4 + tile.x] == str(step)
    power = (tile.ext or {}).get("power") or 0
    rows = [r for r in _tool(ai, "map", overlay="power").splitlines() if r[:3].strip().isdigit()]
    assert rows[tile.y][4 + tile.x] == ("*" if power >= 1000 else str(min(9, power // 100)))
