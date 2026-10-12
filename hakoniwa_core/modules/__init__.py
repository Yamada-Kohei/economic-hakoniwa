"""モジュールの読み込み。

hakoniwa_core/modules/ の下のパッケージと、各パックの modules/ の下のパッケージを、
名前順に import して register(registry) を呼びます。
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path

from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import Registry


def build_registry(defs: Defs) -> Registry:
    registry = Registry()
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.ispkg:
            module = importlib.import_module(f"{__name__}.{info.name}")
            module.register(registry)
    for pack in defs.packs:
        for init in sorted((pack.path / "modules").glob("*/__init__.py")):
            _import_pack_module(pack.name, init).register(registry)
    return registry


def _import_pack_module(pack: str, init: Path):
    name = f"hakoniwa_packs.{pack}.{init.parent.name}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[str(init.parent)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
