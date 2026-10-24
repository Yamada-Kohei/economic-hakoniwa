from django.contrib import admin

from server.accounts.models import ApiKey, SignupRecord


@admin.register(SignupRecord)
class SignupRecordAdmin(admin.ModelAdmin):
    """消すと、その IP からもう一度登録できるようになる。「追加で登録できる人数」を入れると、
    そのアカウントの接続元から、上限に加えてその人数まで登録できる（特定の人に複数登録を認める）。"""

    list_display = ["__str__", "user", "extra", "created_at"]
    list_editable = ["extra"]
    search_fields = ["user__username"]
    readonly_fields = ["ip_hash", "user", "created_at"]

    def has_add_permission(self, request):
        return False


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """AI 用の鍵。鍵そのものは持たない（ハッシュだけ）。取り消すには「取り消した時刻」を入れる。"""

    list_display = ["user", "prefix", "name", "created_turn", "last_used_turn", "revoked_at"]
    list_filter = ["revoked_at"]
    search_fields = ["user__username", "name", "prefix"]
    readonly_fields = [
        "user",
        "prefix",
        "digest",
        "created_at",
        "created_turn",
        "last_used_at",
        "last_used_turn",
    ]

    def has_add_permission(self, request):
        return False
