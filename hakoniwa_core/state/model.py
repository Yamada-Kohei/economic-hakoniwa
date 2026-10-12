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


@dataclass
class Tile:
    x: int
    y: int
    terrain: str
    population: int = 0
    ext: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderState:
    """プレイヤーが並べた命令。seq の小さい順に実行する。"""

    id: int
    seq: int
    kind: str
    payload: dict[str, Any]
    status: str = ORDER_PENDING
    reason: str | None = None
    executed_turn: int | None = None


@dataclass
class LogEntry:
    """ターンログの1行。文面は messages の表示名から作る。"""

    kind: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class IslandState:
    id: int
    name: str
    wx: int  # 世界マップ上の区画
    wy: int
    size: int
    tiles: list[Tile]  # y * size + x の順
    orders: list[OrderState] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    ext: dict[str, Any] = field(default_factory=dict)

    def tile(self, x: int, y: int) -> Tile:
        return self.tiles[y * self.size + x]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size


@dataclass
class WorldState:
    turn: int
    seed: int
    islands: dict[int, IslandState]
    ext: dict[str, Any] = field(default_factory=dict)

    def island_ids(self) -> list[int]:
        """島の番号の昇順。順番を決めたいときはいつもこれを使う。"""
        return sorted(self.islands)
