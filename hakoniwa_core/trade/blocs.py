"""貿易圏（フェーズ3-1）。

貿易圏は ``world.ext["trade"]["blocs"]`` に、番号の順に辞書で持ちます。

- id・name・created（作ったターン）
- rep: 代表の島。members: 加盟国（番号の順）。applicants: 承認を待つ島。
  leaving: 次のターンに抜ける島
- open: 誰でも入れるか（False は代表の承認が要る）
- currency: 決済通貨（加盟国のどれかの通貨。通貨の番号は島の番号）
- prices: 財ごとの圏の値段（決済通貨）。history: 財ごとの直近 trade.history_turns ターンの値段
- volume・demand・supply: 財ごとの前のターンの約定した量・買い・売り
- no_tariff: 圏内の関税をなくす決まり（フェーズ3-3。true なら圏の市場で買った荷に関税がかからない）
- fixed: 財ごとの圏内の固定価格（フェーズ3-3。決済通貨。値幅 0 で板寄せし、余った側を量に比例して
  分ける）

命令（作る・入る・抜ける・設定・代表を譲る・引き継ぐ）は、命令の段で島の申し送りに積み、貿易の段の
初めに島の番号の順に決めます（requests）。決められなかったわけはその島のログに書きます。決済通貨・
加盟の決まり・代表が変わったときは、加盟国すべてのログに知らせます（決済通貨は商社の売り買いに
すぐ響くため）。

圏の市場の値段は、初めは加盟国の島の値段を為替で決済通貨に直して平均したものです。売り買いのどちら
かしかない財は、出ている注文の一番よい指値の方へ値幅の中で動き、どちらもない財は、加盟国の島の値段
の平均に戻します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.trade import cells, currency_label, distance, next_id, world_state
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS


def blocs(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world)["blocs"]


def find(world: WorldState, bloc_id: int) -> dict[str, Any] | None:
    for b in blocs(world):
        if b["id"] == bloc_id:
            return b
    return None


def member_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    """島が入っている圏（番号の順）。"""
    return [b for b in blocs(world) if island_id in b["members"]]


def currencies_of(world: WorldState, island_id: int) -> set[int]:
    """島が圏の市場で使う決済通貨（自国通貨を除く）。"""
    return {b["currency"] for b in member_of(world, island_id)} - {island_id}


def is_inactive(island: IslandState) -> bool:
    """休止中の島か（サーバーが読み込むときに印を付ける）。"""
    return bool(island.ext.get("inactive"))


# --- 命令の申し送り ----------------------------------------------------------------


def _tell(
    world: WorldState, bloc: dict[str, Any], kind: str, args: dict[str, Any], skip: int = -1
) -> None:
    """加盟国（skip の島を除く）のログに知らせる。"""
    for member in bloc["members"]:
        island = world.islands.get(member)
        if island is not None and member != skip:
            island.log.append(LogEntry(kind, {"bloc": bloc["name"], **args}))


def _new_rep(world: WorldState, bloc: dict[str, Any], rep: int) -> None:
    """代表を rep にして、本人と、ほかの加盟国に知らせる。"""
    bloc["rep"] = rep
    island = world.islands.get(rep)
    if island is None:
        return
    island.log.append(LogEntry("bloc_rep_received", {"bloc": bloc["name"]}))
    _tell(world, bloc, "bloc_rep_changed", {"country": island.name}, skip=rep)


def create(world: WorldState, island: IslandState, name: str, open_: bool, turn: int) -> None:
    state = world_state(world)
    bloc = {
        "id": next_id(state, "bloc"),
        "name": name,
        "rep": island.id,
        "members": [island.id],
        "applicants": [],
        "leaving": [],
        "open": open_,
        "currency": island.id,
        "created": turn,
        "prices": {},
        "history": {},
        "volume": {},
        "demand": {},
        "supply": {},
    }
    state["blocs"].append(bloc)
    island.log.append(LogEntry("bloc_created", {"bloc": name}))


def join(world: WorldState, island: IslandState, bloc: dict[str, Any]) -> str | None:
    """入る（承認制なら申し込む）。入れなければ、そのわけ。"""
    if island.id in bloc["members"]:
        return "reason_bloc_member"
    if bloc["open"]:
        _add_member(world, bloc, island)
        return None
    if island.id not in bloc["applicants"]:
        bloc["applicants"].append(island.id)
        bloc["applicants"].sort()
    island.log.append(LogEntry("bloc_applied", {"bloc": bloc["name"]}))
    rep = world.islands.get(bloc["rep"])
    if rep is not None:
        rep.log.append(LogEntry("bloc_application", {"bloc": bloc["name"], "country": island.name}))
    return None


def approve(world: WorldState, bloc: dict[str, Any], applicant: int) -> str | None:
    if applicant not in bloc["applicants"]:
        return "reason_bloc_no_application"
    bloc["applicants"].remove(applicant)
    other = world.islands.get(applicant)
    if other is None:
        return "reason_bloc_no_application"
    _add_member(world, bloc, other)
    return None


def _add_member(world: WorldState, bloc: dict[str, Any], island: IslandState) -> None:
    bloc["members"] = sorted({*bloc["members"], island.id})
    fxrates.ensure(world, island, bloc["currency"])
    island.log.append(LogEntry("bloc_joined", {"bloc": bloc["name"]}))


def leave(island: IslandState, bloc: dict[str, Any]) -> str | None:
    """抜ける（次のターンから。運ぶ途中の荷はそのまま届く）。"""
    if island.id not in bloc["members"]:
        return "reason_bloc_not_member"
    if island.id not in bloc["leaving"]:
        bloc["leaving"].append(island.id)
    island.log.append(LogEntry("bloc_leaving", {"bloc": bloc["name"]}))
    return None


def settle_leaves(world: WorldState) -> None:
    """抜けると決めた島を外す（貿易の段の終わり）。代表が抜けたら番号の小さい加盟国が代表に、
    決済通貨の島が抜けたら代表の通貨にする。加盟国がいなくなった圏はなくす。"""
    keep = []
    for bloc in blocs(world):
        gone = set(bloc.get("leaving", []))
        bloc["leaving"] = []
        if gone:
            bloc["members"] = [m for m in bloc["members"] if m not in gone]
            for island_id in sorted(gone):
                island = world.islands.get(island_id)
                if island is not None:
                    island.log.append(LogEntry("bloc_left", {"bloc": bloc["name"]}))
        if not bloc["members"]:
            continue
        if bloc["rep"] not in bloc["members"]:
            _new_rep(world, bloc, bloc["members"][0])
        if bloc["currency"] not in bloc["members"]:
            change_currency(world, bloc, bloc["rep"])
        keep.append(bloc)
    world_state(world)["blocs"] = keep


def change_currency(world: WorldState, bloc: dict[str, Any], currency: int) -> None:
    """決済通貨を変える。圏の値段は今のレートで新しい通貨に直す。"""
    old = bloc["currency"]
    if old == currency:
        return
    holder = world.islands[currency]
    fxrates.ensure(world, holder, old)
    e = fxrates.rate(holder, old)  # 古い通貨 1 単位が新しい通貨でいくらか
    bloc["prices"] = {g: max(1, p * e // BASIS) for g, p in bloc["prices"].items()}
    if bloc.get("fixed"):
        bloc["fixed"] = {g: max(1, p * e // BASIS) for g, p in bloc["fixed"].items()}
    bloc["history"] = {}
    bloc["currency"] = currency
    for member in bloc["members"]:
        fxrates.ensure(world, world.islands[member], currency)
    _tell(world, bloc, "bloc_currency_changed", {"currency": currency_label(world, currency)})


def settings(
    world: WorldState, bloc: dict[str, Any], open_: bool | None, currency: int | None
) -> str | None:
    if currency is not None:
        if currency not in bloc["members"]:
            return "reason_bloc_currency"
        change_currency(world, bloc, currency)
    if open_ is not None and open_ != bloc["open"]:
        bloc["open"] = open_
        _tell(world, bloc, "bloc_opened" if open_ else "bloc_closed", {})
    return None


def no_tariff(bloc: dict[str, Any]) -> bool:
    """圏内の関税をなくす決まりがあるか（フェーズ3-3）。"""
    return bool(bloc.get("no_tariff"))


def fixed_price(bloc: dict[str, Any], good: str) -> int | None:
    """財の圏内の固定価格（決済通貨。決めていなければ None）。"""
    return bloc.get("fixed", {}).get(good)


def set_no_tariff(world: WorldState, bloc: dict[str, Any], value: bool) -> None:
    if bool(bloc.get("no_tariff")) == value:
        return
    if value:
        bloc["no_tariff"] = True
    else:
        bloc.pop("no_tariff", None)
    _tell(world, bloc, "bloc_no_tariff" if value else "bloc_tariff_allowed", {})


def set_fixed(world: WorldState, bloc: dict[str, Any], good: str, price: int) -> None:
    """財の圏内の固定価格を決める（price 0 でやめる）。"""
    fixed = bloc.setdefault("fixed", {})
    if price > 0:
        fixed[good] = int(price)
        args = {
            "good": good,
            "price": int(price),
            "currency": currency_label(world, bloc["currency"]),
        }
        _tell(world, bloc, "bloc_price_fixed", args)
    elif fixed.pop(good, None) is not None:
        _tell(world, bloc, "bloc_price_unfixed", {"good": good})
    if not fixed:
        bloc.pop("fixed", None)


def transfer(world: WorldState, bloc: dict[str, Any], to: int) -> str | None:
    if to not in bloc["members"] or to == bloc["rep"]:
        return "reason_bloc_not_member"
    _new_rep(world, bloc, to)
    return None


def take_over(world: WorldState, island: IslandState, bloc: dict[str, Any]) -> str | None:
    rep = world.islands.get(bloc["rep"])
    if island.id not in bloc["members"]:
        return "reason_bloc_not_member"
    if rep is not None and not is_inactive(rep):
        return "reason_bloc_rep_active"
    _new_rep(world, bloc, island.id)
    return None


# --- 圏の値段と見え方 --------------------------------------------------------------


def home_average(world: WorldState, bloc: dict[str, Any], good: str) -> int | None:
    """加盟国の島の値段を決済通貨に直した平均（どの加盟国にも値段がなければ None）。"""
    currency = bloc["currency"]
    values = []
    for member in bloc["members"]:
        island = world.islands[member]
        price = island.economy.prices.get(good)
        if not price:
            continue
        values.append(price * BASIS // max(1, fxrates.rate(island, currency)))
    return sum(values) // len(values) if values else None


def reference(world: WorldState, bloc: dict[str, Any], good: str) -> int | None:
    """圏の市場の基準の値段（前の値段。なければ加盟国の値段の平均）。"""
    return bloc["prices"].get(good) or home_average(world, bloc, good)


def view_of(world: WorldState, bloc: dict[str, Any], island_id: int) -> dict[str, Any]:
    """加盟国に見せる圏の様子（次のターンの市場の段で、商社が読む）。

    cells は、運賃の見込みに使う距離: ほかの加盟国を近い順に並べた前半分の平均（区画）。supply は、
    前のターンの圏の市場に売りに出ていた財（標準の計画が、輸入で手に入る物として見る。フェーズ3-2）。
    short は、前のターンの圏の市場で買いたい量の一部しか買えなかった財（標準の計画が、輸出向けに
    作る物として見る。フェーズ3-2）。
    """
    here = world.islands[island_id]
    others = sorted(cells(here, world.islands[m]) for m in bloc["members"] if m != island_id)
    near = others[: max(1, (len(others) + 1) // 2)] if others else [1]
    return {
        "name": bloc["name"],
        "currency": bloc["currency"],
        "members": len(bloc["members"]),
        "rep": bloc["rep"],
        "cells": max(1, round(sum(near) / len(near))),
        "prices": dict(bloc["prices"]),
        "supply": sorted(g for g, q in bloc.get("supply", {}).items() if q > 0),
        "short": sorted(
            g for g, q in bloc.get("demand", {}).items() if q > bloc.get("volume", {}).get(g, 0)
        ),
        # 圏内の関税をなくす決まりと、固定価格（フェーズ3-3。商社の見込みと画面に使う）
        "no_tariff": no_tariff(bloc),
        "fixed": dict(bloc.get("fixed", {})),
    }


def nearest_distance(world: WorldState, a: int, b: int) -> float:
    return distance(world.islands[a], world.islands[b])
