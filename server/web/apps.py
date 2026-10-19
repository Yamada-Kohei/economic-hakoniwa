from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_migrate


class WebConfig(AppConfig):
    name = "server.web"
    verbose_name = "画面"

    def ready(self):
        post_migrate.connect(_sync_updates, sender=self)


def _sync_updates(sender, using="default", **kwargs):
    """反映（migrate）のたびに、コミットの「更新履歴:」の行を更新履歴へ写す（表を戻したときは何もしない）。"""
    from server.web import top
    from server.web.models import SiteUpdate

    if SiteUpdate._meta.db_table in connections[using].introspection.table_names():
        top.sync_updates()
