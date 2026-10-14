"""試し遊びのための小さな道具（標準ライブラリだけで動きます）。

ブラウザを使わずに、登録・ログイン・建国・画面や API の読み取り・命令の確かめと並べ替えをします。
使い方は docs/playtest.md の「道具」を見てください。

    python tools/playtest/hk.py --base http://127.0.0.1:8000 login codex <パスワード>
    python tools/playtest/hk.py get /api/islands/4/economy
    python tools/playtest/hk.py map 4

ログインの cookie は HK_COOKIES（既定はホームの .hk-cookies.txt）に置きます。
リポジトリには置きません。
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = os.environ.get("HK_BASE", "http://127.0.0.1:8000")
COOKIES = Path(os.environ.get("HK_COOKIES", Path.home() / ".hk-cookies.txt"))


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.MozillaCookieJar(str(COOKIES))
        if COOKIES.exists():
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def save(self) -> None:
        COOKIES.parent.mkdir(parents=True, exist_ok=True)
        self.jar.save(ignore_discard=True, ignore_expires=True)
        COOKIES.chmod(0o600)

    def csrf(self) -> str:
        for c in self.jar:
            if c.name == "csrftoken":
                return c.value
        return ""

    def request(self, method: str, path: str, data: bytes | None = None, ctype: str = ""):
        url = self.base + path
        req = urllib.request.Request(url, data=data, method=method)
        if method != "GET":
            req.add_header("X-CSRFToken", self.csrf())
            req.add_header("Referer", url)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with self.opener.open(req) as res:
                body = res.read().decode("utf-8")
                status = res.status
                final = res.geturl()
        except urllib.error.HTTPError as e:
            body, status, final = e.read().decode("utf-8", "replace"), e.code, url
        self.save()
        return status, body, final

    def get(self, path: str):
        return self.request("GET", path)

    def json(self, method: str, path: str, value) -> tuple[int, str]:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        status, body, _ = self.request(method, path, data, "application/json")
        return status, body

    def form(self, path: str, fields: dict[str, str]):
        """フォームの画面を開いて csrf の cookie を受け取ってから送る。"""
        self.get(path)
        fields = {"csrfmiddlewaretoken": self.csrf(), **fields}
        data = urllib.parse.urlencode(fields).encode()
        return self.request("POST", path, data, "application/x-www-form-urlencoded")


def text_of(page: str) -> str:
    """HTML を読みやすい文字にする（script と style は捨てる）。"""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|table|section)>", "\n", page)
    page = re.sub(r"(?i)</t[dh]>", "\t", page)
    page = re.sub(r"<[^>]+>", "", page)
    lines = [" ".join(html.unescape(line).split()) for line in page.splitlines()]
    return "\n".join(line for line in lines if line)


def errors_in(page: str) -> str:
    found = re.findall(r'(?is)<ul class="errorlist[^"]*">(.*?)</ul>', page)
    return " / ".join(text_of(f).replace("\n", " ") for f in found)


def show(status: int, body: str) -> int:
    try:
        print(json.dumps(json.loads(body), ensure_ascii=False, indent=1))
    except ValueError:
        print(body)
    return 0 if status < 400 else 1


def cmd_signup(c: Client, a) -> int:
    _, body, _ = c.get("/accounts/signup/")
    if "すでに登録されています" in body:
        print(
            "この接続元からは登録できません（同じ接続元の登録の上限）。docs/playtest.md の 2 章へ"
        )
        return 1
    fields = {"username": a.user, "password1": a.password, "password2": a.password}
    _, body, final = c.form("/accounts/signup/", fields)
    if final.rstrip("/").endswith("/found"):
        print("登録しました。次は found で建国します。")
        return 0
    print("登録できませんでした:", errors_in(body) or text_of(body)[:500])
    return 1


def cmd_login(c: Client, a) -> int:
    user = a.user or os.environ.get("HK_USER", "")
    password = a.password or os.environ.get("HK_PASSWORD", "")
    status, body, final = c.form("/accounts/login/", {"username": user, "password": password})
    if "/accounts/login" in final:
        print("ログインできませんでした:", errors_in(body) or "名前かパスワードが違います")
        return 1
    print(f"ログインしました（{user}）。")
    return 0


def cmd_found(c: Client, a) -> int:
    """ランダムな形と、勧められた首都で建国する。"""
    seed = a.seed if a.seed is not None else random.randrange(2**31)
    c.get("/found/")  # csrf の cookie
    status, body = c.json("POST", "/api/founding/random-shape", {"seed": seed})
    if status >= 400:
        print("形を作れませんでした（ログインしていますか）:", body[:300])
        return 1
    land = json.loads(body)["land"]
    regime = {"politics": a.politics, "head_of_state": a.head, "economy": a.economy}
    status, body = c.json(
        "POST", "/api/founding/terrain", {"land": land, "seed": seed, "regime": regime}
    )
    info = json.loads(body)
    if info["problems"] or not info["suggested_capital"]:
        print("形に問題があります:", info["problems"], "（--seed を変えてやり直してください）")
        return 1
    status, body, _ = c.request("GET", "/api/founding/cells")
    free = json.loads(body)["free"]
    if not free:
        print("世界マップに空いた区画がありません。")
        return 1
    cell = a.cell or random.Random(seed).choice(free)
    names = {
        "country": a.name,
        "capital": a.capital,
        "currency": a.currency,
        "currency_symbol": a.symbol,
        "leader": a.leader,
    }
    plan = {
        "land": land,
        "capital": info["suggested_capital"],
        "regime": regime,
        "names": names,
        "resources": [r for r in a.resources.split(",") if r],
        "edits": [],
        "seed": seed,
        "place_style": a.place_style,
        "cell": cell,
    }
    status, body = c.json("POST", "/api/founding", plan)
    data = json.loads(body)
    if status >= 400:
        print("建国できませんでした:", data.get("problems"))
        return 1
    print(f"建国しました: {data['url']}（陸 {len(land)} マス、首都 {plan['capital']}、種 {seed}）")
    return 0


def cmd_get(c: Client, a) -> int:
    status, body, _ = c.get(a.path)
    return show(status, body)


def cmd_page(c: Client, a) -> int:
    status, body, _ = c.get(a.path)
    text = text_of(body)
    if a.find:
        i = text.find(a.find)
        text = text[i:] if i >= 0 else f"（「{a.find}」は見つかりませんでした）"
    print(text[: a.length])
    return 0 if status < 400 else 1


def load_value(arg: str):
    """JSON そのものか、@ファイル名。"""
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    return json.loads(arg)


def cmd_check(c: Client, a) -> int:
    c.get("/")  # csrf の cookie
    status, body = c.json("POST", f"/api/islands/{a.island}/orders/check", load_value(a.order))
    return show(status, body)


def cmd_orders(c: Client, a) -> int:
    if a.orders is None:
        status, body, _ = c.get(f"/api/islands/{a.island}/orders")
        return show(status, body)
    value = load_value(a.orders)
    if isinstance(value, list):
        value = {"orders": value}
    c.get("/")
    status, body = c.json("PUT", f"/api/islands/{a.island}/orders", value)
    return show(status, body)


def cmd_map(c: Client, a) -> int:
    """島の文字の地図と、施設の一覧。"""
    status, body, _ = c.get(f"/api/islands/{a.island}/map")
    if status >= 400:
        return show(status, body)
    data = json.loads(body)
    status, body, _ = c.get("/api/defs")
    defs = json.loads(body)
    size = data.get("size", 32)
    terrain = data.get("terrain") or []
    marks = {"sea": "~", "shallows": "-", "plain": ".", "forest": "T", "mountain": "^"}
    grid = [[marks.get(terrain[y * size + x], "?") for x in range(size)] for y in range(size)]
    facilities = []
    for f in data.get("facilities") or []:
        if f["kind"] == "housing":
            grid[f["y"]][f["x"]] = "#"
        else:
            grid[f["y"]][f["x"]] = "F" if f.get("built", True) else "f"
            facilities.append(f)
    print("    " + "".join(str(x % 10) for x in range(size)))
    for y, row in enumerate(grid):
        print(f"{y:>3} " + "".join(row))
    print("~ 海  - 浅瀬  . 平地  T 森  ^ 山  # 住宅  F 施設  f 工事中")
    for f in facilities:
        name = defs["facilities"].get(f["kind"], {}).get("name", f["kind"])
        state = f.get("status") or ""
        if not f.get("built", True):
            state = "工事中"
        print(f"({f['x']},{f['y']}) {name} {state} 稼働 {f.get('rate', 0)}".rstrip())
    return 0


def cmd_advance(c: Client, a) -> int:
    """管理画面の「今すぐ1ターン進める」を n 回押す（管理者の権限が要る）。"""
    for _ in range(a.turns):
        _, body, final = c.get("/admin/world/world/")
        if "/admin/login" in final:
            print("管理画面に入れません（管理者の権限のあるアカウントでログインしてください）。")
            return 1
        ids = re.findall(r'name="_selected_action" value="(\d+)"', body)
        if not ids:
            print("世界が見つかりません。")
            return 1
        fields = {"action": "advance_turn", "_selected_action": ids[0], "index": "0"}
        fields = {"csrfmiddlewaretoken": c.csrf(), **fields}
        data = urllib.parse.urlencode(fields).encode()
        _, body, _ = c.request(
            "POST", "/admin/world/world/", data, "application/x-www-form-urlencoded"
        )
        found = re.findall(r'(?s)<ul class="messagelist">(.*?)</ul>', body)
        message = text_of(found[0]).replace("\n", " ") if found else "結果が読めませんでした"
        print(message)
        if "error" in (found[0] if found else "error"):
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="経済版箱庭諸国の試し遊びの道具")
    p.add_argument(
        "--base", default=DEFAULT_BASE, help="サーバーの URL（既定は HK_BASE か 127.0.0.1:8000）"
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("signup", help="アカウントを作る（作るとログインした状態になる）")
    s.add_argument("user")
    s.add_argument("password")
    s.set_defaults(func=cmd_signup)

    s = sub.add_parser("login", help="ログインする（引数を省くと HK_USER と HK_PASSWORD）")
    s.add_argument("user", nargs="?", default="")
    s.add_argument("password", nargs="?", default="")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("found", help="ランダムな形と勧められた首都で建国する")
    s.add_argument("--name", required=True, help="国名")
    s.add_argument("--capital", default="首都")
    s.add_argument("--currency", default="クレジット")
    s.add_argument("--symbol", default="Cr")
    s.add_argument("--leader", default="元首")
    s.add_argument(
        "--resources", default="iron_ore,coal", help="地下資源（2つまで。stone,iron_ore,coal）"
    )
    s.add_argument("--politics", default="democracy", help="democracy|authoritarian|totalitarian")
    s.add_argument("--head", default="republic", help="monarchy|republic")
    s.add_argument("--economy", default="market", help="market|mixed|planned")
    s.add_argument("--place-style", default="japanese")
    s.add_argument("--seed", type=int)
    s.add_argument("--cell", type=int, nargs=2, help="世界マップの区画（省くと空きからランダム）")
    s.set_defaults(func=cmd_found)

    s = sub.add_parser("get", help="API を読む（JSON を整えて出す）")
    s.add_argument("path")
    s.set_defaults(func=cmd_get)

    s = sub.add_parser("page", help="画面を文字にして読む")
    s.add_argument("path")
    s.add_argument("find", nargs="?", default="", help="この文字から読み始める")
    s.add_argument("length", nargs="?", type=int, default=4000)
    s.set_defaults(func=cmd_page)

    s = sub.add_parser("check", help='命令を1つ確かめる（{"kind":..,"payload":..} か @ファイル）')
    s.add_argument("island", type=int)
    s.add_argument("order")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser(
        "orders", help="命令の列を見る。JSON の一覧か @ファイルを渡すと丸ごと置き換える"
    )
    s.add_argument("island", type=int)
    s.add_argument("orders", nargs="?")
    s.set_defaults(func=cmd_orders)

    s = sub.add_parser("map", help="島の文字の地図と施設の一覧")
    s.add_argument("island", type=int)
    s.set_defaults(func=cmd_map)

    s = sub.add_parser(
        "advance", help="ターンを進める（管理者の権限が要る。A では run_turn でもよい）"
    )
    s.add_argument("turns", nargs="?", type=int, default=1)
    s.set_defaults(func=cmd_advance)

    a = p.parse_args(argv)
    return a.func(Client(a.base), a)


if __name__ == "__main__":
    sys.exit(main())
