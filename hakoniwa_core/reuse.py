"""測りの結果の使い回し（measure batch --reuse）: 計算の中身が前と同じなら、回し直さない。

結果の JSON には、回したときの「計算の中身の指紋」（code_fingerprint）を書いておく。次の
batch で同じ組の結果が見つかり、指紋が今の中身と同じなら、その結果をそのまま使う。指紋が違う・
書いていない・ファイルが壊れているときは、使わずに回す（回しすぎる側に倒す）。

読み書きする置き場では、指紋・組の名前・Python と道具の版を鍵にする。実行中は札を持ち、
結果を置き終えてから、バイト数とハッシュを書いた完了の印を置く。札は計算を節約するためだけで、
写してよいかは完了の印と結果の中身で確かめる。

指紋に入れるもの: hakoniwa_core/ の全部（測りと関門の道具も。結果の形を決めるため）、game.yaml、
そこから指される定義データ（packs_dirs）とスキーマ（schemas_dir）。改行は LF にそろえて数える
（Windows の作業ツリーと同じ指紋になるように）。入れないもの: server/・tests/・docs/・Python の
版（3.12・3.13・3.14 で状態の指紋が一致することを確かめてある）。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import socket
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hakoniwa_core.config import default_config_path, load_config

PACKAGE = Path(__file__).resolve().parent
STORE_ENV = "HAKONIWA_MEASURE_STORE"  # 置き場の場所（--store の既定）
OWNER_ENV = "HAKONIWA_MEASURE_OWNER"  # 札に書く名前（--owner の既定）
STALE_SECONDS = 600.0  # 札の時刻がこれより古ければ、止まったとみて引き継ぐ
BEAT_SECONDS = 30.0  # 回している間、札の時刻を新しくする間隔
_sleep = time.sleep  # 試験が time.sleep を差し替えても、置き換えのやり直しは本当に待つ

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


def environment_tag() -> str:
    """置き場を分ける Python と道具の版（OS の違いでは分けない）。"""
    versions = []
    for package in ("PyYAML", "jsonschema"):
        try:
            version = importlib.metadata.version(package)
        except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
            version = "unknown"
        versions.append(f"{package}{version}")
    python = f"py{sys.version_info.major}.{sys.version_info.minor}-{sys.implementation.name}"
    return "-".join([python, *versions])


def store_key(fingerprint: str) -> str:
    """指紋を省略せず、Python と道具の版を足した置き場の鍵。"""
    return f"{fingerprint}-{environment_tag()}"


class Store:
    """完了の印で結果を確かめ、札で同じ組の実行をなるべく重ねない置き場。"""

    folder: Path

    def __init__(
        self,
        root: Path,
        fingerprint: str,
        owner: str = "",
        stale_seconds: float = STALE_SECONDS,
    ) -> None:
        self.fingerprint = fingerprint
        self.environment = environment_tag()
        self.folder = root / store_key(fingerprint)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.owner = owner
        self.host = socket.gethostname()
        self.stale_seconds = stale_seconds
        self._tokens: dict[str, str] = {}

    @staticmethod
    def _temporary(path: Path) -> Path:
        return path.with_name(f"{path.name}.tmp-{uuid4().hex}")

    @staticmethod
    def _replace(source: Path, target: Path) -> None:
        """os.replace。Windows では、置き先をほかの処理が開いている間は失敗するので少し待つ。"""
        for attempt in range(5):
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                _sleep(0.05 * (attempt + 1))

    @staticmethod
    def _cleanup(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _stamp(data: bytes) -> dict[str, Any]:
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    @staticmethod
    def _copy_series(source: Path, target: Path) -> dict[str, Any]:
        """大きい時系列は一度に抱えず、写したバイトそのものを数える。"""
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as src, target.open("wb") as dst:
            while block := src.read(1024 * 1024):
                dst.write(block)
                digest.update(block)
                size += len(block)
        return {"sha256": digest.hexdigest(), "bytes": size}

    def _result(self, raw: bytes, name: str) -> dict[str, Any]:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("結果が JSON の辞書ではない")
        if data.get("code_fingerprint") != self.fingerprint:
            raise ValueError("計算の中身が違う")
        if f"{data['scenario']}-s{data['seed']}-t{data['turns']}" != name:
            raise ValueError("結果の組の名前が違う")
        return data

    def _done(self, name: str) -> dict[str, Any]:
        data = json.loads((self.folder / f"{name}.done.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("完了の印が JSON の辞書ではない")
        if data["name"] != name:
            raise ValueError("完了の印の組の名前が違う")
        if data["fingerprint"] != self.fingerprint:
            raise ValueError("完了の印の指紋が違う")
        if data["environment"] != self.environment:
            raise ValueError("完了の印の Python と道具の版が違う")
        files = data["files"]
        if not isinstance(files, dict) or "json" not in files:
            raise ValueError("完了の印に結果の欄がない")
        for kind in ("json", "jsonl"):
            if kind not in files:
                continue
            stamp = files[kind]
            if (
                not isinstance(stamp, dict)
                or not isinstance(stamp.get("sha256"), str)
                or not isinstance(stamp.get("bytes"), int)
                or stamp["bytes"] < 0
            ):
                raise ValueError("完了の印のハッシュかバイト数を読めない")
        return data

    def fetch(self, name: str, out_dir: Path, series: bool) -> tuple[bool, str]:
        """印と中身が全部合った結果だけを out_dir に写す。"""
        temporary: list[Path] = []
        try:
            try:
                done = self._done(name)
            except FileNotFoundError:
                return False, "置き場に結果がない"
            raw = (self.folder / f"{name}.json").read_bytes()
            if self._stamp(raw) != done["files"]["json"]:
                return False, "結果が印と合わない"
            self._result(raw, name)
            if series and "jsonl" not in done["files"]:
                return False, "置き場に時系列がない"
            out_dir.mkdir(parents=True, exist_ok=True)
            result = self._temporary(out_dir / f"{name}.json")
            temporary.append(result)
            result.write_bytes(raw)
            if series:
                timeline = self._temporary(out_dir / f"{name}.jsonl")
                temporary.append(timeline)
                if not (self.folder / f"{name}.jsonl").is_file():
                    return False, "置き場に時系列がない"
                stamp = self._copy_series(self.folder / f"{name}.jsonl", timeline)
                if stamp != done["files"]["jsonl"]:
                    return False, "時系列が印と合わない"
            log = self._temporary(out_dir / f"{name}.log")
            temporary.append(log)
            log.write_text(f"使い回し: {self.folder}\n", encoding="utf-8")
            # 検証した一時ファイルを使う。検証のあとに元のファイルを開き直さない。
            if series:
                os.replace(timeline, out_dir / f"{name}.jsonl")
            os.replace(result, out_dir / f"{name}.json")
            os.replace(log, out_dir / f"{name}.log")
            return True, ""
        except (OSError, ValueError, KeyError) as exc:
            return False, f"置き場から写せない（{exc}）"
        finally:
            self._cleanup(temporary)

    def _place(self, raw: bytes, target: Path, name: str, temporary: list[Path]) -> bytes:
        """結果を置き、置き場に残ったバイトを返す。先に置かれた結果は書き換えない。

        2 本が同じ組を二重に回して同時に置いても、先に置いた方の結果が残り、どちらの印もその
        結果のハッシュを書く（結果の `seconds` は回すたびに違うので、後から置き換えると印と
        合わなくなる）。os.link は置き先がもうあれば失敗するので、置くのは 1 本だけになる。
        """
        broken = False
        try:
            stored = target.read_bytes()
            self._result(stored, name)
            return stored
        except FileNotFoundError:
            pass
        except (ValueError, KeyError):
            broken = True  # 壊れた結果は置き直す
        result = self._temporary(target)
        temporary.append(result)
        result.write_bytes(raw)
        if broken:
            self._replace(result, target)
            return raw
        try:
            os.link(result, target)
            return raw
        except FileExistsError:
            stored = target.read_bytes()
            self._result(stored, name)
            return stored

    def publish(self, name: str, out_dir: Path, series: bool) -> tuple[bool, str]:
        """結果を置いてから印を置く。すでにある結果には時系列だけを足す。

        置き場の印と中身が合っていれば何もしない。合っていなければ（壊れた・印がない）作り直す。
        """
        temporary: list[Path] = []
        try:
            raw = (out_dir / f"{name}.json").read_bytes()
            self._result(raw, name)
            if series and not (out_dir / f"{name}.jsonl").is_file():
                return False, "結果に時系列がない"
            target = self.folder / f"{name}.json"
            changed = False
            try:
                done = self._done(name)
                if self._stamp(target.read_bytes()) != done["files"]["json"]:
                    done = None
            except (FileNotFoundError, ValueError, KeyError):
                done = None
            if done is None:
                stored = self._place(raw, target, name, temporary)
                done = {
                    "name": name,
                    "fingerprint": self.fingerprint,
                    "environment": self.environment,
                    "owner": self.owner,
                    "host": self.host,
                    "finished": datetime.now(UTC).isoformat(),
                    "files": {"json": self._stamp(stored)},
                }
                changed = True
            if series and not self._series_matches(name, done):
                timeline = self._temporary(self.folder / f"{name}.jsonl")
                temporary.append(timeline)
                done["files"]["jsonl"] = self._copy_series(out_dir / f"{name}.jsonl", timeline)
                self._replace(timeline, self.folder / f"{name}.jsonl")
                changed = True
            if changed:
                marker = self._temporary(self.folder / f"{name}.done.json")
                temporary.append(marker)
                marker.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                self._replace(marker, self.folder / f"{name}.done.json")
            return True, ""
        except (OSError, ValueError, KeyError) as exc:
            return False, f"置き場に置けない（{exc}）"
        finally:
            self._cleanup(temporary)

    def _series_matches(self, name: str, done: dict[str, Any]) -> bool:
        """置き場の時系列が印と合っているか。"""
        stamp = done["files"].get("jsonl")
        if stamp is None:
            return False
        digest = hashlib.sha256()
        size = 0
        try:
            with (self.folder / f"{name}.jsonl").open("rb") as src:
                while block := src.read(1024 * 1024):
                    digest.update(block)
                    size += len(block)
        except FileNotFoundError:
            return False
        return {"sha256": digest.hexdigest(), "bytes": size} == stamp

    def _description(self, name: str) -> str:
        try:
            data = json.loads((self.folder / f"{name}.lock").read_text(encoding="utf-8"))
            if isinstance(data, dict) and "owner" in data and "started" in data:
                return f"{data['owner']}（{data['started']} から）"
        except (OSError, ValueError):
            pass
        return "読めない札"

    def free(self, name: str) -> bool:
        """札を取らずに、なくなったか古くなったかだけを見る。"""
        try:
            modified = (self.folder / f"{name}.lock").stat().st_mtime
            return time.time() - modified > self.stale_seconds
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _create_lock(self, name: str) -> None:
        token = uuid4().hex
        data = {
            "owner": self.owner,
            "host": self.host,
            "pid": os.getpid(),
            "started": datetime.now(UTC).isoformat(),
            "token": token,
        }
        path = self.folder / f"{name}.lock"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        self._tokens[name] = token

    def claim(self, name: str) -> tuple[bool, str]:
        """札を取る。古い札だけを退け、一度だけ取り直す。"""
        try:
            self._create_lock(name)
            return True, ""
        except FileExistsError:
            if not self.free(name):
                return False, self._description(name)
        except OSError:
            return False, self._description(name)
        path = self.folder / f"{name}.lock"
        stale = path.with_name(f"{path.name}.stale-{uuid4().hex}")
        try:
            os.replace(path, stale)
            if time.time() - stale.stat().st_mtime <= self.stale_seconds:
                # 古いと見た後に、ほかの batch が引き継いで作った新しい札だった: 戻して待つ
                try:
                    os.link(stale, path)
                except OSError:
                    pass
                return False, self._description(name)
            stale.unlink()
            self._create_lock(name)
            return True, ""
        except OSError:
            return False, self._description(name)
        finally:
            self._cleanup([stale])

    def _owns(self, name: str) -> bool:
        token = self._tokens.get(name)
        if token is None:
            return False
        data = json.loads((self.folder / f"{name}.lock").read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("token") == token

    def beat(self, name: str) -> None:
        """自分の札だけを新しくする。取られた札には触れない。"""
        try:
            if self._owns(name):
                os.utime(self.folder / f"{name}.lock", None)
        except (OSError, ValueError):
            pass

    def release(self, name: str) -> None:
        """自分の札だけを消し、覚えていた token を捨てる。"""
        try:
            if self._owns(name):
                (self.folder / f"{name}.lock").unlink()
        except (OSError, ValueError):
            pass
        finally:
            self._tokens.pop(name, None)
