"""財政で中央銀行保有と政府・中央銀行を合わせた借りを見せる。"""

import pytest

from server.web import display
from tests.server.conftest import world as world  # noqa: F401
from tests.server.test_phase1 import _own_island
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db


def test_fiscal_shows_central_bank_bonds_and_net_public_debt(client, world):
    island = _own_island(client)
    _stats(island, 1, {"bonds.central_bank": 600_000, "net_public_debt": -250_000})
    response = client.get(f"/islands/{island.id}/fiscal/")
    position = {r["key"]: r for r in response.context["fiscal"]["position"]}
    assert position["bonds.central_bank"]["label"] == "中央銀行が持つ国債（刷ったお金）"
    assert position["net_public_debt"]["label"] == "政府と中央銀行を合わせた借り"
    assert position["net_public_debt"]["text"] == display.money_text(
        -250_000, response.context["currency"]
    )
    html = response.content.decode()
    assert "中央銀行が持つ国債（刷ったお金）" in html
    assert "政府と中央銀行を合わせた借り" in html
    assert "市中と外国が持つ国債から" in html
    data = client.get(f"/api/islands/{island.id}/fiscal").json()["fiscal"]
    assert data["position"] == response.context["fiscal"]["position"]
