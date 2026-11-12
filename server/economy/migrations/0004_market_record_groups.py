# 市場の記録に、買い手と売り手の別ごとの量を足す（W-72・W-258）

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("economy", "0003_market_record_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketrecord",
            name="buyers",
            field=models.JSONField(blank=True, default=dict, verbose_name="買い手の別ごとの量"),
        ),
        migrations.AddField(
            model_name="marketrecord",
            name="sellers",
            field=models.JSONField(blank=True, default=dict, verbose_name="売り手の別ごとの量"),
        ),
    ]
