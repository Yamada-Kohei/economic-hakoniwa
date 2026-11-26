"""コミットの「更新履歴:」の行に、遊ぶ人向けでない言葉が混ざっていないか調べる道具。

本番の「更新のお知らせ」には、コミットの「更新履歴:」の行がそのまま写ります。
そのため、書かない理由のメモ（「〜のため足さない」）や、PR・コミット・試験といった
内側の言葉がその行に入っていると、そのまま遊ぶ人に出てしまいます（2026-11-26 に実際に起きた）。
この道具は、指定した範囲のコミット（マージは除く）の「更新履歴:」の行を集め、
禁止語が入っていたら並べて終わりの値 1 を返します。

    python tools/spec/check_changelog.py origin/main..HEAD   # 範囲を渡す
    python tools/spec/check_changelog.py                      # 既定は origin/main..HEAD

決まり（CONTRIBUTING.md の「PR」）:
- 「更新履歴:」の行は、遊ぶ人に見える変更のときだけ書く。書かないときは行そのものを書かない。
- 既定は切の切り替えには書かず、入にするコミットで書く。
- 文は遊ぶ人の言葉で。PR・コミット・試験・関数の名前を入れない。
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# 「更新履歴:」の行に入っていたら落とす言葉。
# 「足さない／足しません」は、書かない理由のメモがそのまま写る事故を防ぐためのもの。
FORBIDDEN = ("足さない", "足しません", "PR", "コミット", "試験", "テスト")

PREFIXES = ("更新履歴:", "更新履歴：")


def changelog_lines(message: str) -> list[str]:
    """コミットの文から「更新履歴:」の行（行全体）を取り出す。"""
    lines = []
    for raw in message.splitlines():
        line = raw.strip()
        if line.startswith(PREFIXES):
            lines.append(line)
    return lines


def problems_in_message(message: str) -> list[tuple[str, str]]:
    """（問題の行, 入っていた禁止語）の組の一覧。問題がなければ空。"""
    found = []
    for line in changelog_lines(message):
        for word in FORBIDDEN:
            if word in line:
                found.append((line, word))
    return found


def commit_messages(rev_range: str) -> list[tuple[str, str]]:
    """範囲のコミット（マージ除く）の（短いハッシュ, 文）の組を新しい順に返す。"""
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=%h%x00%B%x1e", rev_range],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        short, _, message = record.partition("\x00")
        commits.append((short.strip(), message))
    return commits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "rev_range",
        nargs="?",
        default="origin/main..HEAD",
        help="調べるコミットの範囲（既定: origin/main..HEAD）",
    )
    args = parser.parse_args(argv)

    bad = 0
    for short, message in commit_messages(args.rev_range):
        for line, word in problems_in_message(message):
            print(f"{short}: 禁止語「{word}」が入っています: {line}")
            bad += 1
    if bad:
        print(
            f"「更新履歴:」の行に禁止語が {bad} 件あります。"
            "遊ぶ人の言葉に直すか、書かない場合は行そのものを消してください"
            "（理由のメモは「更新履歴」の語を使わずに本文へ）。",
            file=sys.stderr,
        )
        return 1
    print("「更新履歴:」の行に禁止語はありません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
