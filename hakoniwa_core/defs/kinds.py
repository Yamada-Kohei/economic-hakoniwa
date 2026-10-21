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
    needs_locale: bool = True  # 表示名（locale/<言語>/<種類>.yaml）が要るか


def _milli_map(amounts: Mapping[str, float]) -> dict[str, int]:
    return {good: to_milli(qty) for good, qty in amounts.items()}


def _normalize_facility(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    # 労働者: workers は合計の人数にそろえ、種類ごとの人数を staff に残す（数だけなら一般）
    workers = entry["workers"]
    staff = dict(workers) if isinstance(workers, Mapping) else {"general": workers}
    entry["workers"] = sum(staff.values())
    entry["staff"] = staff
    construction = dict(entry["construction"])
    construction["materials"] = _milli_map(construction.get("materials", {}))
    entry["construction"] = construction
    entry["recipes"] = {
        name: {
            **recipe,
            "inputs": _milli_map(recipe.get("inputs", {})),
            "outputs": _milli_map(recipe.get("outputs", {})),
            "factors": dict(recipe.get("factors", entry.get("factors", {}))),
            # 経験と集積（フェーズ3-3）で、作り方から施設の産業と分類を引けるようにする
            "industry": entry.get("industry"),
            "experience_share": entry.get("experience_share", 1),
            "category": entry.get("category"),
        }
        for name, recipe in entry["recipes"].items()
    }
    return entry


def _normalize_settlement(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    entry["materials"] = _milli_map(entry.get("materials", {}))
    return entry


def _normalize_infrastructure(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    entry["materials"] = _milli_map(entry.get("materials", {}))
    if "fallback" in entry:
        entry["fallback"] = _milli_map(entry["fallback"])
    return entry


def _normalize_army(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    entry["equipment"] = _milli_map(entry.get("equipment", {}))
    return entry


def _normalize_start(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    entry["warehouse"] = _milli_map(entry.get("warehouse", {}))
    return entry


def _goods(
    kind: str, eid: str, path: tuple[str | int, ...], goods: Any, tables: Tables
) -> Iterator[Problem]:
    for good in goods:
        if good not in tables["goods"]:
            yield (kind, eid, (*path, good), f"財 {good} は定義されていません")


def _check_facility(fid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    for kind in entry["staff"]:
        if kind not in tables["workers"]:
            message = f"労働者の種類 {kind} は定義されていません"
            yield ("facilities", fid, ("workers", kind), message)
    for i, terrain in enumerate(entry["terrain"]):
        if terrain not in tables["terrain"]:
            yield ("facilities", fid, ("terrain", i), f"地形 {terrain} は定義されていません")
    materials = entry["construction"].get("materials", {})
    yield from _goods("facilities", fid, ("construction", "materials"), materials, tables)
    for rname, recipe in entry["recipes"].items():
        for side in ("inputs", "outputs"):
            yield from _goods("facilities", fid, ("recipes", rname, side), recipe[side], tables)
        resource = recipe.get("requires_resource")
        if resource is not None and not tables["goods"].get(resource, {}).get("mining"):
            path = ("recipes", rname, "requires_resource")
            message = f"{resource} は鉱業資源（mining: true の財）ではありません"
            yield ("facilities", fid, path, message)
    yield from _check_facility_zone(fid, entry)


def _check_settlement(sid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    yield from _goods("settlements", sid, ("materials",), entry.get("materials", {}), tables)


def _check_infrastructure(iid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    for key in ("materials", "fallback"):
        yield from _goods("infrastructure", iid, (key,), entry.get(key, {}), tables)
    if "path" in entry and entry["role"] != "transport":
        yield ("infrastructure", iid, ("path",), "path は role: transport のときだけ書けます")
    if "grid" in entry and entry["role"] != "power":
        yield ("infrastructure", iid, ("grid",), "grid は role: power のときだけ書けます")


def _check_order(oid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    try:
        Draft202012Validator.check_schema(thaw(entry["payload"]))
    except SchemaError as e:
        yield ("orders", oid, ("payload",), f"payload のスキーマが正しくありません: {e.message}")
        return
    secret = entry.get("secret", True)
    if secret is True:
        return
    # 中身で伏せる命令: 項目は payload にあり、値はその項目の形に合う
    properties = entry["payload"].get("properties", {})
    for key, value in secret.items():
        if key not in properties:
            yield ("orders", oid, ("secret", key), f"payload に {key} がありません")
        elif not Draft202012Validator(thaw(properties[key])).is_valid(thaw(value)):
            yield ("orders", oid, ("secret", key), f"{value!r} は {key} の値になりません")


def _check_facility_zone(fid: str, entry: Mapping[str, Any]) -> Iterator[Problem]:
    # 町の中に置く店（site: town。回7）は、町のマスを決めて建てられる
    town = entry.get("site") == "town"
    if entry.get("tileless") and entry.get("buildable") is not False and not town:
        yield (
            "facilities",
            fid,
            ("tileless",),
            "マスを持たない施設は buildable: false にしてください",
        )


def _check_regime(rid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    for i, fid in enumerate(entry.get("state_owned", ())):
        if fid not in tables["facilities"]:
            yield ("regimes", rid, ("state_owned", i), f"施設 {fid} は定義されていません")
    start = entry.get("start")
    if start is not None and start not in tables["start_island"]:
        yield ("regimes", rid, ("start",), f"start_island.yaml に {start} がありません")


def _check_start(sid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    facilities = tables["facilities"]
    capital = entry["capital"]
    if capital["facility"] not in facilities:
        yield ("start_island", sid, ("capital", "facility"), "施設が定義されていません")
    level = capital.get("town_level")
    if level is not None and level not in tables["settlements"]:
        yield ("start_island", sid, ("capital", "town_level"), f"段階 {level} がありません")
    for i, item in enumerate(entry["facilities"]):
        kind = item["kind"]
        if kind not in facilities:
            yield ("start_island", sid, ("facilities", i), f"施設 {kind} は定義されていません")
            continue
        recipe = item.get("recipe")
        if recipe is not None and recipe not in facilities[kind]["recipes"]:
            yield ("start_island", sid, ("facilities", i), f"{kind} に作り方 {recipe} はありません")
        resource = item.get("if_resource")
        if resource is not None and not tables["goods"].get(resource, {}).get("mining"):
            yield ("start_island", sid, ("facilities", i), f"{resource} は鉱業資源ではありません")
    yield from _goods("start_island", sid, ("warehouse",), entry.get("warehouse", {}), tables)


def _check_naming(nid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    for fid in entry.get("industries", {}):
        if fid not in tables["facilities"]:
            yield ("naming", nid, ("industries", fid), f"施設 {fid} は定義されていません")
    keys = {r["axis"] for r in tables["regimes"].values()} | {"owner", "category"}
    for i, pattern in enumerate(entry["patterns"]):
        for key in pattern.get("when", {}):
            if key not in keys:
                path = ("patterns", i, "when", key)
                message = f"条件 {key} は使えません（使えるもの: {sorted(keys)}）"
                yield ("naming", nid, path, message)


def _check_army(uid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    yield from _goods("army", uid, ("equipment",), entry.get("equipment", {}), tables)


def _check_climate(cid: str, entry: Mapping[str, Any], tables: Tables) -> Iterator[Problem]:
    yield from _goods("climate", cid, ("outputs",), entry.get("outputs", {}), tables)


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
        KindSpec(
            "settlements",
            "settlements.schema.json",
            normalize=_normalize_settlement,
            check=_check_settlement,
        ),
        KindSpec("workers", "workers.schema.json"),
        KindSpec(
            "infrastructure",
            "infrastructure.schema.json",
            normalize=_normalize_infrastructure,
            check=_check_infrastructure,
        ),
        KindSpec("regimes", "regimes.schema.json", check=_check_regime),
        KindSpec(
            "start_island",
            "start_island.schema.json",
            normalize=_normalize_start,
            check=_check_start,
        ),
        KindSpec("naming", "naming.schema.json", check=_check_naming, needs_locale=False),
        KindSpec("finance", "finance.schema.json"),
        KindSpec("climate", "climate.schema.json", check=_check_climate),
        KindSpec("army", "army.schema.json", normalize=_normalize_army, check=_check_army),
    ]
}

# 表示名のファイルで使える種類。messages はターンログなどの文面、recipes は作り方の表示名、
# units は量の単位（goods.yaml の unit）の表示名、industries は産業の分類（facilities.yaml の
# industry）。
LOCALE_KINDS = (*KINDS, "messages", "recipes", "units", "industries")
