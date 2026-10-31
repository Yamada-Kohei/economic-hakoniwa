from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# 試験は国の大きさの倍 1 の数で書いてある（倍の試験は tests/core/test_scale.py で倍を指して回す）
os.environ.setdefault("HK_SCALE", "1")


def pytest_configure(config) -> None:
    # 文字コードを書かずにファイルを読み書きすると、Windows（X870）では cp932 になって落ちる。
    # CI は PYTHONWARNDEFAULTENCODING=1 で回し、ここで自分のコードの分だけを誤りにする（GT-43）。
    # pyproject.toml に書かないのは、LAN の自動の反映が pyproject.toml の変わった版で試しの venv を
    # 作ろうとし、サーバーに python3.12-venv がなくて止まったため
    config.addinivalue_line(
        "filterwarnings", "error::EncodingWarning:(tests|hakoniwa_core|server|tools)"
    )


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
