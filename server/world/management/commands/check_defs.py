from django.core.management.base import BaseCommand, CommandError

from hakoniwa_core.defs import DefsError
from server.game import get_game


class Command(BaseCommand):
    help = "定義データ（パック）を読み込んで検査する"

    def handle(self, *args, **options):
        get_game.cache_clear()
        try:
            game = get_game()
        except DefsError as e:
            for issue in e.issues:
                self.stderr.write(str(issue))
            raise CommandError(f"定義データに {len(e.issues)} 件の誤りがあります") from e
        defs = game.defs
        packs = ", ".join(f"{p.name} {p.version}" for p in defs.packs)
        counts = ", ".join(f"{k} {len(v)}" for k, v in defs.tables.items())
        self.stdout.write(f"OK: パック {packs} / {counts}")
