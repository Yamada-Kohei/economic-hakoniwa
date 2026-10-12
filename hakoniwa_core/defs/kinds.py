"""定義ファイルの種類ごとの決まり（スキーマ・内部の形への変換・ファイルをまたぐ確認）。

新しい種類の定義ファイルを足すときは、ここに KindSpec を1つ足し、schemas/ にスキーマを置きます。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from hakoniwa_core.defs.freeze import thaw
from hakoniwa_core.units import to_milli

# 確認の結果は (種類, 名前, たどる場所, 内容) で返す。行番号は読み込み側で探す。
Problem = tuple[str, str, tuple[str | int, ...], str]
Tables = Mapping[str, Mapping[str, Mapping[str, Any]]]


@dataclass(frozen=True)
class KindSpec:
    name: str  # ファイル名（拡張子なし）と同じ
    schema: str  # schemas/ の中のファイル名
    normalize: Callable[[dict[str, Any]], dict[str, Any]] = lambda entry: entry
    check: Callable[[str, Mapping[str, Any], Tables], Iterator[Problem]] = (
        lambda _id, _entry, _tables: iter(())
    )


def _milli_map(amounts: Mapping[str, float]) -> dict[str, int]:
    return {good: to_milli(qty) for good, qty in amounts.items()}


def _normalize_facility(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    construction = dict(entry["construction"])
    construction["materials"] = _milli_map(construction.get("materials", {}))
    entry["construction"] = construction
    entry["recipes"] = {
        name: {
            **recipe,
            "inputs": _milli_map(recipe.get("inputs", {})),
            "outputs": _milli_map(recipe.get("outputs", {})),
        }
        for name, recipe in entry["recipes"].items()
    }
    return entry


def _check_facility(fid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    for i, terrain in enumerate(entry["terrain"]):
        if terrain not in tables["terrain"]:
            yield ("facilities", fid, ("terrain", i), f"地形 {terrain} は定義されていません")
    for good in entry["construction"].get("materials", {}):
        if good not in tables["goods"]:
            path = ("construction", "materials", good)
            yield ("facilities", fid, path, f"財 {good} は定義されていません")
    for rname, recipe in entry["recipes"].items():
        for side in ("inputs", "outputs"):
            for good in recipe.get(side, {}):
                if good not in tables["goods"]:
                    path = ("recipes", rname, side, good)
                    yield ("facilities", fid, path, f"財 {good} は定義されていません")


def _check_order(oid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    try:
        Draft202012Validator.check_schema(thaw(entry["payload"]))
    except SchemaError as e:
        yield ("orders", oid, ("payload",), f"payload のスキーマが正しくありません: {e.message}")


KINDS: dict[str, KindSpec] = {
    spec.name: spec
    for spec in [
        KindSpec("goods", "goods.schema.json"),
        KindSpec("terrain", "terrain.schema.json"),
        KindSpec(
            "facilities",
            "facilities.schema.json",
            normalize=_normalize_facility,
            check=_check_facility,
        ),
        KindSpec("orders", "orders.schema.json", check=_check_order),
    ]
}

# 表示名のファイルで使える種類。messages はターンログなどの文面。
LOCALE_KINDS = (*KINDS, "messages")
