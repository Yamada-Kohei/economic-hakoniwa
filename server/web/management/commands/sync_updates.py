"""コミットの「更新履歴:」の行と top.yaml の updates を、トップの更新履歴へ写す。

migrate のたびにも同じことをする（server/web/apps.py）。"""

from django.core.management.base import BaseCommand

from server.web import top


class Command(BaseCommand):
    help = "コミットの「更新履歴:」の行を、トップの更新履歴へ写す"

    def handle(self, *args, **options):
        self.stdout.write(f"更新履歴を {top.sync_updates()} 行写しました")
