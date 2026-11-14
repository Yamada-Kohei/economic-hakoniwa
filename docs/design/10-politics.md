# 10 政治と体制

政治体制と経済体制の特色、政情とその段階（デモ・暴動・反乱の前触れ）、治安と報道の統制、議会と選挙と支持率、憲法改正と経済体制の変更と混乱期間、首都の建て替え、労働の動員、計画経済（卸と小売の二重の値段・取引税と価格の補助・割当・生産目標・重点産業・配給と行列・貿易の独占）と混合経済の値段の規制、移民の政策の体制ごとの上限、国どうしのやりとり（公電と掲示板）を扱います。建国で体制を選ぶ流れと首都の建物はタブ 2、幸福度と国をまたぐ移民の動きはタブ 2、失業と賃金と格差はタブ 4、暴動で止まる施設の種類と国有化はタブ 5、治安費と国庫の払う順番と産業政策はタブ 6、中央銀行の独立と国債の不履行はタブ 7、計画経済の商社の動きはタブ 9 を見ます。

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
| PL-20 | 計画経済に移ると、民営の施設と民間銀行は帳簿の純資産を国債で払って国有化する。資本家階級は一般の層にまとめ、預金・住宅ローン・飢えの蓄えも一緒に移す。計画経済から移ると、国営施設は残り、民営化は命令で行い、民営工場と民間銀行の参入が始まる（資本家階級は財産と所得で暮らせる人数から始まる。タブ 4 LB-42） | 決定 |  | tests/core/test_phase3_4.py::test_change_to_planned_nationalizes_everything, tests/core/test_phase3_4.py::test_change_from_planned_keeps_state_facilities_and_lets_firms_in, tests/core/test_owners_cap.py::test_change_to_planned_moves_owners_mortgages_and_hunger, ::test_change_from_planned_starts_owners_from_what_they_own |
| PL-21 | 体制の特色: 民主主義は政情が下がる動きが遅く R が正、権威主義は行政力 ＋1 と国の工事の工期が短く政情が速く動く、全体主義は労働の動員と幸福度の上限と政情が最も速く R が負 | 仮 |  | tests/core/test_phase3_4.py::test_stability_moves_toward_the_target_by_the_regime_speed |
| PL-22 | 経済体制の特色: 計画経済はぜいたく品の種類の満足が少ない（重点産業の工期と経験の倍率は 2026-11-01 に外し、割当だけにした）。市場経済は民営工場の参入の基準が低い。混合経済は基幹産業への政策融資（タブ 7 BK-15） | 仮 |  | （未確認） |
| PL-23 | 労働の動員（全体主義の命令）: 失業者を国営施設に最低賃金（なければ平均賃金の決めた割合）で割り当て、住民のうち働く人の割合を足す。生産目標を見ないので作りすぎて腐ることがあり、難しさとして残す | 決定 | `politics.mobilization.share`, `politics.mobilization.wage` | tests/core/test_phase3_4.py::test_mobilization_puts_the_unemployed_to_work_in_state_factories |
| PL-24 | 計画経済の島の財は卸の値段（公定価格）と小売の値段を持つ（PL-60）。島の市場は卸の値段で値幅0の板寄せ（電力も）。卸の値段は原則据え置きで、決めたターンごとに原価（原価＋1割を今の平均賃金で解いた値段）を確かめ、差が幅を超えた財だけ改める（据え置く財も卸は改める） | 決定（2026-11-12 に卸の値段として書き直し、実装した） | `planning.review_turns`, `planning.review_band` | tests/core/test_phase3_4.py::test_planned_market_clears_at_the_official_prices, tests/core/test_phase3_4.py::test_official_prices_stay_unless_the_gap_passes_the_band |
| PL-25 | 据え置く財（既定は食料）は小売の値段を据え置き、卸の値段が改まっても動かさない。卸より安くなった分は家計への売りにだけ価格の補助として埋める（PL-60。前は施設どうしの売りにも補助が付いた） | 決定（2026-11-12 に書き直し、実装した） | `planning.held_goods` | tests/core/test_phase3_4.py::test_held_goods_below_cost_are_subsidized |
| PL-26 | 命令: 公定価格の改定（名指しした財の卸の値段を今の原価に改める。据え置く財を名指しすると小売も改める。議会のある体制は過半数）、公定価格の決まり（幅と据え置く財）、卸の値段の上書きと小売の値段の上書き（どちらも確かめで変えない。小売の上書きは行政力 2 で、据え置く財の小売を上げるときだけ議会のある体制は過半数） | 決定（2026-11-12 に小売の上書きを足し、実装した） | `planning.retail.floor` | tests/core/test_phase3_4.py::test_official_price_reform_and_rules_orders, tests/core/test_phase3_4.py::test_plan_orders_check_and_log, tests/core/test_phase3_4.py::test_retail_price_order |
| PL-27 | 割当ルール: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事 → 家計（配給）→ 輸出の順に配り、同じ順位の中は量に比例 | 決定 |  | tests/core/test_phase3_4.py::test_allocator_serves_priority_then_state_then_households_then_exports |
| PL-28 | 配給: 家計の中はお金の多さでなく1人あたり同じ量まで。買えなかった割合を財ごとの「行列」として出し、使えなかったお金は預金に残る（強制貯蓄） | 決定 |  | tests/core/test_phase3_4.py::test_ration_gives_each_person_the_same_amount, tests/core/test_phase3_4.py::test_half_price_plan_queues_and_saves_but_keeps_producing |
| PL-29 | 生産目標: 国営施設ごとに、直近の公定価格での需要（買えなかった分と、国の工事とインフラの資材のうち公共事業費で買えなかった分も含む）に上乗せした量と、輸出の計画の量（PL-62）を足した量を、同じ財を作る国営施設に設備の大きさで割り振る。施設は採算でなく目標に合わせて動く | 決定 | `planning.demand_turns`, `planning.target_markup` | tests/core/test_phase3_4.py::test_targets_follow_demand_and_capacity |
| PL-30 | 重点産業は命令で産業の分類を決めた数まで選ぶ。割当の先頭になる（施設の投入財。建てている工事は、同じ優先度の工事の中で国の倉庫の資材・注文・公共事業費を先に受け取る）。工期と経験の倍率は付けない（2026-11-01） | 決定 | `planning.max_priority` | tests/core/test_phase3_4.py::test_plan_orders_check_and_log |
| PL-31 | 計画経済の貿易は国営の商社の独占: 行列のある財を輸入し、売れ残った財と輸出の計画の財（PL-62）を値段によらず輸出する。輸入は輸出で得た外貨の範囲（タブ 9） | 決定 |  | tests/core/test_phase3_4.py::test_planned_trade_is_a_state_monopoly, tests/core/test_phase3_4.py::test_planned_traders_export_unsold_goods_whatever_the_price |
| PL-32 | 混合経済はどの財にも値段の上限と下限を決められる。市場経済は電気料金の上限だけ。上限より高い下限・下限より安い上限の命令は断る | 決定 |  | tests/core/test_phase3_4.py::test_price_floor_keeps_the_market_price_up, tests/server/test_phase3_4.py::test_plan_page_for_a_mixed_economy_shows_price_controls |
| PL-33 | 闇市は入れない。後からモジュールで足せるよう、配給で買えなかった量と余ったお金を段の間に残す | 決定（後から） |  | （未確認） |
| PL-34 | 移民の出国の政策は政治の仕組みで上限がある: 民主主義は自由だけ、権威主義は許可制まで、全体主義は禁止まで（`regimes.yaml` の `exit_control`）。受け入れの政策は体制によらない | 決定 |  | tests/core/test_migration.py::test_democracy_cannot_restrict_exit_and_amending_relaxes_it |
| PL-35 | 国どうしのやりとりは、非公開の公電（送るとすぐ届き、発信国と宛先国の持ち主だけが読める）と公開の掲示板（板・番号付きの書き込み。PL-51〜PL-55）。画面・API・MCP・hk.py から同じように使える | 決定 |  | tests/server/test_letters.py::test_send_receive_and_read, tests/server/test_letters.py::test_others_cannot_read |
| PL-36 | 公電は1つの島が24時間に送れる数に上限があり（サーバーの設定）、管理画面で非表示にできる | 決定 |  | tests/server/test_letters.py::test_daily_limit, tests/server/test_letters.py::test_admin_hides_letters |
| PL-37 | 外交の決まりは遊ぶ人に任せる。条約は両国が署名すると効く条項の束で、停戦・講和・不可侵・同盟は4-1の①で作った（予告どおりの破棄は罰なし、破ると違反の記録と信用の傷、ゲームは条項を強いない）。貿易圏・二国間契約・固定相場・融通・援助・移民の約束も同じ条約の条項（PL-45〜PL-50。4-1の②で作った）。関税と禁輸は一方的のまま | 決定（①と②で作った） | `diplomacy.notice_turns`, `diplomacy.violation_premium` | tests/core/test_diplomacy.py::test_denounced_non_aggression_ends_without_penalty, tests/core/test_diplomacy.py::test_declaring_war_on_a_treaty_partner_is_a_violation |
| PL-38 | 反乱と内戦はフェーズ4。今は反乱の前触れをターンログで知らせるだけ | 決定（フェーズ4 で作る） |  |  |
| PL-39 | 政治の画面に政情・目標の内訳・段階・支持率・議席・採決の履歴・混乱期間・建て替えの資材を出す。計画経済は「計画」、混合経済は「値段の規制」の画面を出す | 決定 |  | tests/server/test_phase3_4.py::test_politics_page_and_api_show_the_state, tests/server/test_phase3_4.py::test_plan_page_for_a_planned_economy, tests/server/test_phase3_4.py::test_market_economy_has_no_plan_link |
| PL-40 | 宣戦布告は重い命令（行政力 2、民主主義は議会の過半数、伏せない）。破棄の予告のターンがたった条約を破る宣戦は違反にならない。出すと国際会議場に自動で載り、戦争は次のターンから始まる。国と国の間は平時・布告・戦争・停戦のどれかで、同じ組の戦争は同時に1つ。休止中の島や自分には宣戦できない（MI-02・MI-03）。同じターンに互いに宣戦すると互いの宣戦になり、両国とも宣戦した国になる（MI-03） | 決定 | `diplomacy.proposal_turns` | tests/core/test_diplomacy.py::test_declared_war_starts_next_turn, tests/core/test_diplomacy.py::test_cannot_declare_twice_or_on_self, tests/core/test_diplomacy.py::test_democracy_votes_on_war_and_treaties, tests/core/test_diplomacy.py::test_mutual_declarations_record_both_treaty_violations |
| PL-41 | 条約の提案・署名・断り・取り下げ・予告つきの破棄。提案は返事がないまま決めたターンがたつと流れ、同時に出せる数に上限がある。提案（提案する国の署名にあたる）と署名と破棄は民主主義では議会の過半数。破棄できないときの理由は、講和の賠償が済んでいない・契約の期間か援助の回数が終わっていない・予告つきで破棄できる条項がない、の3つに分けて出す。講和が効くと、その戦争の停戦の条約は終わる（次の戦争の停戦を塞がない）。賠償だけが残る講和は、次の戦争の講和を塞がない。休止中の島には宣戦ができず、条約は停戦と、休止中の島が賠償を払わない講和しか結べない（PL-56）。署名した両国のうち後の署名で、そのターンの外交の段から効く。戦争のあいだは停戦と講和（と講和に添える取り決め）しか結べない（MI-04〜MI-06）。外交の段は、そのターンの署名を先に全部（島の番号の順）、そのほかの申し送り（提案・断り・取り下げ・破棄・宣戦）を後に決める。同じターンに署名と取り下げが重なると署名が勝ち、署名で効いた不可侵・同盟を同じターンの宣戦が破れば違反になる（島の番号で結果が変わらない） | 決定 | `diplomacy.proposal_turns`, `diplomacy.max_proposals`, `diplomacy.min_notice_turns`, `diplomacy.max_notice_turns` | tests/core/test_diplomacy.py::test_treaty_needs_both_signatures, tests/core/test_diplomacy.py::test_proposal_can_be_refused_withdrawn_or_lapse, tests/core/test_diplomacy.py::test_clause_rules, tests/core/test_diplomacy.py::test_signatures_win_over_withdrawals_and_precede_declarations |
| PL-42 | 停戦は戦っている相手と両国の署名で効き、予告のターンがたつと戦争に戻る。講和は戦争を終え、部隊の引き揚げの期限（`withdraw_turns`）・捕虜を返すか・賠償（払う国・通貨・額・期日）を書ける。賠償はゲームが払わせず、払う国が援助の命令で送り、期日までに届かなければ違反として記録する | 決定（引き揚げと捕虜は期限と印を記録するだけ。部隊に効くのは4-2以降） | `diplomacy.withdraw_turns`, `diplomacy.max_reparation_turns` | tests/core/test_diplomacy.py::test_ceasefire_stops_fighting_and_notice_resumes_war, tests/core/test_diplomacy.py::test_peace_ends_the_war_and_sets_withdrawal, tests/core/test_diplomacy.py::test_unpaid_reparations_are_a_violation_that_dents_credit |
| PL-43 | 条約の違反（結んでいる不可侵・同盟を破って宣戦する・賠償を期日までに送らない）は島に記録され、国債の利回りの上乗せ（1件ごとに `violation_premium`、合計は `violation_premium_max` まで）として `violation_decay_turns` ターンかけて消える。銀行が国債を買わなくなることはない。賠償の違反の重みは払わなかった割合（足りない額 ÷ 賠償の額）。違反の記録には条約の題名も残し、条約の記録が外れた後も画面に題名を出す | 決定 | `diplomacy.violation_premium`, `diplomacy.violation_premium_max`, `diplomacy.violation_decay_turns` | tests/core/test_diplomacy.py::test_unpaid_reparations_are_a_violation_that_dents_credit, tests/core/test_diplomacy.py::test_reparation_weight_is_the_unpaid_share_and_keeps_the_title, tests/server/test_diplomacy.py::test_violation_title_survives_treaty_pruning_and_old_records_fall_back |
| PL-44 | 国際会議場は公開の声明の場。島を持つ人が声明を出せる（1つの島が24時間に `HAKONIWA_HALL_PER_DAY` 件まで。管理画面で非表示にできる）。宣戦・停戦・講和・条約の効き始め・破棄の予告・違反は、当事国のターンログから自動で載る（巻き戻しで消える）。誰でも読める。画面・API・MCPから同じ中身。島を持つ人は国際会議場からも、外交の画面と同じ入力欄（`web/treaty_propose_form.html`）と命令の欄で条約を提案できる（命令は同じ `propose_treaty`。声明の欄では条約は結べないと書く）。首都を占領されたときの降伏も載る（MI-36）。管理画面のターンログで、国際会議場の出来事の書いた文（宣戦の理由・条約の題名と本文）を非表示にでき、そのときは「運営が非表示にしました」と出す（宣戦や条約が起きたこと自体は残す）。巻き戻すと、戻した先より後のターンの声明は戻した先のターンの声明にする（消さない） | 決定 | | tests/server/test_diplomacy.py::test_statements, tests/server/test_diplomacy.py::test_war_shows_in_the_hall_and_summaries, tests/server/test_diplomacy.py::test_hall_events_disappear_with_a_rollback, tests/server/test_diplomacy.py::test_the_hall_has_the_treaty_form_for_island_owners_only, tests/server/test_diplomacy.py::test_admin_can_hide_hall_words_and_the_treaty_text, tests/server/test_diplomacy.py::test_rollback_keeps_later_statements_at_the_restored_turn |
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
| PL-56 | 休止中の島とも、停戦と、休止中の島が賠償を払わず捕虜を返す講和は結べる。休止は鎖国ではなく、経済は前の政策のまま回り、結んだ条約と貿易も続くので、戦争中の島を休止にしても戦争を終えられないのは困る。提案が出た時点で、持ち主に代わって自動で署名して効かせ（条約に `caretaker` を残す）、休止中の島のログに `treaty_caretaker` を書く。ほかの条項・宣戦・休止中の島が出す提案・休止中の島が賠償を払う講和・捕虜を返さない講和は結べない。休止中の島への提案は返事待ちの席を使わないので、提案の数の上限に当たらない（持ち主が戻ってから）。休止を解いたときは、結んだ停戦と講和がそのまま続き、解いてからしばらく持ち主の「注意」に代わりに結んだ件数を出し、外交の画面の条約に「休止中に代わりに結んだ」の印を付ける（タブ 11 UI-66、W-155） | 決定（W-96） |  | tests/core/test_diplomacy.py::test_a_caretaker_accepts_a_ceasefire_and_a_plain_peace_for_an_inactive_island, ::test_a_plain_peace_ends_the_war_with_an_inactive_island |
| PL-57 | 計画経済の島の国営施設は人を抱える（W-198）: 操業目標が下がっても、前のターンに働いていた人（動員した人を除く）から辞める人の分（月 `planning.attrition`、端数はためる）だけ減らし、解雇しない。作る量は操業目標までで、余った人は隠れた失業。技術者の席と賃金の式が見る人手の足りなさは目標に要る人数のまま。国庫や年の予算で払えない分の抱えた人は、遅配にせず手放す。人員の枠（PL-59）を超えた分は、抱えずに手放す | 決定 | `planning.attrition` | tests/core/test_recession_round2.py::test_planned_state_facilities_keep_their_workers |
| PL-58 | 管理画面で島を消すと、その島の終わっていない戦争と条約は次のターンの外交の段で終わる（戦争は `ended_reason` が `island_removed`、条約は `end_reason` が `party_removed`。残った国のログに出し、違反にしない）。消した島の部隊は消し、消した島にいたほかの国の部隊はその場で除隊して自国の家計に戻す（引き揚げの期限を過ぎた部隊と同じ。MI-02） | 決定（2026-11-04 W-88） |  | tests/core/test_diplomacy.py::test_removed_island_ends_live_wars_and_treaties_without_a_violation, tests/core/test_military_war.py::test_removed_island_units_disappear_and_visitors_return_to_households, tests/server/test_diplomacy.py::test_removed_country_finishes_war_in_the_api_and_screen |
| PL-59 | 人員計画（2026-11-10。公開の世界の苦情「計画経済なのに人を割り振れない」）: 計画経済の島は命令 `set_staff_plan` で、建て終わった国営施設（物を作る施設・公共サービス・商業・サービス業。人数を荷や預金で決める運送業・港・海運業・商社・銀行は除く）ごとに、雇う人数の枠（上限）を決める。施設は、今の決まりの人数（生産目標に要る人数と抱える人 PL-57）と枠の小さいほうまで雇う。枠を超えている施設は、前のターンに働いていなかった人（抱えられた余りの人と休業中の人）を枠までその月に手放し、働いていた人に食い込む分は月に前のターンの人数（動員した人を除く）の `planning.staff_release` まで手放して、枠に届くまで続ける。手放された人は同じ月の雇用の段で失業者として雇われ直す（配置換えで、解雇の手当はない）。計画経済の島では、枠の有無によらず、賃金の取り置きと失業者を雇う順を優先度の高い順（失業者を雇う順は同じなら賃金の高い順、番号の順。取り置きは同じ優先度の中を国庫の項目の組 → ほかの施設 → 公共事業の順にし、組の中は LB-45 で比例して分ける）にし、公共事業も前のターンの作業員を雇い続けたうえで優先度（維持は `infrastructure.upkeep_priority`、新しい工事は `infrastructure.build_priority`。命令 `set_public_works` に足す項目で変えられる）の位置で雇い、賃金の差での引き抜きは優先度が相手以上の施設だけがする。商業・サービス業は最後のまま。生産目標の割り振りは、枠のある施設ではその月に残す人数で作れる量までにする。枠で人が足りない施設は「人員の枠」で止まったと出し、人手不足と分ける。枠は外すまで残り、撤去・民営化・計画経済をやめたときに消える。暴動などで壊れて直している間は枠をかけない（直す工事を縛らない。直し終わると戻る）。枠を決めない施設の雇いたい人数と抱え込みは今までどおり。命令・画面・JSON API・MCP をそろえる | 決定（2026-11-10。LB-45 と同じ PR で実装） | `planning.staff_release`, `infrastructure.upkeep_priority`, `infrastructure.build_priority` | tests/core/test_staff_plan.py |
| PL-60 | 二重の値段（2026-11-12、W-231。W-246 の (2) をまとめた）: 計画経済の島は施設どうし・国の工事・国の倉庫・輸出が卸の値段、家計の買い物（消費税がかかる物の全部）が小売の値段で払い、消費税はかけない。小売は、据え置く財は据え置き、上書きした財はその値段、ほかは卸 ×（1 ＋ 標準の取引税の率。消費税の率の命令が受け持つ）で、行列や売れ残りでは動かさない。家計が買った量 ×（小売 − 卸）がプラスなら取引税、マイナスなら価格の補助で、島ごとに市場の段の終わりに差し引き、補助が多ければ人件費と運営費の組の取り置きと国庫の残りから払い、払いきれない分は国の未払いにして国債の発行の見込みに入れ、次のターンに先に払う（2026-11-12 の表の 2-1。予算の項目「補助金」には入れない）。新しい国債が売れず引き受けもない間だけ、払えない分を売った施設の赤字にする。家計の注文の比べ・予算・指値は小売で行い、板寄せの指値だけ卸に直す | 決定（2026-11-12。実装した） | `planning.held_goods`, `fiscal.consumption_tax`, `planning.retail.floor`, `fiscal.payment_order`（項目 price_subsidy） | tests/core/test_phase3_4.py::test_turnover_tax_and_price_subsidy_net_out, ::test_held_goods_below_cost_are_subsidized, ::test_unpaid_price_subsidy_is_carried_and_paid_first, ::test_old_planned_islands_move_to_dual_prices, ::test_retail_price_order |
| PL-62 | 輸出の計画（2026-11-13、計画経済の電気不足の案 3）: 国が、外貨を使う順の 1 段の財（タブ 9 FX-76。石炭・燃料・原油と主食）の輸入できた量と行列、国営施設の設備の直しで買えなかった資材（傷む分まで）を、圏の値段で通貨ごとに外貨に直して要る外貨を見積もり、外貨の蓄え（輸入の数か月分）に届くまでの積み増しを足し、売れ残りの輸出の稼ぎを引いた額を稼ぐ輸出の量を、余った設備（達成率で縮める）のある財から外貨の効率（圏の値段から輸入する原料を引いた額 ÷ 原価）の高い順に、圏で売れる見込みまで選んで生産目標に足す。その量の商社の買いと売りの契約の買いは食料も家計より先に配る。遊ぶ人は命令で財ごとに量を決めるか輸出しないと名指しでき、名指ししない財と標準の計画の島は自動。蓄えが目標より少ない間は、3・4 段の輸入に蓄えを使わない（`planning.export.reserve_rank: 3`）。中央銀行の外貨は使わない。名指しした財は行列・値下がり・効率では外さない。商社は計画の分の注文を先に出し、銀行の段は計画の分の代金を次の支払いに見込む。要る量は直近の平均で見て、行列は圏で売りに出た量まで、商社自身の輸出の買いは除く。設備に使う財（木材など）は外貨を使う順によらず要る外貨に数え、蓄えも使える。目標に届かなかった財の余りは作れた量で見て、行列のある財の原料は輸出しない。選ばなかった財は少しずつ減らし、生産目標には商社がもう持つ輸出の荷を引いた量を足す | 決定（2026-11-13、本人の表の 1-2・2-2・3-1 と B。2026-11-14 原料も止めるまま） | `planning.export.draw_turns`, `planning.export.reserve_turns`, `planning.export.reserve_rank`, `trade.target_speed`, `trade.bloc_band` | tests/core/test_export_plan.py |
| PL-63 | 運営からの告知の知らせ（2026-11-15、セビーリャさん「運営からのメッセージ…赤い印を出してほしい、返信があった時みたいに」）: 告知の板（管理者だけが書ける板）の書き込み（スレッドの頭と続き）は全員へのお知らせとし、まだ見ていない数を見出しの「掲示板」に返信と同じ赤い印（「お知らせ N」）で出す。掲示板の一覧の頭にまだ見ていない告知（新しい順に 10 件まで）を並べ、一覧か告知の板を開くと全部を、告知のスレッドを開くとそのスレッドの分を見たことにする。どこまで見たかはアカウントごとに持つ。数えるのは書いた本人のものと非表示を除いた `ANNOUNCE_DAYS`（server/board/services.py）日のうちの告知で、印を持たない人（新しく登録した人とこの仕組みの前からいる人）も同じ。返信の知らせの JSON API と MCP の `board_replies` に `unread_announcements`・`announcements` を、国の要約と差分の `board` に `unread_announcements` を出す。運営がほかの板に書いた書き込みは知らせない | 決定 |  | tests/server/test_site_notices.py::test_announcement_shows_a_red_mark_until_the_board_is_opened, ::test_opening_one_announcement_thread_keeps_the_others_unread, ::test_old_announcements_and_other_boards_do_not_count |

### 仮の決まり

| 番号 | 何の代わりか | いつ外すか |
| --- | --- | --- |
| PL-21 | 体制の名前に付けた数字の補正（R・動きやすさ・行政力・工期・幸福度の上限）。現実では、体制の違いは誰が決められるか・何を止められるか・情報がどう流れるかから結果が出る（Codex E29。報道の統制・議会・出国の上限・動員は、すでに使える手段の違いとして入れている） | 回ごとに 1 つずつ置き換えた所から（2026-10-18 のカード「少しずつ」。どれをどの回でかはその回の設計で決める。タブ 14 W-60） |
| PL-22 | 経済体制の名前に付けた倍率（ぜいたく品の満足、参入の基準）。配給の品の少なさ、銀行と投資の判断から結果が出る形の代わり。重点産業の工期と経験の倍率は、資材を先に回す割当と二重になっていたので 2026-11-01 に外した | 同上 |

## 仕組み

### 体制と特色

- 政治体制は2軸（政治の仕組み: 民主主義・権威主義・全体主義、国家元首の形: 君主制・共和制）、経済体制は市場・混合・計画（2026-10-12 決定）。建国で選び（タブ 2）、決めていない島は各軸の初めの選択になる。
- 特色は `regimes.yaml` の `traits` に書き、軸ごとに選んだ体制の特色を `politics.traits` で合わせる。
  - 足し合わせる: 政情の補正 R（`stability_bonus`）、行政力（`admin_bonus`）。
  - 掛け合わせる: 国の工事とインフラの工期（`works_turns`）、ぜいたく品の満足（`luxury_variety`）。
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
| 計画経済 | 公定価格・割当・生産目標・配給で動く。重点産業に資材を先に回せる | ぜいたく品の種類から来る満足が少ない |
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
- 二重の値段（PL-60。W-231、2026-11-12）: 財ごとに国が決める値段を、卸の値段（公定価格）と小売の値段の 2 つ持つ。施設どうしの売り買いは卸の値段、家計の買い物は小売の値段で、差は取引税か価格の補助として国庫に出入りする。現実の計画経済も、工場の値段（卸）と店の値段（小売）を分け、差を取引税で取り、小売が卸より安い財は国が補助で埋めた。
  - 卸の値段: 施設どうしの売り買い、国の工事とインフラの資材、国の倉庫の買い入れと売り、輸出（商社と二国間契約の買い）が払う値段で、売り手はどの買い手からも卸の値段を受け取る。島の市場は卸の値段で値幅0の板寄せになる（タブ 3 MK-34）。原則は据え置き（ソ連の値段のように、決めた値段を長く変えない。2026-10-15 決定）。
    - `planning.review_turns` ターンごとに原価を確かめ、卸の値段との差が幅（`planning.review_band`）を超えた財だけ原価に改める。据え置く財の卸の値段も同じように改める（据え置くのは小売の値段だけ）。原価は、建国の初めの値段と同じ解き方（原価＋1割）を今の島の平均賃金で解いた値段。島で作れない財は、前の原価に平均賃金の伸びを掛ける。
    - 賃金は国が決め、施設の月に生産性の伸びの分だけ上げる（タブ 4 LB-40）。人手の足りなさや物価では上げないので、改定が賃金を押し上げ、賃金がまた改定を押す追いかけっこにならない。
  - 小売の値段: 家計が買う物（消費税がかかる物の全部: 食料・衣料・ぜいたく品・サービス・小売り・電気。板寄せを通らない直売と屋台も）に家計が払う値段。計画経済の島では消費税をかけず、小売の値段に含める（ソ連に消費税はなく、取引税が小売の値段に入っていた）。小売の値段は財ごとに次の 3 つのどれかで決まる。
    - 据え置く財（`planning.held_goods`。既定は穀物・魚・肉・食品）: 小売の値段を国が決めたまま据え置き、卸の値段が改まっても動かさない。動かすのは公定価格の改定の命令で名指ししたときだけ（下の命令）。
    - 命令で上書きした財（`set_retail_price`）: 上書きした値段のまま。確かめでも変えない。
    - ほかの財: 卸の値段 ×（1 ＋ 標準の取引税の率）。標準の取引税の率は、計画経済の島の消費税の率の命令がそのまま受け持つ（画面では「取引税の標準の率」と出す）。率を変えると次の市場の段から、卸の値段が改まるとその段から、小売も動く。新しく値段が付いた財と、据え置く財から外した財も、この式の値段から始める。
  - 家計の注文: 家計の値段の比べ（食べ物の栄養あたりの値段の比べ、品目の選び方）・予算・指値は、すべて財ごとの小売の値段で行い、板寄せに出すときだけ指値を卸の値段に直す（財ごとの実効の率 ＝ 小売 ÷ 卸 − 1 で、今の消費税の割り方と同じ形。据え置く財はマイナスにもなる）。運賃は今までどおり卸の値段に運賃の率を掛けた額を小売に足して払う。
  - 差（取引税と価格の補助）: 家計が買った量 ×（小売の値段 − 卸の値段）が、プラスなら取引税、マイナスなら価格の補助。売り手が施設でも、海外の商人（商人の値段と卸の差は今までどおり国の貿易の勘定 MERCHANT_GAP が持ち、小売と卸の差だけをここで数える）・国の倉庫（売上は卸で数える。タブ 6 FS-02）・計画経済の商社が輸入した物・直売と屋台でも同じ。施設どうしの売り買い・国の工事・輸出には差は出ない。
    - 払い方: 約定のときは家計の払う小売の額を取り、売り手には卸の額を渡す。プラスの差はそのまま国庫に入れ、マイナスの差（売り手に足りない分）は約定では払わずに数えておき、市場の段の終わりに取引税と差し引いてまとめて払う。家計の電気の差は生産の段で数えておき、同じく市場の段の終わりに差し引く（今の据え置きの補助 pay_subsidies と同じ流れ）。
    - 差し引いて取引税が多ければ、残りは国庫の収入（タブ 6 FS-02）。価格の補助が多ければ、足りない分を国庫から払う。予算の段は、前のターンの差し引き後の不足に `fiscal.reserve_margin` を足した額を、支払いの順番の人件費と運営費の組の中で取り置く（タブ 6 FS-09。売り場での約束なので、年の予算の項目「補助金」には入れない）。卸の値段の確かめで据え置く財の卸が上がったターンは、取り置きより補助が跳ねる。
    - 取り置きと国庫の残りで払いきれない分は、売り手への国の未払いとして残し、国債の段の発行の見込みに入れ、次のターンの価格の補助の取り置きから先に払う（2026-11-12 の表の 2-1。国庫が足りなければ国債で埋める。国債の段は市場の段の後なので、同じターンには埋められない）。新しい国債が売れ残り、中央銀行の引き受けもない間（不履行の後の締め出しなど。タブ 7）だけは、未払いを持ち越さず売った施設の赤字にし（量に比例して払える分だけ払う）、続いている間に 1 度だけ知らせる。
    - 国庫直轄の施設が売った分は、売上も費用も国庫なのでお金を動かさず、取引税と価格の補助を帳簿にだけ残す。
    - 価格の補助が取引税を初めて上回ったターンに 1 度知らせる（次に取引税が上回るまで出さない）。ソ連では食料の補助が1980年代末に国の支出の大きな割合を占め、財政の赤字の元になった。
  - 数え方: GDP の支出側は家計の買い物を卸の値段で数え、取引税と価格の補助は付加価値の外として別に出す（生産側も卸なので、関門の GDP の食い違いは開かない）。物価の指数・実質賃金・家計の食費の見込み（最低賃金の目安など）・行列の重み・強制貯蓄（plan_forced）・店の「小売り」の需要の元（家計が品物に使った額）は、家計が払う小売の値段で数える。計画経済に入るターン・出るターンと前の版の島を移すターンは、物価の指数をつなぎ直す（税の分の段差を物価上昇として数えないため）。体制どうしを比べる道具（`tools/regime_cmp`）も、計画経済の島の実質賃金は小売で数えた値になる。
  - 行列と売れ残りでは小売を動かさない（2026-11-12 本人「あんまり動かさん方がいい」。表の 1-1）。公定価格は原則据え置き（2026-10-15）で、行列と使えないお金（強制貯蓄）がたまるのは計画経済の難しさとして残す。現実のソ連も小売を政治の判断でしか動かさず、行列を長く放っておいた。家計の逃げ道は、後から入れる闇市（PL-33）が受け持つ。
  - 命令: 公定価格の改定 `review_official_prices`（名指しした財、空なら卸の値段を上書きしていない財すべての卸の値段を今の原価に改める。据え置く財を名指しすると、その小売も卸の値段 ×（1 ＋ 標準の率）に改める。小売を上書きした財を名指しすると小売の上書きもやめる。食料の小売の値上げは政治の決断なので行政力が重く、議会のある体制では過半数。ソ連の1962年の肉と牛乳の値上げはノボチェルカスクの暴動を招き、その後1991年の値上げまで肉と牛乳の小売は据え置かれた）、公定価格の決まり `set_official_price_rules`（幅と据え置く財）、卸の値段の上書き `set_official_price`（確かめで変えない。0 でやめる）、小売の値段の上書き `set_retail_price`（確かめで変えない。0 でやめる。卸の値段より安くすると価格の補助になるが、卸の値段 × `planning.retail.floor` より下にはできない。小売の上書きは公定価格の改定と同じ行政力 2 で、据え置く財の小売を上げる上書きだけ議会の採決が要る）。値上げの政情への響きは、物価と実質賃金を通して今の仕組みから出る（特別な数字は入れない）。
  - 標準の計画: 公定価格の改定と小売の上書きは出さない。税の手（タブ 12 GT-37）で消費税の率を上げ下げすると、計画経済の島では標準の取引税の率が動き、据え置く財の小売には効かない。据え置く財の補助は賃金の伸びとともに増え、国債に積もる（放っておかれた国のための自動の手は作らない）。
  - 市場の段の初めに卸の値段を基準の値段にし、島の市場は卸の値段で値幅0の板寄せになる。電力も生産の段で同じにし、家計の電気は小売の値段で払う。売れ残りの値引き（タブ 3 MK-41）は公定価格の財では動かず、計画経済の島では売れ残った物は商社が値段によらず輸出する。
  - 移るとき: 計画経済に移ったターンは、卸の値段を今の原価に、小売の値段を卸の値段 ×（1 ＋ 消費税の率）にする（据え置く財もこの値段から据え置く。移ったときは価格の補助は 0）。前の版の計画経済の島は、小売の値段 ＝ 今の公定価格 ×（1 ＋ 消費税の率）、卸の値段 ＝ 据え置く財は今の原価（前の据え置きの補助を足した額）・ほかの財は今の公定価格、で始める。家計の払う額は、据え置く財の運賃（卸に率を掛けるので）が増える分のほかは変わらず、売り手が家計から受け取る額も変わらない。変わるのは、据え置く財を施設が買う値段（前は補助で安く、これからは原価）。計画経済をやめると小売の値段は消え、消費税と市場の値段に戻る。
  - 現実と違う所: (1) 店の利ざやは小売の値段に含めず、今までどおり別の財「小売り」として家計が店に払う（「小売り」にも小売の値段がかかる）。現実の小売の値段は卸＋取引税＋商業の利ざやだった。(2) 取引税は財ごとの 1 つの率でなく、卸と小売の差として出る（ソ連の主なやり方「差額法」と同じ）。(3) 輸出は卸の値段で、外貨の値段との差の扱い（ソ連の外国貿易の価格差の勘定）は入れない。
- 割当ルール: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事（インフラ・海運業・運送業・国の倉庫）→ 家計（配給）→ 輸出（商社と二国間契約の買い）の順に配る。同じ順位の中は量に比例（輸出の計画の分の商社の買いと売りの契約の買いは、食料も国営施設と同じ順位。下の輸出の計画）。
- 生産目標: 国営施設ごとに、直近 `planning.demand_turns` ターンの公定価格での需要（買えなかった分も含む。国の工事とインフラの資材は、公共事業費で買えずに注文しなかった分も建設計画の需要として数える。試し遊び1回目: 倉庫の建材が尽きた後、買えなかった分が需要に出ず、建材工場が減産して工事が何ターンも止まった）に `planning.target_markup` を足し、輸出の計画の量（下）を足した量を、同じ財を作る国営施設に設備の大きさ（全力の産出）で割り振る。施設は採算でなく目標に合わせて動く（操業目標の式の代わり。タブ 3）。過去の需要から決めるので、計画の誤りが品不足と売れ残りとして積み重なる。
- 人の抱え込み（W-198。W-186 回2）: 国営施設は操業目標が下がっても解雇せず、辞める人（定年・自分から辞める人）の分だけ月 `planning.attrition` ずつ減る。作る量は操業目標までに抑え、余った人は隠れた失業になる（1人あたりの産出が下がる）。賃金は国営施設の予算から払う。国庫や年の予算で払えない月は、抱えた人を遅配にせず手放す（目標に要る人の分だけ今までどおり遅配の仕組みに回す。国が払えなければ抱え込みは続かない）。公定価格の原価は定義の人数のままなので、抱えた分は赤字と補助金になる（ソフトな予算制約）。工場が災害や暴動で止まった月と、商業・運輸・港・銀行（人数を売上や荷で決める施設）は抱えない。民営化や体制の変更で抱え込みが外れると、次のターンから解雇の決まり（タブ 4 LB-43）にかかり、新しい体制の段の割合で月ごとに減る（一度には減らない）。現実のソ連・東欧の国営企業は解雇せず人を抱え込み、失業は統計に出ないが生産性が低かった。
- 人員計画（PL-59。2026-11-10 のカード「人員の枠を命令する」）: 国が人を動かす手段は、施設ごとの人数の枠（上限）と、空いた人を雇う順番（優先度）の 2 つ。枠が「どこから人を出すか」を、優先度が「どこへ入れるか」を決める。働いている人を、枠のない施設から国が自動で動かすことはしない。
  - 命令 `set_staff_plan`: 施設と枠の表。1 つの命令に何施設分でも並べられ、要る行政力は命令 1 つ分。（2026-11-10 のカード「まとめて 1 つの命令で」。現実の計画経済も年の労働計画を 1 本で出した）枠を空にするとその施設の枠を外す。マスのある施設は座標で、マスを持たない施設（商業・サービス業・運送業・銀行など）は種類で指す。決められるのは、建て終わった国営の、物を作る施設・公共サービス（役所・学校・大学・警察。絞ったときの効き目は今の仕組みから出る: 役所は働く人の数が行政力に、学校と大学は稼働率が教育に、警察は働く人の数が治安と島の守りに効く。軍の施設（兵営・港の基地・レーダー・ミサイル基地・防空・要塞）は働く人の数が効き目に出ないので外す）・商業・サービス業。人数を荷や預金で決める施設（運送業・港・海運業・商社・銀行。人の抱え込み PL-57 と同じ並び）は対象にしない（人数を絞っても荷が減らず、運賃の割り増しだけが残るため）。建設中の工事と公共事業には枠を付けない（資材と公共事業費で決まる）。
  - 枠の効き方: 枠は、一般の労働者・技術者・動員した人を合わせた人数の上限。施設は「今の決まりの人数（生産目標に要る人数。抱える施設は抱える人を足した数）」と枠の小さいほうまで雇う。前のターンの人数（動員した人を除く。抱え込みの数え方と同じ）が枠より多い施設は、まず前のターンに働いていなかった人（前のターンの稼働率で要った人数を超える人。生産調整で抱えられた余りの人と、電気・送電網・原料が足りずに休業中の人）を、枠までその月に手放す。それでも枠より多ければ、働いていた人をその月に前のターンの人数の `planning.staff_release`（端数は切り上げ）まで減らし、枠に届くまで毎月続ける。働いていない人をすぐ出せるのは、作る量を落とさずに済むため（公開の世界の例: 稼働 0% の機械工場が 16,166 人、送電網につながらない鉱山が 2,568 人を抱えた横で、公共事業は 9 ターン 0 人だった）。遠いマスの人から手放す（雇い続けるときの減らし方と同じ）。技術者の席・賃金の遅配で残る人・人を呼ぶ量（タブ 2 の人の移り）は、枠で切った後の人数で数える。技術者の席を割り当てる順は今のまま（技術者の賃金の高い順、同じなら優先度）で、枠で席を失った技術者は、余った技術者としてほかの施設の一般の席に就く（タブ 4 の雇い方の 4）。
  - 手放された人: 同じ月の雇用の段で失業者に戻り、下の順番でほかの施設と工事に雇われる。国の命令による配置換えなので解雇の手当はない。どこにも雇われなければ失業手当を受ける（商業・サービス業に枠の空きがあれば、最後にそこが雇う）。村のマスに住む人は、失業者でなく自給の農業に戻る（タブ 2）。
  - 雇う順番（枠を決めていない国も含め、計画経済の島の全部で効く）: 失業者を雇う順を賃金の高い順でなく優先度の高い順にする（同じ優先度なら賃金の高い順、番号の小さい順）。賃金は国が決めた数字で（タブ 4 LB-40）、人を引く力を表さないため。雇いたい人の賃金を国庫と公共事業費から取り置く順も同じ優先度の順にする（取り置く順が賃金の順のままだと、優先度の高い工事が「公共事業費が足りず」で雇えない）。同じ優先度の中の分け方は、体制によらない決まり（タブ 4 LB-45。要る額に比例して同じ割合）による。賃金の差での引き抜き（`labor.poach_gap`）は、引き抜く側の優先度が相手以上のときだけにする（国が低い優先度にした施設が、賃金表の差で優先度の高い施設から人を抜かないように）。商業・サービス業からは今までどおりどの施設も公共事業も引き抜け（タブ 8 IN-04）、商業・サービス業は最後に残った人を雇う。前のターンの人を雇い続ける決まりは変えない。
  - 公共事業の作業員: 土木業者のある島では、業者が受けた注文のうち一番高い工事の優先度の位置で業者が雇い（タブ 8 IN-37）、業者で埋まらない分の直営の作業員が次の位置で雇う。維持は `infrastructure.upkeep_priority`、新しい工事は `infrastructure.build_priority`（資材の買い注文と同じ数字。実装で命令 `set_public_works` に維持と新しい工事の優先度の項目を足し、国ごとに変えられるようにする。資材の順番も一緒に動く）の位置で、島のどこからでも雇う。同じ優先度なら施設が先。公共事業も、前のターンの作業員をその月に要る人数まで先に雇い続ける（今は毎月失業者に戻るので、人手の足りない施設が先に雇って抱え、工事に戻らない）。新しく雇う分は、施設の求人が通勤圏に届かないマスの人から先に雇う（首都に近いマスから取ると、町の施設が人手不足のまま遠い町に失業者が残る）。賃金の取り置きは、体制によらない決まり（タブ 8 IN-04。前のターンに雇いに行けた人数まで）のままで、雇い続ける作業員の分は必ずその中に入れる。人員の枠で空いた人を見込みより多く雇えた月は、その分をそのとき残っている公共事業費で払う。
  - 生産目標: 同じ財を作る国営施設への割り振り（設備の大きさ）は、枠のある施設ではその月に残す人数（枠に向けて減らしている途中は、その月に手放した後の人数）で作れる量までにし、残りは枠のない施設に回す。最後の枠の数で切ると、操業目標のほうが人より速く下がり、人を残したまま減産してしまう。回す先がなければ、その財は足りなくなって行列が伸び、計画経済の商社が輸入する。
  - 画面と知らせ: 人員計画の画面は、施設ごとに今の人数・生産目標に要る人数・全力の人数・枠を並べた表で、枠を打って 1 回で出す。枠のせいで雇えなかった施設は、止まった理由を「人員の枠」と出す（人手不足・賃金が払えないと分ける）。枠で人が足りない施設の一覧を国の要約に出し続ける。公共事業の列があるのに作業員が 0 人の月が続いたら、「人員計画で人を空けられます」と知らせる。施設のパネルの優先度の説明は体制で出し分ける（計画経済は「値の大きい施設から、空いた人・資材・電力を配る。今いる人を出すには人員計画」、ほかの体制は「同じ賃金なら値の大きい施設から雇う。資材と電力は値幅の端で足りないときに値の大きい順」）。
  - 労働の動員との重なり: 動員で入れる人も枠まで。動員が生産目標を見ずに作りすぎる施設は、枠で止められる。
  - 枠を決めない国: 雇いたい人数と抱え込みは今までどおり（生産目標の分を雇い、余った人は抱える）。標準の計画と休止中の島は人員計画を出さない。ただし上の雇う順番と公共事業の作業員の決まりは枠がなくても効くので、計画経済の島の数字は全部動く。実装の PR は X870 の full の比べと、引き継ぎの 20 島の再現（公共事業の作業員が 0 人のままの島の数）で確かめる。
  - 枠が消えるとき: 施設の撤去、民営化、計画経済をやめたとき。その後の人の減り方は、人の抱え込みが外れるときの決まりと解雇の決まり（タブ 4 LB-43）による。
  - 現実との対応: 企業ごとの定員と賃金の原資の枠（国が年の労働計画で決めた）、定員を減らした人の配置換え、重点の建設への組織的な募集。優先度の低い所（店・消費財）が人手不足で行列になるのは、重工業と建設を先にした現実と同じ向き。
  - 現実と違う所: (1) 働いていない人をその月に全員動かせるのと、働いている人を月に `planning.staff_release` 動かせるのは、平時の現実より速い（ソ連の工場の入れ替わりは年 15〜20% ほど、定員を減らす予告は 2 か月前）。市場の国の引き抜きの上限（`labor.poach_limit`）と同じ速さにそろえた。(2) 現実の企業は余った人を国から隠したが、ここでは国が全部の人数を見られる。(3) 現実の計画経済に失業の制度はなく、配置先が決まるまで元の職場に籍があった。ここでは手放された月に雇われなければ失業手当に回る。
- 重点産業: 命令 `set_priority_industries` で産業の分類を `planning.max_priority` まで選ぶ。割当の先頭になり、施設は足りない投入財を先に受け取る。建てている工事は、同じ優先度の工事の中で、国の倉庫の資材・注文の量・公共事業費を先に回してもらう（前は番号の順で、先の工事が使い切ると重点産業の工事は注文も出せず、割当の先頭が効かなかった）。早く建つのは資材がそろうから。工期と経験の伸びに倍率は付けない（2026-11-01。前は工期 0.7 倍・経験 1.2 倍の仮の倍率があり、割当と二重に得をしていた）。
- 配給と行列: 家計が買う財が足りないときは、お金の多さではなく1人あたり同じ量まで配る。買えなかった割合を財ごとの「行列」として出し、幸福度では買えなかった分を足りないものとして数える。使えなかったお金は預金に残り（強制貯蓄）、お金だけが増える様子が統計に出る。
- 貿易の独占: 計画経済の商社は国営で、値段の差ではなく、行列のある財を輸入し、売れ残った財と輸出の計画の財（下）を値段によらず輸出する。輸入は持っている外貨の範囲（タブ 9）。初めて扱う財も値段によらず輸出する（帳簿の値段で見ていたため、公定価格が圏の値段より高い物を輸出できず、ボーキサイトとアルミがどの島でも作られなくなった）。
- 輸出の計画（PL-62。2026-11-13、計画経済の電気不足を調べた後の案 3。調べは共有フォルダの plan-power-20261112/report.md）: 国営の商社が輸入に使えるのは輸出で得た外貨だけ（上の貿易の独占）なのに、輸出は生産目標（国内の需要＋1 割）の売れ残りだけなので、石炭も機械も作れない島は、海外の商人が去った後に燃料と設備の資材を買う外貨がなくなり、発電所が止まっていた（500 ターンの測りで計画経済の島の電気の不足が後半 21〜32%、16 島のうち 5〜7 島が 45〜88% 停電）。そこで、国が要る輸入の額を見積もり、その外貨を稼ぐ輸出の量を生産目標に足す。現実のソ連の国家計画委員会も、外貨の計画（要る輸入と、それを払う輸出）を生産の計画と一緒に立て、輸出向けの納入を工場の計画の一部として割り当てた。
  - 見積もり（市場の段の終わり。生産目標を決めるのと同じとき）: 要る外貨 ＝ ①外貨を使う順の 1 段の財（タブ 9 FX-76。財の定義の `fx_priority` が 1 の財で、石炭・燃料・原油と主食の穀物・魚・食品・肉。主食は 2026-11-13 の本人のカードで 1 段）の、商社が輸入できた量と島の市場の買えなかった量の和 × 圏の値段 ＋ ②国営施設の設備を直すのに島の市場で買えなかった資材（2 段の機械・鋼鉄・建材・電子機器など）× 圏の値段。②は資材ごとに、国営施設の設備が 1 ターンに傷む分（設備の資材 × 傷みの割合 ÷ 12）を上限にする。どちらも直近 `planning.demand_turns` ターンの平均で見る。①に輸入できた量を入れるのは、残った行列だけで見ると、輸入が届いて行列が消えたターンに計画が 0 に戻り、外貨が尽きてまた行列が出る行ったり来たりになるため。②を買えなかった量で見るのは、島で少しでも作っている資材も足りない分は数え、設備の直し残し（発電所だけで外貨 1 億ほど）を一度に見積もらないため。
  - 稼ぐ額 ＝ 要る外貨 ＋（外貨の蓄えの目標 − 商社が持っている外貨）÷ `planning.export.draw_turns` − 売れ残りの輸出で得た外貨（直近の平均）。外貨の蓄えの目標 ＝ 要る外貨 × `planning.export.reserve_turns`（輸入の数か月分。2026-11-13 本人の B「要る分＋外貨の蓄え」）。蓄えが目標より少なければ数ターンかけて積み増し、多ければその分を取り崩すので稼ぐ額が減る。0 より小さければ 0（輸出の計画はなく、今までどおり売れ残りだけを輸出する）。現実の計画経済も「輸入のために輸出する」と考え、要る輸入を先に決めてそれを払う輸出を割り当てたが、ちょうど要る分だけでなく、数か月分の輸入に当たる外貨と金の蓄えを持った（市場経済の国の目安も輸入の 3 か月分）。外貨は通貨ごとに持つので、見積もりも 1 段と設備の資材を輸入する圏の通貨ごとに行う。持っている外貨は島の国営の商社の合計で見て、稼いだ商社と輸入する商社が違えば、国が商社の間で外貨を回す（現実の外国貿易省の下の公団どうしも、外貨は国が配った）。
  - 蓄えの使い方: 蓄えが目標を下回っている間は、外貨を使う順の 3 段・4 段（原料と部品・消費財）の輸入には目標を超える分しか使わず（`planning.export.reserve_rank: 3`）、1 段・2 段（燃料と主食・設備の資材）だけが蓄えを使える。原料を止める弱さとして、戦争の世界（war-s4）で計画経済の島の衣類の工場が綿花を買えずに止まった。4（消費財だけ止める）にすると衣類の工場は戻ったが、蓄えが原料に回って燃料が買えず、輸出の計画で減った停電の半分ほどが戻った（種1 の最後の窓 25.9% → 33.8%）ので、3 のままにした（2026-11-14、本人のカード。衣類の工場の件は後回しの W-276）。蓄えは燃料と主食が止まりかけたときのためのもので、消費財の行列に先に使い切らないため（消費財の行列は大きく、制限がないと積み増した外貨がすぐ消費財に消えて蓄えにならない）。
  - 何を輸出するか: 候補は、国営施設が作っていて余った設備のある財（設備の傷みを除いた全力の産出 × 前のターンの達成率 − 国内の生産目標 ＞ 0。達成率で縮めるのは、電気・人・原料が足りずに目標まで作れない施設の余りを数えないため）のうち、稼ぐ額の通貨の圏で前のターンに値段があった財。島の市場に行列のある財と、国営の商社が輸入している財は候補にしない（1 段・2 段の財でも、島で余っていれば候補になる。主食の余る島は主食を輸出して燃料を買う）。外貨の効率 ＝（圏の値段を為替で自国通貨に直した額 − 作るのに輸入する原料の外貨を自国通貨に直した額）÷ 原価（卸の値段を確かめるときの原価。据え置いた卸の値段でなく）の高い順（同じなら財の名前の順）に、財ごとの量 ＝ 余り・商社が圏で売れると見る量（FX-54。前のターンに計画のあった島どうしは、前のターンの計画の量の比で分ける）・残りの稼ぐ額 ÷ 圏の値段 の一番小さいものを、稼ぐ額に届くまで積む。圏の値段が直近の平均より `trade.bloc_band` 以上下がっている財は候補から外す（売り込みで圏の値段を崩し続けないため）。効率の高い財から選ぶのは、同じ外貨を国内の少ない費用（人・原料・電気）で稼ぐためで、現実のソ連も輸出の効率（外貨 1 単位を稼ぐのに要る国内の費用）を財ごとに数えて輸出の品目を選んだ。国内で要らない財（たとえば国内の需要が 0 で止まっていた鉄鉱山の鉄鉱石）も、効率が高ければ輸出の計画で動き出す（需要の履歴がない財も、輸出の計画の量だけで生産目標を立てる）。
  - 計画の量の動かし方: 財ごとの計画の量は、前のターンの計画の量から `trade.target_speed` の割合だけ新しい量に近づける（商社の狙う量と同じ形）。圏で売れ残って島の市場に戻した荷の量は、次のターンの計画の量から引く（行って戻るだけにしない）。
  - 生産目標: 財ごとの生産目標 ＝ 国内の需要の平均 ×（1 ＋ `planning.target_markup`）＋ 輸出の計画の量。国内の需要の履歴からは、国営の商社が輸出の計画の分として島の市場に出した買いの注文の量を除く（約定しなかった分も含めて注文の量で除く。除かないと、輸出の買いが次のターンの需要に入って目標が毎ターン膨らむ）。売れ残りの輸出の買いと、売りの二国間契約で政府が島の市場で買う量は、今までどおり需要に入る。割り振りと 1 ターンに変える幅（`market.max_target_change`）は今の生産目標と同じなので、輸出の計画が急に大きくなっても施設は少しずつ増産する。
  - 商社: 輸出の計画のある財は、島の売れ残りがなくても輸出の向きにし、狙う量を「輸出の計画の量 ＋ 売れ残りの分け前」にする（圏で売れる量までの頭打ちは FX-54 のまま。タブ 9 FX-77）。島の市場には、計画の分と売れ残りの分を別の注文で出す（配る順が違うため）。扱える重さは、1 段の財の輸入の見込みの分を先に取り置き、残りで輸出の荷を扱う（重くて安い鉄鉱石や石の輸出で枠が埋まり、石炭が入らなくならないように）。圏では今までどおり圏の値段を指値に売り（FX-58）、安売りはしない。計画の分の注文は売れ残りの分より先に出す（市場の段は注文の順に商社のお金を取り置くので、後に回すと売れ残りの輸出の買いにお金を先に取られた。実装の試験で見つけた）。銀行の段の次の支払いの見込みには、直近の島での仕入れの平均と、計画の量を公定価格で買う代金の大きいほうを入れる（計画が増えた月に買うお金が足りなくならないように）。
  - 配る順（足りないとき）: 輸出の計画の分の注文と、売りの二国間契約で政府が島の市場で買う注文は、食料も含めて、ほかの国営施設と国の工事と同じ順位（家計の配給より先）にする（2026-11-13 本人の表の 1-2。契約は外国との約束なので、計画の輸出より後にしない）。売れ残りの輸出は一番後のまま。現実でも輸出向けの納入は国内向けより先とされ、1930 年代のソ連（飢饉の中の穀物の輸出）や 1980 年代のルーマニア（借金を返すための食料の輸出と配給）は、食料も国内より先に輸出した。食料が足りないときは家計の配給が減って飢えに進むことがあり、それは計画経済の難しさとして残す（遊ぶ人は下の命令で食料を輸出しないと決められる。標準の計画の島は命令を出さないので、測りで飢えと崩れが増えないかを見る）。
  - 稼いだ外貨: 今までどおり商社が持ち、外貨を使う順（タブ 9 FX-76。燃料 → 設備の資材 → 原料と部品 → 消費財）で使う。輸出の計画は「どれだけ稼ぐか」だけを決め、使い方は変えない。
  - 価格差: 卸の値段で買って圏の値段で売る差は、今までどおり政府が価格差の補給金で埋める（タブ 9 FX-52）。輸出が増えると補給金も増える（測りの t330 の世界では、卸 1 単位の物が圏で 0.03〜0.15 単位の外貨にしかならず、外貨 1 単位を稼ぐと補給金が自国通貨で十数単位かかる）。効率の高い財から選ぶのは、この補給金を小さくすることでもある。前のターンに補給金を払いきれなかった島は、稼ぐ額を前のターンの輸出の実績より上げない（払い残しが商社の赤字と借り入れになり、利子が積もった前の失敗 W-158 をくり返さないため）。
  - 中央銀行の外貨と二国間契約: 中央銀行が持っている外貨は使わない（中央銀行の外貨を商社の輸入に回す案は先送り）。買いの二国間契約で届く物は島の市場の行列を減らすので、見積もりの分が自然に減る。売りの契約で政府が受け取った外貨は今までどおり政府が為替で売り、商社の持つ外貨に入れない。遊ぶ人は二国間契約で物と物の取り決め（キューバの砂糖とソ連の石油のような）を結べ、その分だけ輸出の計画が小さくなる。
  - 立ち上がり: 停電がひどい島では、余った設備は達成率で縮むので、電気の要らない・少ない財（畑と漁船。食品工場も電気は少ない）が先に候補に残り、その外貨で石炭が入って火力が戻ってから、鉱山の鉄鉱石などが候補に上がる。電気がほぼ 0 で何も作れない島は輸出の計画も動かない（遊ぶ人は二国間契約で抜け出す）。
  - 標準の計画と遊ぶ人: 輸出の計画は命令なしに自動で動く（標準の計画の島はこの自動のまま。2026-11-13 本人の表の 2-2）。遊ぶ人は命令 `set_export_plan`（輸出の計画）で財ごとに「量を決めて輸出する」か「輸出しない」を名指しできる。名指しした量は稼ぐ額・圏で売れる見込みで縮めず、余った設備と商社の扱える重さの分だけ生産目標と商社の狙う量に入り、その外貨を稼ぐ額から引いた残りを、名指ししていない財から自動で選ぶ。「輸出しない」と名指しした財は自動の候補から外す（売れ残りの輸出は今までどおり）。名指しした量が圏で売れなければ、荷は圏で売れ残り、長く売れなければ島に戻る（今の決まり）。名指しした財は、行列・圏の値下がり・外貨の効率では候補から外さない（遊ぶ人の判断を優先する。商社が輸入している財だけは外す）。名指しは外すまで続き、空の表で全部やめる。行政力は重点産業の命令と同じ。計画経済の島だけが出せ、計画経済をやめると消える。ほかに、重点産業（輸出の財を作る施設に原料と電気を先に回す）・人員計画・二国間契約でも動かせる。現実の計画でも、国が輸出の品目と量を工場への割り当てで名指しした。
  - 画面と知らせ: 計画経済の画面に「輸出の計画」を出す: 要る外貨（1 段と設備の内訳、通貨ごと）、持っている外貨、稼ぐ額、財ごとの計画の量・売れた量・外貨の効率。JSON API・MCP の道具・国の要約も同じにする。稼ぐ額があるのに候補がない（余った設備がない・圏に買い手がない）ターンが続いたら、続いている間に 1 度だけ知らせる。
  - 計画経済をやめたとき: 輸出の計画は生産目標と一緒に消える。商社の持ち荷と外貨はそのまま残り、新しい体制の商社として売り買いする（今の決まり）。
  - 現実と違う所: (1) 現実の計画は年ごと（5 か年の計画と年の計画）で、ここは毎ターン直近の平均で見直す（計画の量と目標は少しずつ動く）。(2) 計画経済の商社は為替を使わないので、輸出が増えても為替のレートは動かない。国内の値段と外の値段の差は国庫が持つ（ソ連の対外貿易の価格差の勘定と同じ向き）。(3) 輸出のために新しい鉱山や工場を建てる計画は立てない。余っている設備だけを使う。(4) 外国の買い手と長く取り決めるのは、遊ぶ人の二国間契約に任せる。
- 闇市は入れない。後からモジュールで足せるよう、配給で買えなかった量（`scratch["plan_unmet"]`）と余ったお金（`scratch["plan_forced"]`）を段の間に残す。
  - 入れるときの形（2026-11-01 決定、W-56）: 計画経済の島だけで、配給の後に闇の板寄せを置く。買い手は配給で買えなかった家計、売り手は村の畑の余りと国営の施設・店からの横流し。国は「農民市場として認める・黙認・取り締まる」を選ぶ（台帳 2026-11-01 の PL-33）。
- 計画経済の島では民営化・民間銀行の許可はできず、施設は倒産しない（タブ 5・7）。

### 混合経済と市場経済の値段の規制

- 混合経済は、どの財にも値段の上限（`set_price_cap`）と下限（`set_price_floor`。農産物の支えなど。0 でなくす）を決められる。市場経済とほかの体制は電気料金の上限だけ。板寄せの値幅の中で上限と下限を守る（タブ 3）。上限より高い下限、下限より安い上限の命令は断る（両方あると下限が勝ち、上限が効かなくなるため）。
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
- 同じターンに出た外交の命令は、島の番号の順でなく、まず署名を全部、次にそのほかを決めます（PL-41。2026-11-04 W-89）。提案が立っている間に相手が署名すれば、同じターンに提案した国が取り下げても署名が勝ちます。現実の申し込みと承諾でも、英米の法は承諾を発した時に結ばれるとし（発信主義）、日本の民法も承諾の期間を決めた申し込みはその間取り下げられない（523 条）ので、承諾した側を守る形です。
- 運用の穴（2026-11-04 W-88）: 国際会議場の出来事の文は管理画面で隠せ、巻き戻しで声明のターンがずれず、条約の記録が外れても違反の欄に題名が残り、消した島の戦争と条約は終わります（PL-43・PL-44・PL-58）。
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
| 足りない | 政情が下がると行政力が減り、命令が遅れる（遅らせる役: 命令の列）。警察が足りないと暴動で止まる施設が増える。配給の品が足りないと行列が伸び、お金は預金に残る（遅らせる役: 強制貯蓄）。価格の補助が取引税より多いと国庫から払い、足りなければ国債で埋める（遅らせる役: 国債。PL-60） | 政情は体制の動きやすさで数年。行列は毎ターン | 治安費、物価と失業の改善、公定価格の改定 | 1980年代のソ連の行列と強制貯蓄、1989年の東欧の体制の崩れ | `politics.stability_turns`, `politics.riot.police_per_person` |
| 余る | 統制で上げた政情は「たまった不満」として積み、ゆるめるか暴動の境を切るとまとめて引く。動員で作りすぎた物は腐る（難しさとして残す）。売れ残りは計画経済の商社が値段によらず輸出する | 不満は上限まで積もる | 統制をゆるめる時期を選ぶ | 統制をゆるめた後に不満が一度に出る（1980年代末の東欧） | `politics.press.grievance_max` |
| 0 になる | 政情が反乱の境を切ると反乱の前触れ（行政力4分の1、暴動2倍）。今は知らせるだけで、体制は倒れない（フェーズ4） | すぐ | 目標を上げる（失業・物価・格差） | 反乱の前の政情の崩れ | `politics.levels` |
| 一部だけ（層の中の少しの人、島の中の 1 か所） | 暴動は町の近くの施設だけ止め、畑や漁船は動く。配給は1人あたり同じ量なので、お金のある層もない層も同じだけ並ぶ | 数ターン | 警察、町から離れた所に建てる | 都市の暴動と、動き続ける農村 | `politics.riot.radius` |
| 持ち主・相手がいない | 議会のない体制は採決しない。議会のある国は与党が少ないと大きな命令が通らない（否決でも行政力を使う）。公電は持ち主のいない島には送れない 休止中の島: 持ち主に代わって、停戦と賠償のない講和の提案だけ自動で受ける。ほかの条約と宣戦は結べない。外交の画面の相手の一覧には、戦争中・停戦中の休止中の島も「休止中。停戦と講和だけ」と添えて出す。持ち主のいない島（まだ誰も建国していない番号）は対象にしない（W-155）。 | 選挙まで | 解散して選び直す、支持率を上げる | 少数与党の国の法案の否決 | `politics.election_turns` |
| 時間がたつ（満期・寿命・一巡・育ちきった後） | 債務不履行の補正は決めたターンかけて戻る。混乱期間は生産性が戻り切ると終わる。卸の値段は据え置いたまま原価とずれ、幅を超えたら改める。据え置く財の小売は卸と離れていき、価格の補助が年ごとに増える（PL-60）。選挙は決めたターンごと | 混乱期間は `politics.transition.turns` | 混乱期間が過ぎるのを待つ | 1990年代の東欧の移行期の生産の落ち込み（数年） | `politics.events.default_turns`, `politics.transition.turns`, `planning.review_turns` |
| 足りない（空いた人が要る数に届かない、PL-59） | 優先度の高い施設と工事から埋まり、低い所は人手不足のまま作る量が落ちる。足りない財は行列になり（遅らせる役: 強制貯蓄）、計画経済の商社が輸入する（ずらす役） | 毎ターン | 低い所の枠を下げて人を空ける、優先度を変える、労働の動員、移民の受け入れ | ソ連の重工業と建設の優先と、消費財・サービスの不足 | `infrastructure.upkeep_priority`, `infrastructure.build_priority` |
| 余る（枠で空いた人を誰も雇わない、PL-59） | 失業者になり失業手当を受ける。商業・サービス業に枠の空きがあれば最後にそこが雇う。村のマスの人は自給の農業に戻る | 手放された月から | 枠を戻せばその月に雇い直す。国の工事を並べる | 1990 年代の中国の国営企業の人員整理と再就職 |  |
| 0 になる（枠を 0 にする・大きく下げる、PL-59） | 働いていなかった人はその月に出る（作る量は変わらない）。働いていた人は月に前のターンの人数の決めた割合ずつ細り、動いている施設がその月に止まることはない。生産目標の割り振りもその月に残す人数で切るので、作る量は人と同じ速さで落ちる。食料の施設を絞れば行列から飢えに進む（遊び手の命令の結果として残す。枠で人が足りない施設は要約に出し続ける） | 半分まで約 7 か月、1 割まで約 2 年（年に直すと 7 割ほどまで減らせる。現実の入れ替わりは年 15〜20%） | 枠を外せばその月から雇い直す。足りない財は商社の輸入と配給 | 定員の削減と配置換えに数か月かかる | `planning.staff_release` |
| 一部だけ（通勤圏、PL-59） | 空いた人を雇えるのは、その人の住むマスが通勤圏に入る施設と、島のどこからでも雇う公共事業・運送業だけ。離れた町で空けた人は遠い工場には入らず、公共事業か失業に回る | 人の移りは 1 ターンに仕事のない人の決めた割合 | 人手の足りない施設の通勤圏の町へ移り住む（タブ 2） | 住宅と居住の許可が人の移りを縛った | `labor.commute_radius`, `population.migration_rate` |
| 持ち主・相手がいない（枠を決めない国、PL-59） | 雇いたい人数と抱え込みは今までどおり（PL-57）。雇う順と公共事業の位置は優先度の既定の数字で効く。標準の計画と休止中の島は枠を出さない。公共事業の作業員が 0 人の月が続けば知らせる |  | 人員計画を出す |  |  |
| 時間がたつ（枠を出したまま、PL-59） | 枠は外すまで残る。需要が増えて生産目標が上がっても枠の上は雇わず、行列が伸びる。建て増しで全力の人数が増えても枠は元の数。撤去・民営化・計画経済をやめたときに枠は消える |  | 枠を直す・外す。要約の「枠で人が足りない施設」で気づく | 定員が実情とずれたまま残る計画の硬さ |  |
| 0 になる（国庫と国債が尽きる、PL-60） | 価格の補助が取引税より多く国庫で払いきれない分は、売り手への国の未払いにして国債の段の発行で埋め、次のターンに先に払う（遅らせる役: 未払いと国債）。新しい国債が売れず中央銀行の引き受けもない間だけ、払えない分を売った施設の赤字にし、1 度だけ知らせる。国営施設は倒産しないので作り続ける（ソフトな予算制約）。標準の計画は据え置く財を改定しないので、補助は国債に積もる | 1 ターン遅れ。締め出しの間は続く | 据え置く財の小売を改定の命令で上げる、据え置く財を外す、ほかの財の取引税の率を上げる | ソ連末期の財政の赤字とお金の刷り増し | `planning.held_goods`, `fiscal.consumption_tax` |
| 一部だけ（家計の層・1 つの財、PL-60） | 小売の値段はどの層にも同じで、配給も 1 人あたり同じ量のまま。取引税と価格の補助は財ごとに出て、島の中で差し引く。施設どうしの売りに差は出ない（パン工場は小麦を卸の値段で買い、据え置くのはパンの小売だけ） | 毎ターン |  | ソ連の小売の値段は全国で同じだった |  |
| 持ち主・相手がいない（国庫直轄の売り手・標準の計画、PL-60） | 国庫直轄の施設が売った分は、取引税と価格の補助を帳簿にだけ残す。標準の計画と休止中の島は公定価格の改定と小売の上書きを出さないので、据え置く財の既定と確かめだけで動く（税の手は標準の取引税の率を動かす）。海外の商人・国の倉庫・輸入品・直売と屋台の物も、家計が買えば同じ差が出る | 毎ターン | 遊ぶ人が戻って命令を出す |  |  |
| 移る（計画経済に入る・出る、前の版の島、PL-60） | 入ったターンは小売 ＝ 卸 ×（1 ＋ 消費税の率）で、価格の補助は 0 から。前の版の島は、家計の払う額と売り手の受け取る額が変わらない値段で始め、変わるのは据え置く財を施設が買う値段だけ。出ると小売の値段は消えて消費税に戻る | そのターン |  | 1990年代の東欧の値段の自由化 | `fiscal.consumption_tax` |
| 足りない（稼ぐ額に輸出が届かない、PL-62） | 余った設備（達成率で縮めた分）と圏の買えなかった量の分しか輸出できず、外貨は燃料 → 設備の資材の順に使われ、届かない分は発電所が止まり設備が傷む（今の難しさが残る）。遅らせる役は商社の持つ外貨と国の倉庫の石炭、ずらす役は二国間契約（遊ぶ人） | 毎ターン | 重点産業で輸出の財に電気と原料を回す、二国間契約、発電所の建て替え | 1990 年代の北朝鮮とキューバ（輸出で稼げず燃料と部品が尽きた） | `planning.export.draw_turns` |
| 余る（輸出の荷が圏で売れ残る、PL-62） | 圏の値段より安くは売らない（FX-58）。売れなかった財は商社の売れると見る量（FX-54）が下がるので、次のターンの輸出の計画の量も下がる。圏で長く売れ残った荷は島の市場に戻す（今の決まり）。持っている外貨が蓄えの目標を大きく超えれば稼ぐ額は 0 | 数ターン | 圏を替える、効率の次の財へ移る（自動） | 東ドイツの西への安売りの輸出 |  |
| 0 になる（燃料の行列も設備の傷みもない・余った設備がない、PL-62） | 稼ぐ額か候補が 0 なら輸出の計画はなく、今までどおり売れ残りだけを輸出する。候補がないまま稼ぐ額が残るターンが続いたら 1 度だけ知らせる | そのターン |  |  |  |
| 一部だけ（同じ財を作る施設の 1 つ、島の中の 1 か所、PL-62） | 輸出の計画の量は生産目標に足すだけなので、同じ財を作る国営施設に設備の大きさで割り振られ（人員の枠のある施設は残す人数まで）、1 か所だけが輸出用に動くことはない。電気が足りない施設は目標まで作れず、輸出の計画の財が足りなくなる（配る順は食料も家計より先） | 毎ターン | 重点産業 |  |  |
| 持ち主・相手がいない（港がない・圏に入っていない・商社が止まっている、PL-62） | 輸出先の圏の値段がないか動いている商社がなければ候補がなく、輸出の計画は 0。休止中の島と標準の計画の島も自動で動く。計画経済をやめると輸出の計画は消え、生産目標と一緒になくなる | そのターン | 圏に入る、港を建てる |  |  |
| 時間がたつ（海外の商人が去る・設備が傷み続ける、PL-62） | 商人のいる間は燃料の行列が小さく稼ぐ額も小さい。去ると行列が出て、直近の平均で数ターンかけて稼ぐ額が上がり、生産目標は 1 ターンに変える幅で少しずつ上がる。設備の傷む分は毎ターン同じで、直し残しは見積もりに入れないので、傷んだ設備は輸出の稼ぎの余りと契約でしか戻らない | 数ターン〜1 年 |  | ソ連の 1970 年代の石油の輸出の伸び | `planning.demand_turns`, `market.max_target_change` |
| 0 になる（電気がほぼ 0 で何も作れない、PL-62） | 余った設備を達成率で縮めるので候補が残らず、輸出の計画は動かない。電気の少ない食料などが残っていればそこから稼ぎ、石炭が入って火力が戻ると鉱山などが候補に上がる | 数ターン〜 | 二国間契約で燃料を入れる、水力を建てる | 1990 年代の北朝鮮 |  |
| 足りない（価格差の補給金を国庫が払いきれない、PL-62） | 払える分だけ払い（FX-52）、払いきれなかったターンの次は稼ぐ額を前のターンの輸出の実績より上げない（遅らせる役: 商社の預金、ずらす役: 効率の高い財への入れ替え） | 次のターン | 国債、効率の高い財 | ソ連の輸出の赤字を埋めた予算 |  |
| 足りない（食料の輸出の計画と家計の配給がぶつかる、PL-62） | 輸出の計画の分の食料は家計より先に配るので、食料が足りないターンは家計の配給が減り、行列から飢えに進むことがある（遅らせる役: 家計の飢えの蓄え・国の穀物の備蓄、ずらす役: 命令で食料を輸出しないと名指しする）。輸出の計画は余った設備（達成率で縮める）からしか立たないので、作れているうちは国内の分は減らない | 毎ターン | 命令で食料を外す・量を決める、二国間契約 | 1932〜33 年のソ連の穀物の輸出、1980 年代のルーマニア | |
| 持ち主・相手がいない（輸出先と輸入元の圏の通貨が違う、PL-62） | 外貨は通貨ごとに持ち、計画経済の商社は両替しないので、見積もりは輸入元の通貨ごとに行い、候補はその通貨の圏で売れる財に限る | そのターン | 圏に入る、二国間契約 |  |  |
| 移る（計画経済をやめる、PL-62） | 輸出の計画は生産目標と一緒に消え、商社の持ち荷と外貨はそのまま新しい体制の商社として売り買いする | そのターン |  |  |  |

## 前提と依存

### この仕組みが前提にしていること

- 幸福度（タブ 2）、失業率・実質賃金・家計の層の所得（タブ 4）、物価上昇率（タブ 3・11）。
- 治安費と国庫の払う順番（タブ 6）、国有化と民営化の仕訳（タブ 5）、国債の不履行（タブ 7）。
- 命令の列と行政力と関所（タブ 1）、移民の出られない人（タブ 2）。
- 人員計画（PL-59）: 雇用の段の雇い続ける・失業者を雇う順番・店が最後に雇う仮の決まり（タブ 4 LB-31）、公共事業の作業員の雇い方（タブ 8）、人手の足りない施設へ人が移る決まり（タブ 2）、生産目標の割り振り。店を企業の施設として作り直した後は、店の枠もほかの施設と同じ形になる。

### この仕組みの結果を前提にしている仕組み

- 行政力（タブ 1）、首都の建物の見た目（タブ 2）、暴動で止まる施設と混乱期間の生産性（タブ 5）。
- 公定価格と値段の規制での板寄せ（タブ 3）、計画経済の商社の貿易の独占（タブ 9）。
- 二重の値段（PL-60）: 家計の注文の指値と消費税の家計側（タブ 4 LB-24）、国の収入と支払いの順番（取引税と価格の補助。タブ 6 FS-02・FS-05・FS-09）、物価の指数と実質賃金（小売の値段で数える。タブ 3・11）、国債と不履行（タブ 7）。
- 議会の採決: 税・国有化・中央銀行の独立・関税・禁輸・資本取引・移民（タブ 2・5・6・7・9）。
- 国の工事とインフラの工期（タブ 5・8）。
- かみ合い: 失業率 × 政情 × 行政力（失業が増えると政情が下がり、命令が遅れて失業を直す手も遅れる）。暴動 × 島に1つの食料の施設（農林水産業を止めないことで断った）。配給 × 強制貯蓄 × お金の量（お金だけが増える）。計画経済の輸出 × 市場経済の島の値段（アルミや化学品が大きく動く。タブ 14 W-26 にまとめた W-28）。

## 見直しの記録

| 日付 | 見つけた場面 | 設計に足したこと |
| --- | --- | --- |
| 2026-10-15 | 設計の重みでは 300 ターン目に 50 か国のうち 36 か国がデモ以上になり、暴動で止まった施設が延べ 3,258 | 失業率の重みをバランス調整の段まで弱めた（回4 で設計の値に戻した。PL-04） |
| 2026-10-15 | 計画経済の商社が初めて扱う物を値段で見て、ボーキサイトとアルミがどの島でも作られなくなった | 売れ残りは初めて扱う物でも値段によらず輸出（PL-31） |
| 2026-10-15 | 首都の建て替えの資材を国の倉庫に入れると、ほかの国の工事に先に使われて終わらなかった | 建て替えの資材は先に買い、建て替えだけに使う（PL-19） |
| 2026-10-16 | 島に1つの漁港が暴動で止まり、島じゅうが3か月で亡くなった（回3） | 暴動で農林水産業を止めない（PL-07、タブ 5 FM-34） |
| 2026-10-18 | 設計書の整理で書き直した。境目の表は本文の分だけ埋めた | 3-4 の「詰めたこと」と回3・回4 の政情の分を今の決まりとして書き直し、体制の特色の数字は `regimes.yaml` と設定の名前で書いた。体制の名前に付けた補正を「仮」にした |
| 2026-10-25 | 戦争中の島を休止にすると戦争を終えられず、休止中の島とは新しい条約も結べなかった（W-96。持ち主の問い「鎖国扱い？」への答えは、鎖国ではなく経済は回る） | 停戦と賠償のない講和は持ち主に代わって自動で受ける（PL-56） |
| 2026-11-04 | 後回しの「戦争の穴」のうち外交の分（W-88・W-89） | PL-40・PL-41（同じターンの命令の順）・PL-43（賠償の重みと題名）・PL-44（会議場の文の非表示・巻き戻しの声明）を直し、PL-58（消した島）を足した |
| 2026-11-10 | 公開の世界の計画経済の国（失業 0.1%）で、公共事業の作業員が 8 ターン 0 人。国営施設は人を抱え、撤去で空いた人は店が先に雇い、優先度は同じ賃金で空いた人を雇うときしか効かなかった | 人員計画（PL-59）: 施設ごとの人数の枠と、計画経済では優先度の順に空いた人を雇う決まり。公共事業も優先度の位置で雇う |
| 2026-11-12 | 掲示板 #24 の 3 番（国が値段を決め補助金を付けて物を出す案、W-246 の (2)）と後回しの W-231。据え置く財の補助が施設どうしの売りにも付き、工場の仕入れまで安くなっていた | 二重の値段（PL-60）: 卸と小売を分け、差を取引税と価格の補助にした。小売は行列や売れ残りでは動かさない |
