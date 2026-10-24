from django.contrib import admin
from django.db.models import Count

from server.board.models import Board, NgRule, Post, Report, Thread


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "order", "staff_only", "max_threads"]
    list_editable = ["order", "staff_only", "max_threads"]


class PostInline(admin.TabularInline):
    model = Post
    extra = 0
    fields = ["number", "author", "name", "ident", "body", "created_at", "hidden"]
    readonly_fields = ["number", "author", "name", "ident", "body", "created_at"]
    show_change_link = True


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "board",
        "post_count",
        "bumped_at",
        "pinned",
        "closed",
        "archived",
        "hidden",
    ]
    list_filter = ["board", "pinned", "closed", "archived", "hidden"]
    search_fields = ["title"]
    inlines = [PostInline]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = [
        "thread",
        "number",
        "author",
        "name",
        "ident",
        "created_at",
        "reports",
        "hidden",
    ]
    list_filter = ["hidden", "by_staff"]
    search_fields = ["body", "ident", "author__username"]
    actions = ["hide", "show"]
    raw_id_fields = ["thread", "author", "island"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(report_count=Count("reports"))

    @admin.display(description="通報", ordering="report_count")
    def reports(self, obj):
        return obj.report_count

    @admin.action(description="選んだ書き込みを非表示にする（あぼーん）")
    def hide(self, request, queryset):
        queryset.update(hidden=True)

    @admin.action(description="選んだ書き込みを表示に戻す")
    def show(self, request, queryset):
        queryset.update(hidden=False)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["post", "reason", "reporter", "created_at", "handled"]
    list_filter = ["handled"]
    actions = ["hide_posts", "mark_handled"]
    raw_id_fields = ["post", "reporter"]

    @admin.action(description="通報された書き込みを非表示にして、対応済みにする")
    def hide_posts(self, request, queryset):
        Post.objects.filter(pk__in=queryset.values("post_id")).update(hidden=True)
        queryset.update(handled=True)

    @admin.action(description="対応済みにする（書き込みはそのまま）")
    def mark_handled(self, request, queryset):
        queryset.update(handled=True)


@admin.register(NgRule)
class NgRuleAdmin(admin.ModelAdmin):
    list_display = ["user", "kind", "word", "label", "created_at"]
    raw_id_fields = ["user", "target"]
