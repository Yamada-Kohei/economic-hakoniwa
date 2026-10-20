import random

from django.contrib import admin, messages
from django.db.models import Max

from server.turns.services import TurnError, run_turn
from server.world import models as m
from server.world.services import create_sample_island, get_or_create_world

# 試し遊び・テスト用に、管理画面からまとめて進めるターン数
TURNS_AT_ONCE = 10


@admin.register(m.World)
class WorldAdmin(admin.ModelAdmin):
    list_display = ["__str__", "next_turn_at", "paused", "updating_since"]
    readonly_fields = ["turn", "seed", "updating_since", "packs"]
    fields = ["turn", "seed", "next_turn_at", "paused", "updating_since", "packs", "ext"]
    actions = ["advance_turn", "advance_turns"]

    def get_actions(self, request):
        # 「選択された世界の削除」は「今すぐ1ターン進める」のすぐ上に並び、押し間違えると世界ごと
        # 消えるので、一覧の操作から外す（消すときは世界の画面の「削除」から）
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="今すぐ1ターン進める")
    def advance_turn(self, request, queryset):
        try:
            result = run_turn(force=True)
        except TurnError as e:
            self.message_user(request, str(e), messages.ERROR)
            return
        self.message_user(request, result.message)

    @admin.action(description=f"今すぐ{TURNS_AT_ONCE}ターン進める（テスト用）")
    def advance_turns(self, request, queryset):
        """1ターンずつ続けて進める（ターンごとにバックアップを取る）。途中で失敗したら、
        そこで止めて、何ターン進んだかを出す。失敗したターンは何も書かれない。"""
        done, seconds = 0, 0.0
        for _ in range(TURNS_AT_ONCE):
            try:
                result = run_turn(force=True)
            except Exception as e:  # 原因はログと管理者へのメールに残っている（run_turn）
                turn = m.World.objects.order_by("id").values_list("turn", flat=True).first()
                self.message_user(
                    request,
                    f"{done} ターン進めたところで止まりました（ターン {turn}）: {e}",
                    messages.ERROR,
                )
                return
            done += 1
            seconds += result.seconds
        self.message_user(
            request, f"{done} ターン進めました（ターン {result.turn}、{seconds:.1f} 秒）"
        )


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
    """島の管理。放置された国（命令が途絶えた国）は自動で消さず、ここの「命令が途絶えたターン数」と
    「最後のログイン」を見て、休止中にする・無人の島にする（持ち主を外す）を管理者が選ぶ。"""

    list_display = [
        "name",
        "owner",
        "wx",
        "wy",
        "inactive",
        "idle_turns",
        "last_login",
        "created_at",
    ]
    list_filter = ["inactive"]
    search_fields = ["name"]
    actions = ["make_inactive", "make_active", "release_owner"]

    def get_queryset(self, request):
        # 命令を最後に実行したターン（実行済みの命令がなければ空）
        return (
            super()
            .get_queryset(request)
            .select_related("owner", "world")
            .annotate(last_order_turn=Max("orders__executed_turn"))
        )

    @admin.display(description="命令が途絶えたターン数", ordering="last_order_turn")
    def idle_turns(self, obj) -> int:
        """最後に命令を実行したターン（なければ建国のターン）から、いまのターンまで。"""
        since = obj.last_order_turn or (obj.ext or {}).get("founded_turn") or 0
        return max(0, obj.world.turn - since)

    @admin.display(description="最後のログイン", ordering="owner__last_login")
    def last_login(self, obj):
        return obj.owner.last_login if obj.owner else None

    @admin.action(description="選んだ島を休止中にする（条約や戦争はそのまま残る）")
    def make_inactive(self, request, queryset):
        n = queryset.update(inactive=True)
        self.message_user(request, f"{n} 島を休止中にしました")

    @admin.action(description="選んだ島の休止を解く")
    def make_active(self, request, queryset):
        n = queryset.update(inactive=False)
        self.message_user(request, f"{n} 島の休止を解きました")

    @admin.action(description="選んだ島を無人の島にする（持ち主を外す。島は残る）")
    def release_owner(self, request, queryset):
        n = queryset.exclude(owner=None).update(owner=None)
        self.message_user(request, f"{n} 島の持ち主を外しました")

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
