from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def game_dir(tmp_path: Path):
    """本体パックとスキーマを写した、書き換えてよい game.yaml の置き場所。

    使い方: path = game_dir(packs=["extra"]); (path / "packs/extra/...").write_text(...)
    """

    def make(packs: list[str] | None = None, extra_yaml: str = "") -> Path:
        shutil.copytree(REPO / "packs" / "base", tmp_path / "packs" / "base")
        shutil.copytree(REPO / "schemas", tmp_path / "schemas")
        config = (REPO / "game.yaml").read_text(encoding="utf-8")
        config = config.replace("packs: [base]", f"packs: {packs or ['base']}")
        (tmp_path / "game.yaml").write_text(config + textwrap.dedent(extra_yaml), encoding="utf-8")
        return tmp_path

    return make


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
