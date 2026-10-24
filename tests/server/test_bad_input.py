"""形の違う JSON や、ありえない値を送られても、500 にならずに 400 台で断る（A05）。

JSON を受ける入り口を、壊れた本文・JSON の型違い・命令の中身の型違いと範囲外で叩き、500 が
出ないことを見る。通るかどうかは見ず、落ちないことだけを確かめる。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from server.game import get_game
from server.web import mcp
from server.world.models import Island

pytestmark = pytest.mark.django_db

# 本文が JSON として壊れている・オブジェクトでない
RAW_BODIES = [
    b"",
    b"null",
    b"[]",
    b"[1, 2]",
    b"1",
    b'"text"',
    b"true",
    b"{",
    b"{'a': 1}",
    b"\xff\xfe\x00",
    b'{"a": NaN}',
    b'{"a": Infinity}',
    b'{"a": 1e999}',
    b'{"a": -1e999}',
    b'{"orders": [{"kind": "set_price_cap", "payload": {"good": "electricity", "price": 1e999}}]}',
    b'{"kind": "set_price_cap", "payload": {"good": "electricity", "price": 1e999}}',
    b"[" * 3000 + b"]" * 3000,
    b'{"a":' * 3000 + b"1" + b"}" * 3000,
]
# 項目に入れる値
ODD_VALUES: list[Any] = [
    None,
    True,
    -1,
    0,
    10**30,
    1e308,
    float("nan"),
    float("inf"),
    "",
    "zzz",
    "9" * 400,
    [],
    {},
    [None],
    [[]],
    {"a": {"b": []}},
]


@pytest.fixture(autouse=True)
def fresh_cache():
    """AI 用の鍵の呼び出し回数の数え（キャッシュ）を、ほかの試験に持ち越さない。"""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def player(world) -> tuple[Client, Island]:
    user = User.objects.create_user("fuzz", password="a-long-pass-123")
    island = Island.objects.order_by("id").first()
    island.owner = user
    island.save(update_fields=["owner"])
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client, island


def _send(client: Client, method: str, url: str, body: bytes | Any) -> int:
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    res = getattr(client, method)(url, body, content_type="application/json")
    return res.status_code


def _json_endpoints(island: Island) -> list[tuple[str, str]]:
    base = f"/api/islands/{island.id}"
    return [
        ("put", f"{base}/orders"),
        ("post", f"{base}/orders/check"),
        ("post", "/api/founding/random-shape"),
        ("post", "/api/founding/terrain"),
        ("post", "/api/founding"),
        ("post", "/api/letters"),
        ("post", "/api/hall"),
        ("post", "/api/bug-reports"),
        ("put", f"{base}/country/edit"),
        ("put", f"{base}/flag"),
        ("post", "/api/board/threads"),
        ("post", "/api/board/ng"),
        ("post", "/api/board/notices"),
        ("post", f"{base}/country/notes"),
    ]


FOUNDING_BODIES: list[Any] = [
    {"land": [[0]]},
    {"land": [["a", "b"]]},
    {"land": [[10**30, 1]]},
    {"land": [[0, 0, 0]]},
    {"land": {"a": 1}},
    {"land": [[0, 0]], "edits": [[0, 0]]},
    {"land": [[0, 0]], "edits": [[0, 0, ["x"]]]},
    {"land": [[0, 0]], "edits": "ab"},
    {"land": [[0, 0]], "regime": {"economy": 5}},
    {"land": [[0, 0]], "regime": {"economy": []}},
    {"land": [[0, 0]], "regime": {"economy": {"a": 1}}},
    {"land": [[0, 0]], "regime": [1]},
    {"land": [[0, 0]], "capital": [0], "cell": [0, 0]},
    {"land": [[0, 0]], "capital": [0, 0], "cell": "ab"},
    {"land": [[0, 0]], "capital": [0, 0], "cell": [0, 0], "names": [1]},
    {"land": [[0, 0]], "capital": [0, 0], "cell": [0, 0], "resources": 5},
    {"land": [[0, 0]], "capital": [0, 0], "cell": [10**30, 0], "seed": 1.5},
]


def test_founding_with_odd_shapes_never_500(player):
    client, _ = player
    seen = []
    for url in ("/api/founding/terrain", "/api/founding"):
        for body in FOUNDING_BODIES:
            if _send(client, "post", url, body) >= 500:
                seen.append((url, str(body)[:80]))
    assert not seen


def test_broken_bodies_never_500(player):
    client, island = player
    seen = []
    for method, url in _json_endpoints(island):
        for raw in RAW_BODIES:
            code = _send(client, method, url, raw)
            if code >= 500:
                seen.append((method, url, raw[:30], code))
    assert not seen


def test_odd_values_in_every_field_never_500(player):
    client, island = player
    seen = []
    keys = ["orders", "kind", "payload", "land", "capital", "cell", "regime", "names", "edits",
            "resources", "seed", "place_style", "to", "subject", "body", "title", "text", "url",
            "client", "intro", "era", "new_era", "board", "name", "png_base64"]  # fmt: skip
    for method, url in _json_endpoints(island):
        for key in keys:
            for value in ODD_VALUES:
                body = {key: value}
                if key == "orders":
                    body = {"orders": value}
                code = _send(client, method, url, body)
                if code >= 500:
                    seen.append((method, url, key))
    assert not sorted(set(seen))


def _sample_for(schema: dict[str, Any], value: str) -> Any:
    kind = schema.get("type")
    if kind == "string":
        return "zzz" if value == "ref" else value
    if kind in ("integer", "number"):
        return {"ref": 999999, "neg": -1, "zero": 0, "huge": 10**12}[value]
    if kind == "array":
        return []
    if kind == "object":
        return {"zzz": 1}
    return None


def _order_payloads(spec: dict[str, Any]) -> list[Any]:
    """命令の形から作る、形は合っていて中身がありえない payload と、型違いの payload。"""
    props = dict(spec.get("properties", {}))
    out: list[Any] = [None, [], "x", 1, {}]
    for mode in ("ref", "neg", "zero", "huge"):
        out.append({k: _sample_for(v, mode) for k, v in props.items()})
    for key in props:
        for value in ODD_VALUES[1::2]:
            out.append({k: _sample_for(v, "ref") for k, v in props.items()} | {key: value})
    return out


def test_every_order_kind_with_odd_payloads_never_500(player):
    client, island = player
    defs = get_game().defs
    seen = []
    for kind, order in defs.orders.items():
        from hakoniwa_core.defs.freeze import thaw

        schema = thaw(order["payload"])
        for i, payload in enumerate(_order_payloads(schema)):
            body = {"kind": kind, "payload": payload}
            # 確かめは全部、列に並べるのは形ごとの代表（5 つの型違いと 4 つの範囲外）だけ
            for method, url in (
                ("post", f"/api/islands/{island.id}/orders/check"),
                ("put", f"/api/islands/{island.id}/orders"),
            ):
                if method == "put" and i >= 9:
                    continue
                send = {"orders": [body]} if method == "put" else body
                code = _send(client, method, url, send)
                if code >= 500:
                    seen.append((kind, method, str(payload)[:60], code))
    assert not seen


def test_odd_order_kinds_never_500(player):
    client, island = player
    seen = []
    for value in ODD_VALUES:
        for send in ({"orders": [{"kind": value, "payload": {}}]}, {"orders": [value]}):
            code = _send(client, "put", f"/api/islands/{island.id}/orders", send)
            if code >= 500:
                seen.append(("put", str(value)[:20], code))
        code = _send(client, "post", f"/api/islands/{island.id}/orders/check", {"kind": value})
        if code >= 500:
            seen.append(("check", str(value)[:20], code))
    assert not seen


def test_mcp_odd_messages_never_500(player, settings):
    client, island = player
    from server.accounts import keys

    _, token = keys.issue(User.objects.get(username="fuzz"), "t")
    seen = []

    def post(body):
        res = client.post(
            "/mcp",
            body if isinstance(body, bytes) else json.dumps(body).encode(),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        return res.status_code

    for raw in RAW_BODIES:
        if post(raw) >= 500:
            seen.append(("raw", raw[:30]))
    for value in ODD_VALUES:
        for msg in (
            value,
            [value],
            {"jsonrpc": "2.0", "id": 1, "method": value},
            {"jsonrpc": "2.0", "id": value, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": value},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": value}},
        ):
            if post(msg) >= 500:
                seen.append(("msg", str(msg)[:60]))
    tools = [t.name for t in mcp.TOOLS]
    for name in tools:
        for value in ODD_VALUES:
            for args in (value, {"island_id": value}, {"kind": value, "payload": value}):
                msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }
                if post(msg) >= 500:
                    seen.append((name, str(args)[:40]))
    assert not seen
