# 7 お金と銀行

お金の帳簿（仕訳と確かめ）、市中銀行（預金・融資・体力・参入）、中央銀行（政策金利・銀行間の貸し借り・貸し出し）、国債（発行と入札・利回り・売買の市場と時価・債務不履行）、銀行の破綻と破綻処理を扱います。国庫の支払いの順番と国債の自動の発行の額はタブ 6、施設の倒産と差し押さえはタブ 5、家計が預金から使う割合はタブ 4、外貨・外国の国債・為替介入・資本取引はタブ 9、中央銀行の独立と体制の関わりはタブ 10 を見ます。

## コードの場所と試験

- 帳簿: `hakoniwa_core/ledger.py`（`post`・`pay` と毎ターンの確かめ）、流れの記録は `hakoniwa_core/economy/books.py`
- 市中銀行: `hakoniwa_core/modules/banking/`（段 `banking`、命令 `set_bank_policy`・`grant_bank_license`）、帳簿の見方と融資の部品は `hakoniwa_core/economy/banks.py`、参入は `hakoniwa_core/modules/firms/`
- 国債: `hakoniwa_core/modules/bonds/`（世界の段 `bonds`、命令 `set_bond_policy`・`set_bond_payment`）、残高と時価は `hakoniwa_core/economy/debt.py`、期間は `packs/base/data/finance.yaml` の `bond_term`
- 中央銀行: `hakoniwa_core/modules/central_bank/`（段 `central_bank`、命令 `set_monetary_policy`・`set_independence`・`emergency_loan`）
- 決算（銀行と中央銀行の損益・納付）: `hakoniwa_core/modules/accounts/`、破綻と破綻処理: `hakoniwa_core/modules/failures/`（命令 `resolve_bank`）
- 試験: tests/core/test_economy.py、tests/core/test_finance.py、tests/core/test_bond_gains.py、tests/core/test_shocks.py、tests/core/test_playtest3_fixes.py、tests/server/test_finance.py

## つながり

- 前提にしていること: 施設の利益と在庫（タブ 3・5）、家計の層と預金（タブ 4）、国庫の支払いの見込み（タブ 6）、物価上昇率と失業率（タブ 11 の統計）、経済体制（タブ 10）。
- 前提にされていること: 国庫と自動の発行（タブ 6）、民営工場の建設資金と参入（タブ 5）、運転資金で動く施設（タブ 5）、消費の式の預金（タブ 4）、外国の国債と為替（タブ 9）。
- 詳しくは下の「前提と依存」。

## 決まりの一覧

| 番号 | 決まり（一行） | 状態 | 設定の名前 | 試験 |
| --- | --- | --- | --- | --- |
| BK-01 | お金を持つ主体は、家計の3つの層・独立採算の施設・市中銀行・政府・中央銀行の5種類。国庫直轄の施設は政府の帳簿の中の部門 | 決定 |  | tests/core/test_economy.py::test_ledger_passes_for_proper_payments |
| BK-02 | お金は `ledger.post` と `ledger.pay` だけで動かす。金融の持ち物は「持ち手の資産＝出し手の負債」で、持ち物ごとに勘定の合計がいつも 0 | 決定 |  | tests/core/test_economy.py::test_ledger_catches_entry_between_different_items, ::test_ledger_rejects_malformed_posts |
| BK-03 | お金が生まれ・消えるのは、銀行の貸し出しと返済、中央銀行の国債の売買・引き受け・貸し出し・利子・納付、破綻処理の預金の切り捨てだけ | 決定 |  | （未確認） |
| BK-04 | 記録の段の最後に、貸し借りの一致・残高と仕訳の一致・お金の増減・預金と国庫がマイナスでないことを確かめ、崩れたらそのターンを失敗にする | 決定 |  | tests/core/test_economy.py::test_ledger_catches_balance_changed_without_entry, ::test_ledger_catches_negative_deposit, ::test_ledger_catches_unknown_kind_and_bad_amount |
| BK-05 | 金額は通貨の最小単位の整数、金利は年率の1万分の1で持つ。1ターンの利子は「残高×金利÷12」の切り捨て | 決定 |  | （未確認） |
| BK-06 | 市中銀行はオフィス街のマスに入る施設で職員を雇う。預金と融資が少ない銀行は少なく雇う。職員が足りない割合だけ新しい融資を絞る | 決定 | `banking.facility`, `banking.staff_volume`, `banking.staff_floor` | tests/core/test_playtest5_fixes.py::test_small_bank_hires_for_its_business |
| BK-07 | 国営銀行はプレイヤーが建設の命令で建てる。民間銀行は参入の政策（自由・許可制・禁止）に従い、預金と利益の見込みが立てば資本家階級の出資で開業する。計画経済は国営銀行だけ | 決定 | `banking.max_banks`, `banking.entry_capital`, `banking.entry_deposits`, `banking.entry_roe_margin` | tests/core/test_finance.py::test_planned_economy_cannot_privatize_or_license_banks, tests/core/test_playtest5_fixes.py::test_bank_does_not_open_when_it_cannot_raise_its_deposit_rate, tests/core/test_playtest_fixes.py::test_bank_entry_block_names_missing_office |
| BK-08 | 家計の層は毎ターン預金の一部を「預金金利−危なさ」の高い銀行へ移す。規制を下回った銀行と中央銀行に頼る銀行からは取り付けで流れ出す | 決定 | `banking.switch_share`, `banking.run_share`, `banking.risk_buffer`, `banking.risk_weight` | （未確認） |
| BK-09 | 預金金利は「政策金利−差」へ少しずつ近づけ、手元の流動性（準備預金の余りと国債の額面）が足りない銀行と開業して預金を集めている銀行は政策金利へ近づける | 決定 | `banking.deposit_rate`, `banking.deposit_spread`, `banking.liquidity_target`, `banking.rate_step` | tests/core/test_shocks.py::test_rate_hike_raises_deposit_and_lending_rates |
| BK-10 | 融資は建設資金（据え置きの後に均等に返す）と運転資金（短く返し、貸し手と借り手ごとに1本で回す）、家計の住宅ローン（BK-47）。金利は毎ターン式 `loan_rate` で計算し直す変動金利 | 決定 | `loans.construction_turns`, `loans.grace_turns`, `loans.working_turns`, `banking.margin`, `banking.margin_range`, `banking.rho` | tests/core/test_finance.py::test_working_loans_merge_per_lender_and_borrower, ::test_construction_loans_wait_for_grace |
| BK-11 | 審査は、現金で見た営業の利益が返済の決めた倍以上で、借入が資産の決めた割合以下。利益で返せなくても在庫の担保の分は運転資金を貸す | 決定 | `loans.coverage`, `loans.max_leverage`, `loans.inventory_ratio` | （未確認） |
| BK-12 | 銀行が貸せるのは、貸した後も自己資本比率が規制以上で、1つの民営施設への残高が大口融資の規制以内の分。借り手は金利の低い銀行から順に借りる | 決定 | `banking.regulatory_ratio`, `banking.large_exposure` | tests/core/test_finance.py::test_construction_loan_is_split_across_banks_within_the_limit, ::test_large_exposure_applies_to_private_facilities_only, ::test_large_exposure_keeps_but_does_not_grow_a_loan_over_the_limit |
| BK-13 | 運転資金を借りきれない分は、国営施設なら政府が出資し、民営施設なら変動費の利益が出ている間だけ資本家階級が出資する | 決定 |  | （未確認） |
| BK-14 | 返済日に預金が足りなければ払える分だけ払って遅れを数え、続けて決めた回数で倒産。計画経済の島では国営銀行が借り換えさせ、倒産しない | 決定 | `loans.arrears_limit` | tests/core/test_finance.py::test_arrears_bankrupt_private_facility, tests/core/test_shocks.py::test_planned_economy_never_bankrupts_or_stops |
| BK-15 | 混合経済では国営銀行が基幹産業に利ざやなしで貸す（政策融資）。計画経済では国営銀行が国営施設に審査なしで貸す | 決定 |  | （未確認） |
| BK-16 | 自己資本比率は「自己資本÷融資の残高」。規制を下回った銀行は新しく貸さない | 決定 | `banking.regulatory_ratio` | （未確認） |
| BK-17 | 銀行の損益は利子の差−職員の賃金−貸し倒れ＋国債の実現損益。法人税の後の配当と納付は、自己資本が目安以上に残る分まで | 決定 | `firms.payout`, `banking.capital_floor`, `banking.risk_buffer` | tests/server/test_finance.py::test_bank_books_separate_realized_and_unrealized_bond_gains |
| BK-18 | 準備預金は預金の決めた割合を最低限として中央銀行に置き、余りには「政策金利−差」の利子が付く | 決定 | `banking.reserve_ratio`, `central_bank.reserve_spread` | （未確認） |
| BK-19 | 中央銀行は島に1つの帳簿だけの主体。独立していなければ政策金利と国債の売買を命令で決め、独立していれば式 `policy_rate` で決める | 決定 | `central_bank.policy_rate`, `central_bank.natural_rate`, `central_bank.inflation_target`, `central_bank.inflation_weight`, `central_bank.unemployment_weight`, `central_bank.max_step`, `central_bank.rate_range` | tests/core/test_finance.py::test_monetary_policy_and_independence, tests/core/test_phase3_3.py::test_independent_central_bank_stops_steering_the_rate |
| BK-20 | 独立させる・戻すのは命令で、切り替えた後の決めたターン数は戻せない。独立をやめたら売れ残りの扱いを独立の前に戻す | 決定 | `central_bank.switch_lock_turns` | tests/core/test_playtest_fixes.py::test_ending_independence_restores_underwriting, ::test_unsold_choice_made_while_independent_is_kept |
| BK-21 | 銀行間の貸し借りは中央銀行の段で、金利を値段にした板寄せ（政策金利の上下の幅）。破綻した銀行には誰も貸さない | 決定 | `central_bank.corridor`, `central_bank.reserve_spread` | （未確認） |
| BK-22 | 借りきれない銀行には中央銀行が担保を取って上端の金利で貸す。担保も足りなければ資金繰りが破綻する。担保のない緊急の貸し出しは命令（独立なら自己資本がプラスの銀行に自動） | 決定 | `central_bank.collateral_ratio` | （未確認） |
| BK-23 | 中央銀行の利益は決算で全額を国庫に納める。損失は繰り越して後の利益で埋め、自己資本がマイナスでも破綻しない | 決定 |  | （未確認） |
| BK-24 | 国債の期間は3つ（1・5・10年）。自動の発行の期間の割合・売れ残りの扱い・追加の発行・直接の引き受けは命令 `set_bond_policy` で決める | 決定 | `bonds.auto_terms` | tests/core/test_codex_turn300_fixes.py::test_bond_terms_given_with_fractions_become_whole_shares, ::test_bond_terms_refuse_values_that_cannot_be_shares, tests/core/test_playtest4_fixes.py::test_bond_policy_log_names_only_what_changed |
| BK-25 | 発行はすべて市中銀行の入札に出す。各銀行は準備預金のうち、最低限と手元に残す分と借りている額を超えた分を何段かに分け、段ごとにその段まで買ったときの偏りで求める利回りで注文し、発行額が売れるいちばん低い利回りで全員が買う | 決定 | `banking.liquidity_share`, `banking.auction_steps` | tests/core/test_balance_round5.py::test_bank_bids_all_spare_reserves_at_rising_yields |
| BK-26 | 市場の利回りは式 `bond_yield`（政策金利＋期間・返す力・物価・不履行の上乗せ−独立の割引）。返す力は「中央銀行以外に払う利子÷国債と中央銀行からのお金を除く国の収入」（年）で、中央銀行の持ち分は市場の外 | 決定 | `bonds.burden_threshold`, `bonds.burden_sigma`, `bonds.kappa`, `bonds.independence_discount` | tests/core/test_finance.py::test_bond_default_raises_yields_and_hits_bank_capital, tests/core/test_balance_round5.py::test_yield_counts_only_interest_paid_outside_the_central_bank |
| BK-27 | 利回りには上限があり、張り付いても何も起きない | 仮 | `bonds.max_yield` | （未確認） |
| BK-28 | 新しい発行の売れ残りは、「引き受ける」の政策で中央銀行が独立していなければ中央銀行が同じ利回りで引き受け、そうでなければ発行せず支出が削られる | 決定 |  | tests/core/test_finance.py::test_unsold_bonds_cut_spending_instead_of_underwriting, tests/core/test_playtest3_fixes.py::test_closed_auction_is_underwritten_or_cut |
| BK-29 | 満期の国債の中央銀行の持ち分は同じ期間の国債に乗り換え、借り換えの売れ残りは独立していても中央銀行が買い支える | 決定 |  | tests/core/test_rules.py::test_central_bank_rolls_over_its_maturing_bonds, ::test_maturing_bonds_are_refinanced_in_their_turn |
| BK-30 | 国債の売買の市場は残りの期間の4区分ごとの、利回りを値段にした板寄せ。銀行は持ち高を、偏りで求める利回りが今の利回りと等しくなる額に近づけ、準備預金の足りない銀行は指値なしで売る。独立していない中央銀行は命令の額を指値なしで出す | 決定 | `bonds.buckets`, `bonds.bucket_terms`, `bonds.trade_band`, `banking.bond_concentration`, `banking.sell_premium` | tests/core/test_playtest_fixes.py::test_central_bank_bond_trade_result_is_logged |
| BK-31 | 国債は時価で持つ。損益に入れるのは売った・償還された・切り下げられた分の実現損益だけで、評価損益は自己資本にだけ数える。評価損で自己資本がマイナスになった銀行は破綻する | 決定 |  | tests/core/test_bond_gains.py::test_selling_a_bond_realizes_the_gain, ::test_unrealized_losses_still_fail_a_bank, tests/core/test_playtest3_fixes.py::test_bonds_issued_after_valuation_are_not_counted_as_gains |
| BK-32 | 資本取引を認めた国の銀行は、外国の利回りが値下がりの見込みを超えて高い通貨の外貨と国債を、預金の決めた割合まで持つ（タブ 9） | 決定 | `banking.foreign_share`, `banking.foreign_step`, `banking.foreign_margin` | tests/core/test_phase3_3.py::test_banks_plan_foreign_currency_only_when_it_pays_and_capital_is_free |
| BK-33 | 債務不履行は命令 `set_bond_payment` で支払いを止めたときだけ、すべての銘柄を一度に。額面を決めた割合だけ切り下げ、残りを同じ期間で出し直す | 決定 | `bonds.haircut` | tests/core/test_shocks.py::test_bond_default_raises_yields_and_hits_holders, tests/core/test_phase3_3.py::test_default_cuts_foreign_holders_too, tests/server/test_finance.py::test_bond_payment_needs_bonds |
| BK-34 | 不履行の罰は3つ: 利回りの上乗せ（だんだん減る）、決めたターン数は銀行が国債を買わない、損をした銀行は新しい融資を止めて借り換えだけ（計画経済の島と、不履行の後に建った銀行を除く） | 決定 | `bonds.default_premium`, `bonds.default_decay_turns`, `bonds.default_exclusion_turns`, `banking.default_crunch_turns` | tests/core/test_playtest3_fixes.py::test_default_closes_the_bond_market_and_stops_lending, ::test_bond_market_reopens_and_lending_resumes, ::test_stopped_bank_only_rolls_over_principal, ::test_planned_island_banks_keep_lending_after_default, ::test_private_entry_waits_for_lending, tests/server/test_finance.py::test_help_describes_default_penalties |
| BK-35 | 自己資本がマイナスか資金繰りが破綻した銀行は破綻し、新しい融資と預金の受け入れを止める。処理は命令 `resolve_bank` で救済・引き継ぎ・清算から選ぶ（行政力を使わない） | 決定 |  | tests/core/test_finance.py::test_resolve_bank_order_needs_failed_bank, tests/core/test_playtest5_fixes.py::test_failed_bank_keeps_the_reason_it_was_logged_with |
| BK-36 | 次のターンまでに選ばなければ引き継ぎにし、引き継いでも規制を下回らない銀行がなければ救済にする | 決定 |  | tests/core/test_shocks.py::test_bank_failure_is_resolved_without_cascade |
| BK-37 | 引き継ぎと清算の損失は、保護されない預金と銀行間の貸し手が同じ割合で負い、残りを預金保険（政府）が払う。政府も足りなければ国債を出し、それでも足りなければ保護される預金も削る | 決定 | `banking.liquidation_price` | tests/core/test_finance.py::test_bank_resolution_keeps_insured_deposits_and_books, tests/core/test_codex2_fixes.py::test_transfer_logs_deposits_cut_when_the_government_cannot_pay, tests/core/test_playtest4_fixes.py::test_deposit_insurance_is_at_most_the_deposits_left, ::test_liquidating_a_bank_without_deposits_pays_no_deposit_insurance |
| BK-38 | 預金は1人あたり（施設と銀行は1つあたり）上限額まで全額を守る。上限額は命令 `set_bank_policy` で決め、無制限にもできる | 決定 | `banking.deposit_insurance` | （未確認） |
| BK-39 | 引き継ぐ健全な銀行のない島で引き継ぎか清算を選ぶと、政府が銀行を引き取り、出資を消して損失を同じ順番で負わせて立て直す | 決定 |  | tests/core/test_playtest_fixes.py::test_single_bank_transfer_resolves_in_place |
| BK-40 | 救済と預金保護に国庫から出すのは、次のターンの優先の支払いを残した額まで。足りない分は預金の切り捨てで埋め、出したターンは国債で国庫を戻す（タブ 6 FS-13） | 決定 | `fiscal.rescue_after`, `banking.capital_floor` | tests/core/test_playtest_fixes.py::test_bailout_without_money_bails_in_and_leaves_bank_solvent, tests/core/test_codex2_fixes.py::test_bond_stats_include_bonds_issued_to_rescue_a_bank |
| BK-41 | 銀行ごとに求める利回りは、市場の利回りに国債への偏り（国債の額面÷資産が決めた割合を超えた分）の上乗せを足す。計画経済の国営銀行は上乗せしない | 決定 | `banking.bond_concentration` | tests/core/test_balance_round5.py::test_bank_bids_all_spare_reserves_at_rising_yields |
| BK-42 | 回5 で、刷ったお金が物価に効く経路を入れる（預金から使う割合を物価と実質金利で動かす・指値に物価の見通しを足す・物価の分は賃金に追わせる。タブ 3・4） | 決定（回5 で作る） |  | tests/core/test_shocks.py::test_printing_money_raises_prices |
| BK-43 | 計画経済の国は中央銀行を独立させられない（命令は通らない）。独立したまま計画経済に移った国（その命令のとき）と、前の版の世界で独立している計画経済の国（更新の段）は、政府の一部に戻る（プレイヤーの切り替えではないので、切り替えの後の戻せないターン数は見ず、数え始めもしない） | 決定（回5） |  | tests/core/test_balance_round5.py::test_planned_economy_cannot_make_its_central_bank_independent |
| BK-44 | 独立した中央銀行は国債の売買の市場で売り買いする。準備預金の足りない銀行が指値なしで売りに出した国債は、同じ額だけ指値なしで買う（買いオペ）。足りない銀行がなければ、買いオペで買って残っている額まで（独立する前に引き受けた国債は売らない）を、今の利回りか市場が求める利回りの高いほうまでで売りに出し、持ち高の少ない銀行が余りのお金で買う分だけ売れる（売りオペ。買い手がいなければ持ち続け、満期は中央銀行の持ち分と同じく乗り換える）。中央銀行の買いの指値は今の利回りで、買うだけで利回りを動かさない。準備の足りない銀行の売りは、外国の持ち手の売りより先に中央銀行が買う。国が出す国債を入札で買うことはしない | 決定（回5） |  | tests/core/test_balance_round5.py::test_independent_central_bank_buys_from_short_banks_and_sells_back |
| BK-47 | 家計の住宅ローン（W-186 の直し 回3）: 町が育つ資材の代金の `housing.ltv` を一般の層が借り（頭金は預金から）、毎ターンの返済が層の所得の `housing.dti` 以内の分まで。銀行と層ごとに1本にまとめ、期間は残高の重みで混ぜ直し、毎ターン「残り÷残りの期間」を返す。使わなかった分は約定の後すぐ返す。層を移る人は預金と同じ割合でローンも持って移り、返済が `loans.arrears_limit` 回続けて遅れた層のローンは残りを銀行の損として消す（家と人はそのまま） | 決定（回3） | `housing.ltv`, `housing.dti`, `housing.mortgage_turns`, `loans.arrears_limit` | tests/core/test_recession_round3.py::test_mortgages_merge_per_bank_and_layer_and_blend_the_term, ::test_people_who_lose_their_jobs_take_their_share_of_the_mortgage, ::test_a_layer_that_cannot_pay_its_mortgage_is_written_off_after_the_limit, ::test_households_borrow_for_new_towns_and_the_books_balance |
| BK-48 | 中央銀行の買いが値幅の中で買いきれなければ、値幅を越えて利回りを下げて買う（W-193。指値オペに近い）。銀行は利回りが準備預金の利回りに近づくほど持ちたい額を減らし、その差を売りに出す。準備預金の利回りより下には下げない | 決定（回3） | `banking.cb_sell_steps`, `central_bank.reserve_spread` | tests/core/test_recession_round3.py::test_banks_hold_fewer_bonds_as_the_yield_nears_the_reserve_rate, ::test_central_bank_buys_beyond_the_band_from_banks_willing_to_sell |
| BK-45 | 直近 1 年の利益がマイナスの銀行は、職員の下限 `banking.staff_floor` を外し、預金と融資の量に見合う人数まで減らす（経費の切り詰め）。利益がプラスのまま `banking.lean_turns` ターン続けば戻す | 決定（回7 後半） | `banking.staff_floor`, `banking.lean_turns` | tests/core/test_balance_round7b.py::test_losing_bank_cuts_staff_below_the_floor_until_it_earns_again, tests/core/test_playtest5_fixes.py::test_small_bank_hires_for_its_business |
| BK-46 | 破綻の前の合併: 直近 1 年の利益がマイナスで、自己資本比率が「規制＋`banking.risk_buffer`」を下回り、危ない融資を安く見た後の自己資本がまだプラスの銀行は、同じ持ち主の区分で直近 1 年の利益がプラスの健全な銀行（引き取った後も「規制＋`banking.risk_buffer`」以上、準備預金が最低限に足りる。預金の多い順）が引き取る。値段（安く見た後の自己資本）を株主に払い、すべてを引き継ぐ。1 ターンに 1 つ、計画経済の島ではしない。合併の後 1 年は新しい銀行が入らない | 決定（回7 後半） | `banking.risk_buffer` | tests/core/test_balance_round7b.py::test_merger_pays_shareholders_and_moves_everything |

### 仮の決まり

| 番号 | 何の代わりか | いつ外すか |
| --- | --- | --- |
| BK-27 | 借金が返せる見込みを失った国の通貨の信用の崩れ（外貨や物への逃避・為替の暴落・買いだめ）。今は利回りが上限に張り付いても、中央銀行が引き受け続けて何も起きない。張り付いたことは画面と知らせで分かるようにする（回5） | フェーズ4かベータ版の後 |

## 仕組み

### お金と帳簿

- お金を持つ主体は、家計の3つの層（一般の労働者・技術者・資本家階級）、独立採算の施設、市中銀行、政府、中央銀行の5種類。国庫直轄の施設は自分の口座を持たず、政府の帳簿の中の部門。
- 現金はなく、お金はすべて口座。金融の持ち物は「持ち手の資産＝出し手の負債」の1つの残高として持つので、持ち物ごとに勘定を足すといつも 0 になる。

| 持ち物 | 持ち手 | 出し手 |
| --- | --- | --- |
| 預金 | 家計の層・施設 | 市中銀行 |
| 準備預金 | 市中銀行 | 中央銀行 |
| 政府預金（国庫） | 政府 | 中央銀行 |
| 国債 | 市中銀行・中央銀行（外国の持ち手はタブ 9） | 政府 |
| 出資 | 資本家階級・政府 | 施設・銀行 |
| 融資 | 市中銀行 | 独立採算の施設 |
| 銀行間の貸し借り | 市中銀行 | 市中銀行 |
| 中央銀行の貸し出し | 中央銀行 | 市中銀行 |
| 非居住者の預金 | 外国の主体 | 中央銀行 |

- お金を動かすのは `ledger.post`（借り方・貸し方・金額・種類）と、それを使う `ledger.pay` だけ。`pay` は両側の預金を付け替え、取引銀行が違えば準備預金も付け替える。政府が払う・受け取るときは国庫と準備預金を付け替える。仕訳の種類は `packs/base/data/finance.yaml` にあり、施設の収支・財政・統計は仕訳の種類ごとの集計から作る。
- お金が生まれ・消えるのは、市中銀行の貸し出し（預金が生まれる）と返済（消える）、中央銀行の国債の買い入れと引き受け（中央銀行のお金が生まれる）と売り（消える）、中央銀行の貸し出しと返済・利子の受け払い・国庫への納付、破綻処理の預金の切り捨てだけ。ほかの取引はお金を主体の間で移すだけ。
- 金額は通貨の最小単位の整数、金利は年率の1万分の1で持ち、1ターンの利子は「残高×金利÷12」の切り捨て。丸めは `ledger` の1か所だけ。
- 物の値打ちはお金の勘定に入れず、決算で数える（在庫はそのターンの値段、設備は建設費の減価償却の残り。タブ 5）。
- 記録の段の最後に、①仕訳の借り方と貸し方の一致、②前の残高＋仕訳＝今の残高、③お金の増減＝生まれた・消えた仕訳の合計、④預金と国庫がマイナスでない、を確かめ、崩れたらそのターンを失敗にして何も保存しない。島をまたぐお金の確かめはタブ 9。
- 保存のモデル（勘定・融資・国債と持ち高・仕訳・施設の帳簿）は `docs/architecture.md` を見る。

### 市中銀行

- 銀行はオフィス街のマスに入る施設（`banking.facility`）で、職員を雇う。預金と融資の合計が平均賃金の `banking.staff_volume` 倍に届かない小さい銀行は、その割合で少なく雇う（下限 `banking.staff_floor`）。職員が足りない割合だけ新しい融資を絞る。銀行の営業は自動で、行政力を使わない。
- 建国した島は銀行2つで始める（フェーズ2-1 から移し替えた混合経済の島は1つ）。
- 国営銀行はプレイヤーが建設の命令でオフィス街に建て、資本は国庫から出す。名前が重なる銀行には番号を付ける。
- 民間銀行の参入は政策（命令 `set_bank_policy` の参入。決めていなければ経済体制の既定）で決まる。「自由」なら、参入した後の銀行1つあたりの島の預金が `banking.entry_deposits` を超え、今ある銀行の自己資本利益率の平均が「政策金利＋`banking.entry_roe_margin`」を超え、政策金利が今の預金金利より高く、銀行の数が `banking.max_banks` 未満で、空いたオフィス街があれば、島の預金の `banking.entry_capital` を資本家階級が出資して開業する。「許可制」なら免許の命令 `grant_bank_license` を出したときだけ、「禁止」なら作られない。計画経済は国営銀行だけ。建たないわけ（オフィス街がない・出資が足りないなど）は島に残し、金融の画面に出す。
- 開業した銀行は、預金が目安に届くまで預金金利を政策金利まで上げて預金を集める。

### 預金と預金金利

- 家計の各層は預金を銀行ごとに分けて持ち、毎ターン預金の `banking.switch_share` までを「預金金利−危なさ」が高い銀行へ移す。危なさは、自己資本比率が「規制＋`banking.risk_buffer`」を下回った分に `banking.risk_weight` を掛けた値。
- 取り付け: 自己資本比率が規制を下回った銀行と、中央銀行から借りている銀行からは、毎ターン預金の `banking.run_share` がほかの銀行へ流れ出す。
- 施設の預金は取引銀行（借りている銀行、なければ島でいちばん大きい銀行）に置く。払うときは取引銀行の預金から先に使う。
- 預金金利は銀行ごとに、毎ターン `banking.rate_step` ずつ「政策金利−`banking.deposit_spread`」へ近づける。準備預金の余りと国債の額面（流通市場ですぐ売れるので流動性に数える。回5 から）が `banking.liquidity_target` を下回る銀行と、開業して預金を集めている銀行は政策金利へ近づける。初めの値は `banking.deposit_rate`。島の預金金利は銀行の平均。
- 預金を使う割合と消費の式はタブ 4。

### 融資

- 使い道は2つ。建設資金と設備投資は、工事が済んでから `loans.grace_turns` は利子だけ払い、その後 `loans.construction_turns` で元金を均等に返す。運転資金は `loans.working_turns` で返し、貸し手と借り手ごとに1本にまとめ、返す時期に審査に通る分を借り換える（回転する運転資金）。
- 運転資金は、銀行の段で次のターンの支払い（操業目標の分の賃金・仕入れ・電気代・運賃）に預金が足りない独立採算の施設が、足りない分を借りる。
- 金利は変動金利で、毎ターン名前付きの式 `loan_rate` で計算し直す。m は銀行ごとの利ざや（`banking.margin` から始め、`banking.margin_range` の中で、貸したい額に借り手が足りなければ下げ、多ければ上げる）、ρ は `banking.rho`、D は借り手の借入、A は資産。

```latex
r_L = r_P + m + \rho \cdot \frac{D}{A}
```

- 審査: ①直近の「利払い前の利益＋減価償却−在庫の増加」（現金で見た営業の利益。売れずに積み上がった在庫は返済に使えない）の平均が、借りた後の毎ターンの返済の `loans.coverage` 倍以上（運転資金の元金は数えない）、②借入の合計が資産（設備の値打ち＋預金＋在庫）の `loans.max_leverage` 倍以下。記録のない施設は今の値段の変動費の利益で、据え置きの間の民営工場は全力で動いたときの見込みで見る（立ち上がりの赤字で断らない）。①を満たさなくても、運転資金の残高が在庫の値打ちの `loans.inventory_ratio` 倍以内なら貸す（在庫の担保）。
- 銀行が貸せるのは、③貸した後も自己資本比率が規制の値以上で、④1つの民営施設への残高が銀行の自己資本×`banking.large_exposure` 以下（大口融資の規制。国営施設は対象外）の分。借り手は金利の低い銀行から順に、その銀行が貸せる分ずつ借りる。自己資本が減って上限を超えた残高は、返す元金の借り換えで保てるが増やせない。
- 借りきれない運転資金は、国営施設なら政府が出資し、民営施設なら変動費の利益が出ている間だけ資本家階級が出資する（出ていなければ縮み、続けば撤退。タブ 5）。建設資金を借りきれなければ資本家階級が多く出資し、出資しきれなければ建たない。
- 返済の遅れ: 返済日に預金が足りなければ運転資金を借りて返し、借りきれなければ払える分だけ払い（払えなかった利子は残高に足す）、遅れの回数を1つ増やす。続けて `loans.arrears_limit` 回で倒産（破綻の段。差し押さえと売却はタブ 5）。
- 住宅ローン（回3、BK-47）: 町が育つ資材を一般の層が市場で買うとき（タブ 2）、代金の `housing.ltv` を `housing.mortgage_turns` ターンのローンで借り、頭金は預金から出す。借りられるのは、毎ターンの返済（今の金利の利子＋元金）が層の直近の所得の平均の `housing.dti` 以内の分まで（国全体の住宅ローンの返済は所得の 5〜10%）。金利は政策金利＋銀行の利ざや（借り手は施設でないので ρ の項はない）。借りるのは市場の段で注文を出すときで、約定の後に資材に払った額の `housing.ltv` を超えた分をすぐ返す（借りたお金が預金に残ると消費に回る）。銀行と層ごとに1本にまとめ、新しく借りるたびに残りの期間を残高の重みで混ぜ直し、毎ターン「残り÷残りの期間」を返す。払った利子は層の所得から引く。仕事を失って失業者の層へ移る人は、預金と同じく1人あたりの額でローンも持って移り（職に就けば戻る）、所得の少ない失業者の層は返せなくなりやすい。返済が `loans.arrears_limit` 回続けて遅れた層のローンは、残りを銀行の損（貸し倒れ）として消す。家と人はそのまま（差し押さえて売る道は入れない）。自己資本比率の規制では会社の融資と同じに数え（今の規制はリスクの重みを持たない）、大口融資の規制はかけない。
- 経済体制: 混合経済では、国営銀行が基幹産業（体制で国営と決まっている施設）に利ざやなしで貸す（政策融資。`packs/base/data/regimes.yaml` の `policy_loans`）。計画経済では、国営銀行が国営施設に審査なしで貸し、返済が遅れたら借り換えさせるので倒産しない。

### 銀行の体力と準備預金

- 銀行の貸借対照表は勘定から計算する（資産は準備預金・融資・国債の時価・銀行間の貸し出し、負債は預金・銀行間の借り入れ・中央銀行からの借り入れ、残りが自己資本。建物は数えない）。
- 自己資本比率＝自己資本÷融資の残高（国債と準備預金は危なさを 0 として数えない）。規制の値 `banking.regulatory_ratio` を下回ると新しく貸さない（貸し渋り）。
- 損益は、融資・国債・準備預金・銀行間の受け取りの利子から、預金・銀行間・中央銀行への利子、職員の賃金、貸し倒れを引き、国債の実現損益を足した額。法人税の後、`firms.payout` を配当（民間）か国庫への納付（国営）にする。払った後も自己資本が目安（「融資×（規制＋`banking.risk_buffer`）」と「預金×`banking.capital_floor`」の大きい方）以上に残る分までしか払わない。
- 準備預金は預金の `banking.reserve_ratio` を最低限として中央銀行に置く。銀行をまたぐ支払いで準備預金が動き、最低限を下回った銀行は国債を売るか銀行間で借りる。余りには「政策金利−`central_bank.reserve_spread`」の利子が付き、余りの一部で国債を買う。

### 中央銀行

中央銀行は島に1つの、地図に載らない帳簿だけの主体。

| 手段 | 独立していないとき | 独立しているとき |
| --- | --- | --- |
| 政策金利 | 命令 `set_monetary_policy` で決める（`central_bank.rate_range` の中） | 式 `policy_rate` で自動 |
| 国債の売買 | 同じ命令で1ターンに買う・売る額を決め、売買の市場に指値なしで出す | しない |
| 直接の引き受け | 売れ残りを引き受ける政策と、命令で額を決めた引き受け | しない |
| 自分の持ち分の乗り換えと借り換えの買い支え | する | する（中央銀行自身の操作だから） |
| 銀行への貸し出し | 担保を取って自動。担保のない緊急の貸し出しは命令 `emergency_loan` | 自動の貸し出しは同じ。緊急の貸し出しは自己資本がプラスの銀行に自動 |

- 独立した中央銀行の政策金利（名前付きの式 `policy_rate`）。r\* は `central_bank.natural_rate`、π は直近12ターンの物価上昇率、π\* は `central_bank.inflation_target`、u\* はふつうの失業率、a と b は `central_bank.inflation_weight` と `central_bank.unemployment_weight`。1ターンに `central_bank.max_step` まで動かし、`central_bank.rate_range` に収める。

```latex
r_P = r^{*} + \pi + a\,(\pi - \pi^{*}) - b\,(u - u^{*})
```

- 独立させる・戻すのは命令 `set_independence`（行政力2）で、切り替えた後 `central_bank.switch_lock_turns` は戻せない。「独立」は政府が中央銀行に命令できないことを指し、政策の手段とは分ける。独立をやめたら国債の売れ残りの扱いを独立の前に戻す。
- 計画経済の国は独立させられない（BK-43。回5 のカード「両方」）。ソ連の国立銀行（ゴスバンク）や中国の人民銀行のように、計画経済の国立銀行は政府の一部で、お金を国の計画どおりに出す窓口だから。独立したまま計画経済に移ると、国有化の代金を引き受けるため政府の一部に戻る。
- 独立した中央銀行も、市場では国債を売り買いする（BK-44）。独立して禁じられるのは国が出す国債を直接買うことで、市場での売り買いで銀行の準備を整えるのはふつうの仕事（ユーロ圏・日本・アメリカ）。準備預金の足りない銀行が売りに出した国債を買い、足りない銀行がなければ買った国債を売り戻す。入れる前は、銀行が1つの島で国の黒字が準備を吸い上げると、国債を売る相手がなく、銀行が中央銀行から半年以上借り続けた。
- 銀行間の貸し借り: 中央銀行の段で、準備預金が最低限より多い銀行が貸し手、少ない銀行が借り手になり、次のターンの銀行の段で返すお金を、金利を値段にした板寄せで貸し借りする。値幅は「政策金利±`central_bank.corridor`」で、下端は準備預金の余りに付く金利、上端は中央銀行から借りる金利。貸し手は余りを3段に分けて出し、借り手は上端まで借りる。破綻した銀行には誰も貸さない。
- 中央銀行の貸し出し: それでも最低限に足りない銀行に、国債の時価と延滞のない融資を担保に（担保の値打ちの `central_bank.collateral_ratio` まで）上端の金利で貸す。担保も足りず最低限を満たせない銀行は資金繰りが破綻し、破綻の段で破綻する。準備預金がマイナスのままでは払えないので、マイナスの分はいつも貸す。
- 中央銀行の利益（国債と貸し出しの利子と国債の実現損益−準備預金の余りの利子）は、決算の段で全額を国庫に納める。損失は繰り越して後の利益で埋めてから納め、自己資本がマイナスになっても破綻しない。国庫の預金には利子を付けない。
- 中央銀行が持つ国債の利子は納付で国庫に戻るので、中央銀行が多く持つ国では不履行の後の高い利回りが罰としてあまり効かない。現実の仕組みに近いので残す。罰として効くのは、損をした銀行の融資の停止と救済の費用。
- 外貨準備・為替介入・外国の国債の買い入れ・通貨の融通はタブ 9。

### 国債の発行と入札

- 期間は3つ（1・5・10年。`packs/base/data/finance.yaml` の `bond_term`。期間の上乗せもそこにある）。額面で発行し、利率は入札で決まった利回り。利払いは発行の次のターンから毎ターン「額面×利率÷12」、満期に額面を返す。建国の国債の期間は `founding.money.bond`。
- 発行の額は国債の段が決める: 今のターンの利払いと償還に国庫が足りない分（借り換え）と、「次のターンの国債以外の支出の見込み×（1＋`bonds.buffer_turns`）＋次のターンの利払い−国庫の残高」（タブ 6 FS-12）。期間は政策の割合（`bonds.auto_terms` が既定）で分ける。命令 `set_bond_policy` で、期間の割合・売れ残りの扱い・追加の発行・中央銀行の直接の引き受け（額はそのターンだけ）を決める。
- 入札: 買い手は市中銀行（資本取引を認めた国では外国の買い手も。タブ 9）。各銀行は準備預金のうち「最低限＋預金×`banking.liquidity_share`（決算の支払いと預金の出入りの分。足りなくなれば国債を売るので小さくてよい）」と、中央銀行と銀行間から借りている額（前のターンに借りてこのターンの初めに返した額を含む。返したばかりの分を満期のお金で国債に戻すと、またターンの終わりに中央銀行から借りることになる）と、政府の預金（国がいつでも引き出すので全額を準備のまま持つ。FS-24）を超えた分を `banking.auction_steps` の段に分けて注文し、発行額が売れるいちばん低い利回りで全員が買う。段の指値は、その段まで買ったときに銀行が求める利回り。満期で戻ったお金と増えた預金は、次の入札で国債に戻る（日本の銀行が預金の余りを国債で持つのと同じ）。
- 銀行ごとに求める利回り: 市場の利回りに、国債への偏りの上乗せを足す。偏りは国債の額面÷資産（準備預金・融資・国債・銀行間の貸し）で、`banking.bond_concentration` の `free` を超えた分に `premium` を掛ける（1つの借り手に偏る危うさ）。中央銀行の持ち分は入らない。計画経済の国営銀行は預金を国に貸すのが仕事なので上乗せしない（ソ連の貯蓄銀行の預金は国の借り入れに回った）。
- 市場の利回り（名前付きの式 `bond_yield`）。λ\_T は期間の上乗せ、I/R は返す力の比（中央銀行以外の持ち手に払う利子÷国債と中央銀行からのお金を除く国の収入。どちらも年。収入の記録が12ターンに足りなければ平均の12倍）で、θ（`bonds.burden_threshold`）を超えた分に `bonds.burden_sigma` を掛ける。中央銀行に払う利子は納付で国庫に戻るので数えない（カード「市場の分だけ」、2026-10-15）。σ×（市場の持つ借金÷年の収入）が 1 を超えると、利回りの上がった分の利払いがさらに利回りを上げる雪だるまになり、上限まで上がる（2010年のギリシャ）。外国の持ち手・中央銀行の乗り換えと引き受け・初めの利回りはこの値を使う。κ は `bonds.kappa`、δ は不履行の上乗せ、β は中央銀行が独立していれば `bonds.independence_discount`。上限は `bonds.max_yield`（仮。BK-27）。

```latex
y = r_P + \lambda_T + \sigma \cdot \max\left(0, \frac{I}{R} - \theta\right) + \kappa \cdot \max(0, \pi - \pi^{*}) + \delta - \beta
```

- 新しい発行の売れ残り: 政策（`policy.bond_unsold`）が「引き受ける」で中央銀行が独立していなければ、中央銀行が同じ利回りで引き受ける。「支出を減らす」か独立していれば発行せず、足りない支出は支払いの順番で削られる（タブ 6）。引き受けた額は統計に残す。
- 満期の銘柄のうち中央銀行の持ち分は、同じ期間の新しい国債に乗り換える（独立していても。アメリカの連邦準備制度と同じ）。借り換えの入札で売れ残った分は、独立した中央銀行も政策によらず買い支える（2012年の欧州中央銀行、2022年のイングランド銀行の緊急の買い入れと同じ、金融の安定のための中央銀行自身の判断）。国庫が足りないだけでは不履行にならない。
- 中央銀行の国債の売り買いの結果と償還は、毎ターンログと統計に残す。

### 国債の売買の市場と時価

- 国債の段で発行の入札の後に、残りの期間で分けた4つの区分（境目は `bonds.buckets`）ごとに、利回りを値段にした板寄せで売り買いする（同じ板寄せの関数。値幅は前のターンの利回り±`bonds.trade_band`）。約定した区分の利回りは約定した利回りになり、売買のなかった区分は政策金利の動きだけ動かす。家計は国債を持たない。
- 市中銀行は、国債の額面を目安（偏りで求める利回りが区分の今の利回りと等しくなる持ち高。0 から資産まで）に近づけるように注文する（売りは自分の求める利回り＋`banking.sell_premium` まで受け入れ、買いは自分の求める利回り以上）。準備預金が最低限に足りない銀行は、銀行間で借りる前に足りない分を指値なしで売る。独立していない中央銀行は、命令の額を指値なしで出す。
- 中央銀行の値幅を越えた買い（回3、W-193、BK-48）: 中央銀行の買い注文が値幅の中で買いきれなければ、残りを、区分ごとに今の利回りから準備預金の利回り（政策金利−`central_bank.reserve_spread`）まで `banking.cb_sell_steps` 段に分けた売りの申し出と突き合わせて買う。銀行は利回りが市場の求める利回りより下がるほど、準備預金に置くのと比べて国債を持つ得が減るので、持ちたい額を準備預金の利回りで 0 になるまで直線で減らし、今の持ち高との差を売りに出す（準備預金が足りない銀行は先に売っているので加わらない）。約定した利回りがその区分の利回りになる。融資の金利は政策金利で決まる変動金利なので、長い利回りが下がっても融資の金利は直接は下がらない（銀行の国債の売り代金が準備預金になり、預金と国債の利回りが下がるのが効き目）。
- 時価: 銘柄の値段は、残りの利払いと償還を、残りの期間の利回りで割り引いた額。区分の間は、区分を代表する残りのターン数（`bonds.bucket_terms`）の利回りを直線でつなぐ。
- 評価: 決算の段で銀行と中央銀行の持つ国債を時価に直し、自己資本は時価で数える。損益に入れるのは、売った・償還された・切り下げられた分の「受け取った額−取得原価」（実現損益）だけで、まだ売っていない評価損益は法人税・配当・国庫への納付に回さない（取得原価は持ち手ごと・銘柄ごと）。破綻の段で出した国債は時価の帳簿に直接足し、次のターンの評価益に数えない。
- 金利が上がると国債を多く持つ銀行の自己資本が減り、規制を下回れば貸し渋りと取り付けが起き、マイナスになれば破綻する（2023年の米国の銀行の破綻と同じ筋道。現実に近いので残す）。
- 資本取引を認めた国の銀行は、外国の利回り−値下がりの率が自国の利回りより `banking.foreign_margin` 以上高い通貨だけ、外貨と外国の国債を預金の `banking.foreign_share` まで、1ターンに `banking.foreign_step` ずつ持つ（タブ 9）。

### 債務不履行

- 不履行は、命令 `set_bond_payment` で支払いを止めたときだけで、すべての銘柄を一度に不履行にする。自国通貨の国債の不履行は、発行できないから起きるのではなく、制度と政府の選び方で起きるため。
- 不履行の銘柄は額面を `bonds.haircut` だけ切り下げ（持ち手の損。外国の持ち手も同じ）、残りを同じ期間で出し直す。区分の利回りが跳ね上がり、時価も下がる。自己資本が尽きた銀行は破綻する。
- 罰は3つ（支払いを止めると借金が減る分だけ得な手になっていたため）。
  - 利回りに上乗せ δ（`bonds.default_premium`。`bonds.default_decay_turns` かけて 0 に減る）。借金の多い国はすぐ上限に届く。
  - 不履行から `bonds.default_exclusion_turns` は、銀行が国債を買わない（入札に来ず、売買の市場でも買わない。売りはする）。新しい発行の売れ残りは政策どおり中央銀行が引き受けるか支出を削る。期間が終わるターンにログで知らせる。
  - 国債で損をした銀行は `banking.default_crunch_turns` のあいだ新しい融資を止め、返す元金の借り換えだけを貸す。断った分は利ざやを上げる。民営工場の参入は止めている銀行からは借りない。計画経済の島の国営銀行と、不履行の後に建った銀行は止めない。止める期間が終わったらログで知らせる。
- 不履行のログ・手引き・国債の命令の欄・金融の画面（止めている銀行）に、罰の中身と終わるターンを出す。政情への効き目はタブ 10。
- 回5 で、国庫が払えないときに国債の支払いを止めるかを、払う順番と借金の重さと一緒に見直す（タブ 6 FS-22）。

### 破綻と破綻処理

- 自己資本がマイナスになった銀行と、資金繰りが破綻した銀行は破綻し、新しい融資と預金の受け入れを止める。破綻したわけはログに出たまま残す。
- プレイヤーは命令 `resolve_bank`（行政力を使わない）で次の3つから選ぶ。次のターンまでに選ばなければ「引き継ぎ」、引き継いでも自己資本比率が規制を下回らない銀行がなければ「救済」にする。
  - 救済: 政府が自己資本を「融資×（規制＋`banking.risk_buffer`）」と「預金×`banking.capital_floor`」の大きい方まで（準備預金が最低限と次のターンの職員の賃金に足りなければその分も）注入し、国営銀行として残す（すぐまた破綻しないように）。
  - 引き継ぎ: 預金・融資・国債・銀行間の貸し借りを、引き継いでも規制を下回らない健全な銀行のうち預金の最も多い銀行に移す。
  - 清算: 融資をすべて額面の `banking.liquidation_price` で健全な銀行に売ったとして損失を数え、引き継ぎと同じく移す（安く買えた分は買った銀行の得）。
- 破綻処理では、返済が遅れているか借り手が倒産した融資を 0、借り手が動いていない融資を額面の `banking.liquidation_price` で見る。安く見た分は預金保険から引き継ぐ銀行に渡る（後で貸し倒れても引き継いだ銀行が倒れないように）。
- 損失（資産が負債に足りない分）は、保護されない預金と、破綻した銀行に貸していた銀行の貸し出しが同じ割合で負う（破綻が連鎖することがある）。足りない分は政府が払い（預金保険）、政府のお金が足りなければ国債を出し、それでも足りなければ保護される預金も削る。削った額はログの「預金の切り捨て」に出す。
- 預金保護: 預金者1人あたり（施設と銀行は1つあたり）上限額（`banking.deposit_insurance`、命令 `set_bank_policy` で変え、無制限にもできる）まで全額を守る（日本のペイオフと同じ形）。家計の層の預金は「人数×上限額」まで守るので、一般と技術者の層はほぼ全額守られ、損は資本家階級と預金の多い施設にかかる。預金保険の額は残った預金を超えない。削られた預金は消費の式の預金を通じて消費を減らす（タブ 4）。
- 引き継ぐ健全な銀行がない島（銀行が1つなど）で引き継ぎか清算を選ぶと、政府が銀行を引き取り、出資を消して、損失を同じ順番で負わせて立て直す（黙って救済にしない。ログにも出す）。
- 救済と預金保護に国庫から出すのは、次のターンの `fiscal.rescue_after` までの支払いを残した額まで。足りない分は預金の切り捨てで埋め、銀行が債務超過のまま残らないようにする。国庫から出したターンは、次のターンの支出の分まで国債を出して国庫を戻す（タブ 6 FS-13）。出した国債は中央銀行の保有と引き受けの統計に入れる。
- 合併（回7 後半。BK-46）: 破綻の段で、破綻を数える前に、弱った銀行（直近 1 年の利益がマイナスで、自己資本比率が「規制＋`banking.risk_buffer`」を下回り、危ない融資を安く見た後の自己資本がまだプラス）を、同じ持ち主の区分（民営どうし・国営どうし）の健全な銀行が引き取ります。引き取る銀行は、直近 1 年の利益がプラスで、引き取った後も自己資本比率が「規制＋`banking.risk_buffer`」以上、株主に払った後の準備預金が合わせた預金の最低限に足りる銀行（預金の多い順）です。安く見た後の自己資本を値段として株主に払い（株を売ったお金なので所得に数えない）、出資を消して、預金・融資・国債・銀行間の貸し借りをすべて引き継ぎます。1 ターンに 1 つまで、計画経済の島ではしません。合併の後 1 年はその島に新しい銀行が入りません（減った分にすぐ入ってまた弱るのを繰り返さないように）。安く見た後の自己資本がマイナスなら合併せず、破綻すれば上の破綻処理の道です（現実の救済合併。日本の地方銀行の再編、米国の当局の仲介する合併）。統計は `banks.merged`、ログは「合併で引き取りました」。
- 経費の切り詰め（回7 後半。BK-45）: 直近 1 年の利益がマイナスになった銀行は、職員の下限（`banking.staff_floor`）を外して預金と融資の量に見合う人数まで減らします（地方銀行の店舗の統合）。直近 1 年の利益がプラスのまま `banking.lean_turns` ターン続いたら下限を戻します。
- 荒れすぎない工夫として、銀行の数・乗り換え・取り付けに上限（`banking.max_banks`・`banking.switch_share`・`banking.run_share`）を置く。
- 閉鎖・倒産・破綻処理で施設がなくなったマスには送電線を残す（タブ 8）。

### 回5 で作り直すこと

- 利回り（カード「市場の分だけ」、2026-10-15）は回5 の前半で入れた（上の「国債の発行と入札」）。日本の形（借金が年GDPの2.5倍でも、日本銀行が半分を持ち残りを国内の貯蓄が持つので利回りが低い）がこの流れから出るかは、回5 の後半の測りで見る。上限は今のまま（BK-27）。
- 使われないお金と物価（「両方入れる」、2026-10-15）: 家計が預金から使う割合を物価上昇率と預金の実質金利で動かす、消費財の指値に直近の物価上昇率を足す、失業が多い島でも物価の上がった分は賃金に追わせる（中身はタブ 3・4）。作られていない物は、お金が増えても商社の輸入か品不足のせり上げでしか値段が付かない。ハイパーインフレ（外貨や物への逃避・為替の暴落・買いだめ）はフェーズ4かベータ版の後。
- 回5 の後半の測りで、中央銀行を独立させた島の銀行の破綻が 0→2 に増えた。金利 0 に近い間に小さな銀行が利ざやの赤字を重ね、そこへ取り付けが来る形（シリコンバレー銀行の型）で、破綻処理の道もあるので数字で抑えない。銀行の経費の切り詰めと合併は回7 後半で入れた（台帳 2026-10-19 03:20。上の「破綻と破綻処理」）。
- 島の国債の銘柄が増え続けるのが、借金の膨らみか経済の動きとして正しいかを確かめる（タブ 14 W-07・W-08）。
- 関門で見ること: 利回りが上限に張り付く島の数、借金／年GDP、利払い／GDP、物価上昇率が目安に入る割合、国債のうち中央銀行の持ち分の割合。揺さぶりの試験（中央銀行が毎ターン GDP と同額を引き受ける）で物価が上がるか（タブ 12）。

## 境目の表

| 場面 | 誰が・何を | どれくらいの間に | 元に戻る道 | 現実の例 | 設定の名前 |
| --- | --- | --- | --- | --- | --- |
| 足りない | 準備預金が足りない銀行: 国債を売る → 銀行間で借りる → 中央銀行が担保で貸す（遅らせる役: 借り）→ 担保もなければ資金繰りの破綻。運転資金が借りられない施設: 政府か資本家階級の出資（遅らせる役）→ 返せなければ遅れを数えて倒産 | 銀行間と中央銀行の借りは次のターンに返す。倒産は遅れが続いて `loans.arrears_limit` 回 | 預金金利を上げて預金を集める。破綻処理で健全な銀行に移す | 最後の貸し手（1866年の恐慌でイングランド銀行が担保を取って貸した。バジョットの原則） | `central_bank.collateral_ratio`, `loans.arrears_limit` |
| 余る | 預金が使われずに積まれる（逃げ先がない。回5 で預金から使う割合を物価と実質金利で動かす）。準備預金の余りは国債の入札と銀行間の貸しに回る | 毎ターン | 回5 | 日本の長い低金利と預金の積み上がり | `banking.auction_share` |
| 0 になる | 自己資本が 0 を割った銀行は破綻し、処理で引き継ぎ・救済・清算。損は保護されない預金と銀行間の貸し手、次に預金保険、最後に保護される預金 | 破綻の次のターンまでに処理 | 救済した銀行は国営銀行として残る | 2023年のシリコンバレー銀行（評価損で自己資本が尽き、取り付け） | `banking.deposit_insurance`, `banking.liquidation_price` |
| 一部だけ（失業した人の住宅ローン） | 仕事を失った人はローンを持って失業者の層へ移り、所得が少ないので返せなくなる。続けて遅れた層のローンは銀行の損として消える（島の住宅ローン全部が一度に消えることはない） | 遅れが `loans.arrears_limit` 回 | 職に就いた人はローンを持って一般の層に戻る | 不況の失業で増える住宅ローンの延滞（2008年のアメリカ） | `housing.dti`, `loans.arrears_limit` |
| 持ち主・相手がいない（中央銀行の買いに売り手が来ない） | 値幅の中の買いで買いきれなければ、利回りを準備預金の利回りまで下げて、持ちたい額の減った銀行から買う（BK-48）。それでも売り手がいなければ買いきれずに残す | 毎ターン | 命令の額を変える | 日本銀行の指値オペ（2022年）・量的緩和 | `banking.cb_sell_steps` |
| 一部だけ（層の中の少しの人、島の中の 1 か所） | 大口の貸し先が1つ倒れると小さい銀行が債務超過になる（大口融資の規制で残高を抑える）。損は預金の多い資本家階級と施設に偏る | 倒産のターン | 規制の範囲の残高だけ借り換えで保てる | 大口の取引先の倒産での地方銀行の破綻 | `banking.large_exposure` |
| 一部だけ（小さな銀行が赤字続き） | 人を減らして赤字を小さくし（BK-45）、弱れば健全な銀行が合併で引き取る（BK-46）。引き取る銀行がなければ破綻処理 | 1 年の赤字の後 | 利益が 2 年続けば人を戻す。合併の 1 年後から新しい銀行が入る | 地方銀行の店舗の統合と再編 | `banking.lean_turns`, `banking.risk_buffer` |
| 持ち主・相手がいない | 国債の入札に買い手が来ない: 新しい発行は政策で引き受けか支出を削る、借り換えは中央銀行が買い支える。不履行の後の決めたターン数は銀行が買わない。破綻した銀行には誰も貸さない。引き継ぐ銀行がない島は政府が引き取って立て直す | 不履行の後は `bonds.default_exclusion_turns` | 期間が明けると買い手が戻る | 不履行の後に国債の市場から締め出された国（2001年のアルゼンチン） | `bonds.default_exclusion_turns` |
| 時間がたつ（満期・寿命・一巡・育ちきった後） | 満期の中央銀行の持ち分は乗り換え、市中銀行の分は借り換え。借金が利子で雪だるまになり、利回りが上限に張り付く島が出る（回5 で利回りの決まり方を作り直す。張り付いた後の行き先はフェーズ4かベータ版の後）。不履行の上乗せはだんだん減る | 標準の計画の 300 ターン目に 50 島中 33〜39 島が上限に張り付いた | 回5 | 利払いが税収の大きな割合を占める国の財政再建 | `bonds.max_yield`, `bonds.default_decay_turns` |

## 前提と依存

### この仕組みが前提にしていること

- 施設の利益・在庫・設備の値打ち（審査と担保。タブ 3・5）。
- 家計の層と預金の持ち方（タブ 4）。
- 国庫の支払いの見込みと支払いの順番（自動の発行の額と、売れ残りで削る支出。タブ 6）。
- 物価上昇率・失業率・年GDP（政策金利と利回りの式。タブ 11 の統計）。
- 経済体制（計画経済の国営銀行・政策融資・参入の既定。タブ 10）。

### この仕組みの結果を前提にしている仕組み

- 国庫と国債の自動の発行、救済の費用（タブ 6）。
- 民営工場の建設資金と参入、運転資金で動く独立採算の施設、倒産と差し押さえ（タブ 5）。
- 預金と預金金利から決まる家計の消費（タブ 4）。
- 外貨・外国の国債・為替介入・非居住者の預金（タブ 9）。
- かみ合い: 中央銀行の独立 × 建国の国債 × 自動の不履行（回1 の前に 60 ターン目の崩れ）。国債 → 銀行 → 国庫 → 発電所 → 食品工場 → 餓死の連鎖（つなぎ目に緩め役がなかった。回1・回3 で遅らせる役を入れた）。人口 × 公共事業費 × 借金（回5）。

## 見直しの記録

| 日付 | 見つけた場面 | 設計に足したこと |
| --- | --- | --- |
| 2026-10-14 | 国債の利払いを止めると次のターンに利回り9%ほどで売れ、借金が減る分だけ得な手になっていた | 不履行の罰を3つ（上乗せ・入札の締め出し・損をした銀行の融資の停止）（BK-34） |
| 2026-10-15 | 中央銀行を独立させた国が建国の5年国債の満期で自動の不履行になり、銀行の破綻から島が崩れた（回1） | 中央銀行の持ち分の乗り換えと借り換えの買い支え、不履行は命令だけ（BK-29・BK-33） |
| 2026-10-18 | 設計書の整理で書き直した。境目の表は rework-design.md 2.2 と本文の分だけ埋めた | 不履行の条件を回1 の形に、破綻処理を選ばなかったときの既定をコードの形（引き継ぎ、だめなら救済）に合わせた。数字は設定の名前で書いた |
| 2026-10-26 | 試し遊び2回目: 不況の国が中央銀行で 20 億の国債を買う注文を出しても 0〜3 億しか買えなかった（W-193）。家計は借りずに家を建て、住宅への投資が 0 だった（W-186） | 値幅を越えた中央銀行の買い（BK-48）、家計の住宅ローン（BK-47）。融資は政策金利の変動金利なので、長い利回りを融資の金利に届かせる案は入れなかった |
