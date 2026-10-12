"""全部の画面で使う値（見出しの「自分の島」のリンク）。"""

from __future__ import annotations

from server.world.models import Island


def own_island(request) -> dict:
    if not request.user.is_authenticated:
        return {}
    island = Island.objects.filter(owner=request.user).values("id", "name").first()
    return {"own_island": island}
