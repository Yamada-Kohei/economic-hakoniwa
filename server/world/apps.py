from django.apps import AppConfig


class WorldConfig(AppConfig):
    name = "server.world"
    verbose_name = "世界"

    def ready(self) -> None:
        from server.world import checks  # noqa: F401  起動時の定義データ検査を登録する
