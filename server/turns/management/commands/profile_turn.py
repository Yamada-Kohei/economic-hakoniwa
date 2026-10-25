"""ターン更新の時間を測る（W-35）。世界の DB の写しに向けて使う。

読み込み・バックアップ・計算の段ごと・書き戻しの秒と、世界の大きさ（施設・在庫の山・国債・
JSON の長さなど）を出す。--profile で cProfile の上位も出す。--keep を付けなければ、
1ターンごとに書いたものを全部取り消す（DB は変わらない）。動いているサーバーの DB には
向けないこと（--keep なしでも更新中の鎖を取る）。

    DATABASE_URL=sqlite:////path/to/copy.sqlite3 python manage.py profile_turn --turns 3 --profile

公開の大きさの世界を長く進めたときの重さを見るには、試しの世界を作って標準の計画で進める:

    python manage.py create_sample_world --islands 40
    python manage.py profile_turn --turns 600 --keep --autoplan --detail-every 60 --profile
"""

from __future__ import annotations

import cProfile
import io
import json
import pstats
import time
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hakoniwa_core import measure
from hakoniwa_core.state.serialize import world_to_dict
from server.game import get_game
from server.orders.models import Order
from server.turns import services
from server.world.repository import WorldRepository


class Command(BaseCommand):
    help = "世界の DB の写しでターン更新を回し、どこに時間がかかるかを出す"

    def add_arguments(self, parser):
        parser.add_argument("--turns", type=int, default=1, help="続けて進めるターンの数")
        parser.add_argument("--keep", action="store_true", help="進めたターンを DB に残す")
        parser.add_argument("--profile", action="store_true", help="cProfile の上位も出す")
        parser.add_argument("--top", type=int, default=40, help="cProfile で出す行の数")
        parser.add_argument(
            "--autoplan",
            action="store_true",
            help="毎ターン標準の計画の命令を並べる（命令の行がない、試しに作った世界だけ）",
        )
        parser.add_argument(
            "--detail-every",
            type=int,
            default=1,
            help="世界の大きさと cProfile を出す間隔（ターン）。ほかのターンは1行だけ",
        )

    def handle(self, *args, turns, keep, profile, top, autoplan, detail_every, **options):
        game = get_game()
        if autoplan and Order.objects.exists():
            raise CommandError("--autoplan は命令の行がない、試しに作った世界の写しだけで使えます")
        carried: dict[int, tuple] = {}  # 標準の計画が読む、前のターンの統計と市場の記録
        for n in range(turns):
            detail = (n + 1) % detail_every == 0 or n == turns - 1
            prof = cProfile.Profile() if profile and detail else None
            with transaction.atomic():
                world = services.get_world()
                times: dict[str, float] = {}
                if prof:
                    prof.enable()
                t = time.perf_counter()
                repo = WorldRepository(world)
                state = repo.load()
                times["load"] = time.perf_counter() - t
                if autoplan:
                    for island in state.islands.values():
                        island.stats, island.markets = carried.get(island.id, ({}, []))
                    t = time.perf_counter()
                    measure.queue_scenario_orders(
                        state, measure.SCENARIOS["plan"], game.defs, game.config
                    )
                    times["autoplan"] = time.perf_counter() - t
                t = time.perf_counter()
                services._check_defs_cover(state)
                services._make_backup(world, state)
                times["backup"] = time.perf_counter() - t
                t = time.perf_counter()
                services._start_economies(state)
                report = game.engine.run_turn(state)
                times["engine"] = time.perf_counter() - t
                t = time.perf_counter()
                repo.save(state)
                times["save"] = time.perf_counter() - t
                if prof:
                    prof.disable()
                if not keep:
                    transaction.set_rollback(True)
            if autoplan:
                carried = {i.id: (dict(i.stats), list(i.markets)) for i in state.islands.values()}
            if detail:
                self._report(state, times, report.seconds, prof, top)
            else:
                self._line(state, times)

    def _line(self, state, times):
        total = sum(v for k, v in times.items() if k != "autoplan")
        people = sum(t.population for i in state.islands.values() for t in i.tiles)
        facilities = sum(len(i.facilities) for i in state.islands.values())
        parts = "  ".join(f"{k} {v:.2f}" for k, v in times.items())
        self.stdout.write(
            f"ターン {state.turn}  合計 {total:.2f} 秒  {parts}  人口 {people:,}  施設 {facilities}"
        )

    def _report(self, state, times, stages, prof, top):
        out = self.stdout.write
        total = sum(v for k, v in times.items() if k != "autoplan")
        people = sum(t.population for i in state.islands.values() for t in i.tiles)
        out(f"== ターン {state.turn}  合計 {total:.2f} 秒（標準の計画を並べる時間は除く）")
        out(f"  人口 {people:,}")
        out("  " + "  ".join(f"{k} {v:.2f}" for k, v in times.items()))
        out("  計算の段（秒、0.05 以上）:")
        for name, sec in sorted(stages.items(), key=lambda kv: -kv[1]):
            if sec >= 0.05:
                out(f"    {name:24s} {sec:7.2f}")
        out("  世界の大きさ:")
        out(f"    島 {len(state.islands)}")
        for island in state.islands.values():
            lots = sum(len(v) for v in island.warehouse.values()) + sum(
                len(v) for f in island.facilities for v in f.stock.values()
            )
            kinds = Counter(f.kind for f in island.facilities)
            sizes = {
                name: len(json.dumps(getattr(island, name), default=str, ensure_ascii=False))
                for name in ("ext", "regime")
            }
            econ = len(json.dumps(world_to_dict_part(island.economy), ensure_ascii=False))
            out(
                f"    島 {island.id}: 施設 {len(island.facilities)}（種類 {len(kinds)}）"
                f" 在庫の山 {lots} 国債 {len(island.bonds)} 融資 {len(island.loans)}"
                f" 命令 {len(island.orders)} 仕訳 {len(island.journal)}"
                f" 取引 {len(island.trades)} 帳簿 {len(island.books)}"
                f" ext {sizes['ext']}字 regime {sizes['regime']}字 economy {econ}字"
            )
        data = json.dumps(world_to_dict(state), ensure_ascii=False)
        out(f"    世界の JSON {len(data):,} 字、ext {len(json.dumps(state.ext, default=str)):,} 字")
        if prof:
            buf = io.StringIO()
            stats = pstats.Stats(prof, stream=buf).sort_stats("cumulative")
            stats.print_stats(top)
            buf.write("\n--- 自分の時間の上位 ---\n")
            stats.sort_stats("tottime").print_stats(top)
            out(buf.getvalue())


def world_to_dict_part(value):
    from dataclasses import asdict, is_dataclass

    return asdict(value) if is_dataclass(value) else value
