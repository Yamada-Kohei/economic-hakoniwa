from django.contrib import admin
from django.utils import timezone

from server.feedback.models import BugReport

# 管理画面では送られた中身を読むだけにして、状態・本人への返事・管理者用のメモを扱う
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
    "replied_at",
]


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "kind", "short_text", "user", "island", "version", "created_at"]
    list_display_links = ["id", "short_text"]
    list_editable = ["status"]
    list_filter = ["status", "kind", "via", "version"]
    search_fields = ["text", "reply", "note", "url", "user__username"]
    date_hierarchy = "created_at"
    fields = [*SHOWN, "status", "reply", "note"]
    readonly_fields = SHOWN
    actions = ["mark_working", "mark_done"]

    def has_add_permission(self, request) -> bool:
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET":
            obj = self.get_object(request, object_id)
            if obj is not None and self.has_view_or_change_permission(request, obj):
                # 一覧を開いただけでは読んだことにせず、ほかの状態を上書きしない
                BugReport.objects.filter(pk=obj.pk, status="new").update(status="read")
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change) -> None:
        if "reply" in form.changed_data:
            obj.replied_at = timezone.now() if obj.reply else None
        super().save_model(request, obj, form, change)

    @admin.display(description="何が起きたか")
    def short_text(self, obj) -> str:
        return obj.text[:60]

    @admin.action(description="選んだ報告を「対応中」にする")
    def mark_working(self, request, queryset):
        queryset.update(status="working")

    @admin.action(description="選んだ報告を「済み」にする")
    def mark_done(self, request, queryset):
        queryset.update(status="done")
