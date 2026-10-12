"""DB とコアの状態（hakoniwa_core.state）の変換。

ターン更新では load() で世界全体をメモリに読み込み、コアで計算し、
save() で変わったものだけを書き戻す。トランザクションは呼ぶ側が張る。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hakoniwa_core.state import IslandState, OrderState, Tile, WorldState
from server.orders.models import Order
from server.turns.models import TurnLog
from server.world import models as m
from server.world.bulk import update_rows

BATCH = 2000


class WorldRepository:
    def __init__(self, world: m.World):
        self.world = world
        # 読み込んだときの値。save() で変わったものだけを書くために覚えておく。
        self._tiles: dict[tuple[int, int, int], tuple[int, tuple[Any, ...]]] = {}
        self._islands: dict[int, tuple[Any, ...]] = {}
        self._orders: dict[int, tuple[Any, ...]] = {}

    # --- 読み込み ------------------------------------------------------------

    def load(self, orders_until: datetime | None = None) -> WorldState:
        """世界全体を読み込む。orders_until より後に届いた命令は次のターンに回す。"""
        islands: dict[int, IslandState] = {}
        for row in self.world.islands.order_by("id"):
            islands[row.id] = IslandState(
                id=row.id,
                name=row.name,
                wx=row.wx,
                wy=row.wy,
                size=row.size,
                tiles=[None] * (row.size * row.size),  # type: ignore[list-item]
                ext=row.ext,
            )
            self._islands[row.id] = _island_values(islands[row.id])

        tiles = m.Tile.objects.filter(island__world=self.world).values_list(
            "id", "island_id", "x", "y", "terrain", "population", "ext"
        )
        for pk, island_id, x, y, terrain, population, ext in tiles.iterator(chunk_size=BATCH):
            island = islands[island_id]
            tile = Tile(x=x, y=y, terrain=terrain, population=population, ext=ext)
            island.tiles[y * island.size + x] = tile
            self._tiles[(island_id, x, y)] = (pk, _tile_values(tile))

        orders = Order.objects.filter(island__world=self.world, status="pending")
        if orders_until is not None:
            orders = orders.filter(created_at__lte=orders_until)
        for o in orders.order_by("island_id", "seq", "id"):
            state = OrderState(
                id=o.id,
                seq=o.seq,
                kind=o.kind,
                payload=o.payload,
                status=o.status,
                reason=o.reason or None,
                executed_turn=o.executed_turn,
            )
            islands[o.island_id].orders.append(state)
            self._orders[o.id] = _order_values(state)

        for island in islands.values():
            if any(t is None for t in island.tiles):
                raise ValueError(f"島 {island.id} のマスがそろっていません")

        return WorldState(
            turn=self.world.turn, seed=self.world.seed, islands=islands, ext=self.world.ext
        )

    # --- 書き戻し ------------------------------------------------------------

    def save(self, state: WorldState) -> None:
        """変わったものだけを書き戻し、ターンログを足す。"""
        changed_islands = []
        for island in state.islands.values():
            if _island_values(island) != self._islands.get(island.id):
                changed_islands.append(
                    m.Island(
                        id=island.id, name=island.name, wx=island.wx, wy=island.wy, ext=island.ext
                    )
                )
        update_rows(m.Island, changed_islands, ["name", "wx", "wy", "ext"])

        changed_tiles = []
        for island in state.islands.values():
            for tile in island.tiles:
                pk, before = self._tiles[(island.id, tile.x, tile.y)]
                if _tile_values(tile) != before:
                    changed_tiles.append(
                        m.Tile(
                            id=pk, terrain=tile.terrain, population=tile.population, ext=tile.ext
                        )
                    )
        update_rows(m.Tile, changed_tiles, ["terrain", "population", "ext"])

        changed_orders = []
        for island in state.islands.values():
            for o in island.orders:
                if _order_values(o) != self._orders.get(o.id):
                    changed_orders.append(
                        Order(
                            id=o.id,
                            status=o.status,
                            reason=o.reason or "",
                            executed_turn=o.executed_turn,
                        )
                    )
        update_rows(Order, changed_orders, ["status", "reason", "executed_turn"])

        TurnLog.objects.bulk_create(
            [
                TurnLog(island_id=island.id, turn=state.turn, kind=e.kind, args=e.args)
                for island in state.islands.values()
                for e in island.log
            ],
            batch_size=BATCH,
        )

        self.world.turn = state.turn
        self.world.ext = state.ext
        self.world.save(update_fields=["turn", "ext"])


def create_island(world: m.World, island: IslandState, owner=None) -> m.Island:
    """コアの島の状態から、新しい島を DB に作る（番号は DB が決める）。"""
    row = m.Island.objects.create(
        world=world,
        owner=owner,
        name=island.name,
        wx=island.wx,
        wy=island.wy,
        size=island.size,
        ext=island.ext,
    )
    m.Tile.objects.bulk_create(
        [
            m.Tile(island=row, x=t.x, y=t.y, terrain=t.terrain, population=t.population, ext=t.ext)
            for t in island.tiles
        ],
        batch_size=BATCH,
    )
    return row


def _island_values(island: IslandState) -> tuple[Any, ...]:
    return (island.name, island.wx, island.wy, _freeze_json(island.ext))


def _tile_values(tile: Tile) -> tuple[Any, ...]:
    return (tile.terrain, tile.population, _freeze_json(tile.ext))


def _order_values(order: OrderState) -> tuple[Any, ...]:
    return (order.status, order.reason, order.executed_turn)


def _freeze_json(value: Any) -> Any:
    """拡張欄の比較用。入れ子の dict や list も比べられる形にする。"""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze_json(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_json(v) for v in value)
    return value
