"""説明書が設定と食い違っていないかを確かめる。

設定を変えた PR で説明書の直し忘れがあると、ここで止まる。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def stage_table(text: str) -> list[str]:
    """docs/modules.md の「標準のモジュール」の節にある段の表の、段の名前を上から順に返す。"""
    section = re.search(r"^## 標準のモジュール.*?(?=^## |\Z)", text, re.M | re.S)
    assert section, "docs/modules.md に「## 標準のモジュール」の節がありません"
    table = re.search(r"^\| 段 \|.*\n((?:\|.*\n?)*)", section.group(0), re.M)
    assert table, "docs/modules.md の「標準のモジュール」の節に、見出しが「段」の表がありません"
    return re.findall(r"^\| `([a-z_]+)` \|", table.group(1), re.M)


def test_stage_table_follows_game_yaml():
    stages = yaml.safe_load((REPO / "game.yaml").read_text(encoding="utf-8"))["stages"]
    table = stage_table((REPO / "docs" / "modules.md").read_text(encoding="utf-8"))
    assert table == stages, (
        "docs/modules.md の段の表を、game.yaml の stages: と同じ段・同じ並びにしてください。"
        f"\n表にない段: {[s for s in stages if s not in table]}"
        f"\n設定にない段: {[s for s in table if s not in stages]}"
    )


def test_stage_table_reads_rows_in_order():
    text = (
        "## 標準のモジュール\n\n| 段 | モジュール | すること |\n|---|---|---|\n"
        "| `market` | `market` | 板寄せ |\n| `trade` | `trade`（全島の段） | 貿易 |\n\n"
        "| モジュール | 登録するもの |\n|---|---|\n| `basics` | 命令 |\n\n"
        "## 例\n\n| `growth` | `growth` | 例の段 |\n"
    )
    assert stage_table(text) == ["market", "trade"]
