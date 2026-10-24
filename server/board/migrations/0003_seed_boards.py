"""板を 5 つ置き、これまでのスレッドを「雑談」に移して書き込みに番号を振る。"""

from django.db import migrations

BOARDS = [
    ("chat", "雑談", "なんでも話す場所。", 1, False),
    ("diplomacy", "外交", "同盟・貿易・条約の相談や募集。公式の声明は国際会議場へ。", 2, False),
    ("questions", "質問", "遊び方や仕組みの質問。", 3, False),
    ("strategy", "攻略", "うまくいったやり方や数字の共有。", 4, False),
    ("news", "運営からの告知", "運営からのお知らせ。管理者だけが書けます。", 5, True),
]


def seed(apps, schema_editor):
    Board = apps.get_model("board", "Board")
    Thread = apps.get_model("board", "Thread")
    Post = apps.get_model("board", "Post")
    boards = {}
    for slug, name, description, order, staff_only in BOARDS:
        boards[slug], _ = Board.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": description,
                "order": order,
                "staff_only": staff_only,
            },
        )
    for thread in Thread.objects.filter(board__isnull=True):
        count = 0
        for post in Post.objects.filter(thread=thread).order_by("created_at", "id"):
            count += 1
            Post.objects.filter(pk=post.pk).update(number=count)
        Thread.objects.filter(pk=thread.pk).update(
            board=boards["chat"],
            post_count=count,
            bumped_at=thread.updated_at,
            closed=count >= 1000,
        )


class Migration(migrations.Migration):
    dependencies = [("board", "0002_schema")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
