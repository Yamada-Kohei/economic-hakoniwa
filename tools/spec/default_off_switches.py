"""既定は切（false）の切り替えの一覧（docs/default-off-switches.md）を作り直す道具。

経済の決まりは、既定は切のまま入れて、切と入を比べてから既定を入にする決まりです
（GT-44）。切り替えが増えると「どれが・いつ入った物で・いまどうなっているか」が
見えにくくなるので、一覧を 1 枚に保ちます（台帳 P1、根7 の 7-b）。

この道具は game.yaml を読み、値が false の設定を全部拾って、道具の中の対応表
（中身・入れた PR・いまの状態）と突き合わせ、一覧の文を作ります。

    python tools/spec/default_off_switches.py           # 一覧を作り直す
    python tools/spec/default_off_switches.py --check   # 一覧が新しいか確かめる

game.yaml に新しい false の設定が増えたら、対応表に無いので落ちます。
そのときは対応表に 1 行足してから作り直してください（漏れを防ぐための落ち方です）。
逆に、既定が入になった・消えた切り替えが対応表に残っていても落ちます。

試験は tests/core/test_default_off_switches.py（この道具の自己確認）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
GAME_YAML = REPO / "game.yaml"
DOC = REPO / "docs" / "default-off-switches.md"

# 切り替えの名前 -> （中身, 入れた PR, いまの状態）。
# 中身は game.yaml の注釈の一言、状態は測りの結果や会議の決めを短く書く。
# 数字や状態が変わったらここを直して、一覧を作り直す。
INFO: dict[str, tuple[str, str, str]] = {
    "market.shortage_response": (
        "品切れで買えなかった買い手も指値を上げ、売り手は買えなかった量を、"
        "採算が合って増やせる施設に「増やせる余地」の割合で配る（要望B）",
        "PR #527",
        "測り済み。この形は既定にしない（要望B の測りで線1 が落ちた）。最終の判断は会議",
    ),
    "trade.trader_expand": (
        "商社の建て増し: 扱える重さで取り逃した引き合う輸入が続けば、"
        "支店・倉庫と人を足して規模を 1 つ上げる（W-308）",
        "PR #498",
        "測り済み（W-308）。条件が未達で、既定は切のまま",
    ),
    "trade.shipping_reentry": (
        "倒れた海運業を管財人の売りで船団ごと引き継ぎ、買い手が付かなければ"
        "船を国の倉庫に残し、運ぶ荷の残る港に後継の会社を置く（W-308）",
        "PR #526",
        "測り済み（W-308）。線5 の判定の仕方が定まらず保留。会議で決める",
    ),
    "trade.offtake.enabled": (
        "商社と作り手の引き取り契約。初版は石炭だけ（W-307）",
        "PR #481",
        "測り済み。入では商社の買いの合計が減る結果が出て、既定は切のまま（商社 1 つの追跡が残り）",
    ),
    "power.household_curve": (
        "家計は電気を買える分だけ買う: いつもの電気料金を真ん中に、"
        "高い段ほど少なく買う段の注文を出す（LB-47）",
        "PR #388",
        "測り直し中（LB-47 の測り直しの包みが受付で実行中）",
    ),
    "population.return_to_land": (
        "帰農: 町の住宅地の仕事のない人が、空きのある近い村へ移って自給の農業に戻る（PO-64）",
        "PR #486",
        "切のまま（W-272 を閉じたときの決め。入にする予定なし）",
    ),
    "planning.export.borrow_age_limit": (
        "1 年を超えた融通の借りが残っている計画の島は、新しく借りない（FX-86 の案 1）",
        "PR #481",
        "測り待ち（入れたときの「比べの後に既定を決める」のまま）",
    ),
}


def find_default_off(config: Any, prefix: str = "") -> list[str]:
    """設定の木から、値が false の設定の名前（点つなぎ）を並び順に拾う。"""
    found: list[str] = []
    if isinstance(config, dict):
        for key, value in config.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if value is False:
                found.append(name)
            else:
                found += find_default_off(value, name)
    return found


def load_switches(game_yaml: Path = GAME_YAML) -> list[str]:
    config = yaml.safe_load(game_yaml.read_text(encoding="utf-8"))
    return find_default_off(config)


def problems(found: list[str], info: dict[str, tuple[str, str, str]] = INFO) -> list[str]:
    """game.yaml の拾いと対応表の食い違い（あれば、その文）。"""
    out = []
    for name in found:
        if name not in info:
            out.append(f"対応表に無い切り替え: {name}（道具の INFO に 1 行足してください）")
    for name in info:
        if name not in found:
            out.append(f"game.yaml に無い切り替えが対応表に残っている: {name}")
    return out


def render(found: list[str], info: dict[str, tuple[str, str, str]] = INFO) -> str:
    """一覧の文（docs/default-off-switches.md の中身）を作る。"""
    lines = [
        "# 既定は切の切り替えの一覧",
        "",
        "経済の決まりは、既定は切のまま入れて、切と入を比べてから既定を入にする",
        "決まりです（GT-44）。この一覧は、その切り替えがどれだけあって、",
        "いまどうなっているかを 1 枚で見るための物です（台帳 P1、根7 の 7-b）。",
        "",
        "作り直しは道具でします（手で表を直さない）:",
        "",
        "    python tools/spec/default_off_switches.py",
        "",
        "game.yaml に新しい既定は切の切り替えが増えたら、道具の中の対応表",
        "（tools/spec/default_off_switches.py の INFO）に 1 行足してください。",
        "対応表に無い切り替えがあると、道具と試験が落ちます。",
        "",
        "| 名前 | 中身 | 入れた PR | いまの状態 |",
        "|---|---|---|---|",
    ]
    for name in found:
        what, pr, state = info[name]
        lines.append(f"| `{name}` | {what} | {pr} | {state} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="既定は切の切り替えの一覧を作り直す")
    parser.add_argument("--check", action="store_true", help="一覧が新しいか確かめるだけ")
    args = parser.parse_args(argv)

    found = load_switches()
    bad = problems(found)
    if bad:
        for line in bad:
            print(line)
        return 1
    text = render(found)
    if args.check:
        if DOC.exists() and DOC.read_text(encoding="utf-8") == text:
            print(f"一覧は新しい（{len(found)} 件）")
            return 0
        print("一覧が古い。python tools/spec/default_off_switches.py で作り直してください")
        return 1
    DOC.write_text(text, encoding="utf-8")
    print(f"一覧を作り直した: {DOC.relative_to(REPO)}（{len(found)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
