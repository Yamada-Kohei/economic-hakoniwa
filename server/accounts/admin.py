from django.contrib import admin

from server.accounts.models import SignupRecord


@admin.register(SignupRecord)
class SignupRecordAdmin(admin.ModelAdmin):
    """消すと、その IP からもう一度登録できるようになる。"""

    list_display = ["__str__", "created_at"]
    readonly_fields = ["ip_hash", "created_at"]

    def has_add_permission(self, request):
        return False
