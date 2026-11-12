"""島の画面の入口（UI-71）: 7 つの入口と、その中の小さなタブ。

入口はホーム・地図・施設・「資源・市場」・貿易・経済・国。各ページはテンプレートの page の値で、
どの入口のどのタブかを決める。入口を押すと、その入口の最初のタブのページを開く。
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

# (入口の名前, [(page, URL の名前, タブの名前), ...])。タブの名前が None のものは計画の画面で、
# 体制で名前が変わる（politics.page_label）。日誌と OWNER_ONLY のタブは持ち主だけ
GROUPS: list[tuple[str, list[tuple[str, str, str | None]]]] = [
    ("ホーム", [("home", "home", "ホーム")]),
    ("地図", [("island", "island", "地図")]),
    (
        "施設",
        [("facilities", "island_facilities", "施設の一覧"), ("chain", "island_chain", "産業の鎖")],
    ),
    ("資源・市場", [("market", "island_market", "市場")]),
    (
        "貿易",
        [
            ("trade", "island_trade", "概要"),
            ("trade_contracts", "trade_contracts", "契約"),
            ("trade_blocs", "trade_blocs", "貿易圏"),
            ("trade_fleet", "trade_fleet", "船と港"),
            ("trade_merchant", "trade_merchant", "海外の商人"),
            ("fx", "island_fx", "為替"),
        ],
    ),
    (
        "経済",
        [
            ("economy", "island_economy", "推移と幸福度"),
            ("fiscal", "island_fiscal", "財政"),
            ("finance", "island_finance", "金融"),
        ],
    ),
    (
        "国",
        [
            ("diplomacy", "island_diplomacy", "外交の状態"),
            ("military", "island_military", "軍"),
            ("politics", "island_politics", "政治"),
            ("plan", "island_plan", None),
            ("country", "island_country", "国の紹介"),
            ("diary", "diary", "日誌"),
            ("log", "turn_log", "ターンログ"),
        ],
    ),
]


# 持ち主にだけ出す貿易のタブ（中身が持ち主だけ。UI-03）。他国の人が URL で開いたときは、
# そのタブだけ出す
OWNER_ONLY = {"trade_contracts", "trade_fleet", "trade_merchant"}


def nav(island_id: int, page: str, plan_label: str = "", mine: bool = False) -> dict[str, Any]:
    """入口の一覧（今の入口に current）と、今の入口の中のタブ（タブが 2 つ以上のときだけ）。"""
    groups = []
    tabs: list[dict[str, Any]] = []
    for name, items in GROUPS:
        shown = []
        for key, url_name, label in items:
            if key == "plan" and not plan_label:
                continue
            if key == "diary" and not mine:
                continue
            if key in OWNER_ONLY and not mine and key != page:
                continue
            shown.append(
                {
                    "page": key,
                    "url": reverse(url_name, args=[island_id]),
                    "name": label or plan_label,
                    "current": key == page,
                }
            )
        current = any(t["current"] for t in shown)
        groups.append({"name": name, "url": shown[0]["url"], "current": current})
        if current and len(shown) > 1:
            tabs = shown
    return {"groups": groups, "tabs": tabs}
