"""勘定の残高の表（IslandState.accounts）。

ふつうの dict（勘定の名前 → 残高）と同じに使えます。そのうえで、名前「持ち手/持ち物/出し手」の
持ち手ごと・持ち物ごと・持ち手と持ち物の組ごとに、名前の索引をいっしょに持ちます。持ち手の預金や、
国債の銘柄の持ち手を引くときに、帳簿の勘定を全部なめずに済ませるためです（ゲームが進むと国債の
銘柄も勘定も増え、全部なめると後のターンほど遅くなる）。

索引は、勘定を足す・消すどの書き方（[]・del・pop・update など）でもいっしょに直ります。索引の
名前の並びは、表そのものの並び（入れた順）と同じです。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_EMPTY: dict[str, None] = {}


def _keys(name: str) -> tuple[str, str, str]:
    """索引の鍵: 持ち手・持ち物・「持ち手/持ち物」。"""
    holder, _, rest = name.partition("/")
    item = rest.partition("/")[0]
    return holder, item, f"{holder}/{item}"


class Accounts(dict):
    """勘定の名前 → 残高。持ち手・持ち物・その組ごとの名前の索引を持つ dict。"""

    __slots__ = ("_by_holder", "_by_item", "_by_pair")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._by_holder: dict[str, dict[str, None]] = {}
        self._by_item: dict[str, dict[str, None]] = {}
        self._by_pair: dict[str, dict[str, None]] = {}
        if args or kwargs:
            self.update(*args, **kwargs)

    # --- 索引 -----------------------------------------------------------------

    def of_holder(self, holder: str) -> Iterable[str]:
        """持ち手 holder の勘定の名前（表の並び）。"""
        return self._by_holder.get(holder, _EMPTY).keys()

    def of_item(self, item: str) -> Iterable[str]:
        """持ち物 item の、持ち手の側の勘定の名前（表の並び）。"""
        return self._by_item.get(item, _EMPTY).keys()

    def of_pair(self, holder: str, item: str) -> Iterable[str]:
        """持ち手 holder の持ち物 item の勘定の名前（出し手ごと。表の並び）。"""
        return self._by_pair.get(f"{holder}/{item}", _EMPTY).keys()

    def _indexes(self, name: str) -> tuple[tuple[dict[str, dict[str, None]], str], ...]:
        holder, item, pair = _keys(name)
        return ((self._by_holder, holder), (self._by_item, item), (self._by_pair, pair))

    def _add(self, name: str) -> None:
        for index, key in self._indexes(name):
            names = index.get(key)
            if names is None:
                index[key] = names = {}
            names[name] = None

    def _drop(self, name: str) -> None:
        for index, key in self._indexes(name):
            names = index[key]
            del names[name]
            if not names:
                del index[key]

    # --- dict の書き換え（索引もいっしょに直す） --------------------------------

    def __setitem__(self, name: str, value: int) -> None:
        if name not in self:
            self._add(name)
        super().__setitem__(name, value)

    def __delitem__(self, name: str) -> None:
        super().__delitem__(name)
        self._drop(name)

    def pop(self, name: str, *default: Any) -> Any:
        if name in self:
            value = super().pop(name)
            self._drop(name)
            return value
        if default:
            return default[0]
        raise KeyError(name)

    def popitem(self) -> tuple[str, int]:
        name, value = super().popitem()
        self._drop(name)
        return name, value

    def setdefault(self, name: str, default: Any = None) -> Any:
        if name not in self:
            self[name] = default
        return self[name]

    def update(self, *args: Any, **kwargs: Any) -> None:
        for name, value in dict(*args, **kwargs).items():
            self[name] = value

    def __ior__(self, other: Any) -> Accounts:
        self.update(other)
        return self

    def clear(self) -> None:
        super().clear()
        self._by_holder.clear()
        self._by_item.clear()
        self._by_pair.clear()

    # --- 写し（索引は作り直す） ------------------------------------------------

    def copy(self) -> Accounts:
        return Accounts(self)

    def __copy__(self) -> Accounts:
        return Accounts(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> Accounts:
        return Accounts(self)  # 名前は文字列、残高は整数なので、中身は写さなくてよい

    def __reduce__(self) -> tuple[Any, ...]:
        return (Accounts, (dict(self),))
