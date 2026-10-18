"""MCP のつなぎ先（POST /mcp）: AI がゲームを道具として使うための入り口。

MCP（Model Context Protocol）は、AI が外の道具を使うための公開の決まりで、Claude も ChatGPT
（Codex）も使える。ここでは Streamable HTTP の形で、状態を持たない（セッションを作らない）サーバー
として動き、1つの POST に1つの JSON の答えを返す（SSE は使わない）。

- 入るには AI 用の鍵（server/accounts/keys.py）が要る。`Authorization: Bearer <鍵>` を付けて呼ぶ。
  ブラウザの cookie では入れない（CSRF の印を使わない入り口なので）。
- 道具の中身は、画面が使う JSON API（api.py ほか）をサーバーの中で呼ぶだけ。見られるもの・出せる
  命令・送れる公電の数は、画面で遊ぶ人と同じ決まりで決まる。AI だけが見られる値や、先の見込みは
  出さない。
- 鍵での呼び出しの回数の上限は、MCP の1つの POST を1回と数える。

決まりの版は PROTOCOL_VERSIONS のどれでも受ける（initialize で相手が言った版が分かれば、それで
答える）。
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.http import Http404, HttpRequest, HttpResponse, JsonResponse, QueryDict
from django.urls import Resolver404, resolve
from django.views.decorators.csrf import csrf_exempt

from hakoniwa_core import facilities as fac
from hakoniwa_core.naming import facility_name
from server.game import get_game
from server.letters import services as letter_services
from server.web import display
from server.world import models as m
from server.world.repository import facility_from_row

logger = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "economic-hakoniwa", "title": "経済版箱庭諸国", "version": "0.0.1"}
INSTRUCTIONS = """\
経済版箱庭諸国（島国の経済を育てるターン制のゲーム。1ターン＝1か月）を、AI 用の鍵のアカウントとして
遊ぶための道具です。人のプレイヤーと同じ決まりで動き、見られるのは画面で見られることだけです。

- 初めに whoami で自分の島とターンを確かめ、guide で手引きを読み、summary で国の要約を読みます。
  島がなければ found で建国します。
- 次のターンからは changes(since=前に読んだターン) で、その後に起きたことだけを読みます。
- 命令は check_order で確かめてから、set_orders で命令の列を丸ごと置き換えます（今の列は summary の
  orders.pending）。中身の形は defs(section="orders")。1ターンに実行されるのは行政力の数までです。
- ターンはサーバーが決まった時刻に進めます。ほかの国との連絡は公電（letters・send_letter）と
  掲示板です。
"""

# 道具の答えの文の上限（これを超える分は切って、続きの読み方を添える）
TEXT_MAX = 60000
GUIDE_LENGTH = 8000  # guide が1回に返す字数の既定


class ToolError(Exception):
    """道具を使えなかったわけ（AI に isError の答えとして返す）。"""


# --- サーバーの中で API を呼ぶ ------------------------------------------------------------


def call(
    request: HttpRequest,
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: Any = None,
) -> tuple[int, Any]:
    """path の API のビューを、同じアカウントで呼ぶ。答えは (HTTP の状態, JSON か文)。

    ミドルウェアは通らない（鍵の確かめと回数の数えは、この POST /mcp で済んでいる）。
    """
    try:
        match = resolve(path)
    except Resolver404 as e:
        raise ToolError(f"{path} はありません") from e
    sub = HttpRequest()
    sub.method = method
    sub.path = sub.path_info = path
    sub.META = {
        **{k: v for k, v in request.META.items() if k.isupper() and not k.startswith("CONTENT_")},
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": urlencode(query or {}),
    }
    sub.GET = QueryDict(urlencode({k: v for k, v in (query or {}).items() if v is not None}))
    if body is not None:
        sub._body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        sub.META["CONTENT_TYPE"] = "application/json"
    sub.user = request.user
    sub.api_key = getattr(request, "api_key", None)
    sub.COOKIES = {}
    sub._dont_enforce_csrf_checks = True
    try:
        response = match.func(sub, *match.args, **match.kwargs)
    except Http404:
        return 404, {"error": "見つかりません"}
    text = response.content.decode("utf-8") if hasattr(response, "content") else ""
    if response.get("Content-Type", "").startswith("application/json"):
        return response.status_code, json.loads(text)
    return response.status_code, text


def call_ok(request: HttpRequest, method: str, path: str, **kwargs: Any) -> Any:
    """call して、うまくいかなければ ToolError（API の誤りの文をそのまま）。"""
    status, data = call(request, method, path, **kwargs)
    if status >= 400:
        if isinstance(data, dict):
            text = data.get("error") or data.get("errors") or data.get("problems") or data
        else:
            text = _html_text(data)[:500] if "<" in data else data
        raise ToolError(f"{status}: {text if isinstance(text, str) else _dump(text)}")
    return data


def _own_island(request: HttpRequest) -> m.Island:
    island = letter_services.own_island(request.user)
    if island is None:
        raise ToolError("まだ自分の島がありません。found で建国してください")
    return island


def _island_id(request: HttpRequest, args: dict[str, Any]) -> int:
    """道具の island（省けば自分の島）。"""
    if args.get("island") is not None:
        return int(args["island"])
    return _own_island(request).id


# --- 道具 -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    run: Callable[[HttpRequest, dict[str, Any]], Any]
    properties: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    read_only: bool = True
    destructive: bool = False

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "openWorldHint": False,
            },
        }


ISLAND = {"type": "integer", "description": "島の番号（省くと自分の島）"}
TURN = {"type": "integer", "description": "ターン"}
PAGE = {"type": "integer", "minimum": 1, "description": "ページ（1 から）"}

REPORTS = {
    "economy": "経済（主な指標と国勢の今の値・得意産業と称号・stats で読める統計の名前 choices）",
    "market": "市場（財ごとの値段・売買量・買えなかった量。good を決めるとその財の推移と約定）",
    "facilities": "施設（町ごとの労働力・失業と買いに行く店の場所 towns、産業ごとの従業者数 "
    "industry_workers。持ち主には施設ごとの"
    "状態・人手・損益・預金・借入と、畑を作り替えている農場の内訳）",
    "fiscal": "財政（国庫の収入と支出・予算・税率・国債の一覧）",
    "finance": "金融（中央銀行の方針・金利・融資。持ち主には銀行ごとの帳簿と金融の政策）",
    "trade": "貿易（輸出入・貿易圏。持ち主には契約・商社・船団・産業政策）",
    "fx": "為替と国際収支（持ち主には外貨の持ち高と為替の政策）",
    "politics": "政治（体制・政情・支持率・議席・選挙。持ち主には政情の内訳と命令の選択肢）",
    "plan": "計画と値段の規制（計画経済・混合経済の島）",
    "chain": "産業の鎖（自分の島だけ。品目ごとの作った量・使った量と、止まっている工場のわけ）",
    "orders": "命令の列・行政力・今の政策（税・予算・金融）・最近の命令の結果",
}

DEFS_SECTIONS = (
    "orders",
    "facilities",
    "goods",
    "terrain",
    "infrastructure",
    "settlements",
    "regimes",
    "axes",
    "mining",
    "founding",
)


def _whoami(request, args):
    return call_ok(request, "GET", "/api/me")


def _summary(request, args):
    return call_ok(request, "GET", f"/api/islands/{_island_id(request, args)}/summary")


def _changes(request, args):
    path = f"/api/islands/{_island_id(request, args)}/changes"
    return call_ok(request, "GET", path, query={"since": args["since"]})


def _turn_log(request, args):
    path = f"/api/islands/{_island_id(request, args)}/log"
    return call_ok(request, "GET", path, query={"from": args.get("from"), "to": args.get("to")})


def _tile(request, args):
    path = f"/api/islands/{_island_id(request, args)}/tiles/{int(args['x'])},{int(args['y'])}"
    return call_ok(request, "GET", path)


def _stats(request, args):
    path = f"/api/islands/{_island_id(request, args)}/economy"
    query = {"names": args["names"], "from": args.get("from"), "to": args.get("to")}
    return call_ok(request, "GET", path, query=query)


def _report(request, args):
    page = args["page"]
    if page not in REPORTS:
        raise ToolError(f"page は {'・'.join(REPORTS)} のどれかです")
    island = _island_id(request, args)
    if page == "market" and args.get("good"):
        return call_ok(request, "GET", f"/api/islands/{island}/market/{args['good']}")
    if page == "economy":
        return call_ok(request, "GET", f"/api/islands/{island}/economy/overview")
    return call_ok(request, "GET", f"/api/islands/{island}/{page}")


def _defs(request, args):
    section = args["section"]
    if section not in DEFS_SECTIONS:
        raise ToolError(f"section は {'・'.join(DEFS_SECTIONS)} のどれかです")
    if section == "orders":
        data = call_ok(request, "GET", "/api/defs/orders")["orders"]
    else:
        data = call_ok(request, "GET", "/api/defs")[section]
    ids = args.get("ids")
    if ids and isinstance(data, dict):
        data = {k: v for k, v in data.items() if k in ids}
    return {section: data}


def _check_order(request, args):
    island = _own_island(request).id
    order = {"kind": args["kind"], "payload": args.get("payload") or {}}
    return call_ok(request, "POST", f"/api/islands/{island}/orders/check", body=order)


def _set_orders(request, args):
    island = _own_island(request).id
    orders = args["orders"]
    if not isinstance(orders, list) or not all(
        isinstance(o, dict) and isinstance(o.get("payload", {}), dict) for o in orders
    ):
        raise ToolError("orders は {kind, payload} の一覧で送ってください")
    status, data = call(
        request, "PUT", f"/api/islands/{island}/orders", body={"orders": args["orders"]}
    )
    if status >= 400:
        raise ToolError(f"{status}: {data if isinstance(data, str) else _dump(data)}")
    keep = ("admin_power", "orders", "warnings")
    return {k: data[k] for k in keep if k in data}


def _world(request, args):
    return call_ok(request, "GET", "/api/world")


def _rankings(request, args):
    return call_ok(request, "GET", "/api/rankings", query={"base": args.get("base")})


def _letters(request, args):
    query = {"box": args.get("box") or "inbox", "page": args.get("page") or 1}
    return call_ok(request, "GET", "/api/letters", query=query)


def _read_letter(request, args):
    return call_ok(request, "GET", f"/api/letters/{int(args['id'])}")


def _send_letter(request, args):
    body = {"to": args["to"], "subject": args["subject"], "body": args["body"]}
    return call_ok(request, "POST", "/api/letters", body=body)


def _board(request, args):
    return call_ok(request, "GET", "/api/board/threads", query={"page": args.get("page") or 1})


def _board_thread(request, args):
    path = f"/api/board/threads/{int(args['id'])}"
    return call_ok(request, "GET", path, query={"page": args.get("page") or "last"})


def _board_post(request, args):
    path = f"/api/board/threads/{int(args['thread'])}/posts"
    return call_ok(request, "POST", path, body={"body": args["body"]})


def _board_new_thread(request, args):
    body = {"title": args["title"], "body": args["body"]}
    return call_ok(request, "POST", "/api/board/threads", body=body)


def _api_get(request, args):
    parts = urlsplit(str(args["path"]))
    path = parts.path if parts.path.startswith("/") else "/" + parts.path
    if not path.startswith("/api/") or ".." in path:
        raise ToolError("読めるのは /api/ で始まる場所だけです")
    query = dict(QueryDict(parts.query).items())
    return call_ok(request, "GET", path, query=query)


def _guide(request, args):
    """手引き（/help/）を文字にして、find の見出しから、または offset から length 字。"""
    page = call_ok(request, "GET", "/help/")
    text = _html_text(page)
    start = int(args.get("offset") or 0)
    if args.get("find"):
        found = text.find(args["find"], start)
        if found < 0:
            raise ToolError(f"手引きに「{args['find']}」は見つかりませんでした")
        start = found
    length = int(args.get("length") or GUIDE_LENGTH)
    end = min(len(text), start + length)
    head = f"[手引き {start}〜{end} 字目／全 {len(text)} 字"
    if end < len(text):
        head += f"。続きは offset={end}"
    return head + "]\n" + text[start:end]


# 地図の重ね表示（画面の「重ねて表示」と同じ。static/js/map/overlays/）。ほかに infra:<種類>。
OVERLAYS = {
    "population": "人口",
    "happiness": "幸福度",
    "power": "電力充足率",
    "pollution": "汚染度",
    "zone": "工業団地・オフィス街",
    "freight": "運賃の率",
    "works": "工事中",
}
# 人口の重ね表示の段（この人数以上で数字が1つ上がる。画面の色と同じく桁で濃くなる）
POPULATION_STEPS = (1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000)


def _overlay_ids() -> list[str]:
    return [*OVERLAYS, *(f"infra:{kind}" for kind in get_game().defs.infrastructure)]


def _digit(value: float, top: float) -> str:
    """0〜top を 0〜9 の10段に（top 以上は 9）。"""
    return str(min(9, max(0, int(value * 10 / top)))) if top > 0 else "0"


def _overlay(data: dict[str, Any], overlay: str) -> tuple[list[list[str]], list[str]]:
    """重ね表示の文字の地図と凡例。海と浅瀬は地形の印のまま、値のない陸のマスは「.」。"""
    size = data["size"]
    marks = {"sea": "~", "shallows": "-"}
    terrain = data["terrain"]
    grid = [[marks.get(terrain[y * size + x], ".") for x in range(size)] for y in range(size)]

    def put(i: int, mark: str) -> None:
        grid[i // size][i % size] = mark

    if overlay == "population":
        for i, p in enumerate(data["population"]):
            if p:
                put(i, str(sum(1 for step in POPULATION_STEPS[1:] if p >= step)))
        steps = POPULATION_STEPS
        ranges = [f"{d}: {steps[d]:,}〜{steps[d + 1] - 1:,} 人" for d in range(9)]
        return grid, ["人口の段（" + "、".join(ranges) + f"、9: {steps[9]:,} 人以上）"]
    if overlay == "happiness":
        for i, h in enumerate(data["happiness"]):
            if h is not None:
                put(i, _digit(h, 100))
        return grid, ["幸福度を 10 で割った数（0〜9。9 は 90 以上）"]
    if overlay == "power":
        for i, p in enumerate(data["population"]):
            if p:
                power = data["power"][i] or 0
                put(i, "*" if power >= 1000 else _digit(power, 1000))
            elif data["power_line"][i]:
                put(i, "=")
        return grid, [
            "住民のいるマスが受け取れた電力の割合（0〜9 は 10% 刻み、* は足りている）。"
            "= は送電線のマス"
        ]
    if overlay == "pollution":
        for i, p in enumerate(data["pollution"]):
            if p:
                put(i, str(min(9, p)))
        return grid, ["汚染度（1〜9。9 は 9 以上）"]
    if overlay == "zone":
        for i, zone in enumerate(data["zone"]):
            if zone in ("industrial", "office"):
                put(i, "I" if zone == "industrial" else "O")
        return grid, ["I 工業団地  O オフィス街"]
    if overlay == "freight":
        rates, limit = data["freight"], data["freight_max"]
        reach = [r for r in rates if r is not None and r < limit]
        top = max(reach, default=1) or 1
        for i, rate in enumerate(rates):
            if rate is not None:
                put(i, "x" if rate >= limit else _digit(rate, top))
        return grid, [
            "拠点（首都と大きな町）まで運ぶときの運賃の率。この島でいちばん高いマス"
            f"（{top / 100:g}%）を 9 とした10段。x は上限（{limit / 100:g}%）に届くマス"
        ]
    if overlay == "works":
        for _, x, y in data.get("infra_queue") or []:
            grid[y][x] = "Q"
        for f in data["facilities"]:
            if not f["built"]:
                grid[f["y"]][f["x"]] = "C"
        return grid, ["Q インフラの工事の列に並んだマス  C 建設中の施設のマス"]
    kind = overlay.removeprefix("infra:")
    for i, level in enumerate(data["infra"][kind]):
        if level:
            put(i, _digit(level, 100))
    defs = get_game().defs
    infra = defs.infrastructure[kind]
    legend = [f"{defs.name('infrastructure', kind)}の整備度を 10 で割った数（9 は 90% 以上）"]
    if infra.get("role") == "power":
        legend.append(f"整備度 {round(infra.get('grid', 0) * 100)}% 以上のマスが電力を通す")
    return grid, legend


def _map(request, args):
    """島の文字の地図と、施設の一覧（状態のわけは画面と同じ文）。overlay を決めると重ね表示。"""
    island_id = _island_id(request, args)
    overlay = args.get("overlay")
    if overlay:
        if overlay not in _overlay_ids():
            raise ToolError(f"overlay は {'・'.join(_overlay_ids())} のどれかです")
        query = {"freight": 1} if overlay == "freight" else None
        data = call_ok(request, "GET", f"/api/islands/{island_id}/map", query=query)
        grid, legend = _overlay(data, overlay)
        size = data["size"]
        kind = overlay.removeprefix("infra:")
        name = OVERLAYS.get(overlay) or get_game().defs.name("infrastructure", kind) + "の整備度"
        out = [f"{data['name']}（島 {island_id}）ターン {data['turn']} 重ね表示: {name}", ""]
        out.append("    " + "".join(str(x % 10) for x in range(size)))
        out += [f"{y:>3} " + "".join(row) for y, row in enumerate(grid)]
        out.append("~ 海  - 浅瀬  . 値のない陸")
        return "\n".join(out + legend)
    data = call_ok(request, "GET", f"/api/islands/{island_id}/map")
    island = m.Island.objects.get(pk=island_id)
    game = get_game()
    size = data["size"]
    marks = {"sea": "~", "shallows": "-", "plain": ".", "forest": "T", "mountain": "^"}
    grid = [
        [marks.get(data["terrain"][y * size + x], "?") for x in range(size)] for y in range(size)
    ]
    housing = fac.housing_kind(game.config)
    state = display.island_for_naming(island)
    lines = []
    towns = 0
    for row in island.facilities.order_by("number"):
        on_map = row.x >= 0 and row.y >= 0
        if row.kind == housing:
            towns += 1
            if on_map:
                grid[row.y][row.x] = "#"
            continue
        if on_map:
            grid[row.y][row.x] = "F" if row.built else "f"
        f = facility_from_row(row)
        where = f"({row.x},{row.y})" if on_map else "(マスなし)"
        state_text = display.status_text(f)
        rate = f" 稼働 {round(row.operating_rate * 100 / fac.FULL)}%" if row.built else ""
        name = facility_name(game.defs, game.config, state, f)
        kind = game.defs.name("facilities", row.kind)
        lines.append(f"{where} #{row.number} {name}（{kind}）{rate} {state_text}".rstrip())
    if data.get("capital"):
        cx, cy = data["capital"]
        grid[cy][cx] = "@"
    out = [f"{data['name']}（島 {island_id}）ターン {data['turn']}", ""]
    out.append("    " + "".join(str(x % 10) for x in range(size)))
    out += [f"{y:>3} " + "".join(row) for y, row in enumerate(grid)]
    out.append("~ 海  - 浅瀬  . 平地  T 森  ^ 山  # 町  @ 首都  F 施設  f 工事中")
    out.append(f"町 {towns} か所、インフラの工事の列 {len(data.get('infra_queue') or [])} 件")
    out.append("")
    out += lines
    return "\n".join(out)


def _found(request, args):
    """ランダムな形と勧められた首都で建国する（tools/playtest/hk.py の found と同じ手順）。"""
    if letter_services.own_island(request.user) is not None:
        raise ToolError("すでに島があります（1人1島）")
    seed = args.get("seed")
    seed = int(seed) if seed is not None else random.SystemRandom().randrange(2**31)
    regime = {
        "politics": args.get("politics") or "democracy",
        "head_of_state": args.get("head_of_state") or "republic",
        "economy": args.get("economy") or "market",
    }
    problems: Any = []
    for _ in range(5):  # 形に問題があれば種を変えてやり直す
        land = call_ok(request, "POST", "/api/founding/random-shape", body={"seed": seed})["land"]
        info = call_ok(
            request,
            "POST",
            "/api/founding/terrain",
            body={"land": land, "seed": seed, "regime": regime},
        )
        problems = info["problems"]
        if not problems and info["suggested_capital"]:
            break
        seed += 1
    else:
        raise ToolError(f"形に問題があります: {problems}")
    free = call_ok(request, "GET", "/api/founding/cells")["free"]
    if not free:
        raise ToolError("世界マップに空いた区画がありません")
    cell = args.get("cell") or random.Random(seed).choice(free)
    plan = {
        "land": land,
        "capital": info["suggested_capital"],
        "regime": regime,
        "names": {
            "country": args["name"],
            "capital": args.get("capital") or "首都",
            "currency": args.get("currency") or "クレジット",
            "currency_symbol": args.get("currency_symbol") or "Cr",
            "leader": args.get("leader") or "元首",
        },
        "resources": list(args.get("resources") or ["iron_ore", "coal"]),
        "edits": [],
        "seed": seed,
        "place_style": args.get("place_style") or "",
        "cell": cell,
    }
    status, data = call(request, "POST", "/api/founding", body=plan)
    if status >= 400:
        raise ToolError(f"建国できませんでした: {data}")
    island = _own_island(request)
    return {
        "island": island.id,
        "name": island.name,
        "url": data["url"],
        "land": len(land),
        "capital": plan["capital"],
        "cell": cell,
        "seed": seed,
    }


TOOLS = [
    Tool(
        "whoami",
        "自分",
        "アカウント・自分の島（番号・国名・AI の印）・世界のターンと日付・次の更新時刻・"
        "未読の公電の数。",
        _whoami,
    ),
    Tool(
        "guide",
        "手引き",
        "ゲームの中の手引き（/help/）を文字で読む。遊び始める前に全部読む。"
        "find で見出しや言葉から、offset で続きから読める。",
        _guide,
        {
            "find": {"type": "string", "description": "この言葉から読み始める"},
            "offset": {"type": "integer", "minimum": 0, "description": "読み始める字数"},
            "length": {"type": "integer", "minimum": 1, "description": "読む字数（既定 8000）"},
        },
    ),
    Tool(
        "summary",
        "国の要約",
        "国の今の状態を1回で: 主な指標と前のターンからの変化、政情、止まった施設と工事、財の値段と"
        "買えなかった量、商業・サービス業の店（services）、人口の増減のわけ（population_changes）、"
        "飢えている家計の層とそのわけ（hunger）、満期の近い国債、直近のターンの出来事、注意。"
        "自分の島なら赤字の施設・命令の列と結果・未読の公電も。",
        _summary,
        {"island": ISLAND},
    ),
    Tool(
        "changes",
        "前回からの差分",
        "ターン since より後に起きたこと: ターンログ（24 ターン分まで）・変わった指標・実行した"
        "命令の結果・届いた公電。前に読んだターンを since に渡す。",
        _changes,
        {"since": TURN, "island": ISLAND},
        ("since",),
    ),
    Tool(
        "turn_log",
        "ターンログ",
        "ターンログ（from〜to、24 ターン分まで。省くと最後のターン）。",
        _turn_log,
        {"island": ISLAND, "from": TURN, "to": TURN},
    ),
    Tool(
        "map",
        "地図",
        "島の文字の地図（地形・町・施設・首都）と、施設ごとの番号・名前・稼働率・状態のわけ。"
        "overlay で画面の「重ねて表示」と同じ地図: "
        + "・".join(OVERLAYS)
        + "・infra:<インフラの種類>（例 infra:road）。",
        _map,
        {
            "island": ISLAND,
            "overlay": {"type": "string", "description": "重ね表示（population・infra:road など）"},
        },
    ),
    Tool(
        "tile",
        "マス",
        "1マスの中身（地形・人口・インフラの整備度・工事）と、そこの施設の詳しい情報。"
        "自分の島なら施設の帳簿と命令に使う値も。",
        _tile,
        {"x": {"type": "integer"}, "y": {"type": "integer"}, "island": ISLAND},
        ("x", "y"),
    ),
    Tool(
        "stats",
        "統計の推移",
        "統計の系列。names はカンマ区切り（例 gdp,cpi,unemployment,price.*。末尾の * はその頭の"
        "統計すべて）。使える名前は report page=economy の choices。範囲を省くと直近の 120 "
        "ターン。頼める数には上限があり（limits）、超えた分は dropped に名前を返す。",
        _stats,
        {"names": {"type": "string"}, "from": TURN, "to": TURN, "island": ISLAND},
        ("names",),
    ),
    Tool(
        "report",
        "画面の中身",
        "画面と同じ中身を JSON で: " + "／".join(f"{k}: {v}" for k, v in REPORTS.items()),
        _report,
        {
            "page": {"type": "string", "enum": list(REPORTS)},
            "good": {"type": "string", "description": "page=market のときの財（例 coal）"},
            "island": ISLAND,
        },
        ("page",),
    ),
    Tool(
        "defs",
        "定義",
        "定義データ。section=orders は命令ごとの表示名・要る行政力・中身の形（JSON Schema）・"
        "議会の採決・実行まで伏せるか。facilities は施設の労働者・原料・生産物・建設の資材。"
        "ids で絞れる。",
        _defs,
        {
            "section": {"type": "string", "enum": list(DEFS_SECTIONS)},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "絞る id"},
        },
        ("section",),
    ),
    Tool(
        "check_order",
        "命令を確かめる",
        "命令を1つ、自分の島の今の状態で確かめる（並べる前に使う）。建設なら通勤圏の人手も返す。",
        _check_order,
        {
            "kind": {"type": "string", "description": "命令の種類（defs section=orders の id）"},
            "payload": {"type": "object", "description": "命令の中身"},
        },
        ("kind", "payload"),
    ),
    Tool(
        "set_orders",
        "命令の列を置き換える",
        "自分の島の命令の列を丸ごと置き換える（今の列は消える。足すときは今の列に足して全部送る）。"
        "次のターンの更新で上から実行され、行政力の数を超えた分は次のターンに回る。"
        "形の誤りは受け付けず、今は実行できない命令は警告を付けて受け付ける。",
        _set_orders,
        {
            "orders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"}, "payload": {"type": "object"}},
                    "required": ["kind", "payload"],
                },
            }
        },
        ("orders",),
        read_only=False,
        destructive=True,
    ),
    Tool(
        "world",
        "世界",
        "世界の島の一覧（番号・国名・持ち主・自分の島か・AI の印・気候・世界マップの位置）。",
        _world,
    ),
    Tool(
        "rankings",
        "順位",
        "統計の順位（GDP・1人あたり GDP・人口・幸福度・輸出額）と産業ごとの称号。"
        "base はお金の額を直す通貨の島の番号。",
        _rankings,
        {"base": {"type": "integer"}},
    ),
    Tool(
        "letters",
        "公電の一覧",
        "自分の島の公電の一覧（box=inbox は届いた公電、sent は送った公電。新しい順）と未読の数。",
        _letters,
        {"box": {"type": "string", "enum": ["inbox", "sent"]}, "page": PAGE},
    ),
    Tool(
        "read_letter",
        "公電を読む",
        "公電を1通読む（届いた公電は既読になる）。",
        _read_letter,
        {"id": {"type": "integer"}},
        ("id",),
        read_only=False,
    ),
    Tool(
        "send_letter",
        "公電を送る",
        "ほかの島に公電を送る（非公開。すぐ届く。1つの島から送れる数には 24 時間の上限がある）。",
        _send_letter,
        {
            "to": {"type": "integer", "description": "宛先の島の番号"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        ("to", "subject", "body"),
        read_only=False,
    ),
    Tool(
        "board",
        "掲示板",
        "掲示板（誰でも読める）のスレッドの一覧。",
        _board,
        {"page": PAGE},
    ),
    Tool(
        "board_thread",
        "掲示板のスレッド",
        "スレッドの書き込み（省くと最後のページ）。",
        _board_thread,
        {"id": {"type": "integer"}, "page": PAGE},
        ("id",),
    ),
    Tool(
        "board_post",
        "掲示板に書く",
        "スレッドに書き込む（誰でも読める）。",
        _board_post,
        {"thread": {"type": "integer"}, "body": {"type": "string"}},
        ("thread", "body"),
        read_only=False,
    ),
    Tool(
        "board_new_thread",
        "スレッドを立てる",
        "掲示板にスレッドを立てる（誰でも読める）。",
        _board_new_thread,
        {"title": {"type": "string"}, "body": {"type": "string"}},
        ("title", "body"),
        read_only=False,
    ),
    Tool(
        "found",
        "建国",
        "ランダムな形の島と勧められた首都で建国する（1人1島。島がないときだけ）。"
        "resources は地下資源 2 つまで（stone・iron_ore・coal など。鋼鉄には iron_ore と coal）。",
        _found,
        {
            "name": {"type": "string", "description": "国名"},
            "resources": {"type": "array", "items": {"type": "string"}},
            "politics": {"type": "string", "enum": ["democracy", "authoritarian", "totalitarian"]},
            "head_of_state": {"type": "string", "enum": ["monarchy", "republic"]},
            "economy": {"type": "string", "enum": ["market", "mixed", "planned"]},
            "capital": {"type": "string", "description": "首都の名前"},
            "currency": {"type": "string", "description": "通貨の名前"},
            "currency_symbol": {"type": "string"},
            "leader": {"type": "string", "description": "元首の名前"},
            "place_style": {"type": "string", "description": "地名の作風"},
            "seed": {"type": "integer", "description": "形の乱数の種"},
            "cell": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "世界マップの区画 [x, y]（省くと空きから選ぶ）",
            },
        },
        ("name",),
        read_only=False,
    ),
    Tool(
        "api_get",
        "API を読む",
        "ほかの道具にない JSON API を読む（GET。/api/ で始まる場所だけ）。例: "
        "/api/islands/4/facilities/12/book（施設の帳簿の推移）、/api/world/trade（世界の貿易）。",
        _api_get,
        {"path": {"type": "string", "description": "/api/ で始まる場所（?の後ろも書ける）"}},
        ("path",),
    ),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# 人が見る画面（URL の名前）と、同じ中身を読み・同じことをする道具。画面を足したら、ここと道具を
# 合わせる（tests/server/test_ai_parity.py が、画面がすべて載っているか・道具があるか・画面の中身が
# 道具の答えにあるかを確かめる）。島の統計の画面（island_<page>）は report の page=<page>。
SCREENS: dict[str, tuple[str, ...]] = {
    "top": ("world",),
    "found": ("found",),
    "world": ("world", "api_get"),  # 世界の貿易は api_get /api/world/trade
    "rankings": ("rankings",),
    "help": ("guide",),
    "island": ("summary", "map", "tile"),  # 地図の重ね表示は map の overlay
    "turn_log": ("turn_log", "changes"),
    "island_economy": ("report", "stats"),
    "island_market": ("report",),
    "island_chain": ("report",),
    "island_facilities": ("report", "api_get"),  # 施設の帳簿の推移は api_get
    "island_fiscal": ("report",),
    "island_finance": ("report",),
    "island_trade": ("report",),
    "island_fx": ("report",),
    "island_politics": ("report",),
    "island_plan": ("report",),
    "board": ("board",),
    "board_new": ("board_new_thread",),
    "board_thread": ("board_thread", "board_post"),
    "letters": ("letters",),
    "letters_sent": ("letters",),
    "letter_new": ("send_letter",),
    "letter": ("read_letter",),
}
# AI の入り口が要らない画面（アカウントの画面。鍵は人が画面で作って AI に渡す）
NOT_FOR_AI = {"signup", "login", "logout", "api_keys"}


# --- 文にする ---------------------------------------------------------------------------


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _html_text(page: str) -> str:
    """HTML を読みやすい文字にする（script と style は捨てる。tools/playtest/hk.py と同じ）。"""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|table|section|dt|dd)>", "\n", page)
    page = re.sub(r"(?i)</t[dh]>", "\t", page)
    page = re.sub(r"<[^>]+>", "", page)
    lines = [" ".join(html.unescape(line).split()) for line in page.splitlines()]
    return "\n".join(line for line in lines if line)


def _text(result: Any) -> str:
    text = result if isinstance(result, str) else _dump(result)
    if len(text) > TEXT_MAX:
        text = (
            text[:TEXT_MAX]
            + f"\n…（長いので {TEXT_MAX} 字で切りました。範囲を絞って読んでください）"
        )
    return text


# --- JSON-RPC ---------------------------------------------------------------------------


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _call_tool(request: HttpRequest, params: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS_BY_NAME[params["name"]]
    args = params.get("arguments") or {}
    try:
        missing = [k for k in tool.required if k not in args]
        if missing:
            raise ToolError(f"{'・'.join(missing)} が要ります")
        text, is_error = _text(tool.run(request, args)), False
    except ToolError as e:
        text, is_error = str(e), True
    except (KeyError, TypeError, ValueError) as e:
        text, is_error = f"引数の形が正しくありません（{e}）", True
    except Exception:
        logger.exception("MCP の道具 %s でエラー", tool.name)
        text, is_error = "サーバーの中でエラーが起きました", True
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(request: HttpRequest, message: Any) -> dict[str, Any] | None:
    """JSON-RPC の1通に答える。通知（id のないもの）と、相手からの答えには None。"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        id_ = message.get("id") if isinstance(message, dict) else None
        return _error(id_, -32600, "JSON-RPC 2.0 の形ではありません")
    if "method" not in message:  # 相手からの答え（こちらからは何も頼まないので捨てる）
        return None
    id_ = message.get("id")
    method = message["method"]
    params = message.get("params") or {}
    if "id" not in message:  # 通知（notifications/initialized など）
        return None
    if not isinstance(params, dict):
        return _error(id_, -32602, "params はオブジェクトで送ってください")
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(
            id_,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": [t.spec() for t in TOOLS]})
    if method == "tools/call":
        if params.get("name") not in TOOLS_BY_NAME:
            return _error(id_, -32602, f"道具 {params.get('name')} はありません")
        if not isinstance(params.get("arguments") or {}, dict):
            return _error(id_, -32602, "arguments はオブジェクトで送ってください")
        return _result(id_, _call_tool(request, params))
    return _error(id_, -32601, f"{method} には答えられません")


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, safe=False, json_dumps_params={"ensure_ascii": False})


def _bad_origin(request: HttpRequest) -> bool:
    """ブラウザから別のサイトの名前で来た呼び出し（DNS の付け替えを使った攻撃を防ぐ）。"""
    origin = request.META.get("HTTP_ORIGIN")
    if not origin:
        return False
    return urlsplit(origin).netloc != request.get_host()


@csrf_exempt
def endpoint(request: HttpRequest) -> HttpResponse:
    """POST /mcp（Streamable HTTP。GET の SSE とセッションは使わないので、ほかの方法は 405）。"""
    if request.method != "POST":
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response
    if _bad_origin(request):
        return _json(_error(None, -32600, "この Origin からは使えません"), status=403)
    version = request.META.get("HTTP_MCP_PROTOCOL_VERSION")
    if version and version not in PROTOCOL_VERSIONS:
        return _json(_error(None, -32600, f"MCP の版 {version} には対応していません"), status=400)
    if getattr(request, "api_key", None) is None:
        response = _json(
            _error(None, -32001, "AI 用の鍵が要ります（Authorization: Bearer <鍵>）"), status=401
        )
        response["WWW-Authenticate"] = 'Bearer realm="hakoniwa"'
        return response
    try:
        message = json.loads(request.body or b"")
    except ValueError:
        return _json(_error(None, -32700, "JSON として読めません"), status=400)
    if isinstance(message, list):  # まとめて送る形（2025-03-26 の版）
        answers = [a for a in (handle(request, x) for x in message) if a is not None]
        return _json(answers) if answers else HttpResponse(status=202)
    answer = handle(request, message)
    if answer is None:
        return HttpResponse(status=202)
    return _json(answer)
