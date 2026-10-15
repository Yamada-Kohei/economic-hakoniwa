"""2026-10-16 の Codex のレビューの直し: 巻き戻し（A01）、他国から見える範囲（A02・A07）。"""

from __future__ import annotations

import pytest

from server.economy.models import Bond, Loan
from server.game import get_game
from server.orders.models import Order
from server.turns.models import Backup, TurnLog
from server.turns.services import TurnError, rollback, run_turn
from server.world import models as m
from tests.server.test_phase1 import _login, _plan

pytestmark = pytest.mark.django_db


def _found_late(client) -> m.Island:
    """ターン 1 に、プレイヤーが後から建国した島。"""
    _login(client, "late")
    res = client.post("/api/founding", _plan(client), content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    return m.Island.objects.get(owner__username="late")


def _aid(source: m.Island, target: m.Island, amount: int = 100) -> Order:
    return Order.objects.create(
        island=source, seq=1, kind="send_aid", payload={"partner": target.id, "amount": amount}
    )


# --- A01: 巻き戻し ----------------------------------------------------------------------


def test_rollback_before_a_later_island_keeps_the_world_running(client, world):
    """後から建国した島へ援助を送った後、建国の前まで戻しても、次の更新が止まらない。"""
    first = m.Island.objects.order_by("id").first()
    run_turn(force=True)  # ターン 0 → 1（バックアップ: ターン 0）
    late = _found_late(client)
    aid = _aid(first, late)
    run_turn(force=True)  # ターン 1 → 2（援助で、後の島が最初の島の通貨を持つ）
    late.refresh_from_db()
    assert late.ext["trade"]["fx"]

    rollback(0)
    world.refresh_from_db()
    late.refresh_from_db()
    assert world.turn == 0
    # 後の島は残り、建国したときの姿で、ターン 0 に建国したことになる
    assert not late.ext.get("trade", {}).get("fx")
    assert late.ext["founded_turn"] == 0
    assert set(Bond.objects.filter(island=late).values_list("issued", flat=True)) == {0}
    assert TurnLog.objects.filter(island=late, turn=0, kind="island_founded").exists()
    assert m.StatRecord.objects.filter(island=late, turn=0).exists()
    assert not TurnLog.objects.filter(turn__gt=0).exists()
    # 戻したターンに実行した命令は、待ちに戻る
    aid.refresh_from_db()
    assert aid.status == "pending" and aid.executed_turn is None

    assert run_turn(force=True).ran  # 前は帳簿が合わずに止まっていた
    assert run_turn(force=True).ran
    aid.refresh_from_db()
    assert aid.status == "done" and aid.executed_turn == 1


def test_rollback_right_after_founding_keeps_the_new_island(client, world):
    """まだ一度も更新していない島（どのバックアップにもない）は、今の姿のまま残す。"""
    run_turn(force=True)
    late = _found_late(client)
    rollback(0)
    late.refresh_from_db()
    assert late.ext["founded_turn"] == 0
    assert TurnLog.objects.filter(island=late, turn=0, kind="island_founded").exists()
    assert run_turn(force=True).ran


def test_rollback_returns_orders_of_undone_turns(world):
    """戻した先より後の更新で実行した命令は、バックアップの後に届いたものも待ちに戻る。"""
    first = m.Island.objects.order_by("id").first()
    early = Order.objects.create(island=first, seq=1, kind="rename_island", payload={"name": "前"})
    run_turn(force=True)  # ターン 0 → 1 で実行
    run_turn(force=True)  # バックアップ: ターン 1
    later = Order.objects.create(island=first, seq=1, kind="rename_island", payload={"name": "後"})
    run_turn(force=True)  # ターン 2 → 3 で実行
    rollback(1)
    early.refresh_from_db()
    later.refresh_from_db()
    assert (early.status, early.executed_turn) == ("done", 1)
    assert (later.status, later.executed_turn) == ("pending", None)
    assert m.Island.objects.get(pk=first.pk).name == "前"
    run_turn(force=True)
    assert m.Island.objects.get(pk=first.pk).name == "後"


def test_rollback_refuses_a_world_whose_books_do_not_match(client, world):
    """戻した世界の帳簿が合わなければ（外貨を持っていた島を消したなど）、何も変えずに断る。"""
    first = m.Island.objects.order_by("id").first()
    run_turn(force=True)
    late = _found_late(client)
    _aid(first, late)
    run_turn(force=True)
    run_turn(force=True)  # バックアップ: ターン 2（最初の島の帳簿に、後の島の預金がある）
    late.delete()
    backups = Backup.objects.count()
    with pytest.raises(TurnError, match="帳簿が合わない"):
        rollback(2)
    world.refresh_from_db()
    assert world.turn == 3 and Backup.objects.count() == backups


# --- A02: マスの詳細の施設 ------------------------------------------------------------------

OWNER_ONLY = {
    "books",
    "loans",
    "bank",
    "emergency_loan",
    "resolve_methods",
    "ownership_target",
    "subsidy_target",
    "accounting_target",
    "recipe_choices",
}


def _facility(client, island: m.Island, f: m.Facility) -> dict:
    res = client.get(f"/api/islands/{island.id}/tiles/{f.x},{f.y}")
    assert res.status_code == 200
    return res.json()["facility"]


def test_tile_shows_books_loans_and_banks_only_to_the_owner(client, world):
    """施設の帳簿・借入・銀行の帳簿と命令の入力の値は、ログインしていない人にも他国の人にも
    返さない（帳簿の API・金融の API・施設の画面と同じ）。だれにでも見える姿はそのまま。"""
    island = m.Island.objects.order_by("id").first()
    run_turn(force=True)
    bank = island.facilities.filter(kind=get_game().config.get("banking.facility")).first()
    private, debtor = island.facilities.filter(owner="private", built=True, x__gte=0).exclude(
        kind__in=["bank", "housing"]
    )[:2]
    Loan.objects.create(
        island=island,
        number=999,
        lender=f"bank:{bank.number}",
        borrower=f"fac:{debtor.number}",
        purpose="operating",
        principal=1000,
        term=12,
        start=1,
    )

    owner = _login(client, "owner")
    island.owner = owner
    island.save()
    mine = {f.id: _facility(client, island, f) for f in (bank, debtor, private)}
    assert {"bank", "books", "emergency_loan"} <= set(mine[bank.id])
    assert "loans" in mine[debtor.id]
    assert "subsidy_target" in mine[private.id] and "ownership_target" in mine[private.id]

    for viewer in (None, "other"):
        client.logout()
        if viewer:
            _login(client, viewer)
        for f in (bank, debtor, private):
            info = _facility(client, island, f)
            assert not OWNER_ONLY & set(info), (viewer, f.kind)
            for key in ("name", "kind", "workers", "operating_rate", "status_text", "stock"):
                assert info[key] == mine[f.id][key]
        assert _facility(client, island, private)["subsidies"] == []


def test_order_check_is_only_for_the_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    url = f"/api/islands/{island.id}/orders/check"
    body = {"kind": "rename_island", "payload": {"name": "x"}}
    assert client.post(url, body, content_type="application/json").status_code == 403
    _login(client, "other")
    assert client.post(url, body, content_type="application/json").status_code == 403
    island.owner = _login(client, "owner")
    island.save()
    assert client.post(url, body, content_type="application/json").json()["ok"] is True


# --- A07: 待っている命令 --------------------------------------------------------------------


def test_secret_orders_are_hidden_from_others_until_they_run(client, world):
    """前もって公表しない命令（為替介入・利下げなど）は、実行するまで持ち主だけに見える。
    公表する命令（税など）と、実行した命令は誰にでも見える。"""
    island = m.Island.objects.order_by("id").first()
    secret = get_game().defs.orders["set_monetary_policy"]
    assert secret.get("secret") and not get_game().defs.orders["set_taxes"].get("secret")
    url = f"/api/islands/{island.id}/orders"
    # 行政力が 1 でも、利下げ（1）と破綻処理（0）は次の更新で実行する
    Order.objects.create(
        island=island, seq=1, kind="set_monetary_policy", payload={"policy_rate": 1.5}
    )
    Order.objects.create(
        island=island, seq=2, kind="resolve_bank", payload={"x": 0, "y": 0, "method": "bailout"}
    )
    Order.objects.create(island=island, seq=3, kind="set_taxes", payload={"income_tax": 12})

    others = client.get(url).json()
    assert len(others["orders"]) == 1 and "税" in others["orders"][0]["text"]
    island.owner = _login(client, "owner")
    island.save()
    assert len(client.get(url).json()["orders"]) == 3

    run_turn(force=True)  # 銀行の破綻処理は、破綻した銀行がないので失敗する
    done = dict(Order.objects.exclude(status="pending").values_list("kind", "status"))
    assert done["set_monetary_policy"] == "done" and done["resolve_bank"] == "failed"
    mine = client.get(url).json()["recent"]
    client.logout()
    others = client.get(url).json()
    # 実行した利下げは誰にでも見え、失敗した（実行されなかった）伏せる命令は持ち主だけ
    assert [r for r in mine if r not in others["recent"]] == [
        r for r in mine if r["status"] == "failed"
    ]
    assert len([r for r in mine if r["status"] == "failed"]) == 1
    # 残っているのは公表する税の命令だけ（行政力が足りなければ次のターンに回る）
    assert len(others["orders"]) == Order.objects.filter(status="pending").count()


def test_reserve_releases_are_public_and_purchases_are_hidden(client, world):
    """国の倉庫の売り（備蓄の放出）は出した時点で誰にでも見え、買い入れは実行まで持ち主だけ
    （10-15 のカード「売りだけ見せる」）。"""
    island = m.Island.objects.order_by("id").first()
    url = f"/api/islands/{island.id}/orders"
    for seq, side in enumerate(["sell", "buy"], start=1):
        payload = {"good": "wood", "side": side, "quantity": 1, "limit": 1}
        Order.objects.create(island=island, seq=seq, kind="warehouse_trade", payload=payload)

    others = client.get(url).json()["orders"]
    assert [o["payload"]["side"] for o in others] == ["sell"]
    island.owner = _login(client, "owner")
    island.save()
    assert [o["payload"]["side"] for o in client.get(url).json()["orders"]] == ["sell", "buy"]
