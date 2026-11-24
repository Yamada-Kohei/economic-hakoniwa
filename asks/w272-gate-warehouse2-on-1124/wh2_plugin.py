"""pytest の差し込み（-p wh2_plugin）: 関門の試験 test_supply_chain_runs_for_300_turns の生の記録を
wh2-raw.json に書く（読むだけ。世界も試験の判定も変えない。会議 #4429・#4433）。

書く物:
- 毎ターン（島 1）: 商人の品目ごとの国の倉庫の荷（total）・売りに出せる荷（sellable: 倉庫の放出の決まりと
  同じく、放出が止められていれば 0、工事の資材・国の穀物の備蓄・定めた売り買いの品目は 0、軍の取り置きを
  引く）・軍の取り置き（keep）・売り手ごとの約定の量（倉庫から出た量は売り手 warehouse）・food.ratio
- 試験の offered・needed（商人の売りの予定と島の施設が全力で要る量。_shortage_from の引数をそのまま）・leave
- 関門の記録の島 1 の値段の比（price_ratio）、財ごとに作られたターン（made）
"""

import json
import os

from hakoniwa_core import autoplan, gate, stock
from hakoniwa_core.modules.market import Market, standing

_state = {"rows": []}
_init, _record = gate.Recorder.__init__, gate.Recorder.record


def _sellable(island, defs, config, good, keep):
    if not island.policy.warehouse_release:
        return 0
    if good in Market._construction_goods(defs) or good == config.get("food_reserve")["good"]:
        return 0
    if good in standing.settings(island):
        return 0
    return max(0, stock.total(island.warehouse, good) - keep.get(good, 0))


def init(self, world, defs, config, *a, **k):
    _init(self, world, defs, config, *a, **k)
    _state.update(rec=self, defs=defs, config=config)


def record(self, world):
    _record(self, world)
    defs, config = _state["defs"], _state["config"]
    island = world.islands[min(world.islands)]
    goods = config.get("foreign_merchant")["goods"]
    keep = autoplan._military_keep(island, config)
    sold = {}
    for t in island.trades:
        if t.good in goods:
            sold.setdefault(t.good, {}).setdefault(t.seller.split(":")[0], 0)
            sold[t.good][t.seller.split(":")[0]] += t.quantity
    _state["rows"].append({
        "t": world.turn,
        "total": {g: stock.total(island.warehouse, g) for g in goods},
        "sellable": {g: _sellable(island, defs, config, g, keep) for g in goods},
        "keep": {g: keep.get(g, 0) for g in goods if keep.get(g, 0)},
        "sold": sold,
        "food_ratio": island.stats.get("food.ratio", 1.0),
    })


gate.Recorder.__init__ = init
gate.Recorder.record = record


def pytest_runtest_setup(item):
    mod = item.module
    if hasattr(mod, "_shortage_from") and not hasattr(mod, "_wh2_orig"):
        mod._wh2_orig = mod._shortage_from

        def wrapped(offered, needed, good, leave):
            _state.update(offered=offered, needed=needed, leave=leave)
            return mod._wh2_orig(offered, needed, good, leave)

        mod._shortage_from = wrapped


def pytest_runtest_logreport(report):
    if report.when == "call":
        _state["outcome"] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    rec = _state.get("rec")
    if rec is None:
        return
    config = _state["config"]
    merchant = config.get("foreign_merchant")
    island_rec = rec.islands[min(rec.islands)]
    out = {
        "outcome": _state.get("outcome"),
        "rows": _state["rows"],
        "offered": {str(t): v for t, v in _state.get("offered", {}).items()},
        "needed": {str(t): v for t, v in _state.get("needed", {}).items()},
        "leave": _state.get("leave"),
        "tail": (merchant["turns"] - merchant["taper_from"]) // 10,
        "start": gate.START,
        "price_range": list(gate.PRICE_RANGE),
        "price_ratio": {g: {str(t): r for t, r in v.items()} for g, v in island_rec.price_ratio.items()},
        "made": {g: sorted(v)[:1] for g, v in rec.made.items()},
        "farming_by_housing": config.get("population.farming_by_housing"),
        "upgrade_without_subsistence": config.get("housing.upgrade_without_subsistence"),
    }
    with open(os.environ.get("W272_WH2_OUT", "wh2-raw.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
