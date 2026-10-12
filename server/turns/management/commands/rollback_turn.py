from django.core.management.base import BaseCommand, CommandError

from server.turns.services import TurnError, rollback


class Command(BaseCommand):
    help = "バックアップから、指定したターンの更新前の状態に戻す"

    def add_arguments(self, parser):
        parser.add_argument("turn", type=int, help="戻したいターン番号")

    def handle(self, *args, turn: int, **options):
        try:
            state = rollback(turn)
        except TurnError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(f"ターン {state.turn} に戻しました")
