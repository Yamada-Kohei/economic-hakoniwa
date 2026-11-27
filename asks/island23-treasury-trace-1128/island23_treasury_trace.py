"""島23 国庫トレース（2026-11-28。掲示板 #5940。読むだけ。計算は変えない）:

仕事 738 / 773（包み w308-reentry-k2-off-1126、場面 plan、種 18、1200 ターン）で
島 23 の国庫勘定（gov/treasury/cb）が負になって帳簿検査（LedgerCheckFailed）で
落ちた件の原因調査用トレース台本。

元の w308_reentry_probe.py と同じ手順で世界を生成し、同じ手順でターンを進める。
ゲームの計算や乱数・状態には一切触れず、対象島（既定: 島 23）について次だけを
summary JSON（--out）へ書き出す:

1. 毎ターン（ターン 0 の初期状態〜終了または例外発生ターン）の勘定残高の推移:
   - 国庫（gov/treasury/cb）
   - 政府の市中銀行預金合計（gov/deposit/bank:*）、政府のお金合計（国庫＋預金）、
     使えるお金（funds.spendable）、国債発行残高
   - 中央銀行側の勘定（cb/issued/treasury、cb/issued/reserve、cb/issued/nr_deposit、
     cb_money、中銀保有国債、中銀貸出）
   - そのターンの仕訳再生中における gov/treasury/cb の最小値
2. 監視対象（国庫・政府預金・政府のお金合計・cb_money・中銀資産）のいずれかが
   最初に負に転じたターン、および LedgerCheckFailed 等の例外が出たターンについて:
   - 負に転じた瞬間の仕訳（index・段・種・借方・貸方・額・前後残高）
   - その直前の仕訳（--pre-entries 件、既定 50 件）
   - そのターンで gov/treasury/cb または gov/deposit/* に触れた全仕訳と残高推移
   - 仕訳の種類ごとの国庫の増減集計
   - 段（stage）ごとの残高推移（Engine の段呼び出しを読み取り専用で観察）
   - 期首・期末の gov/*・cb/* 勘定一覧、fiscal / committed の scratch、ターンログ

LedgerCheckFailed 等でターン処理が中断された場合も、その時点の島の状態と仕訳を
記録したうえで --out に JSON を書き出し、終了コード 0 で終える（受付が成果物を
回収できるようにするため）。

使い方:
    python island23_treasury_trace.py --scenario plan --seed 18 --turns 1200 \
        --island 23 --label K2off --out island23-trace-K2off-plan-s18-t1200.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hakoniwa_core import ledger, measure
from hakoniwa_core.economy import debt, funds
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world

CB_ISSUED_TREASURY = ledger.issued(ledger.CB, ledger.TREASURY)
CB_ISSUED_RESERVE = ledger.issued(ledger.CB, ledger.RESERVE)
CB_ISSUED_NR_DEPOSIT = ledger.issued(ledger.CB, ledger.NONRESIDENT)
GOV_DEPOSIT_PREFIX = f"{ledger.GOV}/{ledger.DEPOSIT}/"
CB_LOAN_PREFIX = f"{ledger.CB}/{ledger.CB_LOAN}/"
CB_BOND_PREFIX = f"{ledger.CB}/bond:"

TRACE_COLS = [
    "turn",                   # ターン番号（0 は初期状態）
    "treasury",               # gov/treasury/cb 残高（国庫）
    "gov_deposited",          # gov/deposit/bank:* 合計（市中銀行への政府預金）
    "gov_money",              # 政府のお金合計（treasury + gov_deposited）
    "gov_spendable",          # 今使える政府のお金（treasury + 引き出し可能預金）
    "gov_bonds",              # 国債発行残高
    "cb_issued_treasury",     # cb/issued/treasury 残高（通常は -treasury <= 0）
    "cb_issued_reserve",      # cb/issued/reserve 残高（通常は -準備預金合計 <= 0）
    "cb_issued_nr_deposit",   # cb/issued/nr_deposit 残高（通常は -非居住者預金合計 <= 0）
    "cb_money",               # 中央銀行マネー合計（準備預金 + 国庫）
    "cb_bonds",               # 中央銀行が保有する国債合計
    "cb_loans",               # 中央銀行の市中銀行への貸出残高
    "treasury_min_in_turn",   # そのターンの仕訳再生中における gov/treasury/cb の最小値
    "journal_count",          # そのターンの対象島の仕訳総数
    "treasury_journal_count", # そのターンで gov/treasury/cb に触れた仕訳数
    "check_ok",               # 1: 正常終了 / 0: 例外で中断
]


def _json_safe(obj: Any) -> Any:
    """JSON 出力で落ちないように安全なプリミティブへ変換する。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    return str(obj)


def _snapshot_stage(island) -> dict[str, int]:
    """段の区切りで軽く取る残高スナップショット（対象島のみ、読み取り専用）。"""
    accounts = island.accounts
    treasury = accounts.get(ledger.TREASURY_ACCOUNT, 0)
    gov_deposited = sum(funds.deposited(island).values())
    cb_issued_treasury = accounts.get(CB_ISSUED_TREASURY, 0)
    cb_issued_reserve = accounts.get(CB_ISSUED_RESERVE, 0)
    cb_issued_nr_deposit = accounts.get(CB_ISSUED_NR_DEPOSIT, 0)
    return {
        "treasury": treasury,
        "gov_deposited": gov_deposited,
        "gov_money": treasury + gov_deposited,
        "cb_issued_treasury": cb_issued_treasury,
        "cb_issued_reserve": cb_issued_reserve,
        "cb_issued_nr_deposit": cb_issued_nr_deposit,
        "cb_money": -cb_issued_reserve + treasury,
    }


def _snapshot_full(island) -> dict[str, Any]:
    """ターン単位で取る詳細な残高スナップショット（読み取り専用）。"""
    accounts = island.accounts
    treasury = accounts.get(ledger.TREASURY_ACCOUNT, 0)
    deposited_by_bank = funds.deposited(island)
    gov_deposited = sum(deposited_by_bank.values())
    gov_money = treasury + gov_deposited
    gov_spendable = funds.spendable(island)
    gov_bonds = debt.total_debt(island)
    cb_issued_treasury = accounts.get(CB_ISSUED_TREASURY, 0)
    cb_issued_reserve = accounts.get(CB_ISSUED_RESERVE, 0)
    cb_issued_nr_deposit = accounts.get(CB_ISSUED_NR_DEPOSIT, 0)
    cb_money = ledger.aggregates(accounts)["cb_money"]
    cb_bonds = sum(debt.holdings_of(island, ledger.CB).values())
    cb_loans = sum(ledger.holdings(island, ledger.CB, f"{ledger.CB_LOAN}/").values())
    return {
        "treasury": treasury,
        "gov_deposited": gov_deposited,
        "gov_money": gov_money,
        "gov_spendable": gov_spendable,
        "gov_bonds": gov_bonds,
        "gov_deposits_by_bank": dict(deposited_by_bank),
        "cb_issued_treasury": cb_issued_treasury,
        "cb_issued_reserve": cb_issued_reserve,
        "cb_issued_nr_deposit": cb_issued_nr_deposit,
        "cb_money": cb_money,
        "cb_bonds": cb_bonds,
        "cb_loans": cb_loans,
    }


def _gov_cb_accounts(accounts: dict[str, int]) -> dict[str, int]:
    """gov または cb が持ち手・出し手・持ち物に関わる非ゼロ勘定を抜き出す。"""
    out = {}
    for name in sorted(accounts):
        val = accounts[name]
        if not val:
            continue
        holder, item, issuer = ledger.parse(name)
        if holder in (ledger.GOV, ledger.CB) or issuer in (ledger.GOV, ledger.CB) or item == ledger.TREASURY:
            out[name] = val
    return out


def _stage_for_index(stage_spans: list[dict[str, Any]], idx: int) -> str:
    """仕訳の通し番号 idx が属する段（stage）の名前を返す。"""
    for sp in stage_spans:
        if sp["journal_start"] <= idx < sp["journal_end"]:
            return sp["stage"]
    return "unknown"


def _is_watched_cb_asset(name: str) -> bool:
    return name.startswith(CB_LOAN_PREFIX) or name.startswith(CB_BOND_PREFIX)


def _replay_turn(
    opening: dict[str, int],
    journal: list[Any],
    stage_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    """1 ターン分の仕訳を期首残高から再生し、負への転落を検出する（高速・読み取り専用）。

    監視対象（通常は >= 0 であるべき残高）:
    - treasury: gov/treasury/cb
    - gov_deposit: いずれかの gov/deposit/bank:* 勘定
    - gov_money: treasury + sum(gov/deposit/bank:*)
    - cb_money: sum(*/reserve/cb) + treasury（aggregates["cb_money"] と一致）
    - cb_asset: cb/bond:* または cb/cb_loan/*
    """
    treasury = opening.get(ledger.TREASURY_ACCOUNT, 0)
    gov_deposits: dict[str, int] = {}
    reserves_total = 0
    cb_assets: dict[str, int] = {}
    for name, val in opening.items():
        if not val:
            continue
        if name.startswith(GOV_DEPOSIT_PREFIX):
            gov_deposits[name] = val
        elif name.endswith(f"/{ledger.RESERVE}/{ledger.CB}"):
            reserves_total += val
        elif _is_watched_cb_asset(name):
            cb_assets[name] = val
    gov_deposited = sum(gov_deposits.values())
    gov_money = treasury + gov_deposited
    cb_money = reserves_total + treasury

    treasury_min = treasury
    treasury_journal_count = 0
    triggers: dict[str, dict[str, Any]] = {}

    def _record_trigger(
        key: str,
        account_name: str,
        idx: int | None,
        entry: Any | None,
        before: int,
        after: int,
    ) -> None:
        if key in triggers:
            return
        if entry is None:
            triggers[key] = {
                "key": key,
                "account": account_name,
                "detected_at": "opening",
                "trigger_index": None,
                "trigger_stage": "opening",
                "trigger_entry": None,
                "balance_before": before,
                "balance_after": after,
            }
        else:
            stage = _stage_for_index(stage_spans, idx)
            triggers[key] = {
                "key": key,
                "account": account_name,
                "detected_at": "mid_turn",
                "trigger_index": idx,
                "trigger_stage": stage,
                "trigger_entry": {
                    "index": idx,
                    "stage": stage,
                    "kind": entry.kind,
                    "debit": entry.debit,
                    "credit": entry.credit,
                    "amount": entry.amount,
                    "balance_before": before,
                    "balance_after": after,
                },
            }

    # 期首時点で既に負の場合も拾う
    if treasury < 0:
        _record_trigger("treasury", ledger.TREASURY_ACCOUNT, None, None, treasury, treasury)
    for dep_name, dep_val in sorted(gov_deposits.items()):
        if dep_val < 0:
            _record_trigger("gov_deposit", dep_name, None, None, dep_val, dep_val)
            break
    if gov_money < 0:
        _record_trigger("gov_money", "gov/treasury+deposit", None, None, gov_money, gov_money)
    if cb_money < 0:
        _record_trigger("cb_money", "cb_money(reserves+treasury)", None, None, cb_money, cb_money)
    for asset_name, asset_val in sorted(cb_assets.items()):
        if asset_val < 0:
            _record_trigger("cb_asset", asset_name, None, None, asset_val, asset_val)
            break

    for idx, e in enumerate(journal):
        debit = e.debit
        credit = e.credit
        amount = e.amount
        touched_treasury = False
        touched_gov_deposit = False
        touched_cb_money = False

        prev_treasury = treasury
        prev_gov_money = gov_money
        prev_cb_money = cb_money

        if debit == ledger.TREASURY_ACCOUNT:
            treasury += amount
            touched_treasury = True
            touched_cb_money = True
        elif credit == ledger.TREASURY_ACCOUNT:
            treasury -= amount
            touched_treasury = True
            touched_cb_money = True

        if debit.startswith(GOV_DEPOSIT_PREFIX):
            prev_dep = gov_deposits.get(debit, 0)
            gov_deposits[debit] = prev_dep + amount
            gov_deposited += amount
            touched_gov_deposit = True
        if credit.startswith(GOV_DEPOSIT_PREFIX):
            prev_dep = gov_deposits.get(credit, 0)
            new_dep = prev_dep - amount
            gov_deposits[credit] = new_dep
            gov_deposited -= amount
            touched_gov_deposit = True
            if new_dep < 0:
                _record_trigger("gov_deposit", credit, idx, e, prev_dep, new_dep)

        if debit.endswith(f"/{ledger.RESERVE}/{ledger.CB}"):
            reserves_total += amount
            touched_cb_money = True
        if credit.endswith(f"/{ledger.RESERVE}/{ledger.CB}"):
            reserves_total -= amount
            touched_cb_money = True

        if _is_watched_cb_asset(debit):
            cb_assets[debit] = cb_assets.get(debit, 0) + amount
        if _is_watched_cb_asset(credit):
            prev_a = cb_assets.get(credit, 0)
            new_a = prev_a - amount
            cb_assets[credit] = new_a
            if new_a < 0:
                _record_trigger("cb_asset", credit, idx, e, prev_a, new_a)

        if touched_treasury:
            treasury_journal_count += 1
            if treasury < treasury_min:
                treasury_min = treasury
            if treasury < 0:
                _record_trigger("treasury", ledger.TREASURY_ACCOUNT, idx, e, prev_treasury, treasury)

        if touched_treasury or touched_gov_deposit:
            gov_money = treasury + gov_deposited
            if gov_money < 0:
                _record_trigger(
                    "gov_money", "gov/treasury+deposit", idx, e, prev_gov_money, gov_money
                )

        if touched_cb_money:
            cb_money = reserves_total + treasury
            if cb_money < 0:
                _record_trigger(
                    "cb_money", "cb_money(reserves+treasury)", idx, e, prev_cb_money, cb_money
                )

    return {
        "treasury_min": treasury_min,
        "treasury_journal_count": treasury_journal_count,
        "triggers": triggers,
    }


def _build_turn_detail(
    turn: int,
    island,
    opening: dict[str, int],
    stage_spans: list[dict[str, Any]],
    defs,
) -> dict[str, Any]:
    """負に転じたターン・例外発生ターンについて、調査に必要な詳細記録を組み立てる。"""
    treasury = opening.get(ledger.TREASURY_ACCOUNT, 0)
    gov_deposited = sum(
        val for name, val in opening.items() if name.startswith(GOV_DEPOSIT_PREFIX) and val
    )
    treasury_journal: list[dict[str, Any]] = []
    by_kind: dict[str, dict[str, int]] = {}

    for idx, e in enumerate(island.journal):
        debit = e.debit
        credit = e.credit
        amount = e.amount
        t_delta = 0
        d_delta = 0
        if debit == ledger.TREASURY_ACCOUNT:
            t_delta += amount
        elif credit == ledger.TREASURY_ACCOUNT:
            t_delta -= amount
        if debit.startswith(GOV_DEPOSIT_PREFIX):
            d_delta += amount
        if credit.startswith(GOV_DEPOSIT_PREFIX):
            d_delta -= amount

        if t_delta == 0 and d_delta == 0:
            continue

        treasury += t_delta
        gov_deposited += d_delta
        stage = _stage_for_index(stage_spans, idx)
        treasury_journal.append(
            {
                "index": idx,
                "stage": stage,
                "kind": e.kind,
                "debit": debit,
                "credit": credit,
                "amount": amount,
                "treasury_delta": t_delta,
                "treasury_after": treasury,
                "gov_deposited_after": gov_deposited,
                "gov_money_after": treasury + gov_deposited,
            }
        )
        if t_delta != 0:
            slot = by_kind.setdefault(e.kind, {"in": 0, "out": 0, "net": 0, "count": 0})
            if t_delta > 0:
                slot["in"] += t_delta
            else:
                slot["out"] += -t_delta
            slot["net"] += t_delta
            slot["count"] += 1

    opening_treasury = opening.get(ledger.TREASURY_ACCOUNT, 0)
    opening_gov_dep = sum(
        val for name, val in opening.items() if name.startswith(GOV_DEPOSIT_PREFIX) and val
    )
    opening_cb_res = opening.get(CB_ISSUED_RESERVE, 0)
    opening_summary = {
        "treasury": opening_treasury,
        "gov_deposited": opening_gov_dep,
        "gov_money": opening_treasury + opening_gov_dep,
        "cb_issued_treasury": opening.get(CB_ISSUED_TREASURY, 0),
        "cb_issued_reserve": opening_cb_res,
        "cb_issued_nr_deposit": opening.get(CB_ISSUED_NR_DEPOSIT, 0),
        "cb_money": -opening_cb_res + opening_treasury,
    }

    problems = ledger.check(island, opening, defs.journal_kinds())

    return {
        "turn": turn,
        "journal_count": len(island.journal),
        "opening_summary": opening_summary,
        "closing_summary": _snapshot_full(island),
        "stage_balances": stage_spans,
        "treasury_summary_by_kind": by_kind,
        "treasury_journal": treasury_journal,
        "opening_gov_cb_accounts": _gov_cb_accounts(opening),
        "closing_gov_cb_accounts": _gov_cb_accounts(island.accounts),
        "fiscal_scratch": _json_safe(island.scratch.get("fiscal")),
        "committed_scratch": _json_safe(island.scratch.get("committed")),
        "turn_logs": [{"kind": le.kind, "args": _json_safe(le.args)} for le in island.log],
        "ledger_check_problems": problems,
    }


def _entries_before(
    journal: list[Any],
    stage_spans: list[dict[str, Any]],
    trigger_index: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """負に転じた仕訳の直前 limit 件（その仕訳自身も含む）を返す。"""
    if trigger_index is None or limit <= 0:
        return []
    start = max(0, trigger_index - limit)
    out = []
    for idx in range(start, trigger_index + 1):
        e = journal[idx]
        out.append(
            {
                "index": idx,
                "stage": _stage_for_index(stage_spans, idx),
                "kind": e.kind,
                "debit": e.debit,
                "credit": e.credit,
                "amount": e.amount,
            }
        )
    return out


def _trace_row(turn: int, snap: dict[str, Any], treasury_min: int, jcount: int, tjcount: int, ok: int) -> list[int]:
    return [
        turn,
        snap["treasury"],
        snap["gov_deposited"],
        snap["gov_money"],
        snap["gov_spendable"],
        snap["gov_bonds"],
        snap["cb_issued_treasury"],
        snap["cb_issued_reserve"],
        snap["cb_issued_nr_deposit"],
        snap["cb_money"],
        snap["cb_bonds"],
        snap["cb_loans"],
        treasury_min,
        jcount,
        tjcount,
        ok,
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=18)
    ap.add_argument("--turns", type=int, default=1200)
    ap.add_argument("--island", type=int, default=23)
    ap.add_argument("--label", default="K2off")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pre-entries", type=int, default=50)
    a = ap.parse_args(argv)

    game = load_game(None)
    config = game.config
    scenario = measure.SCENARIOS[a.scenario]
    world = generate_world(a.seed, scenario.islands, config, game.defs)

    target_id = a.island
    if target_id not in world.islands:
        raise ValueError(f"島 {target_id} が世界に見つかりません（範囲: {sorted(world.islands)[:5]}..）")

    target = world.islands[target_id]
    island_info = {
        "id": target.id,
        "name": target.name,
        "has_economy": target.has_economy,
        "regime": _json_safe(getattr(target, "regime", None)),
        "wx": target.wx,
        "wy": target.wy,
        "climate": _json_safe(getattr(target, "climate", None)),
    }

    # Engine の段呼び出しを読み取り専用で観察し、各仕訳がどの段で出たかを記録する。
    # world の状態・乱数・実行順序は一切変えない。
    stage_spans: list[dict[str, Any]] = []
    orig_run_island = game.engine._run_island_stage
    orig_run_world = game.engine._run_world_stage

    def _observe_stage(orig_fn, stage, w, turn):
        isl = w.islands.get(target_id)
        start_idx = len(isl.journal) if isl is not None else 0
        try:
            return orig_fn(stage, w, turn)
        finally:
            if isl is not None:
                end_idx = len(isl.journal)
                stage_spans.append(
                    {
                        "stage": stage.name,
                        "journal_start": start_idx,
                        "journal_end": end_idx,
                        **_snapshot_stage(isl),
                    }
                )

    game.engine._run_island_stage = lambda stage, w, turn: _observe_stage(orig_run_island, stage, w, turn)
    game.engine._run_world_stage = lambda stage, w, turn: _observe_stage(orig_run_world, stage, w, turn)

    trace_rows: list[list[int]] = []
    init_snap = _snapshot_full(target)
    trace_rows.append(_trace_row(0, init_snap, init_snap["treasury"], 0, 0, 1))

    first_negative_by_key: dict[str, dict[str, Any]] = {}
    turn_details: dict[str, dict[str, Any]] = {}
    all_negative_treasury_turns: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    completed_turns = 0

    for _ in range(a.turns):
        next_turn = world.turn + 1
        fallback_opening = dict(target.accounts)
        stage_spans = []
        measure.queue_scenario_orders(world, scenario, game.defs, config)
        exc_caught: Exception | None = None
        try:
            game.engine.run_turn(world)
            completed_turns = world.turn
        except Exception as exc:
            exc_caught = exc

        t = next_turn if exc_caught is not None else world.turn
        opening = target.scratch.get("opening_accounts", fallback_opening)
        replay = _replay_turn(opening, target.journal, stage_spans)
        snap = _snapshot_full(target)
        trace_rows.append(
            _trace_row(
                t,
                snap,
                replay["treasury_min"],
                len(target.journal),
                replay["treasury_journal_count"],
                0 if exc_caught is not None else 1,
            )
        )

        need_detail = exc_caught is not None
        if replay["treasury_min"] < 0 or snap["treasury"] < 0:
            t_trig = replay["triggers"].get("treasury")
            all_negative_treasury_turns.append(
                {
                    "turn": t,
                    "opening_treasury": opening.get(ledger.TREASURY_ACCOUNT, 0),
                    "min_treasury": replay["treasury_min"],
                    "closing_treasury": snap["treasury"],
                    "trigger_stage": t_trig["trigger_stage"] if t_trig else None,
                    "trigger_kind": (
                        t_trig["trigger_entry"]["kind"]
                        if t_trig and t_trig.get("trigger_entry")
                        else None
                    ),
                    "trigger_amount": (
                        t_trig["trigger_entry"]["amount"]
                        if t_trig and t_trig.get("trigger_entry")
                        else None
                    ),
                }
            )

        for key, trig in replay["triggers"].items():
            if key not in first_negative_by_key:
                need_detail = True
                first_negative_by_key[key] = {
                    "turn": t,
                    **trig,
                    "entries_before_trigger": _entries_before(
                        target.journal, stage_spans, trig["trigger_index"], a.pre_entries
                    ),
                }

        if need_detail and str(t) not in turn_details:
            turn_details[str(t)] = _build_turn_detail(t, target, opening, stage_spans, game.defs)

        if exc_caught is not None:
            problems = ledger.check(target, opening, game.defs.journal_kinds())
            failure = {
                "turn": t,
                "exception_type": type(exc_caught).__name__,
                "message": str(exc_caught),
                "target_island_problems": problems,
            }
            break

    # 最初に負に転じたイベント（全監視対象のうち最も早いもの。同時なら treasury を優先）
    first_negative = None
    if first_negative_by_key:
        def _sort_key(item):
            key, ev = item
            idx = ev["trigger_index"] if ev["trigger_index"] is not None else -1
            prio = 0 if key == "treasury" else 1
            return (ev["turn"], idx, prio, key)

        _, first_negative = min(first_negative_by_key.items(), key=_sort_key)

    out = {
        "package": "island23-treasury-trace-1128",
        "scenario": a.scenario,
        "seed": a.seed,
        "requested_turns": a.turns,
        "completed_turns": completed_turns,
        "label": a.label,
        "reentry_on": bool(config.get("trade.shipping_reentry")),
        "expand_on": bool(config.get("trade.trader_expand")),
        "island": island_info,
        "trace_cols": TRACE_COLS,
        "trace": trace_rows,
        "first_negative": first_negative,
        "first_negative_by_key": first_negative_by_key,
        "all_negative_treasury_turns": all_negative_treasury_turns,
        "turn_details": turn_details,
        "failure": failure,
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
