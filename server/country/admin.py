from django.contrib import admin
from django.utils.html import format_html

from server.country.models import Flag, Note, Profile

# 管理画面では中身を読むだけにして、変えられるのは非表示の印だけにする（国旗の画像は見られる）


class HideOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Flag)
class FlagAdmin(HideOnlyAdmin):
    list_display = ["island", "width", "height", "updated_at", "hidden"]
    list_filter = ["hidden"]
    fields = ["island", "preview", "width", "height", "updated_at", "hidden"]
    readonly_fields = ["island", "preview", "width", "height", "updated_at"]
    actions = ["hide"]

    @admin.display(description="国旗")
    def preview(self, obj):
        return format_html(
            '<img src="/islands/{}/flag" alt="" style="max-width:240px;background:#fff">',
            obj.island_id,
        )

    @admin.action(description="選んだ国旗を非表示にする（仮の旗に戻る）")
    def hide(self, request, queryset):
        queryset.update(hidden=True)


@admin.register(Profile)
class ProfileAdmin(HideOnlyAdmin):
    list_display = ["island", "short_intro", "era_kind", "updated_at", "intro_hidden"]
    list_filter = ["intro_hidden", "era_kind"]
    search_fields = ["intro"]
    fields = ["island", "intro", "era_kind", "eras", "updated_at", "intro_hidden"]
    readonly_fields = ["island", "intro", "era_kind", "eras", "updated_at"]
    actions = ["hide"]

    @admin.display(description="紹介文")
    def short_intro(self, obj) -> str:
        return obj.intro[:40]

    @admin.action(description="選んだ紹介文を非表示にする")
    def hide(self, request, queryset):
        queryset.update(intro_hidden=True)


@admin.register(Note)
class NoteAdmin(HideOnlyAdmin):
    list_display = ["text", "visitor", "island", "turn", "posted_at", "hidden"]
    list_filter = ["hidden"]
    search_fields = ["text"]
    fields = ["world", "island", "visitor", "author", "text", "turn", "posted_at", "hidden"]
    readonly_fields = ["world", "island", "visitor", "author", "text", "turn", "posted_at"]
    actions = ["hide"]

    @admin.action(description="選んだ一言を非表示にする")
    def hide(self, request, queryset):
        queryset.update(hidden=True)
