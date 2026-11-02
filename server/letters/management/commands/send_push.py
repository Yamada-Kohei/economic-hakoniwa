"""予定を持ち主ごとにまとめて送る。タイマーから 1 分ごとに呼ぶ。"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from server.accounts import keys as account_keys
from server.letters import push, services
from server.letters.models import PushOutbox, PushSubscription

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "公電のブラウザ通知の予定をまとめて送り、送信済みの予定を 7 日で消します"

    def handle(self, *args, **options) -> None:
        if not push.configured():
            return
        send = push.load_sender()
        now = timezone.now()
        PushOutbox.objects.filter(sent_at__lt=now - timedelta(days=7)).delete()
        users = list(
            PushOutbox.objects.filter(sent_at__isnull=True)
            .order_by("user_id")
            .values_list("user_id", flat=True)
            .distinct()
        )
        for user_id in users:
            self._send_user(user_id, send)

    def _send_user(self, user_id: int, send) -> None:
        # PostgreSQL では同じ持ち主への並行送信を直列にする。
        # SQLite での運用はタイマーを重ねて起動しない。
        with transaction.atomic():
            rows = list(
                PushOutbox.objects.select_for_update()
                .filter(user_id=user_id, sent_at__isnull=True)
                .order_by("id")
            )
            if not rows:
                return
            row_ids = [row.pk for row in rows]
            last_id = row_ids[-1]
            island = services.own_island(rows[0].user)
            pending = PushOutbox.objects.filter(pk__in=row_ids, sent_at__isnull=True)
            eligible = pending.filter(
                letter__recipient__owner_id=user_id,
                letter__read_at__isnull=True,
                letter__hidden=False,
            ).exists()
            if not eligible or (island and account_keys.is_ai_island(island)):
                pending.update(sent_at=timezone.now())
                return
            unread = services.inbox(island).filter(read_at__isnull=True) if island else None
            count = unread.count() if unread is not None else 0
            if not count:
                pending.update(sent_at=timezone.now())
                return
            body = (
                f"{unread.first().sender.name}から公電が届きました"
                if count == 1
                else f"未読の公電が {count} 通あります"
            )
            payload = json.dumps(
                {"title": "経済版箱庭諸国", "body": body, "url": reverse("letters")},
                ensure_ascii=False,
            )
            failed = False
            for subscription in PushSubscription.objects.filter(user_id=user_id):
                if subscription.last_outbox_id >= last_id:
                    continue
                try:
                    push.validate_endpoint(subscription.endpoint)
                except ValueError:
                    subscription.delete()
                    continue
                try:
                    send(
                        subscription_info={
                            "endpoint": subscription.endpoint,
                            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                        },
                        data=payload,
                        vapid_private_key=settings.HAKONIWA_VAPID_PRIVATE_KEY,
                        vapid_claims={"sub": settings.HAKONIWA_VAPID_CONTACT},
                        headers={"Topic": push.TOPIC},
                        ttl=push.TTL,
                        timeout=push.TIMEOUT,
                    )
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    if status in (404, 410):
                        subscription.delete()
                    else:
                        failed = True
                else:
                    PushSubscription.objects.filter(pk=subscription.pk, user_id=user_id).update(
                        last_ok_at=timezone.now(), last_outbox_id=last_id
                    )
            if not failed:
                pending.update(sent_at=timezone.now())
                return
            pending.update(attempts=F("attempts") + 1)
            dropped = pending.filter(attempts__gte=push.MAX_ATTEMPTS).update(sent_at=timezone.now())
            if dropped:
                # 宛先・鍵・公電の件名や本文はログに出さない。
                logger.warning("公電の通知を送信失敗 3 回で破棄: user=%s rows=%s", user_id, dropped)
