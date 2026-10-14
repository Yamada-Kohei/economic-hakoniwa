"""港・海運業・商社が雇いたい人数（フェーズ3-1）。

この3つは定義の人数を全部は雇わず、扱った荷と航海に出ている船に合わせて雇います。雇用の段は雇う人数
に、生産の段は施設の状態（雇いたい分を雇えていれば「扱う荷に合わせて操業」）に使います。
"""

from __future__ import annotations

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility
from hakoniwa_core.trade import is_port, is_shipping, is_trader, ships, traders


def wanted(config: GameConfig, defs: Defs, f: Facility) -> int | None:
    """港・海運業・商社が雇いたい人数。ほかの施設と、建っていない施設は None。"""
    if not f.built:
        return None
    if is_port(config, f):  # 扱った荷の量に合わせて
        return ships.port_workers(config, defs, f)
    if is_shipping(config, f):  # 船の数に合わせて船員を
        return ships.crew_wanted(config, f)
    if is_trader(config, f):  # 扱った荷の量に合わせて
        return traders.workers_wanted(config, defs, f)
    return None
