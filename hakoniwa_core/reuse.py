"""測りの結果の使い回し（measure batch --reuse）: 計算の中身が前と同じなら、回し直さない。

結果の JSON には、回したときの「計算の中身の指紋」（code_fingerprint）を書いておく。次の
batch で同じ組の結果が見つかり、指紋が今の中身と同じなら、その結果をそのまま使う。指紋が違う・
書いていない・ファイルが壊れているときは、使わずに回す（回しすぎる側に倒す）。

読み書きする置き場（measure batch --store、設計書 12 確かめ方 GT-45）では、指紋・組の名前・Python と
道具の版を鍵にする。実行中は札を持ち、結果を中身のハッシュ入りの名前で置き終えてから、ファイルの
名前とハッシュを書いた完了の印を置く。札は計算を節約するためだけで、写してよいかは完了の印と
結果の中身で確かめる。

指紋に入れるもの: hakoniwa_core/ の全部（測りと関門の道具も。結果の形を決めるため）、game.yaml、
そこから指される定義データ（packs_dirs）とスキーマ（schemas_dir）。改行は LF にそろえて数える
（Windows の作業ツリーと同じ指紋になるように）。国の大きさの倍を環境変数 HK_SCALE で替えている
ときは、その倍も入れる。入れないもの: server/・tests/・docs/・Python の
版（3.12・3.13・3.14 で状態の指紋が一致することを確かめてある）。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import socket
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hakoniwa_core import scale
from hakoniwa_core.config import default_config_path, load_config

PACKAGE = Path(__file__).resolve().parent
STORE_ENV = "HAKONIWA_MEASURE_STORE"  # 置き場の場所（--store の既定）
OWNER_ENV = "HAKONIWA_MEASURE_OWNER"  # 札に書く名前（--owner の既定）
STALE_SECONDS = 600.0  # 札の時刻がこれより古ければ、止まったとみて引き継ぐ
BEAT_SECONDS = 30.0  # 回している間、札の時刻を新しくする間隔
RUN_LIMIT_SECONDS = 6 * 3600.0  # 置き場を使う batch は、これより長く回る組を固まったとみて止める
_KEY = re.compile(r"^[0-9a-f]{64}-py")  # 置き場の中の、版ごとのフォルダの名前
_sleep = time.sleep  # 試験が time.sleep を差し替えても、置き換えのやり直しは本当に待つ
_STORE_SUFFIXES = {"json": "json", "jsonl": "jsonl", "snap": "snap.json.gz"}

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
    fingerprint = fingerprint_paths(
        [PACKAGE, path.resolve(), *config.packs_dirs, config.schemas_dir], config.base_dir
    )
    # 国の大きさの倍を環境変数で替えていると、同じファイルでも結果が変わる（hakoniwa_core/scale.py）
    override = os.environ.get(scale.ENV)
    if override:
        fingerprint = hashlib.sha256(
            f"{fingerprint}\0{scale.ENV}={float(override)!r}".encode()
        ).hexdigest()
    return fingerprint


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
    def _file_name(name: str, stamp: dict[str, Any], kind: str) -> str:
        """結果のファイルの名前。中身のハッシュを入れるので、同じ名前の中身は変わらない。"""
        return f"{name}.{stamp['sha256'][:16]}.{_STORE_SUFFIXES[kind]}"

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
        """完了の印を読む。形・組の名前・指紋・版が合わなければ ValueError か KeyError。"""
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
        for kind in _STORE_SUFFIXES:
            if kind not in files:
                continue
            stamp = files[kind]
            if (
                not isinstance(stamp, dict)
                or not isinstance(stamp.get("sha256"), str)
                or not isinstance(stamp.get("bytes"), int)
                or stamp["bytes"] < 0
                or not isinstance(stamp.get("file"), str)
                or stamp["file"] != self._file_name(name, stamp, kind)
            ):
                raise ValueError("完了の印のファイル名・ハッシュ・バイト数を読めない")
        return data

    def _copy(self, source: Path, target: Path) -> dict[str, Any]:
        """source を target に写し、写したバイトそのもののハッシュとバイト数を返す。"""
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as src, target.open("wb") as dst:
            while block := src.read(1024 * 1024):
                dst.write(block)
                digest.update(block)
                size += len(block)
        return {"sha256": digest.hexdigest(), "bytes": size}

    def _matches(self, stamp: dict[str, Any]) -> bool:
        """印の欄が指すファイルが置き場にあり、バイト数とハッシュが合うか。"""
        digest = hashlib.sha256()
        size = 0
        try:
            with (self.folder / stamp["file"]).open("rb") as src:
                while block := src.read(1024 * 1024):
                    digest.update(block)
                    size += len(block)
        except OSError:
            return False
        return size == stamp["bytes"] and digest.hexdigest() == stamp["sha256"]

    def _verified(self, name: str) -> dict[str, Any] | None:
        """印が読めて、印の指す結果が置き場にあり中身が合うときだけ、その印。"""
        try:
            done = self._done(name)
            raw = (self.folder / done["files"]["json"]["file"]).read_bytes()
            if self._stamp(raw) != {k: done["files"]["json"][k] for k in ("sha256", "bytes")}:
                return None
            self._result(raw, name)
            return done
        except (OSError, ValueError, KeyError):
            return None

    def fetch(self, name: str, out_dir: Path, series: bool) -> tuple[bool, str]:
        """印と中身が全部合った結果だけを out_dir に写す。

        出力先の一時の名前に写し、写したバイトそのものを印と照らしてから、本当の名前に置く。
        """
        temporary: list[Path] = []
        marker = self.folder / f"{name}.done.json"
        try:
            try:
                done = self._done(name)
            except FileNotFoundError:
                return False, "置き場に結果がない"
            files = done["files"]
            if series and "jsonl" not in files:
                return False, "置き場に時系列がない"
            out_dir.mkdir(parents=True, exist_ok=True)
            result = self._temporary(out_dir / f"{name}.json")
            temporary.append(result)
            stamp = self._copy(self.folder / files["json"]["file"], result)
            if stamp != {k: files["json"][k] for k in ("sha256", "bytes")}:
                return False, "結果が印と合わない"
            self._result(result.read_bytes(), name)
            if series:
                timeline = self._temporary(out_dir / f"{name}.jsonl")
                temporary.append(timeline)
                stamp = self._copy(self.folder / files["jsonl"]["file"], timeline)
                if stamp != {k: files["jsonl"][k] for k in ("sha256", "bytes")}:
                    return False, "時系列が印と合わない"
            log = self._temporary(out_dir / f"{name}.log")
            temporary.append(log)
            log.write_text(f"使い回し: {self.folder}\n", encoding="utf-8")
            if series:
                os.replace(timeline, out_dir / f"{name}.jsonl")
            os.replace(result, out_dir / f"{name}.json")
            os.replace(log, out_dir / f"{name}.log")
        except (OSError, ValueError, KeyError) as exc:
            return False, f"置き場から写せない（{exc}）"
        finally:
            self._cleanup(temporary)
        try:
            os.utime(marker, None)  # 使った時刻を残す（古い版の片付けの目安）
        except OSError:
            pass
        return True, ""

    def fetch_snapshot(self, name: str, target: Path) -> tuple[bool, str]:
        """最後のターンの写し（simulate --save）を、印と合うときだけ target に置く。"""
        temporary: list[Path] = []
        marker = self.folder / f"{name}.done.json"
        try:
            try:
                done = self._done(name)
            except FileNotFoundError:
                return False, "置き場に結果がない"
            if "snap" not in done["files"]:
                return False, "置き場に写しがない"
            expected = done["files"]["snap"]
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = self._temporary(target)
            temporary.append(staged)
            stamp = self._copy(self.folder / expected["file"], staged)
            if stamp != {k: expected[k] for k in ("sha256", "bytes")}:
                return False, "写しが印と合わない"
            self._replace(staged, target)
        except (OSError, ValueError, KeyError) as exc:
            return False, f"置き場から写しを写せない（{exc}）"
        finally:
            self._cleanup(temporary)
        try:
            os.utime(marker, None)
        except OSError:
            pass
        return True, ""

    def _put(self, name: str, kind: str, source: Path, temporary: list[Path], placed: list[Path]):
        """source を、中身のハッシュ入りの名前で置き場に置き、印に書く欄を返す。

        名前に中身のハッシュが入るので、置いたファイルは後から書き換わらない。同じ中身がもう
        あれば置かない。
        """
        staged = self._temporary(self.folder / f"{name}.{_STORE_SUFFIXES[kind]}")
        temporary.append(staged)
        stamp = self._copy(source, staged)
        target = self.folder / self._file_name(name, stamp, kind)
        if not self._matches({"file": target.name, **stamp}):
            self._replace(staged, target)
            placed.append(target)
        return {"file": target.name, **stamp}

    def publish(
        self, name: str, out_dir: Path, series: bool, snapshot: bool = False
    ) -> tuple[bool, str]:
        """結果を置いてから印を置く。すでにある結果には時系列と写しを足す。

        結果・時系列・写しは、中身のハッシュ入りの変わらない名前で書き終えてから、
        印（ファイルの名前とハッシュ）だけを最後に置き換える。同じ組を二重に回した 2 本が同時に
        置いても、印はいつも自分の指すファイルと合う。結果の `seconds` は回すたびに違うので、
        同じ名前に上書きすると印と食い違う。印と中身が合わない・印が壊れている置き場は、
        ここで作り直す。
        snapshot を頼んでも、出力先に写しがなければ結果だけを置く。時系列を初めて足すときは、
        写しも今回の物に替える（写しは自分の回の時系列を覚えているため）。
        """
        temporary: list[Path] = []
        placed: list[Path] = []
        try:
            source = out_dir / f"{name}.json"
            self._result(source.read_bytes(), name)
            if series and not (out_dir / f"{name}.jsonl").is_file():
                return False, "結果に時系列がない"
            sources = {"json": source}
            if series:
                sources["jsonl"] = out_dir / f"{name}.jsonl"
            snap = out_dir / f"{name}.{_STORE_SUFFIXES['snap']}"
            if snapshot and snap.is_file():
                sources["snap"] = snap
            current = self._verified(name)
            files = dict(current["files"]) if current is not None else {}
            for kind in sources.keys() - {"json"}:
                if kind in files and not self._matches(files[kind]):
                    del files[kind]
            # 写しは、自分の回の時系列の行数と中身を覚えている。時系列を今回初めて足すなら、前から
            # ある写し（時系列なしの回の物）からは時系列つきの続きを回せないので、今回の写しに替える
            renewed: set[str] = set()
            if {"jsonl", "snap"} <= sources.keys() and "jsonl" not in files and "snap" in files:
                del files["snap"]
                renewed.add("snap")
            if sources.keys() <= files.keys():
                return True, ""
            for kind, path in sources.items():
                if kind not in files:
                    files[kind] = self._put(name, kind, path, temporary, placed)
            # 置く直前に印を読み直す。遅れて来た publish が、先に置かれた印（時系列つきなど）を
            # 悪くしないよう、もう置いてある物を先に使う
            latest = self._verified(name)
            if latest is not None:
                for kind, stamp in latest["files"].items():
                    if kind in renewed:
                        continue
                    if kind == "json" or self._matches(stamp):
                        files[kind] = stamp
            if latest is None or files != latest["files"]:
                done = {
                    "name": name,
                    "fingerprint": self.fingerprint,
                    "environment": self.environment,
                    "owner": self.owner,
                    "host": self.host,
                    "finished": datetime.now(UTC).isoformat(),
                    "files": files,
                }
                marker = self._temporary(self.folder / f"{name}.done.json")
                temporary.append(marker)
                marker.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                self._replace(marker, self.folder / f"{name}.done.json")
            used = {stamp["file"] for stamp in files.values()}
            self._cleanup(path for path in placed if path.name not in used)
            return True, ""
        except (OSError, ValueError, KeyError) as exc:
            self._cleanup(placed)
            return False, f"置き場に置けない（{exc}）"
        finally:
            self._cleanup(temporary)

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


def prune(root: Path, days: float) -> list[str]:
    """置き場の片付け: days 日より長く使っていない版のフォルダと、古い一時のファイルを消す。

    「使った」は、置いた・写した・札を立てた時刻（完了の印と札の更新時刻）で見る。消した物の
    名前を返す。消している間に読みに来た batch は、写せなかった扱いで自分で回す。
    """
    removed: list[str] = []
    now = time.time()
    if not root.is_dir():
        return removed
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and _KEY.match(p.name)):
        marks = [*folder.glob("*.done.json"), *folder.glob("*.lock")]
        try:
            used = max((p.stat().st_mtime for p in marks), default=folder.stat().st_mtime)
        except OSError:
            continue
        if now - used > days * 86400:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
            continue
        for path in folder.iterdir():  # 落ちた batch が残した一時のファイル
            if ".tmp-" not in path.name and ".stale-" not in path.name:
                continue
            try:
                if now - path.stat().st_mtime > 86400:
                    path.unlink()
                    removed.append(f"{folder.name}/{path.name}")
            except OSError:
                pass
    return removed
