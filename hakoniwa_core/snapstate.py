"""測りの途中の値を、型と並びを保ったまま JSON の形へ写す（C3 W-236 H-3）。

quick の最後の写しから full の残りを回すとき、関門の記録（gate.Recorder）や測りの足し上げ
（measure.Measure）を写しに書いて、別のプロセスで元どおりに戻すのに使います。JSON は tuple を
list に、数字の鍵を文字に変えてしまうので、型の分かる印（"!"）を付けて書きます。

写せるのは、ここに並べた型そのものだけです。defaultdict や名前付きの tuple のような派生の型は、
戻したときに元の型でなくなるので、黙って写さずに TypeError にします（欄を足した人がその場で
気付く）。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Any


def encode(value: Any, classes: Sequence[type] = ()) -> Any:
    """値を JSON に書ける形へ直す。集合の中身は毎回同じ順に並べる。"""
    kind = type(value)
    if value is None or kind in (bool, int, float, str):
        return value
    if kind is list:
        return [encode(v, classes) for v in value]
    if kind is tuple:
        return {"!": "tuple", "v": [encode(v, classes) for v in value]}
    if kind in (set, frozenset):
        items = [encode(v, classes) for v in value]
        items.sort(key=lambda v: json.dumps(v, sort_keys=True))
        return {"!": kind.__name__, "v": items}
    if kind is Counter:
        return {
            "!": "counter",
            "v": [[encode(k, classes), encode(v, classes)] for k, v in value.items()],
        }
    if kind is dict:
        if all(type(k) is str for k in value) and "!" not in value:
            return {k: encode(v, classes) for k, v in value.items()}
        return {
            "!": "dict",
            "v": [[encode(k, classes), encode(v, classes)] for k, v in value.items()],
        }
    if kind in classes and is_dataclass(value):
        return {
            "!": "data",
            "t": kind.__name__,
            "v": {f.name: encode(getattr(value, f.name), classes) for f in fields(value)},
        }
    raise TypeError(f"写せない型です: {kind.__name__}")


def decode(data: Any, classes: Sequence[type] = ()) -> Any:
    """JSON から読んだ値を、写す前の型と並びへ戻す。"""
    if data is None or isinstance(data, bool):
        return data
    if isinstance(data, (int, float, str)):
        return data
    if isinstance(data, list):
        return [decode(v, classes) for v in data]
    if not isinstance(data, dict):
        raise TypeError(f"戻せない型です: {type(data).__name__}")
    if "!" not in data:
        return {k: decode(v, classes) for k, v in data.items()}
    kind = data["!"]
    if kind == "tuple":
        return tuple(decode(v, classes) for v in data["v"])
    if kind == "set":
        return {decode(v, classes) for v in data["v"]}
    if kind == "frozenset":
        return frozenset(decode(v, classes) for v in data["v"])
    if kind in ("counter", "dict"):
        values = {decode(k, classes): decode(v, classes) for k, v in data["v"]}
        return Counter(values) if kind == "counter" else values
    if kind == "data":
        name = data["t"]
        cls = next((c for c in classes if c.__name__ == name and is_dataclass(c)), None)
        if cls is None:
            raise ValueError(f"知らないデータクラスです: {name}")
        return cls(**{k: decode(v, classes) for k, v in data["v"].items()})
    raise ValueError(f"知らない写しの種類です: {kind}")


def dump(obj: object, skip: Collection[str] = (), classes: Sequence[type] = ()) -> dict[str, Any]:
    """除く欄以外を、実体に入っている順で写す。"""
    return {name: encode(value, classes) for name, value in vars(obj).items() if name not in skip}


def restore(obj: object, data: Mapping[str, Any], classes: Sequence[type] = ()) -> None:
    """写しにある欄だけを戻し、ほかの欄はそのままにする。"""
    for name, value in data.items():
        setattr(obj, name, decode(value, classes))
