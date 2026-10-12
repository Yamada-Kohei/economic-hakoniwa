import random

from django.contrib import admin, messages

from server.turns.services import TurnError, run_turn
from server.world import models as m
from server.world.services import create_sample_island, get_or_create_world


@admin.register(m.World)
class WorldAdmin(admin.ModelAdmin):
    list_display = ["__str__", "next_turn_at", "paused", "updating_since"]
    readonly_fields = ["turn", "seed", "updating_since", "packs"]
    fields = ["turn", "seed", "next_turn_at", "paused", "updating_since", "packs", "ext"]
    actions = ["advance_turn"]

    @admin.action(description="今すぐ1ターン進める")
    def advance_turn(self, request, queryset):
        try:
            result = run_turn(force=True)
        except TurnError as e:
            self.message_user(request, str(e), messages.ERROR)
            return
        self.message_user(request, result.message)


class TileInline(admin.TabularInline):
    model = m.Tile
    extra = 0
    fields = ["x", "y", "terrain", "population", "zone", "pollution"]
    readonly_fields = ["x", "y"]
    show_change_link = True

    def get_queryset(self, request):
        # 1024 マスすべては重いので、陸地と浅瀬だけ並べる
        return super().get_queryset(request).exclude(terrain="sea")


@admin.register(m.Island)
class IslandAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "wx", "wy", "inactive", "created_at"]
    search_fields = ["name"]

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["name", "owner"]
        return [
            "name",
            "owner",
            "wx",
            "wy",
            "size",
            "capital_x",
            "capital_y",
            "resources",
            "regime",
            "names",
            "inactive",
            "ext",
        ]

    def get_readonly_fields(self, request, obj=None):
        return ["wx", "wy", "size", "capital_x", "capital_y"] if obj else []

    def get_inlines(self, request, obj):
        return [TileInline] if obj else []

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        # 新しい島: 空いている区画に、ランダムな形・資源・体制で建国した島を作る
        created = create_sample_island(
            get_or_create_world(), obj.name, random.Random(), owner=obj.owner
        )
        obj.pk = created.pk
        obj.refresh_from_db()


@admin.register(m.Tile)
class TileAdmin(admin.ModelAdmin):
    list_display = ["island", "x", "y", "terrain", "population", "zone", "pollution"]
    list_filter = ["terrain", "zone"]


@admin.register(m.Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ["island", "number", "kind", "x", "y", "owner", "built", "workers", "status"]
    list_filter = ["kind", "built", "owner"]
    search_fields = ["name"]


@admin.register(m.Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ["island", "facility", "good", "made_turn", "quantity"]
    list_filter = ["good"]


@admin.register(m.StatRecord)
class StatRecordAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "name", "value"]
    list_filter = ["name"]
