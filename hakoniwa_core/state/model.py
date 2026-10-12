"""世界の状態。ターン処理はこの形のデータを書き換えます。

保存のしかた（Django のモデル）はサーバー側が受け持ち、ここでは知りません。
モジュール固有の状態は ext（拡張欄）に入れます。
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
    accounting: str = "treasury"  # 国庫直轄 treasury か独立採算 self_supporting か（フェーズ2から）
    name: str = ""  # プレイヤーが付けた名前。空なら体制に合わせた自動の名前
    built: bool = False  # 工事が済んだか
    progress: int = 0  # 工事の進み（1000 分の 1 ターン単位）
    workers: int = 0  # このターンに雇っている人数（工事中は工事の人手）
    operating_rate: int = 0  # 稼働率（1000 分の 1）
    priority: int | None = None  # 命令で変えた優先順位。None は定義の既定値
    status: str = ""  # 全力で動けない理由（"" は全力）。例: "workers", "input:coal", "power"
    stock: dict[str, int] = field(default_factory=dict)  # 施設が持つ財（投入財・工事の資材）
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
    # 島の共同倉庫。財ごとに [作られたターン, 量] の組を古い順に並べる（傷む財の計算に使う）
    warehouse: dict[str, list[list[int]]] = field(default_factory=dict)
    orders: list[OrderState] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    ext: dict[str, Any] = field(default_factory=dict)
    # このターンの記録（人口・生産量など）。サーバーが統計の表に保存する。状態の写しには入れない
    stats: dict[str, float] = field(default_factory=dict)
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
