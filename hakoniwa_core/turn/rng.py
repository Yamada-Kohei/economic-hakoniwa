"""乱数。世界の種・ターン番号・段の名前・島の番号から作るので、
処理の順番や島の数が変わっても、同じ入力から同じ結果が出ます。"""

from __future__ import annotations

import hashlib
import random


def make_rng(seed: int, turn: int, stage: str, island_id: int | None = None) -> random.Random:
    key = f"{seed}:{turn}:{stage}:{'' if island_id is None else island_id}"
    digest = hashlib.sha256(key.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
