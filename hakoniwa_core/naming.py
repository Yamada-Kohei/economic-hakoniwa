"""地名と、施設に自動で付く名前。材料は naming.yaml にあります。

地名は島を区画に分けて1つずつ付けます。建国のときに選んだ作風（日本風・イギリス風など）で
自動で作り、プレイヤーが命令で付け直せます。首都のある区画の地名は首都名です。
プレイヤーが名前を付けた施設はその名前を使い、付けていない施設は、今の体制・業種・地名から
その場で名前を作ります（体制が変われば自動の名前も変わる）。町（住宅地）には名前を付けません。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState, LogEntry

AUTO_NAME = "auto_name"  # 施設の ext: 生まれたときに決めて残す自動の名前
AUTO_BASIS = "auto_basis"  # 施設の ext: その名前を付けたときの事情。変わると付け直す（出来事）


def _style(defs: Defs, config: GameConfig) -> Mapping[str, Any] | None:
    return defs.naming.get(config.raw.get("locale", "ja"))


def place_styles(defs: Defs, config: GameConfig) -> dict[str, str]:
    """選べる地名の作風と表示名（定義した順）。"""
    style = _style(defs, config)
    if style is None:
        return {}
    return {sid: s["label"] for sid, s in style["places"].items()}


def default_place_style(defs: Defs, config: GameConfig) -> str:
    styles = place_styles(defs, config)
    chosen = config.raw.get("naming", {}).get("place_style")
    return chosen if chosen in styles else next(iter(styles), "")


def generate_places(
    defs: Defs, config: GameConfig, rng: random.Random, count: int, style_id: str = ""
) -> list[str]:
    """島の地名を、作風 style_id で count 個作る（なるべく重ならないように）。"""
    style = _style(defs, config)
    if style is None:
        return [f"{i + 1}" for i in range(count)]
    places = style["places"]
    spec = places.get(style_id) or places[default_place_style(defs, config)]
    joiner = spec.get("joiner", "")
    pool = [a + joiner + b for a in spec["first"] for b in spec["second"] if a != b]
    rng.shuffle(pool)
    while len(pool) < count:
        pool += pool
    return pool[:count]


def place_count(island_size: int, config: GameConfig) -> int:
    block = config.get("naming.place_block")
    per_side = -(-island_size // block)
    return per_side * per_side


def place_index(island: IslandState, x: int, y: int, config: GameConfig) -> int:
    """マスの地名の区画の番号。島を place_block マス四方の区画に分け、区画ごとに1つの地名を使う。"""
    block = config.get("naming.place_block")
    per_side = -(-island.size // block)
    return (y // block) * per_side + x // block


def place_of(island: IslandState, x: int, y: int, config: GameConfig) -> str:
    """マスの地名。"""
    places = island.ext.get("places") or []
    if not places:
        return island.names.get("capital", island.name)
    return places[place_index(island, x, y, config) % len(places)]


def assign_serial(island: IslandState, f: Facility) -> None:
    """同じ種類の施設の中での通し番号（「第三製鉄所」の三）を付ける。作るときに1回だけ呼ぶ。"""
    serials = island.ext.setdefault("serials", {})
    serials[f.kind] = serials.get(f.kind, 0) + 1
    f.ext["serial"] = serials[f.kind]


def facility_name(defs: Defs, config: GameConfig, island: IslandState, f: Facility) -> str:
    """施設の名前。町（住宅地）には名前を付けず、段階の表示名（村・町など）を返す。"""
    if f.kind == config.get("housing.facility"):
        level = f.ext.get("level") or defs.settlement_levels()[0]
        return defs.name("settlements", level, config.raw.get("locale", "ja"))
    if f.name:
        return f.name
    stored = f.ext.get(AUTO_NAME)
    if stored:
        return stored
    return auto_name(defs, config, island, f)


def auto_name(defs: Defs, config: GameConfig, island: IslandState, f: Facility) -> str:
    """施設の自動の名前。地名で呼ぶ形（{place}）で、同じ種類の先に建った施設と同じ名前に
    なるときは、地名に番号を付ける（作風の duplicate。既定は「{place}第{number}」で、
    「オーパス第二銀行公社」のようになる）。
    """
    style = _style(defs, config)
    if style is None:
        return defs.name("facilities", f.kind, config.raw.get("locale", "ja"))
    name, values, text = _base_name(defs, config, island, f, style)
    if text is None or "{place}" not in text:
        return name
    serial = f.ext.get("serial", f.id)
    same = sum(
        1
        for other in island.facilities
        if other.kind == f.kind
        and other is not f
        and not other.name
        and other.ext.get("serial", other.id) < serial
        and _base_name(defs, config, island, other, style)[0] == name
    )
    if same == 0:
        return name
    numbers = style.get("numbers", [])
    number = numbers[same] if same < len(numbers) else str(same + 1)
    duplicate = style.get("duplicate", "{place}第{number}")
    values = {**values, "place": duplicate.format(place=values["place"], number=number)}
    return text.format(**values)


def _base_name(
    defs: Defs, config: GameConfig, island: IslandState, f: Facility, style: Mapping[str, Any]
) -> tuple[str, dict[str, str], str | None]:
    """作風の形で作った名前と、形に入れた値と、使った形（形が合わなければ None）。"""
    display = defs.name("facilities", f.kind, config.raw.get("locale", "ja"))
    serial = f.ext.get("serial", f.id)
    facts = {**island.regime, "owner": f.owner, "category": defs.facilities[f.kind]["category"]}
    industry = style.get("industries", {}).get(f.kind, {})
    numbers = style.get("numbers", [])
    # マスを持たない施設（運送業）は首都の地名で呼ぶ
    x, y = (f.x, f.y) if f.x >= 0 and f.y >= 0 else (island.capital or (0, 0))
    values = {
        "place": place_of(island, x, y, config),
        "short": industry.get("short", display),
        "long": industry.get("long", display),
        "number": numbers[serial - 1] if 0 < serial <= len(numbers) else str(serial),
        "leader": island.names.get("leader", ""),
    }
    for pattern in style["patterns"]:
        when = pattern.get("when", {})
        if any(facts.get(k) != v for k, v in when.items()):
            continue
        every = pattern.get("every")
        if every and serial % every != 0:
            continue
        if "{leader}" in pattern["text"] and not values["leader"]:
            continue
        return pattern["text"].format(**values), values, pattern["text"]
    return display, values, None


# --- 生まれたときに決めて残す名前（段6 A4） ------------------------------------------------
#
# 民営の企業は生まれるときに、創業者の姓か地元の地名を頭の名前（brand）にして名前を抽選する。
# 工場の名前は「頭の名前＋地名＋業種」（土地に根ざす施設は地名＋業種）。国営は体制ごとの形
# （auto_name）で作る。名前は生まれたとき（企業に入った後）に一度だけ決めて ext に残し、
# 付け直すのは「事情」（auto_basis: 属する企業・国営か・体制）が変わった出来事のときだけ。
# 抽選は島の乱数と別に引くので、名前は計算の結果に影響しない。


def _draw(island: IslandState, *key: object) -> random.Random:
    return random.Random(":".join(str(k) for k in ("name", island.id, island.wx, island.wy, *key)))


def _company(defs: Defs, config: GameConfig, island: IslandState) -> Mapping[str, Any] | None:
    style = _style(defs, config)
    if style is None:
        return None
    companies = style.get("companies", {})
    sid = island.ext.get("place_style") or default_place_style(defs, config)
    return companies.get(sid) or companies.get(default_place_style(defs, config))


def _short(defs: Defs, config: GameConfig, company: Mapping[str, Any], kind: str) -> str:
    style = _style(defs, config) or {}
    over = company.get("industries", {}).get(kind, {})
    base = style.get("industries", {}).get(kind, {})
    return over.get("short") or base.get("short") or defs.name("facilities", kind, "ja")


def _long(defs: Defs, config: GameConfig, kind: str) -> str:
    style = _style(defs, config) or {}
    return style.get("industries", {}).get(kind, {}).get("long") or defs.name(
        "facilities", kind, config.raw.get("locale", "ja")
    )


def _site(island: IslandState, f: Facility, config: GameConfig) -> str:
    x, y = (f.x, f.y) if f.x >= 0 and f.y >= 0 else (island.capital or (0, 0))
    return place_of(island, x, y, config)


def _numbered(style: Mapping[str, Any], names: set[str], make: Any) -> str:
    """make(番号の言葉か空) が taken と重ならない名前を返す（先頭は番号なし）。"""
    numbers = style.get("numbers", [])
    n = 1
    while True:
        word = "" if n == 1 else (numbers[n - 1] if n - 1 < len(numbers) else str(n))
        name = make(word)
        if name not in names or n > 99:
            return name
        n += 1


def name_firm(defs: Defs, config: GameConfig, island: IslandState, number: int, turn: int) -> bool:
    """民営の企業の名前（auto_name）と頭の名前（brand）を、生まれた事情で一度だけ決める。"""
    from hakoniwa_core.economy import companies as co

    rec = co.record(island, number)
    company = _company(defs, config, island)
    if rec is None or rec.get(co.AUTO) or rec[co.OWNER] != "private" or company is None:
        return False
    members = sorted(co.members(island, number), key=lambda m: m.id)
    if not members:
        return False
    first = members[0]
    style = _style(defs, config) or {}
    short = _short(defs, config, company, first.kind)
    place = _site(island, first, config)
    rng = _draw(island, "firm", number)
    founder = rng.random() < float(config.get("naming.founder_share"))
    used = {r.get(co.AUTO) for r in island.economy.firms.values()}
    brands = {r.get(co.BRAND) for r in island.economy.firms.values()}
    early = rec.get(co.FOUNDED, turn) < config.get("naming.early_years") * 12
    if founder:
        forms = company["early" if early else "late"]
        pool = list(company["surnames"])
        rng.shuffle(pool)
        pool.sort(key=lambda sn: sn in brands)  # 使われていない姓を先に
        brand = pool[0]
        form = rng.choice(forms)
        make = lambda word: form.format(  # noqa: E731
            surname=brand + (word and f"第{word}"), short=short, place=place
        )
    else:
        brand = place
        form = rng.choice(company["local"])
        make = lambda word: form.format(  # noqa: E731
            place=place + (word and f"第{word}"), surname="", short=short
        )
    rec[co.AUTO] = _numbered(style, used, make)
    rec[co.BRAND] = brand
    return True


def _basis(island: IslandState, f: Facility) -> str:
    from hakoniwa_core.economy import companies as co

    if f.owner == "state":
        regime = island.regime
        leader = bool(island.names.get("leader"))
        axes = ":".join(str(regime.get(a)) for a in ("politics", "head_of_state", "economy"))
        return f"state:{axes}:{leader}"
    number = co.firm_of(f)
    return f"private:{number}" if number is not None else "private"


def _private_name(
    defs: Defs,
    config: GameConfig,
    island: IslandState,
    f: Facility,
    taken: set[str],
) -> str:
    from hakoniwa_core.economy import companies as co

    style = _style(defs, config) or {}
    company = _company(defs, config, island)
    kind = f.kind
    long = _long(defs, config, kind)
    place = _site(island, f, config)
    joiner = company.get("joiner", "") if company else ""
    number = co.firm_of(f)
    rec = co.record(island, number) if number is not None else None
    land = kind in style.get("land_bound", [])

    def tail(word: str) -> str:
        return place + (word and f"第{word}")

    if company is None:
        return auto_name(defs, config, island, f)
    if land:
        return _numbered(style, taken, lambda w: tail(w) + long)
    if rec is not None and rec.get(co.BRAND):
        brand = rec[co.BRAND]
        if brand == place:
            return _numbered(style, taken, lambda w: tail(w) + long)
        return _numbered(style, taken, lambda w: brand + joiner + tail(w) + long)
    # 企業に入らない民営（銀行・運送業・商社など）は、施設ごとに創業者の姓か地名で呼ぶ
    rng = _draw(island, "facility", f.id)
    short = _short(defs, config, company, kind)
    if rng.random() < float(config.get("naming.founder_share")):
        surname = rng.choice(company["surnames"])
        return _numbered(style, taken, lambda w: surname + (w and f"第{w}") + joiner + short)
    return _numbered(style, taken, lambda w: tail(w) + short)


def ensure_names(defs: Defs, config: GameConfig, island: IslandState, turn: int) -> None:
    """企業の名前と、名前のない施設の名前を決め、事情の変わった施設の名前を付け直す。企業に入る
    のを待つ施設・差し押さえ中の施設・町と記念碑は飛ばす（飛ばした施設は従来の名前で呼ぶ）。
    付け直した施設があれば、ターンログに1行出す（初めて名前を付けたときは出さない）。"""
    from hakoniwa_core.economy import companies as co

    style = _style(defs, config)
    if style is None:
        return
    for key in sorted(island.economy.firms, key=int):
        name_firm(defs, config, island, int(key), turn)
    housing = config.get("housing.facility")
    eligible = {
        k
        for k, d in defs.facilities.items()
        if d.get("category") not in ("housing", "monument") and k != housing
    }
    taken: dict[str, set[str]] = {}
    for f in island.facilities:
        if f.ext.get(AUTO_NAME):
            taken.setdefault(f.kind, set()).add(f.ext[AUTO_NAME])
    renamed: list[tuple[str, str]] = []
    for f in sorted(island.facilities, key=lambda f: f.id):
        if f.kind not in eligible or "foreclosed" in f.economy:
            continue
        if f.owner == "private" and co.eligible(defs, config, f) and co.firm_of(f) is None:
            continue  # 企業に入るまで待つ
        basis = _basis(island, f)
        if f.ext.get(AUTO_NAME) and f.ext.get(AUTO_BASIS) == basis:
            continue
        old = f.ext.get(AUTO_NAME)
        names = taken.setdefault(f.kind, set())
        names.discard(old)
        if f.owner == "state":
            f.ext.pop(AUTO_NAME, None)
            new = auto_name(defs, config, island, f)
        else:
            new = _private_name(defs, config, island, f, names)
        names.add(new)
        f.ext[AUTO_NAME] = new
        f.ext[AUTO_BASIS] = basis
        if old and old != new and not f.name:
            renamed.append((old, new))
    if renamed:
        old, new = renamed[0]
        island.log.append(
            LogEntry("facilities_renamed", {"count": len(renamed), "old": old, "new": new})
        )
