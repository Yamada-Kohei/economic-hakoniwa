"""施設が全力で動かなかった理由（施設の status）の表（W-34・GT-42）。

施設の status は「理由の名前」か「理由の名前:財」（予算の項目のこともある）の文字で持つ。
理由を決めるのは主に生産の段（modules/production）で、稼働準備中（STARTING）と理由なし（NONE）は工事・建国・
政治・戦の段も書く。読む所（賃金・解雇・操業目標・標準の計画・測り・産業の鎖・画面・ログ・地図）は、
みなこの表を引く。理由を足すときは、この表に1行足し、文面（messages.yaml の status_* と
facility_stopped_*、画面の手 server/web/display.py の STATUS_HINTS、入門の説明
server/web/views.py の STATUS_HELP）をそろえる。そろっているかは tests/core/test_stops.py が調べる。

表を引く関数は2つ:

- rule(config, status): 計算の判定（休業手当・原料待ち・操業目標を抑えるか）。status の文字と設定
  （電力の財の名前）だけで決め、施設の ext は見ない。組（group）の分け方とは切り離してあり、組を
  変えても計算は変わらない。
- show(config, status, ext): 測り・画面の組と文面。ext を見るのは文の出し分け（止めたわけ
  halt_reason の戦争・ミサイル・占領）だけ。

表にない名前（古い保存や作り漏れ）は「その他」の行になり、計算では休業手当にせず、原料待ちにも
しない。例外では止めない。

現実の稼働率の調べ（米国の Quarterly Survey of Plant Capacity Utilization）は、全力で動かなかった
理由を「注文が足りない・材料が足りない・燃料やエネルギーが足りない・人手や技能が足りない・設備の
制約・保管の制約・運ぶ手段の制約・その他」から選ばせる。組はこれに近い形にし、「お金」（賃金・
原料・予算を払えない）と「災害」「戦争と暴動」「準備中」はこのゲームの分け方として足した。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 理由の名前（施設の status の「:」の前）。文字は保存した世界と同じものから変えない
NONE = ""  # 全力で動いた・止まったわけがない
STARTING = "starting"  # 工事が済んで、まだ動いたことがない
TARGET = "target"  # 売れ行きに合わせて作る量を下げた
NO_DEMAND = "no_demand"  # 発電所: 送電網に電気を使う所がない
POWER_PRICE = "power_price"  # 発電所: 電気料金が発電の費用に届かない
CARGO = "cargo"  # 港・商社・運送業: 扱う荷に合わせて少なく雇う
BUSINESS = "business"  # 小さい銀行: 預金と融資の大きさに合わせて少なく雇う
ORDERS = "orders"  # 土木業者: 受けた工事に合わせて少なく雇う（W-270）
INPUT = "input"  # 原料が品切れ（":財"）
INPUT_PRICE = "input_price"  # 原料が高くて採算が合わない（":財"）
FUNDS = "funds"  # 原料を買うお金が足りない（":財"）
PUBLIC_PRICE = "public_price"  # 学校と大学の教材が高くて買えない（":財"）
POWER = "power"  # 電力不足
NO_GRID = "no_grid"  # 発電所のある送電網につながっていない
WAGES = "wages"  # 賃金を払うお金が足りない
BUDGET = "budget"  # 年の予算の月割りが足りない（":予算の項目"。LB-45）
WORKERS = "workers"  # 働き手が足りない
STAFF_PLAN = "staff_plan"  # 人員計画の枠（PL-59）
ENGINEERS = "engineers"  # 技術者が足りない
EQUIPMENT = "equipment"  # 設備の傷み（回6）
HALTED = "halted"  # 災害（と戦争・ミサイル・占領。ext の halt_reason）で止まっている
RIOT = "riot"  # 暴動で止まっている
RECIPE_CHANGE = "recipe_change"  # 作り方を替えたターン

# 「:」の後に付くもの
GOOD = "good"
ITEM = "item"

# 組（測りの idle.* と産業の鎖の分け方）
SALES = "sales"
INPUTS = "inputs"
G_POWER = "power"
G_FUNDS = "funds"
G_WORKERS = "workers"
G_ENGINEERS = "engineers"
G_EQUIPMENT = "equipment"
G_HALTED = "halted"
WAR = "war"
G_STARTING = "starting"
OTHER = "other"
GROUPS = (
    SALES,
    INPUTS,
    G_POWER,
    G_FUNDS,
    G_WORKERS,
    G_ENGINEERS,
    G_EQUIPMENT,
    G_HALTED,
    WAR,
    G_STARTING,
    OTHER,
)
# 戦争・ミサイル・占領で止めたわけ（ext の halt_reason。暴動は riot の status で別に持つ）
WAR_REASONS = ("war", "missile", "occupied")


@dataclass(frozen=True)
class Reason:
    """理由の1行。"""

    key: str  # 表の行の名前（status の頭。電力の行は "input_price_power" のように _power を付ける）
    group: str  # 測りと産業の鎖の組
    furlough: bool = False  # 休業手当（6割）にする（外から物が来ずに動けない。modules/labor）
    input_short: bool = False  # 原料が手に入らずに動けない（標準の計画・操業目標・発電所の理由）
    caps_target: bool = False  # 操業目標を、動けた割合に1歩足した分までに抑える（modules/market）
    on_power: bool = False  # 財が電力（標準の計画は原料待ちに数えない）
    stop_mark: bool = True  # 稼働率 0 のとき、地図の赤い点と産業の鎖の止まりに出す
    tail: str = ""  # 「:」の後に付くもの（GOOD 財、ITEM 予算の項目、空なら何も付かない）
    text: str = ""  # 画面の名前の文面（messages.yaml）。空なら status_<key>
    hint: str = ""  # 画面の手（display.STATUS_HINTS）。空なら <key>

    @property
    def text_key(self) -> str:
        return self.text or f"status_{self.key}"

    @property
    def hint_key(self) -> str:
        return self.hint or self.key


def _r(key: str, group: str, **kw: Any) -> Reason:
    return Reason(key, group, **kw)


REASONS: dict[str, Reason] = {
    r.key: r
    for r in (
        _r(STARTING, G_STARTING, stop_mark=False),
        _r(TARGET, SALES),
        _r(NO_DEMAND, SALES),
        _r(POWER_PRICE, SALES),
        _r(CARGO, SALES, stop_mark=False),
        _r(BUSINESS, SALES, stop_mark=False),
        _r(ORDERS, SALES, stop_mark=False),
        _r(INPUT, INPUTS, tail=GOOD, furlough=True, input_short=True, caps_target=True),
        _r(INPUT_PRICE, INPUTS, tail=GOOD, input_short=True, caps_target=True),
        _r(FUNDS, G_FUNDS, tail=GOOD, input_short=True, caps_target=True),
        _r(PUBLIC_PRICE, INPUTS, tail=GOOD, input_short=True, caps_target=True),
        _r(POWER, G_POWER, furlough=True, caps_target=True),
        _r(NO_GRID, G_POWER, furlough=True, caps_target=True),
        _r(WAGES, G_FUNDS),
        _r(BUDGET, G_FUNDS, tail=ITEM),
        _r(WORKERS, G_WORKERS),
        _r(STAFF_PLAN, G_WORKERS),
        _r(ENGINEERS, G_ENGINEERS),
        _r(EQUIPMENT, G_EQUIPMENT),
        _r(HALTED, G_HALTED),
        _r(RIOT, WAR),
        _r(RECIPE_CHANGE, OTHER),
    )
}
# 財が電力のときの行: 電気料金が高い・電気代を払うお金がないのは、外から物が来ないのと同じく休業手当
POWER_ROWS: dict[str, Reason] = {
    INPUT_PRICE: _r(
        "input_price_power",
        G_POWER,
        tail=GOOD,
        furlough=True,
        input_short=True,
        caps_target=True,
        on_power=True,
    ),
    FUNDS: _r(
        "funds_power",
        G_POWER,
        tail=GOOD,
        furlough=True,
        input_short=True,
        caps_target=True,
        on_power=True,
    ),
}
# 表にない名前（古い保存や作り漏れ）。計算では何もしない
UNKNOWN = _r("other", OTHER)
# 理由なし（全力で動いた施設や、壊れて理由を消した施設）
BLANK = _r("none", OTHER, stop_mark=False, text="status_other")
# 戦争・ミサイル・占領で止まった（halted の文面だけを替える）
WAR_HALT = _r("war_halt", WAR, text="status_halted")


def split(status: str) -> tuple[str, str]:
    """status を理由の名前と「:」の後（財か予算の項目。なければ空）に分ける。"""
    head, _, tail = (status or "").partition(":")
    return head, tail


def rule(config: Any, status: str) -> Reason:
    """計算の判定に使う行。status と設定（電力の財の名前）だけで決める。"""
    head, tail = split(status)
    if not head:
        return BLANK
    if head in POWER_ROWS and tail and tail == config.get("power.good"):
        return POWER_ROWS[head]
    return REASONS.get(head, UNKNOWN)


def show(config: Any, status: str, ext: dict[str, Any] | None = None) -> Reason:
    """測りと画面の組・文面に使う行。halted は止めたわけ（ext の halt_reason）で戦争の行にする。"""
    row = rule(config, status)
    if row.key == HALTED and (ext or {}).get("halt_reason") in WAR_REASONS:
        return WAR_HALT
    return row


def stopped_mark(config: Any, status: str, rate: int, ext: dict[str, Any] | None = None) -> bool:
    """地図の赤い点を出すか: 稼働率が 0 で、理由が止まりに数えるもの。"""
    return rate <= 0 and bool(status) and show(config, status, ext).stop_mark


# 計算の読み手の短い呼び名


def furloughed(config: Any, status: str) -> bool:
    """休業手当にする status か（外から物が来ずに動けない。電力の割高・資金不足も含む）。"""
    return rule(config, status).furlough


def short_of_inputs(config: Any, status: str) -> bool:
    """原料（電力の割高・資金不足を含む）が手に入らずに動けない status か。"""
    return rule(config, status).input_short


def caps_target(config: Any, status: str) -> bool:
    """操業目標を、動けた割合に1歩足した分までに抑える status か（仕入れか電力が足りない）。"""
    return rule(config, status).caps_target


def short_good(status: str) -> str:
    """原料待ちの status の財（「:」の後）。"""
    return split(status)[1]
