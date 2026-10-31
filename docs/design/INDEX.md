# 設計書の決まりの索引

このファイルは `python tools/spec/design_index.py write` で作ります。手で直さないでください。
決まりの全文は各ファイルの「決まりの一覧」にあり、`python tools/spec/design_index.py show <番号>` でも出せます。
決まりはここでは頭の一文だけを、長いものは途中まで載せています。

## [1 全体とターンの流れ](01-overview.md)

決まり 29 個、うち仮の決まりの表にあるもの 1 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| OV-01 | ルールは純粋な Python のコア `hakoniwa_core` に置き、Django は保… | 決定 | tests/core/test_boundaries.py |
| OV-02 | 世界は 1 サーバーに 1 つ | 決定 | tests/core/test_founding.py、tests/server/test_turns.py |
| OV-03 | 1 ターンは箱庭暦の 1 か月 | 決定 | tests/core/test_dates.py、tests/server/test_calendar.py |
| OV-04 | 定期実行が `run_turn` を呼び、次の更新時刻（間隔は `turn.interval_m`… | 決定 | tests/server/test_turns.py |
| OV-05 | 更新中の印を立て、世界全体を読み込んで写し（バックアップ）を取ってから段を回す | 決定 | tests/server/test_turns.py |
| OV-06 | 進行役は `stages` に並べた順に段を呼ぶだけ | 決定 | tests/core/test_docs_match.py、tests/core/test_engine.py |
| OV-07 | 読み込みから書き戻しまでは 1 つのトランザクション | 決定 | tests/server/test_turns.py |
| OV-08 | 島ごとの段は `run_island` の中でその島だけを書き換え、他の島は段が始まる前の写しだ… | 決定 | tests/core/test_engine.py |
| OV-09 | 全島の段は「集める `collect` → 一度に決める `resolve` → 各島に返す ``… | 決定 | tests/core/test_engine.py、tests/core/test_phase3_3.py |
| OV-10 | 乱数は世界の種・ターン番号・段の名前・島の番号から作る | 決定 | tests/core/test_engine.py、tests/core/test_gate.py |
| OV-11 | 同じものを取り合うときは定義データの優先順位と番号で順を決め、それでも並ぶときだけ乱数を使う | 決定 | tests/core/test_rules.py |
| OV-12 | 量は整数で持つ | 決定 | （未確認） |
| OV-13 | お金は `ledger` の `post`・`pay` を通してだけ動く | 決定 | tests/core/test_economy.py |
| OV-14 | 島をまたぐお金と運ぶ途中の荷は、全島の段 `trade_check` が毎ターン突き合わせ、合わ… | 決定 | tests/core/test_phase3_3.py、tests/core/test_trade.py |
| OV-15 | 定義データはすべてパック | 決定 | tests/core/test_defs.py |
| OV-16 | 同じ名前の定義はエラー | 決定 | tests/core/test_defs.py |
| OV-17 | 定義データは文字列の名前で参照し、DB の外部キーにしない | 決定 | tests/server/test_turns.py |
| OV-18 | 式は名前の付いた差し替え口で、`formulas` で別の関数に替えられる | 決定 | tests/core/test_engine.py |
| OV-19 | コード中に数値を直接書かず、定義データか `game.yaml` に置く | 決定 | （未確認） |
| OV-20 | 命令は受付時と実行時の 2 回検証する | 決定 | tests/core/test_rules.py |
| OV-21 | 画面用の別の計算を持たず、施設の収支や統計はターン更新で保存した記録から作る | 決定 | （未確認） |
| OV-22 | DB 固有の書き方をせず Django の ORM だけを使い、SQLite と Postgre… | 決定 | tests/server/test_settings.py |
| OV-23 | 巻き戻しは世界全体を同じ時点にそろえる | 決定 | tests/server/test_turns.py |
| OV-24 | 前のフェーズから続く島は、更新の初めの段 `upgrade` が 1 度だけ移し替える | 決定 | tests/core/test_phase3_2.py、tests/core/test_shocks.py |
| OV-25 | 財の量の単位（トン・m³・台・MWh など）は定義データに持ち、画面では「資源」と呼ぶ | 仮（仮の決まりの表あり） | （未確認） |
| OV-26 | ベータ版は家に置く専用の機械 1 台で動かし（10-25 に VPS から変更）、世界を複数動か… | 決定 | （未確認） |
| OV-28 | 更新が走っている間は、命令の列の書き換えを受け付けない（409 | 決定 | tests/server/test_turns.py |
| OV-29 | JSON を受ける入り口は、壊れた本文・オブジェクトでない本文・NaN や Infinity・項… | 決定 | tests/server/test_bad_input.py |
| OV-27 | 登録は誰でもできる | 決定 | tests/server/test_web.py |

## [2 島と町と人口](02-population.md)

決まり 56 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| PO-01 | 世界は区画の格子で、区画の数は島の数（目安 `world.default_islands`）のお… | 決定 | tests/server/test_phase3_2.py |
| PO-02 | 1 つのアカウントで持てる島は 1 つ | 決定 | tests/server/test_phase1.py |
| PO-03 | 島は `island.size` 四方の中に描き、縁の 1 マスは海 | 決定 | tests/core/test_founding.py |
| PO-04 | 地形（海・浅瀬・平地・森・山）は描いた形と種だけから自動で配り、`founding.terrai`… | 決定 | tests/core/test_founding.py |
| PO-05 | 鉱業資源は石炭・原油・鉄鉱石・石材・ボーキサイトから `founding.max_resourc`… | 決定 | tests/core/test_founding.py |
| PO-06 | 気候帯は世界マップの行を `climate.zones` の順に `climate.shares`… | 決定 | tests/core/test_phase3_2.py |
| PO-07 | 肥沃度は平地のマスに `climate.fertility` の幅でまとまりで配り、農場と牧場の… | 決定 | tests/core/test_phase3_2.py |
| PO-08 | 首都は建国で選ぶ | 決定 | tests/core/test_founding.py |
| PO-09 | 首都のマスには中央省庁が建ち、政治体制に応じた建物の見た目になる | 決定 | tests/core/test_phase3_4.py、tests/server/test_web.py |
| PO-10 | 政治体制（2 軸）と経済体制（市場・混合・計画）を建国で選び、初めの施設の持ち主を経済体制に合わ… | 決定 | tests/core/test_founding.py |
| PO-11 | 国名・首都名・通貨名と記号・国家元首の名前は建国で入れ（長さの上限は `founding.nam`… | 決定 | tests/core/test_founding.py |
| PO-12 | 地名は島を `naming.place_block` マス四方の区画に分けて作風で自動で付け、首… | 決定 | tests/core/test_founding.py、tests/core/test_orders.py、tests/core/test_rules.py |
| PO-13 | 建国直後の島は `start_island.yaml` に従い、首都の町・周りの村・初めの施設・… | 決定 | tests/core/test_founding.py |
| PO-14 | 初めの施設のうち `fit` を書いたものは、数を要る量に合わせる: 食料は建国の住民の食べる量… | 決定 | tests/core/test_founding.py、tests/core/test_recession_round1.py |
| PO-15 | 建国のお金は、建国の国債 → 家計への給付 → 出資 → 銀行の国債買い、の順の仕訳で作る | 決定 | tests/core/test_founding.py、tests/core/test_infrastructure.py |
| PO-16 | 町（住宅）は自動で建つ施設 `housing.facility` で、段階（村・町・都市・大都市… | 決定 | tests/core/test_high_rise.py、tests/core/test_housing_floor.py、tests/core/test_playtest3_fixes.py |
| PO-17 | 町ができる・床を足すのは、人手の足りない施設（店が雇いきれなかった集落を含む）の通勤圏で、近くの… | 決定 | tests/core/test_rules.py |
| PO-18 | 床は段階の幅の `housing.floor_step` ずつ足し、段階の上限を超えるときに次の… | 決定 | tests/core/test_housing_floor.py、tests/core/test_rules.py |
| PO-19 | 床を足す資材は、市場・混合の国では、人手の足りない所で町を育てる分は一般の層が、住み替えの分は望… | 決定（国が買う分は回2 で作る） | tests/core/test_housing_floor.py、tests/core/test_playtest3_fixes.py、tests/core/test_rules.py |
| PO-20 | 都市・大都市に育つには、そのマスの上下水道が `settlements.yaml` の wate… | 決定 | tests/core/test_balance_round4.py |
| PO-21 | 村の仕事のない住民は自給の農業で自分の食べる分をまかない、市場で買わない | 決定 | tests/core/test_balance_round3.py |
| PO-22 | 仕事のない人は毎ターン `population.migration_rate` の割合で、人手の… | 決定 | （未確認） |
| PO-23 | 住民は 1 人 1 ターン `population.food_per_person` の食料を要… | 決定 | tests/core/test_rules.py |
| PO-24 | 飢えの蓄えは層ごとの不足の月数 S で、S ← S + 不足 − `population.hun`… | 決定 | tests/core/test_balance_round3.py |
| PO-25 | 飢えるのは市場で食料を買う人で、お金のない家計（失業している家計）から | 決定 | tests/core/test_balance_round4.py |
| PO-26 | 飢えている間は幸福度が蓄え 1 か月分ごとに下がり、子が生まれにくくなる | 決定 | tests/core/test_balance_round4.py |
| PO-27 | 飢え始めたら、国の要約と注意に「どの層が・なぜ買えないか（物がないのか、お金がないのか）」と亡く… | 決定（Codex の直しの束で作る） | （未確認） |
| PO-28 | 食べ物が買えないと、村の働き手は前のターンに食べられなかった割合だけ仕事を休んで畑に戻り、市場に… | 決定 | tests/core/test_balance_round4.py |
| PO-29 | 家計の飢えの蓄えが `population.hunger.flee` を超えている間、そのターン… | 決定 | tests/core/test_balance_round4.py |
| PO-30 | 生まれる割合は式 `birth_rate`（`population.births`）で、学んだ人… | 決定 | tests/core/test_balance_round4.py |
| PO-31 | 亡くなる割合は式 `death_rate`（`population.deaths`）で、食事の質… | 決定 | tests/core/test_balance_round4.py |
| PO-32 | 生まれたマスに住む所がなければ島の空いた町へ移り、どこにも空きがなければ消えずに詰めて住む（過密… | 決定 | tests/core/test_round5_village.py |
| PO-33 | 人口の増減は家計の層の人数に比例して割り振り、資本家階級は割合を保つ | 決定 | （未確認） |
| PO-34 | 幸福度は式 `happiness`（`population.happiness`）で層ごとに出し… | 決定 | tests/core/test_phase3_4.py、tests/core/test_playtest3_fixes.py |
| PO-35 | 前のフェーズから移し替えた島は、幸福度の重みをその時の重みから `population.blen`… | 決定 | tests/core/test_playtest3_fixes.py |
| PO-36 | 汚染は施設が稼働率に応じて `facilities.yaml` の pollution の量を … | 決定 | tests/core/test_phase3_2.py |
| PO-37 | 災害は 1 島 1 ターンに `disasters.rate` の確率で起き、施設 1 つが種類… | 決定 | tests/core/test_balance_round6.py、tests/core/test_playtest3_fixes.py、tests/core/test_rules.py |
| PO-38 | 国をまたぐ移民は、暮らしの見込み V（実質賃金 × 仕事の見つかる割合 × 食べられた割合）の対… | 決定 | tests/core/test_migration.py |
| PO-39 | 動く組は仕事のある一般の家計・失業している家計・技術者の家計で、資本家階級は動かない | 決定 | tests/core/test_migration.py |
| PO-40 | 受け入れは 1 年に住民の上限（既定 `migration.default_cap`）と町の空き… | 決定 | tests/core/test_migration.py |
| PO-41 | 出国を止められた人のうち国境（`migration.border` × 警察の充足）をすり抜けた… | 決定 | tests/core/test_migration.py |
| PO-42 | 移った人は行き先の層と空いた町に入り、預金を元の島の通貨のまま持って行く（資本取引が禁止なら政府… | 決定 | tests/core/test_migration.py |
| PO-43 | 移った人のうち働いている人は賃金の `migration.remittance` を元の島の一般… | 決定 | tests/core/test_migration.py |
| PO-44 | 二国の間の移動の自由の協定と、難民を受け入れる約束は、条約の命令として結べるようにする | 決定（フェーズ4 で作る） |  |
| PO-45 | 長く操作しない島は自動で消さず休止のまま残し、管理者が管理画面で扱う | 決定 | （未確認） |
| PO-46 | 飢饉で村へ逃れた人は、町で食べ物が買え、町に人手の足りない仕事があれば、1 ターンに決めた割合ず… | 決定 | tests/core/test_balance_round7.py |
| PO-47 | 標準の記念碑は 10: モノリス・建国記念碑・平和記念碑・戦いの碑・慰霊碑・元首の像・労働者の像… | 決定（4-1 ④） | tests/core/test_monuments.py、tests/server/test_monuments.py |
| PO-48 | 自作の記念碑はモノリスと同じ条件で、絵を遊ぶ人が上げる（国旗と同じ守り） | 決定（4-1 ④） | tests/server/test_monuments.py |
| PO-49 | 国の大きさの倍（段6 A3、W-85）: マス1つ・施設1つの表す大きさを `scale.peo`… | 決定 | tests/core/test_scale.py、tests/server/test_world_scale.py |
| PO-50 | 家の傷みと手入れ（W-186 の直し 回3）: 町の家は年に `housing.wear` ずつ… | 決定（回3） | tests/core/test_recession_round3.py |
| PO-51 | 延べ床（W-190・W-201）: 町のマスは延べ床を持ち、最低の広さ `housing.spa`… | 決定 | tests/core/test_housing_floor.py |
| PO-52 | 望む広さと住み替え（W-201）: 層の望む広さは `housing.space.base` ×… | 決定 | tests/core/test_housing_floor.py |
| PO-53 | 幸福度の住まいの広さ: 層ごとに min(1, 1 人あたりの広さ ÷ 望む広さ) × 重み ``… | 決定 | tests/core/test_housing_floor.py |
| PO-54 | 高層化（W-190）: 大都市から上（高層都市・超高層）へは、そのマスの通勤圏に新しい町を置ける… | 決定（仮: 地価の代わり、W-206 | tests/core/test_high_rise.py |
| PO-55 | 国の命令「住宅を建てる」（W-190）: 町のマスか空いた平地を選び、足す床（次の段階まで、また… | 決定（W-190 の回2 で作った | tests/core/test_public_housing.py |
| PO-56 | 計画経済の国営住宅（W-202）: 計画経済の国では家計は住宅の資材を買わず住宅ローンも借りない | 決定（W-190 の回2 で作った | tests/core/test_public_housing.py |

## [3 物と市場と値段](03-market.md)

決まり 39 個、うち仮の決まりの表にあるもの 2 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| MK-01 | 取引は「島×財」ごとに、ターンの市場の段で1回の板寄せで決める | 決定 | tests/core/test_economy.py |
| MK-02 | 財の市場に値幅はない | 決定 | tests/core/test_economy.py |
| MK-03 | 約定価格ちょうどの注文と、値幅の端で止まったときの多い側は、優先順位の高い順、同じ順位の中は量に… | 決定 | tests/core/test_economy.py |
| MK-04 | 売りと買いが交わらなければ約定せず、一番安い売りと一番高い買いの指値の真ん中（気配）を次のターン… | 決定 | tests/core/test_economy.py |
| MK-05 | 注文は出すときに「量×指値」のお金を取り置き、取り置けない分は出せない | 決定 | tests/core/test_economy.py |
| MK-06 | 注文を出すのは、独立採算と国庫直轄の施設（在庫の売りと次のターンの投入財の買い）、工事中の施設と… | 決定 | tests/core/test_balance_round3.py |
| MK-07 | 希望価格は売れ行きで見直す（式 `price_update`） | 決定 | tests/core/test_economy.py、tests/core/test_recession_round2.py |
| MK-08 | 独立採算の売り手は1単位の変動費（投入財・賃金・電気代・運賃）より安い指値を出さない | 決定 | （未確認） |
| MK-09 | 操業目標は、売れ行きの見込み（前のターンに売れた量へ少しずつ近づける）と在庫の目安との差で決め、… | 決定 | tests/core/test_rules.py |
| MK-10 | 持ち越せない売れ残りを翌ターンに指値なしで売った分は、値段が変動費を下回った幅が値幅に届くまで、… | 決定 | tests/core/test_economy.py |
| MK-11 | 市場の段の後に、持ち越せる期間（goods.yaml の `shelf_life`）を過ぎた在庫… | 決定 | tests/core/test_playtest4_fixes.py、tests/core/test_rules.py |
| MK-12 | 原料が足りずに稼働率が下がった施設は、前のターンに買うつもりだった分の原料がそろっていれば、止ま… | 決定 | tests/core/test_playtest3_fixes.py |
| MK-13 | 品切れの財（買いたい人がいるのに売りに出なかった財）は、島で作り始められる一番安い値段が今の値段… | 決定 | tests/core/test_balance_round4.py、tests/core/test_recession_round2.py |
| MK-14 | 島に買い手がなく売りだけの財は、圏で輸出が引き合う値段まで値幅ずつ下がる | 決定 | tests/core/test_trade.py |
| MK-15 | 家計は食料を、少ししか買えない段ほど高い指値で買う | 決定 | tests/core/test_balance_round3.py |
| MK-16 | 値幅の上端で買い続ける買い手（家計の車の燃料、工事とインフラの資材）は、いつもの値段の一番上の段… | 仮（仮の決まりの表あり） | tests/core/test_phase3_2.py |
| MK-17 | 工事とインフラの資材の注文は、同じ払い手（公共事業費）の分を合わせて売りに出ている量までにし、超… | 決定 | （未確認） |
| MK-18 | 作り方が複数ある施設は施設ごとに作り方を持つ | 決定 | tests/core/test_phase3_2.py |
| MK-19 | 民営が今作っていない物に替えるのは、その物の買いたい量が売りに出た量を大きく上回るときだけ | 決定 | tests/core/test_phase3_2.py |
| MK-20 | 島で品切れの財を作れる民営の施設は、見直しの順番を待たずに見直し、買いたい量が少なくても避けない | 決定 | tests/core/test_balance_round4.py |
| MK-21 | 農場の作付けは畑の割合で持ち、1ターンに畑の一部ずつ蒔き直す | 決定 | tests/core/test_balance_round3.py、tests/core/test_phase3_2.py |
| MK-22 | 2つの物を作る施設（製油所など）の原価は、初めの値段の比で分ける | 決定 | tests/core/test_phase3_2.py |
| MK-23 | 電力は生産の段で送電網ごとに同じ板寄せで売り買いする | 決定 | tests/core/test_rules.py、tests/core/test_shocks.py |
| MK-24 | 発電所の売り値の下限（1単位の変動費）には燃料の運賃を入れる | 決定 | （未確認） |
| MK-25 | 島の中の運賃は、売り手と買い手がそれぞれ「約定価格×自分の運賃の率」を運送業に払う | 決定 | tests/core/test_rules.py |
| MK-26 | 運賃の率は施設ごとの見込み | 決定 | tests/core/test_infrastructure.py、tests/core/test_rules.py |
| MK-27 | 運送業は島に1つ（マスを持たない施設） | 決定 | tests/core/test_rules.py |
| MK-28 | 運送業は自動車をトラックとして買い、1台を運転手の決めた人数分と数える | 決定 | tests/core/test_phase3_2.py |
| MK-29 | 初めの値段は、建国と移し替えのときに定義データのレシピから「原価＋上乗せ」で解く | 決定 | tests/core/test_recession_round1.py、tests/core/test_rules.py |
| MK-30 | 前のフェーズから移し替えた島で値段のない新しい財には、島の初めの値段に今の物価指数の倍率を掛けた… | 決定 | tests/core/test_phase3_2.py |
| MK-31 | 財は 24 品目（23 品目とサービス） | 決定 | （未確認） |
| MK-32 | 計算の量は島の大きさに合わせて縮め、重さの1トンは実物のおよそ 25 トン分とする | 仮（仮の決まりの表あり） | tests/server/test_display_units.py |
| MK-33 | 市場の記録に財ごとの値段・売買の量・買いたかった量・売りたかった量と、施設ごとに買えなかったわけ… | 決定 | tests/core/test_phase3_2.py、tests/core/test_rules.py |
| MK-34 | 計画経済の島は公定価格（値幅なし）で、混合経済の島は財ごとの値段の規制で、同じ板寄せを使う（決め… | 決定 | tests/core/test_phase3_4.py |
| MK-35 | 消費財の原価が家計の注文の一番上の段を超えると売れない件は「難しさとして残す」（注文の段はタブ … | 決定 | tests/core/test_economy.py |
| MK-36 | 消費財の指値に物価の見通しを足す | 決定（回5 で作る） | （未確認） |
| MK-37 | 家計は電気と町の資材を除く品物に、代金に見合う小売りを店から一緒に買う | 決定 | tests/core/test_balance_round7.py |
| MK-38 | 国営の施設（学校と大学も）は、市場で買いきれなかった次のターンの投入財を国の倉庫から使う | 決定 | tests/core/test_state_inputs_from_warehouse.py |
| MK-39 | 備蓄の放出の決まり: 島のどの施設も作っていない物を、前のターンに市場で買えなかった施設があれば… | 決定 | tests/core/test_state_inputs_from_warehouse.py |

## [4 仕事と家計](04-labor.md)

決まり 39 個、うち仮の決まりの表にあるもの 3 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| LB-01 | 労働者は一般と技術者の2種類 | 決定 | tests/core/test_rules.py |
| LB-02 | 雇用の段は、前のターンの人を雇い続け（多ければ遠いマスから減らす）、賃金の高い施設から通勤圏の失… | 決定 | tests/core/test_rules.py |
| LB-03 | 雇いたい人数は操業目標（工事中は資材で進められる分）で決め、払える賃金の分までに抑える | 決定 | tests/core/test_playtest_fixes.py |
| LB-04 | 技術者の席は定義の技術者数に雇いたい人数の割合を掛けた分 | 決定 | tests/core/test_playtest3_fixes.py |
| LB-05 | 賃金は施設ごと・労働者の種類ごとに毎ターン見直す（式 `wage_update`） | 決定（仮の決まりの表あり） | tests/core/test_recession_round2.py |
| LB-06 | 独立採算の施設は、今の値段で変動費の利益が 0 になる賃金より上げない | 決定 | tests/core/test_balance_round4.py |
| LB-07 | 技術者の賃金は初め一般の決めた倍率で、同じ式で動く | 決定 | （未確認） |
| LB-08 | 工事の作業員・公務員・教員・警察官は前のターンの島の平均賃金で雇う | 決定 | tests/core/test_rules.py |
| LB-09 | 賃金の段で賃金を払い、所得税を差し引いて政府に払う | 決定 | tests/core/test_rules.py |
| LB-10 | 失業手当は「失業者1人に平均賃金の決めた割合」を国庫から失業している家計の層に払う（失業した技術… | 決定 | tests/server/test_economy.py |
| LB-11 | 最低賃金は命令 `set_labor_policy` で決める（決めなければなし） | 決定 | tests/core/test_phase3_4.py |
| LB-12 | 払えなかった賃金は「未払い賃金」として払い手（政府・独立採算の施設）の負債に残し、お金が入れば新… | 決定 | tests/core/test_balance_round3.py |
| LB-13 | 倒産と撤退のとき、未払い賃金は未払いの税と貸し手より先に払われる（分け方の全体はタブ 5） | 決定 | tests/core/test_balance_round3.py |
| LB-14 | 家計の層は、一般の労働者（家族と村の住民を含む）・失業している家計・技術者（家族を含む）・資本家… | 決定 | tests/core/test_balance_round4.py |
| LB-15 | 失業している家計の人数は賃金の段の初めに雇用の結果から数え直し（村で自給する人は除く）、仕事を失… | 決定 | tests/core/test_balance_round4.py |
| LB-16 | 層を移る人（育った技術者・引退・失業と再就職・人口の増減の割り振り）は、元の層の1人あたりの預金… | 決定 | tests/core/test_balance_round4.py |
| LB-17 | 収入は、働く層が賃金・失業手当・預金の利子、資本家階級が配当と預金の利子 | 決定 | （未確認） |
| LB-18 | 層が使う額（式 `consumption`）は、直近の可処分所得の平均に層ごとの割合を掛けた分と… | 決定 | （未確認） |
| LB-19 | 使う順は、電気代（生産の段で払った分を差し引く）→ 必需品（食料・衣料 | 決定 | tests/core/test_phase3_2.py |
| LB-20 | 食料は必要な量を、質を足した栄養あたりの値段の安い順に財ごとに割り振り、前のターンに売りに出てい… | 決定 | tests/core/test_economy.py |
| LB-21 | 前のターンに食べきれなかったか飢えの蓄えが残る家計は、預金の一部を足して食費の限りにし、要る量の… | 決定 | tests/core/test_balance_round3.py |
| LB-22 | 食料が足りないときは、食費のうち足りない割合の分をサービスにもぜいたく品にも使わず手元に残す | 決定 | tests/core/test_balance_round4.py |
| LB-23 | 消費財の注文は「いつもの値段」（家計が買ったターンだけの直近の平均）の段に分けた指値で出し、今の… | 仮（仮の決まりの表あり） | tests/core/test_economy.py、tests/core/test_playtest5_fixes.py |
| LB-24 | 消費税は家計が市場で買う物（食料・衣料・ぜいたく品・サービス）と電気に、代金に上乗せして政府に払… | 決定 | （未確認） |
| LB-25 | 町の資材は、都市の発展の段が「育てる」と決めた分と住み替えの分（W-190 の回から）を、市場・… | 決定 | （未確認） |
| LB-26 | 村の仕事のない住民は自給の農業で自分の食べる分をまかない、市場で買わない | 決定 | tests/core/test_balance_round4.py |
| LB-27 | 幸福度は家計の層ごとに出して人数で平均する | 決定 | （未確認） |
| LB-28 | 建国のとき、働く層と資本家階級に1人あたりの預金を置き、一般の労働者の決めた割合を技術者にする | 決定 | tests/core/test_recession_round1.py、tests/server/test_economy.py |
| LB-29 | 財「サービス」はその場で作ってその場で使い、持ち越せず島の外へ運べない | 作り直し予定（回7） | tests/core/test_balance_round4.py |
| LB-30 | 家計は必需品を買った残りのうち所得に応じた割合をサービスに使う | 決定（回7 でも残す） | tests/core/test_balance_round4.py |
| LB-31 | 店の場所は通勤圏でいちばん人の多い集落で、雇う人数は場所ごとに買いに来る住民で割り振る | 仮（仮の決まりの表あり） | tests/core/test_balance_round4.py |
| LB-32 | 店の雇う人数の目安は直近の売上（買えなかった分も足す）× 人件費の割合 ÷ 賃金で、前のターンか… | 作り直し予定（回7） | tests/core/test_balance_round4.py |
| LB-33 | 店の運転資金は銀行から借り（審査は目安の人数での売上 − 賃金）、借りられなければ持ち主が出資す… | 決定 | （未確認） |
| LB-34 | 店は企業の施設として作り直す: 賃金で人を取り合い、電力・食料・消費財を少し使い、建設費と減価償… | 決定（自営は、商業・サービス業が店で買いきれない品物を割高に売る屋台の席で表した | tests/core/test_balance_round7.py |
| LB-35 | 家族や村の支え合い（働いている家族の稼ぎの一部が、仕事も手当もない家族の食べ物に回る）を入れる | 決定 | tests/core/test_balance_round7.py |
| LB-36 | 家計が預金から使う割合を物価上昇率と実質金利で動かし、消費財の指値に物価の見通しを足し、失業が多… | 決定（回5 で作る） | （未確認） |
| LB-37 | 統計に、層ごとの人数・可処分所得・預金・幸福度、平均賃金（一般）と技術者の賃金、実質賃金、失業率… | 決定 | tests/server/test_reports.py |
| LB-38 | 電気・送電網・原料が足りずに動けなかった分の働き手には、賃金の `labor.furlough_`… | 決定（回7 後半） | tests/core/test_balance_round7b.py |
| LB-39 | 売りに出ていたのに家計が1つも買わないターンが `household.reprice_turns`… | 決定（回7 後半 | tests/core/test_balance_round7b.py |

## [5 施設と企業](05-firms.md)

決まり 52 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| FM-01 | 施設の種類・建てる場所・働き手・作り方（レシピ）・産業の分類は定義データ（`packs/base`… | 決定 | （未確認） |
| FM-02 | プレイヤーは命令で施設を建て、取り壊す | 決定 | tests/core/test_rules.py |
| FM-03 | 稼働率は人手・投入財・電力の充足率の一番低いものと操業目標で決まる | 決定 | tests/core/test_infrastructure.py |
| FM-04 | 施設の会計は、民営（独立採算）・国営の独立採算・国営の国庫直轄の3つ | 決定 | （未確認） |
| FM-05 | （FM-36 に置き換え）施設1つがお金の主体1つで、民営の持ち主は資本家階級1つ（企業という単… | 置き換え（回7） | （未確認） |
| FM-06 | 独立採算の施設は自分の預金と借入を持ち、税引き後の利益の決めた割合を配当（民営）か国庫への納付（… | 決定 | tests/core/test_rules.py |
| FM-07 | 国庫直轄の施設は口座を持たず、売上は国庫へ、費用は国庫から予算の範囲で払う | 決定 | tests/core/test_shocks.py |
| FM-08 | 国営の独立採算の施設は民営と同じく操業目標を決めるが、自分で閉鎖せず倒産しない（返済が足りなけれ… | 決定 | （未確認） |
| FM-09 | 計画経済の島では倒産を起こさず、返済が遅れた国営施設には国営銀行が借り換えで穴を埋める | 決定 | tests/core/test_shocks.py |
| FM-10 | 設備を持たない施設（店・港・銀行・商社・運送業・海運業など）は、実際に払った建設費を決めたターン… | 作り直した（回6） | tests/core/test_balance_round6.py |
| FM-11 | 施設は建てたときの資材を設備として持ち、毎ターン傷み、傷んだ分だけ作れる量が減る | 決定 | tests/core/test_balance_round6.py |
| FM-12 | 国営の施設は国が予算から直す | 決定 | tests/core/test_balance_round6.py |
| FM-13 | 民営工場の参入は参入と撤退の段で決める | 決定 | tests/core/test_finance.py |
| FM-14 | 参入の合図は、その財が品切れ続き（足りない量が新しい工場の全力の決めた割合以上のターンが、直近の… | 決定 | tests/core/test_finance.py |
| FM-15 | 原料のどれかが島で作られていない作り方、工事の資材が島で作られず売りにも出ていない、原料が品切れ… | 決定 | tests/core/test_finance.py |
| FM-16 | 民営工場が建つのは空いた工業団地のマスだけ（農場と漁港は、指定のない人の住んでいないマスにも建つ | 決定 | tests/core/test_balance_round6.py、tests/core/test_finance.py |
| FM-17 | 建設費（1割増しと動き出しの支払い分を足す）の決めた割合を資本家階級が出資し、残りを銀行の建設資… | 決定 | tests/core/test_playtest3_fixes.py |
| FM-18 | 設備投資補助のある参入では、補助を参入のときに補助金の予算から先に出す | 決定 | tests/core/test_playtest_fixes.py |
| FM-19 | 民営工場と民間銀行が建たないわけ（工業団地がない・働き手がいない・出資が足りない・融資が断られた… | 決定 | tests/core/test_playtest_fixes.py |
| FM-20 | 撤退: 動かない（売る在庫もない）ターンが決めた数続くか、変動費の利益が決めたターン数続けて決め… | 作り直し予定（回7） | tests/core/test_balance_round6.py、tests/core/test_finance.py |
| FM-21 | 倒産: 返済が決めたターン数続けて遅れると倒産し、施設は止まり働き手は失業する | 決定 | tests/core/test_finance.py |
| FM-22 | 倒産と撤退の後、設備と在庫を売った代金は、未払い賃金 → 未払いの税 → 貸し手（貸した額の割合… | 決定 | tests/core/test_balance_round3.py |
| FM-23 | 民営の工事が決めたターン数進まなければ工事をやめる | 決定 | tests/core/test_finance.py |
| FM-24 | 民営の施設はプレイヤーが取り壊せない（国有化すれば取り壊せる） | 決定 | （未確認） |
| FM-25 | 国有化（命令）: 民営の施設を純資産（0未満なら0）で政府が買い取り、代金を国庫から資本家階級に… | 決定 | tests/core/test_finance.py |
| FM-26 | 民営化（命令）: 国営の施設を純資産で資本家階級に売る | 決定 | tests/core/test_finance.py |
| FM-27 | 補助金（命令 | 決定 | tests/core/test_finance.py |
| FM-28 | 命令 `set_zone` で陸のマスを工業団地かオフィス街に指定・解除する（首都と町のマスは指… | 決定 | tests/core/test_finance.py、tests/server/test_finance.py |
| FM-29 | 鉱山は鉱業資源ごとの作り方を持ち、島に決めた数まで（工事中も数え、民営も数える | 決定 | tests/core/test_phase3_2.py |
| FM-30 | 施設の定義に産業の分類 `industry`（農業・鉱業・鉄鋼・非鉄・機械・自動車・繊維・石油・… | 決定 | （未確認） |
| FM-31 | 経験: 島×産業ごとに施設が出した産出の倍率（稼働率×生産性）を足して数え、係数を累積の対数で上… | 決定 | tests/core/test_phase3_3.py、tests/core/test_recession_round1.py |
| FM-32 | 集積: 工場（分類が industry の施設）だけに、半径の中の同じ産業のほかの施設1つごとに… | 決定 | tests/core/test_phase3_3.py |
| FM-33 | 各産業の累積が世界1位の国に「鋼都」（鉄鋼）などの称号を付け、統計の画面に得意産業と世界の順位を… | 決定 | （未確認） |
| FM-34 | 暴動で止まるのは都市のマスの近くの施設で、農林水産業（農場・漁港・牧場・製材所）は止まらない（暴… | 決定 | tests/core/test_phase3_4.py |
| FM-35 | 商業・サービス業（島に1つの店）は倒産と撤退をせず、持ち主は経済体制の既定（細部はタブ 4 LB… | 置き換え（回7 | tests/core/test_balance_round4.py |
| FM-36 | 施設の束としての企業を主体にする | 決定 | tests/core/test_balance_round7.py |
| FM-37 | 店を企業の施設として作り直す | 決定 | tests/core/test_balance_round7.py |
| FM-38 | 家計は電気と町の資材を除く品物を店を通して買う | 決定 | tests/core/test_balance_round7.py |
| FM-39 | 百貨店の出店の規制は「規制なし・許可制・禁止」 | 決定 | tests/core/test_balance_round7.py |
| FM-40 | 企業の1つの施設が倒産すると、同じ企業の借入のある施設もみな倒産する（クロスデフォルト） | 決定 | tests/core/test_balance_round7.py |
| FM-41 | 作物替えの途中で前の作物の畑の肥料が手元になければ、その畑は肥料なしの作り方で動く | 決定 | tests/core/test_balance_round7.py |
| FM-42 | 民営の企業の施設が返済か未払いの賃金・税に足りないとき、企業の預金で足りない分は、同じ企業のほか… | 決定（回7 後半） | tests/core/test_balance_round7b.py |
| FM-43 | 自動車は部品工場（鋼鉄・アルミ・化学品・衣料・機械・電力 → 自動車部品）と組み立て工場（部品・… | 決定（回7 後半 | tests/core/test_balance_round6.py、tests/core/test_balance_round7b.py |
| FM-44 | 組の参入: 原料が島で作られていなくても、島にある原料で作れる民営の施設があれば、その施設を先に… | 決定（回7 後半） | tests/core/test_balance_round7b.py |
| FM-45 | 水力発電所の出力は、集水域（半径 4 マス | 決定（W-148） | tests/core/test_hydro.py |
| FM-46 | 施設と企業の自動の名前は、生まれたときに一度だけ決めて残す（民営の企業は創業者の姓か生まれた地名… | 決定（仮: 創業者と地元の出資の割合） | tests/core/test_company_names.py |
| FM-47 | 施設の規模（段6 A3、W-85）: 同じ施設の建っているマスに同じ施設を建てる命令は、建て増し… | 決定 | tests/core/test_scale.py |
| FM-48 | 損益の在庫の増減は原価法・低価法で数える: 施設の作る物は「1 単位の原価（変動費と設備の傷み）… | 決定 | tests/core/test_recession_round1.py |
| FM-49 | 設備投資（W-186 の直し 回3）: 民営の独立採算の施設が、直近 `firms.invest`… | 決定（回3） | tests/core/test_recession_round3.py |
| FM-50 | 農場の段階（W-190）: 農場は規模でなく段階で集約する（max_size 1 のまま） | 決定（W-190 の回で作る） | （作る回で） |
| FM-51 | 農場の機械（W-190）: 機械は段階の設備として施設の設備（FM-11）と別に持ち、傷む（寿命… | 決定・実装済み（W-190 回3） | tests/core/test_farm_grades.py |
| FM-52 | 民営の農場の集約（W-190）: 作り方を見直す周期（`recipe_turns`）ごとに、次の… | 決定・実装済み（W-190 回3） | tests/core/test_farm_grades.py、tests/core/test_stage7_fixes.py |

## [6 国の財政と政策](06-finance.md)

決まり 27 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| FS-01 | 政府のお金は中央銀行にある国庫の口座だけ | 決定 | tests/core/test_economy.py |
| FS-02 | 収入は、所得税・法人税・消費税・関税、国庫直轄の施設の売上、国営の独立採算の施設と国営銀行の納付… | 決定 | （未確認） |
| FS-03 | 所得税は賃金の段で賃金から差し引き、消費税は家計の買い物と電気の代金に上乗せし（タブ 4）、法人… | 決定 | tests/core/test_bond_gains.py |
| FS-04 | 税率は命令で変え、次の段から効く | 決定 | tests/core/test_phase3_4.py |
| FS-05 | 支出は、公務員・教員・警察官の賃金、失業手当、国庫直轄の施設の費用、公共事業、教育、治安、補助金… | 決定 | （未確認） |
| FS-06 | 予算の項目（公共事業費・住宅費・教育費・治安費・補助金・国の倉庫の買い入れ | 決定 | （未確認） |
| FS-07 | 建国のときの予算は、住民1人あたりの決めた額（教育費は学校の運営の決めた年数分、住宅費は計画経済… | 決定 | tests/core/test_public_housing.py |
| FS-08 | ターンの初めに、前のターンの実績から各項目の支払いを多めに見積もり、国庫の残高を支払いの順番に取… | 決定 | （未確認） |
| FS-09 | 支払いの順番は、国債の利払い →〔公務員・国庫直轄の施設・教育・治安〕→ 失業手当 → 昔の未払… | 決定 | tests/core/test_balance_round5.py |
| FS-10 | 払えなかった賃金は未払い賃金として政府の負債に残し、お金が入れば新しく雇う前に先に払う | 決定 | tests/core/test_balance_round3.py |
| FS-11 | 国庫が空で人を雇えない施設は、人手不足ではなく「賃金が払えない」で止まったと出す | 決定 | tests/core/test_playtest_fixes.py |
| FS-12 | 国債の段が毎ターン「次のターンの支払いの見込み＋手元に残す額−国庫の残高」を自動で発行して国庫を… | 決定 | tests/core/test_finance.py |
| FS-13 | 銀行の救済と預金保護に国庫から出すのは、次のターンの支払いの順番の決めた項目までを残した額まで | 決定 | tests/core/test_playtest3_fixes.py、tests/core/test_playtest5_fixes.py、tests/core/test_playtest_fixes.py |
| FS-14 | 政府の買い物（国庫直轄の施設の仕入れ、公共事業と学校の資材）は、ほかの買い手と同じく市場の板寄せ… | 決定 | tests/core/test_playtest3_fixes.py |
| FS-15 | 国の倉庫は政府が持つ倉庫 | 決定 | tests/core/test_infrastructure.py、tests/server/test_economy.py |
| FS-16 | 契約で届いた物は、買い手が契約ごとに「市場に出す」（既定）か「国の倉庫に入れる」かを選ぶ | 決定 | tests/core/test_playtest5_fixes.py、tests/core/test_trade.py |
| FS-17 | 失業手当は失業者1人に平均賃金の決めた割合を国庫から払う | 決定 | tests/server/test_economy.py |
| FS-18 | 産業政策は命令（行政力1、次のターンから）で決める4つの道具: 関税（輸入の財ごとの率）・輸出補… | 決定 | tests/core/test_phase3_3.py |
| FS-19 | 関税は、商社が圏の市場で買った荷を下ろすとき「約定の値段を自国通貨に直した額×率」を国庫に払う | 決定 | tests/core/test_phase3_3.py |
| FS-20 | 輸出補助は圏の市場で売れたとき国庫から商社へ、補助金の予算から払う | 決定 | tests/core/test_finance.py、tests/core/test_phase3_3.py |
| FS-21 | 民主主義の国では関税と禁輸に議会の賛成が要る（タブ 10） | 決定 | tests/core/test_phase3_4.py |
| FS-22 | 国庫が払えないとき、国債の利払いは先頭のまま払い、支払いを止めるかはプレイヤーの命令 `set_`… | 決定 | tests/core/test_balance_round5.py |
| FS-23 | 国営の施設の設備は国が予算から直す（回6 | 決定（回6 で作る） | （未確認） |
| FS-24 | 国庫のうち「支出の `bonds.buffer_turns`+1 ターン分と次の利払い」を超えた… | 決定（回5） | tests/core/test_balance_round5.py |
| FS-25 | 国の穀物の備蓄: 目安は住民の食べる量の決めたターン数分で、建国のときに国の倉庫に入れる | 決定 | tests/core/test_recession_round1.py |
| FS-26 | 住宅費（W-190・W-202）: 国が町の床を足す資材（命令「住宅を建てる」と、計画経済の国の… | 決定（命令の工事と計画経済の町は回2 で作った | tests/core/test_public_housing.py、tests/core/test_stage7_fixes.py |
| FS-27 | 公営住宅の家賃（W-190・W-202）: 国の床に住む層は、床 1 人分ごとに命令で決める名目… | 決定（家賃の徴収は回2 で作った | tests/core/test_public_housing.py |

## [7 お金と銀行](07-banking.md)

決まり 48 個、うち仮の決まりの表にあるもの 1 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| BK-01 | お金を持つ主体は、家計の3つの層・独立採算の施設・市中銀行・政府・中央銀行の5種類 | 決定 | tests/core/test_economy.py |
| BK-02 | お金は `ledger.post` と `ledger.pay` だけで動かす | 決定 | tests/core/test_economy.py |
| BK-03 | お金が生まれ・消えるのは、銀行の貸し出しと返済、中央銀行の国債の売買・引き受け・貸し出し・利子・… | 決定 | （未確認） |
| BK-04 | 記録の段の最後に、貸し借りの一致・残高と仕訳の一致・お金の増減・預金と国庫がマイナスでないことを… | 決定 | tests/core/test_economy.py |
| BK-05 | 金額は通貨の最小単位の整数、金利は年率の1万分の1で持つ | 決定 | （未確認） |
| BK-06 | 市中銀行はオフィス街のマスに入る施設で職員を雇う | 決定 | tests/core/test_playtest5_fixes.py |
| BK-07 | 国営銀行はプレイヤーが建設の命令で建てる | 決定 | tests/core/test_finance.py、tests/core/test_playtest5_fixes.py、tests/core/test_playtest_fixes.py |
| BK-08 | 家計の層は毎ターン預金の一部を「預金金利−危なさ」の高い銀行へ移す | 決定 | （未確認） |
| BK-09 | 預金金利は「政策金利−差」へ少しずつ近づけ、手元の流動性（準備預金の余りと国債の額面）が足りない… | 決定 | tests/core/test_shocks.py |
| BK-10 | 融資は建設資金（据え置きの後に均等に返す）と運転資金（短く返し、貸し手と借り手ごとに1本で回す）… | 決定 | tests/core/test_finance.py |
| BK-11 | 審査は、現金で見た営業の利益が返済の決めた倍以上で、借入が資産の決めた割合以下 | 決定 | （未確認） |
| BK-12 | 銀行が貸せるのは、貸した後も自己資本比率が規制以上で、1つの民営施設への残高が大口融資の規制以内… | 決定 | tests/core/test_finance.py |
| BK-13 | 運転資金を借りきれない分は、国営施設なら政府が出資し、民営施設なら変動費の利益が出ている間だけ資… | 決定 | （未確認） |
| BK-14 | 返済日に預金が足りなければ払える分だけ払って遅れを数え、続けて決めた回数で倒産 | 決定 | tests/core/test_finance.py、tests/core/test_shocks.py |
| BK-15 | 混合経済では国営銀行が基幹産業に利ざやなしで貸す（政策融資） | 決定 | （未確認） |
| BK-16 | 自己資本比率は「自己資本÷融資の残高」 | 決定 | （未確認） |
| BK-17 | 銀行の損益は利子の差−職員の賃金−貸し倒れ＋国債の実現損益 | 決定 | tests/core/test_stage7_fixes.py、tests/server/test_finance.py |
| BK-18 | 準備預金は預金の決めた割合を最低限として中央銀行に置き、余りには「政策金利−差」の利子が付く | 決定 | （未確認） |
| BK-19 | 中央銀行は島に1つの帳簿だけの主体 | 決定 | tests/core/test_finance.py、tests/core/test_phase3_3.py |
| BK-20 | 独立させる・戻すのは命令で、切り替えた後の決めたターン数は戻せない | 決定 | tests/core/test_playtest_fixes.py |
| BK-21 | 銀行間の貸し借りは中央銀行の段で、金利を値段にした板寄せ（政策金利の上下の幅） | 決定 | （未確認） |
| BK-22 | 借りきれない銀行には中央銀行が担保を取って上端の金利で貸す | 決定 | （未確認） |
| BK-23 | 中央銀行の利益は決算で全額を国庫に納める | 決定 | （未確認） |
| BK-24 | 国債の期間は3つ（1・5・10年） | 決定 | tests/core/test_codex_turn300_fixes.py、tests/core/test_playtest4_fixes.py |
| BK-25 | 発行はすべて市中銀行の入札に出す | 決定 | tests/core/test_balance_round5.py |
| BK-26 | 市場の利回りは式 `bond_yield`（政策金利＋期間・返す力・物価・不履行の上乗せ−独立の… | 決定 | tests/core/test_balance_round5.py、tests/core/test_finance.py |
| BK-27 | 利回りには上限があり、張り付いても何も起きない | 仮（仮の決まりの表あり） | （未確認） |
| BK-28 | 新しい発行の売れ残りは、「引き受ける」の政策で中央銀行が独立していなければ中央銀行が同じ利回りで… | 決定 | tests/core/test_finance.py、tests/core/test_playtest3_fixes.py |
| BK-29 | 満期の国債の中央銀行の持ち分は同じ期間の国債に乗り換え、借り換えの売れ残りは独立していても中央銀… | 決定 | tests/core/test_rules.py |
| BK-30 | 国債の売買の市場は残りの期間の4区分ごとの、利回りを値段にした板寄せ | 決定 | tests/core/test_playtest_fixes.py |
| BK-31 | 国債は時価で持つ | 決定 | tests/core/test_bond_gains.py、tests/core/test_playtest3_fixes.py |
| BK-32 | 資本取引を認めた国の銀行は、外国の利回りが値下がりの見込みを超えて高い通貨の外貨と国債を、預金の… | 決定 | tests/core/test_phase3_3.py |
| BK-33 | 債務不履行は命令 `set_bond_payment` で支払いを止めたときだけ、すべての銘柄を… | 決定 | tests/core/test_phase3_3.py、tests/core/test_shocks.py、tests/server/test_finance.py |
| BK-34 | 不履行の罰は3つ: 利回りの上乗せ（だんだん減る）、決めたターン数は銀行が国債を買わない、損をし… | 決定 | tests/core/test_playtest3_fixes.py、tests/server/test_finance.py |
| BK-35 | 自己資本がマイナスか資金繰りが破綻した銀行は破綻し、新しい融資と預金の受け入れを止める | 決定 | tests/core/test_finance.py、tests/core/test_playtest5_fixes.py |
| BK-36 | 次のターンまでに選ばなければ引き継ぎにし、引き継いでも規制を下回らない銀行がなければ救済にする | 決定 | tests/core/test_shocks.py |
| BK-37 | 引き継ぎと清算の損失は、保護されない預金と銀行間の貸し手が同じ割合で負い、残りを預金保険（政府）… | 決定 | tests/core/test_codex2_fixes.py、tests/core/test_finance.py、tests/core/test_playtest4_fixes.py |
| BK-38 | 預金は1人あたり（施設と銀行は1つあたり）上限額まで全額を守る | 決定 | （未確認） |
| BK-39 | 引き継ぐ健全な銀行のない島で引き継ぎか清算を選ぶと、政府が銀行を引き取り、出資を消して損失を同じ… | 決定 | tests/core/test_playtest_fixes.py |
| BK-40 | 救済と預金保護に国庫から出すのは、次のターンの優先の支払いを残した額まで | 決定 | tests/core/test_codex2_fixes.py、tests/core/test_playtest_fixes.py |
| BK-41 | 銀行ごとに求める利回りは、市場の利回りに国債への偏り（国債の額面÷資産が決めた割合を超えた分）の… | 決定 | tests/core/test_balance_round5.py |
| BK-42 | 回5 で、刷ったお金が物価に効く経路を入れる（預金から使う割合を物価と実質金利で動かす・指値に物… | 決定（回5 で作る） | tests/core/test_shocks.py |
| BK-43 | 計画経済の国は中央銀行を独立させられない（命令は通らない） | 決定（回5） | tests/core/test_balance_round5.py |
| BK-44 | 独立した中央銀行は国債の売買の市場で売り買いする | 決定（回5） | tests/core/test_balance_round5.py |
| BK-47 | 家計の住宅ローン（W-186 の直し 回3）: 町の床を足す資材の代金の `housing.lt`… | 決定（回3） | tests/core/test_recession_round3.py |
| BK-48 | 中央銀行の買いが値幅の中で買いきれなければ、値幅を越えて利回りを下げて買う（W-193 | 決定（回3） | tests/core/test_recession_round3.py |
| BK-45 | 直近 1 年の利益がマイナスの銀行は、職員の下限 `banking.staff_floor` を… | 決定（回7 後半） | tests/core/test_balance_round7b.py、tests/core/test_playtest5_fixes.py |
| BK-46 | 破綻の前の合併: 直近 1 年の利益がマイナスで、自己資本比率が「規制＋`banking.ris`… | 決定（回7 後半） | tests/core/test_balance_round7b.py |

## [8 インフラと教育](08-infrastructure.md)

決まり 27 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| IN-01 | インフラは道路・鉄道・上下水道・送電網の4種で、マスごとに種類ごとの整備度と維持で戻す目標を持つ | 決定 | tests/server/test_reports.py |
| IN-02 | 工事は命令 `build_infrastructure`（行政力1）で種類と決めた数までのマスを… | 決定 | tests/server/test_reports.py |
| IN-03 | 公共事業は島に1つにまとめて動かす | 決定 | tests/core/test_infrastructure.py |
| IN-04 | 作業員は施設が雇った後の失業者から、通勤圏を見ずに島のどこからでも島の平均賃金で雇う（引き抜かな… | 決定 | （未確認） |
| IN-05 | 資材は国の倉庫の分を先に使い、足りない分を市場で買う | 決定 | tests/core/test_infrastructure.py |
| IN-06 | 島で手に入らない資材を使う種類は、代わりの資材（`fallback`）で工事する | 決定 | tests/core/test_infrastructure.py |
| IN-07 | 工事は雇えた人手とそろった資材の少ない方の割合だけ進む | 決定 | （未確認） |
| IN-08 | 整備度は毎ターン種類ごとの傷みだけ下がり、公共事業費から目標まで戻す | 決定 | tests/core/test_infrastructure.py |
| IN-09 | 工事の資材・運賃・作業員の賃金は工事の部門（GDP の投資）に数え、インフラは政府の資産として記… | 決定 | （未確認） |
| IN-10 | 景気対策の特別な決まりは置かない | 決定 | （未確認） |
| IN-11 | 生産性の係数は f = 1 + w ×（low + (high − low) × L − 1） | 決定 | tests/core/test_infrastructure.py |
| IN-12 | インフラの L は交通（道路と鉄道の高い方）だけで、送電網は入れない | 決定 | tests/core/test_infrastructure.py |
| IN-13 | 交通の整備度はマスを越える道のりを短くし（道路より鉄道が短い）、運賃の率の道のりはその最短の道で… | 決定 | tests/core/test_infrastructure.py |
| IN-14 | 送電網の整備度が決めた値以上のマスと、施設と町のマスがつながって1つの送電網になる | 決定 | tests/core/test_rules.py |
| IN-15 | 閉鎖・倒産・取り壊し・破綻処理で施設がなくなったマスには送電線を残す（施設のないマスの取り壊しは… | 決定 | tests/core/test_finance.py、tests/core/test_rules.py |
| IN-16 | 上下水道は、町が都市・大都市に育つ条件（タブ 2 PO-20）、幸福度（PO-34）、亡くなる割… | 決定 | tests/core/test_balance_round4.py |
| IN-17 | 建国と移し替えのとき、施設と町のマスとその周りに道路、送電線のマスに送電網、都市と大都市のマスに… | 決定 | tests/core/test_infrastructure.py、tests/core/test_playtest3_fixes.py |
| IN-18 | 学校と大学は民間が建てない国庫直轄の施設 | 決定 | （未確認） |
| IN-19 | 教育水準は「働き手のうち学校で学んだ人の割合」という人の蓄え | 決定 | tests/core/test_infrastructure.py |
| IN-20 | 若い人の学んだ割合は、子どもたちが学校に通えた割合（定員×稼働率÷子どもの数）で、通う年数をかけ… | 決定 | tests/core/test_infrastructure.py |
| IN-21 | 教育水準は施設の生産性（教育の係数）、幸福度、生まれる割合に効く | 決定 | tests/core/test_balance_round4.py、tests/core/test_infrastructure.py |
| IN-22 | 大学は1ターンに「育てる数×稼働率×入学の割合」を一般の労働者から技術者にし、技術者は決めた割合… | 決定 | tests/core/test_infrastructure.py、tests/core/test_playtest3_fixes.py |
| IN-23 | 入学の割合は技術者の失業率が上がるほど下がる（育ちすぎて失業する技術者が増え続けないように） | 決定 | tests/core/test_infrastructure.py |
| IN-24 | 技術者は島全体の人数で、通勤圏を見ずに島のどこへでも通う | 決定 | tests/core/test_infrastructure.py、tests/core/test_playtest3_fixes.py |
| IN-26 | 役所の決まった事業: 命令の工事がなくても、役所が上下水道の足りない町（育たない町と、人が密に住… | 決定（回4） | tests/core/test_recession_round4.py |
| IN-27 | 役所が並べた工事は、命令で並べた工事の後に進め、資材は国の倉庫から使わずに市場で買う（倉庫は遊ぶ… | 決定（回4） | tests/core/test_recession_round4.py |
| IN-25 | 島の地図の重ね表示に、インフラの整備度（種類ごと）・運賃の率・工事中の印を出す | 決定 | tests/server/test_reports.py |

## [9 貿易と為替](09-trade.md)

決まり 51 個、うち仮の決まりの表にあるもの 3 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| FX-01 | 島をまたぐ処理は全島の段（`collect` → `resolve` → `apply`）で書き… | 決定 | tests/core/test_phase3_3.py |
| FX-02 | 通貨は島ごとに1つで、番号は島の番号 | 決定 | tests/server/test_trade.py |
| FX-03 | 外国の主体が持つ自国通貨は、発行した島の帳簿の非居住者の預金（持ち手 `nr:<島>:<主体>`… | 決定 | tests/core/test_trade.py |
| FX-04 | 外貨の持ち高は量と帳簿の値打ち（手に入れたときの自国通貨の額）を持ち、売ったときの差を為替の実現… | 決定 | （未確認） |
| FX-05 | 世界の確かめ（`trade_check`）: どの組でも「Y が持つ X の外貨 = X の帳簿… | 決定 | tests/core/test_phase3_3.py |
| FX-06 | 島の外との物の出入りはすべて港を通り輸送船で運ぶ（海外の商人だけは要らない） | 決定 | tests/core/test_trade.py |
| FX-07 | 海運業は島に1つでマスを持たず、輸送船を設備として持つ | 決定 | tests/core/test_trade.py |
| FX-08 | 荷は買う側の島の船で運ぶ | 決定 | tests/core/test_trade.py |
| FX-09 | 船は燃料か石炭の、今の値段で安い方で動き、船出のときに往復の分を使う | 決定 | tests/core/test_trade.py |
| FX-10 | 運賃は「荷1トン×区画1つ」あたりで、直近の費用（船員の賃金・燃料・船の傷み）÷ 運んだ量 ×（… | 決定 | tests/core/test_trade.py |
| FX-11 | 船は決めたターン数使ったら解体する | 決定 | tests/core/test_balance_round6.py、tests/core/test_trade.py |
| FX-12 | 国は命令で貿易圏を作り、作った国が代表 | 決定 | tests/core/test_trade.py、tests/server/test_phase3_3.py |
| FX-13 | 代表は別の加盟国に譲れ、代表の島が休止中ならほかの加盟国が引き継げる | 決定 | tests/core/test_trade.py |
| FX-14 | 圏の市場は圏×財ごとに貿易の段で板寄せ（値段は決済通貨） | 決定 | tests/core/test_trade.py |
| FX-15 | 圏内の固定価格のある財は値幅0で板寄せし、余った側を量に比例して分ける | 決定 | tests/core/test_phase3_3.py |
| FX-16 | 商社はオフィス街のマスに建ち島にいくつも置ける独立採算の施設 | 決定 | tests/core/test_trade.py |
| FX-17 | 商社の指値: 輸出は圏の前の値段を自国通貨に直して取り分と港の使用料を引き輸出補助を足した値段ま… | 決定 | tests/core/test_phase3_3.py |
| FX-18 | 商社の狙う量は財ごとに、売れた量と足りない量の分け前へ毎ターン少しずつ近づける | 決定 | tests/core/test_playtest5_fixes.py |
| FX-19 | 輸入の指値は圏の値幅の上端まで | 決定 | tests/core/test_playtest5_fixes.py、tests/server/test_trade.py |
| FX-20 | 売れ残った荷は決めたターン数の後に指値を外して売る | 決定 | （未確認） |
| FX-21 | 島で買い手がいるのに売りに出ていない物は、前のターンに圏の市場に売りがあった財だけ、輸入が引き合… | 決定 | tests/core/test_phase3_2.py、tests/core/test_trade.py |
| FX-22 | 民営の商社は、島の商社の扱った重さの平均が上限の決めた割合を超え、空いたオフィス街があり、資本家… | 決定 | tests/core/test_trade.py |
| FX-23 | 二国間契約は一方の政府が「相手・財・1ターンの量・値段・通貨（どちらかの国）・期間」を示し、相手… | 決定 | tests/core/test_trade.py |
| FX-24 | 売り手の国は国の倉庫から出し、足りなければ島の市場で優先して買う | 決定 | tests/core/test_trade.py |
| FX-25 | 契約で届いた物の行き先は買い手が契約ごとに選ぶ: 市場に出す（既定 | 決定 | tests/core/test_playtest5_fixes.py、tests/core/test_trade.py |
| FX-26 | 物の援助は値段0で相手の承諾の要らない片道の契約として船で運び、お金の援助は国庫から一度だけ送る | 決定 | tests/core/test_phase3_3.py |
| FX-27 | 海外の商人は、貿易の仕組みを置いたターン（建国か移し替え）から決めたターン数だけ、決まった値段と… | 仮（仮の決まりの表あり） | tests/core/test_trade.py |
| FX-28 | レートは「決済通貨1単位が自国通貨でいくらか」 | 決定 | tests/core/test_trade.py |
| FX-29 | 注文を出すのはその島の主体: 商社（次の数ターンの輸入の分を残して売り買い）・政府（契約の支払い… | 決定 | tests/core/test_trade.py |
| FX-30 | 中央銀行は受け身の注文を出す: 前のレートより安い側で市場の厚みの分を買い、高い側で持っている分… | 決定 | tests/core/test_codex_turn300_fixes.py、tests/core/test_playtest5_fixes.py、tests/core/test_trade.py |
| FX-31 | 政府は外貨建ての契約のそのターンに払う分を、中央銀行が持っているその通貨から今のレートで先に受け… | 決定 | tests/core/test_playtest5_fixes.py、tests/core/test_playtest_round1_trade.py |
| FX-32 | 片側だけの注文は、相手のない量 ÷（相手のない量＋市場の厚み）の割合だけ、出ている一番よい指値へ… | 決定 | tests/core/test_trade.py |
| FX-33 | 同じ通貨の組のレートは1つ（島 A の板で決まったレートの逆数を島 B の板にも入れる） | 決定 | （未確認） |
| FX-34 | 受け身の買いは、中央銀行の外国の資産から融通で借りている額を引いた正味が年の GDP の決めた割… | 仮（仮の決まりの表あり） | tests/core/test_phase3_3.py |
| FX-35 | 中央銀行どうしの融通: 受け身の売りに要る外貨が足りない中央銀行は、同じ圏で余っている加盟国の中… | 決定 | tests/core/test_phase3_3.py、tests/core/test_trade.py |
| FX-36 | 融通で借りられるのは年の GDP の決めた割合まで、発行国が作って貸すのはその国の年の GDP … | 仮（仮の決まりの表あり） | tests/core/test_phase3_3.py |
| FX-37 | 介入: 中央銀行は命令で通貨ごとに毎ターン買う・売る額を決め、買いは値幅の上端・売りは下端の指値 | 決定 | tests/core/test_phase3_3.py |
| FX-38 | 固定相場: 決めたレートの幅の端で、下に量の限りのない買い、上に持っている外貨の全部の売りを出す | 決定 | tests/core/test_phase3_3.py |
| FX-39 | 中央銀行が独立しているときは、介入・固定相場・中央銀行の外国の国債の命令をやめる | 決定 | tests/core/test_phase3_3.py |
| FX-40 | 資本取引は自由・政府と中央銀行だけ・禁止の3つ（既定は経済体制の `capital_contro`… | 決定 | tests/core/test_phase3_3.py |
| FX-41 | 外国の国債は発行国の通貨で、入札と売買の市場に外国の買い手が加わる | 決定 | tests/core/test_phase3_3.py |
| FX-42 | 資本取引が自由な国の市中銀行は、利回り − 値下がりの率が自国より決めた差以上高い通貨の外貨と国… | 決定 | tests/core/test_phase3_3.py |
| FX-43 | 国際収支は、島をまたぐお金がすべて通る `money.transfer` で仕訳の種類ごとに集め… | 決定 | tests/core/test_phase3_3.py、tests/server/test_phase3_3.py |
| FX-44 | 建国と移し替えで、海運業（輸送船と燃料つき）・首都に一番近い海沿いの港・首都の近くのオフィス街の… | 決定 | tests/core/test_trade.py |
| FX-45 | 貿易の画面・為替の画面・世界マップは、自国には全部を、他国には国の番号と公表するものだけを出す | 決定 | tests/server/test_trade.py |
| FX-46 | サーバーが島を作る（建国・サンプルの島）ときに、港と商社を置く（`founding.open_f`… | 決定（W-140） | tests/core/test_trade.py |
| FX-47 | 倒産して差し押さえられた商社だけが残る島も、動いている商社がない島として開き直す | 決定（W-140） | tests/core/test_trade.py |
| FX-48 | 商社が雇う人数は、直近 `trade.entry_turns` ターンの扱った重さの平均が扱える… | 決定（W-157） | tests/core/test_trade.py |
| FX-49 | 海外の商人は売りと同じ品目を、初めの値段 × `foreign_merchant.buy_mul`… | 仮 | tests/core/test_recession_round4.py |
| FX-50 | 戦争で港が封鎖された島には、海外の商人が封鎖の強さ（`military.naval.blocka`… | 仮 | tests/core/test_recession_round4.py |
| FX-51 | 暴動・災害・占領で止まっている商社（施設の止まりの期間）は、人が来ないので新しい注文を出さない:… | 決定（W-159） | tests/core/test_trade.py |

## [10 政治と体制](10-politics.md)

決まり 57 個、うち仮の決まりの表にあるもの 2 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| PL-01 | 政治体制は「政治の仕組み（民主主義・権威主義・全体主義）」と「国家元首の形（君主制・共和制）」の… | 決定 | tests/core/test_phase3_4.py |
| PL-02 | 体制の特色は `regimes.yaml` の `traits` に書き、軸ごとに選んだ体制の特… | 決定 | tests/server/test_phase3_4.py |
| PL-03 | 政情は島ごとの 0〜100 の数で、名前付きの式 `stability` の目標 T* に、毎タ… | 決定 | tests/core/test_phase3_4.py |
| PL-04 | 目標は 50 ＋ 幸福度 − 失業率 − 物価上昇率 − 格差（それぞれ決めた境を超えた分に重み… | 決定 | tests/core/test_phase3_4.py |
| PL-05 | 出来事の補正: 債務不履行（だんだん戻る）、体制の変更の混乱期間、首都の政府の建物が壊れている間… | 決定 | tests/core/test_migration.py |
| PL-06 | 段階は落ち着いている・デモ・暴動・反乱の前触れの4つ | 決定 | tests/core/test_phase3_4.py |
| PL-07 | 暴動と反乱の前触れでは、町以上の住宅地の近くの施設を乱数で選んで次のターンから数ターン止め（農林… | 決定 | tests/core/test_log_messages.py、tests/core/test_phase3_4.py |
| PL-08 | 壊れた施設は、建てるときの資材と人手の決めた割合を国の工事として使って直す | 決定 | tests/core/test_phase3_4.py |
| PL-09 | 警察署（公務の施設）の警官は治安費から払い、住民あたりの決めた人数がいれば、止まる施設と壊れる見… | 決定 | tests/core/test_phase3_4.py、tests/server/test_phase3_4.py |
| PL-10 | 報道と集会は自由・制限・統制の3段（命令） | 決定 | tests/core/test_phase3_4.py |
| PL-11 | 支持率は政情に実質賃金の1年の伸びを足し、失業率の1年の伸びを引いたもの | 決定 | tests/core/test_phase3_4.py |
| PL-12 | 民主主義の島は決めたターンごとに選挙を自動で行い、支持率を与党の議席の割合にする | 決定 | tests/core/test_phase3_4.py |
| PL-13 | 議会の要る命令（`orders.yaml` の `parliament`）は実行するときに採決す… | 決定 | tests/core/test_phase3_4.py |
| PL-14 | 議会が要る命令は、税率の大きな変更・国有化・民営化・中央銀行の独立の切り替え・関税・禁輸・資本取… | 決定 | tests/core/test_phase3_4.py |
| PL-15 | 否決された命令も行政力を使い、何も変えず、わけをターンログに書く | 決定 | tests/core/test_phase3_4.py |
| PL-16 | 共和制は選挙で支持率が決めた値を超えると、次の決めたターン数だけ R を足す | 決定 | tests/core/test_phase3_4.py |
| PL-17 | 君主制は R を足し、君主の年齢をデータで持つ | 決定（出来事は後から） | （未確認） |
| PL-18 | 憲法改正と経済体制の変更は重い命令（民主主義では議会の3分の2） | 決定 | tests/core/test_phase3_4.py |
| PL-19 | 政治体制を変えると首都の建物を建て替える | 決定 | tests/core/test_phase3_4.py、tests/server/test_phase3_4.py |
| PL-20 | 計画経済に移ると、民営の施設と民間銀行は帳簿の純資産を国債で払って国有化する | 決定 | tests/core/test_phase3_4.py |
| PL-21 | 体制の特色: 民主主義は政情が下がる動きが遅く R が正、権威主義は行政力 ＋1 と国の工事の工… | 仮（仮の決まりの表あり） | tests/core/test_phase3_4.py |
| PL-22 | 経済体制の特色: 計画経済は重点産業の工期と経験の伸びが良く、ぜいたく品の種類の満足が少ない | 仮（仮の決まりの表あり） | （未確認） |
| PL-23 | 労働の動員（全体主義の命令）: 失業者を国営施設に最低賃金（なければ平均賃金の決めた割合）で割り… | 決定 | tests/core/test_phase3_4.py |
| PL-24 | 計画経済の島は公定価格で値幅0の板寄せ（電力も） | 決定 | tests/core/test_phase3_4.py |
| PL-25 | 据え置く財（既定は食料）は原価を下回っても据え置き、差を国庫から補助金で埋める（払えなければ量に… | 決定 | tests/core/test_phase3_4.py |
| PL-26 | 命令: 公定価格の改定（名指しした財を今の原価に | 決定 | tests/core/test_phase3_4.py |
| PL-27 | 割当ルール: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事 → 家計（配給）→ … | 決定 | tests/core/test_phase3_4.py |
| PL-28 | 配給: 家計の中はお金の多さでなく1人あたり同じ量まで | 決定 | tests/core/test_phase3_4.py |
| PL-29 | 生産目標: 国営施設ごとに、直近の公定価格での需要（買えなかった分と、国の工事とインフラの資材の… | 決定 | tests/core/test_phase3_4.py |
| PL-30 | 重点産業は命令で産業の分類を決めた数まで選ぶ | 決定 | tests/core/test_phase3_4.py |
| PL-31 | 計画経済の貿易は国営の商社の独占: 行列のある財を輸入し、売れ残った財を値段によらず輸出する | 決定 | tests/core/test_phase3_4.py |
| PL-32 | 混合経済はどの財にも値段の上限と下限を決められる | 決定 | tests/core/test_phase3_4.py、tests/server/test_phase3_4.py |
| PL-33 | 闇市は入れない | 決定（後から） | （未確認） |
| PL-34 | 移民の出国の政策は政治の仕組みで上限がある: 民主主義は自由だけ、権威主義は許可制まで、全体主義… | 決定 | tests/core/test_migration.py |
| PL-35 | 国どうしのやりとりは、非公開の公電（送るとすぐ届き、発信国と宛先国の持ち主だけが読める）と公開の… | 決定 | tests/server/test_letters.py |
| PL-36 | 公電は1つの島が24時間に送れる数に上限があり（サーバーの設定）、管理画面で非表示にできる | 決定 | tests/server/test_letters.py |
| PL-37 | 外交の決まりは遊ぶ人に任せる | 決定（①と②で作った） | tests/core/test_diplomacy.py |
| PL-38 | 反乱と内戦はフェーズ4 | 決定（フェーズ4 で作る） |  |
| PL-39 | 政治の画面に政情・目標の内訳・段階・支持率・議席・採決の履歴・混乱期間・建て替えの資材を出す | 決定 | tests/server/test_phase3_4.py |
| PL-40 | 宣戦布告は重い命令（行政力 2、民主主義は議会の過半数、伏せない） | 決定 | tests/core/test_diplomacy.py |
| PL-41 | 条約の提案・署名・断り・取り下げ・予告つきの破棄 | 決定 | tests/core/test_diplomacy.py |
| PL-42 | 停戦は戦っている相手と両国の署名で効き、予告のターンがたつと戦争に戻る | 決定（引き揚げと捕虜は期限と印を記録するだけ | tests/core/test_diplomacy.py |
| PL-43 | 条約の違反（結んでいる不可侵・同盟を破って宣戦する・賠償を期日までに送らない）は島に記録され、国… | 決定 | tests/core/test_diplomacy.py |
| PL-44 | 国際会議場は公開の声明の場 | 決定 | tests/server/test_diplomacy.py |
| PL-45 | 経済の取り決め（貿易圏・二国間契約・固定相場・中央銀行の融通・援助・移民の受け入れ）は、条約の条… | 決定 | tests/core/test_diplomacy_economic.py |
| PL-46 | 貿易圏の条項: 提案した国が代表になる圏をつくって相手を入れるか、提案した国が代表の圏（承認制で… | 決定 | tests/core/test_diplomacy_economic.py |
| PL-47 | 契約の条項: 効くと、売る国・買う国・財・1 ターンの量・値段・通貨・期間・届いた物の行き先で、… | 決定 | tests/core/test_diplomacy_economic.py |
| PL-48 | 固定相場と融通の条項: 固定相場は、固定する国の中央銀行が相手の通貨を決めたレートに固定する（独… | 決定 | tests/core/test_diplomacy_economic.py |
| PL-49 | 援助の条項: 送る国が、決めた間隔（1〜`max_aid_period_turns` ターン）で… | 決定 | tests/core/test_diplomacy_economic.py |
| PL-50 | 移民の条項: 人の行き来の自由は、両国が移民の受け入れ（政策）を open に保つ約束、難民の受… | 決定（現実との違い: 相手を限った受け入れは持たない） | tests/core/test_diplomacy_economic.py |
| PL-51 | 掲示板は板（雑談・外交・質問・攻略・運営からの告知 | 決定（W-147） | tests/server/test_board.py |
| PL-52 | 書き込みの名前欄は、空なら「名無し」、`名前#秘密` で秘密から作ったトリップ（◆の印）が付く | 決定（W-147） | tests/server/test_board.py |
| PL-53 | 本文の `>>番号`（`>>3-5` も）は同じスレッドの書き込みへのリンクになり、指を置く（ス… | 決定（W-147） | tests/server/test_board.py |
| PL-54 | NG は人・ID・言葉で決める | 決定（W-147） | tests/server/test_board.py |
| PL-55 | 書き込みの歯止め（いいね・通報にも同じ待ち）: 登録してから `HAKONIWA_BOARD_N`… | 決定（W-147） | tests/server/test_board.py |
| PL-56 | 休止中の島とも、停戦と、休止中の島が賠償を払わず捕虜を返す講和は結べる | 決定（W-96） | tests/core/test_diplomacy.py |
| PL-57 | 計画経済の島の国営施設は人を抱える（W-198）: 操業目標が下がっても、前のターンに働いていた… | 決定 | tests/core/test_recession_round2.py |

## [11 画面と統計](11-ui.md)

決まり 56 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| UI-01 | 画面はサーバーが Django のテンプレートで HTML を作り、島の俯瞰図・建国・グラフ・命… | 決定 | tests/server/test_phase1.py、tests/server/test_trade.py |
| UI-02 | 画面と API はルールの計算をしない | 決定 | （未確認） |
| UI-03 | ほかの国の画面も開けるが、見せるのは国全体の統計と公開の数だけ | 決定 | tests/server/test_phase3_2.py、tests/server/test_reports.py、tests/server/test_trade.py |
| UI-04 | 待っている命令はほかの国の人にも見せるが、現実の国が前もって公表しない命令（`orders.ya`… | 決定 | tests/server/test_ai_players.py、tests/server/test_review_fixes.py |
| UI-05 | 命令の確かめ（`orders/check`）と1マスの命令の入力に使う値は島の持ち主だけ（ほかの… | 決定 | tests/server/test_phase1.py、tests/server/test_web.py |
| UI-06 | 統計は記録の段がそのターンの仕訳・約定・市場と、段が残したお金と物の流れから島ごとに作り、名前（… | 決定 | tests/server/test_reports.py |
| UI-07 | GDP は生産の側（施設の付加価値、工事と公務員と教員の賃金）と支出の側（家計の消費・投資・在庫… | 決定 | tests/core/test_measure.py |
| UI-08 | 物価指数は家計が買う品目の値段を、直近に買った量で重みづけしたもの | 決定 | tests/core/test_shocks.py |
| UI-09 | 格差は資本家階級と労働者階級の 1 人あたり可処分所得の比 | 決定 | tests/server/test_reports.py |
| UI-10 | 財政の収入・支出・収支と層ごとの 1 人あたりの所得は保存せず、統計の系列を返すときに計算して足… | 決定 | tests/server/test_reports.py |
| UI-11 | 記録を残す期間: 仕訳と約定は短く、施設の帳簿は 200 年分、市場の記録と統計とターンログはず… | 決定 | （未確認） |
| UI-12 | 巻き戻しでは、戻すターンより後の統計・ターンログ・記録を消し、勘定・融資・国債はバックアップの写… | 決定 | tests/server/test_economy.py、tests/server/test_phase1.py |
| UI-13 | 画面のある時点は「箱庭暦32年11月（ターン 383）」のように日付を主にしてターンを添える | 決定 | tests/server/test_calendar.py |
| UI-14 | グラフは自前の小さな折れ線（`static/js/chart/`）で、横軸は暦の年と月 | 決定 | （未確認） |
| UI-15 | 島の俯瞰図はマスをひし形に並べ、奥から手前へ描く | 決定 | tests/server/test_web.py |
| UI-16 | 地図の重ね表示は 1 種類 1 ファイル（`map/overlays/`）で足せる | 決定 | tests/server/test_ai_parity.py |
| UI-17 | 絵はテーマごとに置き、ないものは色付きのひし形と箱で描く | 決定 | tests/server/test_theme_sprites.py |
| UI-18 | 世界マップは区画ごとに島の形を 1 マス 1 ピクセルで描き、気候帯・貿易圏の縁取り・港・荷のあ… | 決定 | tests/server/test_phase3_2.py、tests/server/test_trade.py |
| UI-19 | 統計の順位は部門ごと（GDP・1 人あたり GDP・人口・幸福度・輸出額）で誰にでも見せる | 決定 | tests/server/test_phase3_3.py |
| UI-20 | 得意産業と称号（各産業の経験の累積の世界 1 位）は誰にでも見せる | 決定 | tests/server/test_phase3_3.py |
| UI-21 | 産業の鎖の画面（持ち主だけ）は品目ごとに作った量・使った量・売買・輸出入・値段と作る施設と使う施… | 決定 | tests/server/test_phase3_2.py |
| UI-22 | ターンログは文の種類と引数（名前）だけを保存し、文面は表示のときに作る | 決定 | tests/core/test_log_messages.py |
| UI-23 | 島の画面と国の要約の「注意」は同じ文で、画面の値を言葉にしただけ（先の見込みは入れない） | 決定 | tests/server/test_codex_turn300_fixes.py |
| UI-24 | 手引き（`/help/`）の施設と命令の表は定義データから作る | 決定 | tests/server/test_phase3_2.py、tests/server/test_trade.py |
| UI-25 | スマートフォンでは CSS だけで縦画面に合わせ、地図は指で動かし拡大縮小する | 決定 | （未確認） |
| UI-26 | AI は AI 用の鍵で入る | 決定 | tests/server/test_ai_players.py |
| UI-27 | AI の鍵を持つ国と、鍵を最後に使ってから 1 年の間の国には、国の一覧と国の画面に「AI」の印… | 決定 | tests/server/test_ai_players.py |
| UI-28 | 国の要約と前のターンからの差分は、画面と同じ値を集めて短くしたもの | 決定 | tests/server/test_ai_players.py |
| UI-29 | MCP のつなぎ先は状態を持たず、道具はサーバーの中で JSON API を同じアカウントで呼ぶ… | 決定 | tests/server/test_ai_players.py |
| UI-30 | 命令・画面・画面に出す値・地図の重ね表示を足したり変えたりしたら、同じ PR で JSON AP… | 決定 | tests/server/test_ai_parity.py |
| UI-31 | 管理は Django の管理サイトで行う: 更新の一時停止、巻き戻し、何ターンかまとめて進める、… | 決定 | tests/server/test_country.py、tests/server/test_letters.py、tests/server/test_turns.py、tests/server/test_web.py |
| UI-32 | 同じ接続元からの登録は管理者が設定で数を決める（家族・学校・携帯回線で 1 つの接続元を分け合う… | 決定 | tests/server/test_phase1.py |
| UI-33 | 量は実物に近い単位で見せる（計算は変えない） | 決定 | tests/server/test_display_units.py |
| UI-34 | トップ画面は島の順位の表（列の頭で並べ替え、既定は GDP の順 | 決定 | tests/server/test_top_page.py |
| UI-35 | 島の画面のマスの情報は、押したマスのそばの小さな窓（ポップオーバー）に出す | 決定 | tests/server/test_map_popover_pies_wording.py |
| UI-36 | 全体を分けた割合を見る統計（人口の階級ごとの内訳・歳入と歳出の内訳・産業ごとの従業者数）は円グラ… | 決定 | tests/server/test_map_popover_pies_wording.py |
| UI-37 | 表示名は統計の正式な言い方にそろえる（労働力人口・就業者・完全失業率・自給農家・従業者、労働者階… | 決定 | tests/server/test_map_popover_pies_wording.py |
| UI-38 | 国の公開ページ（`/islands/<番号>/country/`、JSON API `GET /`… | 決定（4-1 ③） | tests/server/test_country.py |
| UI-39 | 国ごとの紀年は表示だけで、世界の暦（ターン）は変わらない | 決定（4-1 ③） | tests/server/test_country.py |
| UI-40 | 放置された国（命令が途絶えた国）は、自動で休止にも無人にもしない | 決定（4-1 ③） | tests/server/test_country.py |
| UI-41 | 世界の近況（`/news/`、JSON API `GET /api/news?page=`、MC… | 決定（4-1 ③） | tests/server/test_news_awards.py |
| UI-42 | 賞は記録を持たず、そのつどターンログと統計から数える（巻き戻せば賞も巻き戻る） | 決定（4-1 ③） | tests/server/test_news_awards.py |
| UI-43 | 記念碑は命令 `build`（`facility` に `monument_…` | 決定（4-1 ④） | tests/server/test_monuments.py |
| UI-44 | 財政の画面の「予算と使った額」（と、予算の命令の入力欄）に、項目ごとに目安を出す | 決定（画面の直し W-143） | tests/server/test_reports.py |
| UI-45 | 不具合の報告: ログインした人は画面のどこからでも「不具合を報告」を開ける | 決定（画面の直し W-146） | tests/server/test_feedback.py |
| UI-46 | 掲示板の画面（`/board/` 板の一覧と最近のスレッド、`/board/b/<板>/` スレ… | 決定（W-147） | tests/server/test_ai_parity.py、tests/server/test_board.py |
| UI-47 | ログインの試しすぎには上限を置く（通常のログインと管理画面のログインの両方、同じ記録を使う） | 決定（10-26、ChatGPT が書いた依頼を本人が貼ったもの） | tests/server/test_rate_limits.py |
| UI-48 | AI 用の鍵の処理数の上限は、HTTP の送信数ではなく処理の数で数える: `/api/` の呼… | 決定（10-26、同上） | tests/server/test_rate_limits.py |
| UI-49 | 回数制限が使う接続元は、つないできた相手の IP（`REMOTE_ADDR`）が既定 | 決定（10-26、同上） | tests/server/test_rate_limits.py |
| UI-50 | 住まいと農場の段階（W-190 の回で作る）: 命令 `build_housing`（町のマスか… | 決定（W-190 の回で作る） | tests/server/test_ai_parity.py |
| UI-51 | 手引きの図: 手引きは図（SVG）で全体の流れ・町・農場・行政力・軍・政情・貿易・国債を見せる | 決定（2026-10-26、本人「図とか絵を使って分かりやすくしてほしい」） | tests/server/test_help_figures.py |
| UI-52 | 建国から 1 年（`GUIDE_TURNS` = 12 ターン）のあいだ、島の画面の上と国の要約… | 決定（2026-10-29、プレイヤーの声「起動して何をやれば良いのか分かりにくい」） | tests/server/test_first_steps.py |
| UI-53 | 手引きは 3 つ: 入門（`/help/start/` | 決定（2026-10-29、本人のカード「入門と詳しい仕組みに分ける」） | tests/server/test_help_start.py |
| UI-54 | 企業の一覧は誰にでも見せる: 他国の施設の画面と国の要約に企業の名前・所有・施設の数と種類・働く… | 決定 | tests/server/test_firm_lists.py |
| UI-55 | アカウントの画面（右上のログイン名から）で、ログイン名とパスワードを変え、国を手放して建て直せる | 決定 | tests/server/test_account.py |
| UI-56 | 市場の値段には種類（約定・気配・持ち越し・未観測）と最後に売買が成立したターンを付け、0 や取引… | 決定（2026-10-31、W-171） | tests/server/test_market_trade_screens.py |

## [12 確かめ方](12-checks.md)

決まり 43 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| GT-01 | 同じ種なら同じ結果になる | 決定 | tests/core/test_engine.py、tests/core/test_gate.py |
| GT-02 | 帳簿の確かめは毎ターン記録の段の最後に行い、崩れていればそのターンを失敗にして、サーバーは何も保… | 決定 | tests/core/test_economy.py、tests/server/test_turns.py |
| GT-03 | 島をまたぐお金（外貨の持ち高と非居住者の預金、融通、外国の国債）と運ぶ途中の荷の突き合わせは、毎… | 決定 | tests/core/test_phase3_3.py |
| GT-04 | DB を通して回したターンと、メモリだけで回したターンは同じ結果になる | 決定 | tests/server/test_phase1.py |
| GT-05 | 部品の試験: 板寄せは乱数の注文で「約定量が最大・値幅を守る・指値より不利な約定がない・取り置い… | 決定 | tests/core/test_economy.py |
| GT-06 | 一括シミュレーションは画面も DB もなしで同じ段を回す | 決定 | tests/core/test_measure.py |
| GT-07 | 関門は 50 島 × 300 ターンを標準の計画で回し、目安を見始めるターンから後の記録で目安に… | 決定 | tests/core/test_gate.py |
| GT-08 | GDP の生産の側と支出の側の差は、2 つの絶対値の大きい方（小さすぎる額より小さくしない）で割… | 決定 | tests/core/test_measure.py |
| GT-09 | 値段の目安は、値段にターンを付けて記録し、目安を見る期間の値段だけで判定する | 決定 | tests/core/test_measure.py |
| GT-10 | 目安の種類: 値段・物価上昇率・物価の振れ・失業率・金利の上下・人口の下限と上限と急な変化・GD… | 決定 | tests/core/test_measure.py、tests/core/test_phase3_2.py、tests/core/test_phase3_3.py |
| GT-11 | 目安から外れたら、目安をゆるめて合格にせず、仕組みから直す（数字を式・上限・倍率で寄せない） | 決定 |  |
| GT-12 | 場面は、標準の計画・命令なし・中央銀行を独立させた国（計画経済の国は独立させられないので除く）・… | 決定 | tests/core/test_measure.py、tests/core/test_nightly.py |
| GT-13 | 組は 2 つ | 決定 | tests/core/test_measure.py |
| GT-14 | 測りと点検はゲームの状態を読むだけで変えない（同じ種で状態の指紋が同じ） | 決定 | tests/core/test_measure.py |
| GT-15 | 最初の 1 年の縮みは、12 ターン目の雇用と実質の生産の 1 ターン目との比で記録だけする（合… | 決定 | tests/core/test_measure.py |
| GT-16 | 失業の内わけ: 島×ターンごとに働き手の行き先、施設が人を雇わなかった理由、通勤圏で求人と失業者… | 決定 | tests/core/test_measure.py |
| GT-17 | 揺さぶりの試験（フェーズ2）: 刷りすぎ・利上げ・銀行の破綻・国債の不履行・発電所の停止・計画経… | 決定 | tests/core/test_shocks.py |
| GT-18 | 揺さぶりの試験（フェーズ3）: 全部の国から禁輸された島・外貨準備の少ない国の固定相場・中央銀行… | 決定 | tests/core/test_phase3_3.py、tests/core/test_phase3_4.py |
| GT-19 | 揺さぶりの試験（フェーズ3 の残り）: 決済通貨の島の生産を止める、船を全部失った島 | 決定 | tests/core/test_balance_round6.py |
| GT-20 | 夜の点検の長い年月: 標準の計画・命令なし・少ない島の世界を 100 年回し、測りの範囲より後に… | 決定 | tests/core/test_nightly.py |
| GT-21 | 夜の点検のでたらめな国: 一部の島が毎ターン、画面と同じ確かめを通ったでたらめな命令を出す | 決定 | tests/core/test_nightly.py |
| GT-22 | 夜の点検は前の夜までに見つけたものと比べ、新しいものだけに印を付ける | 決定 | tests/core/test_nightly.py |
| GT-23 | 重い計算（長い試し回し・全部の試験・測り・夜の点検）は X870 で回す | 決定 |  |
| GT-24 | 長い試し回し（測り・関門・夜の点検）で崩れが main より増えたら、増えた分を直してからマージ… | 決定 |  |
| GT-25 | 規模の目標: 既定の 50 島で 1 回のターン更新を 5 分以内にする | 決定 | （未確認） |
| GT-26 | 標準の計画は測りと自動の試験と開発用のサンプルの島だけで使い、サーバーの世界の国は動かさない（命… | 決定 | tests/core/test_gate.py |
| GT-27 | 標準の計画は毎ターン上から見て最初に足りないもの（食料・発電・工事の資材・投入財・消費財・使って… | 決定 | tests/core/test_autoplan_budget.py、tests/core/test_gate.py |
| GT-28 | 標準の計画の世界の側: 北と南に 1 つずつ誰でも入れる貿易圏を作り、真ん中の行の島は両方に入れ… | 決定 | tests/core/test_trade.py |
| GT-29 | 標準の計画の飢饉の手: 食べ物の充足が下がった月は国営の農場を穀物に替え、国の倉庫の食べ物を出し… | 決定 | tests/core/test_balance_round5.py |
| GT-37 | 標準の計画の財政の手: 国が賃金を払いきれなければ所得税と消費税を少しずつ上げ、ゆとりが戻れば建… | 決定 | tests/core/test_balance_round5.py |
| GT-30 | 設計書の決まりの一覧と索引がずれていない、番号の頭・試験のパス・設定の名前が正しい、段の表が g… | 決定 | tests/core/test_docs_match.py、tests/core/test_spec_design.py、tests/core/test_spec_settings.py |
| GT-31 | 試し遊びは AI 用の鍵で入り、国の要約と差分か MCP で遊ぶ | 決定 |  |
| GT-32 | 崩れの見張り: 測りと夜の点検（長い年月）は、1 ターン目から島ごとに飢饉・国の出入りを除いた人… | 決定 | tests/core/test_collapse.py |
| GT-33 | 関門の分け方: マージの判断には崩れ（GT-32）とターンの停止を使い、main より増えたかを… | 決定 | tests/core/test_collapse.py |
| GT-34 | 崩れた世界の写し: 崩れごとに、崩れが始まったターンの少し前（`gate.py` の `SNAP`… | 決定 | tests/core/test_collapse.py |
| GT-35 | 試験の書き方: 仕組みの試験は、その仕組みを作った場面（足りない・0・一部だけなど）を組み立てて… | 決定 |  |
| GT-36 | 島の種類ごとに全画面を開く試験: 建国直後・人のいない島・銀行のない島・計画経済・戦争中・占領・… | 決定 | tests/server/test_all_screens.py |
| GT-42 | 同じ形を 1 か所で作る（止まった理由を持ち主と原因の表から出す、名前と座標の文を 1 つの関数… | 後回し（タブ 14 W-34） |  |
| GT-38 | 回7 の目安と見張り: 働く人のうちサービス（商業・金融・運輸・商社・公務・教育・治安）で働く割… | 決定 | tests/core/test_balance_round7.py |
| GT-39 | 関門の値段の幅は、サービスと小売りだけ物価指数でなく平均賃金の伸びで割って見る（賃金が物の値段よ… | 決定（回7 後半） | tests/core/test_gate.py |
| GT-40 | 標準の計画は、島で作っていない投入財でも、作る施設の投入財が手元にあれば手に入ると見て使う施設（… | 決定（回7 後半） | tests/core/test_balance_round7b.py |
| GT-41 | 測りは、計算の中身（hakoniwa_core・game.yaml・packs・schemas）… | 決定 | tests/core/test_measure_reuse.py |
| GT-43 | 試験は回す場所と順によらず同じ結果になる（Linux でも Windows の X870 でも、… | 決定 | tests/core/test_playtest_fixes.py、tests/server/test_board.py、tests/server/test_theme_sprites.py |

## [15 軍事と戦争](15-military.md)

決まり 67 個、うち仮の決まりの表にあるもの 3 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| MI-01 | ベータ版の軍は陸軍と海軍だけ | 決定（4-2 で作る）（仮の決まりの表あり） |  |
| MI-02 | 国と国の間は「平時」か「戦争」 | 決定（4-1 の①で作った |  |
| MI-03 | 宣戦布告は重い命令（民主主義は議会の過半数） | 決定（4-1 の①で作った |  |
| MI-04 | 停戦は両国の署名で効く（片方の国の宣言だけでは止まらない | 決定（4-1 の①で作った |  |
| MI-05 | 講和は両国が署名する条約で、戦争を終え、相手の島にいる部隊を決めたターンのうちに引き揚げさせ、占… | 決定（4-1 の①で作った（仮の決まりの表あり） |  |
| MI-06 | 条約の軍事の条項（不可侵・同盟・講和）は、ゲームが止めも強いもしない | 決定（4-1 の①で作った |  |
| MI-07 | 建国の島は軍を持たない（国防費は 0） | 決定（4-2 の②で作った |  |
| MI-08 | 兵士は軍が雇う公務の働き手で、賃金は国防費から払う | 決定（4-2 の②で作った |  |
| MI-09 | 徴兵は命令で入れ、決めた人数を家計の層から人数の割合でくじで集め、賃金は志願より低い | 決定（4-2 の②で作った |  |
| MI-10 | 兵士の食べ物と燃料と弾薬は軍が持つ（家計の食べ物の数からその人数を外す） | 決定（4-2 の②で食べ物を作った |  |
| MI-11 | 新しい兵は決めたターンの訓練を終えるまで練度が低い | 決定（4-2 の②で作った |  |
| MI-12 | 除隊は命令で、除いた兵は家計に戻り失業者になる | 決定（4-2 の②で作った |  |
| MI-13 | 国防費は予算の 2 つの項目（兵の賃金と食べ物の `defense_personnel`、装備の… | 決定（4-2 の②で国防費の 2 つの項目と命令 set_defense を作った |  |
| MI-14 | 賃金が払えないと士気が下がり、士気が低い部隊からは毎ターン兵が逃げて家計に戻る | 決定（4-2 の②で賃金の遅れ・士気・逃亡・食べ物の不足を作った |  |
| MI-15 | 新しい品目: 銅鉱石・銅・電子機器・武器・弾薬・軍用車両・軍艦・フリゲート・潜水艦・ミサイル・迎… | 決定（4-2 の①で銅鉱石・銅・電子機器・武器・弾薬・軍用車両・軍艦・フリゲート・潜水艦を作った |  |
| MI-16 | 新しい施設: 銅の精錬所・電子機器工場・兵器工場（武器と弾薬）・ミサイル工場・核施設（国だけが持… | 決定（4-2 の①で銅の精錬所・電子機器工場・兵器工場と、自動車工場・造船所の作り方を作った |  |
| MI-17 | 武器・弾薬・軍用車両・軍艦・フリゲート・潜水艦・ミサイル・迎撃ミサイル・核弾頭・ウラン鉱石は貿易… | 決定（4-2 の①で作った |  |
| MI-18 | 部隊は陸の歩兵・機甲と、海の艦隊（小さな軍艦・フリゲート・潜水艦） | 決定（4-2 の③で陸の歩兵・機甲を作った |  |
| MI-19 | 部隊は命令「編成」で作る | 決定（4-2 の③で作った |  |
| MI-20 | 基地の施設: 兵舎（陸の部隊が平時にいる所）・軍港（艦隊の母港 | 決定（4-2 の③で兵舎・軍港・レーダー・要塞の施設と陣地（命令 dig_trench |  |
| MI-21 | 島の中の動きは1ターンに進めるマスの数が決まっていて、道路の整備度で増え、山と森で減る | 決定（4-2 の③で陸の動きを作った |  |
| MI-22 | 島をまたぐのは海だけ | 決定（4-2 の③'で、軍が持つ輸送船団に載せ降ろしを作った |  |
| MI-23 | 船の道は世界マップの区画を出発から行き先へ直線にたどる | 決定（4-2 の③'で艦隊の航海を作った |  |
| MI-24 | 揚陸は相手の港か、海に接する平地のマスにする | 決定（4-3 の①で作った |  |
| MI-25 | 相手の島にいる部隊の補給は、船で港か揚陸した所へ運んだ分と、占領したマスで取り立てた分だけ | 決定（4-3 の①で作った |  |
| MI-26 | 見える範囲はサーバーが計算する: 自分の島の全部、自分の部隊と艦隊の周り（種類ごと）、レーダーの… | 決定（4-2 の③'で作った |  |
| MI-27 | 動きと攻撃の命令は、現実の国が前もって公表しない命令として伏せる | 決定（4-2 の③'で作った |  |
| MI-28 | 陸の戦闘は、戦争の相手の部隊が同じマスにいるとき起きる | 決定（4-3 の①で作った（仮の決まりの表あり） |  |
| MI-29 | 士気が崩れた側は隣の味方のマスへ退き、退く所がなければ降伏して捕虜になる | 決定（4-3 の①で作った |  |
| MI-30 | 戦場のマスの住民の一部が亡くなり、残りは同じ島のほかの町か、受け入れる国へ逃げる（難民） | 決定（4-3 の①で作った分: 戦場のマスの住民の一部が亡くなり、残りは同じ島のほかの町へ逃げ、… |  |
| MI-31 | 海の戦いは、戦争の相手の艦隊どうしが同じ区画にいて、どちらかが相手を見つけたとき起きる | 決定（4-3 の②で作った |  |
| MI-32 | 護衛は艦隊を自国の海運業の船団に付ける命令 | 決定（4-3 の②で作った |  |
| MI-33 | 通商破壊は艦隊と潜水艦にいくつかの区画を見張らせる命令 | 決定（4-3 の②で作った |  |
| MI-34 | 封鎖は相手の島の区画に艦隊を置く命令 | 決定（4-3 の②で作った |  |
| MI-35 | 海運業は沈んだ船を船の傷みと同じく費用に数え、運賃に乗せる | 決定（4-3 の②で作った |  |
| MI-36 | 占領: 戦闘の後に戦争の相手の部隊だけがいるマスは占領される | 決定（4-3 の①で作った |  |
| MI-37 | ミサイルはミサイル工場で作り、ミサイル基地に置いて、命令「ミサイル攻撃」で戦争の相手の島のマスを… | 決定（4-3 の③a で作った |  |
| MI-46 | 核兵器: ウラン鉱石（限られた島の鉱業資源）を、国だけが持てる核施設（民営工場の参入と民営化の命… | 決定（4-3 の③b で作った |  |
| MI-47 | 核を持たない・使わない約束は条約の条項として結べる（2 国の間、または国際会議場で多くの国が署名… | 決定（4-3 の③b で作った |  |
| MI-48 | 船は航海の間、区画ごとの位置を持つ（貿易の船も） | 決定（4-3 の②で作った |  |
| MI-49 | 捕虜は捕らえた国の国防費で食べさせ、元の島の人口に「捕虜」の印を付けて数える | 決定（4-3 の①で作った |  |
| MI-50 | 1 ターンの中の順は、島の段 `military`（募集・編成・維持・除隊）を雇用の段の前、全島… | 決定（4-2 の②で島の段 military、③で全島の段 operations を、市場の段の… |  |
| MI-51 | 戦争の相手でない国の島には揚陸できない | 決定（4-2 で作る） |  |
| MI-52 | 部隊と艦隊への命令（動き・護衛・見張り・封鎖・揚陸・ミサイル攻撃）は行政力を使わず、部隊が持ち続… | 決定（4-2 の③で作った |  |
| MI-53 | 兵と捕虜は出身のマスの人口から抜き、島全体の数（兵・捕虜）として持つ | 決定（4-2 の②で兵を作った |  |
| MI-54 | 部隊・艦隊・戦争の状態・占領・見た記録は世界の状態（WorldState）に持ち、写し・巻き戻し… | 決定（4-2 の③で部隊を世界の状態 `world.ext["military"]` に持った |  |
| MI-55 | 軍の統計・ターンログ・戦いの記録は、見る人の見える範囲で絞る（持ち主には全部、ほかの国には国全体… | 決定（4-2 の③'で、ログと統計と要約の出し分けを作った |  |
| MI-56 | 貿易の荷が船の上にあるのは帰りの片道だけ | 決定（4-3 の②で作った |  |
| MI-57 | 運賃は「島の運賃の率＋道ごとの戦争の危険の上乗せ」にし、商社の指値の運賃の見込みも道ごとにする（… | 決定（4-3 の②で運賃の上乗せまで作った: 船出の運賃に区画ごとの見込みを乗せる |  |
| MI-58 | 降伏と反乱で体制が倒れた国は「選び直し待ち」になり、専用の命令 1 つで全部の軸を決める | 決定（4-3 の①で降伏の分を作った |  |
| MI-59 | 持ち主のいない島は軍を持たず、ゲームは守りを動かさない（守るのは警察だけ） | 決定（4-2 で作る） |  |
| MI-60 | 前からある世界の銅鉱石とウラン鉱石の鉱脈は、世界の種と島の番号だけで決め、移し替えの版の印で 1… | 決定（4-2 の①で銅を作った |  |
| MI-61 | 戦争の難民: 島の中の避難は `operations` の段がほかの町へ移し、国外へは移民の段に… | 決定（4-3 の①で島の中の避難を、③c で国外への難民を作った |  |
| MI-62 | 核施設とミサイル基地は、建てるときに「隠す」を選べる（地下に造る） | 決定（4-3 の③c で作った |  |
| MI-63 | 駐留: 同盟の条約に駐留の条項を書くと、相手の国の兵舎と軍港に部隊と艦隊を平時でも置ける | 決定（4-2 で作る） |  |
| MI-64 | 外国の島にいる部隊（駐留・揚陸・占領）は、その島の人口・家計・雇用・市場に入らない | 決定（4-2 で作る） |  |
| MI-65 | 軍の命令の一覧 | 決定（核を積む指定は 4-3 の③b で作った |  |
| MI-66 | 測りの戦争の場面（`war`）: 標準の計画の世界で、3 組の島が戦争をする | 決定（段6 A2 で作った | tests/core/test_security_plan.py |
| MI-38 | 防空陣地は周りのマスへ来たミサイルを、迎撃ミサイルの数と電子機器の経験で落とす | 決定（4-3 の③a で作った |  |
| MI-39 | 反乱: 政情が反乱の前触れのまま決めたターン続くと、反乱軍が首都から遠い町に部隊として現れる | 決定（4-4 の②で作った |  |
| MI-40 | 反乱軍は島の中の主体で、ゲームが動かす | 決定（4-4 の②で作った |  |
| MI-41 | 反乱軍が首都を落とすと体制が倒れ、持ち主が体制を選び直し、混乱期間に入る（タブ 10 PL-18… | 決定（4-4 の②で作った |  |
| MI-42 | 戦争の政情: 宣戦の後は一時だけ政情の目標が上がり、それから自国の戦死者の人口に対する割合 r … | 決定（4-3 の①で作った |  |
| MI-43 | 電子機器は銅と化学品と機械から作り、レーダー・ミサイル・軍艦・潜水艦・軍用車両の材料にする | 決定（4-2・4-4 の①で作った |  |
| MI-44 | 標準の計画は自分から宣戦せず、核施設を建てない（使われていない鉱業資源に施設を建てる方針から核施… | 決定（4-3 の①で守りを作り、段6 A2 で警察署を足した | tests/core/test_security_plan.py |
| MI-45 | 戦争・部隊・艦隊・占領・見張り・ミサイルの命令と状態は、画面と同じものを JSON API・MC… | 決定（4-2 の③'で艦隊の分を作った |  |
| MI-67 | 陸の部隊の大きさは国の大きさの倍（PO-49）に合わせる: 部隊 1 つの兵・装備・重さを倍にし… | 決定 | tests/core/test_scale.py |

