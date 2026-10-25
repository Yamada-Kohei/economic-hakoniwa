"""部隊・艦隊・基地・占領・反乱軍・条約のある世界（段6 B4）。

全画面を開く試験（tests/server/test_all_screens.py）と、ブラウザで画面を開く道具
（tools/screens/）が同じ世界を使う。核の試験で使う道具（tests/core/ の部隊・艦隊・戦争・反乱の
置き方）をそのまま借り、DB に書いた世界を読み込んで部隊や反乱軍を足し、書き戻す。

島の種類（`build` が返す名前 → 島の番号。島ごとに持ち主のアカウント owner-<名前> がある）:

- player: 島 1。市場経済・議会のない体制。兵舎・軍港・ミサイル基地・隠した核施設・部隊・艦隊・
  塹壕・反乱軍。
- rival: 島 2。計画経済。島 1 と戦争中で、島 1 のマスを占領していて、部隊もいる。
- market: 島 3。警察がなく、反乱が革命になって体制の選び直し待ち。
- bankless: 島 4。銀行を 1 つも持たない島。
- fresh: 建国直後（ターン 0 のまま、1 度も更新されていない）の島。
- empty: 人がいなくなった島（人口 0・施設は残る）。
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection

from hakoniwa_core import politics
from hakoniwa_core.military import army, civil_war, forces
from hakoniwa_core.state import Facility
from server.game import get_game
from server.turns.services import run_turn
from server.world.repository import WorldRepository
from server.world.services import create_sample_island, get_or_create_world
from tests.core.test_civil_war import ctx_of, unrest
from tests.core.test_military_fleets import put_port, ready_fleet
from tests.core.test_military_units import ready_unit
from tests.core.test_military_war import feed, put_unit, start_war

PASSWORD = "a-long-pass-123"


class _Null:
    def write(self, *_):
        pass


def _restart_island_numbers() -> None:
    """島の番号を 1 から振り直す（部隊や戦争の置き方の道具は島 1・島 2 と決めて書いてある。
    PostgreSQL は試験の巻き戻しでも番号の数え方が戻らない）。"""
    with connection.cursor() as cursor:
        for sql in connection.ops.sequence_reset_by_name_sql(
            no_style(), [{"table": "world_island", "column": "id"}]
        ):
            cursor.execute(sql)


def make_user(name: str) -> User:
    user, _ = User.objects.get_or_create(username=name)
    user.set_password(PASSWORD)
    user.save()
    return user


def _facility(island, kind: str, tile, **ext) -> Facility:
    f = Facility(id=max([f.id for f in island.facilities] + [0]) + 1, kind=kind, x=tile.x, y=tile.y)
    f.built = True
    f.ext.update(ext)
    island.facilities.append(f)
    return f


def _free_plains(island, count: int, skip=()):
    used = {(f.x, f.y) for f in island.facilities} | set(skip)
    out = [
        t
        for t in island.tiles
        if t.terrain == "plain" and t.population == 0 and (t.x, t.y) not in used
    ]
    return out[:count]


def build(seed: int = 3, warm_turns: int = 3, war_turns: int = 4) -> dict[str, int]:
    """世界を作って返す（島の種類 → 島の番号）。DB の中で呼ぶ。"""
    from server.world.models import World

    _restart_island_numbers()
    call_command("create_sample_world", islands=4, seed=seed, stdout=_Null())
    world = World.objects.get()
    islands = list(world.islands.order_by("id"))
    player, rival, market, bankless = (i.id for i in islands)
    for island, kind in zip(islands, ("player", "rival", "market", "bankless"), strict=True):
        island.owner = make_user(f"owner-{kind}")
        island.save()
    for _ in range(warm_turns):
        run_turn(force=True)

    game = get_game()
    repo = WorldRepository(World.objects.get())
    state = repo.load()
    p, r = state.islands[player], state.islands[rival]
    p.regime["politics"] = "authoritarian"
    r.regime["economy"] = "planned"
    for island in state.islands.values():
        forces.state_of(island, game.config)["morale"] = 7000
        forces.state_of(island, game.config)["training"] = 6000

    # 銀行を持たない島（銀行は金融の施設。島の番号 4 から外す）
    bank_kinds = {k for k in game.defs.facilities if k.endswith("bank")}
    bankless_island = state.islands[bankless]
    bankless_island.facilities[:] = [
        f for f in bankless_island.facilities if f.kind not in bank_kinds
    ]

    # 部隊・艦隊・基地（島 1）
    ready_unit(game, state, "infantry")
    ready_unit(game, state, "armor")
    ready_fleet(game, state, kind="frigate", ships=1)
    ready_fleet(game, state, kind="transport", ships=1)
    port = put_port(p)
    trench, missiles, nuke, hidden_missiles = _free_plains(p, 4, skip={(port.x, port.y)})
    trench.ext[army.TRENCH] = 1
    _facility(p, "missile_base", missiles)
    _facility(p, "nuclear_facility", nuke, hidden=True)
    _facility(p, "missile_base", hidden_missiles, hidden=True)

    # 島 2 の部隊と、島 1 との戦争・占領
    a, b = _free_plains(r, 2)
    put_unit(game, state, 2, "infantry", a.x, a.y)
    start_war(game, state)
    farm = next(
        f for f in r.facilities if f.built and f.kind != "house" and (f.x, f.y) != r.capital
    )
    occupier = put_unit(game, state, 1, "infantry", farm.x, farm.y, at=2)
    occupier["entered"] = state.turn + 1
    for _ in range(war_turns):
        feed(game, state)
        game.engine.run_turn(state)

    # 島 3: 警察のない島で反乱が起き、革命で体制の選び直し待ちになる
    m3 = state.islands[market]
    m3.regime["politics"] = "authoritarian"
    m3.facilities[:] = [f for f in m3.facilities if not game.defs.facilities[f.kind].get("police")]
    for _ in range(4 * game.config.get("military.civil_war.trigger_turns")):
        unrest(m3)
        feed(game, state)
        game.engine.run_turn(state)
        if "reselect" in politics.state_of(m3):
            break

    # 島 1: 反乱の前触れを続けて、反乱軍を立ち上げる（立ち上がったところで止めて、内戦の最中を残す）
    turns = game.config.get("military.civil_war.trigger_turns")
    for _ in range(turns):
        unrest(p)
        civil_war.run(state, ctx_of(game, state, state.turn + 1))
    repo.save(state)

    kinds = {"player": player, "rival": rival, "market": market, "bankless": bankless}

    # 建国直後（1 度も更新されていない）と、人のいなくなった島
    rng = __import__("random").Random(seed)
    fresh = create_sample_island(get_or_create_world(), "建国直後島", rng, make_user("owner-fresh"))
    kinds["fresh"] = fresh.id
    emptied = create_sample_island(get_or_create_world(), "無人島", rng, make_user("owner-empty"))
    emptied.tiles.update(population=0)
    kinds["empty"] = emptied.id
    return kinds
