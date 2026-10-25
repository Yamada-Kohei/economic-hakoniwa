"""施設についての共通の計算（定義の引き方・置ける場所・人手や資材の割合）。

段や命令はここを通して施設の定義を読みます。割合はすべて 1000 分の 1 の整数（1000 が 100%）です。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from hakoniwa_core import nature, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import distance, is_coastal, within
from hakoniwa_core.state import (
    OWNER_STATE,
    ZONE_COMMERCIAL,
    ZONE_OFFICE,
    Facility,
    IslandState,
    Tile,
)

FULL = 1000  # 割合の 100%
ENGINEER = "engineer"  # 技術者（島全体の人数として持つ働き手。フェーズ2-3）
STATUS_STARTING = "starting"  # 工事が済んで、まだ動いたことがない（施設の status）
# 作り方の変更（フェーズ3-2）。命令と民営の見直しで ext["recipe_next"] に置き、次の市場の段で
# 替える（新しい原料はその市場で買う）。替えた次のターン（ext["recipe_changed"]）は稼働率が半分
RECIPE_NEXT = "recipe_next"
RECIPE_CHANGED = "recipe_changed"
# 畑の作付け（回3。Codex E17）: 育つ期間（作り方の grow_turns）のある作り方は、畑を作り方ごとの割合
# で持つ（economy["fields"] {作り方: 1000 分の 1}）。作物を替えると、1ターンに今の作物の収穫の分
# （1000 ÷ 育つ期間）ずつ新しい作物を蒔き、蒔いた畑は育つ期間のあいだ何も出さない
# （economy["sown"] [[作り方, 割合, 収穫の始まるターン]]）。同じ作物（肥料の有無）はすぐ替わる
FIELDS = "fields"
SOWN = "sown"
CROP_READY = "crop_ready"  # 畑をみな新しい作物にし終えたターン（ext。民営の見直しが売れ行きを待つ）
FINISHED = "finished"  # 工事が終わったターン（ext。回6）
# 原料が手に入らずに動けない理由（施設の status の「:」の前）。input は品不足、input_price は
# 原料が高くて採算が合わない、funds は原料を買うお金が足りない、public_price は学校と大学の教材が
# 高くて買えない
INPUT_SHORT = ("input", "input_price", "funds", "public_price")
# 建て増し（段6 A3）: 施設の規模（ext。書いていなければ 1）。規模 n の施設は、働き手・原料・産出・
# 設備・汚染が規模 1 の n 倍。建て増しの工事は、町の中の店と同じくマスを持たない負の位置に置き、
# 働く場所（SITE）と建て増す施設の番号（EXPANDS）を ext に持つ。済むと建て増す施設に合わせる
SIZE = "size"
EXPANDS = "expands"


def short_of_inputs(status: str) -> bool:
    """施設の status が、原料が手に入らずに動けないことを表すか。"""
    return status.partition(":")[0] in INPUT_SHORT


def ratio(have: int, need: int) -> int:
    """have / need を 1000 分の 1 で（上限 1000）。need が 0 なら 1000。"""
    if need <= 0:
        return FULL
    return min(FULL, have * FULL // need)


def scale(amount: int, rate: int) -> int:
    """amount の rate / 1000（切り捨て）。"""
    return amount * rate // FULL


def productivity_of(f: Facility) -> int:
    """施設の生産性（1000 分の 1）。決め方は economy/productivity.py。"""
    return f.economy.get("productivity", FULL)


def size_of(f: Facility) -> int:
    """施設の規模（建て増した数 + 1）。"""
    return int(f.ext.get(SIZE, 1))


# 1 マスに入る規模の上限の既定（facilities.yaml の max_size を書いていない施設）。15km 四方ほどの
# マス（国の大きさ 20 倍）に大きな工場が 10 ほど並ぶ見立ての仮の値で、現実の数から出していない。
# 測りで土地が足りるかを見て直す
DEFAULT_MAX_SIZE = 10


def max_size(defs: Defs, kind: str) -> int:
    """1 マスに入る規模の上限。マスを持たない施設・町・記念碑は建て増さない（1）。"""
    fdef = defs.facilities[kind]
    if fdef.get("tileless") or fdef.get("buildable") is False or fdef.get("monument"):
        return 1
    return int(fdef.get("max_size", DEFAULT_MAX_SIZE))


def pending_expansions(island: IslandState, target: Facility) -> int:
    """施設 target を建て増している工事の数。"""
    return sum(1 for f in island.facilities if f.ext.get(EXPANDS) == target.id)


def expansion_target(island: IslandState, kind: str, x: int, y: int) -> Facility | None:
    """(x, y) に同じ施設 kind が建っていれば、その施設（建て増す相手）。"""
    f = island.facility_at(x, y)
    return f if f is not None and f.kind == kind else None


def expansion_problem(
    defs: Defs, island: IslandState, target: Facility
) -> tuple[str, dict[str, Any]] | None:
    """施設 target を建て増せないときは (理由, 引数)。"""
    kind = target.kind
    limit = max_size(defs, kind)
    if limit <= 1:
        return ("reason_cannot_expand", {"facility": kind})
    if not target.built or target.ext.get("repair"):
        return ("reason_expand_unbuilt", {"facility": kind})
    if target.owner != OWNER_STATE:
        return ("reason_private_expand", {"facility": kind})
    if size_of(target) + pending_expansions(island, target) >= limit:
        return ("reason_expand_full", {"facility": kind, "max": limit})
    return None


def expansion_of(f: Facility) -> int | None:
    """建て増しの工事なら、建て増す施設の番号。"""
    found = f.ext.get(EXPANDS)
    return int(found) if found is not None else None


def outputs_of(defs: Defs, f: Facility) -> dict[str, int]:
    """全力で動いたときの1ターンの産出（作り方の産出 × 生産性）。"""
    p = productivity_of(f)
    return {g: q * p // FULL for g, q in recipe_of(defs, f)["outputs"].items()}


def facility_def(defs: Defs, f: Facility) -> Mapping[str, Any]:
    return defs.facilities[f.kind]


def recipe_of(defs: Defs, f: Facility) -> Mapping[str, Any]:
    """施設の今の作り方。畑を作り替えている途中の施設（economy["fields"]）は、収穫している畑の
    作り方を割合で混ぜた作り方（蒔いて育っている畑は何も出さず、何も使わない）。"""
    fields = f.economy.get(FIELDS)
    if fields is None:
        recipe = defs.facilities[f.kind]["recipes"][f.recipe]
    else:
        recipe = _blend(defs, f.kind, f.recipe, tuple(sorted(fields.items())))
    size = size_of(f)
    return recipe if size == 1 else _sized(recipe, size)


_SIZED: dict[tuple[int, int], tuple[Mapping[str, Any], Mapping[str, Any]]] = {}


def _sized(recipe: Mapping[str, Any], size: int) -> Mapping[str, Any]:
    """規模 size の施設の作り方（原料と産出を size 倍）。"""
    key = (id(recipe), size)
    found = _SIZED.get(key)
    if found is not None and found[0] is recipe:
        return found[1]
    if len(_SIZED) > 4096:
        _SIZED.clear()
    sized = MappingProxyType(
        {
            **recipe,
            "inputs": MappingProxyType({g: q * size for g, q in recipe["inputs"].items()}),
            "outputs": MappingProxyType({g: q * size for g, q in recipe["outputs"].items()}),
        }
    )
    _SIZED[key] = (recipe, sized)
    return sized


_BLENDS: dict[tuple[int, str, str, tuple[tuple[str, int], ...]], Mapping[str, Any]] = {}


def _blend(
    defs: Defs, kind: str, target: str, fields: tuple[tuple[str, int], ...]
) -> Mapping[str, Any]:
    key = (id(defs), kind, target, fields)
    found = _BLENDS.get(key)
    if found is not None:
        return found
    if len(_BLENDS) > 4096:
        _BLENDS.clear()
    recipes = defs.facilities[kind]["recipes"]
    inputs: dict[str, int] = {}
    outputs: dict[str, int] = {}
    for name, share in fields:
        recipe = recipes[name]
        for table, amounts in ((inputs, recipe["inputs"]), (outputs, recipe["outputs"])):
            for good, qty in amounts.items():
                table[good] = table.get(good, 0) + qty * share // FULL
    blended = MappingProxyType(
        {
            **recipes[target],
            "inputs": MappingProxyType({g: q for g, q in sorted(inputs.items()) if q > 0}),
            "outputs": MappingProxyType({g: q for g, q in sorted(outputs.items()) if q > 0}),
        }
    )
    _BLENDS[key] = blended
    return blended


def steady(f: Facility) -> Facility:
    """畑を作り替えている途中の施設を、畑がみな今の作り方（f.recipe）で収穫できるとみた写し。
    採算の見込み（変動費の利益・売り値の下限・原料に払える値段）に使う。農家は、蒔き直した作物が
    育つまでの間も、その作物の採算で働き手を雇い続け、撤退するかを決める（育つ間の赤字で閉じない）。"""
    if FIELDS not in f.economy:
        return f
    economy = {k: v for k, v in f.economy.items() if k not in (FIELDS, SOWN)}
    return replace(f, economy=economy)


def grow_turns(defs: Defs, kind: str, recipe: str) -> int:
    """作り方 recipe の作物が蒔いてから収穫まで育つターン数（育つ期間のない作り方は 0）。"""
    return int(defs.facilities[kind]["recipes"][recipe].get("grow_turns", 0))


def crop_of(defs: Defs, kind: str, recipe: str) -> tuple[str, ...]:
    """作り方の作物（作る物の組。肥料の有無だけが違う作り方は同じ作物）。"""
    return tuple(sorted(defs.facilities[kind]["recipes"][recipe]["outputs"]))


def has_fields(defs: Defs, f: Facility) -> bool:
    """畑を割合で持って少しずつ作り替える施設か（今の作り方に育つ期間がある）。"""
    return grow_turns(defs, f.kind, f.recipe) > 0


def step_fields(defs: Defs, f: Facility, turn: int) -> None:
    """畑の作付けを1ターン進める（生産の段の初め）。育ちきった畑を収穫の畑に入れ、今の作り方
    （f.recipe）と違う作物の畑を、1ターンに収穫の分（1000 ÷ その作物の育つ期間）ずつ今の作り方で
    蒔き直す。同じ作物で作り方だけ違う畑（肥料の有無）はすぐ今の作り方にする。みな今の作り方で
    収穫できるようになったら、畑の割合を消して元の1つの作り方に戻す。"""
    fields: dict[str, int] | None = f.economy.get(FIELDS)
    if fields is None:
        return
    sown: list[list[Any]] = f.economy.setdefault(SOWN, [])
    crop = crop_of(defs, f.kind, f.recipe)
    for row in sown:
        if crop_of(defs, f.kind, row[0]) == crop:
            row[0] = f.recipe
    ready = [row for row in sown if row[2] <= turn]
    sown[:] = [row for row in sown if row[2] > turn]
    for name, share, _ in ready:
        fields[name] = fields.get(name, 0) + share
    for name in sorted(fields):
        if name != f.recipe and crop_of(defs, f.kind, name) == crop:
            fields[f.recipe] = fields.get(f.recipe, 0) + fields.pop(name)
    grow = max(1, grow_turns(defs, f.kind, f.recipe))
    for name in sorted(fields):
        if name == f.recipe:
            continue
        cycle = max(1, grow_turns(defs, f.kind, name))
        amount = min(fields[name], -(-FULL // cycle))
        fields[name] -= amount  # 0 になっても、作り替えが済むまで前の作物として残す（field_goods）
        if amount > 0:
            sown.append([f.recipe, amount, turn + grow])
    if not sown and not any(v for name, v in fields.items() if name != f.recipe):
        f.economy.pop(FIELDS, None)
        f.economy.pop(SOWN, None)
        f.ext[CROP_READY] = turn
        _forget_other_outputs(defs, f)
    elif not sown:
        f.economy.pop(SOWN, None)


def fit_fields(defs: Defs, f: Facility, power: str) -> None:
    """作物を替えている途中の施設で、前の作物の畑に要る原料（肥料の化学品）が手元に足りなければ、
    その畑を同じ作物の、原料の手元にある作り方（肥料なし）に替える（生産の段の初め。回7）。
    肥料の届かない畑は収量が下がるだけで、新しい作物の畑まで止まらない（台帳 2026-10-18 06:33）。"""
    fields: dict[str, int] | None = f.economy.get(FIELDS)
    if not fields:
        return
    recipes = defs.facilities[f.kind]["recipes"]

    def short() -> set[str]:
        need = recipe_of(defs, f)["inputs"]
        return {g for g, q in need.items() if g != power and stock.total(f.stock, g) < q}

    for name in sorted(fields):
        lacking = short()
        if not lacking:
            return
        uses = set(recipes[name]["inputs"]) & lacking
        if name == f.recipe or fields[name] <= 0 or not uses:
            continue
        crop = crop_of(defs, f.kind, name)
        fallback = [
            other
            for other, r in sorted(recipes.items())
            if other != name
            and crop_of(defs, f.kind, other) == crop
            and not set(r["inputs"]) & lacking
        ]
        if not fallback:
            continue
        best = max(fallback, key=lambda other: sum(recipes[other]["outputs"].values()))
        fields[best] = fields.get(best, 0) + fields.pop(name)


def field_goods(defs: Defs, f: Facility) -> set[str]:
    """施設が作っている物と、畑を作り替えている途中なら前の作物と蒔いた作物の物（すぐ作り戻せる
    物。民営の見直しで、今作っていない物として扱わない）。"""
    recipes = defs.facilities[f.kind]["recipes"]
    names = {f.recipe, *f.economy.get(FIELDS, {}), *(row[0] for row in f.economy.get(SOWN, []))}
    if FIELDS not in f.economy:
        names = {f.recipe}
    return {g for name in names for g in recipes[name]["outputs"]}


def food_maker(defs: Defs, kind: str) -> bool:
    """作り方のどれかで食料（goods.yaml の food のある財）を作れる施設の種類か（農場・牧場・漁港・
    食品工場）。食べ物が買えずに畑に戻った村の人も、こういう仕事には勤める（収穫から食べられ、
    作物を食べ物に戻せる。modules/employment。回4）。"""
    recipes = defs.facilities[kind].get("recipes", {})
    return any("food" in defs.goods[g] for r in recipes.values() for g in r.get("outputs", {}))


def _forget_other_outputs(defs: Defs, f: Facility) -> None:
    """今の作り方で作らない物の売り値と売れ行きの見込みを捨てる（手元に残った物は市場の段で売る）。"""
    outputs = defs.facilities[f.kind]["recipes"][f.recipe]["outputs"]
    for key in ("ask", "expected"):
        table = f.economy.get(key)
        if table:
            for good in [g for g in table if g not in outputs]:
                del table[good]


def priority_of(defs: Defs, config: GameConfig, f: Facility) -> int:
    if f.priority is not None:
        return f.priority
    return defs.facilities[f.kind].get("priority", config.get("labor.default_priority"))


def commute_radius(defs: Defs, config: GameConfig, kind: str) -> int:
    return defs.facilities[kind].get("commute_radius", config.get("labor.commute_radius"))


def commute_labor(
    defs: Defs, config: GameConfig, island: IslandState, kind: str, x: int, y: int
) -> tuple[int, int]:
    """施設 kind を (x, y) に置いたときの通勤圏の (労働者, そのうち前のターンの就業者) の数。"""
    tiles = [island.tile(*p) for p in within(island, (x, y), commute_radius(defs, config, kind))]
    tiles = [t for t in tiles if t.population > 0]
    share = labor_share(defs, island)
    labor = sum(labor_force(defs, island, t, share) for t in tiles)
    employed = sum(
        min(t.ext.get("employed", 0), labor_force(defs, island, t, share)) for t in tiles
    )
    return labor, employed


def housing_kind(config: GameConfig) -> str:
    return config.get("housing.facility")


def is_housing(config: GameConfig, f: Facility) -> bool:
    return f.kind == housing_kind(config)


def on_map(f: Facility) -> bool:
    """マスを持つ施設か（運送業・海運業のようなマスを持たない施設は x, y が負）。"""
    return f.x >= 0 and f.y >= 0


SITE = "site"  # 施設の ext: 町の中の店（回7）の属する町のマス [x, y]


def position(f: Facility) -> tuple[int, int]:
    """施設の働く場所（町の中の店は町のマス、ほかは施設のマス）。"""
    site = f.ext.get(SITE)
    if site:
        return int(site[0]), int(site[1])
    return f.x, f.y


def located(f: Facility) -> bool:
    """マスを持つか、町の中に置いた施設か（運送業のような島全体の施設でないか）。"""
    return on_map(f) or bool(f.ext.get(SITE))


def shops_at(island: IslandState, pos: tuple[int, int]) -> int:
    """町のマス pos に入っている店の数。"""
    key = [pos[0], pos[1]]
    return sum(1 for f in island.facilities if f.ext.get(SITE) == key and EXPANDS not in f.ext)


def shop_slots(defs: Defs, town: Facility) -> int:
    """町のマスに入る店の数（settlements.yaml の shops）。"""
    level = town.ext.get("level") or defs.settlement_levels()[0]
    return int(defs.settlements.get(level, {}).get("shops", 0))


def vacate(island: IslandState, f: Facility) -> None:
    """閉鎖・倒産・破綻処理で施設を島から取り除く（プレイヤーの取り壊しの命令ではない）。

    施設のマスは送電網の一部なので、マスに送電網（100%）を残して、まわりの施設の送電網が切れない
    ようにする。
    """
    from hakoniwa_core import infra

    island.facilities.remove(f)
    if on_map(f):
        infra.set_level(island.tile(f.x, f.y), infra.POWER, infra.FULL)


def is_tileless(defs: Defs, kind: str) -> bool:
    return bool(defs.facilities[kind].get("tileless"))


def recipe_labor(defs: Defs, kind: str, recipe: str | None) -> float:
    """作り方 recipe の一般の労働者の割合（書かなければ 1）。電子機器を使う作り方は少ない人手で
    同じだけ作る（MI-43。工場の自動化・役所・銀行）。"""
    return defs.facilities[kind]["recipes"].get(recipe or "", {}).get("labor", 1.0)


def workers_needed(defs: Defs, kind: str, recipe: str | None = None) -> int:
    """全力で動くのに要る労働者の数（技術者を含む）。作り方 recipe を渡すと、その作り方の人手
    （技術者の数は変えず、一般の労働者を recipe_labor の割合に）。"""
    return scaled_workers(defs, kind, recipe_labor(defs, kind, recipe))


def scaled_workers(defs: Defs, kind: str, labor: float) -> int:
    """一般の労働者を labor の割合にしたときの、全力で動くのに要る労働者の数。"""
    fdef = defs.facilities[kind]
    if labor >= 1:
        return fdef["workers"]
    return fdef["workers"] - int(fdef["staff"].get("general", 0) * (1 - labor))


def workers_of(defs: Defs, f: Facility) -> int:
    """施設 f の今の作り方で全力で動くのに要る労働者の数（規模の分を含む）。"""
    return workers_needed(defs, f.kind, f.recipe) * size_of(f)


def engineers_needed(defs: Defs, kind: str) -> int:
    """全力で動くのに要る労働者のうちの技術者の数（規模 1）。"""
    return defs.facilities[kind].get("staff", {}).get(ENGINEER, 0)


def engineers_of(defs: Defs, f: Facility) -> int:
    """施設 f が全力で動くのに要る技術者の数（規模の分を含む）。"""
    return engineers_needed(defs, f.kind) * size_of(f)


def engineer_wage_of(config: GameConfig, f: Facility | None, wage: Any) -> Any:
    """施設の技術者の賃金。決まっていなければ、一般の賃金 wage の labor.engineer_wage_ratio 倍。"""
    if f is not None and "engineer_wage" in f.economy:
        return f.economy["engineer_wage"]
    value = wage * config.get("labor.engineer_wage_ratio")
    return round(value) if isinstance(wage, int) else value


def labor_cost(
    defs: Defs,
    config: GameConfig,
    kind: str,
    wage: Any,
    f: Facility | None = None,
    labor: float | None = None,
) -> Any:
    """全力で動いたときの1ターンの賃金: 一般の賃金 wage × 一般の人数 ＋ 技術者の賃金 × 技術者の人数
    （施設 f があれば、その技術者の賃金と作り方で。labor を渡すと、その一般の労働者の割合で）。"""
    engineers = engineers_needed(defs, kind)
    if labor is None:
        labor = recipe_labor(defs, kind, f.recipe) if f is not None else 1.0
    general = scaled_workers(defs, kind, labor) - engineers
    size = size_of(f) if f is not None else 1
    if not engineers:
        return general * wage * size
    return (general * wage + engineers * engineer_wage_of(config, f, wage)) * size


def engineer_cap(config: GameConfig, f: Facility) -> int:
    """技術者の足りなさで決まる稼働率の上限（1000 分の 1）: floor + (1 − floor) × 技術者の充足率。

    floor は labor.engineer_floor。雇いたい技術者の席（雇用の段の economy["engineers_wanted"]）が
    なければ上限はない。
    """
    seats = f.economy.get("engineers_wanted", 0)
    if seats <= 0:
        return FULL
    floor = round(config.get("labor.engineer_floor") * FULL)
    return floor + (FULL - floor) * ratio(f.economy.get("engineers", 0), seats) // FULL


def equipment_cap(f: Facility) -> int:
    """設備の傷みで決まる稼働率の上限（1000 分の 1。回6。決め方は economy/equipment.py）。"""
    return f.economy.get("condition", FULL)


def is_halted(f: Facility, turn: int) -> bool:
    """災害などで止まっているか。"""
    return f.ext.get("halted_until", 0) >= turn


def labor_share(defs: Defs, island: IslandState) -> float:
    """住民のうち一般の労働者になる割合。

    島全体の人数として持つ家計の層（働かない資産を持つ層と、島のどこにでも通う技術者の層）の
    分だけ減る。労働の動員（全体主義。フェーズ3-4）をしている間は、その分だけ増える。
    """
    share = defs.workers["general"].get("share", 1.0)
    idle = sum(h.get("share", 0.0) for h in island.economy.households.values())
    mobilized = island.ext.get("politics", {}).get("mobilized", 0.0)
    return min(1.0, (share + mobilized) * (1 - idle))


def labor_force(defs: Defs, island: IslandState, tile: Tile, share: float | None = None) -> int:
    """マスの一般の労働者の数。share は labor_share（多くのマスを数えるときに1回だけ出す）。"""
    if share is None:
        share = labor_share(defs, island)
    return int(tile.population * share)


def labor_ratio(defs: Defs, f: Facility) -> int:
    return ratio(f.workers, workers_of(defs, f))


def total_progress(defs: Defs, f: Facility) -> int:
    """工事が済むまでの進みの合計（1ターンに最大 1000 進む）。定義の工期 × 1000 で、体制や重点産業で
    工期を変えた国の工事は、建て始めたときに決めた ext["build_total"]（フェーズ3-4）。"""
    found = f.ext.get("build_total")
    if found:
        return int(found)
    return defs.facilities[f.kind]["construction"]["turns"] * FULL


def materials_of(defs: Defs, f: Facility) -> dict[str, int]:
    """工事 f の資材の合計。隠して建てる施設（ext["materials_factor"]。MI-62）は定義より多い。"""
    materials = defs.facilities[f.kind]["construction"].get("materials", {})
    factor = f.ext.get("materials_factor")
    if not factor:
        return dict(materials)
    return {good: round(qty * factor) for good, qty in materials.items()}


def step_materials(defs: Defs, f: Facility, progress: int | None = None) -> dict[str, int]:
    """工事を今（progress を渡せばその進みから）1ターン分（1000）進めるのに要る資材。
    全部の工事で合計がちょうど定義の量になる。"""
    whole = total_progress(defs, f)
    if progress is None:
        progress = f.progress
    start = min(progress, whole)
    end = min(progress + FULL, whole)
    return {
        good: qty * end // whole - qty * start // whole
        for good, qty in sorted(materials_of(defs, f).items())
    }


def material_ratio(defs: Defs, f: Facility) -> int:
    """工事中の施設が、手元の資材で今のターンにどれだけ進めるか（1000 分の 1）。"""
    need = step_materials(defs, f)
    return min((ratio(stock.total(f.stock, g), q) for g, q in need.items() if q > 0), default=FULL)


def input_ratio(defs: Defs, f: Facility, power: str) -> tuple[int, str | None]:
    """手元の原料で今のターンにどれだけ動けるか（1000 分の 1）と、一番足りない原料。

    電力（power）は在庫を持たず、生産の段で送電網から買うので数えない。
    """
    found, short = FULL, None
    for good, qty in sorted(recipe_of(defs, f)["inputs"].items()):
        if good == power:
            continue
        r = ratio(stock.total(f.stock, good), qty)
        if r < found:
            found, short = r, good
    return found, short


def labor_demand(defs: Defs, f: Facility, turn: int, power: str | None = None) -> int:
    """このターンに雇いたい人数。工事中は手元の資材で進められる分の人手、動いている施設は
    操業目標（economy["target"]、1000 分の 1）の分だけ（設備の割合まで。回6）。

    お金の経済の施設は、power（電力の財）を渡すと、手元の原料で動ける分までにする（原料が届く
    まで、動けない人を雇って賃金だけ払わない。工事中の資材と同じ）。
    """
    fdef = defs.facilities[f.kind]
    if not f.built:
        labor = fdef["construction"].get("labor", 0)
        return -(-labor * material_ratio(defs, f) // FULL)  # 切り上げ
    if is_halted(f, turn):
        return 0
    # 設備が傷んで作れない分の人は雇わない（回6）
    target = min(f.economy.get("target", FULL), equipment_cap(f))
    if power is not None and "target" in f.economy:
        target = min(target, input_ratio(defs, f, power)[0])
    return scale(workers_of(defs, f), target)


def placement_problem(
    defs: Defs, island: IslandState, kind: str, x: int, y: int, recipe: str | None = None
) -> tuple[str, dict[str, Any]] | None:
    """施設 kind を (x, y) に建てられないときは (理由, 引数) を返す。建てられれば None。"""
    if kind not in defs.facilities:
        return ("reason_unknown_facility", {})
    fdef = defs.facilities[kind]
    if not island.in_bounds(x, y):
        return ("reason_out_of_bounds", {})
    tile = island.tile(x, y)
    zone = fdef.get("zone")
    wrong_zone = None
    if zone is not None and tile.zone != zone:
        wrong_zone = ("reason_zone_required", {"facility": kind, "zone": f"zone_{zone}"})
    if fdef.get("site") == "town":  # 町の中の店（回7）: 町のマスの、空いた枠に入る
        town = island.facility_at(x, y)
        if town is None or defs.facilities[town.kind].get("category") != "housing":
            return ("reason_shop_needs_town", {"facility": kind})
        if shops_at(island, (x, y)) >= shop_slots(defs, town):
            return ("reason_shop_slots", {"facility": kind, "max": shop_slots(defs, town)})
        if wrong_zone is not None:
            return wrong_zone
    else:
        if tile.terrain not in fdef["terrain"]:
            return ("reason_wrong_terrain", {"facility": kind, "terrain": tile.terrain})
        # 用途地域の決まりは「施設がある」より先に見る（百貨店を町のマスに置こうとしたとき、
        # 「商業地にだけ建てられる」と分かるように。試し遊び1回目）
        if wrong_zone is not None:
            return wrong_zone
        occupant = island.facility_at(x, y)
        if occupant is not None:
            if occupant.kind != kind:
                return ("reason_tile_occupied", {})
            # 同じ施設の建っているマスには建て増す（段6 A3。作り方は建っている施設のまま）
            return expansion_problem(defs, island, occupant)
    if zone is None and tile.zone in (ZONE_OFFICE, ZONE_COMMERCIAL) and not fdef.get("site"):
        return ("reason_office_zone" if tile.zone == ZONE_OFFICE else "reason_commercial_zone", {})
    if fdef.get("coastal") and not is_coastal(island, (x, y), lambda t: is_land(defs, t)):
        return ("reason_not_coastal", {"facility": kind})
    limit = fdef.get("max_per_island")
    if (
        limit is not None
        and sum(1 for f in island.facilities if f.kind == kind and EXPANDS not in f.ext) >= limit
    ):
        return ("reason_too_many", {"facility": kind, "max": limit})
    near = fdef.get("near_capital")
    if near is not None:
        capital = island.capital
        if capital is None or distance(capital, (x, y)) > near:
            return ("reason_far_from_capital", {"facility": kind, "distance": near})
    recipes = fdef["recipes"]
    if recipe is None:
        recipe = default_recipe(defs, kind)
        if recipe is None:
            return ("reason_recipe_required", {"facility": kind})
    if recipe not in recipes:
        return ("reason_unknown_recipe", {"facility": kind})
    problem = recipe_problem(defs, island, kind, recipe)
    water = nature.water_spec(recipes[recipe])
    if problem is None and water is not None:
        # 水力発電所は、川の水が足りない場所には建てられない（その年の雨のぶれは見ない。W-148）
        got = nature.water_factor(island, defs, water, x, y, weather=False)
        if got < water["min"]:
            args = {"facility": kind, "percent": round(got * 100), "min": round(water["min"] * 100)}
            return ("reason_dry_site", args)
    return problem


def recipe_problem(
    defs: Defs, island: IslandState, kind: str, recipe: str
) -> tuple[str, dict[str, Any]] | None:
    """作り方 recipe がこの島で使えないときは (理由, 引数)。鉱業資源と気候（フェーズ3-2）を見る。"""
    spec = defs.facilities[kind]["recipes"][recipe]
    resource = spec.get("requires_resource")
    if resource is not None and resource not in island.resources:
        return ("reason_missing_resource", {"good": resource})
    if not nature.usable(defs, island, spec):
        return ("reason_climate", {"facility": kind, "climate": nature.climate_of(island) or ""})
    return None


def is_land(defs: Defs, terrain: str) -> bool:
    return bool(defs.terrain.get(terrain, {}).get("land"))


def producible_goods(defs: Defs, resources: Iterable[str]) -> set[str]:
    """地下資源 resources のある島で、施設の生産方式をつないで生産できる資源。

    要る地下資源があり、原料をみな島で生産できる生産方式の産出を、増えなくなるまで足していく。
    """
    resources = set(resources)
    recipes = [
        r
        for f in defs.facilities.values()
        for r in f["recipes"].values()
        if r.get("requires_resource") is None or r["requires_resource"] in resources
    ]
    made: set[str] = set()
    while True:
        more = {g for r in recipes if set(r["inputs"]) <= made for g in r["outputs"]} - made
        if not more:
            return made
        made |= more


def switch_problem(
    defs: Defs, island: IslandState, f: Facility, recipe: str
) -> tuple[str, dict[str, Any]] | None:
    """施設 f の作り方を recipe に替えられないときは (理由, 引数)。島で使える作り方
    （recipe_problem）で、掘る鉱業資源が今と同じこと（鉱山は掘る鉱床のマスに建つので、石炭の鉱山を
    鉄鉱石の鉱山には替えられない。替えられると、値段の高い方へ掘る物を替え合って、品不足が行ったり
    来たりした）。"""
    recipes = defs.facilities[f.kind]["recipes"]
    if recipe not in recipes:
        return ("reason_unknown_recipe", {"facility": f.kind})
    now = recipes.get(f.recipe, {}).get("requires_resource")
    if recipes[recipe].get("requires_resource") != now:
        return ("reason_recipe_other_resource", {"facility": f.kind})
    return recipe_problem(defs, island, f.kind, recipe)


def switch_recipe(defs: Defs, f: Facility, recipe: str, turn: int) -> None:
    """施設 f の作り方を recipe に替え、turn のターンの稼働率を半分にする。前の作り方だけの売り値と
    売れ行きの見込みは捨てる（手元に残った物は市場の段で売る）。

    育つ期間のある作り方（畑）は一度に替えず、今の畑を割合で持って、生産の段で少しずつ蒔き直す
    （step_fields。回3）。稼働率は半分にしない（蒔き直した畑が育つまで何も出さない分で表す）。
    """
    if grow_turns(defs, f.kind, recipe) > 0 and grow_turns(defs, f.kind, f.recipe) > 0:
        if crop_of(defs, f.kind, recipe) != crop_of(defs, f.kind, f.recipe):
            f.economy.setdefault(FIELDS, {f.recipe: FULL})
        elif FIELDS in f.economy:
            fields = f.economy[FIELDS]
            fields[recipe] = fields.get(recipe, 0) + fields.pop(f.recipe, 0)
        f.recipe = recipe
        if FIELDS not in f.economy:
            _forget_other_outputs(defs, f)
        return
    f.recipe = recipe
    f.ext[RECIPE_CHANGED] = turn
    _forget_other_outputs(defs, f)


def default_recipe(defs: Defs, kind: str) -> str | None:
    """作り方が1つだけならその名前。複数あれば default（フェーズ3-2 で作り方を足した農場・
    火力発電所・造船所の元の作り方。前と同じ命令で建つように）、それもなければ None（建てるときに
    選ぶ）。"""
    recipes = defs.facilities[kind]["recipes"]
    if len(recipes) == 1:
        return next(iter(recipes))
    return "default" if "default" in recipes else None


def settlement_of(defs: Defs, population: int) -> str:
    """住民の数から決まる、町（住宅地）の見た目の段階（村・町など）。"""
    levels = defs._settlement_levels
    found = levels[0]
    for level in levels:
        if population >= defs.settlements[level]["min_population"]:
            found = level
    return found


def housing_capacity(defs: Defs, f: Facility) -> int:
    level = f.ext.get("level") or defs.settlement_levels()[0]
    return defs.settlements[level]["capacity"]


def new_housing(island: IslandState, kind: str, pos: tuple[int, int], level: str) -> Facility:
    """町（住宅地）を建てる（自動で建つので工事はない。資源は呼ぶ側が倉庫から使う）。"""
    home = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=pos[0],
        y=pos[1],
        owner=OWNER_STATE,
        built=True,
        ext={"level": level},
    )
    island.facilities.append(home)
    return home
