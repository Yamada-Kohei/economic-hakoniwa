from django.contrib import admin, messages

from server.turns.models import Backup, TurnLog
from server.turns.services import TurnError, rollback


@admin.register(TurnLog)
class TurnLogAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "kind", "args"]
    list_filter = ["kind"]


@admin.register(Backup)
class BackupAdmin(admin.ModelAdmin):
    list_display = ["__str__", "created_at"]
    fields = ["world", "turn", "created_at"]
    readonly_fields = fields
    actions = ["rollback_to"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="このターンの更新前に巻き戻す")
    def rollback_to(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "バックアップを1つだけ選んでください", messages.ERROR)
            return
        try:
            state = rollback(queryset.get().turn)
        except TurnError as e:
            self.message_user(request, str(e), messages.ERROR)
            return
        self.message_user(request, f"ターン {state.turn} に戻しました")
