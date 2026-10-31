"""管理画面の「サーバーの様子」（server/web/server_status.py）。"""

from __future__ import annotations

import json
import time

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.web import server_status

PASSWORD = "pass-for-tests-123"


@pytest.fixture
def admin_client(db):
    user = User.objects.create_superuser("admin", password=PASSWORD)
    client = Client()
    client.force_login(user)
    return client


def _write_status(folder, now, **extra):
    status = {
        "written_at": int(now),
        "cpu_percent": 12,
        "memory_percent": 30,
        "load1": 0.4,
        "temperature_c": 45,
        "game_page": {"code": "200", "ms": 85},
        "tunnel_code": "200",
        "units": [
            {"name": "hakoniwa-gunicorn", "state": "active"},
            {"name": "caddy", "state": "failed"},
        ],
        "failed_units": "caddy.service",
        "errors_24h": 3,
        "backup": {"at": int(now) - 7200, "bytes": 123456},
        "last_deploy": "2026-10-31 21:22:00 反映した",
        "upgradable": 2,
        "reboot_required": True,
        "alerts": ["tunnel-down"],
        **extra,
    }
    (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")


def test_only_superusers_can_open_it(db):
    assert Client().get("/admin/server/").status_code == 302  # 管理画面のログインへ
    staff = User.objects.create_user("staff", password=PASSWORD, is_staff=True)
    client = Client()
    client.force_login(staff)
    assert client.get("/admin/server/").status_code == 403
    player = User.objects.create_user("player", password=PASSWORD)
    client.force_login(player)
    assert client.get("/admin/server/").status_code == 302


def test_without_the_written_values_it_shows_what_it_can_read(admin_client, settings, tmp_path):
    settings.HAKONIWA_STATUS_DIR = str(tmp_path)
    page = admin_client.get("/admin/server/").content.decode()
    assert "サーバーの様子" in page
    assert "この機械では値を書き出していません" in page
    assert "メモリ" in page and "SSD（/）" in page
    assert "この 24 時間" not in page


def test_with_the_written_values_it_draws_meters_charts_and_services(
    admin_client, settings, tmp_path, world
):
    settings.HAKONIWA_STATUS_DIR = str(tmp_path)
    now = time.time()
    _write_status(tmp_path, now)
    rows = [f"{int(now) - 60 * k},{k % 50},{40},0.5,{50},{100 + k % 7}" for k in range(600)]
    rows.append("not,a,row")
    (tmp_path / "history.csv").write_text("\n".join(rows) + "\n", encoding="ascii")
    page = admin_client.get("/admin/server/").content.decode()
    assert "この 24 時間" in page and page.count('viewBox="0 -10 ') == 4
    assert "<polyline" in page and "<title>" in page
    assert "トンネルがつながっていない" in page  # 見張りの知らせ
    assert "配信（Caddy）" in page and "failed" in page
    assert "3 件" in page and "再起動が要ります" in page
    assert "85 ミリ秒" in page
    assert f"<td>{world.turn}" in page


def test_stale_values_are_called_out(admin_client, settings, tmp_path):
    settings.HAKONIWA_STATUS_DIR = str(tmp_path)
    _write_status(tmp_path, time.time() - 600)
    page = admin_client.get("/admin/server/").content.decode()
    assert "で止まっています" in page


def test_the_admin_home_links_to_it(admin_client):
    page = admin_client.get("/admin/").content.decode()
    assert 'href="/admin/server/"' in page and "サーバーの様子" in page


def test_chart_breaks_the_line_where_records_are_missing():
    now = 1_800_000_000.0
    rows = [[now - 3600 * 20 + 60 * k, 10.0, None, None, None, None] for k in range(30)]
    rows += [[now - 3600 * 2 + 60 * k, 90.0, None, None, None, None] for k in range(30)]
    c = server_status.chart(rows, 1, "CPU", "%", now, top=100)
    assert not c["empty"] and len(c["segments"]) == 2
    assert c["peak"] == "90" and c["now"] == "90"
    assert server_status.chart(rows, 2, "メモリ", "%", now)["empty"]


def test_meter_levels():
    assert server_status.Meter("CPU", 50, "%", "", 80, 95).level == "good"
    assert server_status.Meter("CPU", 85, "%", "", 80, 95).level == "serious"
    assert server_status.Meter("CPU", 99, "%", "", 80, 95).level == "critical"
    assert server_status.Meter("CPU", None, "%", "", 80, 95).level_text == "読めない"
