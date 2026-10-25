from django.contrib import admin

from server.accounts.models import ApiKey, LoginAttempt, RateCounter, SignupRecord


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


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    """ログインの失敗の記録（接続元とアカウント名の組み合わせごと。どちらもハッシュ）。
    消すと、その組み合わせの停止が解ける。「停止の終わり」が今より先のものが、停止中。"""

    list_display = ["__str__", "failures", "locked_until", "window_start", "touched_at"]
    list_filter = ["locked_until"]
    readonly_fields = [
        "ip_hash",
        "user_hash",
        "failures",
        "window_start",
        "locked_until",
        "touched_at",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(RateCounter)
class RateCounterAdmin(admin.ModelAdmin):
    """区切りごとの回数（ai-key: AI 用の鍵の処理数、login-ip: 接続元ごとのログインの送信数）。
    接続元が読めなかった人たちは、login-ip の「unknown」の箱にまとまる（key は混ぜたハッシュなので
    見分けはつかないが、ログに警告が出る）。"""

    list_display = ["scope", "key", "window", "count", "window_end"]
    list_filter = ["scope"]
    readonly_fields = ["scope", "key", "window", "window_end", "count"]

    def has_add_permission(self, request):
        return False
