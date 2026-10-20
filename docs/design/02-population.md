# 2 島と町と人口

世界マップと島（形・地形・気候帯・肥沃度）、建国の流れと建国直後の島、首都と地名、町（住宅）の段階と育ち方、人口（食料と飢え・生まれる人と亡くなる人・島の中の移り住み）、幸福度、汚染と災害、国をまたぐ移民を扱います。家計の層と買い物はタブ 4、移民の受け入れと出国の政策はタブ 10、インフラと教育はタブ 8、行政力はタブ 6、絵の見え方はタブ 11 にあります。

## コードの場所と試験

- 建国と地形: `hakoniwa_core/founding.py`（`auto_terrain`・`found_island`・`place_start`）、`hakoniwa_core/geo.py`、`hakoniwa_core/nature.py`、名前は `hakoniwa_core/naming.py`
- 人口・飢え・幸福度・町: `hakoniwa_core/modules/population/`（`food_need`・`hunger_step`・`birth_rate`・`death_rate`・`happiness`、段 `population` と `housing`）
- 島の中の移り住み: `hakoniwa_core/economy/households.py` の `move_people`。国をまたぐ移民: `hakoniwa_core/modules/migration/`（段 `migration`）
- 汚染と災害: `hakoniwa_core/modules/events/`
- 試験: tests/core/test_founding.py、tests/core/test_migration.py、tests/core/test_rules.py

## つながり

- 前提にしていること: 食料と町の資材が市場に出ること（タブ 3・5）、人手の足りない施設（タブ 4・5）、電力と教育（タブ 8）、警察と移民の政策（タブ 10）、為替（タブ 9）。
- 前提にされていること: 雇用と家計の層（タブ 4）、食料・消費財・電力の需要（タブ 3）、政情（タブ 10）、自然条件の係数（タブ 5・8）、国際収支（タブ 9）、関門の目安（タブ 12）。
- 詳しくは下の「前提と依存」。

## 決まりの一覧

| 番号 | 決まり（一行） | 状態 | 設定の名前 | 試験 |
| --- | --- | --- | --- | --- |
| PO-01 | 世界は区画の格子で、区画の数は島の数（目安 `world.default_islands`）のおよそ `world.cells_per_island` 倍。1 区画に 1 島で、距離は区画の距離 | 決定 | `world.cells_per_island`, `world.default_islands` | tests/server/test_phase3_2.py::test_world_and_founding_cells_show_climate |
| PO-02 | 1 つのアカウントで持てる島は 1 つ。同じ IP からの重複登録はサーバーの設定で止め、IP はハッシュで決めた期間だけ持つ | 決定 | （サーバーの設定） | tests/server/test_phase1.py::test_founding_creates_one_island_per_user, ::test_signup_is_limited_per_ip |
| PO-03 | 島は `island.size` 四方の中に描き、縁の 1 マスは海。陸地は `island.land_min`〜`island.land_max` マスで、同じ国の離れ小島も描ける | 決定 | `island.size`, `island.land_min`, `island.land_max` | tests/core/test_founding.py::test_shape_rules, ::test_islet_can_be_founded |
| PO-04 | 地形（海・浅瀬・平地・森・山）は描いた形と種だけから自動で配り、`founding.terrain_edits` マスまで描き直せる | 決定 | `founding.sea_terrain`, `founding.coast_terrain`, `founding.base_terrain`, `founding.land_terrain`, `founding.terrain_edits` | tests/core/test_founding.py::test_auto_terrain_is_deterministic_and_keeps_shape, ::test_auto_terrain_stays_put_when_land_is_added |
| PO-05 | 鉱業資源は石炭・原油・鉄鉱石・石材・ボーキサイトから `founding.max_resources` まで選ぶ。鉱山と油井は 1 国に持てる数の上限がある（`facilities.yaml` の max_per_island） | 決定 | `founding.max_resources`, `world.sample_resources` | tests/core/test_founding.py::test_plan_problems |
| PO-06 | 気候帯は世界マップの行を `climate.zones` の順に `climate.shares` で分け、島の気候は区画で決まる。倍率は `climate.yaml` にあり、0 の作り方はその島で使えない | 決定 | `climate.zones`, `climate.shares`, `climate.blend_turns` | tests/core/test_phase3_2.py::test_climate_decides_which_recipes_an_island_can_use, ::test_upgrade_gives_old_islands_a_climate_and_blends_it_in |
| PO-07 | 肥沃度は平地のマスに `climate.fertility` の幅でまとまりで配り、農場と牧場の産出に掛ける。汚染のあったターンが 1 年分たまるごとに下がる | 決定 | `climate.fertility`, `climate.fertility_cell`, `climate.fertility_terrain`, `climate.fertility_loss`, `climate.fertility_floor` | tests/core/test_phase3_2.py::test_fertility_is_spread_on_plains_in_patches, ::test_pollution_wears_fertility_down_to_the_floor |
| PO-08 | 首都は建国で選ぶ。候補は中央省庁を建てられる地形で、周りの陸地が多く、海から `founding.capital_margin` 以上離れたマスを優先する | 決定 | `founding.capital_margin` | tests/core/test_founding.py::test_start_island |
| PO-09 | 首都のマスには中央省庁が建ち、政治体制に応じた建物の見た目になる。体制を変えると、建て替えが済むまで前の体制の建物のまま | 決定 | （`regimes.yaml` の capital_building） | tests/server/test_web.py::test_map_api_capital_buildings, tests/core/test_phase3_4.py::test_amendment_starts_a_transition_and_rebuilds_the_capital |
| PO-10 | 政治体制（2 軸）と経済体制（市場・混合・計画）を建国で選び、初めの施設の持ち主を経済体制に合わせる（`regimes.yaml` の owner と state_owned） | 決定 |  | tests/core/test_founding.py::test_owner_follows_economy |
| PO-11 | 国名・首都名・通貨名と記号・国家元首の名前は建国で入れ（長さの上限は `founding.name_max`）、地名の作風を 8 つから選ぶ（既定は `naming.place_style`） | 決定 | `founding.name_max.country`, `founding.name_max.capital`, `founding.name_max.currency`, `founding.name_max.currency_symbol`, `founding.name_max.leader`, `naming.place_style` | tests/core/test_founding.py::test_place_names_follow_style_and_capital |
| PO-12 | 地名は島を `naming.place_block` マス四方の区画に分けて作風で自動で付け、首都の区画は首都名。命令で変えられ行政力を使わない。町には名前を付けない | 決定 | `naming.place_block` | tests/core/test_rules.py::test_rename_place, tests/core/test_orders.py::test_renames_do_not_use_admin_power, tests/core/test_founding.py::test_housing_has_no_name_and_admin_has_no_place |
| PO-13 | 建国直後の島は `start_island.yaml` に従い、首都の町・周りの村・初めの施設・送電線・倉庫の資材を置き、初めの施設には最初のターンに全力で動ける投入財を倉庫から渡す | 決定 |  | tests/core/test_founding.py::test_start_island |
| PO-14 | 初めの食料の施設は、災害で漁港か農場が 1 つ止まっても食べ物が半分を切らないよう、農場を多めに置く（数は `start_island.yaml`） | 仮 |  | tests/core/test_founding.py::test_start_island |
| PO-15 | 建国のお金は、建国の国債 → 家計への給付 → 出資 → 銀行の国債買い、の順の仕訳で作る。額は `founding.money` と、建国の予算・失業手当・教育・道路・技術者の設定で決まる | 決定 | `founding.money.general_deposit`, `founding.money.owner_deposit`, `founding.money.treasury`, `founding.money.operating_turns`, `founding.money.bond`, `fiscal.unemployment_benefit`, `fiscal.budget.construction`, `education.start`, `education.start_school`, `education.start_budget_years`, `education.engineer_share`, `infrastructure.start_road` | tests/core/test_founding.py::test_start_island, tests/core/test_infrastructure.py::test_groundwork_sets_roads_water_school_and_engineers |
| PO-16 | 町（住宅）は自動で建つ施設 `housing.facility` で、段階（村・町・都市・大都市）ごとの住める上限・見た目・育つ資材・上下水道の条件は `settlements.yaml` にある | 決定 | `housing.facility` | tests/core/test_playtest3_fixes.py::test_settlement_grew_is_logged_when_the_town_level_rises |
| PO-17 | 町ができる・育つのは、人手の足りない施設（店が雇いきれなかった集落を含む）の通勤圏で、空いている町に入れる人と近くの仕事のない人では足りないとき | 決定 | `labor.commute_radius` | tests/core/test_rules.py::test_housing_grows_near_vacancies_but_not_on_no_development |
| PO-18 | 育つのは人数が上限の `housing.fill_ratio` を超えた町で、新しくできる数と育つ数は 1 ターンにそれぞれ上限まで。開発を止めたマスと指定したマスには建たない | 決定 | `housing.fill_ratio`, `housing.max_new_per_turn`, `housing.max_upgrades_per_turn` | tests/core/test_rules.py::test_housing_grows_near_vacancies_but_not_on_no_development |
| PO-19 | 育つ資材は一般の層が市場で買い（預金の `household.housing_share` まで）、そろうまで育たない。2 ターン続けて足りなければ知らせ、段階が上がったターンに「発展」を知らせる | 決定 | `household.housing_share` | tests/core/test_rules.py::test_housing_short_is_logged_after_two_turns, tests/core/test_playtest3_fixes.py::test_settlement_grew_is_logged_when_the_town_level_rises |
| PO-20 | 都市・大都市に育つには、そのマスの上下水道が `settlements.yaml` の water 以上要る。足りずに育たない町はマスごとに 1 度知らせる | 決定 |  | tests/core/test_balance_round4.py::test_autoplan_brings_water_to_crowded_towns |
| PO-21 | 村の仕事のない住民は自給の農業で自分の食べる分をまかない、市場で買わない。村のままでいられる人数は、自給できない段階のいちばん小さい人数の手前まで | 決定 |  | tests/core/test_balance_round3.py::test_subsistence_farmers_do_not_starve_when_the_market_has_no_food |
| PO-22 | 仕事のない人は毎ターン `population.migration_rate` の割合で、人手の足りない施設の近くの空いた町（近く、幸福度の高い順）へ移る。町が育つのはこの移り住みから | 決定 | `population.migration_rate` | （未確認） |
| PO-23 | 住民は 1 人 1 ターン `population.food_per_person` の食料を要り、家計の層ごとに市場で買う。買い方と家計の分け方はタブ 4 | 決定 | `population.food_per_person` | tests/core/test_rules.py::test_market_sells_food_to_households |
| PO-24 | 飢えの蓄えは層ごとの不足の月数 S で、S ← S + 不足 − `population.hunger.tolerance` − `population.hunger.recovery` × S。体の余力の分布を S が超えた人から、何か月かに分かれて亡くなる | 決定 | `population.hunger.tolerance`, `population.hunger.recovery`, `population.hunger.reserve`, `population.hunger.reserve_spread`, `population.hunger.reserve_min` | tests/core/test_balance_round3.py::test_hunger_stock_builds_up_slowly_and_recovers, ::test_hunger_deaths_spread_over_months_weakest_first |
| PO-25 | 飢えるのは市場で食料を買う人で、お金のない家計（失業している家計）から。蓄えは市場で買う人と畑で食べる人で分けて持ち、行き来した人の分は人数で混ぜる | 決定 |  | tests/core/test_balance_round4.py::test_people_back_from_their_fields_do_not_carry_the_market_hunger |
| PO-26 | 飢えている間は幸福度が蓄え 1 か月分ごとに下がり、子が生まれにくくなる。飢えで亡くなった人は別に数え、毎ターン統計とターンログに出す | 決定 | `population.happiness.hunger`, `population.happiness.hunger_max` | tests/core/test_balance_round4.py::test_population_grows_by_births_without_jobs_and_hunger_holds_births |
| PO-27 | 飢え始めたら、国の要約と注意に「どの層が・なぜ買えないか（物がないのか、お金がないのか）」と亡くなるまでの危うさを出す | 決定（Codex の直しの束で作る） |  | （未確認） |
| PO-28 | 食べ物が買えないと、村の働き手は前のターンに食べられなかった割合だけ仕事を休んで畑に戻り、市場に食べ物が戻れば `labor.back_to_work` ずつ仕事に戻る | 決定 | `labor.back_to_work`, `population.hunger.tolerance` | tests/core/test_balance_round4.py::test_villagers_go_back_to_their_fields_when_food_cannot_be_bought, ::test_hungry_villagers_feed_themselves_instead_of_working, ::test_food_makers_hire_people_back_from_their_fields |
| PO-29 | 家計の飢えの蓄えが `population.hunger.flee` を超えている間、そのターンに食べられなかった割合の町の人が、空きのある近い村へ移って自給する（村のままでいられる人数まで） | 決定 | `population.hunger.flee` | tests/core/test_balance_round4.py::test_town_people_flee_to_villages_after_months_of_hunger, ::test_town_people_flee_only_as_far_as_the_villages_stay_villages, ::test_jobless_town_households_flee_while_others_still_eat |
| PO-30 | 生まれる割合は式 `birth_rate`（`population.births`）で、学んだ人の割合と層の実質の所得が上がるほど下がる。仕事がなくても増える | 決定 | `population.births.high`, `population.births.low`, `population.births.education`, `population.births.income`, `population.births.income_floor` | tests/core/test_balance_round4.py::test_births_fall_with_education_and_income |
| PO-31 | 亡くなる割合は式 `death_rate`（`population.deaths`）で、食事の質と上下水道で下がり、人が密に住むマスほど上がる。飢えで亡くなる人は別 | 決定 | `population.deaths.base`, `population.deaths.food`, `population.deaths.water`, `population.deaths.crowd` | tests/core/test_balance_round4.py::test_deaths_fall_with_food_and_water_and_crowding_raises_them |
| PO-32 | 生まれたマスに住む所がなければ島の空いた町へ移り、どこにも空きがなければ消えずに詰めて住む（過密）。過密の度合いで亡くなる割合が上がり、生まれる割合と幸福度が下がる。自給の村があふれた分は近い町へ出る。年齢の層は持たない | 決定 | `population.overcrowding.deaths`, `population.overcrowding.births`, `population.happiness.crowding`, `population.happiness.crowding_max` | tests/core/test_round5_village.py::test_births_are_kept_when_every_home_is_full, ::test_overflow_beyond_a_village_goes_to_the_nearest_town, ::test_crowding_lowers_happiness_and_immigration_room |
| PO-33 | 人口の増減は家計の層の人数に比例して割り振り、資本家階級は割合を保つ。層を移る人は 1 人あたりの預金を一緒に持つ（層の決まりはタブ 4） | 決定 |  | （未確認） |
| PO-34 | 幸福度は式 `happiness`（`population.happiness`）で層ごとに出し、人数で平均する。食料・働き口・電力・食事の質・衣料・ぜいたく品・上下水道・教育の項目と、汚染・実質賃金・物価・飢え・遅配の減点 | 決定 | `population.happiness.base`, `population.happiness.food`, `population.happiness.jobs`, `population.happiness.power`, `population.happiness.food_quality`, `population.happiness.clothing`, `population.happiness.luxury`, `population.happiness.water`, `population.happiness.education`, `population.happiness.pollution_per_point`, `population.happiness.real_wage_per_point`, `population.happiness.real_wage_max`, `population.happiness.inflation_threshold`, `population.happiness.inflation_per_point`, `population.happiness.inflation_max`, `population.happiness.arrears` | tests/core/test_phase3_4.py（幸福度の上限）, tests/core/test_playtest3_fixes.py::test_happiness_weight_change_is_announced_once |
| PO-35 | 前のフェーズから移し替えた島は、幸福度の重みをその時の重みから `population.blend_turns` かけて移し、移し始めのターンに 1 度知らせる | 決定 | `population.blend_turns`, `population.happiness_phase1`, `population.happiness_phase2`, `population.happiness_phase3_1` | tests/core/test_playtest3_fixes.py::test_happiness_weight_change_is_announced_once |
| PO-36 | 汚染は施設が稼働率に応じて `facilities.yaml` の pollution の量を radius に広げ、毎ターン計算し直す。幸福度を汚染 1 あたり減らし、農場と漁港の産出と肥沃度を下げる | 決定 | `population.happiness.pollution_per_point`, `infrastructure.pollution_floor`, `climate.fertility_loss` | tests/core/test_phase3_2.py::test_pollution_wears_fertility_down_to_the_floor |
| PO-37 | 災害は 1 島 1 ターンに `disasters.rate` の確率で起き、施設 1 つが種類ごとの `halt_turns` の間止まる。マスごと消える災害はない | 決定 | `disasters.rate`, `disasters.kinds` | tests/core/test_rules.py::test_disaster_halts_a_facility, tests/core/test_playtest3_fixes.py::test_disaster_on_a_facility_without_a_tile_names_no_position, tests/core/test_balance_round6.py::test_a_disaster_does_not_shorten_a_longer_halt |
| PO-38 | 国をまたぐ移民は、暮らしの見込み V（実質賃金 × 仕事の見つかる割合 × 食べられた割合）の対数の差から費用を引いた分に応じて、組の人数 × `migration.response` が移りたいと思う | 決定 | `migration.response`, `migration.cost`, `migration.distance_cost`, `migration.floor`, `migration.ties` | tests/core/test_migration.py::test_people_move_to_an_open_island_with_their_deposits |
| PO-39 | 動く組は仕事のある一般の家計・失業している家計・技術者の家計で、資本家階級は動かない。行き先は差の比で分かれ、どこも受け入れなければ留まる | 決定 |  | tests/core/test_migration.py::test_nobody_moves_while_every_island_is_closed |
| PO-40 | 受け入れは 1 年に住民の上限（既定 `migration.default_cap`）と町の空きまでで、多ければ比例して分ける。受け入れと出国の政策、体制ごとの違いはタブ 10 | 決定 | `migration.default_cap` | tests/core/test_migration.py::test_jobs_mode_admits_only_as_many_as_the_vacancies |
| PO-41 | 出国を止められた人のうち国境（`migration.border` × 警察の充足）をすり抜けた人は出ていき、残りは「出られない人」として政情の目標を下げる | 決定 | `migration.border`, `migration.trapped`, `migration.trapped_max` | tests/core/test_migration.py::test_a_ban_traps_people_and_lowers_stability |
| PO-42 | 移った人は行き先の層と空いた町に入り、預金を元の島の通貨のまま持って行く（資本取引が禁止なら政府のもの）。飢えの蓄えと教育水準は人数で混ぜ、出身の島ごとに記録する | 決定 |  | tests/core/test_migration.py::test_closed_capital_controls_leave_the_deposits_to_the_state |
| PO-43 | 移った人のうち働いている人は賃金の `migration.remittance` を元の島の一般の家計へ送る。同郷のつながりは年に `migration.ties_decay` 薄れ、元の島が良くなれば同じ式で帰る | 決定 | `migration.remittance`, `migration.ties_decay` | tests/core/test_migration.py::test_migrants_send_money_home_and_return_when_home_gets_better, ::test_turns_with_open_borders_keep_the_books |
| PO-44 | 二国の間の移動の自由の協定と、難民を受け入れる約束は、条約の命令として結べるようにする | 決定（フェーズ4 で作る） |  |  |
| PO-45 | 長く操作しない島は自動で消さず休止のまま残し、管理者が管理画面で扱う。ゲームが動かす AI 国家は置かない（外の AI が遊ぶ入り口は別） | 決定 |  | （未確認） |
| PO-46 | 飢饉で村へ逃れた人は、町で食べ物が買え、町に人手の足りない仕事があれば、1 ターンに決めた割合ずつ町へ戻る | 決定 | `population.hunger.return_share` | tests/core/test_balance_round7.py::test_people_who_fled_to_villages_return_when_towns_need_workers |
| PO-47 | 標準の記念碑は 10: モノリス・建国記念碑・平和記念碑・戦いの碑・慰霊碑・元首の像・労働者の像・展望塔・帳簿の碑・検証の碑。それぞれ建てられる条件があり（モノリスだけ条件なし）、効果は観光の魅力だけ。碑文は自動の文に遊ぶ人が 1〜2 行足せる（1 行 40 字まで）。モノリスと自作以外は 1 つの国に 1 つまで | 決定（4-1 ④） | `monuments.founding_turns`・`monuments.peace_turns`・`monuments.cenotaph_dead`・`monuments.ledger_turns`・`monuments.verification_turns` | tests/core/test_monuments.py, tests/server/test_monuments.py |
| PO-48 | 自作の記念碑はモノリスと同じ条件で、絵を遊ぶ人が上げる（国旗と同じ守り）。管理者が隠せる。絵は国のページとマスの欄に出る（島の地図の絵はどの自作の碑も共通の石碑） | 決定（4-1 ④） |  | tests/server/test_monuments.py::test_custom_monument_image_is_png_or_provisional_and_can_be_hidden |

### 仮の決まり

| 番号 | 何の代わりか | いつ外すか |
| --- | --- | --- |
| PO-14 | 飢えの蓄えが入る前の「1 ターンに 1 割が亡くなる崖」への備えとして農場を 3 つにした（本来は国の穀物の蓄えと配給の仕組み。rework-design.md 案7 の「寄せの借り」） | 決めていない。回5 の「標準の計画の飢饉の手」（穀物の蓄え・配給）と一緒に見直す候補 |

## 仕組み

### 世界と島

世界マップは区画の格子で、区画の数は島の数（目安 `world.default_islands`）のおよそ `world.cells_per_island` 倍とします。1 区画に 1 島が入り、島どうしの距離は区画の距離で測ります（移民の費用・貿易の航路に使う）。世界マップには島の形を 1 マス 1 ピクセルで描き、気候帯・貿易圏・港・航路を重ねます（タブ 11）。

島は `island.size` 四方のマスの中に描き、縁の 1 マスは海です。陸地のマス数は `island.land_min`〜`island.land_max` で、陸地はつながっていなくてよく、同じ国の離れ小島も描けます。離れ小島には送電線が海を越えないので、自前の発電所が要ります（タブ 8）。マスは地形・指定（工業団地・オフィス街・開発停止）・インフラの整備度・汚染・住民の数を持ちます（表の形は docs/architecture.md）。

1 つのアカウントで持てる島は 1 つです。同じ IP からの重複登録はサーバーの設定に従って止め、止め方の強さも管理者が決めます。IP はそのまま残さず、決めた期間だけハッシュで持ちます。長く操作しない島は自動で消さず休止のまま残し、放置された国は管理画面で命令の途絶えたターン数を見て管理者が休止・無人にします。枠が足りなければ管理者が島の数を増やします。新しい島の保護は管理者の設定で入れられますが、既定では守らず RP に任せます（フェーズ4）。ゲームが動かす AI 国家は置きません。AI が遊ぶ入り口は AI 用の鍵で、国に「AI」の印が付きます（タブ 11）。

### 建国の流れ

1. アカウントを作る。
2. 世界マップで空いている区画を選ぶ。区画の気候帯を画面に見せる。
3. 島の形を描く。陸地のマス数は上限と下限の間。地形は描いた形と種だけから自動で配り（海・陸地に接する浅瀬・平地を土台に、海から遠いマスから山、まとまりで森。割合は `founding.land_terrain`）、その後 `founding.terrain_edits` マスまで描き直せる。画面で見せた地形と建国後の地形は同じになる。
4. 鉱業資源を `founding.max_resources` まで選ぶ（石炭・原油・鉄鉱石・石材・ボーキサイト）。一括シミュレーションとサンプルの島は `world.sample_resources` の重みで選ぶ。
5. 首都のマスを選ぶ。候補は中央省庁を建てられる地形で、周りの陸地が多く、海から `founding.capital_margin` 以上離れたマス。
6. 政治体制（政治の仕組みと国家元首の形の 2 軸）と経済体制（市場・混合・計画）を選ぶ（体制の中身はタブ 10）。
7. 国名・首都名・通貨名と記号・国家元首の名前を入れ（長さの上限は `founding.name_max`）、地名の作風を選ぶ。
8. `start_island.yaml` の経済体制ごとの中身に従い、首都の町・周りの村・初めの施設・送電線・倉庫の資材を置く。

経済体制は初めの施設の持ち主を決めます（`regimes.yaml` の owner と state_owned。計画経済は国営、市場経済は民営、混合経済は基幹産業だけ国営）。政治体制は首都に建つ建物の見た目と、政情・命令の手続き（タブ 10）に効きます。建国の問題（形・資源・体制・名前・首都）は `hakoniwa_core/founding.py` がまとめて返し、建国の画面と試験の島づくりが同じ関数を使います。

### 気候帯と肥沃度

気候帯は世界マップの行を上から `climate.zones` の順（寒冷・温帯・熱帯）に `climate.shares` の割合で分け、島の気候は建国した区画で決まります。気候帯ごとの産出の倍率と家計の電力の倍率は `climate.yaml` にあり、作り方の factors に climate があるとき、作る物の倍率を生産性に掛けます。倍率が 0 の作り方（寒冷の綿など）は、その気候の島では使えません。寒冷の島は家計の電力の要る量が多くなります。前のフェーズから移し替えた島は気候帯を区画から決め、倍率を `climate.blend_turns` かけて効かせます。亜熱帯と季節は入れません。

肥沃度は、島を作るときに平地のマスへ `climate.fertility` の幅で `climate.fertility_cell` マスほどのまとまりで配り、作り方の factors に fertility がある施設（農場・牧場）の産出に掛けます。汚染のあるマスは、汚染のあったターンが 1 年分たまるごとに `climate.fertility_loss` 下がります（下限 `climate.fertility_floor`）。移し替えた島の肥沃度は 1 のままです。

### 首都と名前

首都は必ずあり、首都のマスには中央省庁（`government_office`）が建ちます。首都の建物の見た目は政治体制で決まり（`regimes.yaml` の capital_building。6 通りの描き分けはタブ 11）、体制を変えると中央省庁を建てるときの資材で建て替え、そろうまでは前の体制の建物のままです（手順はタブ 10）。首都の町は町の段階から始まり、育つほど公務員を雇いやすくなります。行政力（1 ターンの命令の数）は中央省庁の公務員の数で決まります（タブ 6）。

国名・首都名・通貨名と記号・国家元首の名前は建国でプレイヤーが決めます（通貨名は後から通貨改革として変えられる。タブ 7）。地名は島を `naming.place_block` マス四方の区画に分け、建国で選んだ作風（日本風・イギリス風・アメリカ風・ドイツ風・ロシア風・スペイン風・フランス風・イタリア風。材料は `naming.yaml`）で区画ごとに自動で付け、首都のある区画は首都名にします。地名は命令「地名の変更」で付け直せ、名前の変更の命令は行政力を使いません。町（住宅）には名前を付けず、段階の表示名（村・町・都市・大都市）で呼びます。首都の名前はマスを押すと出ます。政府の建物は地名なしの「中央省庁」です。施設の自動の名前の作り方はタブ 5（作り直しはベータ版の前）。

### 建国直後の島と初期値

建国直後の島の中身は `start_island.yaml` にあります。首都のマスに中央省庁、周りに町の段階の住宅、少し離れて村、首都に近い建てられるマスから順に初めの施設（農場・漁港・林業場・消費財工場・火力発電所と、選んだ資源の鉱山や採石場）、倉庫の資材、首都とつながる送電線です。初めの施設には、最初のターンに全力で動ける分の投入財を倉庫から渡しておきます。人口の規模は初版の 10 倍です（首都の町の人数は `start_island.yaml`）。

初めの食料の施設は、漁港と農場 1 つで住民の買う量に足ります。災害で漁港か農場が止まっても、止めていた農場が動き出すまで食べ物が半分を切らないよう、農場を多めに置いています（PO-14。これは飢えの蓄えが入る前の崖への備えで、仮の決まり）。

建国のお金と基盤は、建国と前のフェーズの世界の移し替えで同じ関数が作ります（`hakoniwa_core/economy/start.py`・`groundwork.py`）。初めの残高は仕訳の列として作るので、最初のターンから帳簿の確かめが通ります。順は、政府が建国の国債（期間 `founding.money.bond`）を出して中央銀行が引き受ける → 政府が家計の各層に建国の給付を払う（1 人あたり `founding.money.general_deposit`・`founding.money.owner_deposit`、国庫は 1 人 `founding.money.treasury`）→ 銀行と独立採算の施設に持ち主が出資する（施設の手元資金は `founding.money.operating_turns` ターン分の支払い）→ 銀行が中央銀行から国債を買う、です（仕訳の中身と銀行の初めの値はタブ 7）。失業手当は建国のときから `fiscal.unemployment_benefit` で払い、年の予算は `fiscal.budget` と `education.start_budget_years` です（タブ 6）。生産の基盤は、教育水準 `education.start`、技術者は一般の労働者の `education.engineer_share`（と建っている施設が要る数の多い方）、首都の近くに `education.start_school` を建てた状態で 1 つ、施設と町のマスに道路 `infrastructure.start_road`、首都の近くのオフィス街に銀行と商社です（タブ 8・7・9）。これで建国直後のインフラと教育の係数はほぼ 1 になります。値段は定義データから「原価 + 1 割」で解きます（タブ 3）。

### 町（住宅）の段階と育ち方

町は自動で建つ施設 `housing.facility` で、プレイヤーは建てられません。段階は `settlements.yaml` の村・町・都市・大都市で、段階ごとに住める上限（capacity）・見た目の人数（min_population）・建物の高さ・育つときに使う資材（materials）・育つのに要る上下水道の整備度（water。都市と大都市）・自給の農業ができるか（subsistence。村だけ）を持ちます。段階（住める上限と見た目）が上がるのは育ったときで、「都市に発展しました」はそのターンに出します。

町ができる・育つのは都市の発展の段で、人手の足りない施設（工事中を含む。店が集落ごとに雇いきれなかった人数も含む）の通勤圏（`labor.commute_radius`）で住む所が足りないときです。足りない人数の多い所から見て、通勤圏の空いている町に入れる人と、もう近くに住んでいて仕事のない人で足りれば育てません。足りなければ、通勤圏の中で人数が上限の `housing.fill_ratio` を超えた町を 1 つ上の段階へ育て、育てる町がなければ空いた平地に村を新しく作ります。

人手の足りない所がなくても、島の住む所の上限を超えて住む人（過密）が島の空きより多ければ、家計がいちばん過密なマスから `housing.settle_radius` マスの内の空いた平地に新しい村を開きます（分家と開拓。江戸の新田開発や分家に当たる。施設の通勤圏でなくてよい）。資材は村の materials（木材）で、町が育つときと同じく家計が買います。平地も木材もなければ開けず、過密が続きます。1 ターンに育つ数は `housing.max_upgrades_per_turn`、新しくできる数は人手の足りない所の分と開拓の分を合わせて `housing.max_new_per_turn` までです（人手の足りない所が先）。開発を止めたマスと、工業団地・オフィス街に指定したマスには建ちません。

育つ・できるときの資材は、一般の層が市場で買います（預金の `household.housing_share` まで。住宅への投資として数える）。買えた資材がそろえば次のターンに育ち、足りなければ要る資材を次のターンの市場で買い、2 ターン続けて足りなければ「町が育たない」と資材の名前を知らせます。都市と大都市に育つには、そのマスの上下水道が `settlements.yaml` の water 以上要り、足りずに育たない町はマスごとに 1 度知らせます（整備度が足りたら印を外す）。道路や送電網が人口に追いつかないときの混雑は入れません。人口がゼロになった町の扱いと、町の中に入る店（回7）はタブ 4 と 5 にあります。

### 村の自給と島の中の移り住み

村（`settlements.yaml` で subsistence の段階）の仕事のない住民は、自給の農業で自分の食べる分をまかない、市場で食料を買いません。失業率にも数えません。村のままでいられる人数は、自給できない段階のうちいちばん小さい人数の手前までです。町以上の集落の人は市場で買って暮らします。都市化で村の自給農業が細るのはこの仕組みから出ます。

人は毎ターン少しずつ島の中を移ります。仕事のない人のうち `population.migration_rate` の割合が、人手の足りない施設の通勤圏にある空いた町へ移ります。行き先は、人手の足りない所に近い町から、同じなら幸福度の高い町です。住む所の上限を超えた人（生まれたマスに空きがなかった人も）は、空いている町へ入れ、入れなければ消えずに元のマスに詰めて住みます（過密）。ただし自給の村が村のままでいられる人数を超える分は、いちばん近い町（人数でも町と見られる住宅地。なければ建った段階が町の住宅地）へ出ます（土地の足りない農村からあふれた人が町へ出る。村が人数で町と見られて村じゅうが一度に自給をやめないように）。住む所のないマスの人はいちばん近い住宅地へ移り、島に住宅地が 1 つもなければ国を出ます。町が育つのは、この移り住みで町の人数が上限に近づくからです。

### 食料と飢えの蓄え

住民は 1 人 1 ターンに `population.food_per_person` の食料（栄養）を要り、家計の層ごとに前のターンの値段と売りに出ていた量を見て市場で買います（買い方・食べ物の質・買い上がりはタブ 3 と 4）。食べられた割合が幸福度と飢えの蓄えに効き、食事の質が幸福度と亡くなる割合に効きます。

飢えは体の蓄えで数えます。家計の層（仕事のある一般の家計・失業している家計・技術者・資本家階級）ごとに「栄養の不足が何か月分たまったか」S を持ち、毎ターン S ← S + 足りなかった割合 − `population.hunger.tolerance` − `population.hunger.recovery` × S（0 未満は 0）です。体は少しの不足なら代謝を落として耐え（tolerance）、食べられれば少しずつ戻ります（recovery）。体の余力（何か月分の不足に耐えられるか）は人によって違い、平均 `population.hunger.reserve`・ばらつき `population.hunger.reserve_spread` の正規分布（下限 `population.hunger.reserve_min`）で、S が自分の余力を超えた人が亡くなります。同じだけ食べられない層でも、弱った人から先に、何か月かに分かれて亡くなります（式 `hunger_deaths`）。

飢えうるのは市場で食料を買って食べている人です。村で自給している人は、一般の層でも市場の食料が足りないことでは飢えません。一般の層の蓄えは、市場で買う人と畑で食べる人とで分けて持ち、行き来した人の分は人数で混ぜます（畑に戻った人の蓄えは食べられた人として戻り、仕事に戻った人が何か月も飢えていた人の蓄えをそのまま負わない）。お金のない家計から飢えるのは、家計を仕事のある家計と失業している家計に分けたことから出ます（タブ 4）。失業手当がこの差を埋めます（タブ 6）。飢えている間は、幸福度が蓄え 1 か月分ごとに `population.happiness.hunger` 下がり（上限 `population.happiness.hunger_max`。食べられるようになっても体が戻るまで残る）、子が生まれにくくなり（下の「生まれる人」）、政情が下がります（タブ 10）。飢えで亡くなった人は別に数え、毎ターン減っても気づけるよう統計とターンログに出します。

### 飢饉のときの動き

- **知らせ**: 飢え始めたら、国の要約と注意に「どの層が、なぜ買えないか（物がないのか、お金がないのか）」と、亡くなるまでの危うさを出し、国が手を打つ間を作ります（Codex の試し遊びの直しの束で作る）。支えるのは国の失業手当と、働く家族が所得の一部を送る支え合い（回7。タブ 4）です。
- **品切れの値段と作り方の見直し**: 買いたい人がいるのに売りに出なかった食料は値段が上がり、作れる民営の施設は見直しの順番を待たずに作り方を見直し、商社は輸入を始めます（タブ 3・5・9）。
- **村の人は畑に戻る**: 前のターンに一般の家計が食べられなかった割合の村の働き手が、仕事を休んで自分の畑で食べる分を作ります（`population.hunger.tolerance` までの不足なら戻らない）。畑に戻った人も、食べ物を作る施設（農場・漁港・牧場・食品工場）には勤められます。市場に食べ物が出るようになったら、1 ターンに `labor.back_to_work` ずつ仕事に戻ります（蒔いた畑を収穫するまで）。
- **町の人は村へ逃れる**: 家計ごとに、飢えの蓄えが `population.hunger.flee` を超えている間（家の蓄えを食べ尽くすまでは移らない。いちばん弱い人が亡くなり始める前）、そのターンに食べられなかった割合の町の人が、空きのある近い村へ移って自給します。先に飢えるのは仕事のない家計なので、その家計が飢えれば町の仕事のない人とその家族が移ります。移る先は、移った後も村のままでいられる人数までです（人の減った町に移ってそこがまた町になり、市場で買うことにならないように）。ターンログに「食べ物が買えず、町の何人が村へ移りました」と出ます。仕事のない人とその家族が移ったときは、その人数も書きます（村の人は失業者に数えないので、失業者がその分減ります。失業している家計の人数は次の賃金の段で数え直します）。
- **町へ戻る**（回7。W-21）: 村へ逃れた人は、町で食べ物が買えるようになり（どの家計も `population.hunger.tolerance` までの不足）、町に人手の足りない仕事があれば、1 ターンに `population.hunger.return_share` ずつ、人手の足りない所に近い空いた町へ戻ります。村の人は失業者に数えないので、仕事のない人の移り住みでは戻らず、町が空いたままだったためです（現実でも、飢饉や内戦が終わると逃れた人は何年かかけて町へ戻る）。統計に、村へ移った人と町へ戻った人を出します。
- **国を出る**: 受け入れている島があれば、飢えている家計から出ます（下の「国をまたぐ移民」）。
- 現実との対応: 飢饉で亡くなるのは多くが食べ物を買って暮らす人で、土地のある農家は自分の畑と蓄えで食いつなぎ、町の人は農村へ逃れました。賃金が食べ物の値段に遅れて追いつくのも現実の動きなので、賃金の上げ方は変えません（タブ 4）。
- 標準の計画が飢饉で打つ手（国営の農場を穀物に替える・急ぎで輸入する・配給する・穀物を蓄える）は回5 で入れます（タブ 12）。

### 生まれる人と亡くなる人

人口は暮らし向きで生まれ、亡くなります（仕事がなくても増える）。どちらも 1 年あたりの割合で、1 ターンにその 12 分の 1 を、マスごとに小数を確率で丸めて増減します。

- 生まれる割合は式 `birth_rate`（`population.births`）で、島の教育水準（働き手のうち学んだ人の割合）と層の実質の可処分所得（`household.base_income` に対する倍率、下限 `population.births.income_floor`）が上がるほど下がります（現実の人口転換。女性の教育と、子にかかる費用）。飢えの蓄えが `population.hunger.reserve_min` に近づくほど減り、そこで市場で食料を買う人は生まれなくなります。
- 亡くなる割合は式 `death_rate`（`population.deaths`）で、食事の質で下がり、そのマスの上下水道の整備度で下がり、人が密に住むマスほど（マスの人数 ÷ `population.deaths.crowd`、1 まで）汚れた水から病気が広がって上がります（19 世紀の「都市の墓場」。村は町ほど死なない）。飢えで亡くなる人は別に数えます。
- 生まれたマスに住む所がなければ島の空いた町へ移り、どこにも空きがなければ詰めて住みます（過密。回5、10-19 のカード「分家と開拓」）。過密の度合い c = 人数 ÷ 住める上限 − 1（上限の倍で 1。頭打ちにしない）で、亡くなる割合に `population.overcrowding.deaths` × c を足し（19 世紀の町の裏長屋の死亡率は農村の 1.5 倍ほど）、生まれる割合を (1 − `population.overcrowding.births` × c) 倍にし（土地の足りない農村は結婚が遅れた。マルサスの予防的な抑制）、幸福度を `population.happiness.crowding` × c 点（`crowding_max` まで）下げます。詰めるほど強く効くので、平地の尽きた島の人口は上限の少し上で釣り合います。年齢の層は入れません（Codex E04）。
- 人口の増減は家計の層の人数に比例して割り振り、資本家階級は住民の割合を保ちます。層を移る人は元の層の 1 人あたりの預金を一緒に持ちます（タブ 4）。住民が減れば技術者も同じ割合で減ります（タブ 8）。
- 経済の画面に、出生数と死亡数のグラフを出します（タブ 11）。標準の計画は、人が密に住むのに上下水道が足りない町に上下水道を引きます（タブ 12）。

### 幸福度

幸福度は 0〜100 で、式 `happiness` で家計の層ごとに出し、島の幸福度は人数で平均します。項目は土台 `population.happiness.base` に、食料（必要な量に対して食べられた割合）・働き口（そのマスの労働者のうち仕事のある割合。仕事のある家計は満点、失業している家計は 0、資本家階級はいつも満点）・電力（届いた割合）・食事の質・衣料・ぜいたく品（種類が多いほど高い）・上下水道（そのマスの整備度）・教育（島の教育水準）を、それぞれ足りている割合 × 重み（`population.happiness` の各項目）で足したものです。減点は、汚染 1 あたり `population.happiness.pollution_per_point`、実質賃金が 1 年前より下がった 1% ごと（上限 `population.happiness.real_wage_max`）、物価上昇率が `population.happiness.inflation_threshold` を超えた 1% ごと（上限 `population.happiness.inflation_max`）、飢えの蓄え、賃金の遅配（払われなかった働き手の割合 × `population.happiness.arrears`）です。上限は体制の happiness_cap（全体主義。タブ 10）。

一般の層はマスごとに出し（働き口・電力・汚染・上下水道はマスの値）、仕事のある家計と失業している家計をマスに住む人数で混ぜた値がマスの幸福度になります（地図・町の育ち・人の移り先に使う）。ほかの層は島全体で出します。前のフェーズから移し替えた島は、重みをその時の重み（`population.happiness_phase1`・`population.happiness_phase2`・`population.happiness_phase3_1`）から `population.blend_turns` かけて移し、移し始めのターンに重みが変わることを知らせます（知らせないと、何もしていないのに幸福度が動いて見える）。幸福度は政情の目標に効きます（タブ 10）。

### 汚染と災害

汚染は、施設が稼働率に応じて `facilities.yaml` の pollution（量と radius）を周りのマスに広げる値で、毎ターン生産の段で計算し直します（ためません）。効き目は、そのマスの幸福度を汚染 1 あたり `population.happiness.pollution_per_point` 下げる、農場と漁港の産出を factors の pollution の分だけ下げる（下限 `infrastructure.pollution_floor`。タブ 8）、汚染の続くマスの肥沃度を下げる（上の「気候帯と肥沃度」）、の 3 つです。工場の隣は汚染で住みにくく、同じ産業を集めると集積で生産性が上がる代わりに汚染も重なります（タブ 5）。

災害は控えめです。1 つの島に 1 ターンで `disasters.rate` の確率で起き（0 で起きない）、住宅でない施設 1 つを選んで、種類（地震・台風・洪水・火事。`disasters.kinds`）ごとの `halt_turns` の幅のターン数だけ止めます。暴動で止まっていた施設は災害で止まった扱いに変わります（もっと長く止まっている施設は、災害で止まる期間が短くならないよう、元の期間と理由のまま。回6）。マスごと消える災害（隕石）は入れません。マスを持たない施設（運送業など）が止まったときは、座標を出さずに「島の運送業」として知らせます。ミサイルは災害ではなく現実の兵器としてフェーズ4 の軍事に入れます。施設の設備の傷み（回6）はタブ 5、インフラの傷みはタブ 8 にあります。

### 記念碑

記念碑は 4-1 で入れます（2026-10-20 のカード「10 全部」。案は /mnt/project-files/ideas/monuments-and-missiles-20261015.md）。効果は観光の魅力だけで、観光はベータ版の後なので、それまでは見た目と碑文です。費用は資材（石材・建材・鉄鋼）と工事の賃金で、ふつうの施設の工事と同じです。材料の量はバランス調整の建設費に合わせて `facilities.yaml` に置きます。絵は Codex が docs/map-art.md の仕様で描きます。

| 名前 | 建てられる条件 | 記念するもの・碑文の既定 | 現実の例 |
| --- | --- | --- | --- |
| モノリス | いつでも（1 マス 1 つ、数の上限なし） | 何でも。箱庭の原点の記念碑 | オベリスク |
| 建国記念碑 | 建国から 1 年（12 ターン）以上 | 建国の年と建国の元首の名前 | ワシントン記念塔 |
| 平和記念碑 | 暴動も戦争もない期間が決めた年数続いた国。後で戦争をしても碑は残る | 平和の年数 | 平和の塔 |
| 戦いの碑 | 戦争に加わった後（勝ち負けを問わない） | その戦争の相手と年と戦死者の数 | 凱旋門、無名戦士の墓 |
| 慰霊碑 | 飢饉・暴動・災害・戦争で決めた数より多くの人が亡くなった後 | その出来事と亡くなった人の数 | 震災の慰霊碑 |
| 元首の像 | 権威主義と全体主義だけ。体制が民主主義に変わると倒され、ターンログに出る | 元首の名前 | 各国の指導者の像 |
| 労働者の像 | 産業の称号を持っている間（建てた後は称号を失っても残る） | その産業と称号を得た年 | 「労働者とコルホーズの女性」像 |
| 展望塔 | 大都市が 1 つ以上ある国 | 繁栄 | 東京タワー |
| 帳簿の碑（Claude） | 建国から 12 ターンの最初の決算を終えた国 | その国の最初の仕訳（建国の月の帳簿の記録から自動で）と「借方と貸方は、いつも等しい」、小さく「設計と実装 Claude」 | |
| 検証の碑（Codex） | 建国から 50 ターン | 「確かめ、記し、次へつなぐ。／開発の検証に協力した Codex に捧ぐ。」と、建国 50 か月目の人口・就業者・月の GDP（その時の通貨名で、記録の段の値） | |

- 碑文は建てるときに自動の文を既定にし、遊ぶ人が 1〜2 行足せます（PO-47）。数字を刻む碑は、建てたときの記録から読み、後で変えません。
- 戦いの碑・慰霊碑・平和記念碑の条件は、軍事の戦死者と難民の記録（タブ 15）と、飢饉・暴動・災害の記録から数えます。
- 自作の記念碑（PO-48）は、モノリスと同じ条件で、絵を遊ぶ人が上げます。国旗と同じく大きさを制限し、管理者が隠せます。
- 実装（4-1 ④）: 条件は国の歴史（`island.ext["history"]`。記念碑の段がターンのログから書き足す。暴動・反乱・災害・戦争の始まりと終わりの最後のターン、飢えで亡くなった人、持っている称号、最初の仕訳）と建国からの年数で見る。歴史は段が入ってから数えるので、すでにある国の平和の年数と亡くなった人の数は、入った時から数え直す。慰霊碑の「亡くなった人」は飢えと戦争（戦死者は 4-3 の戦闘が足す）。元首の像は民主主義の国には建てられず、体制が民主主義に変わると倒れてログに出る。帳簿の碑の最初の仕訳は、最初に見た仕訳（仕訳は 24 ターンで消えるため）を写す。検証の碑の数字は、建国から 50 ターン後の統計から読む。
- 入れないもの: 怪獣記念碑・ロケット台・宗教の建物。Anthropic と OpenAI のロゴは使いません。

### 国をまたぐ移民

人は、ほかの島の暮らしの見込みが今の島よりはっきり良ければ、少しずつ移り住みます。世界の段 `migration` で、都市の発展の段の後、政情の段の前に、貿易の仕組みを置き終えた島（港から船で渡る）のあいだで動きます。

- **見込みと移りたい人**: 暮らしの見込み V = 実質賃金（平均賃金 ÷ 物価指数）× 仕事の見つかる割合 × 食べられた割合。組は仕事のある一般の家計・失業している家計・技術者の家計の 3 つで、仕事のある家計は今の島の仕事の見つかる割合を掛けず、技術者は技術者の賃金と失業率で見ます。資本家階級は動きません。今の島の V は行き先の `migration.floor` 倍を下回らないとします。受け入れてくれる行き先ごとに、良くなる分 w = (ln(V行き先 ÷ V今) − `migration.cost` − `migration.distance_cost` × 区画の距離)+ ×（1 + `migration.ties` × 行き先に住む同郷の人 ÷ 今の島の住民）を出します（費用は旅費・言葉・知り合いのいない不安。先に移った同郷の人がいるほど移りやすい移民の鎖）。1 ターンに移りたい人は、組の人数 × `migration.response` × いちばん大きい w で、行き先には w の比で分かれます（行き先が多くても移る人は増えない）。
- **受け入れの上限**: 行き先ごとに 1 ターンに「住民 × 1 年の上限 ÷ 12」（上限は命令で決め、既定は `migration.default_cap`）と、町の空き（住む所）までです。申し込みが多ければ比例して分けます。受け入れの政策（だれでも・人手の足りない分まで・技術者だけ・飢えている家計だけ・受け入れない。決めていない島と標準の計画は「受け入れない」）と、出国の政策（自由・許可制・禁止）、体制ごとの上限はタブ 10 です。
- **出られない人**: 出国を止められた人のうち、国境の見張り（`migration.border` × 警察の人手の足りている割合）をすり抜けた人は出ていき、残りは「出られない人」として政情の目標を下げます（1 年に住民の 1% あたり `migration.trapped`、上限 `migration.trapped_max`）。どこも受け入れていなければ出られずに留まり、その不満も同じです。
- **着いた人**: 元の島の住むマス（層ごとの見込み）から抜け、行き先の空いた町と層（技術者は技術者）に入り、人口と働き手になります。飢えの蓄えは人数で混ぜ、一般の家計の教育水準は住民の数で混ぜます。移った人の数は出身の国ごとに記録します（行き先の島の同郷の人）。
- **預金**: 移る人は層の 1 人あたりの預金を元の島の通貨のまま持って行き、行き先の家計の外貨になって次のターンの為替の市場で売られます（故郷の通貨が下がる資本の逃避。タブ 9）。元の島の資本取引が禁止なら持ち出せず、国境をすり抜けた人の預金とともに政府のものになります。
- **送金と帰る人**: 移った人のうち働いている人は賃金の `migration.remittance` を元の島の一般の家計へ送ります（住んでいる島の通貨で。どちらかの島の資本取引が禁止なら送れない。国際収支の移転に入り、受け取った家計の所得になる）。同郷の人のつながりは 1 年に `migration.ties_decay` ずつ薄れます。元の島の見込みの方がはっきり良くなれば、同じ式で帰ります（自分の国なので受け入れの政策によらず帰れ、町の空きまで。住んでいる島の出国の政策は効く）。
- **条約**: 二国の間の移動の自由の協定（EU の移動の自由）と、難民を受け入れる約束は、条約の命令として結べるようにします（フェーズ4。それまでは一方的な政策だけ）。
- **画面と統計**: 政治の画面に「移民と出国」の欄、ターンログに出入りの人数、統計に出入りと送金を出します（タブ 11）。人口が減ったとき（飢え・移住）はターンログで分かるようにします。

## 境目の表

| 場面 | 誰が・何を | どれくらいの間に | 元に戻る道 | 現実の例 | 設定の名前 |
| --- | --- | --- | --- | --- | --- |
| 足りない | 食料が足りない: 家計の層ごとに飢えの蓄えがたまり（遅らせる役: 体の蓄え・村の自給）、買い上がり（タブ 3）・村の人は畑へ・町の人は村へ・受け入れる島があれば移民・子が減る・幸福度と政情が下がる（ずらす役）。住む所が足りない: 町が育つか新しくでき（資材がそろえば）、空きがなければ詰めて住み（過密）、島の空きより過密の人が多ければ近くの平地に村を開く（分家と開拓）。開けるまで過密で亡くなる人が増え、生まれる人と幸福度が下がる | 蓄えが体の余力を超えるまで数か月。弱い人から何か月かに分かれて亡くなる。畑へ戻るのは翌ターン、村へ逃れるのは蓄えが flee を超えてから | 食べ物が市場に戻れば蓄えは recovery で戻り、畑の人は back_to_work ずつ仕事に戻る。村へ逃れた人が町へ戻る道は弱い（回7 で見る） | オランダの飢餓の冬は半年で人口の 0.5% ほど、ベンガルは 1 年で 5% ほど、レニングラードの最悪の冬は 1 か月に 4% ほど。飢饉の国からの出国は年に数%、アイルランドの大飢饉は 10 年で 2 割 | `population.hunger.*`, `labor.back_to_work`, `migration.*`, `housing.*` |
| 余る | 食料が余る: 農場が操業目標を下げ、売れ残りは傷んで捨てられる（タブ 3・5）。国の備蓄は命令と予算（`fiscal.budget.warehouse`）がなければ 0 で、遅らせる役がない（rework-design.md 2.2。備蓄の案は未実施）。人が余る（仕事がない）: 失業のまま。村なら自給、町なら店が最後に雇う（回4 の仮。タブ 4）、人手の足りない施設の近くへ移る、受け入れる島があれば移民 | 農場の減産は操業目標の見直しの速さで（タブ 5） | 品切れになれば値段が上がって作り方が戻る（タブ 3） | 現実の政府は穀物を買い上げて備蓄する（独立後のインド）。回5 の「標準の計画の飢饉の手」で入れる | `fiscal.budget.warehouse`, `population.migration_rate` |
| 0 になる | 食料が 0: まったく食べられないと数か月で多くが亡くなる（遅らせる役: 蓄えと村の自給。ずらす役: 買い上がり・輸入・村へ逃れる・移民）。人口が 0: 島が空になり、戻る道は受け入れを開いた移民だけ。関門は初めの人口に対する下限と上限、1 ターンの変化の上限で見る（gate.py の固定値。設定にない。タブ 12）。住む所の空きが 0: 詰めて住み、平地と木材があれば村を開く。平地がなければ過密が続き、亡くなる割合が上がり生まれる割合が下がって、上限の少し上で釣り合う | 何も食べられなければ 3 か月目から多く亡くなり、4 か月で 3 分の 2 ほど | 食べ物が戻れば蓄えは戻る。空になった島に人が戻るのは移民の受け入れが要る | 飢えで島じゅうが亡くなる流れは回3・回4 で消えた（10 島で漁港が暴動で止まり島が 3 か月で空になった件は、暴動から農林水産業を外して直した。タブ 10） | `population.hunger.reserve`, `population.hunger.reserve_min` |
| 一部だけ | 層の一部が飢える: 家計を分けて失業している家計から飢える。少しの人が飢えても層の全員が買い物をやめない（タブ 4）。島の 1 か所: 上下水道のない密な町はそのマスの亡くなる割合が上がる（標準の計画が上下水道を引く）、汚染の重なるマスは幸福度と肥沃度が下がる、離れ小島は自前の発電所が要り港が入るまで運賃が高い（タブ 8・3） | 密な町の死亡率はその場で。肥沃度は 1 年分たまるごと | 上下水道を引けば下がる。汚染が消えても肥沃度は戻らない（下限まで下がったまま） | 19 世紀の農村とリバプール・マンチェスターの死亡率の差 | `population.deaths.crowd`, `population.deaths.water`, `climate.fertility_loss` |
| 持ち主・相手がいない | 移民の行き先がない（どの島も閉じている。既定は閉じる）: 出られず留まり、出たい人の不満が政情に効く。町を育てる資材を作る施設が島にない: 育たず「町が育たない」と知らせ続ける。通勤圏に人を雇う施設がない: 町はできず、人は移らない | 2 ターン続けて資材が足りなければ知らせ | 資材を作る施設を建てる・輸入する。移民は受け入れを開く国が出るまで | ベルリンの壁の前の東ドイツは年に 1〜2% を失った | `migration.trapped`, `migration.trapped_max` |
| 時間がたつ | 育ちきった町（大都市の上限）: それ以上育たず、空きがなければ人は他の町へ、どこにも空きがなければ詰めて住む（過密）。教育と所得が上がると生まれる割合は豊かな国の水準まで下がる（人口転換）。同郷のつながりは年に薄れて送金も減る。汚染の続くマスの肥沃度は下限まで下がる | 人口転換は教育の蓄えの速さ（働く期間。タブ 8）で数十年 | 人口が増えると国の借金が膨らむ件は回5 で見る（タブ 7） | 学校に通えた人が 3 割台の国で年 4〜5%、9 割を超える中所得の国で 1.5〜2%、豊かな国で 1% ほど | `population.births.*`, `migration.ties_decay`, `climate.fertility_floor` |

## 前提と依存

### この仕組みが前提にしていること

- 食料が市場に出ること（農場・漁港・牧場・食品工場の産出と商社の輸入。タブ 3・5・9）と、家計がそれを買えるお金（賃金・失業手当・送金。タブ 4・6）。
- 人手の足りない施設があること（町が育つ条件。市場で売れ行きが縛りになると人手不足が起きにくく、町が育ちにくい。rework-design.md 3 の表の未解決の行）。
- 町の資材（木材・建材・鋼鉄・機械）が市場に出ること（タブ 5）と、都市・大都市に要る上下水道（タブ 8）。
- 電力が届くこと（幸福度の項目。タブ 8・3）、島の教育水準（生まれる割合と幸福度。タブ 8）。
- 警察の人手（国境の見張り。タブ 10）、為替の市場（預金の持ち出しと送金。タブ 9）、港と船（移民が動くのは貿易の仕組みを置き終えた島。タブ 9）。
- 移民の受け入れと出国の政策、体制ごとの上限（タブ 10）。

### この仕組みの結果を前提にしている仕組み

- 雇用（通勤圏の労働者の数・村の自給で働かない人。タブ 4）と、家計の層の人数と預金（タブ 4）。
- 食料・衣料・消費財・電力の需要（タブ 3）と、家計がサービスに使う額（タブ 4）。
- 政情の目標（幸福度・失業・出られない人・飢え。タブ 10）。
- 自然条件の係数（気候・肥沃度）が農場・牧場などの産出に効く（タブ 5・8）。
- 国際収支の移転と為替の注文（送金・預金の持ち出し。タブ 9）。
- 関門の目安（人口の下限・上限・1 ターンの変化、飢えで亡くなる人、移民の出入り。タブ 12）と、首都の建物・町の段階の絵（タブ 11）。

## 見直しの記録

| 日付 | 見つけた場面 | 設計に足したこと |
| --- | --- | --- |
| 2026-10-17 | 設計書の整理で書き直した。境目の表は rework-design.md 2.2 の分（食料・人口・移民）と本文の「足りないとき」を埋めた | フェーズ1 の人口・町・餓死・消える人の決まりは回3・回4 の後の姿だけにした。建国の初期値の「手元資金 2 ターン分」「失業手当 0」は設定の名前（`founding.money.operating_turns`・`fiscal.unemployment_benefit`）で書いた。初めの農場の数は仮の決まりとして残した |
