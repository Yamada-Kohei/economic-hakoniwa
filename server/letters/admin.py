from django.contrib import admin

from server.letters.models import Letter

# 管理画面では中身を読むだけにして、変えられるのは非表示の印だけにする
SHOWN = ["sender", "recipient", "author", "subject", "body", "turn", "sent_at", "read_at"]


@admin.register(Letter)
class LetterAdmin(admin.ModelAdmin):
    list_display = ["subject", "sender", "recipient", "author", "sent_at", "read_at", "hidden"]
    list_filter = ["hidden"]
    search_fields = ["subject", "body"]
    fields = [*SHOWN, "hidden"]
    readonly_fields = SHOWN
    actions = ["hide"]

    def has_add_permission(self, request) -> bool:
        return False  # 管理画面から手紙を書くことはしない

    @admin.action(description="選んだ手紙を非表示にする")
    def hide(self, request, queryset):
        queryset.update(hidden=True)
