from django.core.management import call_command
from django.core.management.base import BaseCommand

from server.world.models import World


class Command(BaseCommand):
    help = "開発用の準備: DB を作り、サンプルの世界がなければ作る"

    def handle(self, *args, **options):
        call_command("migrate", interactive=False, verbosity=0)
        self.stdout.write("DB を準備しました")
        if not World.objects.exists():
            call_command("create_sample_world", islands=3, seed=1)
        self.stdout.write(
            "python manage.py runserver で起動し、http://127.0.0.1:8000/ を開いてください"
        )
