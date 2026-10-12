"""読み込んだ定義を書き換えられない形にする。"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """JSON にするときなどのために、普通の dict と list に戻す。"""
    if isinstance(value, MappingProxyType | dict):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [thaw(v) for v in value]
    return value
