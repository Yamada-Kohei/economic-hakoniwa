from django.contrib import admin

from server.hall.models import Statement

# 管理画面では中身を読むだけにして、変えられるのは非表示の印だけにする
SHOWN = ["world", "island", "author", "title", "body", "turn", "posted_at"]


@admin.register(Statement)
class StatementAdmin(admin.ModelAdmin):
    list_display = ["title", "island", "author", "turn", "posted_at", "hidden"]
    list_filter = ["hidden"]
    search_fields = ["title", "body"]
    fields = [*SHOWN, "hidden"]
    readonly_fields = SHOWN
    actions = ["hide"]

    def has_add_permission(self, request) -> bool:
        return False  # 管理画面から声明を出すことはしない

    @admin.action(description="選んだ声明を非表示にする")
    def hide(self, request, queryset):
        queryset.update(hidden=True)
