"""パックの読み込みと検証。

本体パック packs/base/ も追加パックも、ここで同じように読み込みます。
読み込む順は「本体パック → 必要とされたパック → 名前順」です。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs.defs import Defs, PackInfo
from hakoniwa_core.defs.errors import DefsError, DefsIssue
from hakoniwa_core.defs.freeze import freeze
from hakoniwa_core.defs.kinds import KINDS, LOCALE_KINDS
from hakoniwa_core.defs.yamlio import YamlFile, read_yaml

BASE_PACK = "base"


@dataclass
class _Origin:
    """ある定義がどのファイルのどこで書かれたか（誤りの場所を示すため）。"""

    pack: str
    file: YamlFile


def load_defs(config: GameConfig) -> Defs:
    """game.yaml の設定に従ってパックを読み込み、検証済みの Defs を返す。"""
    return _Loader(config).load()


class _Loader:
    def __init__(self, config: GameConfig):
        self.config = config
        self.issues: list[DefsIssue] = []
        self.validators: dict[str, Draft202012Validator] = {}

    # --- 全体の流れ -------------------------------------------------------

    def load(self) -> Defs:
        found = self._discover()
        self._raise_if_issues()
        order = self._resolve_order(found)
        self._raise_if_issues()

        tables: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in KINDS}
        origins: dict[tuple[str, str], _Origin] = {}
        for name in order:
            self._load_pack_data(found[name], tables, origins)
        self._raise_if_issues()

        tables = {
            kind: {i: KINDS[kind].normalize(e) for i, e in entries.items()}
            for kind, entries in tables.items()
        }
        self._cross_check(tables, origins)

        locales = self._load_locales([found[n] for n in order])
        self._check_locale_complete(tables, locales, origins)
        self._raise_if_issues()

        return Defs(
            tables=freeze(tables),
            locales=freeze(locales),
            packs=tuple(found[n] for n in order),
        )

    def _raise_if_issues(self) -> None:
        if self.issues:
            raise DefsError(self.issues)

    def _issue(self, file: YamlFile | Path | None, line: int | None, message: str) -> None:
        path = file.path if isinstance(file, YamlFile) else file
        self.issues.append(DefsIssue(path, line, message))

    def _validator(self, schema_name: str) -> Draft202012Validator:
        if schema_name not in self.validators:
            path = self.config.schemas_dir / schema_name
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.validators[schema_name] = Draft202012Validator(schema)
        return self.validators[schema_name]

    def _validate(self, schema_name: str, data: Any, file: YamlFile, at: tuple) -> bool:
        ok = True
        errors = self._validator(schema_name).iter_errors(data)
        for err in sorted(errors, key=lambda e: list(map(str, e.absolute_path))):
            path = (*at, *err.absolute_path)
            where = "/".join(str(p) for p in path) or "(ファイル全体)"
            self._issue(file, file.line_of(path), f"{where}: {err.message}")
            ok = False
        return ok

    # --- パックを探して並べる ---------------------------------------------

    def _discover(self) -> dict[str, PackInfo]:
        found: dict[str, PackInfo] = {}
        for packs_dir in self.config.packs_dirs:
            if not packs_dir.is_dir():
                self._issue(packs_dir, None, "パックのフォルダがありません")
                continue
            for pack_yaml in sorted(packs_dir.glob("*/pack.yaml")):
                info = self._read_pack_yaml(pack_yaml)
                if info is None:
                    continue
                if info.name in found:
                    self._issue(
                        pack_yaml,
                        None,
                        f"パック {info.name} が {found[info.name].path} にもあります",
                    )
                    continue
                found[info.name] = info
        return found

    def _read_pack_yaml(self, path: Path) -> PackInfo | None:
        file = read_yaml(path)
        if not self._validate("pack.schema.json", file.data, file, ()):
            return None
        data = file.data
        if data["name"] != path.parent.name:
            self._issue(file, file.line_of(["name"]), "name はフォルダ名と同じにしてください")
            return None
        return PackInfo(
            name=data["name"],
            version=str(data["version"]),
            requires=tuple(data.get("requires", [])),
            path=path.parent,
        )

    def _resolve_order(self, found: dict[str, PackInfo]) -> list[str]:
        if BASE_PACK not in found:
            self._issue(None, None, f"本体パック {BASE_PACK} が見つかりません")
            return []
        wanted = [BASE_PACK, *sorted(set(self.config.packs) - {BASE_PACK})]
        order: list[str] = []
        visiting: list[str] = []

        def visit(name: str, needed_by: str | None) -> None:
            if name in order:
                return
            if name in visiting:
                cycle = " → ".join([*visiting[visiting.index(name) :], name])
                self._issue(None, None, f"パックの依存が循環しています: {cycle}")
                return
            if name not in found:
                who = f"{needed_by} が必要とする" if needed_by else "game.yaml で指定した"
                self._issue(None, None, f"{who}パック {name} が見つかりません")
                return
            visiting.append(name)
            for req in sorted(found[name].requires):
                visit(req, name)
            visiting.pop()
            order.append(name)

        for name in wanted:
            visit(name, None)
        return order

    # --- 定義データ --------------------------------------------------------

    def _load_pack_data(
        self,
        pack: PackInfo,
        tables: dict[str, dict[str, dict[str, Any]]],
        origins: dict[tuple[str, str], _Origin],
    ) -> None:
        data_dir = pack.path / "data"
        if not data_dir.is_dir():
            return
        for path in sorted(data_dir.glob("*.yaml")):
            kind = path.stem
            if kind not in KINDS:
                known = "、".join(KINDS)
                self._issue(path, None, f"知らない種類の定義ファイルです（使えるもの: {known}）")
                continue
            file = read_yaml(path)
            if file.data is None:
                continue
            if not isinstance(file.data, dict):
                self._issue(file, 1, "名前ごとの定義を並べた形（マッピング）にしてください")
                continue
            for entry_id, entry in file.data.items():
                self._load_entry(pack, kind, str(entry_id), entry, file, tables, origins)

    def _load_entry(
        self,
        pack: PackInfo,
        kind: str,
        entry_id: str,
        entry: Any,
        file: YamlFile,
        tables: dict[str, dict[str, dict[str, Any]]],
        origins: dict[tuple[str, str], _Origin],
    ) -> None:
        line = file.line_of([entry_id])
        if not _is_id(entry_id):
            self._issue(file, line, f"名前 {entry_id} は英小文字・数字・_ で書いてください")
            return
        if not isinstance(entry, dict):
            self._issue(file, line, f"{entry_id} の中身はマッピングにしてください")
            return
        table = tables[kind]
        key = (kind, entry_id)
        if entry.get("override") is True:
            if entry_id not in table:
                self._issue(
                    file, line, f"override する {kind}.{entry_id} が先に読んだパックにありません"
                )
                return
            patch = {k: v for k, v in entry.items() if k != "override"}
            merged = dict(table[entry_id])
            if not self._merge_numbers(merged, patch, file, (entry_id,)):
                return
            entry = merged
        elif entry_id in table:
            first = origins[key]
            self._issue(
                file,
                line,
                f"{kind}.{entry_id} はパック {first.pack} でも定義されています"
                "（上書きするなら override: true を付けてください）",
            )
            return
        if not self._validate(KINDS[kind].schema, entry, file, (entry_id,)):
            return
        table[entry_id] = entry
        origins[key] = _Origin(pack.name, file)

    def _merge_numbers(
        self, target: dict[str, Any], patch: dict[str, Any], file: YamlFile, at: tuple
    ) -> bool:
        """override では数値だけを差し替える（入れ子のマッピングはたどる）。"""
        ok = True
        for k, v in patch.items():
            path = (*at, k)
            old = target.get(k)
            if isinstance(v, dict) and isinstance(old, dict):
                inner = dict(old)
                ok = self._merge_numbers(inner, v, file, path) and ok
                target[k] = inner
            elif _is_number(v) and (old is None or _is_number(old)):
                target[k] = v
            else:
                where = "/".join(map(str, path))
                self._issue(
                    file, file.line_of(path), f"{where}: override で変えられるのは数値だけです"
                )
                ok = False
        return ok

    def _cross_check(
        self,
        tables: dict[str, dict[str, dict[str, Any]]],
        origins: dict[tuple[str, str], _Origin],
    ) -> None:
        for kind, spec in KINDS.items():
            for entry_id, entry in tables[kind].items():
                for p_kind, p_id, path, message in spec.check(entry_id, entry, tables):
                    origin = origins[(p_kind, p_id)]
                    line = origin.file.line_of([p_id, *path])
                    self._issue(origin.file, line, f"{p_kind}.{p_id}: {message}")

    # --- 表示名 ------------------------------------------------------------

    def _load_locales(self, packs: list[PackInfo]) -> dict[str, dict[str, dict[str, str]]]:
        """locale/<言語>/<種類>.yaml を読む。後のパックの表示名で上書きできる。"""
        locales: dict[str, dict[str, dict[str, str]]] = {}
        for pack in packs:
            for path in sorted((pack.path / "locale").glob("*/*.yaml")):
                lang, kind = path.parent.name, path.stem
                if kind not in LOCALE_KINDS:
                    self._issue(path, None, f"知らない種類の表示名ファイルです: {kind}")
                    continue
                file = read_yaml(path)
                if file.data is None:
                    continue
                if not self._validate("locale.schema.json", file.data, file, ()):
                    continue
                table = locales.setdefault(lang, {}).setdefault(kind, {})
                table.update({str(k): v for k, v in file.data.items()})
        return locales

    def _check_locale_complete(
        self,
        tables: dict[str, dict[str, dict[str, Any]]],
        locales: dict[str, dict[str, dict[str, str]]],
        origins: dict[tuple[str, str], _Origin],
    ) -> None:
        lang = self.config.raw.get("locale", "ja")
        names = locales.get(lang, {})
        for gid, good in tables["goods"].items():
            unit = good.get("unit")
            if unit is not None and unit not in names.get("units", {}):
                origin = origins[("goods", gid)]
                self._issue(
                    origin.file,
                    origin.file.line_of([gid]),
                    f"単位 {unit} の表示名が locale/{lang}/units.yaml にありません",
                )
        for fid, fdef in tables["facilities"].items():
            industry = fdef.get("industry")
            if industry is not None and industry not in names.get("industries", {}):
                origin = origins[("facilities", fid)]
                self._issue(
                    origin.file,
                    origin.file.line_of([fid, "industry"]),
                    f"産業 {industry} の表示名が locale/{lang}/industries.yaml にありません",
                )
        for kind, entries in tables.items():
            if not KINDS[kind].needs_locale:
                continue
            for entry_id in entries:
                if entry_id not in names.get(kind, {}):
                    origin = origins[(kind, entry_id)]
                    self._issue(
                        origin.file,
                        origin.file.line_of([entry_id]),
                        f"{kind}.{entry_id} の表示名が locale/{lang}/{kind}.yaml にありません",
                    )


def _is_id(value: str) -> bool:
    return (
        bool(value)
        and value[0].isascii()
        and value[0].islower()
        and all(c.isascii() and (c.islower() or c.isdigit() or c == "_") for c in value)
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
