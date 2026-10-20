"""試し遊びのための小さな道具（標準ライブラリだけで動きます）。

ブラウザを使わずに、登録・ログイン・建国・画面や API の読み取り・命令の確かめと並べ替えをします。
使い方は docs/playtest.md の「道具」と「AI 用の入り口」を見てください。

    python tools/playtest/hk.py --base http://127.0.0.1:8000 key   # AI 用の鍵を覚えさせる
    python tools/playtest/hk.py summary                            # 自分の国の要約
    python tools/playtest/hk.py changes                            # 前に読んだターンからの差分
    python tools/playtest/hk.py get api/islands/4/economy
    python tools/playtest/hk.py map 4
    python tools/playtest/hk.py send 5 'ごあいさつ' 'はじめまして'
    python tools/playtest/hk.py mcp                                       # MCP の stdio のつなぎ

AI 用の鍵（アカウントの画面 /accounts/keys/ で作る）は HK_KEY か、`key` で覚えさせたファイル
（HK_KEY_FILE。既定はホームの .hk-key）から読み、/api/ と /mcp の呼び出しに付けます。鍵があれば
ログインは要りません。鍵がなければ、ログインの cookie（HK_COOKIES。既定はホームの
.hk-cookies.txt）を使います。前に読んだターンは HK_STATE（既定はホームの .hk-state.json）に
置きます（summary と changes が覚える。--json のときも同じ）。どれもリポジトリには置きません。
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
KEY_FILE = Path(os.environ.get("HK_KEY_FILE", Path.home() / ".hk-key"))
STATE = Path(os.environ.get("HK_STATE", Path.home() / ".hk-state.json"))
KEY_PATHS = ("/api/", "/mcp")  # 鍵が効く場所（サーバーの server/accounts/keys.py と同じ）


def load_key() -> str:
    """AI 用の鍵（HK_KEY か、key で覚えさせたファイル）。なければ空。"""
    key = os.environ.get("HK_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    return key


def fix_path(path: str) -> str:
    """先頭の / を補う。Windows の Git Bash が /api/... を C:/Program Files/Git/api/... に書き換えた
    ものは、/api/ や /islands/ から後ろを使う。"""
    if re.match(r"^[A-Za-z]:[/\\]", path):
        found = re.search(
            r"/(api|islands|letters|board|accounts|help)(/|$)", path.replace("\\", "/")
        )
        if found:
            return path.replace("\\", "/")[found.start() :]
    return path if path.startswith("/") else "/" + path


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.key = load_key()
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
        path = fix_path(path)
        url = self.base + path
        req = urllib.request.Request(url, data=data, method=method)
        if self.key and path.startswith(KEY_PATHS):
            req.add_header("Authorization", f"Bearer {self.key}")
        elif method != "GET":
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
        # 稼働率は 1000 分の 1 で来る（MCP の地図と同じく % で出す）
        rate = f"稼働 {round(f.get('rate', 0) / 10)}%" if f.get("built", True) else ""
        print(" ".join(p for p in (f"({f['x']},{f['y']})", name, state, rate) if p))
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


# --- 手紙と掲示板 --------------------------------------------------------------
# 出力は人が読める短い形にする。--json を付けると API の JSON をそのまま出す。


def fail(status: int, body: str) -> int:
    """API の誤りを1行で出す。"""
    try:
        text = json.loads(body).get("error") or body
    except (ValueError, AttributeError):
        if status == 403 and "CSRF" in body:
            text = "CSRF の印がありません（ログインしていないかもしれません。login し直す）"
        else:
            text = text_of(body)[:300]
    print(f"エラー（{status}）: {text}")
    return 1


def when(stamp: str | None) -> str:
    """ISO の時刻を「10-15 20:03」の形にする。"""
    return stamp[5:16].replace("T", " ") if stamp else ""


def island_label(island: dict | None) -> str:
    return f"{island['name']}（島 {island['id']}）" if island else ""


def who(item: dict) -> str:
    """掲示板の書いた人（島があれば国名と島の番号も）。"""
    name = item.get("author") or "退会した人"
    island = item.get("island")
    return f"{name}（{island['name']}・島 {island['id']}）" if island else name


def read_text(arg: str | None, path: str | None) -> str:
    """本文: 引数か、-f のファイル（- は標準入力）。どちらもなければ標準入力から読む。"""
    if arg is not None and path:
        sys.exit("本文は引数か -f のどちらかで渡してください。")
    if path:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    if arg is not None:
        return arg
    if sys.stdin.isatty():
        print("本文を入力し、最後に Ctrl-D を押してください。", file=sys.stderr)
    return sys.stdin.read()


def cmd_letters(c: Client, a) -> int:
    box = "sent" if a.sent else "inbox"
    status, body, _ = c.get(f"/api/letters?box={box}&page={a.page}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    title = "送った手紙" if a.sent else "受信箱"
    print(
        f"{island_label(data['island'])}の{title}（{data['page']}/{data['pages']} ページ、"
        f"{data['count']} 通、届いた手紙の未読 {data['unread']} 通）"
    )
    for x in data["letters"]:
        mark = "既読" if x["read"] else "未読"
        head = f"#{x['id']} [{mark}] ターン {x['turn']} {when(x['sent_at'])}"
        if a.sent:
            print(f"{head} → {island_label(x['to'])}: {x['subject']}")
        else:
            print(f"{head} {island_label(x['from'])}・{x['author']}: {x['subject']}")
    if not data["letters"]:
        print("（手紙はありません）")
    return 0


def cmd_letter(c: Client, a) -> int:
    status, body, _ = c.get(f"/api/letters/{a.number}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    x = json.loads(body)
    read = f"既読（{when(x['read_at'])}）" if x["read"] else "未読"
    print(f"#{x['id']} {x['subject']}")
    print(f"差出人: {island_label(x['from'])}・{x['author']} → 宛先: {island_label(x['to'])}")
    print(f"ターン {x['turn']}・{when(x['sent_at'])}・{read}")
    print("----")
    print(x["body"])
    return 0


def cmd_send(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    value = {"to": a.island, "subject": a.subject, "body": text}
    status, body = c.json("POST", "/api/letters", value)
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    x = json.loads(body)
    print(f"送りました: #{x['id']} → {island_label(x['to'])}: {x['subject']}（ターン {x['turn']}）")
    return 0


def cmd_board(c: Client, a) -> int:
    status, body, _ = c.get(f"/api/board/threads?page={a.page}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    print(f"掲示板（{data['page']}/{data['pages']} ページ、{data['count']} スレッド）")
    for t in data["threads"]:
        print(
            f"#{t['id']} {t['title']}（{t['posts']} 件、最後 {when(t['updated_at'])}）"
            f" 立てた人 {who(t)}"
        )
    if not data["threads"]:
        print("（スレッドはありません）")
    return 0


def cmd_thread(c: Client, a) -> int:
    status, body, _ = c.get(f"/api/board/threads/{a.number}?page={a.page}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    t = data["thread"]
    print(f"#{t['id']} {t['title']}（{data['page']}/{data['pages']} ページ、{data['count']} 件）")
    for p in data["posts"]:
        print(f"--- #{p['id']} {who(p)} {when(p['created_at'])}")
        print(p["body"])
    return 0


def cmd_post(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    status, body = c.json("POST", f"/api/board/threads/{a.thread}/posts", {"body": text})
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    p = json.loads(body)
    print(f"書き込みました: スレッド #{p['thread']} の #{p['id']}")
    return 0


def cmd_new_thread(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    status, body = c.json("POST", "/api/board/threads", {"title": a.title, "body": text})
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    t = json.loads(body)["thread"]
    print(f"スレッドを立てました: #{t['id']} {t['title']}")
    return 0


# --- AI 用の入り口（鍵・自分・国の要約・差分・ターンログ・MCP） ----------------------------


def cmd_key(c: Client, a) -> int:
    """AI 用の鍵を HK_KEY_FILE に覚えさせる（引数か標準入力から）。--forget で消す。"""
    if a.forget:
        KEY_FILE.unlink(missing_ok=True)
        print(f"鍵を消しました（{KEY_FILE}）。")
        return 0
    key = (a.value or "").strip()
    if not key:
        if sys.stdin.isatty():
            print("鍵（hk_ で始まる）を貼り付けて Enter を押してください。", file=sys.stderr)
        key = sys.stdin.readline().strip()
    if not key.startswith("hk_"):
        print("鍵は hk_ で始まります（アカウントの画面 /accounts/keys/ で作ります）。")
        return 1
    c.key = key
    status, body, _ = c.get("/api/me")
    if status >= 400:
        return fail(status, body)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key + "\n", encoding="utf-8")
    KEY_FILE.chmod(0o600)
    me = json.loads(body)
    island = me["island"]
    where = f"島 {island['id']} {island['name']}" if island else "島はまだありません"
    print(f"鍵を覚えました（{KEY_FILE}）。アカウント {me['user']}、{where}。")
    return 0


def cmd_new_key(c: Client, a) -> int:
    """ログインした状態（login か signup の後）で AI 用の鍵を作り、HK_KEY_FILE に覚えさせる。

    ブラウザのアカウントの画面（/accounts/keys/）で「作る」を押すのと同じ。
    """
    _, body, final = c.form("/accounts/keys/", {"action": "create", "name": a.name})
    if "/accounts/login" in final:
        print("ログインしていません。先に login してください。")
        return 1
    found = re.search(r'<code id="new-key">(hk_[^<]+)</code>', body)
    if not found:
        errors = re.findall(r'<p class="error">(.*?)</p>', body)
        print(
            "鍵を作れませんでした:", " / ".join(map(html.unescape, errors)) or text_of(body)[:300]
        )
        return 1
    a.value, a.forget = found.group(1), False
    return cmd_key(c, a)


def own_island(c: Client) -> int:
    status, body, _ = c.get("/api/me")
    if status >= 400:
        sys.exit(f"自分が分かりません（{status}）。key で鍵を覚えさせるか、login してください。")
    island = json.loads(body).get("island")
    if not island:
        sys.exit("まだ島がありません。found で建国してください。")
    return island["id"]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_turn(c: Client, island: int, turn: int) -> None:
    """summary と changes で読んだターンを覚える（次の changes の since の既定）。"""
    state = load_state()
    state[f"{c.base}|{island}"] = turn
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def cmd_me(c: Client, a) -> int:
    status, body, _ = c.get("/api/me")
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    me = json.loads(body)
    print(f"アカウント {me['user'] or '（ログインしていない）'}・{me['date']}")
    if me["key"]:
        k = me["key"]
        print(f"AI 用の鍵 {k['prefix']}… {k['name']}（1 分に {k['calls_per_minute']} 回まで）")
    if me["island"]:
        i = me["island"]
        mark = "・AI の印あり" if i["ai"] else ""
        print(f"島 {i['id']} {i['name']}{mark}・未読の公電 {me.get('unread_letters', 0)} 通")
    return 0


def _rows(items: list[dict], fmt) -> str:
    return " / ".join(fmt(x) for x in items)


def _good(g: dict) -> str:
    change = f"({g['change']})" if g.get("change") else ""
    return (
        f"{g['name']} {g.get('price', '-')}{change} 売買 {g.get('volume', 0)}"
        f" 買えず {g.get('unmet', 0)} 生産 {g['production']} 国の倉庫 {g['stock']}"
    )


def _bond(b: dict) -> str:
    return f"#{b['number']} {b['term']} 満期 {b['maturity']} 残高 {b['outstanding']} {b['rate']}"


def _result(x: dict) -> str:
    done = "実行" if x["status"] == "done" else "失敗"
    why = f"（{x['reason']}）" if x["reason"] else ""
    return f"ターン {x['turn']} {done}: {x['text']}{why}"


def _indicator(r: dict) -> str:
    before = f"（前 {r['before']}）" if "before" in r else ""
    return f"{r['label']} {r['now']}{before}"


def print_summary(d: dict) -> None:
    i, p, f = d["island"], d["politics"], d["facilities"]
    mark = "・AI" if i["ai"] else ""
    owner = i["owner"] or "持ち主なし"
    print(f"{i['name']}（島 {i['id']}・{owner}{mark}）{d['date']}・統計はターン {d['stats_turn']}")
    resources = "・".join(i["resources"]) or "なし"
    regime = "・".join(i["regime"].values())
    print(f"体制: {regime} / 地下資源: {resources} / 気候: {i['climate'] or '-'}")
    print("[指標] " + _rows(d["indicators"], _indicator))
    election = f" 次の選挙 ターン {p['next_election']}" if p.get("next_election") else ""
    numbers = f" 政情 {p['stability']} 支持率 {p['support']}" if p["stability"] is not None else ""
    migration = p.get("migration")
    moves = f" 移民 {migration['immigration']}・出国 {migration['emigration']}" if migration else ""
    print(f"[政情] {p['level_name']}{numbers}{election}{moves}")
    s = d.get("strengths") or {}
    if s.get("strengths") or s.get("titles"):
        ranks = [
            f"{x['name']}" + (f"（世界 {x['rank']} 位）" if x.get("rank") else "")
            for x in s.get("strengths", [])
        ]
        titles = f" 称号: {'・'.join(s['titles'])}" if s.get("titles") else ""
        print(f"[得意産業] {'・'.join(ranks) or 'なし'}{titles}")
    for text in d["alerts"]:
        print(f"[注意] {text}")
    why = f"（{f['infra_why']}）" if f["infra_why"] else ""
    print(
        f"[施設] {f['facilities']} か所、止まった施設 {f['stopped_count']}、"
        f"工事中 {f['construction_count']}、インフラの工事の列 {f['infra_works']}{why}"
    )
    for x in f["stopped"]:
        print(f"  止まった ({x['x']},{x['y']}) #{x['number']} {x['name']} {x['status_text']}")
    if f["partial"]:
        partial = _rows(f["partial"], lambda r: f"{r['status_text']} {r['count']}")
        print(f"  満たない稼働のわけ: {partial}")
    for x in f["construction"]:
        print(
            f"  工事 ({x['x']},{x['y']}) {x['kind_name']} {x['progress']}/{x['turns']} ターン"
            f" {x['status_text']}"
        )
    print("[財] " + _rows(d["goods"], _good))
    pc = d.get("population_changes")
    if pc:
        labels = pc["labels"]
        print("[人口の増減] " + "、".join(f"{labels[k]} {pc[k]:,}" for k in labels))
    sv = d.get("services")
    if sv:
        names = {"workers": "働く人", "seats": "次に雇う目安", "price": "値段", "sales": "売上"}
        names |= {"sold": "売れた量", "unused": "売れ残り"}
        parts = [f"{label} {sv[f'{k}_text']}" for k, label in names.items() if k in sv]
        print("[商業・サービス業] " + "、".join(parts))
    fm = d.get("firms")
    if fm:
        print(
            f"[企業] 民営 {fm['count']:g}、施設が2つ以上 {fm['groups']:g}、"
            f"一番大きい企業の施設の割合 {fm['largest_share']}%"
        )
        for x in fm.get("list", []):
            money = "、".join(
                f"{label} {x[k]}"
                for k, label in (("cash", "預金"), ("profit", "利益"), ("dividend", "配当"))
                if k in x
            )
            print(f"  企業 #{x['firm']} {x['name']}（{x['owner_name']}）施設 {x['count']} {money}")
    rt = d.get("retail")
    if rt:
        parts = [
            f"{label} {rt[k + '_text']}"
            for k, label in (
                ("need", "要る量"),
                ("sold", "商店と百貨店"),
                ("direct", "小さな店と屋台"),
                ("direct_sales", "その額"),
            )
            if k + "_text" in rt
        ]
        app = rt.get("store_application")
        wait = ""
        if app:
            state = "許した" if app["approved"] else "許すのを待つ"
            wait = f"、百貨店の出店の申請 ({app['x']},{app['y']}) {state}"
        print(
            f"[小売り] 商店 {rt['shops']}、百貨店 {rt['department_stores']}、"
            f"出店の規制 {rt['large_store_text']}{wait}"
        )
        if parts:
            print("  " + "、".join(parts))
    if d["bonds_due"]:
        print("[満期の近い国債] " + _rows(d["bonds_due"], _bond))
    for x in d.get("losing", []):
        print(f"[赤字の施設] ({x['x']},{x['y']}) #{x['number']} {x['name']} {x['profit_total']}")
    o = d.get("orders")
    if o:
        print(f"[命令] 行政力 {o['admin_power']}、列 {len(o['pending'])} 件")
        for x in o["pending"]:
            print(f"  待ち: {x['text']}")
        if o["last_results"]:
            print(f"  直近に命令を実行したターン {o['last_turn']} の結果:")
        for x in o["last_results"]:
            print(f"  {_result(x)}")
    if "letters" in d:
        print(f"[公電] 未読 {d['letters']['unread']} 通")
        for x in d["letters"]["latest_unread"]:
            print(f"  #{x['id']} ターン {x['turn']} {x['from']}: {x['subject']}")
    print(f"[出来事 ターン {d['events_turn']}]")
    for e in d["events"]:
        print(f"  {e['text']}")


def cmd_summary(c: Client, a) -> int:
    island = a.island or own_island(c)
    status, body, _ = c.get(f"/api/islands/{island}/summary")
    if status >= 400:
        return show(status, body) if a.json else fail(status, body)
    d = json.loads(body)
    remember_turn(c, island, d["turn"])  # --json でも覚える（Codex の試し遊び）
    if a.json:
        return show(status, body)
    print_summary(d)
    return 0


def cmd_changes(c: Client, a) -> int:
    island = a.island or own_island(c)
    since = a.since
    if since is None:
        since = load_state().get(f"{c.base}|{island}")
    query = f"?since={since}" if since is not None else ""
    status, body, _ = c.get(f"/api/islands/{island}/changes{query}")
    if status >= 400:
        return show(status, body) if a.json else fail(status, body)
    d = json.loads(body)
    remember_turn(c, island, d["turn"])  # --json でも覚える（Codex の試し遊び）
    if a.json:
        return show(status, body)
    print(f"島 {d['island']}: ターン {d['since']} より後（いま {d['date']}）")
    if d["truncated"]:
        print(f"（ターンログはターン {d['log_from']} から。前は log --from で読む）")
    turn = None
    for e in d["log"]:
        if e["turn"] != turn:
            turn = e["turn"]
            print(f"[ターン {turn}]")
        print(f"  {e['text']}")
    if d["indicators"]:
        print(
            "[指標] " + _rows(d["indicators"], lambda r: f"{r['label']} {r['before']} → {r['now']}")
        )
    for x in d["orders"]:
        print(f"[命令] {_result(x)}")
    for x in d.get("letters", []):
        mark = "既読" if x["read"] else "未読"
        print(f"[公電] #{x['id']} [{mark}] ターン {x['turn']} {x['from']['name']}: {x['subject']}")
    if not (d["log"] or d["indicators"] or d["orders"] or d.get("letters")):
        print("（新しいことはありません）")
    return 0


def cmd_log(c: Client, a) -> int:
    island = a.island or own_island(c)
    query = "&".join(f"{k}={v}" for k, v in (("from", a.start), ("to", a.end)) if v is not None)
    status, body, _ = c.get(f"/api/islands/{island}/log" + (f"?{query}" if query else ""))
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    d = json.loads(body)
    turn = None
    for e in d["entries"]:
        if e["turn"] != turn:
            turn = e["turn"]
            print(f"[ターン {turn}]")
        print(f"  {e['text']}")
    if not d["entries"]:
        print("（ターンログはありません）")
    return 0


def cmd_mcp(c: Client, a) -> int:
    """MCP の stdio のつなぎ: 標準入力の JSON-RPC を1行ずつ POST /mcp に送り、答えを標準出力に書く。

    MCP の道具を stdio でしか使えない AI（や、鍵を設定のファイルに書きたくないとき）のため。
    鍵は HK_KEY か key で覚えさせたファイルから読む。
    """
    if not c.key:
        print(
            "AI 用の鍵がありません（key で覚えさせるか、HK_KEY に入れてください）", file=sys.stderr
        )
        return 1
    version = None
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        req = urllib.request.Request(c.base + "/mcp", data=line.encode("utf-8"), method="POST")
        req.add_header("Authorization", f"Bearer {c.key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if version:
            req.add_header("MCP-Protocol-Version", version)
        try:
            with urllib.request.urlopen(req) as res:
                status, body = res.status, res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            status, body = 0, f"サーバーにつながりません: {e.reason}"
        if status == 202 or not body:
            continue
        try:
            answer = json.loads(body)
        except ValueError:
            answer = None
        if not isinstance(message, dict) or "id" not in message:
            if status >= 400:
                print(f"MCP: {status} {body[:300]}", file=sys.stderr)
                continue
        elif (
            status >= 400
            or not isinstance(answer, (dict, list))
            or (isinstance(answer, dict) and "jsonrpc" not in answer)
        ):
            text = answer.get("error") if isinstance(answer, dict) else body[:300]
            answer = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32000, "message": f"{status}: {text}"},
            }
        if isinstance(answer, dict):
            result = answer.get("result") or {}
            if isinstance(result, dict) and result.get("protocolVersion"):
                version = result["protocolVersion"]
        out.write(json.dumps(answer, ensure_ascii=False).encode("utf-8") + b"\n")
        out.flush()
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

    raw = argparse.ArgumentParser(add_help=False)
    raw.add_argument("--json", action="store_true", help="API の JSON をそのまま出す")

    def add_text(s: argparse.ArgumentParser) -> None:
        s.add_argument("body", nargs="?", help="本文（省くと標準入力から読む）")
        s.add_argument("-f", "--file", help="本文のファイル（- は標準入力）")

    s = sub.add_parser("letters", parents=[raw], help="届いた手紙の一覧（--sent で送った手紙）")
    s.add_argument("--sent", action="store_true")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(func=cmd_letters)

    s = sub.add_parser("letter", parents=[raw], help="手紙を1通読む（届いた手紙は既読になる）")
    s.add_argument("number", type=int)
    s.set_defaults(func=cmd_letter)

    s = sub.add_parser("send", parents=[raw], help="島に手紙を送る")
    s.add_argument("island", type=int, help="宛先の島の番号")
    s.add_argument("subject", help="題名")
    add_text(s)
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("board", parents=[raw], help="掲示板のスレッドの一覧")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(func=cmd_board)

    s = sub.add_parser("thread", parents=[raw], help="スレッドの書き込みを読む")
    s.add_argument("number", type=int)
    s.add_argument("--page", default="last", help="ページ（既定は最後のページ）")
    s.set_defaults(func=cmd_thread)

    s = sub.add_parser("post", parents=[raw], help="スレッドに書き込む")
    s.add_argument("thread", type=int)
    add_text(s)
    s.set_defaults(func=cmd_post)

    s = sub.add_parser("new-thread", parents=[raw], help="スレッドを立てる")
    s.add_argument("title", help="題名")
    add_text(s)
    s.set_defaults(func=cmd_new_thread)

    s = sub.add_parser("key", help="AI 用の鍵を覚えさせる（引数か標準入力。--forget で消す）")
    s.add_argument("value", nargs="?", help="鍵（省くと標準入力から読む）")
    s.add_argument("--forget", action="store_true")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("new-key", help="ログインした状態で AI 用の鍵を作り、覚えさせる")
    s.add_argument("name", nargs="?", default="hk.py", help="鍵の名前（何の AI に渡すかのメモ）")
    s.set_defaults(func=cmd_new_key)

    s = sub.add_parser("me", parents=[raw], help="自分（アカウント・鍵・島・ターン）")
    s.set_defaults(func=cmd_me)

    s = sub.add_parser("summary", parents=[raw], help="国の要約（島を省くと自分の島）")
    s.add_argument("island", nargs="?", type=int)
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser(
        "changes",
        parents=[raw],
        help="ターン since より後に起きたこと（省くと前に読んだターンから）",
    )
    s.add_argument("since", nargs="?", type=int)
    s.add_argument("--island", type=int)
    s.set_defaults(func=cmd_changes)

    s = sub.add_parser("log", parents=[raw], help="ターンログ（省くと最後のターン。24 ターンまで）")
    s.add_argument("island", nargs="?", type=int)
    s.add_argument("--from", dest="start", type=int)
    s.add_argument("--to", dest="end", type=int)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("mcp", help="MCP の stdio のつなぎ（AI の MCP の設定から起動する）")
    s.set_defaults(func=cmd_mcp)

    a = p.parse_args(argv)
    return a.func(Client(a.base), a)


if __name__ == "__main__":
    sys.exit(main())
