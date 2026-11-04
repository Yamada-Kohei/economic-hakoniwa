from django.contrib import admin, messages
from django.db import transaction

from server.hall.services import MODERATED_TEXT
from server.turns.models import Backup, TurnLog
from server.turns.services import TurnError, rollback
from server.world.models import Island, World


@admin.register(TurnLog)
class TurnLogAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "kind", "args"]
    list_filter = ["kind"]
    actions = ["moderate_hall_text"]

    @admin.action(description="国際会議場の出来事の文を非表示にする")
    @transaction.atomic
    def moderate_hall_text(self, request, queryset):
        """選んだ出来事の書いた文（宣戦の理由・条約の題名と本文）を隠す。条約の出来事なら、その条約の
        ほかの出来事・世界の条約の記録・違反の記録の題名も同じ文にする（宣戦や条約が起きたことは残す）。"""
        count = 0
        for log in queryset.filter(args__hall=True).select_related("island"):
            log.args = {**log.args, "moderated": True}
            log.save(update_fields=["args"])
            count += 1
            treaty_id = log.args.get("treaty")
            if treaty_id is not None:
                _moderate_treaty(log.island.world_id, treaty_id)
        self.message_user(request, f"国際会議場の出来事 {count} 件の文を非表示にしました")


def _moderate_treaty(world_id: int, treaty_id: int) -> None:
    world = World.objects.select_for_update().get(pk=world_id)
    for treaty in (world.ext or {}).get("diplomacy", {}).get("treaties", []):
        if treaty["id"] == treaty_id:
            treaty.update(title=MODERATED_TEXT, text=MODERATED_TEXT)
    world.save(update_fields=["ext"])
    for other in TurnLog.objects.filter(
        island__world_id=world_id, args__hall=True, args__treaty=treaty_id
    ):
        if not other.args.get("moderated"):
            other.args = {**other.args, "moderated": True}
            other.save(update_fields=["args"])
    for island in Island.objects.filter(world_id=world_id).select_for_update():
        rows = (island.ext or {}).get("diplomacy", {}).get("violations", [])
        hit = [v for v in rows if v.get("treaty") == treaty_id]
        for v in hit:
            v["title"] = MODERATED_TEXT
        if hit:
            island.save(update_fields=["ext"])


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
