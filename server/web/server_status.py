"""管理画面の「サーバーの様子」（/admin/server/）。管理者だけが開ける。

機械の今の値（メモリ・ディスク・温度・負荷・起動してからの時間）はこの場で /proc と /sys から読む。
root でしか見られない値（サービスの状態・エラーの記録・写しなど）と 24 時間の推移は、公開サーバーの
hakoniwa-status（tools/deploy/prod/bin、1 分ごと、root）が書き出したものを読むだけにする。
書き出しのない機械（LAN の試しのサーバーなど）では、その分を「書き出していません」と出す。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from server.accounts.models import LoginAttempt
from server.world.models import Island, World

STALE_SECONDS = 180  # 書き出しがこれより古ければ「止まっているかも」と出す
BUCKET_SECONDS = 300  # 推移の図は 5 分ごとの平均にする（スマホの幅で 288 点）
HOURS = 24

# 見張り（hakoniwa-health）の知らせの印 → 画面の言葉
ALERT_NAMES = {
    "turn-late": "ターンが進んでいない",
    "db-unreadable": "DB を読めない",
    "backup-old": "毎晩の写しが古い",
    "units-failed": "失敗しているサービスがある",
    "tunnel-down": "トンネルがつながっていない",
    "game-down": "ゲームのページが開かない",
}
UNIT_NAMES = {
    "hakoniwa-gunicorn": "ゲーム本体",
    "caddy": "配信（Caddy）",
    "cloudflared": "トンネル",
    "postgresql@16-main": "DB",
    "hakoniwa-turn.timer": "ターンのタイマー",
    "hakoniwa-deploy.timer": "反映のタイマー",
    "hakoniwa-health.timer": "見張りのタイマー",
    "hakoniwa-backup.timer": "写しのタイマー",
}


@dataclass
class Meter:
    """棒のメーター 1 本。level は good / warning / serious / critical（色と文字で出す）。"""

    label: str
    value: float | None
    unit: str
    detail: str
    warn: float
    bad: float
    top: float = 100.0

    @property
    def width(self) -> float:
        if self.value is None:
            return 0.0
        return max(0.0, min(100.0, 100.0 * self.value / self.top))

    @property
    def level(self) -> str:
        if self.value is None:
            return "none"
        if self.value >= self.bad:
            return "critical"
        if self.value >= self.warn:
            return "serious"
        return "good"

    @property
    def level_text(self) -> str:
        return {"critical": "危ない", "serious": "高め", "good": "ふつう", "none": "読めない"}[
            self.level
        ]


# ---- 機械の今の値（この場で読む） ----


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def memory_percent() -> tuple[float | None, str]:
    text = _read("/proc/meminfo")
    if not text:
        return None, ""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0])  # kB
    total, avail = values.get("MemTotal"), values.get("MemAvailable")
    if not total or avail is None:
        return None, ""
    used = total - avail
    swap = values.get("SwapTotal", 0) - values.get("SwapFree", 0)
    detail = f"{_gib(used * 1024)} / {_gib(total * 1024)}、スワップ {_gib(swap * 1024)}"
    return round(100 * used / total), detail


def cpu_percent_now(wait: float = 0.3) -> float | None:
    """/proc/stat を少し空けて 2 回読み、その間の使用率を出す（書き出しがないときの代わり）。"""

    def sample() -> tuple[int, int] | None:
        text = _read("/proc/stat")
        if not text:
            return None
        parts = text.splitlines()[0].split()[1:9]
        nums = [int(p) for p in parts if p.isdigit()]
        if len(nums) < 5:
            return None
        return sum(nums), nums[3] + nums[4]

    a = sample()
    time.sleep(wait)
    b = sample()
    if not a or not b or b[0] <= a[0]:
        return None
    dt = b[0] - a[0]
    return round(100 * (dt - (b[1] - a[1])) / dt)


def temperature_c() -> float | None:
    best = None
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        raw = _read(str(zone / "temp"))
        if not raw or not raw.strip().isdigit():
            continue
        value = int(raw.strip()) / 1000
        if (_read(str(zone / "type")) or "").strip() == "x86_pkg_temp":
            return round(value)
        best = value if best is None else max(best, value)
    return None if best is None else round(best)


def disks() -> list[tuple[str, str]]:
    """(画面の名前, 場所)。あるものだけ。"""
    found = [("SSD（/）", "/")]
    if os.path.ismount("/srv/backup"):
        found.append(("HDD（写しの置き場）", "/srv/backup"))
    return found


def _gib(n: float) -> str:
    return f"{n / 1024**3:.1f}GB"


def uptime_text() -> str:
    raw = _read("/proc/uptime")
    if not raw:
        return "読めません"
    sec = int(float(raw.split()[0]))
    d, rest = divmod(sec, 86400)
    return f"{d}日{rest // 3600}時間" if d else f"{rest // 3600}時間{rest % 3600 // 60}分"


def load_text() -> str:
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return "読めません"
    return f"{one:.2f} / {five:.2f} / {fifteen:.2f}（1・5・15 分の平均、コア {os.cpu_count()}）"


# ---- 書き出されたもの ----


def read_status(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_history(folder: Path, now: float, hours: int = HOURS) -> list[list[float | None]]:
    """history.csv の行（時刻,CPU%,メモリ%,負荷,温度,応答ミリ秒）のうち、この hours 時間の分。"""
    try:
        text = (folder / "history.csv").read_text(encoding="ascii", errors="replace")
    except OSError:
        return []
    since = now - hours * 3600
    rows = []
    for line in text.splitlines():
        cells = line.split(",")
        if len(cells) < 6:
            continue
        values = []
        for cell in cells[:6]:
            try:
                values.append(float(cell))
            except ValueError:
                values.append(None)
        if values[0] is not None and since <= values[0] <= now + 60:
            rows.append(values)
    return rows


# ---- 推移の図（SVG を組み立てる。外の部品は使わない） ----

W, H = 320.0, 96.0  # 図の内側の大きさ（viewBox）。幅は画面に合わせて伸び縮みする


def chart(
    rows: list[list[float | None]],
    column: int,
    label: str,
    unit: str,
    now: float,
    top: float | None = None,
) -> dict[str, Any]:
    """1 本の折れ線の図。5 分ごとの平均を点にし、記録のない所は線を切る。"""
    start = now - HOURS * 3600
    n = int(HOURS * 3600 // BUCKET_SECONDS)
    sums = [0.0] * n
    counts = [0] * n
    for row in rows:
        t, v = row[0], row[column]
        if t is None or v is None:
            continue
        i = int((t - start) // BUCKET_SECONDS)
        if 0 <= i < n:
            sums[i] += v
            counts[i] += 1
    points = [(i, sums[i] / counts[i]) for i in range(n) if counts[i]]
    if not points:
        return {"label": label, "empty": True}
    values = [v for _, v in points]
    peak = max(values)
    scale_top = top if top is not None else _nice_top(peak)
    scale_top = max(scale_top, peak) or 1.0

    def xy(i: int, v: float) -> tuple[float, float]:
        return (round(W * (i + 0.5) / n, 1), round(H - H * v / scale_top, 1))

    segments: list[str] = []
    current: list[str] = []
    last_i = None
    for i, v in points:
        if last_i is not None and i != last_i + 1:
            segments.append(" ".join(current))
            current = []
        x, y = xy(i, v)
        current.append(f"{x},{y}")
        last_i = i
    segments.append(" ".join(current))
    lone = [s for s in segments if " " not in s]  # 1 点だけの切れ端は丸で出す

    hover = []
    step = 12  # 1 時間ごとの当たり（ゆびで触れる幅）
    for k in range(0, n, step):
        chunk = [(i, v) for i, v in points if k <= i < k + step]
        if not chunk:
            continue
        avg = sum(v for _, v in chunk) / len(chunk)
        at = datetime.fromtimestamp(start + k * BUCKET_SECONDS, tz=timezone.get_current_timezone())
        hover.append(
            {
                "x": round(W * k / n, 1),
                "w": round(W * step / n, 1),
                "title": f"{at:%H:%M}〜 {label} 平均 {_fmt(avg)}{unit}、最大 "
                f"{_fmt(max(v for _, v in chunk))}{unit}",
            }
        )
    last = points[-1][1]
    ticks = []
    for h in (24, 18, 12, 6):
        at = datetime.fromtimestamp(now - h * 3600, tz=timezone.get_current_timezone())
        ticks.append({"x": round(W * (HOURS - h) / HOURS, 1), "text": f"{at:%H時}"})
    return {
        "label": label,
        "empty": False,
        "unit": unit,
        "segments": [s for s in segments if " " in s],
        "dots": [s.split(",") for s in lone],
        "hover": hover,
        "top": _fmt(scale_top),
        "mid_y": round(H / 2, 1),
        "now": _fmt(last),
        "peak": _fmt(peak),
        "ticks": ticks,
        "w": W,
        "h": H,
    }


def _nice_top(v: float) -> float:
    for top in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000):
        if v <= top:
            return float(top)
    return v


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}".rstrip("0").rstrip(".")


# ---- ゲームの値 ----


def game_facts(now) -> dict[str, Any]:
    world = World.objects.order_by("id").first()
    day = now - timedelta(hours=24)
    users = get_user_model().objects
    islands = Island.objects.filter(owner__isnull=False)
    failures = LoginAttempt.objects.filter(touched_at__gte=day).aggregate(n=Sum("failures"))["n"]
    facts = {
        "world": world,
        "late_minutes": None,
        "nations": islands.filter(inactive=False).count(),
        "resting": islands.filter(inactive=True).count(),
        "active_24h": users.filter(last_login__gte=day).count(),
        "signups_24h": users.filter(date_joined__gte=day).count(),
        "login_failures_24h": failures or 0,
    }
    if world and world.next_turn_at and not world.paused and world.next_turn_at < now:
        facts["late_minutes"] = int((now - world.next_turn_at).total_seconds() // 60)
    return facts


# ---- 画面 ----


def view(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    folder = Path(settings.HAKONIWA_STATUS_DIR)
    now_ts = time.time()
    status = read_status(folder)
    rows = read_history(folder, now_ts)
    written_ago = None
    if status and isinstance(status.get("written_at"), int | float):
        written_ago = int(now_ts - status["written_at"])

    cpu = status.get("cpu_percent") if status and written_ago is not None else None
    if cpu is None or (written_ago or 0) > STALE_SECONDS:
        cpu = cpu_percent_now()
    mem, mem_detail = memory_percent()
    temp = temperature_c()
    meters = [
        Meter("CPU", cpu, "%", "この 1 分の平均" if status else "いま", 80, 95),
        Meter("メモリ", mem, "%", mem_detail, 80, 90),
    ]
    for name, path in disks():
        try:
            u = shutil.disk_usage(path)
        except OSError:
            continue
        meters.append(
            Meter(
                name,
                round(100 * u.used / u.total),
                "%",
                f"{_gib(u.used)} / {_gib(u.total)}",
                70,
                85,
            )
        )
    if temp is not None:
        meters.append(Meter("温度", temp, "℃", "CPU", 80, 90, top=105))

    charts = []
    if rows:
        charts = [
            chart(rows, 1, "CPU", "%", now_ts, top=100),
            chart(rows, 2, "メモリ", "%", now_ts, top=100),
            chart(rows, 4, "温度", "℃", now_ts, top=100),
            chart(rows, 5, "ページの応答", "ミリ秒", now_ts),
        ]
        charts = [c for c in charts if not c["empty"]]

    units = []
    alerts = []
    backup_at = None
    if status:
        for u in status.get("units") or []:
            name = str(u.get("name", ""))
            state = str(u.get("state", ""))
            ok = state == "active"
            units.append({"name": UNIT_NAMES.get(name, name), "ok": ok, "state": state})
        alerts = [ALERT_NAMES.get(str(a), str(a)) for a in status.get("alerts") or []]
        at = (status.get("backup") or {}).get("at")
        if isinstance(at, int | float):
            backup_at = datetime.fromtimestamp(at, tz=timezone.get_current_timezone())

    now = timezone.now()
    context = {
        **_admin_context(request),
        "title": "サーバーの様子",
        "status": status,
        "written_ago": written_ago,
        "stale": written_ago is not None and written_ago > STALE_SECONDS,
        "meters": meters,
        "charts": charts,
        "units": units,
        "alerts": alerts,
        "backup_at": backup_at,
        "backup_hours": None
        if backup_at is None
        else int((now - backup_at).total_seconds() // 3600),
        "load": load_text(),
        "uptime": uptime_text(),
        "version": settings.HAKONIWA_VERSION or "（不明）",
        "game": game_facts(now),
        "now": now,
    }
    return render(request, "admin/server_status.html", context)


def _admin_context(request) -> dict[str, Any]:
    from django.contrib import admin

    return admin.site.each_context(request)
