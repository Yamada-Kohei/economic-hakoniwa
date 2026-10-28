"""試し遊びのための小さな道具（標準ライブラリだけで動きます）。

ブラウザを使わずに、登録・ログイン・建国・画面や API の読み取り・命令の確かめと並べ替えをします。
使い方は docs/playtest.md の「道具」と「AI 用の入り口」を見てください。

    python tools/playtest/hk.py --base http://127.0.0.1:8000 key   # AI 用の鍵を覚えさせる
    python tools/playtest/hk.py summary                            # 自分の国の要約
    python tools/playtest/hk.py changes                            # 前に読んだターンからの差分
    python tools/playtest/hk.py get api/islands/4/economy
    python tools/playtest/hk.py map 4
    python tools/playtest/hk.py send 5 'ごあいさつ' 'はじめまして'
    python tools/playtest/hk.py hall                              # 国際会議場
    python tools/playtest/hk.py hall-post 'ごあいさつ' '各国に平和を。'
    python tools/playtest/hk.py diplomacy                         # 自分の国の外交
    python tools/playtest/hk.py military                          # 自分の国の軍
    python tools/playtest/hk.py fleets                            # 世界の艦隊と前に見た場所
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
# 道具として名乗る（Python の既定の名乗り Python-urllib は、公開の入り口でロボットとして止められる）
USER_AGENT = "hakoniwa-hk.py (+https://github.com/Yamada-Kohei/economic-hakoniwa)"
ACTIVE_PROFILE = ""


def read_profiles(path: Path) -> dict[str, dict[str, str]]:
    """接続先とファイルの場所だけ読む。鍵・パスワードそのものは置かない。"""
    profiles = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profiles, dict):
        raise ValueError("プロファイルは名前ごとのオブジェクトにしてください")
    for name, settings in profiles.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("プロファイル名は英数字・_・- だけにしてください")
        allowed = {"base", "key_file", "cookies", "state"}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError("設定できるのは base・key_file・cookies・state だけです")
        if not all(isinstance(value, str) and value.strip() for value in settings.values()):
            raise ValueError("プロファイルの値は空でない文字列にしてください")
        base = urllib.parse.urlsplit(settings.get("base", DEFAULT_BASE))
        if (
            base.scheme not in {"http", "https"}
            or not base.netloc
            or base.username
            or base.password
        ):
            raise ValueError("base は資格情報を含まない http / https の URL にしてください")
    return profiles


def use_profile(name: str, settings: dict[str, str], source: Path) -> None:
    """選んだアカウントの鍵・cookie・差分の記録を分ける。選択はこの呼び出しだけ。"""
    global ACTIVE_PROFILE, COOKIES, KEY_FILE, STATE
    ACTIVE_PROFILE = name
    root = Path.home() / ".hk" / name

    def location(key: str, default: Path) -> Path:
        path = Path(settings[key]).expanduser() if key in settings else default
        return path.resolve() if path.is_absolute() else (source.parent / path).resolve()

    COOKIES = location("cookies", root / "cookies.txt")
    KEY_FILE = location("key_file", root / "key.txt")
    STATE = location("state", root / "state.json")


def load_key() -> str:
    """AI 用の鍵（HK_KEY か、key で覚えさせたファイル）。なければ空。"""
    key = "" if ACTIVE_PROFILE else os.environ.get("HK_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    return key


def fix_path(path: str) -> str:
    """先頭の / を補う。Windows の Git Bash が /api/... を C:/Program Files/Git/api/... に書き換えた
    ものは、/api/ や /islands/ から後ろを使う。"""
    if re.match(r"^[A-Za-z]:[/\\]", path):
        found = re.search(
            r"/(api|islands|letters|board|hall|accounts|help)(/|$)", path.replace("\\", "/")
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
        req.add_header("User-Agent", USER_AGENT)
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
    user = a.user or ("" if ACTIVE_PROFILE else os.environ.get("HK_USER", ""))
    password = a.password or ("" if ACTIVE_PROFILE else os.environ.get("HK_PASSWORD", ""))
    if not user or not password:
        print(
            "名前とパスワードを指定してください"
            "（プロファイルでは HK_USER・HK_PASSWORD は使いません）"
        )
        return 1
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
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8-sig"))
    return json.loads(arg)


def cmd_check(c: Client, a) -> int:
    """1件または列を、今の状態で1件ずつ確かめる。列の置き換えはしない。"""
    value = load_value(a.order)
    orders = value.get("orders") if isinstance(value, dict) and "orders" in value else value
    batch = isinstance(orders, list)
    orders = orders if batch else [orders]
    if not all(
        isinstance(order, dict)
        and isinstance(order.get("kind"), str)
        and isinstance(order.get("payload", {}), dict)
        for order in orders
    ):
        print("命令は {kind, payload} か、その一覧にしてください", file=sys.stderr)
        return 1
    c.get("/")  # csrf の cookie
    results = []
    for index, order in enumerate(orders, 1):
        status, body = c.json("POST", f"/api/islands/{a.island}/orders/check", order)
        if status >= 400:
            return fail(status, body)
        result = json.loads(body)
        if not batch:
            show(status, body)
            return 0 if result.get("ok") is True else 1
        results.append({"index": index, **result})
    ok = all(result.get("ok") is True for result in results)
    show(200, json.dumps({"ok": ok, "results": results}, ensure_ascii=False))
    return 0 if ok else 1


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


def cmd_facilities(c: Client, a) -> int:
    """施設の画面と同じ一覧を1行ずつ出す（損益などは持ち主だけ）。"""
    island = a.island or own_island(c)
    status, body, _ = c.get(f"/api/islands/{island}/facilities")
    if status >= 400:
        return show(status, body) if a.json else fail(status, body)
    data = json.loads(body)
    if a.json:
        return show(status, body)
    if "table" not in data:
        print(
            "施設ごとの一覧は島の持ち主だけが読めます。国全体の数は get で API を読んでください。"
        )
        return 1
    rows = data["table"]["rows"]
    count = 0
    for row in rows:
        state = (row.get("status_text") or "稼働中") if row.get("built") else "建設中"
        if a.status and state != a.status:
            continue
        count += 1
        place = f"({row['x']},{row['y']})" if row["on_map"] else "マスなし"
        rate = row.get("operating_rate")
        rate_text = "未観測" if rate is None else f"{rate}%"
        print(
            f"#{row['number']} {place} {row['name']}（{row['kind_name']}） {state} "
            f"稼働 {rate_text} 従業者 {row['workers']} "
            f"損益 {row.get('profit') or '未集計'} 預金 {row.get('deposit') or '—'}"
        )
    print(f"{count} / {len(rows)} 件")
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


# --- 手紙・掲示板・国際会議場・外交 ----------------------------------------------
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
    """掲示板の書いた人: 名前（国名で書いたなら島の番号も）・トリップ・ID。"""
    text = item.get("name") or "名無し"
    island = item.get("island")
    if island:
        text += f"（島 {island['id']}）"
    if item.get("trip"):
        text += f" ◆{item['trip']}"
    if item.get("id"):
        text += f" ID:{item['id']}"
    return text


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


def cmd_diary(c: Client, a) -> int:
    island = a.island or own_island(c)
    status, body, _ = c.get(f"/api/islands/{island}/diary")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    x = json.loads(body)
    if x["turn"] is None:
        print("（国の日誌はまだありません）")
        return 0
    print(f"国の日誌（ターン {x['turn']}・{when(x['updated_at'])} に書いたもの）")
    print("----")
    print(x["text"])
    return 0


def cmd_diary_set(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    island = a.island or own_island(c)
    c.get("/")  # csrf の cookie
    status, body = c.json("PUT", f"/api/islands/{island}/diary", {"text": text})
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    x = json.loads(body)
    print(f"国の日誌を書きました（{len(x['text'])} 字、ターン {x['turn']}）")
    return 0


def board_post_line(p: dict) -> str:
    """書き込み 1 件の見出し（番号・名前・時刻・いいね・返信）。"""
    head = f"--- {p['number']} {who(p)} {when(p['created_at'])}"
    if p.get("sage"):
        head += " sage"
    if p.get("likes"):
        head += f" ♥{p['likes']}"
    if p.get("replied_by"):
        head += " 返信:" + ",".join(f">>{n}" for n in p["replied_by"])
    return head


def board_post_body(p: dict) -> str:
    if p.get("hidden"):
        return "（あぼーん: 管理者が非表示にしました）"
    return p["body"] if p.get("body") is not None else "（NG の書き込み。--show-ng で出す）"


def cmd_boards(c: Client, a) -> int:
    status, body, _ = c.get("/api/board/boards")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    for b in json.loads(body)["boards"]:
        note = "（運営のみ）" if b["staff_only"] else ""
        print(f"{b['slug']}: {b['name']}{note} {b['threads']} スレッド {b['description']}")
    return 0


def cmd_board(c: Client, a) -> int:
    query = f"page={a.page}" + (f"&board={a.board}" if a.board else "")
    if a.archived:
        query += "&archived=1"
    status, body, _ = c.get(f"/api/board/threads?{query}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    print(f"掲示板（{data['page']}/{data['pages']} ページ、{data['count']} スレッド）")
    for t in data["threads"]:
        flags = ("固定 " if t["pinned"] else "") + ("満 " if t["closed"] else "")
        print(
            f"#{t['id']} [{t['board']['slug']}] {t['title']}（{t['posts']} 件、"
            f"{flags}最後 {when(t['updated_at'])}）"
        )
    if not data["threads"]:
        print("（スレッドはありません）")
    return 0


def cmd_thread(c: Client, a) -> int:
    query = []
    if a.latest:
        query.append(f"latest={a.latest}")
    elif a.start:
        query.append(f"from={a.start}")
        if a.to:
            query.append(f"to={a.to}")
    else:
        query.append(f"page={a.page}")
    if a.show_ng:
        query.append("ng=show")
    status, body, _ = c.get(f"/api/board/threads/{a.number}?{'&'.join(query)}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    t = data["thread"]
    print(f"#{t['id']} [{t['board']['slug']}] {t['title']}（{t['posts']}/{t['limit']} 件）")
    for p in data["posts"]:
        print(board_post_line(p))
        print(board_post_body(p))
    return 0


def writer_fields(a) -> dict:
    fields: dict = {"identity": "country" if a.country else "anon"}
    if a.name:
        fields["name"] = a.name
    if a.sage:
        fields["sage"] = True
    return fields


def cmd_post(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    value = {"body": text, **writer_fields(a)}
    status, body = c.json("POST", f"/api/board/threads/{a.thread}/posts", value)
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    p = json.loads(body)
    print(f"書き込みました: スレッド #{p['thread']} の {p['number']} 番 ({who(p)})")
    return 0


def cmd_new_thread(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    value = {"title": a.title, "body": text, "board": a.board, **writer_fields(a)}
    status, body = c.json("POST", "/api/board/threads", value)
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    t = json.loads(body)["thread"]
    print(f"スレッドを立てました: #{t['id']} [{t['board']['slug']}] {t['title']}")
    return 0


def cmd_board_search(c: Client, a) -> int:
    query = urllib.parse.urlencode({"q": a.query, "board": a.board or "", "page": a.page})
    status, body, _ = c.get(f"/api/board/search?{query}")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    print(f"「{data['query']}」{data['count']} 件（{data['page']}/{data['pages']} ページ）")
    for p in data["posts"]:
        print(f"スレッド #{p['thread']['id']} {p['thread']['title']}")
        print(board_post_line(p))
        print(board_post_body(p))
    return 0


def cmd_replies(c: Client, a) -> int:
    if a.mark:
        c.get("/")  # csrf の cookie
        status, body = c.json("POST", "/api/board/notices", {})
    else:
        status, body, _ = c.get("/api/board/notices")
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    data = json.loads(body)
    print(f"自分の書き込みへの返信（未読 {data['unread']} 件）")
    for n in data["notices"]:
        new = "NEW " if n["unread"] else ""
        print(
            f"{new}スレッド #{n['thread']['id']} {n['thread']['title']}（あなたの {n['to']} 番へ）"
        )
        print(board_post_line(n["post"]))
        print(board_post_body(n["post"]))
    if not data["notices"]:
        print("（返信はありません）")
    return 0


def board_act(c: Client, a, path: str, value: dict, done: str) -> int:
    c.get("/")  # csrf の cookie
    status, body = c.json("POST", path, value)
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    print(done.format(**json.loads(body)))
    return 0


def cmd_like(c: Client, a) -> int:
    path = f"/api/board/threads/{a.thread}/posts/{a.number}/like"
    return board_act(c, a, path, {}, "いいね: {number} 番 {likes} 件（付けた: {liked}）")


def cmd_report(c: Client, a) -> int:
    path = f"/api/board/threads/{a.thread}/posts/{a.number}/report"
    return board_act(c, a, path, {"reason": a.reason}, "通報しました（{number} 番）")


def cmd_ng(c: Client, a) -> int:
    if a.action == "list":
        status, body, _ = c.get("/api/board/ng")
        if a.json:
            return show(status, body)
        if status >= 400:
            return fail(status, body)
        for r in json.loads(body)["rules"]:
            print(f"#{r['id']} {r['kind']} {r['word']} {r['label']}")
        return 0
    c.get("/")  # csrf の cookie
    if a.action == "remove":
        status, body = c.json("DELETE", f"/api/board/ng/{a.target}", {})
    else:
        value: dict = {"kind": a.kind}
        if a.kind == "word":
            value["word"] = a.target
        else:
            value.update(thread=a.thread, number=int(a.target))
        status, body = c.json("POST", "/api/board/ng", value)
    if a.json:
        return show(status, body)
    if status >= 400:
        return fail(status, body)
    print("NG の設定を変えました")
    return 0


def print_wars(wars: list[dict]) -> None:
    for w in wars:
        print(
            f"  戦争 #{w['id']} {island_label(w['attacker'])} → {island_label(w['defender'])}"
            f": {w['state_name']}（布告 ターン {w['declared']}、開戦 ターン {w['start']}）"
        )
        if w.get("ended") is not None:
            print(f"    終結 ターン {w['ended']}")
    if not wars:
        print("  （ありません）")


def print_treaties(treaties: list[dict]) -> None:
    for t in treaties:
        parties = "・".join(island_label(p) for p in t["parties"])
        print(f"  条約 #{t['id']} {t['title']} [{t['state_name']}] {parties}")
        print(f"    提案: {island_label(t['proposer'])}・ターン {t['created']}")
        if t.get("start") is not None:
            print(f"    効き始め ターン {t['start']}")
        if t.get("end") is not None:
            print(f"    終了 ターン {t['end']}")
        if t.get("text"):
            print(t["text"])
        for clause in t["clauses"]:
            details = []
            if "notice_turns" in clause:
                details.append(f"破棄の予告 {clause['notice_turns']} ターン")
            if "withdraw_turns" in clause:
                details.append(f"引き揚げ {clause['withdraw_turns']} ターン以内")
            if "prisoners" in clause:
                details.append("捕虜を返す" if clause["prisoners"] else "捕虜の返還なし")
            print(f"    {clause['name']}" + (f"（{'、'.join(details)}）" if details else ""))
            rep = clause.get("reparations")
            if rep:
                due = (
                    f"ターン {rep['due']}"
                    if rep["due"] is not None
                    else f"効き始めから {rep['due_turns']} ターン以内"
                )
                print(
                    f"      賠償: {island_label(rep['payer'])} → {island_label(rep['payee'])}、"
                    f"{island_label(rep['currency'])}の通貨で {rep['amount']}"
                    f"（送付済み {rep['paid']}、額は通貨の最小単位）、期日 {due} [{rep['state']}]"
                )
        notice = t.get("notice")
        if notice:
            print(
                f"    破棄の予告: {island_label(notice['by'])}・ターン {notice['turn']}、"
                f"終了予定 ターン {notice['until']}"
            )
        for v in t.get("violations", []):
            print(
                f"    違反: ターン {v['turn']} {island_label(v['by'])}"
                f"・{v['clause']}・{v['reason']}"
            )
    if not treaties:
        print("  （ありません）")


def cmd_hall(c: Client, a) -> int:
    status, body, _ = c.get(f"/api/hall?page={a.page}")
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    data = json.loads(body)
    print(
        f"国際会議場（ターン {data['turn']}、{data['page']}/{data['pages']} ページ、"
        f"声明と出来事 {data['count']} 件）"
    )
    print("[戦争の状態]")
    print_wars(data["wars"])
    print("[効いている条約]")
    print_treaties(data["treaties"])
    print("[声明と出来事]")
    for entry in data["entries"]:
        if entry["type"] == "statement":
            print(
                f"--- 声明 #{entry['id']} ターン {entry['turn']} {when(entry['posted_at'])}"
                f" {island_label(entry['island'])}: {entry['title']}"
            )
        else:
            print(f"--- 出来事 ターン {entry['turn']}")
        print(entry["text"])
    if not data["entries"]:
        print("  （声明と出来事はありません）")
    return 0


def cmd_hall_post(c: Client, a) -> int:
    text = read_text(a.body, a.file)
    c.get("/")  # csrf の cookie
    status, body = c.json("POST", "/api/hall", {"title": a.title, "body": text})
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    entry = json.loads(body)
    print(
        f"声明を出しました: #{entry['id']} {island_label(entry['island'])}: {entry['title']}"
        f"（ターン {entry['turn']}）"
    )
    return 0


def print_military_rows(rows: list[dict], seen: bool = False) -> None:
    """部隊・艦隊の一覧。API が返した公開範囲の項目だけを出す。"""
    for row in rows:
        location = f"({row['x']}, {row['y']})"
        name = row.get("name", row["army"])
        if seen:
            state = "今見えている" if row["current"] else "前に見た場所"
            print(
                f"  #{row['id']} {name} 国 #{row['owner']} {location} {state} ターン {row['turn']}"
            )
            continue
        state = {"forming": "編成中", "ready": "そろった"}.get(row["status"], row["status"])
        full = row.get("soldiers_full")
        print(f"  #{row['id']} {name} {location} 兵 {row['soldiers']}/{full} {state}")
        if "ships" in row:
            docked = "母港につないでいる" if row["docked"] else "母港の外"
            print(f"    艦 {row['ships']} 隻 {docked} 残り {row['steps_left']} 区画")
            if row["capacity"]:
                print(f"    荷 {row['cargo']} 運べる重さ {row['capacity']} トン")
        elif row.get("aboard"):
            print(f"    乗船先 #{row['aboard']}")
        print(f"    装備 {row.get('equipment', {})} 命令 {row.get('order')}")
    if not rows:
        print("  （ありません）")


def cmd_military(c: Client, a) -> int:
    island = a.island or own_island(c)
    status, body, _ = c.get(f"/api/islands/{island}/military")
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    data = json.loads(body)["military"]
    print(f"島 {island} の軍（統計のターン {data['stats_turn']}）")
    print(f"兵 {data['soldiers']} 人")
    print(f"国防費・兵の賃金と食べ物 {data['budget']['personnel']}")
    print(f"国防費・装備の買い入れと編成 {data['budget']['procurement']}")
    if "volunteers" in data:
        print(f"志願 {data['volunteers']} 徴兵 {data['conscripts']} 未配属 {data['free_soldiers']}")
        print(f"士気 {data['morale']:.0%} 練度 {data['training']:.0%}")
        print(
            f"賃金・志願 {data['volunteer_wage']:.0%} "
            f"徴兵 {data['conscript_wage']:.0%}（島の平均賃金に対して）"
        )
        print(f"賃金の遅れ {data['wages_late_turns']} ターン 食べ物の不足 {data['hungry']}")
        print(
            f"直近の賃金・支払 {data['wages_paid']} 未払 {data['wages_unpaid']} "
            f"逃亡 {data['deserted']} 死亡 {data['starved']}"
        )
        print("[陸の部隊]")
        print_military_rows(data["units"])
        print("[艦隊]")
        print_military_rows(data["fleets"])
        print("[基地]")
        for base in data["bases"]:
            print(f"  #{base['facility']} {base['kind']} ({base['x']}, {base['y']}) {base}")
        if not data["bases"]:
            print("  （ありません）")
        print("[陣地]")
        print("  " + ("、".join(f"({t['x']}, {t['y']})" for t in data["trenches"]) or "なし"))
    else:
        print("部隊と艦隊の中身は、その国の持ち主だけに見えます。")
        print("[見つけた艦隊]")
        print_military_rows(data["fleets"], seen=True)
    print("全項目は --json で読めます。")
    return 0


def cmd_fleets(c: Client, a) -> int:
    status, body, _ = c.get("/api/world/fleets")
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    data = json.loads(body)
    print(f"世界の艦隊（ターン {data['turn']}）")
    print("[自国の艦隊]")
    print_military_rows(data["mine"])
    print("[見つけた他国の艦隊]")
    print_military_rows(data["seen"], seen=True)
    print("全項目は --json で読めます。")
    return 0


def cmd_diplomacy(c: Client, a) -> int:
    island = a.island or own_island(c)
    status, body, _ = c.get(f"/api/islands/{island}/diplomacy")
    if a.json or status >= 400:
        return show(status, body) if a.json else fail(status, body)
    data = json.loads(body)
    print(f"島 {island} の外交（ターン {data['turn']}）")
    print("[戦争の状態]")
    print_wars(data["wars"])
    for key, label in (
        ("treaties", "効いている条約"),
        ("incoming", "届いた提案（返事待ち）"),
        ("outgoing", "出した提案（返事待ち）"),
        ("closed", "終わった条約と提案"),
    ):
        if key in data:
            print(f"[{label}]")
            print_treaties(data[key])
    print("[終わった戦争]")
    print_wars(data["past_wars"])
    print("[条約の違反の記録]")
    for v in data["violations"]:
        print(
            f"  ターン {v['turn']} 条約 #{v['treaty']} {v['treaty_title']}"
            f"・{v['clause_name']}: {v['reason_name']}"
        )
    if not data["violations"]:
        print("  （ありません）")
    print("決まりと命令の選択肢を含む全項目は --json で読めます。")
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
        req.add_header("User-Agent", USER_AGENT)
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
    # Windows のコンソール・リダイレクトでも日本語を UTF-8 で出す。MCP は既存どおりバイト列。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    p = argparse.ArgumentParser(description="経済版箱庭諸国の試し遊びの道具")
    p.add_argument(
        "--base", help="サーバーの URL（既定はプロファイル・HK_BASE・127.0.0.1:8000 の順）"
    )
    p.add_argument("--profile", help="アカウントごとの設定名（この呼び出しだけ切り替える）")
    p.add_argument(
        "--profiles",
        type=Path,
        default=Path.home() / ".hk-profiles.json",
        help="プロファイルの JSON（接続先とファイルの場所だけ。リポジトリの外に置く）",
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

    s = sub.add_parser("check", help="命令1件か列を確かめる（JSON か @ファイル。列は1件ずつ確認）")
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

    s = sub.add_parser("facilities", parents=[raw], help="施設の一覧を1行ずつ出す（持ち主だけ）")
    s.add_argument("island", nargs="?", type=int)
    s.add_argument("--status", help="状態の表示名で絞る（例: 原料不足・電力不足・建設中）")
    s.set_defaults(func=cmd_facilities)

    sub.add_parser("profiles", help="設定済みのプロファイル名を出す（鍵の中身は読まない）")

    def add_text(s: argparse.ArgumentParser) -> None:
        s.add_argument("body", nargs="?", help="本文（省くと標準入力から読む）")
        s.add_argument("-f", "--file", help="本文のファイル（- は標準入力）")

    s = sub.add_parser("diary", parents=[raw], help="自分の国の日誌（持ち主だけのメモ欄）を読む")
    s.add_argument("--island", type=int, help="自分の島の番号（省けば自分の島）")
    s.set_defaults(func=cmd_diary)

    s = sub.add_parser(
        "diary-set", parents=[raw], help="自分の国の日誌を書き換える（全文を置き換え）"
    )
    s.add_argument("--island", type=int, help="自分の島の番号（省けば自分の島）")
    add_text(s)
    s.set_defaults(func=cmd_diary_set)

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

    def add_writer(s: argparse.ArgumentParser) -> None:
        s.add_argument("--name", help="名前（名前#秘密 でトリップ。空なら名無し）")
        s.add_argument("--country", action="store_true", help="自分の国名で書く")
        s.add_argument("--sage", action="store_true", help="上げない")

    s = sub.add_parser("boards", parents=[raw], help="掲示板の板の一覧")
    s.set_defaults(func=cmd_boards)

    s = sub.add_parser("board", parents=[raw], help="掲示板のスレッドの一覧")
    s.add_argument("--board", help="板の名前（boards の左の名前）。省くとすべて")
    s.add_argument("--archived", action="store_true", help="過去ログ")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(func=cmd_board)

    s = sub.add_parser("thread", parents=[raw], help="スレッドの書き込みを読む")
    s.add_argument("number", type=int)
    s.add_argument("--page", default="last", help="ページ（100 件ずつ。既定は最後のページ）")
    s.add_argument("--latest", type=int, help="最後の N 件")
    s.add_argument("--from", dest="start", type=int, help="この番号から（--to まで）")
    s.add_argument("--to", type=int)
    s.add_argument("--show-ng", action="store_true", help="NG の書き込みの本文も出す")
    s.set_defaults(func=cmd_thread)

    s = sub.add_parser("post", parents=[raw], help="スレッドに書き込む（>>番号 で返信）")
    s.add_argument("thread", type=int)
    add_text(s)
    add_writer(s)
    s.set_defaults(func=cmd_post)

    s = sub.add_parser("new-thread", parents=[raw], help="スレッドを立てる")
    s.add_argument("title", help="題名")
    s.add_argument("--board", default="chat", help="板の名前（既定 chat）")
    add_text(s)
    add_writer(s)
    s.set_defaults(func=cmd_new_thread)

    s = sub.add_parser("board-search", parents=[raw], help="掲示板の題名と本文を探す")
    s.add_argument("query", help="言葉（空白で区切るとすべてを含む）")
    s.add_argument("--board")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(func=cmd_board_search)

    s = sub.add_parser("replies", parents=[raw], help="自分の書き込みへの返信の知らせ")
    s.add_argument("--mark", action="store_true", help="見えたぶんを既読にする")
    s.set_defaults(func=cmd_replies)

    s = sub.add_parser("like", parents=[raw], help="書き込みにいいねを付ける・外す")
    s.add_argument("thread", type=int)
    s.add_argument("number", type=int)
    s.set_defaults(func=cmd_like)

    s = sub.add_parser("report", parents=[raw], help="荒らしの書き込みを管理者に通報する")
    s.add_argument("thread", type=int)
    s.add_argument("number", type=int)
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("ng", parents=[raw], help="掲示板の NG（list・add・remove）")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("target", nargs="?", help="add: word なら言葉、person・ident なら書き込みの番号")
    s.add_argument("--kind", choices=["word", "ident", "person"], default="word")
    s.add_argument("--thread", type=int, help="person・ident のとき、書き込みのあるスレッド")
    s.set_defaults(func=cmd_ng)

    s = sub.add_parser("hall", parents=[raw], help="国際会議場の戦争・条約・声明と出来事")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(func=cmd_hall)

    s = sub.add_parser("hall-post", parents=[raw], help="国際会議場に公開の声明を出す")
    s.add_argument("title", help="題名")
    add_text(s)
    s.set_defaults(func=cmd_hall_post)

    s = sub.add_parser("diplomacy", parents=[raw], help="外交の状態（島を省くと自分の島）")
    s.add_argument("island", nargs="?", type=int)
    s.set_defaults(func=cmd_diplomacy)

    s = sub.add_parser("military", parents=[raw], help="軍の状態（島を省くと自分の島）")
    s.add_argument("island", nargs="?", type=int)
    s.set_defaults(func=cmd_military)

    s = sub.add_parser("fleets", parents=[raw], help="世界の艦隊（自国と見つけた他国）")
    s.set_defaults(func=cmd_fleets)

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
    base_override = a.base
    a.base = base_override or DEFAULT_BASE
    if a.profile or a.command == "profiles":
        source = a.profiles.expanduser().resolve()
        try:
            profiles = read_profiles(source)
        except (OSError, ValueError) as e:
            p.error(f"プロファイルを読めません: {e}")
        if a.command == "profiles":
            print("\n".join(sorted(profiles)) or "（プロファイルはありません）")
            return 0
        if a.profile not in profiles:
            p.error("指定したプロファイルがありません（profiles で名前を確認してください）")
        profile = profiles[a.profile]
        # --base の指定があればそちらを使う。環境の HK_KEY などを別の国へ持ち越さない。
        if base_override is None:
            a.base = profile.get("base", DEFAULT_BASE)
        use_profile(a.profile, profile, source)
    return a.func(Client(a.base), a)


if __name__ == "__main__":
    sys.exit(main())
