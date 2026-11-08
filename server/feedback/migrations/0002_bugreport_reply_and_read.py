from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("feedback", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="bugreport",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "未読"),
                    ("read", "読んだ"),
                    ("working", "対応中"),
                    ("done", "済み"),
                ],
                default="new",
                max_length=16,
                verbose_name="状態",
            ),
        ),
        migrations.AddField(
            model_name="bugreport",
            name="reply",
            field=models.TextField(blank=True, default="", verbose_name="返事（送った人に見せる）"),
        ),
        migrations.AddField(
            model_name="bugreport",
            name="replied_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="返事を書いた時刻"),
        ),
    ]
