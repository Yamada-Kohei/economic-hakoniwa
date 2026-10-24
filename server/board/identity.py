"""名前欄の読み方（トリップ）と、板ごと・日ごとに変わる ID。

どちらもサーバーの秘密鍵で混ぜたハッシュなので、画面に出た文字から元のアカウントや秘密の文字列は
たどれない。ID は「同じ日に同じ板で書いた同じアカウント」だけが同じ文字になる。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import date

from django.conf import settings

NAME_MAX = 20
BLOCKED_NAME_CHARS = "★◆​‌‍﻿"  # 管理者の印・トリップの印に似せられないようにする
NO_NAME = "名無し"  # 名前欄が空のときの表示


def _digest(*parts: str) -> bytes:
    return hmac.new(
        settings.SECRET_KEY.encode(), "\x1f".join(parts).encode(), hashlib.sha256
    ).digest()


def _short(raw: bytes, length: int) -> str:
    return base64.b32encode(raw).decode().rstrip("=").lower()[:length]


def day_ident(day: date, board_id: int, user_id: int) -> str:
    """その日・その板での、アカウントの ID（8 文字）。"""
    return _short(_digest("id", day.isoformat(), str(board_id), str(user_id)), 8)


def trip_of(secret: str) -> str:
    """トリップ（`名前#秘密` の秘密から作る 10 文字。同じ秘密なら同じ文字になる）。"""
    return _short(_digest("trip", secret), 10)


def split_name(raw: str) -> tuple[str, str]:
    """名前欄を（名前, トリップ）に分ける。`#` より後ろがトリップの秘密で、画面には残さない。"""
    name, hash_mark, secret = raw.partition("#")
    name = "".join(c for c in name if c not in BLOCKED_NAME_CHARS and (c.isprintable() or c == " "))
    name = name.strip()[:NAME_MAX]
    return name, (trip_of(secret) if hash_mark and secret else "")
