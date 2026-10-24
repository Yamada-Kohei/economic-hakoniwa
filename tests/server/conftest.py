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


@pytest.fixture(autouse=True)
def board_without_waiting(settings):
    """掲示板の歯止め（登録直後・連投の間隔）は、試験では待たない。歯止めの試験は自分で戻す。"""
    settings.HAKONIWA_BOARD_NEWCOMER_MINUTES = 0
    settings.HAKONIWA_BOARD_INTERVAL_SECONDS = 0
