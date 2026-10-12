"""ターン更新: cron などから短い間隔で呼ぶ。次の更新時刻になっていなければ何もしない。"""

from django.core.management.base import BaseCommand, CommandError

from server.turns.services import TurnError, run_turn


class Command(BaseCommand):
    help = "次の更新時刻になっていれば、世界を1ターン進める"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="時刻と一時停止を無視して進める")

    def handle(self, *args, force: bool = False, **options):
        try:
            result = run_turn(force=force)
        except TurnError as e:
            raise CommandError(str(e)) from e
        suffix = f"（{result.seconds:.2f} 秒）" if result.ran else ""
        self.stdout.write(result.message + suffix)
