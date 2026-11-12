"""ホームの「今月すべきこと」（UI-76・UI-77）。持ち主にだけ出す。

島の画面・国の要約にもとからある値（「注意」・最初の 1 年の「次にすること」・止まった施設・
二国間契約・品不足・失業率・銀行の破綻・条約の提案・貿易圏）を、急ぐ順の 1 本の並びにまとめる。
新しい計算や先の見込みは入れない。各行は短い文と、打てる手のある所へのリンク（url・link_text）を持つ。

急ぐ順は ORDER の 1 か所で決める（上ほど急ぐ）。

1. 初めの更新の前（次にすること）
2. 飢えている層・食料の不足
3. 戦争・停戦、体制の選び直し待ち
4. 破綻した銀行の処理待ち、条約の返事待ち、申し込まれた契約、次のターンに満期の国債
5. 電力の不足、止まった施設、品不足、買えなかった財、失業の急な増え
6. 政情・混乱期間・燃料・売りに出した施設・赤字の施設・海外の商人・国債の利回りの上限・
   休止の間の条約
7. 貿易圏への加盟の申し込み（代表のとき）、最初の 1 年の手順（工業団地・民営の参入・命令の列）
8. どの貿易圏にも入っていない、未読の公電・掲示板の返信

同じことを言う行は 1 つにする。止まった施設・条約の提案は 1 件ずつの行が「注意」の件数の文に代わり、
「次にすること」の行は同じ種類の「注意」に代わる（次にすることには見る所の説明があるため）。
買えなかった財が全部品不足の行にあれば、買えなかった財の行は出さない。返事や処理の命令をもう命令の列に
入れた契約・条約・銀行は出さない。1 件ずつの行は CAP 件までにし、残りは「ほか N 件」の 1 行で一覧へ
つなぐ。

UI-77 の「大事だがめったに使わない」物は、もとの値が要ると言っているときだけ出す: 首都が落ちて体制の
選び直しを待っているとき（military の reselect と同じ値）、代表をしている貿易圏に加盟の申し込みが
あるとき、どの貿易圏にも入っておらず入れる圏があるとき。
"""

from __future__ import annotations

from typing import Any, NamedTuple

from django.urls import reverse

from hakoniwa_core.naming import facility_name
from hakoniwa_core.trade import KEY as TRADE_KEY
from server.game import get_game
from server.web import display, trade
from server.world import models as m
from server.world.repository import facility_from_row

CAP = 5  # 1 件ずつの行（止まった施設・品不足・契約・条約・銀行）に出す数
UNEMPLOYMENT_RISE = 0.01  # 前のターンから完全失業率がこれだけ上がったら出す（主な変化の目安と同じ）

# 急ぐ順（上ほど急ぐ）
ORDER = [
    "first_update",
    "hunger",
    "food",
    "war",
    "regime",
    "bank",
    "treaty",
    "contract",
    "bonds_due",
    "power",
    "stopped",
    "shortage",
    "unmet",
    "unemployment",
    "politics",
    "transition",
    "fuel",
    "on_sale",
    "losing",
    "merchant",
    "yield_capped",
    "caretaker",
    "bloc_applicants",
    "industrial_zone",
    "private_entry",
    "orders",
    "bloc",
    "letters",
    "board",
]
RANK = {kind: i for i, kind in enumerate(ORDER)}

# 「注意」の行の行き先: 種類 → (URL の名前, ページの中の位置, リンクの文)。島の番号を付ける画面だけ
LINKS: dict[str, tuple[str, str, str]] = {
    "hunger": ("island_market", "", "市場を見る"),
    "food": ("island_market", "", "市場を見る"),
    "war": ("island_military", "", "軍の画面へ"),
    "treaty": ("island_diplomacy", "#proposals", "外交の画面へ"),
    "bonds_due": ("island_fiscal", "", "財政の画面へ"),
    "power": ("island_facilities", "", "施設の状態を見る"),
    "stopped": ("island_facilities", "", "施設の状態を見る"),
    "unmet": ("island_market", "", "市場を見る"),
    "politics": ("island_politics", "", "政治の画面へ"),
    "transition": ("island_politics", "", "政治の画面へ"),
    "fuel": ("island_facilities", "", "施設の状態を見る"),
    "on_sale": ("island_facilities", "", "施設の状態を見る"),
    "losing": ("island_facilities", "", "施設の状態を見る"),
    "merchant": ("island_trade", "#merchant", "貿易の画面へ"),
    "yield_capped": ("island_fiscal", "", "財政の画面へ"),
    "caretaker": ("island_diplomacy", "", "外交の画面へ"),
}
# 島の番号を付けない行き先
PLAIN_LINKS = {
    "letters": ("letters", "公電を読む"),
    "board": ("board_notices", "掲示板の知らせへ"),
}
# 1 件ずつの行が、同じ種類の「注意」と「次にすること」に代わる種類
ITEMIZED = {"stopped", "treaty"}


class Alert(NamedTuple):
    """「注意」の 1 行（kind は ORDER の種類。画面と国の要約には text だけを出す）。"""

    kind: str
    text: str


def _row(kind: str, text: str, url: str, link_text: str) -> dict[str, Any]:
    return {"kind": kind, "text": text, "url": url, "link_text": link_text}


def _alert_row(island: m.Island, alert: Alert) -> dict[str, Any]:
    if alert.kind in PLAIN_LINKS:
        name, link = PLAIN_LINKS[alert.kind]
        return _row(alert.kind, alert.text, reverse(name), link)
    name, anchor, link = LINKS[alert.kind]
    return _row(alert.kind, alert.text, reverse(name, args=[island.id]) + anchor, link)


def _capped(
    rows: list[dict[str, Any]], total: int, kind: str, more: str, url: str, link: str
) -> list[dict[str, Any]]:
    """CAP 件までと、残りがあれば「ほか N 件」の 1 行。"""
    out = rows[:CAP]
    if total > len(out):
        out.append(_row(kind, more.format(n=total - len(out)), url, link))
    return out


def _pending(island: m.Island) -> dict[str, set]:
    """命令の列に入っている返事・処理の命令（契約・条約の番号、銀行のマス）。"""
    out: dict[str, set] = {"contract": set(), "treaty": set(), "bank": set()}
    for kind, payload in island.orders.filter(status="pending").values_list("kind", "payload"):
        payload = payload or {}
        if kind in ("accept_contract", "cancel_contract"):
            out["contract"].add(payload.get("contract"))
        elif kind in ("sign_treaty", "refuse_treaty"):
            out["treaty"].add(payload.get("treaty"))
        elif kind == "resolve_bank":
            out["bank"].add((payload.get("x"), payload.get("y")))
    return out


def _stopped(island: m.Island, facilities: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        _row(
            "stopped",
            f"{f['name']}が止まっています（{f['status_text']}）",
            reverse("facility_page", args=[island.id, f["number"]]),
            "施設のページへ",
        )
        for f in facilities["stopped"]
    ]
    url = reverse("island_facilities", args=[island.id])
    more = "ほかに止まっている施設が {n} か所あります"
    return _capped(rows, facilities["stopped_count"], "stopped", more, url, "施設の一覧へ")


def _shortages(island: m.Island) -> tuple[list[dict[str, Any]], set[str]]:
    """品不足がターンログで知らせるターン数（records.shortage.turns）以上続いている財。"""
    game = get_game()
    least = game.config.get("records.shortage")["turns"]
    streaks = ((island.economy or {}).get("ext") or {}).get("shortage") or {}
    goods = sorted(
        (-turns, good)
        for good, turns in streaks.items()
        if turns >= least and good in game.defs.goods
    )
    rows = [
        _row(
            "shortage",
            f"{game.defs.name('goods', good)}の品不足が {-turns} ターン続いています",
            reverse("market_good", args=[island.id, good]),
            "品目のページへ",
        )
        for turns, good in goods
    ]
    url = reverse("island_chain", args=[island.id])
    more = "ほかに品不足の続いている品目が {n} あります"
    return _capped(rows, len(rows), "shortage", more, url, "産業の鎖を見る"), {g for _, g in goods}


def _unemployment(
    island: m.Island, now: dict[str, float], before: dict[str, float]
) -> list[dict[str, Any]]:
    if "unemployment" not in now or "unemployment" not in before:
        return []
    if now["unemployment"] - before["unemployment"] < UNEMPLOYMENT_RISE - 1e-12:
        return []
    was = display.stat_text("unemployment", before["unemployment"], "%", "")
    is_ = display.stat_text("unemployment", now["unemployment"], "%", "")
    return [
        _row(
            "unemployment",
            f"完全失業率が {was} から {is_} に上がりました。止まった施設と、公共事業・失業手当を"
            "確かめます",
            reverse("island_fiscal", args=[island.id]),
            "財政の画面へ",
        )
    ]


def _banks(island: m.Island, pending: set) -> list[dict[str, Any]]:
    """破綻して処理（命令 resolve_bank）を待っている銀行。"""
    game = get_game()
    failed = [
        row
        for row in island.facilities.filter(kind=game.config.get("banking.facility")).order_by(
            "number"
        )
        if row.built
        and (row.economy or {}).get("failed") is not None
        and (row.x, row.y) not in pending
    ]
    if not failed:
        return []
    state = display.island_for_naming(island)
    rows = []
    for row in failed:
        f = facility_from_row(row)
        name = facility_name(game.defs, game.config, state, f)
        rows.append(
            _row(
                "bank",
                f"{name}が破綻し、処理を待っています。救済・引き継ぎ・清算から選びます"
                "（選ばなければ次のターンに引き継ぎ）",
                reverse("facility_page", args=[island.id, row.number]),
                "施設のページへ",
            )
        )
    url = reverse("island_finance", args=[island.id]) + "#banks"
    more = "ほかに処理を待っている銀行が {n} あります"
    return _capped(rows, len(rows), "bank", more, url, "金融の画面へ")


def _treaties(island: m.Island, dip: dict[str, Any], pending: set) -> list[dict[str, Any]]:
    url = reverse("island_diplomacy", args=[island.id]) + "#proposals"
    rows = [
        _row(
            "treaty",
            f"{t['from']['name']}から条約「{t['title']}」の提案が届いています。署名か断りを選びます",
            url,
            "外交の画面へ",
        )
        for t in dip.get("to_answer", [])
        if t["id"] not in pending
    ]
    more = "ほかに返事をしていない条約の提案が {n} 件あります"
    return _capped(rows, len(rows), "treaty", more, url, "外交の画面へ")


def _contracts(
    island: m.Island, state: dict[str, list], names: dict, pending: set
) -> list[dict[str, Any]]:
    url = reverse("island_trade", args=[island.id]) + "#contracts"
    offered = [
        c
        for c in trade.contract_rows(state["contracts"], island.id, island.world.turn, names)
        if c["can_accept"] and c["id"] not in pending
    ]
    rows = [
        _row(
            "contract",
            f"{c['partner_name']}から二国間契約の申し込みが来ています（こちらが{c['side_name']}: "
            f"{c['good_name']} 1ターン {c['quantity']:,g} {c['unit']}・1単位 "
            f"{display.currency_prefix(c['currency_symbol'])}{c['price']:,g}）。"
            "受けるか断るかを選びます",
            url,
            "貿易の画面へ",
        )
        for c in offered
    ]
    more = "ほかに申し込まれた契約が {n} 件あります"
    return _capped(rows, len(rows), "contract", more, url, "貿易の画面へ")


def _regime(island: m.Island) -> list[dict[str, Any]]:
    """首都が落ちて体制が倒れ、選び直し（命令 choose_regime）を待っている（軍の画面と同じ値）。"""
    politics = (island.ext or {}).get("politics") or {}
    if politics.get("reselect") is None:
        return []
    until = (politics.get("transition") or {}).get("until")
    text = "体制が倒れました。体制を選び直します"
    if until is not None:
        text += f"（{display.turn_text(until)}までに決めないと、今の体制のまま固まります）"
    return [_row("regime", text, reverse("island_military", args=[island.id]), "軍の画面へ")]


def _blocs(island: m.Island, state: dict[str, list]) -> list[dict[str, Any]]:
    """代表をしている圏への加盟の申し込みと、どの圏にも入っていないこと（入れる圏があるとき）。"""
    url = reverse("island_trade", args=[island.id])
    rows = []
    member = [b for b in state["blocs"] if island.id in b["members"]]
    for b in member:
        if b["rep"] == island.id and b.get("applicants"):
            rows.append(
                _row(
                    "bloc_applicants",
                    f"貿易圏「{b['name']}」に加盟の申し込みが {len(b['applicants'])} 件あります"
                    "（代表の承認を待っています）",
                    url + "#blocs",
                    "貿易の画面へ",
                )
            )
    if member or "since" not in ((island.ext or {}).get(TRADE_KEY) or {}):
        return rows
    joinable = [b for b in state["blocs"] if island.id not in b.get("applicants", [])]
    if joinable:
        rows.append(
            _row(
                "bloc",
                f"どの貿易圏にも入っていません。入れる貿易圏が {len(joinable)} あります",
                url + "#other-blocs",
                "貿易の画面へ",
            )
        )
    return rows


def rows(
    island: m.Island,
    alerts: list[Alert],
    steps: list[dict[str, Any]],
    facilities: dict[str, Any],
    goods: list[dict[str, Any]],
    dip: dict[str, Any],
    now: dict[str, float],
    before: dict[str, float],
) -> list[dict[str, Any]]:
    """今月すべきこと（急ぐ順）。alerts は持ち主の「注意」、steps は「次にすること」、
    facilities は facility_state、goods は国の要約の財、dip は diplomacy.brief、now と before は
    今と前のターンの統計。"""
    pending = _pending(island)
    state = trade.world_state(island.world)
    names = trade.directory(island.world) if state["contracts"] else {}
    shortage_rows, short = _shortages(island)
    itemized = (
        _stopped(island, facilities)
        + shortage_rows
        + _unemployment(island, now, before)
        + _banks(island, pending["bank"])
        + _treaties(island, dip, pending["treaty"])
        + _contracts(island, state, names, pending["contract"])
        + _regime(island)
        + _blocs(island, state)
    )
    covered = set(ITEMIZED)
    # 買えなかった財が全部品不足の行にあれば、買えなかった財の行は要らない
    unmet = {g["good"] for g in goods if g.get("unmet")}
    if unmet and unmet <= short:
        covered.add("unmet")
    out = list(itemized)
    step_rows = [
        _row(s["key"], s["text"], s["url"], s["link_text"])
        for s in steps
        if s["key"] not in covered
    ]
    out += step_rows
    covered |= {r["kind"] for r in step_rows}
    out += [_alert_row(island, a) for a in alerts if a.kind not in covered]
    out.sort(key=lambda r: RANK.get(r["kind"], len(ORDER)))
    return out
