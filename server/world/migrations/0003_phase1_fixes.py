"""フェーズ1の修正に合わせて、もうある世界を直す（表の形は変えない）。

- 人口の規模を 10 倍にした（施設の労働者・町の居住上限も 10 倍）ので、マスの住民の数を 10 倍にする。
- 地名の様式を持たない島は、これまでの地名（日本風）のまま様式を japanese とし、首都のある区画の
  地名を首都名にする（新しい建国と同じ形）。
"""

from django.db import migrations
from django.db.models import F

PLACE_BLOCK = 8  # この移行を書いたときの game.yaml の naming.place_block


def forwards(apps, schema_editor):
    Tile = apps.get_model("world", "Tile")
    Island = apps.get_model("world", "Island")
    Tile.objects.filter(population__gt=0).update(population=F("population") * 10)
    for island in Island.objects.all():
        ext = dict(island.ext or {})
        if "place_style" in ext:
            continue
        ext["place_style"] = "japanese"
        places = list(ext.get("places") or [])
        capital = (island.names or {}).get("capital")
        if places and capital and island.capital_x is not None:
            per_side = -(-island.size // PLACE_BLOCK)
            i = (island.capital_y // PLACE_BLOCK) * per_side + island.capital_x // PLACE_BLOCK
            places[i % len(places)] = capital
            ext["places"] = places
        island.ext = ext
        island.save(update_fields=["ext"])


def backwards(apps, schema_editor):
    Tile = apps.get_model("world", "Tile")
    Tile.objects.filter(population__gt=0).update(population=F("population") / 10)


class Migration(migrations.Migration):
    dependencies = [
        ("world", "0002_phase1"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
