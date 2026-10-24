"""リクエストの本文の JSON を読む。

Python の json は NaN・Infinity・-Infinity と、1e999 のように大きすぎて inf になる数を黙って
数として読むが、JSON の決まりにはなく、PostgreSQL の JSONB にも入らない。入り口で断って、
保存の途中で 500 にならないようにする。
読めないものは ValueError にする（JSON として壊れている場合も同じ）。
"""

from __future__ import annotations

import json
import math
from typing import Any


def _reject(name: str) -> Any:
    raise ValueError(f"JSON に {name} は書けません")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 1e999 のような、大きすぎて inf になる数
        raise ValueError("JSON の数が大きすぎます")
    return value


def loads(raw: bytes | str) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject, parse_float=_finite)
    except RecursionError as e:  # 深く入れ子にした本文
        raise ValueError("JSON の入れ子が深すぎます") from e
    except UnicodeDecodeError as e:
        raise ValueError("JSON を UTF-8 として読めません") from e
