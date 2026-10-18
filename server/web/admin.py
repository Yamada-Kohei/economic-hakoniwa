from django.contrib import admin

from server.web.models import AccessCounter


@admin.register(AccessCounter)
class AccessCounterAdmin(admin.ModelAdmin):
    """カウンタの数を直す（引っ越しで前の数から続けるときなど）。"""

    list_display = ["name", "total", "since"]
