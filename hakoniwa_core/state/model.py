"""世界の状態。ターン処理はこの形のデータを書き換えます。

保存のしかた（Django のモデル）はサーバー側が受け持ち、ここでは知りません。
モジュール固有の状態は ext（拡張欄）に入れます。

財の在庫（島の国の倉庫と施設の在庫）は、財ごとに [作られたターン, 量] の組を古い順に並べた
形で持ちます（hakoniwa_core/stock.py）。お金は勘定 accounts の残高だけで持ち、
hakoniwa_core/ledger.py を通してだけ動かします（フェーズ2から）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ORDER_PENDING = "pending"
ORDER_DONE = "done"
ORDER_FAILED = "failed"

OWNER_STATE = "state"  # 国営
OWNER_PRIVATE = "private"  # 民営

ZONE_NONE = ""
ZONE_NO_DEVELOPMENT = "no_development"  # 開発を止めるマス（住宅が自動で建たない）
ZONE_OFFICE = "office"  # オフィス街（銀行が入る。フェーズ2）

ACCOUNTING_TREASURY = "treasury"  # 国庫直轄（国営だけ）
ACCOUNTING_SELF = "self_supporting"  # 独立採算

# 在庫: 財ごとに [作られたターン, 量] の組を古い順に並べたもの
Stock = dict[str, list[list[int]]]


@dataclass
class Tile:
    x: int
    y: int
    terrain: str
    population: int = 0
    zone: str = ZONE_NONE  # 指定（フェーズ2で工業団地・オフィス街を足す）
    infra: dict[str, int] = field(default_factory=dict)  # インフラ。フェーズ1は送電線 {"power": 1}
    pollution: int = 0
    ext: dict[str, Any] = field(default_factory=dict)

    @property
    def power_line(self) -> bool:
        return self.infra.get("power", 0) > 0


@dataclass
class Facility:
    """マスに建つ施設（住宅も施設の一種）。1マスに1つ。"""

    id: int  # 島の中での番号（作った順）
    kind: str  # facilities.yaml の名前
    x: int
    y: int
    recipe: str = "default"
    owner: str = OWNER_STATE
    accounting: str = ACCOUNTING_TREASURY  # 国庫直轄 treasury か独立採算 self_supporting か
    name: str = ""  # プレイヤーが付けた名前。空なら体制に合わせた自動の名前
    built: bool = False  # 工事が済んだか
    progress: int = 0  # 工事の進み（1000 分の 1 ターン単位）
    workers: int = 0  # このターンに雇っている人数（工事中は工事の人手）
    operating_rate: int = 0  # 稼働率（1000 分の 1）
    priority: int | None = None  # 命令で変えた優先順位。None は定義の既定値
    status: str = ""  # 全力で動けない理由（"" は全力）。例: "workers", "input:coal", "power"
    # 施設が持つ財（産出物の在庫・投入財・工事の資材）。作られたターンごとの組で持つ
    stock: Stock = field(default_factory=dict)
    # 経済の状態（賃金・希望価格・操業目標・雇っている人の住むマスなど。hakoniwa_core/economy）
    economy: dict[str, Any] = field(default_factory=dict)
    ext: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderState:
    """プレイヤーが並べた命令。seq の小さい順に実行する。"""

    id: int
    seq: int
    kind: str
    payload: dict[str, Any]
    status: str = ORDER_PENDING
    reason: str | None = None  # 実行できなかった理由（messages の名前）
    reason_args: dict[str, Any] = field(default_factory=dict)  # 理由の文の引数
    executed_turn: int | None = None


@dataclass
class LogEntry:
    """ターンログの1行。文面は messages の表示名から作る。"""

    kind: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class JournalEntry:
    """仕訳の1行。debit の残高が amount だけ増え、credit の残高が amount だけ減る。"""

    kind: str  # 仕訳の種類（finance.yaml の journal_kinds）
    debit: str
    credit: str
    amount: int


@dataclass
class TradeRecord:
    """約定の1件（市場の段で、買い手と売り手の組ごと）。"""

    good: str
    buyer: str  # 主体の名前（fac:12、hh:general、gov など）
    seller: str
    quantity: int  # 1000 分の 1 単位
    amount: int  # 代金（運賃と税を含まない）


@dataclass
class MarketRecord:
    """財ごとの、このターンの市場の記録。"""

    good: str
    price: int  # 約定価格（売買がなければ基準の値段のまま）
    volume: int  # 売買の量
    demand: int  # 買いたかった量（出された買い注文の合計）
    supply: int  # 売りたかった量（出された売り注文の合計）


@dataclass
class FacilityBook:
    """施設の帳簿の1ターン分。items は収支の項目ごとの額（hakoniwa_core/economy/books.py）。"""

    facility: int
    items: dict[str, int] = field(default_factory=dict)
    deposit: int = 0
    debt: int = 0
    inventory: int = 0  # 在庫の値打ち（そのターンの値段で）


@dataclass
class Bond:
    """国債の銘柄。持ち手ごとの額面は勘定（<持ち手>/bond:<番号>/gov）で持つ。"""

    id: int
    issued: int  # 発行したターン
    term: int  # 期間（ターン）
    rate: int  # 年の利率（1万分の1）
    face: int  # 発行額（額面）

    @property
    def maturity(self) -> int:
        return self.issued + self.term


@dataclass
class Policy:
    """島の政策。値は建国のときに game.yaml の既定から入れ、命令で変わる。

    率と金利は1万分の1（10% は 1000）、お金は通貨の最小単位。
    """

    income_tax: int = 0  # 所得税率
    corporate_tax: int = 0  # 法人税率
    consumption_tax: int = 0  # 消費税率
    budget: dict[str, int] = field(default_factory=dict)  # 分野ごとの年の予算
    minimum_wage: int = 0  # 最低賃金（0 は決めない）
    unemployment_benefit: int = 0  # 失業手当（平均賃金に対する率）
    price_cap: dict[str, int] = field(default_factory=dict)  # 公共料金の上限（財 → 値段）
    policy_rate: int = 0  # 政策金利
    deposit_rate: int = 0  # 預金金利（フェーズ2-1 は固定。2-2 から銀行ごとに動く）
    bond_terms: dict[str, int] = field(default_factory=dict)  # 自動の発行の期間（ターン）→ 割合


@dataclass
class EconomyState:
    """島の経済の状態。値段・賃金・物価の履歴、家計の層、取引銀行など。"""

    started: int | None = None  # お金の経済が始まったターン（None はまだ。フェーズ1の島）
    prices: dict[str, int] = field(default_factory=dict)  # 財ごとの基準の値段（前の約定価格）
    # 直近の値段・家計が買った量・物価指数・平均賃金の履歴（古い順。長さは game.yaml の records）
    price_history: dict[str, list[int]] = field(default_factory=dict)
    bought_history: dict[str, list[int]] = field(default_factory=dict)
    cpi_history: list[float] = field(default_factory=list)
    wage_history: list[int] = field(default_factory=list)
    gdp_history: list[int] = field(default_factory=list)  # GDP（生産の側）
    average_wage: int = 0  # 前のターンの平均賃金（雇われている人数で重みを付けた平均）
    households: dict[str, dict[str, Any]] = field(default_factory=dict)  # 層ごとの状態
    banks: list[int] = field(default_factory=list)  # 銀行の施設の番号（主体 bank:<番号>）
    deposit_bank: dict[str, int] = field(default_factory=dict)  # 主体 → 預金を置く銀行
    spending: dict[str, int] = field(default_factory=dict)  # 前のターンの政府の支出（項目ごと）
    freight: dict[str, Any] = field(default_factory=dict)  # 運賃の率の割り増しなど
    housing: dict[str, Any] = field(default_factory=dict)  # 町の資材の注文と、買った資材
    bond_seq: int = 0
    cb_loss: int = 0  # 中央銀行の繰り越した損失（後の利益で埋めてから国庫に納める）
    happiness_blend: int = 0  # 幸福度の重みをフェーズ1から移す残りのターン数
    ext: dict[str, Any] = field(default_factory=dict)


@dataclass
class IslandState:
    id: int
    name: str  # 国名
    wx: int  # 世界マップ上の区画
    wy: int
    size: int
    tiles: list[Tile]  # y * size + x の順
    capital_x: int | None = None  # 首都のマス
    capital_y: int | None = None
    resources: list[str] = field(default_factory=list)  # 選んだ鉱業資源
    regime: dict[str, str] = field(default_factory=dict)  # 体制の軸ごとの選択（regimes.yaml）
    names: dict[str, str] = field(default_factory=dict)  # 首都名・通貨名と記号・国家元首の名前
    facilities: list[Facility] = field(default_factory=list)  # 番号の小さい順
    # 国の倉庫（フェーズ1の島の共同倉庫）。財ごとに [作られたターン, 量] の組を古い順に並べる
    warehouse: Stock = field(default_factory=dict)
    orders: list[OrderState] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    # お金: 勘定の名前（主体/持ち物/相手）→ 残高。hakoniwa_core/ledger.py を通してだけ動かす
    accounts: dict[str, int] = field(default_factory=dict)
    bonds: list[Bond] = field(default_factory=list)
    economy: EconomyState = field(default_factory=EconomyState)
    policy: Policy = field(default_factory=Policy)
    ext: dict[str, Any] = field(default_factory=dict)
    # このターンの記録（人口・生産量など）。サーバーが統計の表に保存する。状態の写しには入れない
    stats: dict[str, float] = field(default_factory=dict)
    # このターンの仕訳・約定・市場の記録・施設の帳簿。サーバーが表に保存する。状態の写しには入れない
    journal: list[JournalEntry] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    markets: list[MarketRecord] = field(default_factory=list)
    books: list[FacilityBook] = field(default_factory=list)
    # 段から段へ渡す、このターンだけの値。保存しない
    scratch: dict[str, Any] = field(default_factory=dict)

    def tile(self, x: int, y: int) -> Tile:
        return self.tiles[y * self.size + x]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    @property
    def capital(self) -> tuple[int, int] | None:
        if self.capital_x is None or self.capital_y is None:
            return None
        return (self.capital_x, self.capital_y)

    @property
    def has_economy(self) -> bool:
        """お金の経済が始まっているか（フェーズ1のままの島は False）。"""
        return self.economy.started is not None

    def facility(self, number: int) -> Facility | None:
        for f in self.facilities:
            if f.id == number:
                return f
        return None

    def facility_at(self, x: int, y: int) -> Facility | None:
        for f in self.facilities:
            if f.x == x and f.y == y:
                return f
        return None

    def facilities_by_tile(self) -> dict[tuple[int, int], Facility]:
        return {(f.x, f.y): f for f in self.facilities}

    def next_facility_id(self) -> int:
        """新しい施設の番号。取り壊した施設の番号は使い回さない。"""
        seq = max(self.ext.get("facility_seq", 0), max((f.id for f in self.facilities), default=0))
        self.ext["facility_seq"] = seq + 1
        return seq + 1


@dataclass
class WorldState:
    turn: int
    seed: int
    islands: dict[int, IslandState]
    ext: dict[str, Any] = field(default_factory=dict)

    def island_ids(self) -> list[int]:
        """島の番号の昇順。順番を決めたいときはいつもこれを使う。"""
        return sorted(self.islands)
