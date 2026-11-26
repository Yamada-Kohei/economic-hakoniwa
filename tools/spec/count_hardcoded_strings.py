"""直書きの文言（日本語の文字列）の件数を数え、ベースラインと比べる道具。

多言語対応の歯止め（掲示板 #6109・#6112）。新しい文言は多言語の形
（テンプレートの translate、パックのロケールなど）で書く決まり（#5524）を、
「直書きがベースラインより増えたら落ちる」試験で守らせるための台本。
試験は tests/core/test_i18n_guard.py、CI では design の仕事で毎回回る。

数え方（系統ごとに、遊ぶ人に見える文言だけを数える）:
- Python（server/ と hakoniwa_core/ の .py、試験のファイルは除く）:
  AST で読み、docstring を除いた文字列定数のうち日本語を含むものを数える。
  f文字列は断片を二重に数えず、式全体で1件とする。
  gettext / gettext_lazy / ngettext などの直接の引数は、翻訳の形で
  書かれた文言なので数えない。
- JS（server/static/js/ の .js）: 簡易スキャナで文字列リテラルと
  テンプレートリテラルを読み、日本語を含むものを数える。コメントは除く。
- テンプレート（server/ の templates/ の .html）: {% translate %} と
  {% blocktranslate %}（分離済み）と {# #} コメントを取り除いたあとの
  地の文のうち、日本語を含む断片を数える。

件数は「出現件数」で、同じ文言が2か所にあれば2件とする（棚卸し #6108 の
ユニーク数とは数え方が違うので、総数は棚卸しより少し大きくなる）。
ベースラインはファイルごとの件数で持ち、どのファイルで増えても落ちる。
直書きを減らしたときは落ちない（ベースラインの更新は任意）。

    python tools/spec/count_hardcoded_strings.py                 # 内訳の表示と照合
    python tools/spec/count_hardcoded_strings.py --write-baseline  # ベースラインの作り直し

ベースラインの作り直しは、直書きの増加を意図して入れる変更のときだけ行い、
増えたファイルと理由を PR に書くこと。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("hardcoded_strings_baseline.json")
BASELINE_VERSION = 1

# ひらがな・カタカナ・漢字のどれかを含む文字列を「日本語の文言」とする。
JA_RE = re.compile(r"[぀-ヿ一-鿿]")

SYSTEMS = ("python", "js", "template")


def _is_japanese(text: str) -> bool:
    return bool(JA_RE.search(text))


# ---------------------------------------------------------------- Python


# gettext 系の呼び出しの直接の引数は、翻訳の形で書かれた文言なので数えない。
_GETTEXT_FUNCS = {"gettext", "gettext_lazy", "gettext_noop", "ngettext", "ngettext_lazy"}


def count_python_text(text: str) -> int:
    """Python のソース1件分の直書き文言の件数。"""
    tree = ast.parse(text)
    docstring_ids: set[int] = set()
    fragment_ids: set[int] = set()
    gettext_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_ids.add(id(first.value))
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant):
                    fragment_ids.add(id(part))
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _GETTEXT_FUNCS:
                for arg in node.args:
                    gettext_ids.add(id(arg))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids or id(node) in fragment_ids or id(node) in gettext_ids:
                continue
            if _is_japanese(node.value):
                count += 1
        elif isinstance(node, ast.JoinedStr):
            if id(node) in gettext_ids:
                continue
            joined = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _is_japanese(joined):
                count += 1
    return count


def _python_files(root: Path) -> list[Path]:
    files = []
    for base in ("server", "hakoniwa_core"):
        for path in sorted((root / base).rglob("*.py")):
            parts = path.relative_to(root).parts
            if "tests" in parts or path.name == "conftest.py":
                continue
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                continue
            files.append(path)
    return files


# -------------------------------------------------------------------- JS


def count_js_text(text: str) -> int:
    """JavaScript のソース1件分の直書き文言の件数。

    文字列・テンプレートリテラル・正規表現・コメントを区別する簡易スキャナ。
    `/` が正規表現か割り算かは、直前が値（識別子・数・文字列・閉じ括弧）か
    どうかで決める決め打ち。正規表現の中の引用符は文字クラスまで見て読み飛ばす。
    """
    count = 0
    i, n = 0, len(text)
    # テンプレートリテラルの ${ } のための積み木。
    # ("tpl", 静的断片の蓄積) と ("expr", 波括弧の深さ) を積む。
    stack: list = []
    last_operand = False  # 直前が値なら / は割り算、それ以外なら正規表現
    while i < n:
        c = text[i]
        if stack and stack[-1][0] == "tpl":
            if c == "\\":
                stack[-1][1].append(text[i : i + 2])
                i += 2
                continue
            if c == "`":
                if _is_japanese("".join(stack[-1][1])):
                    count += 1
                stack.pop()
                last_operand = True
                i += 1
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "{":
                stack.append(("expr", 0))
                last_operand = False
                i += 2
                continue
            stack[-1][1].append(c)
            i += 1
            continue
        # ここからコード側（${ } の中を含む）
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and text[i + 1 : i + 2] == "/":
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            continue
        if c == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c == "/" and not last_operand:
            # 正規表現リテラルを読み飛ばす
            i += 1
            in_class = False
            while i < n:
                d = text[i]
                if d == "\\":
                    i += 2
                    continue
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    i += 1
                    break
                elif d == "\n":
                    break
                i += 1
            last_operand = True
            continue
        if c in "'\"":
            j = i + 1
            buf: list[str] = []
            while j < n:
                d = text[j]
                if d == "\\":
                    buf.append(text[j : j + 2])
                    j += 2
                    continue
                if d == c or d == "\n":
                    break
                buf.append(d)
                j += 1
            if _is_japanese("".join(buf)):
                count += 1
            i = j + 1
            last_operand = True
            continue
        if c == "`":
            stack.append(("tpl", []))
            i += 1
            continue
        if stack and stack[-1][0] == "expr":
            if c == "{":
                stack[-1] = ("expr", stack[-1][1] + 1)
                last_operand = False
                i += 1
                continue
            if c == "}":
                if stack[-1][1] == 0:
                    stack.pop()
                    i += 1
                    continue
                stack[-1] = ("expr", stack[-1][1] - 1)
                last_operand = True
                i += 1
                continue
        if c.isalnum() or c in "_$":
            while i < n and (text[i].isalnum() or text[i] in "_$"):
                i += 1
            last_operand = True
            continue
        last_operand = c in ")]}"
        i += 1
    return count


def _js_files(root: Path) -> list[Path]:
    return sorted((root / "server" / "static" / "js").rglob("*.js"))


# ------------------------------------------------------------- テンプレート

_COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
_BLOCKTRANSLATE_RE = re.compile(
    r"\{%\s*blocktranslate\b.*?%\}.*?\{%\s*endblocktranslate\s*%\}", re.S
)
_TRANSLATE_RE = re.compile(r"\{%\s*(?:translate|trans)\b[^%]*%\}")
_TAG_RE = re.compile(r"\{%.*?%\}", re.S)
_VAR_RE = re.compile(r"\{\{.*?\}\}", re.S)
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def count_template_text(text: str) -> int:
    """Django テンプレート1件分の、地の文の直書き文言の件数。"""
    text = _COMMENT_RE.sub("", text)
    text = _BLOCKTRANSLATE_RE.sub("", text)
    text = _TRANSLATE_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = _VAR_RE.sub("", text)
    count = 0
    for segment in _HTML_TAG_RE.split(text):
        if _is_japanese(segment.strip()):
            count += 1
    return count


def _template_files(root: Path) -> list[Path]:
    return sorted(p for p in (root / "server").rglob("*.html") if "templates" in p.parts)


# ------------------------------------------------------------------ 集計

_COUNTERS = {
    "python": (_python_files, count_python_text),
    "js": (_js_files, count_js_text),
    "template": (_template_files, count_template_text),
}


def count_all(root: Path = REPO) -> dict[str, dict[str, int]]:
    """系統ごと・ファイルごとの件数。0件のファイルは含めない。"""
    result: dict[str, dict[str, int]] = {}
    for system, (find_files, count_text) in _COUNTERS.items():
        per_file: dict[str, int] = {}
        for path in find_files(root):
            count = count_text(path.read_text(encoding="utf-8"))
            if count:
                per_file[path.relative_to(root).as_posix()] = count
        result[system] = per_file
    return result


def area_of(system: str, relpath: str) -> str:
    """内訳表示のための「置き場所」。"""
    parts = relpath.split("/")
    if system == "python":
        return "/".join(parts[:2]) if parts[0] == "server" else parts[0]
    if system == "js":
        rest = parts[3:]  # server/static/js/ の次
        return rest[0] if len(rest) > 1 else "（直下）"
    # template: templates の直下の階（画面のまとまり）
    idx = parts.index("templates")
    return parts[idx + 1] if idx + 2 < len(parts) else "（直下）"


def totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    result = {system: sum(counts[system].values()) for system in SYSTEMS}
    result["all"] = sum(result.values())
    return result


def areas(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for system in SYSTEMS:
        per_area: dict[str, int] = {}
        for relpath, count in counts[system].items():
            area = area_of(system, relpath)
            per_area[area] = per_area.get(area, 0) + count
        result[system] = dict(sorted(per_area.items()))
    return result


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_excess(
    counts: dict[str, dict[str, int]], baseline: dict
) -> list[tuple[str, str, int, int]]:
    """ベースラインを超えた（系統, ファイル, 現在, ベースライン）の一覧。"""
    excess = []
    for system in SYSTEMS:
        base_files = baseline["files"].get(system, {})
        for relpath, count in sorted(counts[system].items()):
            base = base_files.get(relpath, 0)
            if count > base:
                excess.append((system, relpath, count, base))
    return excess


def make_baseline(counts: dict[str, dict[str, int]], base_commit: str) -> dict:
    return {
        "version": BASELINE_VERSION,
        "base_commit": base_commit,
        "totals": totals(counts),
        "areas": areas(counts),
        "files": counts,
    }


def print_breakdown(counts: dict[str, dict[str, int]]) -> None:
    total = totals(counts)
    per_area = areas(counts)
    labels = {"python": "Python", "js": "JS", "template": "テンプレートの地の文"}
    for system in SYSTEMS:
        print(f"## {labels[system]}: {total[system]} 件")
        for area, count in per_area[system].items():
            print(f"  {area}: {count}")
    print(f"## 合計: {total['all']} 件")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直書き文言の件数の集計と照合")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="現在の件数でベースラインを作り直す",
    )
    parser.add_argument("--base-commit", default="", help="ベースラインに残す起点の sha")
    args = parser.parse_args(argv)

    counts = count_all()
    print_breakdown(counts)
    if args.write_baseline:
        baseline = make_baseline(counts, args.base_commit)
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"ベースラインを書き直しました: {BASELINE_PATH}")
        return 0
    baseline = load_baseline()
    excess = find_excess(counts, baseline)
    if excess:
        print("\n直書きがベースラインより増えています:")
        for system, relpath, count, base in excess:
            print(f"  [{system}] {relpath}: {base} → {count}")
        print("新しい文言は多言語の形で書くか、意図した増加なら")
        print("--write-baseline でベースラインを更新し、理由を PR に書いてください。")
        return 1
    print("\n直書きはベースライン以内です。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
