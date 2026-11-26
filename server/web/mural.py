"""姉妹港の壁画（案36）: 貿易額がいちばん大きい相手の島の輪郭を、自分の港に飾る。

相手は、貿易の相手国の集計（web/trade.py の partner_rows と同じ統計 partner.*）の
直近 1 年（12 ターン）の合計で決める。海外の商人は島の形が無いので対象外。
輪郭は、相手の陸のマスを等角のひし形で並べた SVG で、「飾る」を押した時点の
写しとして PortMural に持つ（相手の地形が後で変わっても、壁画はそのまま）。
どの港で貿易したかは統計に残らないので、壁画から相手の港への直リンク（36b）は作らない。
"""

from __future__ import annotations

from typing import Any

from server.country import services as country_services
from server.country.models import PortMural
from server.game import get_game
from server.web import country as country_views
from server.web import display
from server.world import models as m

DEFAULT_FILL = "#44546a"  # 相手がテーマカラーを選んでいないときの輪郭の色


def top_partner(island: m.Island) -> tuple[m.Island, float, int] | None:
    """直近 1 年の貿易額（輸出＋輸入）がいちばん大きい相手の国。無ければ None。"""
    turn, _ = display.latest_stats(island)
    if turn is None:
        return None
    totals: dict[int, float] = {}
    rows = island.stats.filter(turn__gt=turn - 12, name__startswith="partner.").values_list(
        "name", "value"
    )
    for name, value in rows:
        parts = name.split(".")
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("exports", "imports"):
            continue  # 品目別の行と、海外の商人（merchant）は数えない
        totals[int(parts[1])] = totals.get(int(parts[1]), 0.0) + value
    totals = {pid: total for pid, total in totals.items() if total > 0}
    if not totals:
        return None
    partner_id = max(sorted(totals), key=lambda pid: totals[pid])
    partner = m.Island.objects.filter(pk=partner_id, world=island.world).first()
    if partner is None:
        return None
    return partner, totals[partner_id], turn


def outline_svg(partner: m.Island) -> str:
    """相手の島の輪郭の SVG（要素ごと）。陸のマスだけを平らに並べる。
    座標は整数、色はテーマカラーの一覧か既定色だけなので、そのまま画面に出せる。"""
    defs = get_game().defs
    cells = [(t.x, t.y) for t in partner.tiles.all() if defs.terrain.get(t.terrain, {}).get("land")]
    theme = country_views.theme_info(getattr(partner, "profile", None))
    fill = theme["hex"] if theme else DEFAULT_FILL
    if not cells:
        return ""
    points = [((x - y) * 16, (x + y) * 8) for x, y in cells]
    min_x = min(px for px, _ in points) - 20
    max_x = max(px for px, _ in points) + 20
    min_y = min(py for _, py in points) - 12
    max_y = max(py for _, py in points) + 12
    path = "".join(
        f"M{cx},{cy - 8} L{cx + 16},{cy} L{cx},{cy + 8} L{cx - 16},{cy} Z" for cx, cy in points
    )
    view_box = f"{min_x} {min_y} {max_x - min_x} {max_y - min_y}"
    return (
        f'<svg viewBox="{view_box}" class="mural-art" aria-hidden="true">'
        f'<path d="{path}" fill="{fill}"/></svg>'
    )


def install_mural(island: m.Island) -> PortMural | None:
    """今のいちばんの相手で壁画を飾る（飾り直す）。相手がいなければ None。"""
    found = top_partner(island)
    if found is None:
        return None
    partner, total, turn = found
    svg = outline_svg(partner)
    mural, _created = PortMural.objects.update_or_create(
        island=island,
        defaults={"partner": partner, "total": total, "tally_turn": turn, "svg": svg},
    )
    return mural


def mural_data(island: m.Island, mine: bool) -> dict[str, Any] | None:
    """島のホームに出す壁画の材料。壁画が無く、持ち主に候補も無ければ None。"""
    mural = PortMural.objects.filter(island=island).select_related("partner").first()
    candidate = top_partner(island) if mine else None
    if mural is None and candidate is None:
        return None
    out: dict[str, Any] = {"mural": None, "candidate": None}
    if mural is not None:
        profile = getattr(island, "profile", None)
        out["mural"] = {
            "partner_id": mural.partner_id,
            "partner_name": mural.partner.name,
            "total_text": display.stat_text(
                "gdp", mural.total, "money", display.currency_symbol(island.id)
            ),
            "date_text": country_services.date_text(island, profile, mural.tally_turn),
            "svg": mural.svg,
        }
    if candidate is not None:
        partner, _total, _turn = candidate
        stale = mural is not None and mural.partner_id != partner.id
        out["candidate"] = {"partner_id": partner.id, "partner_name": partner.name, "stale": stale}
    return out
