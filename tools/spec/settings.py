"""設定（game.yaml）と定義データ（packs/）の数字を、一覧と差分で出す道具。

数字の置き場所は game.yaml と packs/ だけです。設計書には数字を書かず、設定の名前だけを書きます。
今の数字はこの道具で見ます（この道具はファイルを読むだけで、書き換えません）。

    python tools/spec/settings.py markdown --rev b6ac87a > 設定の数字.md  # 読み物の形（節ごとの表）
    python tools/spec/settings.py list                   # game.yaml の設定をすべて（値と注釈）
    python tools/spec/settings.py list fx politics       # 名前がこれで始まる設定だけ
    python tools/spec/settings.py list --file packs/base/data/facilities.yaml bank
    python tools/spec/settings.py diff origin/main       # そのコミットから変わった設定
    python tools/spec/settings.py diff origin/main HEAD  # 2つのコミットの間で変わった設定

diff は game.yaml と packs/ の YAML を比べます。名前は「fx.band」のように点でつなぎ、
並びの中の要素は「stages[3]」のように番号で書きます。説明は YAML の行の後ろの注釈から取ります。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GAME_YAML = "game.yaml"
PACKS_DIR = "packs"

MISSING = object()

_KEY_LINE = re.compile(
    r"^(?P<key>[^\s#:'\"\[\]{},][^#:]*?|'[^']*'|\"[^\"]*\"):(?:\s+(?P<rest>.*))?$"
)


def flatten(data: object, prefix: str = "") -> dict[str, object]:
    """入れ子の設定を「fx.band」のような名前と値の組にする。

    中身がすべて値（数・文字列など）の並びは1つの値のまま残し、辞書を含む並びは番号で分ける。
    """
    out: dict[str, object] = {}
    if isinstance(data, dict):
        if not data and prefix:
            out[prefix] = {}
        for key, value in data.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(value, name))
    elif isinstance(data, list) and any(isinstance(x, dict | list) for x in data):
        for i, value in enumerate(data):
            out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = data
    return out


def _split_comment(text: str) -> tuple[str, str]:
    """「値  # 注釈」を値と注釈に分ける。引用符の中の # は注釈と見なさない。"""
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip(), text[i + 1 :].strip()
    return text.rstrip(), ""


def _join(parts: list[str]) -> str:
    """折り返した注釈の行をつなぐ。英数字どうしの間にだけ空白を入れる。"""
    out = ""
    for part in parts:
        if out and part and out[-1].isascii() and out[-1].isalnum() and part[0].isascii():
            out += " "
        out += part
    return out


def comments(text: str) -> dict[str, str]:
    """YAML の注釈を、設定の名前ごとに集める。

    行の後ろの注釈を使い、なければ、その設定のすぐ上に同じ字下げで続く注釈だけの行を使う。
    注釈だけの行が、注釈の付いた行の直後に深い字下げで続くときは、その注釈の続きと見なす。
    """
    found: dict[str, str] = {}
    stack: list[tuple[int, str]] = []  # (字下げ, 名前の部分)
    counters: dict[str, int] = {}
    last: tuple[str, int] | None = None  # 直前に注釈を記録した (名前, 字下げ)
    block_indent: int | None = None  # | や > の文字列の中を読み飛ばす
    lead: list[str] = []  # すぐ上に続く注釈だけの行
    lead_indent = -1

    def path_of(parts: list[str]) -> str:
        out = ""
        for part in parts:
            out = out + part if part.startswith("[") or not out else f"{out}.{part}"
        return out

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            lead = []
            continue
        indent = len(line) - len(line.lstrip(" "))
        line_indent = indent
        body = line.lstrip(" ")
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        if body.startswith("#"):
            text_part = body.lstrip("#").strip()
            if last is not None and indent > last[1]:
                found[last[0]] = _join([found[last[0]], text_part])
                continue
            last = None
            if lead and indent == lead_indent:
                lead.append(text_part)
            else:
                lead, lead_indent = [text_part], indent
            continue
        above = _join(lead) if lead and lead_indent == line_indent else ""
        lead = []
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if body.startswith("- ") or body == "-":
            parent = path_of([part for _, part in stack])
            index = counters.get(parent, 0)
            counters[parent] = index + 1
            stack.append((indent, f"[{index}]"))
            body = body[2:].lstrip(" ")
            indent += 2
            if not body:
                last = None
                continue
            match = _KEY_LINE.match(body)
            if match is None:
                value, note = _split_comment(body)
                name = path_of([part for _, part in stack])
                if not note and above:
                    found[name] = above
                if note:
                    found[name] = note
                    last = (name, indent - 2)
                else:
                    last = None
                continue
        else:
            match = _KEY_LINE.match(body)
            if match is None:
                last = None
                continue
        key = match.group("key").strip().strip("'\"")
        value, note = _split_comment(match.group("rest") or "")
        stack.append((indent, key))
        name = path_of([part for _, part in stack])
        if value.startswith(("|", ">")):
            block_indent = indent
        if note:
            found[name] = note
            last = (name, indent)
        else:
            if above:
                found[name] = above
            last = None
    return found


def fmt(value: object) -> str:
    """値を1行の文字に直す。"""
    if value is MISSING:
        return "（なし）"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(fmt(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, str) and value == "":
        return '""'
    return str(value)


def note_for(name: str, notes: dict[str, str]) -> str:
    """その名前の注釈。なければ、いちばん近い親の注釈（下の設定をまとめて説明しているもの）。"""
    while name:
        if name in notes:
            return notes[name]
        parent = re.sub(r"(\.[^.\[]+|\[\d+\])$", "", name)
        if parent == name:
            break
        name = parent
    return ""


def list_lines(text: str, prefixes: list[str]) -> Iterator[str]:
    """設定を「名前 = 値  # 注釈」の行で出す。並びの要素に注釈があれば1つずつ出す。"""
    values = flatten(yaml.safe_load(text) or {})
    notes = comments(text)
    for name, value in values.items():
        if prefixes and not any(name == p or name.startswith((f"{p}.", f"{p}[")) for p in prefixes):
            continue
        items = isinstance(value, list) and any(f"{name}[{i}]" in notes for i in range(len(value)))
        if items:
            note = note_for(name, notes)
            yield f"{name}:" + (f"  # {note}" if note else "")
            for i, item in enumerate(value):
                item_note = notes.get(f"{name}[{i}]", "")
                yield f"  [{i}] {fmt(item)}" + (f"  # {item_note}" if item_note else "")
            continue
        note = note_for(name, notes)
        yield f"{name} = {fmt(value)}" + (f"  # {note}" if note else "")


def diff(old: dict[str, object], new: dict[str, object]) -> list[tuple[str, object, object]]:
    """2つの flatten の結果を比べ、変わった・増えた・なくなった名前を並びの順に返す。"""
    changes = []
    for name in list(old) + [n for n in new if n not in old]:
        before = old.get(name, MISSING)
        after = new.get(name, MISSING)
        if before != after or type(before) is not type(after):
            changes.append((name, before, after))
    return changes


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _yaml_files(rev: str | None) -> list[str]:
    if rev is None:
        files = [p.relative_to(REPO).as_posix() for p in (REPO / PACKS_DIR).rglob("*.yaml")]
    else:
        files = _git("ls-tree", "-r", "--name-only", rev, "--", PACKS_DIR).split()
        files = [f for f in files if f.endswith(".yaml")]
    return [GAME_YAML, *sorted(files)]


def _read(rev: str | None, path: str) -> str | None:
    if rev is None:
        file = REPO / path
        return file.read_text(encoding="utf-8") if file.exists() else None
    try:
        return _git("show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return None


def diff_lines(old_rev: str, new_rev: str | None) -> Iterator[str]:
    """2つの版（new_rev が None なら今のファイル）の間で変わった設定を、ファイルごとに出す。"""
    paths = sorted(set(_yaml_files(old_rev)) | set(_yaml_files(new_rev)), key=_order)
    for path in paths:
        old_text = _read(old_rev, path)
        new_text = _read(new_rev, path)
        old = flatten(yaml.safe_load(old_text) or {}) if old_text is not None else {}
        new = flatten(yaml.safe_load(new_text) or {}) if new_text is not None else {}
        changes = diff(old, new)
        if not changes:
            continue
        notes = comments(new_text or old_text or "")
        yield f"## {path}"
        for name, before, after in changes:
            note = note_for(name, notes)
            yield f"{name}: {fmt(before)} → {fmt(after)}" + (f"  # {note}" if note else "")
        yield ""


def _order(path: str) -> tuple[int, str]:
    return (0 if path == GAME_YAML else 1, path)


def sections(text: str) -> list[tuple[str, str, list[str]]]:
    """game.yaml を、上の階層の設定ごとの節に分ける。

    返すのは (見出し, 説明, その節の上の階層の名前) の並び。設定の前の注釈（空行の後に続くもの）の
    初めの文が見出しで、残りが説明。注釈のない設定は名前が見出しになる。下に設定を持たない1行の設定
    （locale など）で注釈の見出しもないものは、前の節に入る。
    """
    out: list[tuple[str, str, list[str]]] = []
    pending: list[str] = []
    block_indent: int | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                continue
            block_indent = None
        if not line.strip():
            pending = []
            continue
        if indent:
            continue
        if line.startswith("#"):
            pending.append(line.lstrip("#").strip())
            continue
        match = _KEY_LINE.match(line)
        if match is None:
            pending = []
            continue
        key = match.group("key").strip().strip("'\"")
        value, _ = _split_comment(match.group("rest") or "")
        if value.startswith(("|", ">")):
            block_indent = 0
        if pending or not value or not out:
            title, rest = _first_sentence("".join(pending))
            out.append((title or key, rest, [key]))
        else:
            out[-1][2].append(key)
        pending = []
    return out


def _first_sentence(text: str) -> tuple[str, str]:
    """括弧の外にある初めの「。」までと、その後ろに分ける（「。」は見出しに残さない）。"""
    depth = 0
    for i, ch in enumerate(text):
        if ch in "（(「":
            depth += 1
        elif ch in "）)」":
            depth = max(depth - 1, 0)
        elif ch == "。" and depth == 0:
            return text[:i], text[i + 1 :].strip()
    return text, ""


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_lines(text: str, source: str, path: str = GAME_YAML) -> Iterator[str]:
    """設定を、節ごとの「名前・値・説明」の表にした読み物の形で出す。"""
    values = flatten(yaml.safe_load(text) or {})
    notes = comments(text)
    yield f"# 設定の数字（{source}）"
    yield ""
    yield (
        f"{path} から道具（`python tools/spec/settings.py markdown`）で作った写しです。"
        f"数字を変えるときは {path} を直し、この写しは作り直します。"
        f"説明は {path} の注釈そのままです。"
    )
    for title, description, keys in sections(text):
        yield ""
        if title == keys[0]:
            yield f"## `{title}`" + ("ほか" if len(keys) > 1 else "")
            description = description or notes.get(title, "")
        elif len(keys) == 1:
            yield f"## {title}（`{keys[0]}`）"
        else:
            yield f"## {title}"
        yield ""
        if description:
            yield description
            yield ""
        yield "| 名前 | 値 | 説明 |"
        yield "| --- | --- | --- |"
        previous = ""
        for name, value in values.items():
            top = re.split(r"[.\[]", name, maxsplit=1)[0]
            if top not in keys:
                continue
            if isinstance(value, list) and any(f"{name}[{i}]" in notes for i in range(len(value))):
                for i, item in enumerate(value):
                    note = notes.get(f"{name}[{i}]", "")
                    yield f"| `{name}[{i}]` | {_cell(fmt(item))} | {_cell(note)} |"
                continue
            note = note_for(name, notes)
            if name not in notes and note == notes.get(top):
                note = ""  # 節の見出しの注釈は繰り返さない
            shown = "（同上）" if note and note == previous and name not in notes else note
            previous = note
            yield f"| `{name}` | {_cell(fmt(value))} | {_cell(shown)} |"


def _source_text(rev: str | None, path: str) -> str:
    text = _read(rev, path)
    if text is None:
        raise SystemExit(f"{path} が{'今のファイルに' if rev is None else f' {rev} に'}ありません")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="設定と定義データの数字を一覧と差分で出す")
    sub = parser.add_subparsers(dest="command", required=True)
    p_md = sub.add_parser("markdown", help="節ごとの表にした読み物の形で出す")
    p_md.add_argument("--rev", help="このコミットのファイルを読む（省くと今のファイル）")
    p_md.add_argument("--file", default=GAME_YAML, help=f"読むファイル（既定 {GAME_YAML}）")
    p_list = sub.add_parser("list", help="今の設定を名前・値・注釈で出す")
    p_list.add_argument("prefix", nargs="*", help="名前がこれで始まる設定だけ出す（fx など）")
    p_list.add_argument("--file", action="append", help=f"読むファイル（既定 {GAME_YAML}）")
    p_list.add_argument("--rev", help="このコミットのファイルを読む（省くと今のファイル）")
    p_diff = sub.add_parser("diff", help="2つの版の間で変わった設定を出す")
    p_diff.add_argument("old", help="比べる元のコミット（origin/main など）")
    p_diff.add_argument("new", nargs="?", help="比べる先のコミット（省くと今のファイル）")
    args = parser.parse_args(argv)

    if args.command == "markdown":
        if args.rev:
            commit = _git("rev-parse", "--short", args.rev).strip()
            source = f"{args.file}、コミット {commit}"
        else:
            source = f"{args.file}、作業中のファイル"
        for line in markdown_lines(_source_text(args.rev, args.file), source, args.file):
            print(line)
    elif args.command == "list":
        for path in args.file or [GAME_YAML]:
            if args.file and len(args.file) > 1:
                print(f"## {path}")
            for line in list_lines(_source_text(args.rev, path), args.prefix):
                print(line)
    else:
        for line in diff_lines(args.old, args.new):
            print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # head などで出力の途中で閉じられたとき
        sys.exit(0)
