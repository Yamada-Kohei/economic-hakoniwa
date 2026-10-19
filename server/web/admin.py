from django.contrib import admin

from server.web.models import AccessCounter, KiribanGetter, SiteUpdate


@admin.register(AccessCounter)
class AccessCounterAdmin(admin.ModelAdmin):
    """カウンタの数を直す（引っ越しで前の数から続けるときなど）。"""

    list_display = ["name", "total", "since"]


@admin.register(KiribanGetter)
class KiribanGetterAdmin(admin.ModelAdmin):
    """キリ番ゲッター様の一覧。載せたくない行は消す。"""

    list_display = ["number", "name", "island_name", "at"]
    search_fields = ["name", "island_name"]
    readonly_fields = ["user", "at"]


@admin.register(SiteUpdate)
class SiteUpdateAdmin(admin.ModelAdmin):
    """トップの更新履歴。コミットの「更新履歴:」の行から写した行も、ここで直す・隠すができる。"""

    list_display = ["date", "text", "hidden", "from_commit"]
    list_editable = ["hidden"]
    list_filter = ["hidden"]
    search_fields = ["text"]
    date_hierarchy = "date"

    @admin.display(boolean=True, description="コミットから")
    def from_commit(self, obj):
        return bool(obj.key and obj.key.startswith("git:"))
