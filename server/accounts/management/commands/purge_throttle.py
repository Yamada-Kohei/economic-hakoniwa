"""回数制限の古い記録を消す（ログインの失敗と、区切りごとの数え）。

記録は新しい記録を作るときについでに消えるが、使われない期間が長かったときのために、cron などから
呼べるようにしてある。期限は HAKONIWA_THROTTLE_KEEP_HOURS（既定 24 時間）。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from server.accounts import loginguard, throttle


class Command(BaseCommand):
    help = "回数制限の古い記録（ログインの失敗・区切りごとの数え）を消す"

    def handle(self, *args, **options):
        attempts = loginguard.purge_attempts()
        counters = throttle.purge()
        self.stdout.write(
            f"ログインの失敗の記録 {attempts} 件、回数の数え {counters} 件を消しました"
        )
