"""行番号を覚えておける YAML の読み込み。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class YamlFile:
    path: Path
    data: Any
    root: yaml.Node | None

    def line_of(self, keys: Sequence[str | int]) -> int | None:
        """keys でたどった場所の行番号（1から）。たどれない所はその手前の行。"""
        node = self.root
        if node is None:
            return None
        line = node.start_mark.line + 1
        for key in keys:
            child = None
            if isinstance(node, yaml.MappingNode):
                for k, v in node.value:
                    if str(k.value) == str(key):
                        child = v
                        line = k.start_mark.line + 1
                        break
            elif isinstance(node, yaml.SequenceNode) and isinstance(key, int):
                if 0 <= key < len(node.value):
                    child = node.value[key]
                    line = child.start_mark.line + 1
            if child is None:
                break
            node = child
        return line


def read_yaml(path: Path) -> YamlFile:
    text = path.read_text(encoding="utf-8")
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    data = yaml.safe_load(text)
    return YamlFile(path=path, data=data, root=root)
