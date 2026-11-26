"""グラフィック強化 案5（残り分）: 世界マップの島名の帯に、その国の小さい旗が出る。

旗の大きさは一覧表の旗（.table-flag）と同じ 24×16 で、帯の左端・名前の左隣に置く。
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from server.world import models as m

pytestmark = pytest.mark.django_db


def test_world_map_name_band_shows_flag(world):
    island = m.Island.objects.order_by("id").first()
    page = Client().get(reverse("world")).content.decode()
    flag_url = reverse("island_flag", args=[island.id])
    assert f'<img class="cell-flag" src="{flag_url}"' in page
    assert 'width="24" height="16"' in page
