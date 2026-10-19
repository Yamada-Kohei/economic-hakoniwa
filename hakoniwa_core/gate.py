"""フェーズ2・3の関門の目安を、一括シミュレーションの記録で確かめる。

毎ターンの終わりに Recorder.record で島ごとの統計を記録し、最後に Recorder.failures で目安から
外れたところを並べます（設計書「関門の確かめ方」の表）。帳簿の確かめは記録の段が毎ターン行い、
島をまたぐお金と運ぶ途中の荷の突き合わせは trade_check の段が毎ターン行い、合わなければその場で
止まります。

フェーズ3-1 から、為替の年の動き（FX_YEAR ターン前のレートからの変わり方の絶対値の平均）と、
石炭の出ない島の失業率の平均（貿易で機械を買えるようになって下がるかを 2-3 と比べる）も見ます。

フェーズ3-2 から、世界全体で、どの財も目安を見始めるターンからどこかの島で作られ、売り買いされて
いるかを見ます（設計書の 3-2 の関門「新しい品目がどれもどこかで作られ売買される」）。財ごとの、
作った島と売り買いのあった島の数の平均は Recorder.goods で出します。

フェーズ3-3 から、得意産業が島ごとに分かれるか（最後のターンの称号＝各産業の経験の累積の1位を、1つの
島が TITLE_SHARE より多く持たない）と、外国の資産（外貨準備と外国の国債。政府・中央銀行・市中銀行）
が限りなく増え続けないか（年の GDP に対する割合が RESERVE_CAP を超えない。最後の RESERVE_WINDOW
ターンで、割合が RESERVE_RISE より多く増えない）を見ます。

バランス調整の回2（測り方）から、値段の比をターンと結び付けて記録し、目安を見る期間（start から
end まで）の値段だけで判定します（Codex のレビュー B02。前は売り買いのあったターンの末尾の N 個を
切り出していたので、期間の外の値段が混ざることがあった）。目安から外れたところには種類（KINDS）を
付け、場面ごとにどの種類を関門に数えるかを選べるようにしました（hakoniwa_core/measure.py）。
最初の1年の縮み（EARLY_TURN ターン目の雇用と実質の生産の、1ターン目との比）と、貿易が1年続けて
途絶えた島も見ます。

目安はゲームのルールではなく確かめの基準なので、game.yaml ではなくこのファイルに置いています。
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import arrears, companies, debt, experience, is_stopped, retail
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.facilities import located
from hakoniwa_core.state import OWNER_PRIVATE, IslandState, WorldState
from hakoniwa_core.units import BASIS

START = 120  # 目安を見始めるターン
# 売り買いのあった財の値段 ÷ 初めの値段 を、物価指数の伸びで割ったもの（ほかの財と比べた値段。
# 物価全体の上がり方は物価上昇率の目安で見る。回5）
PRICE_RANGE = (0.25, 4.0)
INFLATION_RANGE = (-0.05, 0.15)  # 物価上昇率（年率）
CPI_WINDOW = 100  # 物価指数の振れ幅を見るターン数（最後から）
CPI_SPREAD = 0.15  # 物価指数の標準偏差 ÷ 平均の上限
UNEMPLOYMENT_RANGE = (0.02, 0.25)
RATE_RANGE = (0.0, 0.20)  # 政策金利と国債の利回り
POPULATION_FLOOR = 0.5  # 初めの人口に対する下限（フェーズ1と同じ）
POPULATION_CAP = 15  # 初めの人口に対する上限
POPULATION_JUMP = 0.05  # 1ターンの人口の変化の上限
GDP_GAP = 0.001  # 生産の側と支出の側の差
FIRMS_WINDOW = 100  # 民営工場の数を見るターン数（最後から。市場経済の島）
FIRMS_SPREAD = 0.5  # 民営工場の数の、その間の平均からの振れ幅の上限
CASCADE_TURNS = 24  # 同じ島で銀行の破綻がこのターン数のうちに続いたら連鎖と見る
FX_YEAR = TURNS_PER_YEAR  # 為替の年の動きを見るターン数
FX_MOVE = 0.30  # 為替の年の動きの平均の上限（島ごと、相手の通貨ごと）
NO_COAL = "coal"  # この資源の出ない島の失業率を別に出す
# 働く人のうちサービス（商業・金融・運輸・商社・公務・教育・治安）で働く割合（回7。W-23）の目安。
# 1人あたりの実質の GDP が household.base_income の 1 倍の島で SERVICES_POOR、RICH_INCOME 倍
# 以上の島で SERVICES_RICH、その間は倍率の対数で結ぶ（現実の目安: 貧しい国は3〜4割、豊かな国は
# 6〜7割）
SERVICES_POOR = (0.3, 0.4)
SERVICES_RICH = (0.6, 0.7)
RICH_INCOME = 8.0
SERVICE_CATEGORIES = ("commerce", "finance", "transport", "trade", "admin", "education", "security")
TITLE_SHARE = 1 / 3  # 1つの島が持つ称号（産業ごとの経験の1位）の、称号のある産業に対する割合の上限
RESERVE_CAP = 2.0  # 外国の資産 ÷ 年の GDP の上限
RESERVE_WINDOW = 120  # 外国の資産の増え方を見るターン数（最後から）
RESERVE_RISE = 0.5  # その間に外国の資産 ÷ 年の GDP が増えてよい幅
EARLY_TURN = TURNS_PER_YEAR  # 最初の1年の縮みを見るターン（1ターン目との比。回2）
TRADE_GAP = TURNS_PER_YEAR  # 貿易（輸出と輸入）がこれだけ続けて 0 なら途絶えた（回2。Codex B03）

# 目安から外れたところの種類（failures の kinds で選ぶ。場面ごとの関門は hakoniwa_core/measure.py）
KINDS = (
    "goods_made",  # 世界: どの島でも作られていない財
    "goods_traded",  # 世界: どの島でも売り買いされていない財
    "titles",  # 世界: 称号が1つの島に集まる
    "unemployment",
    "inflation",
    "rate_high",
    "rate_low",
    "price",
    "cpi_spread",
    "gdp_gap",
    "population_floor",
    "population_cap",
    "population_jump",
    "firms",
    "bank_cascade",
    "fx",
    "reserves",
    "reserves_rise",
    "trade_stopped",
    "services_share",
)


# 崩れの見張り（やり直しの報告の I1。2026-10-18）: 目安（現実の平年並みか）とは別に、1ターン目
# から島が壊れていく兆しを数える。マージの前に main より増えていないかを見る（設計書 12 GT-32）。
# 値は確かめの基準で、ゲームのルールではない。現実の大きさ（ひどい飢饉でも亡くなるのは1年以上
# かけて1〜2割）から置いた
COLLAPSE_KINDS = (
    "famine",  # COLLAPSE_SPAN ターンに飢えで亡くなった人が、その初めの人口の FAMINE_SHARE 以上
    "population_drop",  # COLLAPSE_SPAN ターンで国の出入りを除いて人口が DROP_SHARE より減った
    "stockout",  # 島で作ったことのある財が、買いたい人がいるのに直近 STOCKOUT_TURNS ターンのうち
    #              STOCKOUT_MISSING ターン以上売りに出ない
    "no_trade",  # 前は取引のあった財が、売りも買いもあるのに NO_TRADE_TURNS ターン続けて約定 0
    "herding",  # 1ターンに同じ種類の施設の HERD_SHARE 以上（HERD_MIN 以上）が作り方を替えた
    "unpaid_wages",  # 国が賃金を払えない（未払い賃金が残る）のが LONG_TURNS ターン続く
    "bank_rescue",  # 銀行が中央銀行から借りたままなのが LONG_TURNS ターン続く
    "silent_stop",  # 地図の上の人の働く施設が、稼働率 0 なのに理由を出さないのが2ターン続く
    "firm_cascade",  # 1つの企業の施設が FIRM_CASCADE_TURNS ターンのうちに CASCADE_MIN 以上
    #                  差し押さえになった（企業ごとの倒産の連鎖。回7。自分で閉じたのは数えない）
    "starved_without_benefit",  # 失業手当 0 の島で、COLLAPSE_SPAN ターンに飢えで亡くなった人が
    #                             初めの人口の FAMINE_SHARE 以上（手当のない国の飢え。回7）
)
COLLAPSE_TITLES = {
    "famine": "飢饉（1年で人口の1%以上が飢えで亡くなる）",
    "population_drop": "国の出入りを除いて人口が1年で1割より多く減った",
    "stockout": "島で作ったことのある財が、買いたい人がいるのに1年ほど売りに出ない",
    "no_trade": "前は取引のあった財が、売りも買いもあるのに1年取引が成り立たない",
    "herding": "同じ種類の施設の多くが同じターンに作り方を替えた",
    "unpaid_wages": "国が半年続けて賃金を払いきれていない",
    "bank_rescue": "銀行が半年続けて中央銀行から借りている",
    "silent_stop": "動いていないのに理由の出ない施設",
    "firm_cascade": "1つの企業の施設が3か月のうちに3つ以上、差し押さえになった",
    "starved_without_benefit": "失業手当のない国で、1年で人口の1%以上が飢えで亡くなる",
}
COLLAPSE_SPAN = TURNS_PER_YEAR
FAMINE_SHARE = 0.01
DROP_SHARE = 0.10
CASCADE_MIN = 3
# 倒産が施設から施設へ移るのは、同じ企業の貸し借り（連帯の保証）でほぼ同じターン。1年で数えると、
# 品目の入れ替えで畑や店を自分で閉じたものまで重なり、どの島でも当たった（2026-10-19 の quick）
FIRM_CASCADE_TURNS = 3
# 品切れと取引が成り立たないのは1年（半年では 50 島の世界の 7〜9 割の島が当たり、見張りに
# ならなかった。2026-10-18 の main の quick）
STOCKOUT_TURNS = TURNS_PER_YEAR
STOCKOUT_MISSING = TURNS_PER_YEAR - 2  # 1〜2ターン少し売りに出ても続いていると見る
NO_TRADE_TURNS = TURNS_PER_YEAR
LONG_TURNS = 6  # 半年（国の賃金の未払い・中央銀行からの借り）
SILENT_TURNS = 2  # 理由の出ない停止（建った直後の1ターンは数えない）
HERD_SHARE = 0.25
HERD_MIN = 3
SNAPSHOT_BEFORE = 3  # 崩れを調べる写しは、崩れが始まったターンのこれだけ前（I5）


@dataclass
class Hit:
    """1つの島の、1つの崩れの種類の当たり。first は崩れが始まったターン（続いて当たる種類は続き
    始めたターン、1年を見る種類は1年の初め）、turns は当たったターンの数、what は中身の数。"""

    first: int
    last: int = 0
    turns: int = 0
    what: Counter = field(default_factory=Counter)


@dataclass
class Watch:
    """島ごとの、崩れの見張りの途中の値と当たり。"""

    starved: list[float] = field(default_factory=list)
    made: set[str] = field(default_factory=set)  # 島で作ったことのある財
    traded: set[str] = field(default_factory=set)  # 島で取引のあったことのある財
    moved: list[float] = field(default_factory=list)  # 国を出た人 − 来た人・戻った人
    missing: dict[str, list[bool]] = field(default_factory=dict)  # 財 → 直近の「売りに出ない」
    streak: dict[tuple[str, str], int] = field(default_factory=dict)  # (種類, 中身) → 続いた数
    recipes: dict[int, str] = field(default_factory=dict)  # 施設の番号 → 前のターンの作り方
    firms: dict[int, int] = field(default_factory=dict)  # 動いている民営の施設 → 企業の番号
    falls: list[tuple[int, int]] = field(default_factory=list)  # (ターン, 企業) 差し押さえか閉鎖
    unaided: list[float] = field(default_factory=list)  # 失業手当 0 のターンの飢えで亡くなった人
    hits: dict[str, Hit] = field(default_factory=dict)

    def hit(self, kind: str, turn: int, what: str = "", since: int | None = None) -> None:
        h = self.hits.setdefault(kind, Hit(first=turn if since is None else since))
        if h.last != turn:
            h.turns += 1
            h.last = turn
        if what:
            h.what[what] += 1

    def held(
        self, kind: str, key: str, on: bool, turn: int, need: int = LONG_TURNS, what: str = ""
    ) -> None:
        """続いて当たる種類: key ごとに on が need ターン続いたら、続き始めたターンから当たりに
        する。中身は what（なければ key）。"""
        n = self.streak.get((kind, key), 0) + 1 if on else 0
        self.streak[(kind, key)] = n
        if n >= need:
            self.hit(kind, turn, what or key, since=turn - n + 1)


@dataclass
class IslandRecord:
    regime: str
    start_population: int
    turns: list[int] = field(default_factory=list)
    unemployment: list[float] = field(default_factory=list)
    inflation: list[float] = field(default_factory=list)
    cpi: list[float] = field(default_factory=list)
    population: list[int] = field(default_factory=list)
    gdp_gap: list[float] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)  # 政策金利と国債の利回りの高い方
    low_rates: list[float] = field(default_factory=list)  # 政策金利と国債の利回りの低い方
    # 財 → 売り買いのあったターン → 値段 ÷ 初めの値段 ÷ 物価指数の伸び（回2 からターンと結び付ける。
    # Codex B02。物価指数で割るのは回5 から）
    price_ratio: dict[str, dict[int, float]] = field(default_factory=dict)
    private_firms: list[int] = field(default_factory=list)  # 動いている民営工場の数
    bank_failures: list[int] = field(default_factory=list)  # 銀行が破綻したターン（1行1件）
    resources: list[str] = field(default_factory=list)  # 島の鉱業資源
    fx: dict[str, dict[int, float]] = field(default_factory=dict)  # 相手の通貨 → ターン → レート
    reserves: list[float] = field(default_factory=list)  # 外国の資産 ÷ 年の GDP（フェーズ3-3）
    employed: list[int] = field(default_factory=list)  # 就業者（回2。最初の1年の縮み）
    output: list[float] = field(default_factory=list)  # 実質の生産（初めの値段で数えた産出。回2）
    trade: list[float] = field(default_factory=list)  # 輸出 ＋ 輸入（回2。貿易が途絶えたか）
    services: list[float] = field(default_factory=list)  # 働く人のうちサービスの割合（回7）
    income: list[float] = field(default_factory=list)  # 1人あたりの実質の GDP ÷ base_income（回7）


class Recorder:
    """島ごとの統計をターンごとに記録する。"""

    def __init__(self, world: WorldState, defs: Defs, config: GameConfig) -> None:
        self.defs = defs
        self.retail = retail.good_of(config)
        self.initial = {  # 島ごとの初めの値段（島で使える作り方で解いた値段）
            i.id: initial_prices(defs, config, i.resources) for i in world.islands.values()
        }
        self.islands = {
            i.id: IslandRecord(
                regime=i.regime.get("economy", ""),
                start_population=sum(t.population for t in i.tiles),
                resources=list(i.resources),
            )
            for i in world.islands.values()
        }
        self.goods_list = list(defs.goods)
        self.turns: list[int] = []  # 記録したターン（世界全体）
        self.made: dict[str, dict[int, int]] = {}  # 財 → ターン → 作った島の数
        self.traded: dict[str, dict[int, int]] = {}  # 財 → ターン → 売り買いのあった島の数
        self.titles: dict[str, int] = {}  # 最後に記録したターンの称号（産業 → 島。フェーズ3-3）
        self.watch = {iid: Watch() for iid in self.islands}
        self.base_income = config.get("household.base_income")

    def record(self, world: WorldState) -> None:
        self.turns.append(world.turn)
        self.titles = dict(world.ext.get(experience.RANKINGS, {}).get("titles", {}))
        for island in world.islands.values():
            rec = self.islands.get(island.id)
            if rec is None or not island.has_economy:
                continue
            s = island.stats
            rec.turns.append(world.turn)
            rec.unemployment.append(s.get("unemployment", 0.0))
            rec.inflation.append(s.get("inflation", 0.0))
            rec.cpi.append(s.get("cpi", 0.0))
            rec.population.append(s.get("population", 0))
            rec.gdp_gap.append(s.get("gdp.gap", 0.0))
            rates = [island.policy.policy_rate] + [b.rate for b in island.bonds]
            rec.rates.append(max(rates) / BASIS)
            rec.low_rates.append(min(rates) / BASIS)
            rec.private_firms.append(
                sum(
                    1
                    for f in island.facilities
                    if f.built
                    and f.owner == OWNER_PRIVATE
                    and self.defs.facilities[f.kind].get("category") not in ("finance", "trade")
                )
            )
            rec.bank_failures += [world.turn] * int(s.get("banks.failed", 0))
            gdp = debt.annual_gdp(island.economy)
            foreign = s.get("fx.reserves", 0) + s.get("foreign_bonds.banks", 0)
            rec.reserves.append(foreign / gdp if gdp > 0 else 0.0)
            initial = self.initial.get(island.id, {})
            start_cpi = next((c for c in rec.cpi if c > 0), 0.0)
            cpi = rec.cpi[-1] / start_cpi if start_cpi > 0 and rec.cpi[-1] > 0 else 0.0
            rec.employed.append(s.get("employed", 0))
            rec.output.append(real_output(s, initial))
            rec.trade.append(s.get("trade.exports", 0) + s.get("trade.imports", 0))
            rec.services.append(self._services_share(island))
            people = s.get("population", 0)
            real = s.get("gdp", 0) / (rec.cpi[-1] / 100) if rec.cpi[-1] > 0 else 0.0
            rec.income.append(real / people / self.base_income if people > 0 else 0.0)
            for key, value in s.items():
                currency = key.removeprefix("fx.")
                if currency != key and currency.isdigit() and value > 0:
                    rec.fx.setdefault(currency, {})[world.turn] = value
            for key, value in s.items():
                good = key.removeprefix("production.")
                if good != key and value > 0:
                    made = self.made.setdefault(good, {})
                    made[world.turn] = made.get(world.turn, 0) + 1
            for m in island.markets:
                if m.volume > 0:
                    traded = self.traded.setdefault(m.good, {})
                    traded[world.turn] = traded.get(world.turn, 0) + 1
                if m.volume > 0 and initial.get(m.good) and cpi > 0:
                    ratio = m.price / initial[m.good] / cpi
                    rec.price_ratio.setdefault(m.good, {})[world.turn] = ratio
            self._watch(island, world.turn, rec, self.watch[island.id])

    def _services_share(self, island: IslandState) -> float:
        """働く人（施設で働く人。町の自給の農業を除く）のうち、サービスの施設で働く割合。"""
        total = served = 0
        for f in island.facilities:
            if not f.built or f.workers <= 0:
                continue
            total += f.workers
            if self.defs.facilities[f.kind].get("category") in SERVICE_CATEGORIES:
                served += f.workers
        return served / total if total else 0.0

    def _watch(self, island: IslandState, turn: int, rec: IslandRecord, w: Watch) -> None:
        """崩れの見張り（COLLAPSE_KINDS）。record が島ごとに呼ぶ。状態は読むだけ。"""
        s = island.stats
        w.starved.append(s.get("population.starved", 0))
        no_benefit = island.policy.unemployment_benefit <= 0
        w.unaided.append(s.get("population.starved", 0) if no_benefit else 0.0)
        w.moved.append(
            s.get("migration.left", 0)
            - s.get("migration.arrived", 0)
            - s.get("migration.returned", 0)
        )
        if len(rec.population) > COLLAPSE_SPAN:
            before = rec.population[-1 - COLLAPSE_SPAN]
            since = rec.turns[-1 - COLLAPSE_SPAN]
            if before > 0 and sum(w.starved[-COLLAPSE_SPAN:]) >= before * FAMINE_SHARE:
                w.hit("famine", turn, since=since)
            stayed = rec.population[-1] + sum(w.moved[-COLLAPSE_SPAN:])
            if before > 0 and stayed < before * (1 - DROP_SHARE):
                w.hit("population_drop", turn, since=since)
            unaided = sum(w.unaided[-COLLAPSE_SPAN:])
            if before > 0 and unaided >= before * FAMINE_SHARE:
                w.hit("starved_without_benefit", turn, since=since)
        self._watch_firms(island, turn, w)
        w.made.update(
            k.removeprefix("production.")
            for k, v in s.items()
            if k.startswith("production.") and v > 0
        )
        # 店が売りに出ていなくても、直売・屋台が売った小売りは品切れでない（回7）
        stalls = s.get("retail.direct", 0) > 0
        for m in island.markets:
            good = m.good
            if m.supply > 0 or good in w.missing:
                gone = m.demand > 0 and m.supply <= 0 and not (good == self.retail and stalls)
                recent = w.missing.setdefault(good, [])
                recent.append(gone)
                del recent[:-STOCKOUT_TURNS]
                # 島で作ったことのある財だけ（初めの在庫で売っただけの、作れない財は数えない）
                if gone and good in w.made and sum(recent) >= STOCKOUT_MISSING:
                    since = turn - len(recent) + 1 + recent.index(True)
                    w.hit("stockout", turn, good, since=since)
            if m.volume > 0:
                w.traded.add(good)
            stuck = good in w.traded and m.demand > 0 and m.supply > 0 and m.volume <= 0
            w.held("no_trade", good, stuck, turn, NO_TRADE_TURNS)
        unpaid = any(
            k.endswith(f"/{ledger.GOV}") and f"/{arrears.WAGE_ARREARS}/" in k and v > 0
            for k, v in island.accounts.items()
        )
        w.held("unpaid_wages", "", unpaid, turn)
        able: Counter = Counter()  # 作り方を替えられる種類の、建った施設の数
        switched: Counter = Counter()
        recipes = {}
        for f in island.facilities:
            d = self.defs.facilities[f.kind]
            if not f.built:
                continue
            if d.get("category") == "finance":
                bank = ledger.bank_entity(f.id)
                cb = ledger.balance(island, ledger.account(ledger.CB, ledger.CB_LOAN, bank))
                alive = f.economy.get("failed") is None
                w.held("bank_rescue", bank, cb > 0 and alive, turn)
            if len(d.get("recipes", {})) > 1:
                able[f.kind] += 1
                if w.recipes.get(f.id, f.recipe) != f.recipe:
                    switched[f.kind] += 1
                recipes[f.id] = f.recipe
            working = d.get("workers", 0) > 0 and d.get("category") != "housing" and located(f)
            silent = not is_stopped(f) and f.operating_rate <= 0 and not f.status
            if working:
                w.held("silent_stop", str(f.id), silent, turn, SILENT_TURNS, f.kind)
        w.recipes = recipes
        for kind, n in switched.items():
            if n >= HERD_MIN and n >= able[kind] * HERD_SHARE:
                w.hit("herding", turn, f"{kind} {n}/{able[kind]}")

    @staticmethod
    def _watch_firms(island: IslandState, turn: int, w: Watch) -> None:
        """前のターンに動いていた民営の企業の施設のうち、差し押さえになったものを企業ごとに数え、
        FIRM_CASCADE_TURNS ターンのうちに CASCADE_MIN 以上なら企業ごとの倒産の連鎖とみる。
        自分で閉じたもの（閉鎖・取り壊し）は企業の判断なので数えない。"""
        now: dict[int, int] = {}
        for f in island.facilities:
            firm = companies.private_firm_of(island, f)
            if "foreclosed" in f.economy:
                if f.id in w.firms:
                    w.falls.append((turn, w.firms[f.id]))
            elif firm is not None and f.built and "closing" not in f.economy:
                now[f.id] = firm
        w.firms = now
        w.falls = [(t, firm) for t, firm in w.falls if t > turn - FIRM_CASCADE_TURNS]
        for firm, n in Counter(firm for _, firm in w.falls).items():
            if n >= CASCADE_MIN:
                w.hit("firm_cascade", turn, f"企業 {firm}")

    def collapses(self) -> list[dict]:
        """崩れの見張りに当たったところ（島ごと・種類ごとに1つ。1ターン目から最後まで）。

        first は崩れが始まったターン、turns は当たったターンの数、what は中身（財や施設の種類）の
        多い順。
        """
        out = []
        for iid, w in sorted(self.watch.items()):
            rec = self.islands[iid]
            for kind in COLLAPSE_KINDS:
                h = w.hits.get(kind)
                if h is None:
                    continue
                what = [k for k, _ in h.what.most_common(5)]
                line = f"島 {iid}（{rec.regime}）: {COLLAPSE_TITLES[kind]}（{h.first} ターン目から"
                line += f" {h.turns} ターン{'、' + '・'.join(what) if what else ''}）"
                out.append(
                    {"kind": kind, "island": iid, "first": h.first, "turns": h.turns}
                    | {"what": what, "line": line}
                )
        return out

    def collapse_counts(self) -> dict[str, int]:
        """崩れの種類ごとの、当たった島の数。"""
        found = Counter(kind for w in self.watch.values() for kind in w.hits)
        return {kind: found.get(kind, 0) for kind in COLLAPSE_KINDS}

    def failures(
        self, start: int = START, end: int | None = None, kinds: Iterable[str] | None = None
    ) -> list[str]:
        """目安から外れたところ（島ごと・目安ごとに1行）。start から end（なければ最後）の
        ターンで見る。kinds を渡すと、その種類（KINDS）だけを並べる。
        """
        found = self.findings(start, end)
        return [line for kind, line in found if kinds is None or kind in set(kinds)]

    def findings(self, start: int = START, end: int | None = None) -> list[tuple[str, str]]:
        """目安から外れたところの (種類, 1行)。"""
        out: list[tuple[str, str]] = []
        for good, (made, traded) in self.goods(start, end).items():
            if made <= 0:
                out.append(("goods_made", f"世界: {good} がどの島でも作られていない"))
            if traded <= 0:
                out.append(("goods_traded", f"世界: {good} がどの島でも売り買いされていない"))
        if self.titles:
            held = Counter(self.titles.values())
            island_id, count = min(held.items(), key=lambda kv: (-kv[1], kv[0]))
            if count > len(self.titles) * TITLE_SHARE:
                line = (
                    f"世界: 島 {island_id} が称号を {count} 個持っている"
                    f"（称号のある産業 {len(self.titles)}）"
                )
                out.append(("titles", line))
        for iid, rec in sorted(self.islands.items()):
            out.extend(
                (kind, f"島 {iid}（{rec.regime}）: {m}")
                for kind, m in _island_failures(rec, start, end)
            )
        return out

    def counts(self, start: int = START, end: int | None = None) -> dict[str, int]:
        """目安の種類ごとの、外れた行の数（島ごとの目安は外れた島の数）。"""
        found = Counter(kind for kind, _ in self.findings(start, end))
        return {kind: found.get(kind, 0) for kind in KINDS}

    def goods(self, start: int = START, end: int | None = None) -> dict[str, tuple[float, float]]:
        """財ごとの、作った島の数と売り買いのあった島の数の、start から end までの1ターン
        あたりの平均。その間のターンを記録していなければ空。
        """
        window = [t for t in self.turns if _inside(t, start, end)]
        if not window:
            return {}

        def mean(by_turn: dict[int, int]) -> float:
            return sum(n for t, n in by_turn.items() if _inside(t, start, end)) / len(window)

        return {
            good: (mean(self.made.get(good, {})), mean(self.traded.get(good, {})))
            for good in self.goods_list
        }

    def summary(self, start: int = START, end: int | None = None) -> dict[str, float]:
        """全部の島をまとめた値（目安と見比べるため）。"""
        unemployment, inflation, spreads, ratios = [], [], [], []
        for rec in self.islands.values():
            window = _window(rec, start, end)
            unemployment += [rec.unemployment[i] for i in window]
            inflation += [rec.inflation[i] for i in window]
            spreads.append(_cpi_spread(rec, end))
            for by_turn in rec.price_ratio.values():
                ratios += [r for t, r in by_turn.items() if _inside(t, start, end)]
        if not unemployment:
            return {}
        no_coal = [
            rec.unemployment[i]
            for rec in self.islands.values()
            if NO_COAL not in rec.resources
            for i in _window(rec, start, end)
        ]
        moves = [m for rec in self.islands.values() for m in _fx_moves(rec, start, end).values()]
        firm_spreads = [
            max(abs(n - statistics.mean(tail)) for n in tail) / statistics.mean(tail)
            for rec in self.islands.values()
            if rec.regime == "market"
            and (tail := _cut(rec.private_firms, rec, end)[-FIRMS_WINDOW:])
            and statistics.mean(tail) > 0
        ]
        failed = [t for rec in self.islands.values() for t in rec.bank_failures]
        out = {
            "unemployment.min": min(unemployment),
            "unemployment.max": max(unemployment),
            "unemployment.mean": statistics.mean(unemployment),
            "unemployment.inside": _inside_share(unemployment, *UNEMPLOYMENT_RANGE),
            "inflation.min": min(inflation),
            "inflation.max": max(inflation),
            "inflation.mean": statistics.mean(inflation),
            "inflation.inside": _inside_share(inflation, *INFLATION_RANGE),
            "cpi.spread.max": max(spreads),
            "price.ratio.min": min(ratios) if ratios else 1.0,
            "price.ratio.max": max(ratios) if ratios else 1.0,
            "price.inside": _inside_share(ratios, *PRICE_RANGE),
            "firms.spread.max": max(firm_spreads, default=0.0),
            "banks.failed": float(sum(1 for t in failed if end is None or t <= end)),
            "unemployment.no_coal.mean": statistics.mean(no_coal) if no_coal else 0.0,
            "fx.move.mean": statistics.mean(moves) if moves else 0.0,
            "fx.move.max": max(moves, default=0.0),
            "titles.holders": float(len(set(self.titles.values()))),
            "titles.max": float(max(Counter(self.titles.values()).values(), default=0)),
            "reserves.max": max(
                (
                    max(rec.reserves[i] for i in _window(rec, start, end))
                    for rec in self.islands.values()
                    if _window(rec, start, end)
                ),
                default=0.0,
            ),
        }
        out.update(self.early())
        return out

    def early(self) -> dict[str, float]:
        """最初の1年の縮み: EARLY_TURN ターン目の就業者と実質の生産の、1ターン目との比（島ごと）の
        中央値と最小。1ターン目は建国の施設が初めて動いたターン（建国したときの統計には就業者がない）。
        記録していなければ空。"""
        employment, output = [], []
        for rec in self.islands.values():
            if 1 not in rec.turns or EARLY_TURN not in rec.turns:
                continue
            first, year = rec.turns.index(1), rec.turns.index(EARLY_TURN)
            if rec.employed[first] > 0:
                employment.append(rec.employed[year] / rec.employed[first])
            if rec.output[first] > 0:
                output.append(rec.output[year] / rec.output[first])
        out = {}
        for name, values in (("employment", employment), ("output", output)):
            if values:
                out[f"early.{name}.median"] = statistics.median(values)
                out[f"early.{name}.min"] = min(values)
        return out

    def prices(
        self, start: int = START, end: int | None = None
    ) -> dict[str, tuple[float, float, float]]:
        """財ごとの「値段 ÷ 初めの値段 ÷ 物価指数の伸び」の最小・中央・最大。

        全部の島の、start から end までの売り買いのあったターンで見る。
        """
        ratios: dict[str, list[float]] = {}
        for rec in self.islands.values():
            for good, by_turn in rec.price_ratio.items():
                values = [r for t, r in by_turn.items() if _inside(t, start, end)]
                ratios.setdefault(good, []).extend(values)
        return {
            good: (min(values), statistics.median(values), max(values))
            for good, values in sorted(ratios.items())
            if values
        }


def real_output(stats: Mapping[str, float], prices: Mapping[str, int]) -> float:
    """実質の生産: このターンに作った量を島の初めの値段で数えた額（物価の動きを除いた産出。中間財も
    含む総産出で、付加価値ではない。Codex のレビュー E32）。"""
    return sum(
        value * prices.get(key.removeprefix("production."), 0)
        for key, value in stats.items()
        if key.startswith("production.")
    )


def _inside(turn: int, start: int, end: int | None) -> bool:
    return turn >= start and (end is None or turn <= end)


def _inside_share(values: list[float], low: float, high: float) -> float:
    return sum(1 for v in values if low <= v <= high) / len(values) if values else 1.0


def _window(rec: IslandRecord, start: int, end: int | None = None) -> list[int]:
    return [i for i, t in enumerate(rec.turns) if _inside(t, start, end)]


def _cut(values: list, rec: IslandRecord, end: int | None) -> list:
    """ターンごとの値の並びのうち、end までのターンの分（end がなければ全部）。"""
    if end is None:
        return values
    return values[: sum(1 for t in rec.turns if t <= end)]


def _cpi_spread(rec: IslandRecord, end: int | None = None) -> float:
    tail = _cut(rec.cpi, rec, end)[-CPI_WINDOW:]
    if len(tail) < 2 or statistics.mean(tail) <= 0:
        return 0.0
    return statistics.pstdev(tail) / statistics.mean(tail)


def _fx_moves(rec: IslandRecord, start: int, end: int | None = None) -> dict[str, float]:
    """相手の通貨ごとの、為替の年の動き（FX_YEAR ターン前のレートからの変わり方の絶対値）の平均。"""
    out = {}
    for currency, by_turn in rec.fx.items():
        moves = [
            abs(rate / by_turn[turn - FX_YEAR] - 1)
            for turn, rate in by_turn.items()
            if _inside(turn, start, end) and by_turn.get(turn - FX_YEAR, 0) > 0
        ]
        if moves:
            out[currency] = statistics.mean(moves)
    return out


def _outside(values: list[float], low: float, high: float) -> tuple[int, float, float]:
    bad = [v for v in values if v < low or v > high]
    return len(bad), min(values, default=0.0), max(values, default=0.0)


def _trade_gap(rec: IslandRecord, start: int, end: int | None) -> tuple[int, int, int]:
    """start から end までで、貿易（輸出と輸入）が 0 のまま続いた一番長いターン数と、その初めと
    終わりのターン。それより前に一度も貿易していなければ数えない（貿易を始めていない島）。"""
    longest, first, last = 0, 0, 0
    run, traded = 0, False
    for t, value in zip(rec.turns, rec.trade, strict=False):
        if end is not None and t > end:
            break
        if value > 0:
            traded, run = True, 0
            continue
        if not traded:
            continue
        run += 1
        if t >= start and run > longest:
            longest, first, last = run, t - run + 1, t
    return longest, first, last


def _island_failures(
    rec: IslandRecord, start: int, end: int | None = None
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    window = _window(rec, start, end)
    if not window:
        return out
    checks = [
        ("unemployment", "失業率", [rec.unemployment[i] for i in window], UNEMPLOYMENT_RANGE),
        ("inflation", "物価上昇率", [rec.inflation[i] for i in window], INFLATION_RANGE),
        ("rate_high", "金利の上限", [rec.rates[i] for i in window], RATE_RANGE),
        ("rate_low", "金利の下限", [rec.low_rates[i] for i in window], RATE_RANGE),
    ]
    for kind, name, values, (low, high) in checks:
        count, lo, hi = _outside(values, low, high)
        if count:
            out.append((kind, f"{name}が {count} ターン外れた（{lo:.3f}〜{hi:.3f}）"))
    for good, by_turn in sorted(rec.price_ratio.items()):
        values = [r for t, r in sorted(by_turn.items()) if _inside(t, start, end)]
        count, lo, hi = _outside(values, *PRICE_RANGE)
        if count:
            line = f"{good} の値段が {count} ターン外れた（初めの {lo:.2f}〜{hi:.2f} 倍）"
            out.append(("price", line))
    spread = _cpi_spread(rec, end)
    if spread > CPI_SPREAD:
        out.append(("cpi_spread", f"物価指数の振れ幅 {spread:.3f}"))
    gap = max(rec.gdp_gap[i] for i in window)
    if gap > GDP_GAP:
        out.append(("gdp_gap", f"GDP の差 {gap:.4f}"))
    people = _cut(rec.population, rec, end)
    if min(people) < rec.start_population * POPULATION_FLOOR:
        line = f"人口が {min(people)} まで減った（初め {rec.start_population}）"
        out.append(("population_floor", line))
    if people[-1] > rec.start_population * POPULATION_CAP:
        line = f"人口が {people[-1]} まで増えた（初め {rec.start_population}）"
        out.append(("population_cap", line))
    jumps = [abs(b - a) / a for a, b in zip(people, people[1:], strict=False) if a]
    if jumps and max(jumps) >= POPULATION_JUMP:
        out.append(("population_jump", f"人口が1ターンで {max(jumps):.1%} 変わった"))
    firms = _cut(rec.private_firms, rec, end)[-FIRMS_WINDOW:]
    if rec.regime == "market" and firms:
        mean = statistics.mean(firms)
        low, high = (1 - FIRMS_SPREAD) * mean, (1 + FIRMS_SPREAD) * mean
        if min(firms) < low or max(firms) > high:
            out.append(("firms", f"民営工場の数が {min(firms)}〜{max(firms)}（平均 {mean:.1f}）"))
    failures = [t for t in rec.bank_failures if end is None or t <= end]
    chained = sum(1 for a, b in zip(failures, failures[1:], strict=False) if b - a <= CASCADE_TURNS)
    if chained:
        line = f"銀行の破綻が {CASCADE_TURNS} ターンのうちに続いた（{chained} 回）"
        out.append(("bank_cascade", line))
    for currency, move in sorted(_fx_moves(rec, start, end).items(), key=lambda kv: int(kv[0])):
        if move > FX_MOVE:
            out.append(("fx", f"島 {currency} の通貨との為替の年の動きが平均 {move:.1%}"))
    reserves = [rec.reserves[i] for i in window]
    if max(reserves) > RESERVE_CAP:
        out.append(("reserves", f"外国の資産が年の GDP の {max(reserves):.2f} 倍まで増えた"))
    tail = _cut(rec.reserves, rec, end)[-RESERVE_WINDOW:]
    if len(tail) == RESERVE_WINDOW and tail[-1] - tail[0] > RESERVE_RISE:
        line = f"外国の資産が増え続けている（年の GDP の {tail[0]:.2f} 倍 → {tail[-1]:.2f} 倍）"
        out.append(("reserves_rise", line))
    count, lo, hi = _services_outside(rec, window)
    if count:
        line = f"サービスで働く人の割合が {count} ターン目安を外れた（{lo:.2f}〜{hi:.2f}）"
        out.append(("services_share", line))
    gap_turns, first, last = _trade_gap(rec, start, end)
    if gap_turns >= TRADE_GAP:
        line = f"貿易が {gap_turns} ターン続けて途絶えた（ターン {first}〜{last}）"
        out.append(("trade_stopped", line))
    return out


def services_band(income: float) -> tuple[float, float]:
    """1人あたりの実質の GDP（base_income の倍率）に見合う、サービスで働く人の割合の幅。"""
    t = min(1.0, max(0.0, math.log(max(income, 1.0)) / math.log(RICH_INCOME)))
    low = SERVICES_POOR[0] + (SERVICES_RICH[0] - SERVICES_POOR[0]) * t
    high = SERVICES_POOR[1] + (SERVICES_RICH[1] - SERVICES_POOR[1]) * t
    return low, high


def _services_outside(rec: IslandRecord, window: list[int]) -> tuple[int, float, float]:
    """目安の幅を外れたターンの数と、その間の割合の最小と最大（記録のない古い記録は 0）。"""
    if len(rec.services) < len(rec.turns):
        return 0, 0.0, 0.0
    values = [rec.services[i] for i in window]
    count = sum(
        1
        for i in window
        if not services_band(rec.income[i])[0] <= rec.services[i] <= services_band(rec.income[i])[1]
    )
    return count, min(values, default=0.0), max(values, default=0.0)
