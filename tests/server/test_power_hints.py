"""電気料金が割高・資金不足で電力を買えない施設への手の案内（電力は輸入できない）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.web import display


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["input_price:electricity", "funds:electricity"])
def test_hints_for_power_do_not_suggest_imports(status):
    hint = display.status_hint(SimpleNamespace(built=True, status=status, ext={}))
    assert hint and "輸入" not in hint
    # 電力でない原料は今までどおり
    other = display.status_hint(SimpleNamespace(built=True, status="input_price:coal", ext={}))
    assert "輸入" in other
