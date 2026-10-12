"""起動時（runserver や manage.py check）に定義データを検査する。"""

from __future__ import annotations

from django.core import checks

from hakoniwa_core.defs import DefsError
from hakoniwa_core.registry import RegistryError
from hakoniwa_core.turn import EngineError


@checks.register()
def check_game_defs(app_configs, **kwargs):
    from server.game import get_game

    get_game.cache_clear()
    try:
        get_game()
    except DefsError as e:
        return [checks.Error(str(issue), id="hakoniwa.E001") for issue in e.issues]
    except (RegistryError, EngineError) as e:
        return [checks.Error(str(e), id="hakoniwa.E002")]
    return []
