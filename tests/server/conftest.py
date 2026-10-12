from __future__ import annotations

import pytest
from django.core.management import call_command

from server.world.models import World


@pytest.fixture
def world(db) -> World:
    call_command("create_sample_world", islands=3, seed=1, stdout=_Null())
    return World.objects.get()


class _Null:
    def write(self, *_):
        pass
