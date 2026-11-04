from django.contrib import admin, messages
from django.db import transaction

from server.hall.services import MODERATED_TEXT
from server.turns.models import Backup, TurnLog
from server.turns.services import TurnError, rollback
from server.world.models import World


@admin.register(TurnLog)
class TurnLogAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "kind", "args"]
    list_filter = ["kind"]
    actions = ["moderate_hall_text"]

    @admin.action(description="国際会議場の出来事の文を非表示にする")
    @transaction.atomic
    def moderate_hall_text(self, request, queryset):
        count = 0
        for log in queryset.filter(args__hall=True).select_related("island"):
            log.args = {**log.args, "moderated": True}
            log.save(update_fields=["args"])
            treaty_id = log.args.get("treaty")
            if treaty_id is not None:
                world = World.objects.select_for_update().get(pk=log.island.world_id)
                for treaty in (world.ext or {}).get("diplomacy", {}).get("treaties", []):
                    if treaty["id"] == treaty_id:
                        treaty.update(title=MODERATED_TEXT, text=MODERATED_TEXT)
                world.save(update_fields=["ext"])
            count += 1
        self.message_user(request, f"国際会議場の出来事 {count} 件の文を非表示にしました")


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
