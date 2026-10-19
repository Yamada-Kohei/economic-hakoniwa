"""自動車工場の作り方「アルミ」を「部品を組み立て」（default）に移す（回7 後半。W-76）。

自動車部品工場ができて、アルミを使うのは部品工場の作り方になった。自動車工場の作り方は1つだけに
なったので、前の作り方のままの施設は定義から消えた作り方を指し、ターンが止まる（定義のずれの確かめ）。
"""

from django.db import migrations

KIND = "automobile_plant"
OLD = "aluminium"
NEW = "default"
NEXT = "recipe_next"  # hakoniwa_core/facilities.py の RECIPE_NEXT


def forward(apps, schema_editor):
    Facility = apps.get_model("world", "Facility")
    for f in Facility.objects.filter(kind=KIND):
        changed = []
        if f.recipe == OLD:
            f.recipe = NEW
            changed.append("recipe")
        if f.ext.get(NEXT) == OLD:
            f.ext.pop(NEXT)
            changed.append("ext")
        if changed:
            f.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("world", "0005_statrecord_series_index"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
