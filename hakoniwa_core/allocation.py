"""足りない物を、欲しい量に比例して分ける。配分の段と送電網で使います。

フェーズ2で板寄せの市場に差し替えたあとも、計画経済の割当ルールとして使い続けます。
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def proportional[K: Hashable](amount: int, requests: Sequence[tuple[K, int]]) -> dict[K, int]:
    """amount を requests（(相手, 欲しい量) を優先する順に並べたもの）に比例して分ける。

    足りていれば欲しい量をそのまま返す。割り切れない余りは、並びの前の相手から1ずつ足す。
    """
    total = sum(r for _, r in requests)
    if total <= amount:
        return {k: r for k, r in requests}
    alloc = {k: r * amount // total for k, r in requests}
    rest = amount - sum(alloc.values())
    for k, r in requests:
        if rest <= 0:
            break
        if alloc[k] < r:
            alloc[k] += 1
            rest -= 1
    return alloc


def by_priority[K: Hashable](amount: int, requests: Sequence[tuple[K, int, int]]) -> dict[K, int]:
    """requests（(相手, 欲しい量, 優先順位)）に、優先順位の高い組から配る。

    同じ優先順位の組の中で足りなければ、欲しい量に比例して分ける。並びは渡された順を保つ。
    """
    alloc: dict[K, int] = {}
    left = amount
    for priority in sorted({p for _, _, p in requests}, reverse=True):
        group = [(k, r) for k, r, p in requests if p == priority]
        got = proportional(left, group)
        alloc.update(got)
        left -= sum(got.values())
    return alloc
