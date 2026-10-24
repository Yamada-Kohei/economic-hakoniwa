# 10 政治と体制

政治体制と経済体制の特色、政情とその段階（デモ・暴動・反乱の前触れ）、治安と報道の統制、議会と選挙と支持率、憲法改正と経済体制の変更と混乱期間、首都の建て替え、労働の動員、計画経済（公定価格・割当・生産目標・重点産業・配給と行列・貿易の独占）と混合経済の値段の規制、移民の政策の体制ごとの上限、国どうしのやりとり（公電と掲示板）を扱います。建国で体制を選ぶ流れと首都の建物はタブ 2、幸福度と国をまたぐ移民の動きはタブ 2、失業と賃金と格差はタブ 4、暴動で止まる施設の種類と国有化はタブ 5、治安費と国庫の払う順番と産業政策はタブ 6、中央銀行の独立と国債の不履行はタブ 7、計画経済の商社の動きはタブ 9 を見ます。

## コードの場所と試験

- 政治の共通の部品（政治の状態・体制の特色・行政力の足し引き・混乱期間の生産性・首都の建物・格差の指数）: `hakoniwa_core/politics.py`
- 政情の段と政治の命令（政情・選挙・議会の採決の関所・憲法改正・報道の統制・労働の動員・暴動・建て替え）: `hakoniwa_core/modules/politics/`（段 `politics`、名前付きの式 `stability`）
- 計画経済（公定価格・据え置きの補助金・割当・生産目標・配給と行列）: `hakoniwa_core/economy/plan.py`。命令は `hakoniwa_core/modules/plan/`
- 命令の行政力と関所: `hakoniwa_core/modules/planning/`。議会の要る命令は `packs/base/data/orders.yaml` の `parliament`
- 体制の特色: `packs/base/data/regimes.yaml` の `traits`。警察署は `facilities.yaml` の `police`
- 移民の政策: `hakoniwa_core/modules/migration/`（動きはタブ 2）
- 公電（内部の名前 letters）: `server/` の外交の画面と API、`tools/playtest/hk.py`
- 掲示板: `server/board/`（保存 `models.py`、書く・読む・歯止め `services.py`、名前とトリップと ID `identity.py`、本文の HTML `render.py`、画面 `views.py`、API `api.py`）、画面は `server/templates/board/`・`server/static/js/board.js`・`server/static/css/board.css`
- 宣戦・条約・戦争の状態（フェーズ4-1）: `hakoniwa_core/diplomacy/`（戦争 `war.py`・条約 `treaties.py`・条項 `clauses.py`・経済の条項 `economic.py`・違反の信用の傷 `credit.py`）、段と命令は `hakoniwa_core/modules/diplomacy/`。国際会議場は `server/hall/`、外交の画面と API は `server/web/diplomacy.py`
- 試験: tests/core/test_phase3_4.py、tests/core/test_migration.py、tests/core/test_log_messages.py、tests/server/test_phase3_4.py、tests/server/test_letters.py、tests/core/test_diplomacy.py、tests/core/test_diplomacy_economic.py、tests/server/test_diplomacy.py、tests/server/test_board.py

## つながり

- 前提にしていること: 幸福度と住民（タブ 2）、失業率・実質賃金・家計の層の所得（タブ 4）、物価上昇率（タブ 3・11）、国有化と民営化の仕訳（タブ 5）、治安費と国庫（タブ 6）、国債の不履行（タブ 7）、公共事業（タブ 8）、命令の列と行政力（タブ 1）。
- 前提にされていること: 行政力（タブ 1）、首都の建物（タブ 2）、町の店と工場の止まり（タブ 5）、市場の公定価格と値段の規制（タブ 3）、計画経済の商社（タブ 9）、移民の出国と受け入れ（タブ 2）、関税・禁輸・資本取引・中央銀行の独立の採決（タブ 6・7・9）、工期（タブ 5・8）、関門の政情の目安（タブ 12）。
- 詳しくは下の「前提と依存」。

## 決まりの一覧

| 番号 | 決まり（一行） | 状態 | 設定の名前 | 試験 |
| --- | --- | --- | --- | --- |
| PL-01 | 政治体制は「政治の仕組み（民主主義・権威主義・全体主義）」と「国家元首の形（君主制・共和制）」の2軸、経済体制は市場・混合・計画の3つ。建国で選び、憲法改正と経済体制の変更で変える | 決定 |  | tests/core/test_phase3_4.py::test_islands_without_a_political_regime_get_the_first_choices |
| PL-02 | 体制の特色は `regimes.yaml` の `traits` に書き、軸ごとに選んだ体制の特色を合わせる（足す・掛ける・小さい方・どれかが真など、特色ごとに決まった合わせ方） | 決定 |  | tests/server/test_phase3_4.py::test_politics_forms_follow_the_regime |
| PL-03 | 政情は島ごとの 0〜100 の数で、名前付きの式 `stability` の目標 T* に、毎ターン「目標との差 × 動きやすさ」だけ近づく。初めのターンは目標から始める | 決定 | `politics.stability_turns` | tests/core/test_phase3_4.py::test_every_island_has_politics_and_it_started_at_the_target, tests/core/test_phase3_4.py::test_stability_moves_toward_the_target_by_the_regime_speed |
| PL-04 | 目標は 50 ＋ 幸福度 − 失業率 − 物価上昇率 − 格差（それぞれ決めた境を超えた分に重み）＋ 体制の補正 R ＋ 出来事の補正 E。失業率の重みは設計の値 | 決定 | `politics.weights` | tests/core/test_phase3_4.py::test_inflation_shock_brings_demonstrations_and_riots_and_they_pass |
| PL-05 | 出来事の補正: 債務不履行（だんだん戻る）、体制の変更の混乱期間、首都の政府の建物が壊れている間、出国を止められて出られない人（タブ 2 PO-41） | 決定 | `politics.events`, `migration.trapped`, `migration.trapped_max` | tests/core/test_migration.py::test_a_ban_traps_people_and_lowers_stability |
| PL-06 | 段階は落ち着いている・デモ・暴動・反乱の前触れの4つ。行政力はデモで −1、暴動で半分、反乱の前触れで4分の1（どれも最低の値あり）。変わったらターンログに書く | 決定 | `politics.levels` | tests/core/test_phase3_4.py::test_levels_follow_the_bounds |
| PL-07 | 暴動と反乱の前触れでは、町以上の住宅地の近くの施設を乱数で選んで次のターンから数ターン止め（農林水産業は止めない。タブ 5 FM-34）、まれに国営の施設を壊す。反乱の前触れは割合と見込みが2倍 | 決定 | `politics.riot.urban`, `politics.riot.radius`, `politics.riot.halt_share`, `politics.riot.halt_turns`, `politics.riot.destroy_chance` | tests/core/test_phase3_4.py::test_riot_halts_urban_facilities_and_police_hold_it_down, tests/core/test_log_messages.py::test_riot_stop_is_written_in_words |
| PL-08 | 壊れた施設は、建てるときの資材と人手の決めた割合を国の工事として使って直す。在庫と帳簿はそのまま | 決定 | `politics.riot.repair_share` | tests/core/test_phase3_4.py::test_destroyed_facility_is_repaired_as_state_works |
| PL-09 | 警察署（公務の施設）の警官は治安費から払い、住民あたりの決めた人数がいれば、止まる施設と壊れる見込みを決めた割合だけ減らす（足りなければ比例）。政情そのものは上げない | 決定 | `politics.riot.police_per_person`, `politics.riot.police_effect` | tests/core/test_phase3_4.py::test_riot_halts_urban_facilities_and_police_hold_it_down, tests/server/test_phase3_4.py::test_police_station_can_be_built |
| PL-10 | 報道と集会は自由・制限・統制の3段（命令）。制限と統制は目標に足す代わりに、その一部を毎ターン「たまった不満」として積み、ゆるめるか政情が暴動の境を切ったらまとめて引く。民主主義は自由だけ | 決定 | `politics.press.bonus`, `politics.press.grievance_rate`, `politics.press.grievance_max` | tests/core/test_phase3_4.py::test_press_control_raises_stability_and_stores_grievance |
| PL-11 | 支持率は政情に実質賃金の1年の伸びを足し、失業率の1年の伸びを引いたもの | 決定 | `politics.support.wage`, `politics.support.unemployment` | tests/core/test_phase3_4.py::test_parliament_votes_by_seats_and_support |
| PL-12 | 民主主義の島は決めたターンごとに選挙を自動で行い、支持率を与党の議席の割合にする。政党は作らない。命令「議会の解散」で今の支持率で選び直せる | 決定 | `politics.election_turns` | tests/core/test_phase3_4.py::test_parliament_votes_by_seats_and_support |
| PL-13 | 議会の要る命令（`orders.yaml` の `parliament`）は実行するときに採決する。賛成の割合 = 与党の議席 ＋（今の支持率 − 選挙のときの支持率）× 振れ。過半数は 1/2 を超えたら、3分の2 は 2/3 以上で通る | 決定 | `politics.parliament.swing`, `politics.parliament.majority`, `politics.parliament.two_thirds` | tests/core/test_phase3_4.py::test_majority_needs_more_than_half_and_two_thirds_is_inclusive |
| PL-14 | 議会が要る命令は、税率の大きな変更・国有化・民営化・中央銀行の独立の切り替え・関税・禁輸・資本取引の制限・移民の受け入れと出国・公定価格の改定・宣戦布告・条約の提案と署名と破棄（過半数）、憲法改正と経済体制の変更（3分の2） | 決定 | `politics.parliament.tax_points` | tests/core/test_phase3_4.py::test_only_big_tax_changes_need_a_vote, tests/core/test_phase3_4.py::test_no_vote_without_parliament |
| PL-15 | 否決された命令も行政力を使い、何も変えず、わけをターンログに書く | 決定 |  | tests/core/test_phase3_4.py::test_rejected_order_is_logged_and_changes_nothing |
| PL-16 | 共和制は選挙で支持率が決めた値を超えると、次の決めたターン数だけ R を足す。新しい国家元首の名前はプレイヤーが決める（共和制だけ） | 決定 | `politics.republic.support`, `politics.republic.turns` | tests/core/test_phase3_4.py::test_leader_can_be_renamed_only_in_a_republic |
| PL-17 | 君主制は R を足し、君主の年齢をデータで持つ。継承などの君主の出来事は後からモジュールで足す | 決定（出来事は後から） | `politics.monarch_age` | （未確認） |
| PL-18 | 憲法改正と経済体制の変更は重い命令（民主主義では議会の3分の2）。変えた後は混乱期間で、生産性が下がった所から一様に戻り、政情の目標が下がる。混乱期間中はもう一度変えられない。同じ体制や知らない体制には変えられない | 決定 | `politics.transition.turns`, `politics.transition.productivity`, `politics.events.transition` | tests/core/test_phase3_4.py::test_amendment_starts_a_transition_and_rebuilds_the_capital, tests/core/test_phase3_4.py::test_same_or_unknown_regime_is_refused |
| PL-19 | 政治体制を変えると首都の建物を建て替える。資材は中央省庁の資材で、国の倉庫の分を先に使い、足りない分はほかの工事より先に公共事業費で買い、建て替えだけに使う。そろうまで前の体制の見た目 | 決定 |  | tests/core/test_phase3_4.py::test_amendment_starts_a_transition_and_rebuilds_the_capital, tests/server/test_phase3_4.py::test_politics_page_shows_the_rebuild_materials |
| PL-20 | 計画経済に移ると、民営の施設と民間銀行は帳簿の純資産を国債で払って国有化する。計画経済から移ると、国営施設は残り、民営化は命令で行い、民営工場と民間銀行の参入が始まる | 決定 |  | tests/core/test_phase3_4.py::test_change_to_planned_nationalizes_everything, tests/core/test_phase3_4.py::test_change_from_planned_keeps_state_facilities_and_lets_firms_in |
| PL-21 | 体制の特色: 民主主義は政情が下がる動きが遅く R が正、権威主義は行政力 ＋1 と国の工事の工期が短く政情が速く動く、全体主義は労働の動員と幸福度の上限と政情が最も速く R が負 | 仮 |  | tests/core/test_phase3_4.py::test_stability_moves_toward_the_target_by_the_regime_speed |
| PL-22 | 経済体制の特色: 計画経済は重点産業の工期と経験の伸びが良く、ぜいたく品の種類の満足が少ない。市場経済は民営工場の参入の基準が低い。混合経済は基幹産業への政策融資（タブ 7 BK-15） | 仮 |  | （未確認） |
| PL-23 | 労働の動員（全体主義の命令）: 失業者を国営施設に最低賃金（なければ平均賃金の決めた割合）で割り当て、住民のうち働く人の割合を足す。生産目標を見ないので作りすぎて腐ることがあり、難しさとして残す | 決定 | `politics.mobilization.share`, `politics.mobilization.wage` | tests/core/test_phase3_4.py::test_mobilization_puts_the_unemployed_to_work_in_state_factories |
| PL-24 | 計画経済の島は公定価格で値幅0の板寄せ（電力も）。公定価格は原則据え置きで、決めたターンごとに原価（原価＋1割を今の平均賃金で解いた値段）を確かめ、差が幅を超えた財だけ改める | 決定 | `planning.review_turns`, `planning.review_band` | tests/core/test_phase3_4.py::test_planned_market_clears_at_the_official_prices, tests/core/test_phase3_4.py::test_official_prices_stay_unless_the_gap_passes_the_band |
| PL-25 | 据え置く財（既定は食料）は原価を下回っても据え置き、差を国庫から補助金で埋める（払えなければ量に比例して払える分だけで、残りは施設の赤字） | 決定 | `planning.held_goods` | tests/core/test_phase3_4.py::test_held_goods_below_cost_are_subsidized |
| PL-26 | 命令: 公定価格の改定（名指しした財を今の原価に。議会のある体制は過半数）、公定価格の決まり（幅と据え置く財）、財ごとの上書き（確かめで変えない） | 決定 |  | tests/core/test_phase3_4.py::test_official_price_reform_and_rules_orders, tests/core/test_phase3_4.py::test_plan_orders_check_and_log |
| PL-27 | 割当ルール: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事 → 家計（配給）→ 輸出の順に配り、同じ順位の中は量に比例 | 決定 |  | tests/core/test_phase3_4.py::test_allocator_serves_priority_then_state_then_households_then_exports |
| PL-28 | 配給: 家計の中はお金の多さでなく1人あたり同じ量まで。買えなかった割合を財ごとの「行列」として出し、使えなかったお金は預金に残る（強制貯蓄） | 決定 |  | tests/core/test_phase3_4.py::test_ration_gives_each_person_the_same_amount, tests/core/test_phase3_4.py::test_half_price_plan_queues_and_saves_but_keeps_producing |
| PL-29 | 生産目標: 国営施設ごとに、直近の公定価格での需要（買えなかった分と、国の工事とインフラの資材のうち公共事業費で買えなかった分も含む）に上乗せした量を、同じ財を作る国営施設に設備の大きさで割り振る。施設は採算でなく目標に合わせて動く | 決定 | `planning.demand_turns`, `planning.target_markup` | tests/core/test_phase3_4.py::test_targets_follow_demand_and_capacity |
| PL-30 | 重点産業は命令で産業の分類を決めた数まで選ぶ。割当の先頭になり、体制の特色（工期と経験）が効く | 決定 | `planning.max_priority` | tests/core/test_phase3_4.py::test_plan_orders_check_and_log |
| PL-31 | 計画経済の貿易は国営の商社の独占: 行列のある財を輸入し、売れ残った財を値段によらず輸出する。輸入は輸出で得た外貨の範囲（タブ 9） | 決定 |  | tests/core/test_phase3_4.py::test_planned_trade_is_a_state_monopoly, tests/core/test_phase3_4.py::test_planned_traders_export_unsold_goods_whatever_the_price |
| PL-32 | 混合経済はどの財にも値段の上限と下限を決められる。市場経済は電気料金の上限だけ | 決定 |  | tests/core/test_phase3_4.py::test_price_floor_keeps_the_market_price_up, tests/server/test_phase3_4.py::test_plan_page_for_a_mixed_economy_shows_price_controls |
| PL-33 | 闇市は入れない。後からモジュールで足せるよう、配給で買えなかった量と余ったお金を段の間に残す | 決定（後から） |  | （未確認） |
| PL-34 | 移民の出国の政策は政治の仕組みで上限がある: 民主主義は自由だけ、権威主義は許可制まで、全体主義は禁止まで（`regimes.yaml` の `exit_control`）。受け入れの政策は体制によらない | 決定 |  | tests/core/test_migration.py::test_democracy_cannot_restrict_exit_and_amending_relaxes_it |
| PL-35 | 国どうしのやりとりは、非公開の公電（送るとすぐ届き、発信国と宛先国の持ち主だけが読める）と公開の掲示板（板・番号付きの書き込み。PL-51〜PL-55）。画面・API・MCP・hk.py から同じように使える | 決定 |  | tests/server/test_letters.py::test_send_receive_and_read, tests/server/test_letters.py::test_others_cannot_read |
| PL-36 | 公電は1つの島が24時間に送れる数に上限があり（サーバーの設定）、管理画面で非表示にできる | 決定 |  | tests/server/test_letters.py::test_daily_limit, tests/server/test_letters.py::test_admin_hides_letters |
| PL-37 | 外交の決まりは遊ぶ人に任せる。条約は両国が署名すると効く条項の束で、停戦・講和・不可侵・同盟は4-1の①で作った（予告どおりの破棄は罰なし、破ると違反の記録と信用の傷、ゲームは条項を強いない）。貿易圏・二国間契約・固定相場・融通・援助・移民の約束も同じ条約の条項（PL-45〜PL-50。4-1の②で作った）。関税と禁輸は一方的のまま | 決定（①と②で作った） | `diplomacy.notice_turns`, `diplomacy.violation_premium` | tests/core/test_diplomacy.py::test_denounced_non_aggression_ends_without_penalty, tests/core/test_diplomacy.py::test_declaring_war_on_a_treaty_partner_is_a_violation |
| PL-38 | 反乱と内戦はフェーズ4。今は反乱の前触れをターンログで知らせるだけ | 決定（フェーズ4 で作る） |  |  |
| PL-39 | 政治の画面に政情・目標の内訳・段階・支持率・議席・採決の履歴・混乱期間・建て替えの資材を出す。計画経済は「計画」、混合経済は「値段の規制」の画面を出す | 決定 |  | tests/server/test_phase3_4.py::test_politics_page_and_api_show_the_state, tests/server/test_phase3_4.py::test_plan_page_for_a_planned_economy, tests/server/test_phase3_4.py::test_market_economy_has_no_plan_link |
| PL-40 | 宣戦布告は重い命令（行政力 2、民主主義は議会の過半数、伏せない）。破棄の予告のターンがたった条約を破る宣戦は違反にならない。出すと国際会議場に自動で載り、戦争は次のターンから始まる。国と国の間は平時・布告・戦争・停戦のどれかで、同じ組の戦争は同時に1つ。休止中の島や自分には宣戦できない（MI-02・MI-03） | 決定 | `diplomacy.proposal_turns` | tests/core/test_diplomacy.py::test_declared_war_starts_next_turn, tests/core/test_diplomacy.py::test_cannot_declare_twice_or_on_self, tests/core/test_diplomacy.py::test_democracy_votes_on_war_and_treaties |
| PL-41 | 条約の提案・署名・断り・取り下げ・予告つきの破棄。提案は返事がないまま決めたターンがたつと流れ、同時に出せる数に上限がある。提案（提案する国の署名にあたる）と署名と破棄は民主主義では議会の過半数。講和が効くと、その戦争の停戦の条約は終わる（次の戦争の停戦を塞がない）。賠償だけが残る講和は、次の戦争の講和を塞がない。休止中の島には宣戦も条約もできない。署名した両国のうち後の署名で、そのターンの外交の段から効く。戦争のあいだは停戦と講和（と講和に添える取り決め）しか結べない（MI-04〜MI-06） | 決定 | `diplomacy.proposal_turns`, `diplomacy.max_proposals`, `diplomacy.min_notice_turns`, `diplomacy.max_notice_turns` | tests/core/test_diplomacy.py::test_treaty_needs_both_signatures, tests/core/test_diplomacy.py::test_proposal_can_be_refused_withdrawn_or_lapse, tests/core/test_diplomacy.py::test_clause_rules |
| PL-42 | 停戦は戦っている相手と両国の署名で効き、予告のターンがたつと戦争に戻る。講和は戦争を終え、部隊の引き揚げの期限（`withdraw_turns`）・捕虜を返すか・賠償（払う国・通貨・額・期日）を書ける。賠償はゲームが払わせず、払う国が援助の命令で送り、期日までに届かなければ違反として記録する | 決定（引き揚げと捕虜は期限と印を記録するだけ。部隊に効くのは4-2以降） | `diplomacy.withdraw_turns`, `diplomacy.max_reparation_turns` | tests/core/test_diplomacy.py::test_ceasefire_stops_fighting_and_notice_resumes_war, tests/core/test_diplomacy.py::test_peace_ends_the_war_and_sets_withdrawal, tests/core/test_diplomacy.py::test_unpaid_reparations_are_a_violation_that_dents_credit |
| PL-43 | 条約の違反（結んでいる不可侵・同盟を破って宣戦する・賠償を期日までに送らない）は島に記録され、国債の利回りの上乗せ（1件ごとに `violation_premium`、合計は `violation_premium_max` まで）として `violation_decay_turns` ターンかけて消える。銀行が国債を買わなくなることはない | 決定 | `diplomacy.violation_premium`, `diplomacy.violation_premium_max`, `diplomacy.violation_decay_turns` | tests/core/test_diplomacy.py::test_unpaid_reparations_are_a_violation_that_dents_credit |
| PL-44 | 国際会議場は公開の声明の場。島を持つ人が声明を出せる（1つの島が24時間に `HAKONIWA_HALL_PER_DAY` 件まで。管理画面で非表示にできる）。宣戦・停戦・講和・条約の効き始め・破棄の予告・違反は、当事国のターンログから自動で載る（巻き戻しで消える）。誰でも読める。画面・API・MCPから同じ中身。島を持つ人は国際会議場からも、外交の画面と同じ入力欄（`web/treaty_propose_form.html`）と命令の欄で条約を提案できる（命令は同じ `propose_treaty`。声明の欄では条約は結べないと書く） | 決定 | | tests/server/test_diplomacy.py::test_statements, tests/server/test_diplomacy.py::test_war_shows_in_the_hall_and_summaries, tests/server/test_diplomacy.py::test_hall_events_disappear_with_a_rollback, tests/server/test_diplomacy.py::test_the_hall_has_the_treaty_form_for_island_owners_only |
| PL-45 | 経済の取り決め（貿易圏・二国間契約・固定相場・中央銀行の融通・援助・移民の受け入れ）は、条約の条項として結べる（条項の種類 `bloc`・`contract`・`peg`・`swap`・`aid`・`free_movement`・`refugees`）。今ある命令（貿易圏をつくる・契約を申し込む・固定相場を決める・援助を送る・移民の政策を決める）は変えず、条項は両国の署名で効くと、その仕組みに「約束」を置く。戦争のあいだは講和に添える形でしか結べない。どちらの島も、お金の経済が始まっていなければ結べない。契約と援助の条項は何本でも書ける（ほかは 1 つの組に 1 つ） | 決定 | | tests/core/test_diplomacy_economic.py::test_clause_forms_are_checked, tests/core/test_diplomacy_economic.py::test_clauses_wait_for_peace_in_wartime, tests/core/test_diplomacy_economic.py::test_economic_clauses_need_a_started_economy |
| PL-46 | 貿易圏の条項: 提案した国が代表になる圏をつくって相手を入れるか、提案した国が代表の圏（承認制でも）に相手を入れる。署名したどちらかが予告なしに圏を抜けると（抜ける申し込みを出した時点で）、抜けた国の違反。破棄を予告しても、予告のターンがたって条約が終わるまでは約束を守る必要があり、その間に抜けると違反（貿易圏・固定相場・移民の見張りは予告の間も続く。予告のターンがたつターンは見ない） | 決定 | `diplomacy.notice_turns` | tests/core/test_diplomacy_economic.py::test_bloc_clause_creates_a_bloc_and_leaving_is_a_violation, tests/core/test_diplomacy_economic.py::test_bloc_clause_adds_the_partner_to_a_bloc_the_proposer_leads, tests/core/test_diplomacy_economic.py::test_leaving_after_notice_period_is_not_a_violation, tests/core/test_diplomacy_economic.py::test_leaving_during_notice_is_a_violation, tests/core/test_diplomacy_economic.py::test_closing_the_doors_during_notice_is_a_violation |
| PL-47 | 契約の条項: 効くと、売る国・買う国・財・1 ターンの量・値段・通貨・期間・届いた物の行き先で、次のターンから効く契約が始まる（契約の履行は貿易のまま）。期間の途中で解約した国の違反。売る国が品物を、買う国が代金を払えなかったターン（外貨を為替の市場で買えなかったときは、市場の厚みのせいなので違反にしない）は、足りなかった国の違反で、同じ条項では `violation_repeat_turns` ターンは重ねない（船・港・禁輸は約束した国のせいにしない）。期間が終わると条項が済む | 決定 | `diplomacy.violation_repeat_turns`, `diplomacy.max_contract_turns` | tests/core/test_diplomacy_economic.py::test_contract_clause_starts_a_contract_and_the_treaty_ends_when_it_does, tests/core/test_diplomacy_economic.py::test_cancelling_a_treaty_contract_is_a_violation_by_the_canceller, tests/core/test_diplomacy_economic.py::test_a_short_delivery_is_a_violation_by_the_one_who_failed_and_not_repeated |
| PL-48 | 固定相場と融通の条項: 固定相場は、固定する国の中央銀行が相手の通貨を決めたレートに固定する（独立した中央銀行は約束できない）。やめる・準備が尽きて外れると、固定した国の違反。融通は、相手の中央銀行も、手元の外貨の余りを貸せる相手（スワップの貸し手）に数える。違反はない | 決定 | | tests/core/test_diplomacy_economic.py::test_peg_clause_pegs_the_currency_and_dropping_it_is_a_violation, tests/core/test_diplomacy_economic.py::test_independent_central_bank_cannot_promise_a_peg, tests/core/test_diplomacy_economic.py::test_swap_clause_makes_the_partner_a_lender |
| PL-49 | 援助の条項: 送る国が、決めた間隔（1〜`max_aid_period_turns` ターン）で 1 回の額を決めた回数（1〜`max_aid_count`）送る約束。送るのは今までどおりお金の援助の命令で、届いた累計が約束に足りないとき（n 回目の期日の次のターンに n 回分の累計で見る）は送る国の違反（1 回落としても、その遅れは次の回から数え直さない）。1 回の援助は、同じ条件の援助の約束が何本かあれば番号の順に、約束の残りまで充てる。1 回の援助は、先に講和の賠償の残りに数え、余りを援助の約束に数える。回数を終えると条項が済む | 決定 | `diplomacy.max_aid_period_turns`, `diplomacy.max_aid_count` | tests/core/test_diplomacy_economic.py::test_aid_clause_counts_payments_and_missing_one_is_a_violation, tests/core/test_diplomacy_economic.py::test_one_aid_payment_counts_for_reparations_first |
| PL-50 | 移民の条項: 人の行き来の自由は、両国が移民の受け入れ（政策）を open に保つ約束、難民の受け入れは、受け入れる国が open か refugees に保つ約束。効くと足りない国の受け入れをその値にする（上限の人数は今のまま）。政策を変えて約束を外すと、変えた国の違反。移民の政策は世界に対して 1 つなので、特定の相手だけに開けることはできない | 決定（現実との違い: 相手を限った受け入れは持たない） | | tests/core/test_diplomacy_economic.py::test_migration_clauses_open_the_doors_and_closing_them_is_a_violation, tests/core/test_diplomacy_economic.py::test_refugee_clause_only_asks_the_host |
| PL-51 | 掲示板は板（雑談・外交・質問・攻略・運営からの告知。管理画面で足せる。告知は管理者だけが書ける）の中にスレッドが並び、スレッドの中に 1 から始まる番号の書き込みが並ぶ。1 つのスレッドは 1000 件で書けなくなる。板ごとのスレッドの数に上限があり、超えると書き込みが一番古いスレッドが過去ログへ落ちる（固定のスレッドは落ちない）。過去ログは読めて検索できるが書けない。sage の書き込みはスレッドを上げない。非表示にした書き込みは番号を残し、本文を出さない（あぼーん） | 決定（W-147） |  | tests/server/test_board.py::test_thread_closes_at_1000, tests/server/test_board.py::test_old_threads_fall_into_the_log, tests/server/test_board.py::test_sage_does_not_bump |
| PL-52 | 書き込みの名前欄は、空なら「名無し」、`名前#秘密` で秘密から作ったトリップ（◆の印）が付く。「国名で書く」を選ぶと自分の国名で書け、国のページへ飛べる。ID は板ごと・日ごとに変わる（同じ日に同じ板で書いた同じアカウントが分かる）。国名で書いた書き込みと管理者として書いた書き込みには ID を付けない（名無しで書いた書き込みと結びつけない）。画面と API にアカウント名は出さず、管理者だけが誰かを確かめられる | 決定（W-147） |  | tests/server/test_board.py::test_threads_posts_are_numbered_and_hide_the_account, tests/server/test_board.py::test_id_is_per_board_and_per_day, tests/server/test_board.py::test_trip_and_name, tests/server/test_board.py::test_country_name_links_to_the_country_page |
| PL-53 | 本文の `>>番号`（`>>3-5` も）は同じスレッドの書き込みへのリンクになり、指を置く（スマホは押す）と元の書き込みが浮き出る。返された書き込みには「返信: >>12」が付き、返された人（自分への返信を除く）には「返信の知らせ」（見出しの未読の数・国の要約の注意）が出て、開くと既読になる。いいねは 1 人 1 つ、自分の書き込みには付けられない | 決定（W-147） |  | tests/server/test_board.py::test_anchors_become_links_and_replies_are_recorded, tests/server/test_board.py::test_notices_count_unread_replies_and_mark_read, tests/server/test_board.py::test_like_toggles_and_not_own |
| PL-54 | NG は人・ID・言葉で決める。人の NG は書き込みを指して付け、誰かは返さずに保存して名前を変えても効く（名無しで書いた書き込みか、その国名で書いた書き込みのどちらか一方にだけ効き、同じ人の名無しと国名は結びつけない）。ID の NG はその日の分だけ効く。NG に当たった書き込みは折りたたんで見せ（API は本文を null にし、`ng=show` で出す）、その人からの返信は未読の数にも「返信:」にも入れず、運営の書き込みは NG にできない。通報は 1 人が 1 つの書き込みに 1 回で、管理画面で書き込みを非表示にして対応済みにできる。ゲームの不具合の報告（UI-45）とは別の窓口 | 決定（W-147） |  | tests/server/test_board.py::test_ng_person_ident_and_word, tests/server/test_board.py::test_report_once_and_admin_can_hide |
| PL-55 | 書き込みの歯止め（いいね・通報にも同じ待ち）: 登録してから `HAKONIWA_BOARD_NEWCOMER_MINUTES` 分は書けない（5ch のどんぐりに当たる）、連投は `HAKONIWA_BOARD_INTERVAL_SECONDS` 秒あける、24 時間に書ける数は `HAKONIWA_BOARD_POSTS_PER_DAY`・立てられるスレッドは `HAKONIWA_BOARD_THREADS_PER_DAY`、同じ文を 24 時間に 2 回は書けない。名前に「運営」は使えない。同じアカウントの書き込みは 1 つずつ受ける（同時に送って歯止めを抜けられない）。本文は 2000 字・40 行まで、題名は 100 字まで。管理者は歯止めの外。画像は入れない（守りの手間が大きい。入れるなら国旗と同じ検査をする） | 決定（W-147） | `HAKONIWA_BOARD_NEWCOMER_MINUTES`・`HAKONIWA_BOARD_INTERVAL_SECONDS`・`HAKONIWA_BOARD_POSTS_PER_DAY`・`HAKONIWA_BOARD_THREADS_PER_DAY` | tests/server/test_board.py::test_limits, tests/server/test_board.py::test_validation |

### 仮の決まり

| 番号 | 何の代わりか | いつ外すか |
| --- | --- | --- |
| PL-21 | 体制の名前に付けた数字の補正（R・動きやすさ・行政力・工期・幸福度の上限）。現実では、体制の違いは誰が決められるか・何を止められるか・情報がどう流れるかから結果が出る（Codex E29。報道の統制・議会・出国の上限・動員は、すでに使える手段の違いとして入れている） | 回ごとに 1 つずつ置き換えた所から（2026-10-18 のカード「少しずつ」。どれをどの回でかはその回の設計で決める。タブ 14 W-60） |
| PL-22 | 経済体制の名前に付けた倍率（重点産業の工期と経験、ぜいたく品の満足、参入の基準）。重点産業に資材と人を先に回す割当、配給の品の少なさ、銀行と投資の判断から結果が出る形の代わり | 同上 |

## 仕組み

### 体制と特色

- 政治体制は2軸（政治の仕組み: 民主主義・権威主義・全体主義、国家元首の形: 君主制・共和制）、経済体制は市場・混合・計画（2026-10-12 決定）。建国で選び（タブ 2）、決めていない島は各軸の初めの選択になる。
- 特色は `regimes.yaml` の `traits` に書き、軸ごとに選んだ体制の特色を `politics.traits` で合わせる。
  - 足し合わせる: 政情の補正 R（`stability_bonus`）、行政力（`admin_bonus`）。
  - 掛け合わせる: 国の工事とインフラの工期（`works_turns`）、重点産業の工期と経験（`priority_build_turns`・`priority_experience`）、ぜいたく品の満足（`luxury_variety`）。
  - 小さい方: 幸福度の上限（`happiness_cap`）。
  - どれかが真なら真: 議会（`parliament`）・労働の動員（`mobilization`）・公定価格（`official_prices`）など。報道の統制（`press_control`）はどれかが偽なら偽。
- 今の特色（数字は `regimes.yaml`）:

| 体制 | 得意 | 苦手 |
| --- | --- | --- |
| 民主主義 | 政情が下がる動きが遅い、R が正 | 大きな命令に議会が要る。報道を統制できない。出国を止められない |
| 権威主義 | 行政力 ＋1。命令で建てる国の工事とインフラの工期が短い | 政情の動きが速い、R が 0。出国は許可制まで |
| 全体主義 | 労働の動員 | 幸福度の上限。政情の動きが最も速い、R が負。出国は禁止まで |
| 君主制 | R が正（君主の権威）。君主の年齢を持つ | — |
| 共和制 | 選挙で支持率が高いと次の数年 R が正。国家元首の名前を変えられる | — |
| 計画経済 | 公定価格・割当・生産目標・配給で動く。重点産業の工期と経験の伸び | ぜいたく品の種類から来る満足が少ない |
| 市場経済 | 民営工場の参入の基準が低い（見込みの利益率が金利に少し足しただけで建つ） | 景気の波（仕組みから自然に出るので特別な数字は入れない） |
| 混合経済 | 基幹産業の国営と政策融資。どの財にも値段の上限と下限 | — |

- 体制の名前に付けた数字の補正は「仮」（上の表）。報道の統制・議会・出国の上限・労働の動員のように、使える手段の違いとして入れるのが本来の形（2026-10-16 の移民のカード「体制で変わる」も手段の違いで、結果に直接の補正は付けない）。補正は、置き換えられるものから回ごとに 1 つずつ仕組みに置き換えます。どれをどの回でかはその回の設計で決め、置き換えるまでは今の数字のまま残します（2026-10-18 のカード「少しずつ」）。崩れを 1 つずつ見つけられるようにするためです。

### 政情

- 政情の段 `politics` は消費と人口・都市の発展（と移民）の後に、島ごとに動く。目標は名前付きの式 `stability`:

```latex
T^{*} = b + w_H\,(H - H_0) - w_U\,(U - U_0)^{+} - w_\pi\,(\pi - \pi_0)^{+} - w_G\,(G - G_0)^{+} + R + E
```

- H は幸福度、U は失業率（%）、π は年の物価上昇率（%）、G は家計の3層の1人あたり所得から出した格差の指数（ジニ係数。層の中はみな同じと見る）。重み w と境（`_free`・`_mid`）は `politics.weights`。失業率の重みは、3-4 で弱めていたのを回4 で設計の値に戻した（タブ 4）。
- R は体制の補正（共和制の選挙の補正を含む）、E は出来事の補正（`politics.events`）: 債務不履行（`default` から `default_turns` ターンかけて0に戻る。タブ 7）、体制を変えた後の混乱期間（`transition`）、首都の政府の建物が暴動で壊れている間（`office`）、出国を止められて出られない人（1年に住民の1% あたり `migration.trapped`、`migration.trapped_max` まで。タブ 2）。報道を統制していれば、その分も足す。
- 政情は「目標との差 × 動きやすさ」だけ動く。動きやすさは体制の `stability_turns_up`・`stability_turns_down` の逆数（既定は `politics.stability_turns`）。初めてのターン（建国と移し替え）は目標から始める（いきなり暴動にしない）。
- 段階（境は `politics.levels`）:

| 政情 | 段階 | 効き目 |
| --- | --- | --- |
| `demo` 以上 | 落ち着いている | なし |
| `riot`〜`demo` | デモ | 行政力 −1。ターンログと知らせ |
| `rebellion`〜`riot` | 暴動 | 行政力が半分。町の近くの施設が止まり、まれに国営の施設が壊れる |
| `rebellion` 未満 | 反乱の前触れ | 行政力が4分の1、暴動の割合と見込みが2倍。反乱と内戦はフェーズ4で、今はターンログで知らせるだけ |

- 行政力は、体制の `admin_bonus` を足してから段階で減らす（どれも最低の値あり）。減るのは次のターンの命令の段から（タブ 1）。

### 暴動と治安

- 止まりうる施設: 町（`politics.riot.urban`）以上の住宅地から `politics.riot.radius` マスの中の、建っていてマスを持つ住宅でない施設。農林水産業（農場・漁港・牧場・製材所）は止めない（回3。島に1つの漁港が止まって食料が0になり、島じゅうが3か月で亡くなる流れが出たため。暴動は町の店・役所・工場で起き、畑や漁船は動き続ける。タブ 5 FM-34）。
- そのうち `politics.riot.halt_share` を乱数で選び、次のターンから `politics.riot.halt_turns` の間止める（理由「暴動」）。国営の施設はまれに（`politics.riot.destroy_chance`）壊れ、建てるときの資材と人手の `politics.riot.repair_share` を国の工事として使って直す（在庫と帳簿はそのまま）。首都の政府の建物が壊れている間は政情の目標が下がる。
- 治安: 予算の項目「治安費」（タブ 6。建国のときは0）で警察署の警官を雇う。警官が住民 × `politics.riot.police_per_person` だけいれば、止まる施設と壊れる見込みを `politics.riot.police_effect` だけ減らす（足りなければ比例）。政情そのものは上げない。警察は国境の見張りにも効く（タブ 2 PO-41）。
- 報道と集会（命令 `set_press`。自由・制限・統制）: 目標に `politics.press.bonus` を足す代わりに、毎ターンその `politics.press.grievance_rate` を「たまった不満」として積み（`politics.press.grievance_max` まで）、ゆるめるか政情が暴動の境を切ったときにまとめて引く。民主主義は自由だけ。

### 議会と選挙

- 議会があるのは民主主義だけ。支持率は「政情 ＋ `politics.support.wage` × 実質賃金の1年の伸び（%）− `politics.support.unemployment` × 失業率の1年の伸び（ポイント）」。
- `politics.election_turns` ターンごとに選挙が自動であり、そのときの支持率が与党の議席の割合になる。政党は作らない。命令「議会の解散」で今の支持率で選び直せる。共和制では選挙が国家元首の選挙も兼ね、新しい元首の名前はプレイヤーが決める（遊ぶ人の物語）。
- 採決: 議会の要る命令（`orders.yaml` の `parliament` が `majority` か `two_thirds`）は、実行する直前の関所（`Registry.add_order_gate`）で採決する。賛成の割合 = 与党の議席の割合 ＋（今の支持率 − 選挙のときの支持率）÷ 100 × `politics.parliament.swing`。過半数は1/2を超えたら、3分の2は2/3以上で通る。否決された命令も行政力を使い、わけをターンログに書く。
- 議会の要る命令: 税率（どれかの税を `politics.parliament.tax_points` ポイントより大きく変えるときだけ）・国有化・民営化・中央銀行の独立の切り替え・関税・禁輸・資本取引の制限・移民の受け入れと出国・公定価格の改定（過半数）、憲法改正と経済体制の変更（3分の2）。どれに要るかは 2026-10-14 の確認で決めた。
- 測りの「中央銀行を独立させた国」の場面は、議会が賛成するまで独立の命令を出し直す（タブ 12）。

### 憲法改正と経済体制の変更

- 命令 `amend_constitution`（政治体制）と `change_economy`（経済体制）。行政力が重く、民主主義では議会の3分の2が要る。同じ体制や知らない体制には変えられない。
- 変えた後は `politics.transition.turns` ターンの混乱期間で、施設の生産性が `politics.transition.productivity` から一様に戻り（タブ 5 の産出の式に掛かる）、政情の目標が `politics.events.transition` 下がる。混乱期間中はもう一度体制を変えられない（2026-10-14 決定）。
- 首都の建て替え: 政治体制を変えると、首都の建物が新しい体制のものに建て替わる。要る資材は中央省庁を建てるときの資材で、国の倉庫にある分を先に使い、足りない分は次のターンの市場で公共事業費で買う。この買いはほかの工事より先で、買えた物は建て替えだけに使う（国の倉庫に入れると、ほかの国の工事に先に使われて建て替えが終わらなかったため）。そろうまで首都の見た目は前の体制の建物（タブ 2 PO-09）。
- 計画経済に移ると、民営の施設と民間銀行は帳簿の純資産を国債で払って国有化する（国有化の命令と同じ仕訳。タブ 5）。市場経済や混合経済に移ると、国営施設はそのまま残り、民営化は命令で行い、民営工場と民間銀行の参入が始まる。
- 民主主義に変えると、出国の政策は体制の上限まで自動でゆるむ。

### 労働の動員

- 全体主義の命令 `mobilize_labor`。失業者を国営施設に割り当て、住民のうち働く人の割合に `politics.mobilization.share` を足す。賃金は最低賃金で、決めていなければ平均賃金の `politics.mobilization.wage`（タブ 4 LB-11）。
- 動員は生産目標を見ずに席の空きまで人を入れるので、目標の4〜5倍作って腐ることがある（Codex の試し遊びのターン 250）。2026-10-15 のカードで「今のまま」とし、計画経済の難しさとして残す。

### 計画経済

- 経済体制の特色 `official_prices` を持つ島（計画経済）だけで効く。板寄せは同じ関数で、値幅を0にして割当ルールを差し替える（タブ 3 MK-34）。
- 公定価格: 財ごとに国が決める値段で、原則は据え置き（ソ連の小売価格のように、決めた値段を長く変えない。2026-10-15 決定）。
  - `planning.review_turns` ターンごとに原価を確かめ、公定価格との差が幅（`planning.review_band`）を超えた財だけ原価に改める。原価は、建国の初めの値段と同じ解き方（原価＋1割）を今の島の平均賃金で解いた値段。島で作れない財は、前の原価に平均賃金の伸びを掛ける。
  - 据え置く財（`planning.held_goods`。既定は穀物・魚・肉・食品）は、原価を下回っても据え置き、売った量 ×（原価 − 公定価格）を補助金にする。独立採算の施設には国庫（国営施設の項目）から払い、足りなければ量に比例して払える分だけで、残りはその施設の赤字。国庫直轄の施設はお金を動かさず帳簿にだけ残す。払いきれていない間は1度だけ知らせる。
  - 命令: 公定価格の改定 `review_official_prices`（名指しした財、空なら上書きしていない財すべてを今の原価に。政治の決断なので行政力が重く、議会のある体制では過半数）、公定価格の決まり `set_official_price_rules`（幅と据え置く財）、財ごとの上書き `set_official_price`（確かめで変えない。0 でやめる）。
  - 市場の段の初めに公定価格を基準の値段にし、島の市場は公定価格で値幅0の板寄せになる。電力も生産の段で同じにする。
- 割当ルール: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事（インフラ・海運業・運送業・国の倉庫）→ 家計（配給）→ 輸出（商社と二国間契約の買い）の順に配る。同じ順位の中は量に比例。
- 生産目標: 国営施設ごとに、直近 `planning.demand_turns` ターンの公定価格での需要（買えなかった分も含む。国の工事とインフラの資材は、公共事業費で買えずに注文しなかった分も建設計画の需要として数える。試し遊び1回目: 倉庫の建材が尽きた後、買えなかった分が需要に出ず、建材工場が減産して工事が何ターンも止まった）に `planning.target_markup` を足した量を、同じ財を作る国営施設に設備の大きさ（全力の産出）で割り振る。施設は採算でなく目標に合わせて動く（操業目標の式の代わり。タブ 3）。過去の需要から決めるので、計画の誤りが品不足と売れ残りとして積み重なる。
- 重点産業: 命令 `set_priority_industries` で産業の分類を `planning.max_priority` まで選ぶ。割当の先頭になり、体制の特色（工期と経験）が効く。
- 配給と行列: 家計が買う財が足りないときは、お金の多さではなく1人あたり同じ量まで配る。買えなかった割合を財ごとの「行列」として出し、幸福度では買えなかった分を足りないものとして数える。使えなかったお金は預金に残り（強制貯蓄）、お金だけが増える様子が統計に出る。
- 貿易の独占: 計画経済の商社は国営で、値段の差ではなく、行列のある財を輸入し、売れ残った財を値段によらず輸出する。輸入は持っている外貨の範囲（タブ 9）。初めて扱う財も値段によらず輸出する（帳簿の値段で見ていたため、公定価格が圏の値段より高い物を輸出できず、ボーキサイトとアルミがどの島でも作られなくなった）。
- 闇市は入れない。後からモジュールで足せるよう、配給で買えなかった量（`scratch["plan_unmet"]`）と余ったお金（`scratch["plan_forced"]`）を段の間に残す。
- 計画経済の島では民営化・民間銀行の許可はできず、施設は倒産しない（タブ 5・7）。

### 混合経済と市場経済の値段の規制

- 混合経済は、どの財にも値段の上限（`set_price_cap`）と下限（`set_price_floor`。農産物の支えなど。0 でなくす）を決められる。市場経済とほかの体制は電気料金の上限だけ。板寄せの値幅の中で上限と下限を守る（タブ 3）。
- 計画の画面は、計画経済の島で「計画」、混合経済の島で「値段の規制」として見出しに出る。市場経済には出さない。

### 移民の政策の体制ごとの上限

- 受け入れ（開く・仕事の空きの分だけ・技術者だけ・難民だけ・閉じる、と1年の上限）と出国（自由・許可制・禁止）は命令で決める。動きと既定（受け入れは閉じる）はタブ 2。どちらも民主主義では議会の過半数が要る。
- 出国は政治の仕組みで上限がある（2026-10-16 のカード「体制で変わる」。`regimes.yaml` の `exit_control`）: 民主主義は自由だけ（現実の憲法と人権の決まり）、権威主義は許可制まで、全体主義は禁止まで（ソ連・東ドイツ・北朝鮮）。結果に直接の補正は付けず、使える手段の違いだけにする。
- 出国を止めた国では、出られない人が政情の目標を下げ、国境の見張り（警察）が足りなければ抜ける人が出る（タブ 2 PO-41）。

### 国どうしのやりとり

- 非公開の公電（内部の名前は letters。2026-10-15 のカードで画面の言葉を「外交」「公電」「件名・発信国・宛先国・返電」に決めた）と、公開の掲示板。画面だけでなく API と試し遊びの道具 hk.py からも読み書きでき、人のプレイヤーも同じように使える（2026-10-14 決定）。
- 掲示板（W-147、2026-10-25 に自前で作り直した。外部の掲示板に置き換える案は取りやめ）は、昔の 2 ちゃんねるの形（板・番号付きの書き込み・`>>番号` の返信・名無しとトリップと日ごとの ID・sage・1000 件で止まる・古いスレッドが過去ログへ落ちる）に、今の掲示板の便利な所（返信の知らせ・いいね・NG・通報・どんぐり相当の歯止め・検索）を足したもの。決まりは PL-51〜PL-55。
  - 板は雑談・外交・質問・攻略・運営からの告知の 5 つ。国際会議場（公式の声明と自動の出来事の 1 本の並び）とは別で、国名で書いた書き込みから国のページへ飛べる。
  - 匿名の守り方: 書き込みには書いた人のアカウントを持つが、画面と API には出さない（管理者だけが見られ、荒らしの対処に使う。利用規約に書く）。ID は板ごと・日ごとに変わるハッシュで、サーバーの秘密鍵で混ぜるので元のアカウントはたどれない。国名で書く書き込みには ID を付けず、名無しの書き込みと結びつかないようにする。
  - 通報（荒らし・不適切な書き込みを管理者へ）とゲームの不具合の報告（UI-45）は別の窓口。画面の案内でも分けて書く。
  - 後から足せるもの: 画像の貼り付け（入れるなら国旗と同じ検査）、板ごとの「国名で書く必須」、専用の閲覧ソフトのための形、書き込みの編集と削除。
- 公電は送るとすぐ届き、読めるのは発信国と宛先国の島の持ち主だけ。自分や持ち主のいない島には送れない。1つの島が24時間に送れる数はサーバーの設定 `HAKONIWA_LETTERS_PER_DAY`。管理画面で非表示にでき、隠した公電は出さない。
- 国際会議場（公開の声明の場。2026-10-20、4-1）。声明は島を持つ人がいつでも出せ、条約と戦争の出来事（宣戦・停戦・講和・条約の効き始め・破棄の予告・違反）は当事国のターンログから自動で載る。宣戦・条約・戦争の状態の決まりは PL-40〜PL-44。
- 外交の決まりは遊ぶ人に任せ、システムの制約にしない（2026-10-12）。ただし経済の取り決め（貿易圏の加入と承認・二国間契約・固定相場・中央銀行の融通・援助・移民の約束）は、フェーズ4の初めに国際会議場と一緒に、両国が署名すると効く条約の条項にする（2026-10-15 決定、4-1 の②で作った。PL-45〜PL-50。違反は国債の不履行と同じく信用の傷、予告どおりの破棄なら罰なし。民主主義では議会の採決が要る）。関税と禁輸は今のまま一方的に出せる。公電が届いたときの知らせはベータ版の前（タブ 14）。
- 完成後の Claude と Codex の対戦は、どちらも管理者の権限を持たず、公電でやりとりする。

### 標準の計画

- 標準の計画（測りと自動の試験だけで使う。タブ 12）は政治の命令を出さない。移民は既定のまま（受け入れは閉じる、出国は自由）、報道は自由、動員はしない。測りの「中央銀行を独立させた国」の場面では、議会が賛成するまで独立の命令を出し直す。

### 統計と画面

- 統計: 政情・目標・段階・支持率・議席、暴動で止まった施設と壊れた施設、警察の人手の足りている割合、計画経済の財ごとの行列と達成率と公定価格、据え置きの補助金。
- 政治の画面と API に、政情と目標の内訳（元にした数つき）・段階・支持率・議席・選挙と採決の履歴・報道の扱いとたまった不満・混乱期間・首都の建て替えの資材を出す。命令のフォームは体制に合わせて出し分ける（使えない命令は出さない）。画面の全体はタブ 11。
- 命令・画面・画面に出す値を変えたら、JSON API・MCP の道具・国の要約と差分も同じ PR で合わせる。

## 境目の表

| 場面 | 誰が・何を | どれくらいの間に | 元に戻る道 | 現実の例 | 設定の名前 |
| --- | --- | --- | --- | --- | --- |
| 足りない | 政情が下がると行政力が減り、命令が遅れる（遅らせる役: 命令の列）。警察が足りないと暴動で止まる施設が増える。配給の品が足りないと行列が伸び、お金は預金に残る（遅らせる役: 強制貯蓄）。補助金を払えないと施設の赤字になる | 政情は体制の動きやすさで数年。行列は毎ターン | 治安費、物価と失業の改善、公定価格の改定 | 1980年代のソ連の行列と強制貯蓄、1989年の東欧の体制の崩れ | `politics.stability_turns`, `politics.riot.police_per_person` |
| 余る | 統制で上げた政情は「たまった不満」として積み、ゆるめるか暴動の境を切るとまとめて引く。動員で作りすぎた物は腐る（難しさとして残す）。売れ残りは計画経済の商社が値段によらず輸出する | 不満は上限まで積もる | 統制をゆるめる時期を選ぶ | 統制をゆるめた後に不満が一度に出る（1980年代末の東欧） | `politics.press.grievance_max` |
| 0 になる | 政情が反乱の境を切ると反乱の前触れ（行政力4分の1、暴動2倍）。今は知らせるだけで、体制は倒れない（フェーズ4） | すぐ | 目標を上げる（失業・物価・格差） | 反乱の前の政情の崩れ | `politics.levels` |
| 一部だけ（層の中の少しの人、島の中の 1 か所） | 暴動は町の近くの施設だけ止め、畑や漁船は動く。配給は1人あたり同じ量なので、お金のある層もない層も同じだけ並ぶ | 数ターン | 警察、町から離れた所に建てる | 都市の暴動と、動き続ける農村 | `politics.riot.radius` |
| 持ち主・相手がいない | 議会のない体制は採決しない。議会のある国は与党が少ないと大きな命令が通らない（否決でも行政力を使う）。公電は持ち主のいない島には送れない | 選挙まで | 解散して選び直す、支持率を上げる | 少数与党の国の法案の否決 | `politics.election_turns` |
| 時間がたつ（満期・寿命・一巡・育ちきった後） | 債務不履行の補正は決めたターンかけて戻る。混乱期間は生産性が戻り切ると終わる。公定価格は据え置いたまま原価とずれ、幅を超えたら改める。選挙は決めたターンごと | 混乱期間は `politics.transition.turns` | 混乱期間が過ぎるのを待つ | 1990年代の東欧の移行期の生産の落ち込み（数年） | `politics.events.default_turns`, `politics.transition.turns`, `planning.review_turns` |

## 前提と依存

### この仕組みが前提にしていること

- 幸福度（タブ 2）、失業率・実質賃金・家計の層の所得（タブ 4）、物価上昇率（タブ 3・11）。
- 治安費と国庫の払う順番（タブ 6）、国有化と民営化の仕訳（タブ 5）、国債の不履行（タブ 7）。
- 命令の列と行政力と関所（タブ 1）、移民の出られない人（タブ 2）。

### この仕組みの結果を前提にしている仕組み

- 行政力（タブ 1）、首都の建物の見た目（タブ 2）、暴動で止まる施設と混乱期間の生産性（タブ 5）。
- 公定価格と値段の規制での板寄せ（タブ 3）、計画経済の商社の貿易の独占（タブ 9）。
- 議会の採決: 税・国有化・中央銀行の独立・関税・禁輸・資本取引・移民（タブ 2・5・6・7・9）。
- 国の工事とインフラの工期（タブ 5・8）、重点産業の経験（タブ 5）。
- かみ合い: 失業率 × 政情 × 行政力（失業が増えると政情が下がり、命令が遅れて失業を直す手も遅れる）。暴動 × 島に1つの食料の施設（農林水産業を止めないことで断った）。配給 × 強制貯蓄 × お金の量（お金だけが増える）。計画経済の輸出 × 市場経済の島の値段（アルミや化学品が大きく動く。タブ 14 W-28）。

## 見直しの記録

| 日付 | 見つけた場面 | 設計に足したこと |
| --- | --- | --- |
| 2026-10-15 | 設計の重みでは 300 ターン目に 50 か国のうち 36 か国がデモ以上になり、暴動で止まった施設が延べ 3,258 | 失業率の重みをバランス調整の段まで弱めた（回4 で設計の値に戻した。PL-04） |
| 2026-10-15 | 計画経済の商社が初めて扱う物を値段で見て、ボーキサイトとアルミがどの島でも作られなくなった | 売れ残りは初めて扱う物でも値段によらず輸出（PL-31） |
| 2026-10-15 | 首都の建て替えの資材を国の倉庫に入れると、ほかの国の工事に先に使われて終わらなかった | 建て替えの資材は先に買い、建て替えだけに使う（PL-19） |
| 2026-10-16 | 島に1つの漁港が暴動で止まり、島じゅうが3か月で亡くなった（回3） | 暴動で農林水産業を止めない（PL-07、タブ 5 FM-34） |
| 2026-10-18 | 設計書の整理で書き直した。境目の表は本文の分だけ埋めた | 3-4 の「詰めたこと」と回3・回4 の政情の分を今の決まりとして書き直し、体制の特色の数字は `regimes.yaml` と設定の名前で書いた。体制の名前に付けた補正を「仮」にした |
