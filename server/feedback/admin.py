from django.contrib import admin

from server.feedback.models import BugReport

# 管理画面では送られた中身を読むだけにして、変えられるのは状態とメモだけにする
SHOWN = [
    "user",
    "island",
    "world_turn",
    "kind",
    "text",
    "url",
    "version",
    "via",
    "context",
    "created_at",
]


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "kind", "short_text", "user", "island", "version", "created_at"]
    list_display_links = ["id", "short_text"]
    list_editable = ["status"]
    list_filter = ["status", "kind", "via", "version"]
    search_fields = ["text", "note", "url", "user__username"]
    date_hierarchy = "created_at"
    fields = [*SHOWN, "status", "note"]
    readonly_fields = SHOWN
    actions = ["mark_working", "mark_done"]

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="何が起きたか")
    def short_text(self, obj) -> str:
        return obj.text[:60]

    @admin.action(description="選んだ報告を「対応中」にする")
    def mark_working(self, request, queryset):
        queryset.update(status="working")

    @admin.action(description="選んだ報告を「済み」にする")
    def mark_done(self, request, queryset):
        queryset.update(status="done")
