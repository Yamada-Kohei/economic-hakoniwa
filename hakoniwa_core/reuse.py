"""測りの結果の使い回し（measure batch --reuse）: 計算の中身が前と同じなら、回し直さない。

結果の JSON には、回したときの「計算の中身の指紋」（code_fingerprint）を書いておく。次の
batch で同じ組の結果が見つかり、指紋が今の中身と同じなら、その結果をそのまま使う。指紋が違う・
書いていない・ファイルが壊れているときは、使わずに回す（回しすぎる側に倒す）。

指紋に入れるもの: hakoniwa_core/ の全部（測りと関門の道具も。結果の形を決めるため）、game.yaml、
そこから指される定義データ（packs_dirs）とスキーマ（schemas_dir）。改行は LF にそろえて数える
（Windows の作業ツリーと同じ指紋になるように）。入れないもの: server/・tests/・docs/・Python の
版（3.12・3.13・3.14 で状態の指紋が一致することを確かめてある）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hakoniwa_core.config import default_config_path, load_config

PACKAGE = Path(__file__).resolve().parent

# 数えないファイル（Python が作る写し）
_SKIP_PARTS = {"__pycache__"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def _files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not (_SKIP_PARTS & set(p.parts)) and p.suffix not in _SKIP_SUFFIXES
    )


def fingerprint_paths(roots: Iterable[Path], base: Path) -> str:
    """roots（ファイルかフォルダ）の中身の指紋。名前（base からの相対）と中身の両方を数える。"""
    digest = hashlib.sha256()
    seen: set[Path] = set()
    for root in roots:
        for path in _files(root):
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                name = path.relative_to(base.resolve()).as_posix()
            except ValueError:
                name = path.as_posix()
            digest.update(name.encode() + b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def code_fingerprint(config_path: str | Path | None = None) -> str:
    """いま動いている計算の中身（コード・設定・定義データ）の指紋。"""
    path = Path(config_path) if config_path else default_config_path()
    config = load_config(path)
    return fingerprint_paths(
        [PACKAGE, path.resolve(), *config.packs_dirs, config.schemas_dir], config.base_dir
    )


def find(
    name: str, fingerprint: str, dirs: Iterable[Path], series: bool
) -> tuple[Path | None, str]:
    """dirs の中から、名前 name の結果で指紋が fingerprint と同じものを探す。

    見つかれば (JSON のパス, "")、なければ (None, 使えなかったわけ)。series なら時系列の
    ファイルも揃っているものだけ。
    """
    reason = "前の結果がない"
    for folder in dirs:
        result = folder / f"{name}.json"
        if not result.is_file():
            continue
        try:
            data: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reason = f"結果を読めない（{folder}）"
            continue
        if not isinstance(data, dict) or "code_fingerprint" not in data:
            reason = f"指紋が書いていない（{folder}）"
        elif data["code_fingerprint"] != fingerprint:
            reason = f"計算の中身が違う（{folder}）"
        elif series and not (folder / f"{name}.jsonl").is_file():
            reason = f"時系列がない（{folder}）"
        else:
            return result, ""
    return None, reason
