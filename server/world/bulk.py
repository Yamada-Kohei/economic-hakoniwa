"""たくさんの行をまとめて書き換える。

Django の bulk_update は CASE WHEN の式を組み立てるため、5万マスを書き換えると Python 側だけで
数十秒かかる。ここでは同じ形の UPDATE 文を executemany でまとめて送る。
SQL は標準の UPDATE だけで、値の変換は Django のフィールドに任せるので、SQLite でも
PostgreSQL でもそのまま動く。
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db import connections, models, router


def update_rows(
    model: type[models.Model], objs: Sequence[models.Model], fields: Sequence[str]
) -> None:
    """objs の各行の fields を、主キーで探して書き換える。"""
    if not objs:
        return
    connection = connections[router.db_for_write(model)]
    meta = model._meta
    columns = [meta.get_field(name) for name in fields]
    quote = connection.ops.quote_name
    sets = ", ".join(f"{quote(c.column)} = %s" for c in columns)
    sql = f"UPDATE {quote(meta.db_table)} SET {sets} WHERE {quote(meta.pk.column)} = %s"
    params = [
        [c.get_db_prep_save(getattr(obj, c.attname), connection) for c in columns] + [obj.pk]
        for obj in objs
    ]
    with connection.cursor() as cursor:
        cursor.executemany(sql, params)
