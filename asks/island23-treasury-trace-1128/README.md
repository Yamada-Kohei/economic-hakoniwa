# 島23 国庫トレースの包み（island23-treasury-trace-1128）

測りの仕事 738（包み `w308-reentry-k2-off-1126`、場面 `plan`、種 18、1200 ターン。回し直し 773 も同様）で、島 23 の国庫勘定 `gov/treasury/cb` が `-329,640,000` まで負になり、帳簿の検査（`LedgerCheckFailed`）で落ちた件の専用トレース包み（掲示板 #5940）。

## 条件（元の包みと完全一致）

- 対象コード: commit `266cf73d192a33b01b9e91f7940f7b053d6d0cfb`
- パッチ: `kprime.diff`（元の包みのものをそのまま使用）
- 場面 `plan`、種 `18`、1200 ターン、1 本のみ（`plan-s18-t1200-trace`）
- ゲームの計算・乱数・状態は一切変えず、記録の取り方だけを追加

## 何を記録するか

`hakoniwa_core/ledger.py` の帳簿では、勘定名が `持ち手/持ち物/出し手` の 3 要素で構成されており、国庫そのものは `gov/treasury/cb` という 1 勘定で表されます。関連する政府預金・中央銀行側の勘定も含めて抜け漏れなく追えるよう、島 23 についてのみ次の内容を summary JSON（`island23-trace-K2off-plan-s18-t1200.json`）に記録します（常設ログは使いません）。

1. **毎ターンの残高推移（`trace_cols` / `trace`）**
   ターン 0（初期状態）から終了ターン（または例外発生ターン）まで、毎ターン次を記録します:
   - `treasury`: `gov/treasury/cb`（国庫残高）
   - `gov_deposited`: `gov/deposit/bank:*` の合計（市中銀行への政府預金）
   - `gov_money`: `treasury + gov_deposited`（政府のお金合計）
   - `gov_spendable`: `funds.spendable(island)`（今使える政府のお金）
   - `gov_bonds`: 国債発行残高
   - `cb_issued_treasury`: `cb/issued/treasury`（中銀側の国庫発行勘定。通常 `<= 0`）
   - `cb_issued_reserve`: `cb/issued/reserve`（中銀側の準備預金発行勘定。通常 `<= 0`）
   - `cb_issued_nr_deposit`: `cb/issued/nr_deposit`（中銀側の非居住者預金発行勘定。通常 `<= 0`）
   - `cb_money`: 中央銀行マネー合計（準備預金＋国庫）
   - `cb_bonds`: 中央銀行の保有国債合計
   - `cb_loans`: 中央銀行の銀行への貸出残高
   - `treasury_min_in_turn`: そのターンの仕訳再生中に記録した `gov/treasury/cb` の最小値（ターン途中で一時的に負になって回復した場合も拾う）
   - `journal_count` / `treasury_journal_count` / `check_ok`
2. **最初に負に転じたターンの詳細と直前の仕訳（`first_negative` / `first_negative_by_key` / `turn_details`）**
   通常は 0 以上であるべき 5 項目（`treasury`, `gov_deposit`, `gov_money`, `cb_money`, `cb_asset`）のどれかが最初に `< 0` になったターン（ターン途中の転落を含む）と、`LedgerCheckFailed` 等の例外が起きたターンについて、次を残します:
   - `trigger_entry`: 負に転じた瞬間の仕訳（通し番号・段 `stage`・種類 `kind`・借方・貸方・金額・前後残高）
   - `entries_before_trigger`: その直前の仕訳（既定 50 件、段・種・借方・貸方・額）
   - `turn_details[str(turn)]`:
     - `treasury_journal`: そのターンで `gov/treasury/cb` または `gov/deposit/*` に触れた全仕訳と、1 件ごとの残高推移
     - `treasury_summary_by_kind`: 仕訳の種類ごとの国庫の入出金合計・純増減・件数
     - `stage_balances`: ターンの各段（`budget`, `market`, `trade`, `banking`, `bonds`, `accounts`, `failures` 等）終了時点の残高推移
     - `opening_gov_cb_accounts` / `closing_gov_cb_accounts`: 期首・期末の `gov`・`cb` 関連勘定一覧
     - `fiscal_scratch` / `committed_scratch`: 国庫の取り置き・使用額・予算上限の内部状態
     - `turn_logs`: そのターンの島 23 のターンログ
     - `ledger_check_problems`: `ledger.check` が報告した不整合一覧
3. **例外発生時の記録（`failure`）**
   `LedgerCheckFailed` 等でターンが止まった場合も例外を捕捉し、`failure` に発生ターンとメッセージを残した上で JSON を書き出して終了コード 0 で終えます。

## 結果の読み方

1. まず `failure` を見て、どのターンでどのエラー（例: `勘定 gov/treasury/cb がマイナスです（-329640000）`）が出たか確認する。
2. `first_negative`（および `first_negative_by_key["treasury"]`）を見る:
   - `turn` と `trigger_stage` で「いつ・どの段で」負になったかが分かる。
   - `trigger_entry` で負に突き落とした仕訳の `kind`・`debit`・`credit`・`amount`・前後残高が分かる。
   - `entries_before_trigger` で、その直前の仕訳列（最大 50 件）を追える。
3. `turn_details[str(turn)]` を見る:
   - `stage_balances` でどの段の間に大きく減ったか俯瞰し、`treasury_summary_by_kind` で主な支出種別を確認し、`treasury_journal` で国庫の出し入れを 1 件ずつ追える。
   - `fiscal_scratch` と `turn_logs` で、取り置き・予算・倒産・銀行破綻など同ターンの出来事と突き合わせられる。
4. `all_negative_treasury_turns` を見ると、最終失敗ターンより前のターンに「途中で一時的に負になって期末に戻った」予兆があったかどうかも確認できる。
5. `trace`（列名は `trace_cols`）から、ターン 0 からの国庫・政府預金・国債・中銀勘定の長期推移を読める。

## 提出先

受付への投入・`origin/asks` への push は管理者（むぎ）側で行います。
