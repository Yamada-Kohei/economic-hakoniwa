"""電力でない原料が高くて止まった施設の状態の文（倭国さんの案 2、2026-11-14）: 売り値の品と原料の
両方を出し、打てる手も両方の側を案内する。電力の行は今までどおり。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.web import display


def _plant(status):
    return SimpleNamespace(
        built=True,
        status=status,
        ext={},
        kind="electronics_plant",
        recipe="default",
        economy={},
        operating_rate=0,
    )


@pytest.mark.django_db
def test_status_names_both_the_product_and_the_material():
    text = display.status_text(_plant("input_price:machinery"))
    assert text == "今の売り値では採算が合わない（製品: 電子機器、原料: 機械）"
    hint = display.status_hint(_plant("input_price:machinery"))
    assert "生産補助" in hint and "輸入" in hint


@pytest.mark.django_db
def test_power_row_is_unchanged():
    assert "売り値" not in display.status_text(_plant("input_price:electricity"))
