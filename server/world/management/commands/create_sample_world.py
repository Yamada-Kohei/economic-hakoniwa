import random

from django.core.management.base import BaseCommand

from server.world.services import create_sample_island, get_or_create_world


class Command(BaseCommand):
    help = "開発・試験用に、世界とランダムな形の島を作る"

    def add_arguments(self, parser):
        parser.add_argument("--islands", type=int, default=3, help="作る島の数")
        parser.add_argument("--seed", type=int, default=None, help="乱数の種")

    def handle(self, *args, islands: int, seed: int | None, **options):
        world = get_or_create_world(seed)
        rng = random.Random(seed)
        start = world.islands.count()
        for i in range(islands):
            create_sample_island(world, f"サンプル島{start + i + 1}", rng)
        self.stdout.write(f"世界（ターン {world.turn}）に島を {islands} 個作りました")
