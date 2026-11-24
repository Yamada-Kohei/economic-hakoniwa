"""pytest の差し込み（-p fuel_store_plugin）: 関門の試験の島 1 の、毎ターンの燃料の国の倉庫の荷・値段の比・
取引量と、倉庫に燃料のあるターンの前後の島の記録の種類を fuel-store.jsonl に書く（読むだけ、世界は変えない）。"""

import json
from collections import Counter

from hakoniwa_core import gate, stock

_orig = gate.Recorder.record
_out = open("fuel-store.jsonl", "w", encoding="utf-8")  # noqa: SIM115


def _record(self, world):
    _orig(self, world)
    island = world.islands[min(world.islands)]
    held = stock.total(island.warehouse, "fuel")
    market = next((m for m in island.markets if m.good == "fuel"), None)
    rec = self.islands.get(island.id)
    ratio = rec.price_ratio.get("fuel", {}).get(world.turn) if rec is not None else None
    row = {"t": world.turn, "held": held, "ratio": ratio,
           "volume": getattr(market, "volume", None), "price": getattr(market, "price", None)}
    if held > 0 or world.turn in _near:
        row["log"] = dict(Counter(e.kind for e in island.log))
        row["fuel_log"] = [{"kind": e.kind, "args": {k: str(v) for k, v in e.args.items()}}
                           for e in island.log if "fuel" in json.dumps(e.args, default=str)][:20]
    if held > 0:
        _near.update({world.turn + 1, world.turn + 2})
    _out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    _out.flush()


_near = set()
gate.Recorder.record = _record
