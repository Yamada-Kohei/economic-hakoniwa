"""モジュールが登録するもの（段・命令・式）の入れ物と、その形。

モジュールは hakoniwa_core/modules/<名前>/ またはパックの modules/<名前>/ に置き、
register(registry) 関数で段・命令・式を登録します。詳しくは docs/modules.md。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from hakoniwa_core.state import IslandState

if TYPE_CHECKING:
    from hakoniwa_core.config import GameConfig
    from hakoniwa_core.defs import Defs
    from hakoniwa_core.turn.context import StageContext


class IslandStage(ABC):
    """島ごとの段。run_island はその島の状態だけを書き換える。

    他の島を読むときは ctx.other_island() を使う（段が始まる前の写しが返る）。
    他の島を読む段は reads_other_islands = True にする（写しを取るため少し遅くなる）。
    """

    name: ClassVar[str]
    reads_other_islands: ClassVar[bool] = False

    @abstractmethod
    def run_island(self, island: IslandState, ctx: StageContext) -> None: ...


class WorldStage(ABC):
    """全島の段。注文を集める → 一度に決める → 各島に結果を返す、の3つに分けて書く。"""

    name: ClassVar[str]

    @abstractmethod
    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        """この島の注文を返す（島の状態は書き換えない）。"""

    @abstractmethod
    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        """全島の注文から、島ごとの結果を決める。"""

    @abstractmethod
    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        """結果をこの島に書き込む。"""


Stage = IslandStage | WorldStage


class OrderFailed(Exception):
    """命令を実行できない。reason は messages の表示名の名前（reason_...）。"""

    def __init__(self, reason: str, **args: Any):
        self.reason = reason
        self.args_ = args
        super().__init__(reason)


@dataclass(frozen=True)
class OrderHandler:
    """命令の種類ごとの処理。

    check: 受付時と実行時に呼ばれる中身の確認。問題があれば OrderFailed を投げる。
    execute: 実行。check の後に呼ばれる。
    """

    kind: str
    execute: Callable[[IslandState, dict[str, Any], StageContext], None]
    check: Callable[[IslandState, dict[str, Any], Defs], None] | None = None


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self) -> None:
        self.stages: dict[str, Stage] = {}
        self.orders: dict[str, OrderHandler] = {}
        self.formulas: dict[str, Callable[..., Any]] = {}
        self.checks: list[Callable[[Defs, GameConfig], list[str]]] = []
        self.order_gates: list[Callable[..., None]] = []

    def add_stage(self, stage: Stage) -> None:
        if stage.name in self.stages:
            raise RegistryError(f"段 {stage.name} は登録済みです")
        self.stages[stage.name] = stage

    def add_order(self, handler: OrderHandler) -> None:
        if handler.kind in self.orders:
            raise RegistryError(f"命令 {handler.kind} は登録済みです")
        self.orders[handler.kind] = handler

    def add_formula(self, name: str, func: Callable[..., Any]) -> None:
        """名前付きの式の既定の関数。game.yaml の formulas: で差し替えられる。"""
        if name in self.formulas:
            raise RegistryError(f"式 {name} は登録済みです")
        self.formulas[name] = func

    def add_order_gate(self, func: Callable[..., None]) -> None:
        """命令の段の関所: 命令を実行する直前に func(island, order, ctx) を呼ぶ。OrderFailed を
        投げると、その命令は実行せずに失敗にする（行政力は使う）。例: 議会の採決（フェーズ3-4）。"""
        self.order_gates.append(func)

    def add_check(self, func: Callable[[Defs, GameConfig], list[str]]) -> None:
        """起動時の確認。モジュールが要る定義や設定がそろっているかを調べ、問題を文で返す関数。"""
        self.checks.append(func)
