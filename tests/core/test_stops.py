"""止まった理由の表（hakoniwa_core/stops.py。W-34・GT-42）。

計算に効く判定（休業手当・解雇しないで抱える人数・原料待ち・操業目標・標準の計画）は、表に替える
前の関数（下の OLD_*。2026-11-14 の main の facilities.py・market・autoplan の写し）を正解として、
理由の名前すべてと「:」の後の形（財・電力・予算の項目・なし）を掛け合わせて同じ答えになるかを見る。
表に替える前の関数は、ここに正解として残す。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hakoniwa_core import stops
from hakoniwa_core.modules import labor
from hakoniwa_core.units import BASIS
from tests.core.test_dismissal import _mill
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import run

ROOT = Path(__file__).resolve().parents[2]

# --- 表に替える前の判定（正解） --------------------------------------------------------

OLD_INPUT_SHORT = ("input", "input_price", "funds", "public_price")
OLD_FURLOUGH = ("power", "no_grid", "input")


def old_short_of_inputs(status: str) -> bool:
    return status.partition(":")[0] in OLD_INPUT_SHORT


def old_furloughed(config, status: str) -> bool:
    kind, _, good = status.partition(":")
    power = config.get("power.good")
    return kind in OLD_FURLOUGH or (kind in ("input_price", "funds") and good == power)


def old_caps_target(status: str) -> bool:  # modules/market の operating_target
    return old_short_of_inputs(status) or status in ("power", "no_grid")


def old_waits_for_input(status: str, power: str) -> bool:  # autoplan の _waits_for_input
    return old_short_of_inputs(status) and status.partition(":")[2] != power


def statuses(power: str) -> list[str]:
    """書く所が作る形の status をみな（理由の名前 × 「:」の後の形）と、表にない名前・空。"""
    out = ["", "mystery", "mystery:coal"]
    for key, row in stops.REASONS.items():
        if row.tail == stops.GOOD:
            out += [f"{key}:coal", f"{key}:{power}"]
        elif row.tail == stops.ITEM:
            out += [f"{key}:education", f"{key}:wages"]
        else:
            out.append(key)
    return out


# --- 計算の判定 ---------------------------------------------------------------------


def test_calculation_rules_match_the_old_functions(game):
    config = game.config
    power = config.get("power.good")
    for status in statuses(power):
        assert stops.furloughed(config, status) == old_furloughed(config, status), status
        assert stops.short_of_inputs(config, status) == old_short_of_inputs(status), status
        assert stops.caps_target(config, status) == old_caps_target(status), status
        waits = stops.short_of_inputs(config, status) and stops.short_good(status) != power
        assert waits == old_waits_for_input(status, power), status
        if old_short_of_inputs(status):
            assert stops.short_good(status) == status.split(":", 1)[1], status


def test_rule_does_not_look_at_ext_and_show_only_changes_the_group(game):
    """計算の判定は ext（止めたわけ）で変わらない。戦争・ミサイル・占領は表示の組だけ戦争にする。"""
    config = game.config
    for reason in (None, "riot", *stops.WAR_REASONS, "disaster"):
        ext = {} if reason is None else {"halt_reason": reason}
        row = stops.show(config, stops.HALTED, ext)
        assert row.furlough == stops.rule(config, stops.HALTED).furlough
        assert row.input_short is False and row.caps_target is False
        assert row.group == (stops.WAR if reason in stops.WAR_REASONS else stops.G_HALTED)


def test_unknown_names_fall_back_to_other_without_changing_the_calculation(game):
    config = game.config
    for status in ("mystery", "mystery:coal", "old_reason:electricity"):
        row = stops.rule(config, status)
        assert row is stops.UNKNOWN
        assert not (row.furlough or row.input_short or row.caps_target)
    assert stops.rule(config, "") is stops.BLANK
    assert not stops.stopped_mark(config, "", 0)
    assert not stops.stopped_mark(config, stops.STARTING, 0)


@pytest.mark.parametrize("cut", [0, 400])
def test_wages_follow_the_old_furlough_rule_for_every_status(game, cut):
    """賃金の段（labor.furlough_share）は、どの status でも前の判定どおりに休業手当か満額か。"""
    config = game.config
    for status in statuses(config.get("power.good")):
        _island, mill, _full = _mill(game)
        mill.operating_rate = cut
        mill.status = status
        share = labor.furlough_share(game.defs, config, mill)
        assert (share < BASIS) == old_furloughed(config, status), status


def test_hiring_holds_follow_the_old_furlough_rule_for_every_status(game):
    """雇用の段（解雇しないで休ませる人数）も、どの status でも前の判定どおり。"""
    config = game.config
    kept = {}
    for sample in ("input:iron_ore", "input_price:iron_ore"):  # 休業手当にする・しない の見本
        island, mill, _full = _mill(game)
        mill.economy["target"] = 1000
        mill.stock.clear()
        mill.status = sample
        run(game, island, ["employment"])
        kept[old_furloughed(config, sample)] = mill.workers
    assert kept[True] != kept[False]
    for status in statuses(config.get("power.good")):
        island, mill, _full = _mill(game)
        mill.economy["target"] = 1000
        mill.stock.clear()
        mill.status = status
        run(game, island, ["employment"])
        assert mill.workers == kept[old_furloughed(config, status)], status


# --- 書く所 ---------------------------------------------------------------------------

REASON_TEXTS = set(stops.REASONS)


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def test_no_status_is_written_as_a_bare_string():
    """施設の status は表の定数で書く（文字の直書きで、表にない理由が生まれないように）。"""
    found = []
    for path in sorted((ROOT / "hakoniwa_core").rglob("*.py")):
        if path.name == "stops.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.keyword) and node.arg == "status":
                targets = [(None, node.value)]
            for target, value in targets:
                if target is not None and not (
                    isinstance(target, ast.Attribute) and target.attr == "status"
                ):
                    continue
                for leaf in ast.walk(value):
                    text = _literal(leaf)
                    if text is not None and (text == "" or text.partition(":")[0] in REASON_TEXTS):
                        found.append(f"{path.relative_to(ROOT)}:{node.lineno} {text!r}")
    assert not found, found


def test_production_names_reasons_only_through_the_table():
    """生産の段は理由の名前を直書きしない（cause への代入と、理由を返す所）。"""
    path = ROOT / "hakoniwa_core/modules/production/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    # 理由を返す関数（市場で買えなかったわけ supply・price・funds を返す関数は別なので見ない）
    deciders = {"_prepare", "_labor_cause", "_short"}
    returns = {
        id(r)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name in deciders
        for r in ast.walk(fn)
        if isinstance(r, ast.Return)
    }
    for node in ast.walk(tree):
        values = []
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "cause" for t in node.targets
        ):
            values = [node.value]
        elif isinstance(node, ast.Return) and id(node) in returns and node.value is not None:
            values = [node.value]
        for value in values:
            for leaf in ast.walk(value):
                text = _literal(leaf)
                if text is not None and text in REASON_TEXTS:
                    found.append(f"{node.lineno} {text!r}")
    assert not found, found


def test_statuses_written_by_production_are_in_the_table(game):
    island, mill, _full = _mill(game)
    mill.stock.clear()
    run(game, island, ["production"])
    for f in island.facilities:
        assert stops.rule(game.config, f.status) is not stops.UNKNOWN, f.status


def test_every_row_has_a_group_and_the_tables_agree():
    for row in [*stops.REASONS.values(), *stops.POWER_ROWS.values()]:
        assert row.group in stops.GROUPS, row.key
    for key, row in stops.POWER_ROWS.items():
        base = stops.REASONS[key]
        assert row.on_power and row.furlough and row.tail == base.tail
        assert row.input_short == base.input_short and row.caps_target == base.caps_target
