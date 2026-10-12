"""定義データの誤りの表し方。誤りは「ファイル名:行: 内容」で示します。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DefsIssue:
    file: Path | None
    line: int | None
    message: str

    def __str__(self) -> str:
        if self.file is None:
            return self.message
        where = str(self.file)
        if self.line is not None:
            where += f":{self.line}"
        return f"{where}: {self.message}"


class DefsError(Exception):
    """定義データに誤りがある。issues にすべての誤りが入っています。"""

    def __init__(self, issues: list[DefsIssue]):
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))
