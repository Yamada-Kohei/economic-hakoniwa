from django.contrib import admin

from server.board.models import Post, Thread


class PostInline(admin.TabularInline):
    model = Post
    extra = 0
    fields = ["author", "body", "created_at", "hidden"]
    readonly_fields = ["author", "body", "created_at"]


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "updated_at", "hidden"]
    list_filter = ["hidden"]
    search_fields = ["title"]
    inlines = [PostInline]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["thread", "author", "created_at", "hidden"]
    list_filter = ["hidden"]
    search_fields = ["body"]
    actions = ["hide"]

    @admin.action(description="選んだ書き込みを非表示にする")
    def hide(self, request, queryset):
        queryset.update(hidden=True)
