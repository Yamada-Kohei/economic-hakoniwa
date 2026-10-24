"""不具合の報告を受ける決まり。画面（static/js/feedback.js）・JSON API・MCP が同じものを使う。

自動で添えるのは、報告した人の島・ターン・ゲームの版・直前に出した命令（サーバーが知っているもの）と、
画面が送る開いていた画面の URL・ブラウザ・画面の大きさ・直近の JavaScript のエラー。画面が送る値は
長さを切り、決まった項目だけ受ける。同じ人の連投の歯止めに、24 時間の数・1 分の数・同じ文の重ねての
報告を断る。
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from functools import cache
from typing import Any

from django.conf import settings
from django.utils import timezone

from server.feedback.models import BugReport
from server.turns.services import get_world
from server.world.models import Island

TEXT_MAX = 2000
KINDS = {k for k, _ in BugReport.KIND_CHOICES}
VIAS = {"web", "api", "mcp"}
PER_MINUTE = 3  # 1 分に送れる数
JS_ERRORS = 5  # 添える JavaScript のエラーの数
JS_ERROR_KEYS = ("message", "source", "line", "page", "at")
FIELD_MAX = 300
LAST_ORDERS = 5  # 添える直前の命令の数
PAYLOAD_MAX = 500  # 命令の中身をそのまま添える長さ（これより長ければ省く）


class ReportError(Exception):
    """報告を受けられないわけ。text は画面と API にそのまま出し、status は API の HTTP の状態。"""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


@cache
def game_version() -> str:
    """動いているゲームの版（git のコミットの頭 12 字。HAKONIWA_VERSION があればそれ）。"""
    if settings.HAKONIWA_VERSION:
        return settings.HAKONIWA_VERSION[:40]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _short(value: Any, limit: int = FIELD_MAX) -> str:
    """文字にして長さを切る（PostgreSQL が受けない NUL 文字は除く）。"""
    return str(value if value is not None else "").replace("\x00", "")[:limit]


def clean_client(client: Any) -> dict[str, Any]:
    """画面が送ってきた添え物から、決まった項目だけを、長さを切って取り出す。"""
    if not isinstance(client, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("viewport", "user_agent", "language"):
        if client.get(key):
            out[key] = _short(client[key])
    errors = client.get("js_errors")
    if isinstance(errors, list):
        rows = []
        for e in errors[-JS_ERRORS:]:
            if isinstance(e, dict):
                rows.append(
                    {k: _short(e.get(k)) for k in ("message", "source", "line", "page", "at")}
                )
        if rows:
            out["js_errors"] = rows
    return out


def last_orders(island: Island | None) -> list[dict[str, Any]]:
    """直前に出した命令（新しい順に LAST_ORDERS 件。中身が大きければ種類だけ）。"""
    if island is None:
        return []
    rows = []
    for o in island.orders.order_by("-id")[:LAST_ORDERS]:
        payload = (
            o.payload
            if len(json.dumps(o.payload, ensure_ascii=False)) <= 500
            else "（長いので省略）"
        )
        rows.append(
            {
                "kind": o.kind,
                "payload": payload,
                "status": o.status,
                "reason": o.reason,
                "at": o.created_at.isoformat(timespec="seconds"),
            }
        )
    return rows


def submit(
    user,
    text: str,
    kind: str = "",
    url: str = "",
    client: Any = None,
    via: str = "web",
) -> BugReport:
    """報告を 1 件受ける。受けられなければ ReportError。"""
    text = (text or "").replace("\x00", "").strip()
    if not text:
        raise ReportError("何が起きたかを書いてください")
    if len(text) > TEXT_MAX:
        raise ReportError(f"{TEXT_MAX} 字までです（いまは {len(text)} 字）")
    if kind and kind not in KINDS:
        raise ReportError(f"種類は {'・'.join(sorted(KINDS))} のどれかです")
    via = via if via in VIAS else "web"
    now = timezone.now()
    mine = BugReport.objects.filter(user=user)
    if mine.filter(created_at__gte=now - timedelta(minutes=1)).count() >= PER_MINUTE:
        raise ReportError("続けて送りすぎです。少し待ってください", 429)
    limit = settings.HAKONIWA_BUG_REPORTS_PER_DAY
    if mine.filter(created_at__gte=now - timedelta(days=1)).count() >= limit:
        raise ReportError(f"報告は 24 時間に {limit} 件までです。しばらく待ってください", 429)
    same = mine.filter(text=text, created_at__gte=now - timedelta(days=1)).first()
    if same is not None:
        raise ReportError(f"同じ内容を報告済みです（報告番号 {same.pk}）", 429)
    island = Island.objects.filter(owner=user).select_related("world").first()
    world = island.world if island is not None else get_world()
    context = clean_client(client)
    context["last_orders"] = last_orders(island)
    return BugReport.objects.create(
        user=user,
        island=island,
        world_turn=world.turn if world is not None else None,
        kind=kind,
        text=text,
        url=_short(url),
        version=game_version(),
        via=via,
        context=context,
    )


def view(report: BugReport) -> dict[str, Any]:
    """API に出す 1 件（送った本人が読む分）。"""
    return {
        "id": report.pk,
        "status": report.status,
        "status_name": report.get_status_display(),
        "kind": report.kind,
        "text": report.text,
        "version": report.version,
        "created_at": report.created_at.isoformat(timespec="seconds"),
    }
