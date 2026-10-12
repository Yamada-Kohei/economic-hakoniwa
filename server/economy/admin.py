from django.contrib import admin

from server.economy import models as m


@admin.register(m.Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["island", "name", "balance"]
    list_filter = ["island"]
    search_fields = ["name"]


@admin.register(m.Bond)
class BondAdmin(admin.ModelAdmin):
    list_display = ["island", "number", "issued", "term", "rate", "face"]
    list_filter = ["island"]


@admin.register(m.MarketRecord)
class MarketRecordAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "good", "price", "volume", "demand", "supply"]
    list_filter = ["island", "good"]


@admin.register(m.FacilityBook)
class FacilityBookAdmin(admin.ModelAdmin):
    list_display = ["island", "turn", "facility", "deposit", "debt", "inventory"]
    list_filter = ["island"]
