"""DB とコアの状態（hakoniwa_core.state）の変換。

ターン更新では load() で世界全体をメモリに読み込み、コアで計算し、
save() で変わったものだけを書き戻す。トランザクションは呼ぶ側が張る。

在庫（倉庫と施設の手持ち）は毎ターンほとんど変わるので、島ごとに消して書き直す。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from hakoniwa_core.state import Facility, IslandState, OrderState, Tile, WorldState
from server.orders.models import Order
from server.turns.models import TurnLog
from server.world import models as m
from server.world.bulk import update_rows

BATCH = 2000

ISLAND_FIELDS = [
    "name",
    "wx",
    "wy",
    "capital_x",
    "capital_y",
    "resources",
    "regime",
    "names",
    "ext",
]
TILE_FIELDS = ["terrain", "population", "zone", "infra", "pollution", "ext"]
FACILITY_FIELDS = [
    "kind",
    "x",
    "y",
    "recipe",
    "owner",
    "accounting",
    "name",
    "built",
    "progress",
    "workers",
    "operating_rate",
    "priority",
    "status",
    "ext",
]
ORDER_FIELDS = ["status", "reason", "reason_args", "executed_turn"]


class WorldRepository:
    def __init__(self, world: m.World):
        self.world = world
        # 読み込んだときの値。save() で変わったものだけを書くために覚えておく。
        self._tiles: dict[tuple[int, int, int], tuple[int, tuple[Any, ...]]] = {}
        self._islands: dict[int, tuple[Any, ...]] = {}
        self._facilities: dict[tuple[int, int], tuple[int, tuple[Any, ...]]] = {}
        self._orders: dict[int, tuple[Any, ...]] = {}

    # --- 読み込み ------------------------------------------------------------

    def load(self, orders_until: datetime | None = None) -> WorldState:
        """世界全体を読み込む。orders_until より後に届いた命令は次のターンに回す。"""
        islands: dict[int, IslandState] = {}
        for row in self.world.islands.order_by("id"):
            islands[row.id] = island_from_row(row)
            self._islands[row.id] = _values(islands[row.id], ISLAND_FIELDS)

        tiles = m.Tile.objects.filter(island__world=self.world).values_list(
            "id", "island_id", "x", "y", *TILE_FIELDS
        )
        for pk, island_id, x, y, *rest in tiles.iterator(chunk_size=BATCH):
            island = islands[island_id]
            tile = Tile(x, y, **dict(zip(TILE_FIELDS, rest, strict=True)))
            island.tiles[y * island.size + x] = tile
            self._tiles[(island_id, x, y)] = (pk, _values(tile, TILE_FIELDS))

        by_pk: dict[int, Facility] = {}
        rows = m.Facility.objects.filter(island__world=self.world).order_by("island_id", "number")
        for row in rows.iterator(chunk_size=BATCH):
            f = facility_from_row(row)
            islands[row.island_id].facilities.append(f)
            by_pk[row.pk] = f
            self._facilities[(row.island_id, row.number)] = (row.pk, _values(f, FACILITY_FIELDS))

        stocks = m.Stock.objects.filter(island__world=self.world).order_by(
            "island_id", "facility_id", "good", "made_turn", "id"
        )
        for island_id, facility_id, good, made, qty in stocks.values_list(
            "island_id", "facility_id", "good", "made_turn", "quantity"
        ).iterator(chunk_size=BATCH):
            if facility_id is None:
                islands[island_id].warehouse.setdefault(good, []).append([made, qty])
            else:
                f = by_pk[facility_id]
                f.stock[good] = f.stock.get(good, 0) + qty

        orders = Order.objects.filter(island__world=self.world, status="pending")
        if orders_until is not None:
            orders = orders.filter(created_at__lte=orders_until)
        for o in orders.order_by("island_id", "seq", "id"):
            state = order_from_row(o)
            islands[o.island_id].orders.append(state)
            self._orders[o.id] = _values(state, ORDER_FIELDS)

        for island in islands.values():
            if any(t is None for t in island.tiles):
                raise ValueError(f"島 {island.id} のマスがそろっていません")

        return WorldState(
            turn=self.world.turn, seed=self.world.seed, islands=islands, ext=self.world.ext
        )

    # --- 書き戻し ------------------------------------------------------------

    def save(self, state: WorldState) -> None:
        """変わったものだけを書き戻し、ターンログと統計を足す。"""
        islands = list(state.islands.values())
        update_rows(
            m.Island,
            [
                island_row(i)
                for i in islands
                if _values(i, ISLAND_FIELDS) != self._islands.get(i.id)
            ],
            ISLAND_FIELDS,
        )

        changed_tiles = []
        for island in islands:
            for tile in island.tiles:
                pk, before = self._tiles[(island.id, tile.x, tile.y)]
                if _values(tile, TILE_FIELDS) != before:
                    changed_tiles.append(tile_row(tile, pk=pk))
        update_rows(m.Tile, changed_tiles, TILE_FIELDS)

        self._save_facilities(islands)
        write_stocks(islands, replace=True)

        changed_orders = [
            order_row(o)
            for island in islands
            for o in island.orders
            if _values(o, ORDER_FIELDS) != self._orders.get(o.id)
        ]
        update_rows(Order, changed_orders, ORDER_FIELDS)

        TurnLog.objects.bulk_create(
            [
                TurnLog(island_id=island.id, turn=state.turn, kind=e.kind, args=e.args)
                for island in islands
                for e in island.log
            ],
            batch_size=BATCH,
        )
        m.StatRecord.objects.bulk_create(
            [
                m.StatRecord(island_id=island.id, turn=state.turn, name=name, value=value)
                for island in islands
                for name, value in sorted(island.stats.items())
            ],
            batch_size=BATCH,
        )

        self.world.turn = state.turn
        self.world.ext = state.ext
        self.world.save(update_fields=["turn", "ext"])

    def _save_facilities(self, islands: list[IslandState]) -> None:
        seen = set()
        changed, created = [], []
        for island in islands:
            for f in island.facilities:
                key = (island.id, f.id)
                seen.add(key)
                if key not in self._facilities:
                    created.append(facility_row(island.id, f))
                    continue
                pk, before = self._facilities[key]
                if _values(f, FACILITY_FIELDS) != before:
                    changed.append(facility_row(island.id, f, pk=pk))
        removed = [pk for key, (pk, _) in self._facilities.items() if key not in seen]
        # 取り壊したマスに同じターンで建てることもあるので、消す → 書き換え → 作るの順にする
        m.Facility.objects.filter(pk__in=removed).delete()
        update_rows(m.Facility, changed, FACILITY_FIELDS)
        m.Facility.objects.bulk_create(created, batch_size=BATCH)


# --- 行とコアの状態の変換 -------------------------------------------------------


def island_from_row(row: m.Island) -> IslandState:
    return IslandState(
        id=row.id,
        name=row.name,
        wx=row.wx,
        wy=row.wy,
        size=row.size,
        tiles=[None] * (row.size * row.size),  # type: ignore[list-item]
        capital_x=row.capital_x,
        capital_y=row.capital_y,
        resources=list(row.resources),
        regime=dict(row.regime),
        names=dict(row.names),
        ext=row.ext,
    )


def island_row(island: IslandState) -> m.Island:
    return m.Island(id=island.id, **{name: getattr(island, name) for name in ISLAND_FIELDS})


def tile_row(tile: Tile, **extra: Any) -> m.Tile:
    return m.Tile(**extra, **{name: getattr(tile, name) for name in TILE_FIELDS})


def facility_from_row(row: m.Facility) -> Facility:
    return Facility(
        id=row.number, **{name: getattr(row, name) for name in FACILITY_FIELDS}, stock={}
    )


def facility_row(island_id: int, f: Facility, **extra: Any) -> m.Facility:
    values = {name: getattr(f, name) for name in FACILITY_FIELDS}
    return m.Facility(island_id=island_id, number=f.id, **values, **extra)


def order_from_row(o: Order) -> OrderState:
    return OrderState(
        id=o.id,
        seq=o.seq,
        kind=o.kind,
        payload=o.payload,
        status=o.status,
        reason=o.reason or None,
        reason_args=o.reason_args or {},
        executed_turn=o.executed_turn,
    )


def order_row(o: OrderState) -> Order:
    return Order(
        id=o.id,
        status=o.status,
        reason=o.reason or "",
        reason_args=o.reason_args,
        executed_turn=o.executed_turn,
    )


def write_stocks(islands: Iterable[IslandState], replace: bool) -> None:
    """島の倉庫と施設の在庫を書く。replace なら、その島の今の在庫の行を消してから書く。"""
    islands = list(islands)
    ids = [i.id for i in islands]
    if replace:
        m.Stock.objects.filter(island_id__in=ids).delete()
    facility_pk = {
        (island_id, number): pk
        for pk, island_id, number in m.Facility.objects.filter(island_id__in=ids).values_list(
            "id", "island_id", "number"
        )
    }
    rows = []
    for island in islands:
        for good in sorted(island.warehouse):
            for made, qty in island.warehouse[good]:
                rows.append(m.Stock(island_id=island.id, good=good, made_turn=made, quantity=qty))
        for f in island.facilities:
            for good in sorted(f.stock):
                rows.append(
                    m.Stock(
                        island_id=island.id,
                        facility_id=facility_pk[(island.id, f.id)],
                        good=good,
                        quantity=f.stock[good],
                    )
                )
    m.Stock.objects.bulk_create(rows, batch_size=BATCH)


def create_island(world: m.World, island: IslandState, owner=None) -> m.Island:
    """コアの島の状態から、新しい島を DB に作る（番号は DB が決める）。"""
    values = {name: getattr(island, name) for name in ISLAND_FIELDS}
    row = m.Island.objects.create(world=world, owner=owner, size=island.size, **values)
    island.id = row.id
    m.Tile.objects.bulk_create(
        [tile_row(t, island=row, x=t.x, y=t.y) for t in island.tiles], batch_size=BATCH
    )
    m.Facility.objects.bulk_create(
        [facility_row(row.id, f) for f in island.facilities], batch_size=BATCH
    )
    write_stocks([island], replace=False)
    TurnLog.objects.bulk_create(
        [TurnLog(island=row, turn=world.turn, kind=e.kind, args=e.args) for e in island.log]
    )
    return row


def replace_island_contents(islands: list[IslandState]) -> None:
    """巻き戻し用: 島・マス・施設・在庫を、渡した状態で丸ごと書き直す（命令は別に戻す）。"""
    update_rows(m.Island, [island_row(i) for i in islands], ISLAND_FIELDS)
    ids = [i.id for i in islands]
    tile_ids = {
        (island_id, x, y): pk
        for pk, island_id, x, y in m.Tile.objects.filter(island_id__in=ids).values_list(
            "id", "island_id", "x", "y"
        )
    }
    update_rows(
        m.Tile,
        [tile_row(t, id=tile_ids[(i.id, t.x, t.y)]) for i in islands for t in i.tiles],
        TILE_FIELDS,
    )
    m.Facility.objects.filter(island_id__in=ids).delete()  # 在庫の行も一緒に消える
    m.Stock.objects.filter(island_id__in=ids).delete()
    m.Facility.objects.bulk_create(
        [facility_row(i.id, f) for i in islands for f in i.facilities], batch_size=BATCH
    )
    write_stocks(islands, replace=False)


def load_island(row: m.Island) -> IslandState:
    """1つの島のマスと施設を読む（画面で命令を確かめる用。倉庫と命令は読まない）。"""
    island = island_from_row(row)
    for x, y, *rest in row.tiles.values_list("x", "y", *TILE_FIELDS):
        island.tiles[y * island.size + x] = Tile(x, y, **dict(zip(TILE_FIELDS, rest, strict=True)))
    island.facilities = [facility_from_row(f) for f in row.facilities.order_by("number")]
    return island


def _values(obj: Any, names: list[str]) -> tuple[Any, ...]:
    return tuple(_freeze_json(getattr(obj, name)) for name in names)


def _freeze_json(value: Any) -> Any:
    """比較用。入れ子の dict や list も比べられる形にする。"""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze_json(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_json(v) for v in value)
    return value
