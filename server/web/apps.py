from django.apps import AppConfig
from django.db.models.signals import post_migrate


class WebConfig(AppConfig):
    name = "server.web"
    verbose_name = "画面"

    def ready(self):
        post_migrate.connect(_sync_updates, sender=self)


def _sync_updates(sender, **kwargs):
    """反映（migrate）のたびに、コミットの「更新履歴:」の行を更新履歴へ写す。"""
    from server.web import top

    top.sync_updates()
