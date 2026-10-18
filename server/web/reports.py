"""島の統計の画面（経済・市場・施設・財政・金融）と、その JSON API の中身の組み立て。

ルールの計算はせず、保存した統計（StatRecord）・市場の記録（MarketRecord）・約定（Trade）・施設の帳簿
（FacilityBook）・勘定と国債の銘柄と、定義データの表示名を使う。施設の帳簿・約定の相手・銀行ごとの
中身は島の持ち主にだけ出し、ほかの人には国全体の統計だけを出す。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Max, Q

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import (
    ENGINEERS,
    GENERAL,
    JOBLESS,
    OWNERS,
    books,
    debt,
    entity_of,
    services,
)
from hakoniwa_core.modules.failures import METHODS as RESOLUTION_METHODS
from hakoniwa_core.modules.population import subsistence_persons, tile_jobless
from hakoniwa_core.naming import facility_name, place_of
from hakoniwa_core.state import IslandState, Tile
from hakoniwa_core.trade import abroad
from hakoniwa_core.units import from_milli
from server.economy.models import Account, FacilityBook, MarketRecord, Trade
from server.game import get_game
from server.web import display
from server.world import models as m
from server.world.repository import bond_from_row, facility_from_row, load_money

DEFAULT_TURNS = 120  # 範囲を決めないときに返すターン数（10年）
MAX_TURNS = 2400  # 1回に返せるターン数
MAX_NAMES = 40  # 1回に選べる名前の数
MAX_SERIES = 80  # 1回に返せる系列の数（名前の頭で選んだ分を含む）

# --- 統計の表示名と単位 ---------------------------------------------------------------

LAYER_NAMES = {
    GENERAL: "一般の労働者",
    OWNERS: "資産を持つ層",
    ENGINEERS: "技術者",
    JOBLESS: "失業している労働者",
}
LAYER_FIELDS = {
    "persons": ("の人数", "人"),
    "income": ("の所得（このターン）", "money"),
    "income_per_person": ("の1人あたりの所得", "money"),
    "deposits": ("の預金", "money"),
    "happiness": ("の幸福度", ""),
    # 飢えの知らせ（Codex の試し遊び、ターン 250〜300）
    "food": ("の食べられた割合（要る量に対して）", "%"),
    "hunger": ("の飢えの蓄え（栄養の不足の月数）", " か月分"),
    "starved": ("の飢えで亡くなった人（このターン）", "人"),
}
BANK_FIELDS = {
    "deposits": ("預金", "money"),
    "loans": ("融資", "money"),
    "net_worth": ("自己資本", "money"),
    "capital_ratio": ("自己資本比率", "%"),
}
# 予算の項目の外の支出（spent.<項目>）の表示名。予算の項目は messages.yaml の budget_<項目>
SPENT_NAMES = {
    "civil_servants": "公務員の賃金",
    "unemployment": "失業手当",
    "state_facilities": "国営施設の運転資金",
    "bonds": "国債の利払いと償還",
}
# 国庫の出入りのうち、借入と返済（財政の収支に数えない）
FINANCING = {"in": {"bond_issue"}, "out": {"bond_redemption"}}
# 国庫の出入りで、仕訳の種類の名前だけでは何のお金かわかりにくいもの（収入か支出か、種類）
FLOW_NAMES = {
    ("in", "sale"): "物の売り渡しの代金（国の倉庫など）",
    ("out", "sale"): "物の買い入れの代金（資材・教材など）",
    ("out", "wage"): "賃金（公務員・作業員・教員など）",
    ("out", "subsidy"): "据え置きの補助金で国庫から出た現金（独立採算の施設の分）",
}

# 名前で決まる統計（display.SUMMARY_STATS に足す分）。単位は display.stat_text と同じ
_EXTRA_STATS = [
    ("gdp.spending", "GDP（支出の側）", "money"),
    ("gdp.gap", "GDP の生産の側と支出の側の差", "%"),
    ("debt_ratio", "国債の残高（GDP の1年分に対する割合）", "%"),
    ("wage.min", "最も低い賃金", "money"),
    ("wage.max", "最も高い賃金", "money"),
    ("wage.real", "実質賃金（建国時の物価で）", "money"),
    ("money.deposits", "預金の総額", "money"),
    ("money.central_bank", "中央銀行のお金（準備預金と国庫）", "money"),
    ("cb_lending", "中央銀行の銀行への貸し出し", "money"),
    ("bonds.underwritten", "中央銀行の国債の直接の引き受け（このターン）", "money"),
    ("bonds.traded", "国債の売買（このターン）", "money"),
    ("bonds.cb_bought", "中央銀行が買った国債（このターン）", "money"),
    ("bonds.cb_sold", "中央銀行が売った国債（このターン）", "money"),
    ("bonds.central_bank", "中央銀行が持つ国債", "money"),
    ("inequality", "格差（資産を持つ層と一般の労働者の1人あたりの所得の比）", "ratio"),
    ("profit", "施設の損益の合計（このターン）", "money"),
    ("freight.surcharge", "運賃の割り増し（運送業の人手不足）", "ratio"),
    ("loans.write_off", "貸し倒れ（このターン）", "money"),
    ("power.used", "届いた電力", "good:electricity"),
    ("food.need", "食料の必要量", "good:food"),
    ("food.supplied", "届いた食料", "good:food"),
    ("education.goal", "学校に通えている子どもの割合（動いている学校の定員から）", "percent"),
    ("education.schooled", "働き手に加わる若い人のうち学んだ人の割合", "percent"),
    ("education.seats", "動いている学校の定員", "人"),
    ("engineers.employed", "働いている技術者", "人"),
    ("engineers.seated", "技術者の席に就いた技術者", "人"),
    ("engineers.wage", "技術者の平均賃金", "money"),
    ("engineers.trained", "大学が育てた技術者（このターン）", "人"),
    ("engineers.retired", "引退した技術者（このターン）", "人"),
    ("infra.queued", "インフラの工事の列のマス", " マス"),
    ("works.workers", "公共事業の作業員", "人"),
    ("firms.private_sites", "民営の工事中の施設", ""),
    ("firms.abandoned", "建設をやめた民営の工事（このターン）", ""),
    ("fiscal.revenue", "財政の収入（国債の発行を除く）", "money"),
    ("fiscal.spending", "財政の支出（国債の償還を除く）", "money"),
    ("fiscal.balance", "財政の収支", "money"),
    # 貿易と為替（フェーズ3-1）
    ("trade.exports", "輸出（このターン）", "money"),
    ("trade.imports", "輸入（このターン。海外の商人を含む）", "money"),
    ("trade.balance", "貿易収支（輸出 − 輸入）", "money"),
    ("trade.merchant", "海外の商人から買った額（このターン）", "money"),
    ("trade.unpaid", "お金が足りず払えなかった運賃と港の使用料", "money"),
    ("ships", "輸送船", " 隻"),
    ("ships.busy", "航海に出ている輸送船", " 隻"),
    ("freight.ship_rate", "船の運賃（荷 1 トン・1 区画あたり）", "money"),
    ("port.handled", "港で積み下ろした荷（このターン）", " トン"),
    ("fx.holdings", "外貨の持ち高（今のレートで自国通貨に直した額）", "money"),
    ("money.nonresident", "外国が持つ自国通貨（非居住者の預金）", "money"),
    # 産業政策と国際金融（フェーズ3-3）
    ("trade.tariffs", "関税の収入（このターン）", "money"),
    ("trade.tariff_unpaid", "お金が足りず払えなかった関税", "money"),
    ("trade.export_subsidies", "輸出補助（このターン）", "money"),
    ("trade.subsidy_unpaid", "予算が足りず払えなかった輸出補助", "money"),
    ("aid.sent", "送ったお金の援助（このターン）", "money"),
    ("aid.received", "受け取ったお金の援助（このターン）", "money"),
    ("bop.current", "経常収支（このターン）", "money"),
    ("bop.goods", "経常収支・貿易収支", "money"),
    ("bop.services", "経常収支・サービス収支", "money"),
    ("bop.income", "経常収支・所得収支（外国の国債と融通の利子）", "money"),
    ("bop.transfers", "経常収支・移転（援助・仕送り・移る人の持ち出す預金）", "money"),
    ("bop.financial", "金融収支（資産の増加 − 負債の増加）", "money"),
    ("bop.assets.fx", "金融収支・外貨の持ち高の増加", "money"),
    ("bop.assets.bonds", "金融収支・外国の国債の増加", "money"),
    ("bop.liabilities.deposits", "金融収支・外国が持つ自国通貨の増加（負債）", "money"),
    ("bop.assets.swaps", "金融収支・中央銀行が融通で貸した外貨の増加", "money"),
    ("bop.liabilities.bonds", "金融収支・外国が持つ自国の国債の増加（負債）", "money"),
    ("bop.liabilities.swaps", "金融収支・中央銀行が融通で借りた外貨の増加（負債）", "money"),
    ("bop.liabilities.merchant", "金融収支・海外の商人に払った額（負債）", "money"),
    ("fx.reserves", "外貨準備（政府と中央銀行の外貨と外国の国債、融通で貸した外貨）", "money"),
    ("fx.swap.borrowed", "中央銀行が融通で借りている額（自国通貨に直す）", "money"),
    ("fx.swap.lent", "中央銀行が融通で貸している額（自国通貨に直す）", "money"),
    ("foreign_bonds.gov", "政府が持つ外国の国債（時価）", "money"),
    ("foreign_bonds.cb", "中央銀行が持つ外国の国債（時価）", "money"),
    ("foreign_bonds.banks", "市中銀行が持つ外国の国債（時価）", "money"),
    ("bonds.foreign", "外国が持つ自国の国債（額面）", "money"),
    ("bonds.bought_abroad", "入札で外国が買った国債（このターン）", "money"),
    ("fx.peg_broken", "固定相場が外れた通貨の数（このターン）", ""),
    ("titles", "持っている称号の数", ""),
    # 国をまたぐ移民（バランス調整の回3）
    ("migration.left", "ほかの国へ移り住んだ人（このターン）", "人"),
    ("migration.arrived", "ほかの国から移り住んできた人（帰ってきた人も。このターン）", "人"),
    ("migration.returned", "国外から帰ってきた人（このターン）", "人"),
    ("migration.escaped", "出国を止められたのに国境を越えた人（このターン）", "人"),
    ("migration.trapped", "出たいのに出国を止められている人（1年あたり）", "人"),
    ("migration.seized", "国を出た人から国が引き取った預金（このターン）", "money"),
    ("migration.remittance_in", "国外に住む人からの仕送り（このターン）", "money"),
    ("migration.remittance_out", "国外への仕送り（このターン）", "money"),
    ("migration.abroad", "国外に住む、この国から移った人", "人"),
    ("migration.foreign_born", "ほかの国から移ってきて住んでいる人", "人"),
    # 政治と計画経済（フェーズ3-4）
    ("politics.stability", "政情（0〜100）", ""),
    ("politics.target", "政情の目標（いまの島の様子から）", ""),
    ("politics.support", "支持率", "percent"),
    ("politics.seats", "与党の議席", "percent"),
    ("politics.halted", "暴動で止まった施設（このターン）", ""),
    ("politics.destroyed", "暴動で壊された施設（このターン）", ""),
    ("labor.mobilized", "労働の動員で働く人", "人"),
    ("plan.queue", "行列（家計が配給で買えなかった割合。公定価格の額で）", "%"),
    ("plan.forced_saving", "強制貯蓄（配給で買えずに残ったお金。このターン）", "money"),
    # 据え置きの補助金は帳簿の上の額（独立採算の施設への補助に加え、国庫直轄の施設の分も含む。
    # 国庫直轄の分は国庫の中で付け替えるだけ）。国庫から出た現金は fiscal.out.subsidy
    ("plan.subsidy", "据え置きの補助金（帳簿の上の額。国庫直轄の分も。このターン）", "money"),
    ("plan.subsidy_unpaid", "国庫が足りず払えなかった据え置きの補助金", "money"),
    ("trade.merchant_gap", "海外の商人に政府が払った、公定価格との差（このターン）", "money"),
    ("cars.households", "家計が持つ自動車", " 台"),
    # サービスの仕事と人口の増え方（バランス調整の回4）
    ("services.workers", "商業・サービス業で働く人", "人"),
    ("services.sales", "サービスの売上（このターン）", "money"),
    ("services.sold", "売れたサービス（このターン）", "good:services"),
    ("services.unused", "売れ残って消えたサービス（このターン）", "good:services"),
    ("population.births", "生まれた人（このターン）", "人"),
    ("population.deaths", "亡くなった人（飢えのほか。このターン）", "人"),
    ("population.starved", "飢えで亡くなった人（このターン）", "人"),
    ("population.births_unhoused", "住む所がなく生まれなかった人（このターン）", "人"),
]
_STATS = {key: (label, unit) for key, label, unit in [*display.SUMMARY_STATS, *_EXTRA_STATS]}
# 名前の頭が財のもの（後ろに財の名前が続く）
_GOODS_STATS = {
    "price.": ("{good}の値段（1{unit}）", "money"),
    "volume.": ("{good}の売買量", "good"),
    "production.": ("{good}の生産量", "good"),
    "use.": ("{good}の使った量（施設の原料）", "good"),
    "stock.": ("{good}の国の倉庫の在庫", "good"),
    "works.stock.": ("公共事業の資材の在庫（{good}）", "good"),
}
# 計算で作る系列（統計の表にはなく、ほかの統計から作る）。層の1人あたりの所得は
# layer.<層>.income_per_person
DERIVED = ("fiscal.revenue", "fiscal.spending", "fiscal.balance")
PER_PERSON = ".income_per_person"


def stat_label(name: str) -> tuple[str, str]:
    """統計の名前の表示名と単位（単位は display.stat_text と同じ）。知らない名前はそのまま。"""
    if name in _STATS:
        return _STATS[name]
    defs = get_game().defs
    for prefix, (template, unit) in _GOODS_STATS.items():
        good = name.removeprefix(prefix)
        if name.startswith(prefix) and good in defs.goods:
            label = template.format(good=defs.name("goods", good), unit=defs.unit_name(good))
            return label, f"good:{good}" if unit == "good" else unit
    head, _, rest = name.partition(".")
    if head == "yield" and rest.isdigit():
        return f"国債の利回り（{bucket_name(int(rest))}）", "rate"
    if head == "fiscal" and rest.startswith(("in.", "out.")):
        side, _, kind = rest.partition(".")
        return f"{'収入' if side == 'in' else '支出'}・{flow_name(side, kind)}", "money"
    if head == "spent":
        return f"使った額・{budget_name(rest)}", "money"
    if head == "infra" and rest in defs.infrastructure:
        return f"{defs.name('infrastructure', rest)}の平均の整備度", "percent"
    if head == "loans":
        return f"融資の残高（{display.message('loan_' + rest)}）", "money"
    if head == "layer":
        layer, _, field = rest.partition(".")
        if field in LAYER_FIELDS:
            suffix, unit = LAYER_FIELDS[field]
            return f"{LAYER_NAMES.get(layer, layer)}{suffix}", unit
    if head == "bank":
        number, _, field = rest.partition(".")
        if field in BANK_FIELDS:
            label, unit = BANK_FIELDS[field]
            return f"銀行（{number} 番）の{label}", unit
    if head == "fx":
        return _fx_label(rest) or (name, "")
    if head in ("experience", "experience_rank") and rest in defs.industries():
        industry = defs.name("industries", rest)
        if head == "experience":
            return f"{industry}の経験（標準の施設が全力で動いたターン数）", ""
        return f"{industry}の経験の世界の順位", " 位"
    return name, ""


# 為替の統計（fx.<種類>.<通貨>）の表示名。通貨の番号は島の番号
FX_KINDS = {
    "volume": "為替の売買量（{currency}の額）",
    "dealt": "中央銀行が買った外貨（{currency}の額。売りはマイナス）",
    "intervened": "介入と固定相場で中央銀行が買った外貨（{currency}の額。売りはマイナス）",
}


def _fx_label(rest: str) -> tuple[str, str] | None:
    """為替の統計（fx. の後ろ）の表示名と単位。レート（fx.<通貨>）は「その通貨 1 が自国通貨で
    いくらか」で、単位は fx（小数第4位まで）。"""
    kind, _, key = rest.rpartition(".")
    if not key.isdigit() or kind not in ("", *FX_KINDS):
        return None
    currency = display.currency_name(int(key))
    if not kind:
        return f"為替レート（{currency} 1 が自国通貨でいくらか）", "fx"
    return FX_KINDS[kind].format(currency=currency), ""


def flow_name(side: str, kind: str) -> str:
    """国庫の出入り（side は in か out）の仕訳の種類の表示名。"""
    return FLOW_NAMES.get((side, kind)) or get_game().defs.name("finance", kind)


def bucket_name(k: int) -> str:
    """国債の売買の区分 k（残りの期間。game.yaml の bonds.buckets）の表示名。"""
    years = [edge / TURNS_PER_YEAR for edge in get_game().config.get("bonds.buckets")]
    if k <= 0:
        return f"残り{years[0]:g}年未満"
    if k >= len(years):
        return f"残り{years[-1]:g}年以上"
    return f"残り{years[k - 1]:g}〜{years[k]:g}年"


def budget_name(item: str) -> str:
    """予算の項目（と予算の外の支出）の表示名。"""
    key = f"budget_{item}"
    text = display.message(key)
    return SPENT_NAMES.get(item, item) if text == key else text


def unit_of(unit: str) -> str:
    """API で返す単位: 財の単位（good:<財>）は、その単位の名前（前に空白を付ける）にする。"""
    if unit.startswith("good:"):
        name = get_game().defs.unit_name(unit.removeprefix("good:"))
        return f" {name}" if name else ""
    return unit


def stat_choices(island: m.Island, mine: bool) -> list[dict[str, str]]:
    """島の直近のターンの統計の名前（選んで系列を見る欄に出す）。表示名の順。"""
    last = island.stats.aggregate(turn=Max("turn"))["turn"]
    if last is None:
        return []
    names = island.stats.filter(turn=last).values_list("name", flat=True)
    rows = [{"name": n, "label": stat_label(n)[0]} for n in names if visible(n, mine)]
    rows += [{"name": n, "label": stat_label(n)[0]} for n in DERIVED]
    rows += [
        {"name": n, "label": stat_label(n)[0]}
        for n in (f"layer.{layer}{PER_PERSON}" for layer in LAYER_NAMES)
    ]
    return sorted(rows, key=lambda r: r["label"])


def visible(name: str, mine: bool) -> bool:
    """他国の島の人に見せる統計か（銀行ごとの中身は持ち主だけ）。"""
    return mine or not name.startswith("bank.")


# --- 統計の系列 -----------------------------------------------------------------------


def turn_range(island: m.Island, start: Any, end: Any) -> tuple[int, int]:
    """要求のターンの範囲（from・to の文字。どちらも省ける）を、島の統計のある範囲で決める。"""
    last = island.stats.aggregate(turn=Max("turn"))["turn"] or 0
    end_turn = _int(end, last)
    start_turn = _int(start, end_turn - DEFAULT_TURNS + 1)
    start_turn = max(start_turn, end_turn - MAX_TURNS + 1, 0)
    return start_turn, end_turn


def _int(value: Any, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def series(island: m.Island, names: list[str], start: int, end: int, mine: bool) -> dict[str, Any]:
    """統計の系列。names は名前か、名前の頭に * を付けたもの（例: price.*）。

    返す形: turns（ターンの並び）、series（名前 → turns と同じ長さの値の並び。ない所は None）、
    labels と units（名前 → 表示名・単位）、limits（1回に選べる名前の数と返せる系列の数）、
    asked（頼んだ名前の数）、found（見つかった系列の数。返せなかった分も含む）、dropped（系列の
    数の上限で返せなかった系列の名前。名前の頭で選んだ分を含む）、not_read（選べる名前の数を
    超えて読まなかった名前）。
    他国の島では銀行ごとの統計を返さない。
    """
    asked = [n for n in names if n]
    names = asked[:MAX_NAMES]
    exact = {n for n in names if not n.endswith("*")}
    prefixes = [n[:-1] for n in names if n.endswith("*")]
    wanted = set(exact)
    if any(n in DERIVED for n in exact) or any(p.startswith("fiscal") for p in prefixes):
        prefixes += ["fiscal.in.", "fiscal.out."]  # 財政の収支は国庫の出入りから作る
    for n in exact:
        if n.endswith(PER_PERSON):  # 1人あたりの所得は、層の所得と人数から作る
            layer = n.removesuffix(PER_PERSON)
            wanted |= {f"{layer}.income", f"{layer}.persons"}
    query = Q(name__in=wanted)
    for p in prefixes:
        query |= Q(name__startswith=p)
    rows = island.stats.filter(query, turn__gte=start, turn__lte=end)
    if not mine:
        rows = rows.exclude(name__startswith="bank.")
    table: dict[str, dict[int, float]] = defaultdict(dict)
    for turn, name, value in rows.values_list("turn", "name", "value"):
        table[name][turn] = value
    _derive(table)
    found = [n for n in names if not n.endswith("*") and n in table and visible(n, mine)]
    for p in [n[:-1] for n in names if n.endswith("*")]:
        found += sorted(n for n in table if n.startswith(p) and n not in found)
    # 上限で返せなかった分は、黙って切らずに名前を返す（Codex の試し遊び: 80 系列で後ろが落ち、
    # 値がないのか切られたのか分からなかった）。選べる数を超えた名前も同じ
    dropped = found[MAX_SERIES:]
    found = found[:MAX_SERIES]
    turns = sorted({t for n in found for t in table[n]})
    labels = {n: stat_label(n) for n in found}
    return {
        "island": island.id,
        "from": start,
        "to": end,
        "turns": turns,
        "series": {n: [table[n].get(t) for t in turns] for n in found},
        "labels": {n: label for n, (label, _) in labels.items()},
        "units": {n: unit_of(unit) for n, (_, unit) in labels.items()},
        "limits": {"names": MAX_NAMES, "series": MAX_SERIES},
        "asked": len(asked),
        "found": len(found) + len(dropped),
        "dropped": dropped,
        "not_read": asked[MAX_NAMES:],
    }


def _derive(table: dict[str, dict[int, float]]) -> None:
    """計算で作る系列（財政の収入・支出・収支と、層の1人あたりの所得）を table に足す。"""
    for name in [n for n in table if n.startswith("layer.") and n.endswith(".income")]:
        persons = table.get(name.removesuffix(".income") + ".persons", {})
        table[name.removesuffix(".income") + PER_PERSON] = {
            t: v / persons[t] for t, v in table[name].items() if persons.get(t)
        }
    revenue: dict[int, float] = defaultdict(float)
    spending: dict[int, float] = defaultdict(float)
    for name, values in table.items():
        side, _, kind = name.removeprefix("fiscal.").partition(".")
        if not name.startswith("fiscal.") or side not in FINANCING or kind in FINANCING[side]:
            continue
        target = revenue if side == "in" else spending
        for turn, value in values.items():
            target[turn] += value
    turns = set(revenue) | set(spending)
    if turns:
        table["fiscal.revenue"] = {t: revenue.get(t, 0.0) for t in turns}
        table["fiscal.spending"] = {t: spending.get(t, 0.0) for t in turns}
        table["fiscal.balance"] = {t: revenue.get(t, 0.0) - spending.get(t, 0.0) for t in turns}


def economy_charts() -> list[dict[str, Any]]:
    """経済の画面のグラフ: 見出しと、描く統計の名前（末尾の * は、その頭の統計すべて）。pie は
    円グラフ（選んだ1つのターンの内訳）。"""
    defs = get_game().defs
    return [
        {"title": "GDP（1ターン）", "names": ["gdp"]},
        {"title": "物価上昇率（年率）", "names": ["inflation"]},
        {"title": "失業率", "names": ["unemployment", "engineers.unemployment"]},
        {"title": "政策金利と国債の利回り", "names": ["policy_rate", "yield.*"]},
        {"title": "財政の収支（1ターン）", "names": list(DERIVED)},
        {
            "title": "層ごとの1人あたりの所得（1ターン）",
            "names": [f"layer.{layer}{PER_PERSON}" for layer in LAYER_NAMES],
        },
        {"title": "格差（1人あたりの所得の比）", "names": ["inequality"]},
        {"title": "教育水準", "names": ["education.level", "education.schooled", "education.goal"]},
        {"title": "技術者", "names": ["engineers", "engineers.employed"]},
        {
            "title": "インフラの平均の整備度（陸のマス）",
            "names": [f"infra.{kind}" for kind in defs.infrastructure],
        },
        {"title": "電力の不足率", "names": ["power.shortage"]},
        {"title": "人口と就業者", "names": ["population", "labor", "employed", "services.workers"]},
        {
            "title": "人口の内訳（階級ごと）",
            "names": [f"layer.{layer}.persons" for layer in LAYER_NAMES],
            "pie": True,
        },
        {
            "title": "生まれた人と亡くなった人（1ターン）",
            "names": ["population.births", "population.deaths", "population.starved"],
        },
        {
            "title": "国をまたぐ移民（1ターン）",
            "names": ["migration.left", "migration.arrived", "migration.returned"],
        },
        {
            "title": "国外に住む出身者と、ほかの国から来た人",
            "names": ["migration.abroad", "migration.foreign_born"],
        },
    ]


# 経済の画面の上に並べる、直近のターンの値
ECONOMY_TILES = [
    "gdp",
    "inflation",
    "unemployment",
    "policy_rate",
    "fiscal.balance",
    "education.level",
    "engineers",
    "power.shortage",
]


def default_names() -> list[str]:
    """名前を決めずに統計の系列の API を呼んだときに返す名前（経済の画面のグラフのもの）。"""
    names: list[str] = []
    for chart in economy_charts():
        names += [n for n in chart["names"] if n not in names]
    return names


def stat_rows(
    stats: dict[str, float], names: list[str], currency: str = ""
) -> list[dict[str, str]]:
    """統計の表の行（直近のターンの値の文）。stats にない名前は出さない。"""
    out = []
    for name in names:
        if name not in stats:
            continue
        label, unit = stat_label(name)
        text = display.stat_text(name, stats[name], unit, currency)
        out.append({"key": name, "label": label, "text": text})
    return out


def stats_part(stats: dict[str, float], names: list[str]) -> dict[str, Any]:
    """統計のうち names にあるものと、その表示名と単位（API の形。画面の stat_rows と同じ統計）。"""
    shown = [n for n in names if n in stats]
    return {
        "stats": {n: stats[n] for n in shown},
        "labels": {n: stat_label(n)[0] for n in shown},
        "units": {n: unit_of(stat_label(n)[1]) for n in shown},
    }


def with_fiscal(stats: dict[str, float]) -> dict[str, float]:
    """直近のターンの統計に、財政の収入・支出・収支を足したもの。"""
    table = {name: {0: value} for name, value in stats.items()}
    _derive(table)
    # 誰もいない層の1人あたりの所得は、値のない系列になる
    return {name: values[0] for name, values in table.items() if 0 in values}


# --- 市場 -----------------------------------------------------------------------------

# 原料を買えずに止まった施設の状態（status の「種類:財」の種類）
SHORT_STATUSES = ("input", "input_price", "funds", "public_price")


def market_turn(island: m.Island) -> int | None:
    return MarketRecord.objects.filter(island=island).aggregate(turn=Max("turn"))["turn"]


def market_table(island: m.Island, stats: dict[str, float], currency: str) -> list[dict]:
    """財ごとの直近のターンの値段・前のターンからの変わり方・売買の量・買いたかった量と
    売りたかった量（注文）・生産量・国の倉庫の在庫。"""
    defs = get_game().defs
    turn = market_turn(island)
    now, before = {}, {}
    if turn is not None:
        records = MarketRecord.objects.filter(island=island, turn__in=[turn, turn - 1])
        for r in records:
            (now if r.turn == turn else before)[r.good] = r
    rows = []
    for good in defs.goods:
        record = now.get(good)
        made = stats.get(f"production.{good}", 0)
        stock = stats.get(f"stock.{good}", 0)
        if record is None and not made and not stock:
            continue
        row: dict[str, Any] = {
            "good": good,
            "name": defs.name("goods", good),
            "unit": defs.unit_name(good),
            "production": round(made, 1),
            "stock": round(stock, 1),
        }
        if record is not None:
            row.update(
                price=display.money_text(record.price, currency),
                volume=display.amount(record.volume),
                demand=display.amount(record.demand),
                supply=display.amount(record.supply),
                unmet=display.amount(max(0, record.demand - record.volume)),
            )
            previous = before.get(good)
            if previous is not None and previous.price:
                change = round((record.price / previous.price - 1) * 100, 1)
                row["change"] = f"{change:+.1f}%" if change else "0.0%"
        rows.append(row)
    return rows


def market_series(
    island: m.Island, good: str, start: int, end: int, mine: bool, currency: str = ""
) -> dict[str, Any]:
    """財 good の値段と量の推移と、直近のターンの市場。持ち主には約定と、買えずに止まった施設も。

    量は表示の単位（1000 分の 1 単位を直したもの）。
    """
    defs = get_game().defs
    records = list(
        MarketRecord.objects.filter(
            island=island, good=good, turn__gte=start, turn__lte=end
        ).order_by("turn")
    )
    data: dict[str, Any] = {
        "island": island.id,
        "good": good,
        "name": defs.name("goods", good),
        "unit": defs.unit_name(good),
        "currency": currency,
        "mine": mine,
        "from": start,
        "to": end,
        "turns": [r.turn for r in records],
        "price": [r.price for r in records],
        "volume": [from_milli(r.volume) for r in records],
        "demand": [from_milli(r.demand) for r in records],
        "supply": [from_milli(r.supply) for r in records],
    }
    latest = MarketRecord.objects.filter(island=island, good=good).order_by("-turn").first()
    if latest is not None:
        data["latest"] = {
            "turn": latest.turn,
            "price": latest.price,
            "volume": from_milli(latest.volume),
            "demand": from_milli(latest.demand),
            "supply": from_milli(latest.supply),
        }
    if mine:
        state = display.island_for_naming(island)
        turn = latest.turn if latest is not None else None
        data["trades"] = trades(island, state, good, turn) if turn is not None else []
        data["short"] = short_facilities(island, state, good)
    return data


def trades(island: m.Island, state: IslandState, good: str, turn: int) -> list[dict]:
    """ターン turn の財 good の約定を、買い手と売り手の組ごとにまとめたもの（代金の多い順）。"""
    pairs: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for buyer, seller, qty, amount in Trade.objects.filter(
        island=island, turn=turn, good=good
    ).values_list("buyer", "seller", "quantity", "amount"):
        pair = pairs[(buyer, seller)]
        pair[0] += qty
        pair[1] += amount
    names = entity_names(island, state, {e for pair in pairs for e in pair})
    rows = [
        {
            "buyer": buyer,
            "buyer_name": names[buyer],
            "seller": seller,
            "seller_name": names[seller],
            "quantity": from_milli(qty),
            "amount": amount,
            "price": round(amount / from_milli(qty)) if qty else None,
        }
        for (buyer, seller), (qty, amount) in pairs.items()
    ]
    return sorted(rows, key=lambda r: (-r["amount"], r["buyer"], r["seller"]))


# 約定の記録の主体の名前（施設・家計・銀行のほか）
ENTITY_NAMES = {
    "gov": "政府",
    "works": "公共事業",
    "warehouse": "国の倉庫",
    "cb": "中央銀行",
}


def entity_names(island: m.Island, state: IslandState, entities: set[str]) -> dict[str, str]:
    """約定の記録の主体（fac:<番号>・hh:<層>・bank:<番号>・gov など）の表示名。"""
    game = get_game()
    numbers = {int(e.split(":")[1]) for e in entities if e.startswith(("fac:", "bank:"))}
    rows = {f.number: f for f in island.facilities.filter(number__in=numbers)}
    out = {}
    for e in entities:
        head, _, rest = e.partition(":")
        if head in ("fac", "bank") and rest.isdigit() and int(rest) in rows:
            f = facility_from_row(rows[int(rest)])
            out[e] = facility_name(game.defs, game.config, state, f)
        elif head == "hh":
            out[e] = f"家計（{LAYER_NAMES.get(rest, rest)}）"
        else:
            out[e] = ENTITY_NAMES.get(e, e)
    return out


def short_facilities(
    island: m.Island, state: IslandState, good: str | None = None
) -> list[dict[str, Any]]:
    """原料が手に入らず（品切れ・割高・資金不足で買えず）止まった施設。good を決めればその財だけ。

    状態（status）は直近のターンの終わりのもの。
    """
    game = get_game()
    query = Q()
    for kind in SHORT_STATUSES:
        query |= Q(status=f"{kind}:{good}") if good else Q(status__startswith=f"{kind}:")
    out = []
    for row in island.facilities.filter(query).order_by("number"):
        f = facility_from_row(row)
        wanted = row.status.partition(":")[2]
        out.append(
            {
                "number": row.number,
                "name": facility_name(game.defs, game.config, state, f),
                "kind_name": game.defs.name("facilities", row.kind),
                "x": row.x,
                "y": row.y,
                "good": wanted,
                "good_name": game.defs.name("goods", wanted),
                "status_text": display.status_text(f),
            }
        )
    return out


# --- 施設 -----------------------------------------------------------------------------


def facility_rows(island: m.Island, currency: str) -> dict[str, Any]:
    """自国の施設（町を除く。民営と工事中を含む）の一覧: 状態・人手・直近のターンの損益・直近の
    数ターンの損益の合計・預金・借入。帳簿の turn は直近の決算のターン。"""
    game = get_game()
    defs, config = game.defs, game.config
    state = display.island_for_naming(island)
    housing = fac.housing_kind(config)
    last = FacilityBook.objects.filter(island=island).aggregate(turn=Max("turn"))["turn"]
    recent: dict[int, list[FacilityBook]] = defaultdict(list)
    if last is not None:
        for b in FacilityBook.objects.filter(
            island=island, turn__gt=last - display.BOOK_TURNS
        ).order_by("-turn"):
            recent[b.facility].append(b)
    rows = []
    total = 0
    shops: list[dict[str, Any]] = []
    for row in island.facilities.exclude(kind=housing).order_by("number"):
        f = facility_from_row(row)
        fdef = defs.facilities.get(f.kind, {})
        books_of = recent.get(f.id, [])
        latest = books_of[0] if books_of and books_of[0].turn == last else None
        item: dict[str, Any] = {
            "number": f.id,
            "name": facility_name(defs, config, state, f) if fdef else f.kind,
            "kind_name": defs.name("facilities", f.kind),
            "owner_name": display.message(f"owner_{f.owner}"),
            "accounting_name": display.message(f"accounting_{f.accounting}"),
            "x": f.x,
            "y": f.y,
            "on_map": f.x >= 0 and f.y >= 0,
            "built": f.built,
            "status_text": display.status_text(f),
            "workers": f.workers,
            "workers_needed": fdef.get("workers", 0)
            if f.built
            else fdef.get("construction", {}).get("labor", 0),
            "operating_rate": round(f.operating_rate * 100 / fac.FULL) if f.built else None,
            "has_books": bool(books_of),
        }
        item.update(display.engineers_info(f, fdef) if fdef else {})
        if f.built and fac.FIELDS in f.economy:  # 畑を作り替えている途中の内訳
            item["transition"] = display.crop_transition(f)["text"]
        if services.is_services(config, f):  # 雇う人数の目安と、店の場所ごとの人数（回4）
            item["workers_needed"] = f.economy.get(services.SEATS, 0)
            shops = shop_rows(island, state, f)
        if latest is not None:
            profit = latest.items.get(books.PROFIT, 0)
            total += profit
            item["profit"] = display.money_text(profit, currency)
            item["loss"] = profit < 0
            if entity_of(config, f) != ledger.GOV:  # 国庫直轄の施設は自分の預金を持たない
                item["deposit"] = display.money_text(latest.deposit, currency)
            if latest.debt:
                item["debt"] = display.money_text(latest.debt, currency)
        if books_of:
            profits = sum(b.items.get(books.PROFIT, 0) for b in books_of)
            item["profit_total"] = display.money_text(profits, currency)
        rows.append(item)
    return {
        "turn": last,
        "book_turns": display.BOOK_TURNS,
        "rows": rows,
        "profit": display.money_text(total, currency),
        "shops": shops,
    }


# 産業の分類（定義の industry）を持たない施設の、産業ごとの働く人の円グラフでの分け方
SECTOR_NAMES = {
    "government_office": "公務",
    "police": "公務",
    "school": "教育",
    "university": "教育",
    "bank": "金融",
    "transport": "運輸",
    "port": "運輸",
    "shipping": "運輸",
    "trading": "商社",
    "services": "商業・サービス業",
}


def industry_workers(island: m.Island) -> list[dict[str, Any]]:
    """産業ごとの、いま施設で働いている人（技術者を含む。町と工事中の施設を除く）。
    施設の画面の円グラフに使う（誰にでも。国全体の数だけ）。多い順。"""
    game = get_game()
    defs, config = game.defs, game.config
    housing = fac.housing_kind(config)
    totals: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    for row in island.facilities.exclude(kind=housing).filter(built=True):
        f = facility_from_row(row)
        fdef = defs.facilities.get(f.kind, {})
        workers = f.workers  # 技術者を含む
        if workers <= 0:
            continue
        industry = fdef.get("industry")
        if industry:
            key, name = industry, defs.name("industries", industry)
        else:
            name = SECTOR_NAMES.get(f.kind) or defs.name("facilities", f.kind)
            key = f"sector:{name}"
        totals[key] += workers
        names[key] = name
    rows = [{"key": k, "label": names[k], "value": v} for k, v in totals.items()]
    return sorted(rows, key=lambda r: -r["value"])


def town_rows(island: m.Island) -> list[dict[str, Any]]:
    """町（住宅地）ごとの働く人。

    住民・働ける人・仕事のある人・自給の農業で食べている人・失業している人と失業率
    （統計と同じ数え方。村で自給の農業をしている人は失業に数えない）、商業・サービス業の
    店で働く人（住んでいる町ごと）、買いに行く店の場所（中心）と、そこが中心なら店が
    割り振った人数と雇えた人数（人の多い順。誰にでも）。Codex の試し遊び（ターン 250〜300）
    で、町ごとの失業と通勤がマスを1つずつ読まないと分からなかった。"""
    game = get_game()
    defs, config = game.defs, game.config
    state = display.island_for_naming(island)
    for row in island.tiles.all():
        state.tiles[row.y * state.size + row.x] = Tile(
            row.x,
            row.y,
            terrain=row.terrain,
            population=row.population,
            zone=row.zone,
            infra=row.infra,
            pollution=row.pollution,
            ext=row.ext or {},
        )
    housing = fac.housing_kind(config)
    levels = {
        (f.x, f.y): f.ext.get("level") for f in state.facilities if f.kind == housing and f.built
    }
    staff = services.staff_by_tile(state, config)
    centers = services.centers(state, config)
    shop = services.facility_of(state, config)
    quotas = shop.economy.get(services.QUOTAS, {}) if shop is not None else {}
    out = []
    for tile in state.tiles:
        if tile is None or tile.population <= 0:
            continue
        pos = (tile.x, tile.y)
        labor = fac.labor_force(defs, state, tile)
        jobless = tile_jobless(defs, state, tile)
        center = centers.get(pos, pos)
        quota = quotas.get(f"{center[0]},{center[1]}")
        level = levels.get(pos)
        out.append(
            {
                "x": tile.x,
                "y": tile.y,
                "place": place_of(state, tile.x, tile.y, config),
                "level": defs.name("settlements", level) if level else "",
                "population": tile.population,
                "labor": labor,
                "employed": min(labor, tile.ext.get("employed", 0)),
                "subsistence": subsistence_persons(defs, state, tile),
                "jobless": jobless,
                "unemployment": jobless / labor if labor else 0.0,
                "unemployment_text": f"{jobless * 100 / labor:.1f}%" if labor else "",
                "services_staff": staff.get(pos, 0),
                "center": {"x": center[0], "y": center[1]},
                "center_place": place_of(state, center[0], center[1], config),
                "shop_planned": quota[0] if quota and center == pos else None,
                "shop_hired": quota[1] if quota and center == pos else None,
            }
        )
    return sorted(out, key=lambda r: (-r["population"], r["y"], r["x"]))


def shop_rows(island: m.Island, state: IslandState, f: Any) -> list[dict[str, Any]]:
    """商業・サービス業の店の場所（集落）ごとの、割り振った人数と雇えた人数（人の多い順）。"""
    game = get_game()
    defs, config = game.defs, game.config
    housing = fac.housing_kind(config)
    levels = {
        (r.x, r.y): (r.ext or {}).get("level")
        for r in island.facilities.filter(kind=housing).only("x", "y", "ext")
    }
    out = []
    for key, (planned, hired) in f.economy.get(services.QUOTAS, {}).items():
        x, y = (int(v) for v in key.split(","))
        level = levels.get((x, y))
        out.append(
            {
                "x": x,
                "y": y,
                "place": place_of(state, x, y, config),
                "level": defs.name("settlements", level) if level else "",
                "planned": planned,
                "hired": hired,
            }
        )
    return sorted(out, key=lambda r: (-r["planned"], r["y"], r["x"]))


def facility_book(
    island: m.Island, number: int, start: int, end: int, currency: str = ""
) -> dict[str, Any] | None:
    """施設の帳簿の推移（ターンごとの収支の項目と預金・借入・在庫の値打ち）。帳簿も施設もなければ
    None。取り壊した施設も、帳簿が残っていれば返す。"""
    game = get_game()
    row = island.facilities.filter(number=number).first()
    records = list(
        FacilityBook.objects.filter(
            island=island, facility=number, turn__gte=start, turn__lte=end
        ).order_by("turn")
    )
    if row is None and not records:
        return None
    name = f"施設（{number} 番）"
    kind_name = ""
    if row is not None:
        f = facility_from_row(row)
        if f.kind in game.defs.facilities:
            name = facility_name(game.defs, game.config, display.island_for_naming(island), f)
        kind_name = game.defs.name("facilities", f.kind)
    used = [key for key, _, _ in display.BOOK_ITEMS if any(r.items.get(key) for r in records)]
    return {
        "island": island.id,
        "number": number,
        "name": name,
        "kind_name": kind_name,
        "currency": currency,
        "from": start,
        "to": end,
        "turns": [r.turn for r in records],
        "items": {key: [r.items.get(key, 0) for r in records] for key in used},
        "labels": {key: label for key, label, _ in display.BOOK_ITEMS if key in used},
        # 収入なら 1、支出なら -1（表の符号。帳簿の額は支出も正の数）
        "signs": {key: sign for key, _, sign in display.BOOK_ITEMS if key in used},
        "deposit": [r.deposit for r in records],
        "debt": [r.debt for r in records],
        "inventory": [r.inventory for r in records],
    }


# --- 財政 -----------------------------------------------------------------------------


def fiscal_view(island: m.Island, stats: dict[str, float], currency: str) -> dict[str, Any]:
    """直近のターンの国庫の収入と支出（仕訳の種類ごと）、借入と返済、予算の項目ごとの予算と
    使った額、税率、国庫と国債の残高。"""
    config = get_game().config

    def flows(side: str) -> list[dict[str, Any]]:
        rows = [
            (name.removeprefix(f"fiscal.{side}."), value)
            for name, value in stats.items()
            if name.startswith(f"fiscal.{side}.")
        ]
        rows.sort(key=lambda r: -r[1])
        return [
            {"kind": kind, "name": flow_name(side, kind), "amount": value} for kind, value in rows
        ]

    def money(value: float) -> str:
        return display.money_text(round(value), currency)

    revenue = [r for r in flows("in") if r["kind"] not in FINANCING["in"]]
    spending = [r for r in flows("out") if r["kind"] not in FINANCING["out"]]
    financing = [{**r, "sign": 1} for r in flows("in") if r["kind"] in FINANCING["in"]] + [
        {**r, "sign": -1} for r in flows("out") if r["kind"] in FINANCING["out"]
    ]
    total_in = sum(r["amount"] for r in revenue)
    total_out = sum(r["amount"] for r in spending)
    policy = island.policy or {}
    budget = policy.get("budget", {})
    items = config.get("fiscal.budget_items")
    budget_rows = [
        {
            "item": item,
            "name": budget_name(item),
            "annual": money(budget.get(item, 0)),
            "per_turn": money(budget.get(item, 0) // TURNS_PER_YEAR),
            "spent": money(stats.get(f"spent.{item}", 0)),
        }
        for item in items
    ]
    other_spent = [
        {
            "item": name.removeprefix("spent."),
            "name": budget_name(name.removeprefix("spent.")),
            "spent": money(value),
        }
        for name, value in sorted(stats.items())
        if name.startswith("spent.") and name.removeprefix("spent.") not in items
    ]
    return {
        "revenue": [{**r, "text": money(r["amount"])} for r in revenue],
        "spending": [{**r, "text": money(r["amount"])} for r in spending],
        "financing": [{**r, "text": money(r["sign"] * r["amount"])} for r in financing],
        "total_in": money(total_in),
        "total_out": money(total_out),
        "balance": money(total_in - total_out),
        "deficit": total_in < total_out,
        "budget": budget_rows,
        "other_spent": other_spent,
        "taxes": stat_rows_policy(policy, currency),
        "position": stat_rows(stats, ["treasury", "debt", "debt_ratio"], currency),
    }


def stat_rows_policy(policy: dict[str, Any], currency: str) -> list[dict[str, str]]:
    """税率・最低賃金・失業手当の表の行。"""
    rows = []
    for key in ("income_tax", "corporate_tax", "consumption_tax"):
        rows.append(
            {"label": display.message(f"policy_{key}"), "text": f"{policy.get(key, 0) / 100:g}%"}
        )
    wage = policy.get("minimum_wage", 0)
    rows.append(
        {
            "label": display.message("policy_minimum_wage"),
            "text": display.money_text(wage, currency) if wage else "決めていない",
        }
    )
    benefit = policy.get("unemployment_benefit", 0) / 100
    rows.append(
        {
            "label": display.message("policy_unemployment_benefit"),
            "text": f"平均賃金の {benefit:g}%" if benefit else "なし",
        }
    )
    return rows


def bond_rows(island: m.Island, currency: str, mine: bool) -> list[dict[str, Any]]:
    """国債の銘柄の一覧（満期の近い順）: 期間・発行と満期のターン・利率・発行額・残高・中央銀行の
    持ち分。持ち主には、銀行ごとの持ち分も。"""
    defs = get_game().defs
    state = display.island_for_naming(island)
    load_money({island.id: state}, Account.objects.filter(island=island))
    state.bonds = [bond_from_row(b) for b in island.bonds.order_by("number")]
    terms = {spec["turns"]: name for name, spec in defs.bond_terms().items()}
    banks = {  # 銀行の名前と位置（同じ名前の国営銀行が2つあっても見分けられるように）
        f"bank:{f.number}": (
            facility_name(defs, get_game().config, state, facility_from_row(f)),
            f"({f.x}, {f.y})",
        )
        for f in island.facilities.filter(kind=get_game().config.get("banking.facility"))
    }
    rows = []
    for b in sorted(state.bonds, key=lambda b: (b.maturity, b.id)):
        held = debt.holders(state, b)
        row = {
            "number": b.id,
            "term": defs.name("finance", terms[b.term]) if b.term in terms else f"{b.term} ターン",
            "issued": b.issued,
            "maturity": b.maturity,
            "rate": display.rate_text(b.rate),
            "face": display.money_text(b.face, currency),
            "outstanding": display.money_text(debt.outstanding(state, b), currency),
            "central_bank": display.money_text(held.get("cb", 0), currency),
            # 外国の持ち分（フェーズ3-3。非居住者の持ち手 nr:<島>:<主体> の合計）
            "foreign": display.money_text(
                sum(v for h, v in held.items() if abroad.parse_holder(h) is not None), currency
            ),
        }
        if mine:
            row["holders"] = [
                {
                    "name": _holder_name(h, banks),
                    "where": banks.get(h, ("", ""))[1],
                    "amount": display.money_text(v, currency),
                }
                for h, v in held.items()
                if v
            ]
        rows.append(row)
    return rows


def _holder_name(holder: str, banks: dict[str, tuple[str, str]]) -> str:
    """国債の持ち手の表示名（自国の銀行・中央銀行と、外国の政府・中央銀行・銀行）。"""
    foreign = abroad.parse_holder(holder)
    if foreign is not None:
        island_id, entity = foreign
        who = ENTITY_NAMES.get(entity) or ("銀行" if entity.startswith("bank:") else entity)
        return f"{display.island_name(island_id)}の{who}"
    return banks.get(holder, (ENTITY_NAMES.get(holder, holder), ""))[0]


# --- 金融 -----------------------------------------------------------------------------

# 金融の画面と API に出す統計（だれにでも出す国全体の統計）
FINANCE_STATS = [
    "policy_rate",
    "interbank_rate",
    "deposit_rate",
    "money.deposits",
    "money.central_bank",
    "cb_lending",
    "bonds.central_bank",
    "bonds.cb_bought",
    "bonds.cb_sold",
    "bonds.underwritten",
    "loans",
    "loans.arrears",
    "loans.write_off",
    "banks",
]


def finance_stats(stats: dict[str, float]) -> list[str]:
    """金融の統計の名前（国債の利回りの区分と使い道ごとの融資を含む。stats にあるものだけ）。"""
    names = FINANCE_STATS[:3]
    names += sorted(n for n in stats if n.startswith("yield."))
    names += FINANCE_STATS[3:]
    names += sorted(n for n in stats if n.startswith("loans.") and n not in FINANCE_STATS)
    return [n for n in names if n in stats]


def central_bank_view(island: m.Island) -> dict[str, Any]:
    """中央銀行の今の方針（政策金利・独立・国債の売買の額）。"""
    p = display.finance_policy_view(island)
    return {
        "policy_rate": p["policy_rate"],
        "independent": p["cb_independent"],
        "bond_trade": p["cb_bond_trade"],
        "bond_trade_text": p["cb_bond_trade_text"],
        "unlock_turn": p["cb_unlock_turn"],
        "lock_turns": p["cb_lock_turns"],
    }


def bank_rows(island: m.Island, currency: str) -> list[dict[str, Any]]:
    """島の銀行（破綻したものを含む）ごとの帳簿（直近の統計）と、出せる命令（緊急の貸し出し・
    破綻処理）。持ち主にだけ出す。"""
    game = get_game()
    state = display.island_for_naming(island)
    out = []
    for row in island.facilities.filter(kind=game.config.get("banking.facility")).order_by(
        "number"
    ):
        f = facility_from_row(row)
        view = display.bank_view(row, f, state, currency)
        item: dict[str, Any] = {
            "number": f.id,
            "name": facility_name(game.defs, game.config, state, f),
            "owner_name": display.message(f"owner_{f.owner}"),
            "x": f.x,
            "y": f.y,
            "built": f.built,
            **view,
        }
        if f.built and display.order_ok(
            state, f, "emergency_loan", {"x": f.x, "y": f.y, "amount": 0}
        ):
            item["emergency_loan"] = True
        resolve = {"x": f.x, "y": f.y, "method": RESOLUTION_METHODS[0]}
        if display.order_ok(state, f, "resolve_bank", resolve):
            item["resolve_methods"] = {
                method: display.message(f"resolution_{method}") for method in RESOLUTION_METHODS
            }
        out.append(item)
    return out


def finance_data(island: m.Island, mine: bool, currency: str) -> dict[str, Any]:
    """金融の API の中身。だれにでも国全体の統計と中央銀行の方針を、持ち主には銀行ごとの帳簿と
    金融の政策も返す。"""
    turn, stats = display.latest_stats(island)
    names = finance_stats(stats)
    data: dict[str, Any] = {
        "island": island.id,
        "mine": mine,
        "turn": turn,
        "currency": currency,
        "stats": {n: stats[n] for n in names},
        "labels": {n: stat_label(n)[0] for n in names},
        "units": {n: unit_of(stat_label(n)[1]) for n in names},
        "central_bank": central_bank_view(island),
    }
    if mine:
        data["banks"] = bank_rows(island, currency)
        data["policy"] = display.finance_policy_view(island)
    return data
