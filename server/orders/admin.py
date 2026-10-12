from django.contrib import admin

from server.orders.models import Order


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["island", "seq", "kind", "status", "reason", "executed_turn", "created_at"]
    list_filter = ["status", "kind"]
