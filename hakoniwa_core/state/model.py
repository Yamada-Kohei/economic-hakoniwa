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

from hakoniwa_core.state.accounts import Accounts

ORDER_PENDING = "pending"
ORDER_DONE = "done"
ORDER_FAILED = "failed"

OWNER_STATE = "state"  # 国営
OWNER_PRIVATE = "private"  # 民営

ZONE_NONE = ""
ZONE_NO_DEVELOPMENT = "no_development"  # 開発を止めるマス（住宅が自動で建たない）
ZONE_OFFICE = "office"  # オフィス街（銀行が入る。フェーズ2）
ZONE_INDUSTRIAL = "industrial"  # 工業団地（民営の工場が採算を見て建つ。フェーズ2-2）

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
    # インフラ: 種類ごとの [整備度, 維持で戻す目標]（1000 分の 1。hakoniwa_core/infra.py）。
    # フェーズ1・2 の送電線は {"power": 1}
    infra: dict[str, Any] = field(default_factory=dict)
    pollution: int = 0
    ext: dict[str, Any] = field(default_factory=dict)

    @property
    def power_line(self) -> bool:
        """送電網の整備度が 0 より大きいか（電力を通すかは infra.py の carries_power）。"""
        value = self.infra.get("power")
        if isinstance(value, list):
            return value[0] > 0
        return bool(value)


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
class Loan:
    """融資の1件。残高は勘定（<貸し手>/loan:<番号>/<借り手>）で持つ。

    金利は変動金利で、銀行の段が毎ターン計算し直して rate に書く（式 loan_rate）。
    """

    id: int
    lender: str  # 貸し手の銀行の主体（bank:<番号>）
    borrower: str  # 借り手の主体（fac:<番号>）
    purpose: str  # 使い道: construction（建設資金）、working（運転資金）
    principal: int  # 貸した額
    term: int  # 元金を返す期間（ターン）
    start: int  # 貸したターン
    rate: int = 0  # 今の年利（1万分の1）
    installment: int = 0  # 毎ターン返す元金
    repay_from: int = 0  # 元金を返し始めるターン（建設資金は工事が済んだ次のターンから）
    arrears: int = 0  # 続けて返済が遅れた回数


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
    # 以下はフェーズ2-2
    cb_independent: bool = False  # 中央銀行が独立しているか
    cb_bond_trade: int = 0  # 中央銀行が毎ターン国債を買う額（マイナスは売る。独立していないとき）
    bond_unsold: str = (
        "underwrite"  # 入札の売れ残り: underwrite（中央銀行が引き受ける）・cut（支出を減らす）
    )
    bank_entry: str = ""  # 民間銀行の参入: free・license・closed（"" は経済体制の既定）
    deposit_insurance: int = 30000  # 預金保護の上限額（1人あたり、施設は1つあたり。-1 は無制限）
    # 補助金: {"kind": production・capital・bailout, "good"/"x"/"y", "rate": 1万分の1,
    #          "until": ターン}
    subsidies: list[dict[str, Any]] = field(default_factory=list)
    # 以下はフェーズ3-3（産業政策と国際金融。hakoniwa_core/trade/policy.py）。財と通貨と島の番号は
    # 文字列のキー
    tariffs: dict[str, int] = field(default_factory=dict)  # 財 → 関税率（1万分の1）
    export_subsidies: dict[str, int] = field(default_factory=dict)  # 財 → 1 単位あたりの輸出補助
    # 財 → 1ターンの輸入の上限（1000分の1）
    import_quotas: dict[str, int] = field(default_factory=dict)
    embargoes: dict[str, list[str]] = field(default_factory=dict)  # 相手の島 → 財（空はすべての財）
    capital_controls: str = ""  # 資本取引: free・official・closed（"" は経済体制の既定）
    fx_intervention: dict[str, int] = field(default_factory=dict)  # 通貨 → 中央銀行が毎ターン買う額
    fx_peg: dict[str, int] = field(default_factory=dict)  # 通貨 → 固定したレート（× 1万分の1）
    # 外国の国債の売り買い: 持ち手（gov・cb）→ 発行した島 → 毎ターン買う額面（マイナスは売る）
    foreign_bonds: dict[str, dict[str, int]] = field(default_factory=dict)
    # 以下はフェーズ3-4。値段の下限（財 → 値段。混合経済だけ。上限は price_cap）
    price_floor: dict[str, int] = field(default_factory=dict)
    # 以下はバランス調整の回3（国をまたぐ移民。hakoniwa_core/modules/migration）
    immigration: str = ""  # 受け入れ: open・jobs・engineers・refugees・closed（"" は受け入れない）
    immigration_cap: int = 0  # 受け入れの1年の上限（住民の1万分の1。0 は game.yaml の既定）
    emigration: str = ""  # 出国: free・permit・ban（"" は自由）
    emigration_quota: int = 0  # 許可制の1年の枠（住民の1万分の1）


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
    happiness_blend: int = 0  # 幸福度の重みを前のフェーズの重みから移す残りのターン数
    loan_seq: int = 0
    # 金融（フェーズ2-2）: 国債の区分ごとの利回り、銀行間の金利、債務不履行のターン、品切れの
    # 履歴など（economy/banks.py と modules/bonds・central_bank・firms が読み書きする）
    finance: dict[str, Any] = field(default_factory=dict)
    # フェーズ2-3: インフラの工事の列・工事の資材・止まったわけ（modules/infrastructure）、
    # 教育水準（1000 分の 1。modules/education）、島全体で数える働き手の人数（技術者 engineer）
    infra: dict[str, Any] = field(default_factory=dict)
    education: int = 0
    workers: dict[str, int] = field(default_factory=dict)
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
    # お金: 勘定の名前（主体/持ち物/相手）→ 残高。hakoniwa_core/ledger.py を通してだけ動かす。
    # 名前の索引を持つ dict（hakoniwa_core/state/accounts.py）。ふつうの dict を渡しても写して持つ
    accounts: dict[str, int] = field(default_factory=Accounts)
    bonds: list[Bond] = field(default_factory=list)
    loans: list[Loan] = field(default_factory=list)  # 融資（番号の小さい順）
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

    def __post_init__(self) -> None:
        if not isinstance(self.accounts, Accounts):
            self.accounts = Accounts(self.accounts)

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
