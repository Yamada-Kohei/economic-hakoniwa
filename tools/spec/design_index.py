"""設計書（docs/design/）の決まりの索引を作り、索引と本文のずれを調べる道具。

設計書の仕組みのファイル（01〜12）は、頭に「決まりの一覧」の表を持ちます。この道具はその表から
docs/design/INDEX.md を作り、番号の重なり・番号の頭・試験のパス・設定の名前を調べます。
索引は手で書かず、この道具で作り直します
（ファイルを読むだけで、write のときだけ INDEX.md を書きます）。

    python tools/spec/design_index.py write          # docs/design/INDEX.md を作り直す
    python tools/spec/design_index.py check          # ずれと誤りを並べる（あれば終わりの値 1）
    python tools/spec/design_index.py show MK-16     # その決まりの行（一覧と仮の決まり）を出す
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DESIGN = REPO / "docs" / "design"
INDEX = DESIGN / "INDEX.md"

# ファイルの番号と、決まりの番号の頭
PREFIXES = {
    1: "OV",
    2: "PO",
    3: "MK",
    4: "LB",
    5: "FM",
    6: "FS",
    7: "BK",
    8: "IN",
    9: "FX",
    10: "PL",
    11: "UI",
    12: "GT",
}

_RULE_ROW = re.compile(r"^\|\s*([A-Z]{2}-\d{2,3})\s*\|")
_SETTING = re.compile(r"`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_\[\]]*)+)`")
_TEST = re.compile(r"(tests/[A-Za-z0-9_/]+\.py)(?:::(\w+))?")
# 設定の名前に見えるが、コードやファイルを指しているもの
_NOT_SETTING = re.compile(r"\.(py|yaml|md|json|html|js)$|^(hakoniwa_core|server|tools|ctx|state)\.")
SUMMARY_CHARS = 48


@dataclass
class Rule:
    number: str
    text: str
    status: str
    settings: str
    tests: str
    file: str


@dataclass
class Tab:
    path: Path
    no: int
    title: str
    rules: list[Rule] = field(default_factory=list)
    provisional: list[list[str]] = field(default_factory=list)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _sections(text: str) -> dict[str, list[str]]:
    """「## 決まりの一覧」と「### 仮の決まり」の行を取り出す。"""
    out: dict[str, list[str]] = {"rules": [], "provisional": []}
    where = None
    for line in text.splitlines():
        if line.startswith("## "):
            where = "rules" if line.strip() == "## 決まりの一覧" else None
        elif line.startswith("### ") and where in ("rules", "provisional"):
            where = "provisional" if line.strip() == "### 仮の決まり" else None
        elif where and _RULE_ROW.match(line):
            out[where].append(line)
    return out


def load_tabs(design: Path = DESIGN) -> list[Tab]:
    tabs = []
    for path in sorted(design.glob("[0-9][0-9]-*.md")):
        no = int(path.name[:2])
        if no not in PREFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0] if text else ""
        tab = Tab(path=path, no=no, title=first.lstrip("# ").strip())
        parts = _sections(text)
        for line in parts["rules"]:
            cells = _cells(line) + [""] * 5
            tab.rules.append(Rule(*cells[:5], file=path.name))
        tab.provisional = [_cells(line) for line in parts["provisional"]]
        tabs.append(tab)
    return tabs


def _summary(text: str) -> str:
    text = text.replace("|", "／")
    head = text.split("。")[0]
    if len(head) > SUMMARY_CHARS:
        head = head[:SUMMARY_CHARS]
        if head.count("`") % 2:  # コードの名前の途中で切ったときは閉じる
            head += "`"
        head += "…"
    return head


def render_index(tabs: list[Tab]) -> str:
    lines = [
        "# 設計書の決まりの索引",
        "",
        "このファイルは `python tools/spec/design_index.py write` で作ります。"
        "手で直さないでください。",
        "決まりの全文は各ファイルの「決まりの一覧」にあり、"
        "`python tools/spec/design_index.py show <番号>` でも出せます。",
        "決まりはここでは頭の一文だけを、長いものは途中まで載せています。",
        "",
    ]
    for tab in tabs:
        provisional = {row[0] for row in tab.provisional}
        lines += [
            f"## [{tab.title}]({tab.path.name})",
            "",
            f"決まり {len(tab.rules)} 個、うち仮の決まりの表にあるもの {len(provisional)} 個。",
            "",
            "| 番号 | 決まり（頭） | 状態 | 試験 |",
            "| --- | --- | --- | --- |",
        ]
        for r in tab.rules:
            mark = "（仮の決まりの表あり）" if r.number in provisional else ""
            status = _summary(r.status) + mark
            tests = "、".join(sorted({m.group(1) for m in _TEST.finditer(r.tests)})) or r.tests
            lines.append(f"| {r.number} | {_summary(r.text)} | {status} | {tests} |")
        lines.append("")
    return "\n".join(lines)


def _setting_names() -> set[str]:
    path = REPO / "tools" / "spec" / "settings.py"
    spec = importlib.util.spec_from_file_location("_spec_settings", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.flatten(yaml.safe_load((REPO / "game.yaml").read_text(encoding="utf-8"))))


def problems(tabs: list[Tab], *, check_index: bool = True) -> list[str]:
    out: list[str] = []
    seen: dict[str, str] = {}
    names = _setting_names()
    for tab in tabs:
        name_of = tab.path.name
        prefix = PREFIXES[tab.no]
        numbers = {r.number for r in tab.rules}
        for r in tab.rules:
            if not r.number.startswith(prefix + "-"):
                out.append(f"{name_of}: {r.number} の頭が {prefix} でない")
            if r.number in seen:
                out.append(f"{name_of}: {r.number} が重なっている（{seen[r.number]} にもある）")
            seen.setdefault(r.number, tab.path.name)
            for m in _TEST.finditer(r.tests):
                test_file = REPO / m.group(1)
                if not test_file.exists():
                    out.append(f"{name_of}: {r.number} の試験のファイル {m.group(1)} がない")
                elif m.group(2) and not re.search(
                    rf"^\s*def {m.group(2)}\b", test_file.read_text(encoding="utf-8"), re.M
                ):
                    out.append(f"{name_of}: {r.number} の試験 {m.group(0)} がない")
            for m in _SETTING.finditer(r.settings):
                name = m.group(1)
                if _NOT_SETTING.search(name):
                    continue
                if name not in names and not any(
                    n.startswith((name + ".", name + "[")) for n in names
                ):
                    out.append(f"{name_of}: {r.number} の設定の名前 {name} が game.yaml にない")
        for row in tab.provisional:
            if row[0] not in numbers:
                out.append(f"{name_of}: 仮の決まりの {row[0]} が決まりの一覧にない")
    if check_index:
        want = render_index(tabs)
        have = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
        if have.rstrip("\n") != want.rstrip("\n"):
            out.append("INDEX.md が本文とずれている（design_index.py write で作り直す）")
    return out


def show(tabs: list[Tab], number: str) -> str:
    out = []
    for tab in tabs:
        for r in tab.rules:
            if r.number == number:
                out.append(f"{tab.path.relative_to(REPO)}  {tab.title}")
                out.append(f"| {r.number} | {r.text} | {r.status} | {r.settings} | {r.tests} |")
        for row in tab.provisional:
            if row[0] == number:
                out.append("仮の決まり: | " + " | ".join(row) + " |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("write", help="docs/design/INDEX.md を作り直す")
    sub.add_parser("check", help="ずれと誤りを並べる")
    p_show = sub.add_parser("show", help="決まりの行を出す")
    p_show.add_argument("numbers", nargs="+")
    args = parser.parse_args(argv)
    tabs = load_tabs()
    if args.command == "write":
        INDEX.write_text(render_index(tabs) + "\n", encoding="utf-8")
        print(f"{INDEX.relative_to(REPO)}: 決まり {sum(len(t.rules) for t in tabs)} 個")
        return 0
    if args.command == "check":
        found = problems(tabs)
        for line in found:
            print(line)
        return 1 if found else 0
    for number in args.numbers:
        text = show(tabs, number)
        print(text or f"{number}: 見つからない")
    return 0


if __name__ == "__main__":
    sys.exit(main())
