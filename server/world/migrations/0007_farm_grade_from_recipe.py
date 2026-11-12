"""段階の作り方で建った農場の段階を、作り方にそろえる（W-257、公開の世界の不具合の報告 #7）。

段階の作り方（機械化・近代化）を選んで建てた農場は、作り方は機械化なのに段階（ext の grade）は
手作業のままだった。機械の傷みと補修・設備の額は段階を見るので、機械のない機械化の農場になっていた。
この直しの前に建った農場は、段階を作り方にそろえ、機械を新品で持っていることにする（工事のときに
機械の代金は払っていないが、建てた人は機械化の農場のつもりで建てているため。2026-11-12）。
工事中の農場も段階だけそろえ、資材と工期は手作業のまま建つ（直しの前に始めた工事の約束を変えない。
新しく建てる農場だけが、段階までの工事の工期と資材を足して建つ。hakoniwa_core の grade_build）。

段階を上げる工事が残っている農場はそろえない（その工事が済むと段階が上がる。先にそろえると、
上げる工事の機械を二重に払うか、近代化の作り方が機械化に下がる）。作り方を替える予約（recipe_next）
が手作業の作り方なら、そろえた段階の同じ作物の作り方にする（市場の段で黙って弾かれないように）。
"""

from django.db import migrations

KIND = "farm"
GRADES = {"mechanized": 1, "modern": 2}  # packs/base/data/facilities.yaml の farm の grades の順
NAMES = {g: name for name, g in GRADES.items()}
GRADE = "grade"  # hakoniwa_core/facilities.py の GRADE
GRADE_TO = "grade_to"  # 段階を上げる工事（マスを持たない工事）
EXPANDS = "expands"  # 建て増し・段階を上げる工事の相手の施設の番号
NEXT = "recipe_next"  # hakoniwa_core/facilities.py の RECIPE_NEXT
# 近代化は肥料のある作り方だけ（hakoniwa_core/defs/kinds.py の requires_input）
FERTILIZED = "fertilized"


def recipe_grade(recipe: str) -> int:
    return next((g for name, g in GRADES.items() if recipe.endswith(f"_{name}")), 0)


def regrade(recipe: str, grade: int) -> str:
    """作り方の名前を、段階 grade の同じ作物の作り方の名前にする（hakoniwa_core の regrade と同じ
    名付け: 元の作り方_段階の名前。近代化は肥料のある元の作り方から）。"""
    base = recipe
    for name in GRADES:
        base = base.removesuffix(f"_{name}")
    if grade == 0:
        return base
    if grade == GRADES["modern"] and FERTILIZED not in base:
        base = FERTILIZED if base == "default" else f"{base}_{FERTILIZED}"
    return f"{base}_{NAMES[grade]}"


def forward(apps, schema_editor):
    Facility = apps.get_model("world", "Facility")
    upgrading = {
        (f.island_id, int(f.ext[EXPANDS]))
        for f in Facility.objects.filter(kind=KIND)
        if GRADE_TO in (f.ext or {}) and f.ext.get(EXPANDS) is not None
    }
    for f in Facility.objects.filter(kind=KIND):
        ext = f.ext or {}
        grade = recipe_grade(f.recipe)
        if not grade or GRADE in ext or GRADE_TO in ext or (f.island_id, f.number) in upgrading:
            continue
        ext[GRADE] = grade
        if ext.get(NEXT) and recipe_grade(ext[NEXT]) != grade:
            ext[NEXT] = regrade(ext[NEXT], grade)
        f.ext = ext
        f.save(update_fields=["ext"])


class Migration(migrations.Migration):
    dependencies = [
        ("world", "0006_automobile_recipe"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
