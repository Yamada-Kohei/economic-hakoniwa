# 設計書の決まりの索引

このファイルは `python tools/spec/design_index.py write` で作ります。手で直さないでください。
決まりの全文は各ファイルの「決まりの一覧」にあり、`python tools/spec/design_index.py show <番号>` でも出せます。
決まりはここでは頭の一文だけを、長いものは途中まで載せています。

## [1 全体とターンの流れ](01-overview.md)

決まり 27 個、うち仮の決まりの表にあるもの 1 個。

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
| OV-26 | ベータ版は VPS 1 台で動かし、世界を複数動かすときは DB とサービスを世界ごとに分ける | 決定 | （未確認） |
| OV-27 | 登録は誰でもできる | 決定 | tests/server/test_web.py |

## [2 島と町と人口](02-population.md)

決まり 45 個、うち仮の決まりの表にあるもの 1 個。

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
| PO-14 | 初めの食料の施設は、災害で漁港か農場が 1 つ止まっても食べ物が半分を切らないよう、農場を多めに… | 仮（仮の決まりの表あり） | tests/core/test_founding.py |
| PO-15 | 建国のお金は、建国の国債 → 家計への給付 → 出資 → 銀行の国債買い、の順の仕訳で作る | 決定 | tests/core/test_founding.py、tests/core/test_infrastructure.py |
| PO-16 | 町（住宅）は自動で建つ施設 `housing.facility` で、段階（村・町・都市・大都市… | 決定 | tests/core/test_playtest3_fixes.py |
| PO-17 | 町ができる・育つのは、人手の足りない施設（店が雇いきれなかった集落を含む）の通勤圏で、空いている… | 決定 | tests/core/test_rules.py |
| PO-18 | 育つのは人数が上限の `housing.fill_ratio` を超えた町で、新しくできる数と育… | 決定 | tests/core/test_rules.py |
| PO-19 | 育つ資材は一般の層が市場で買い（預金の `household.housing_share` まで… | 決定 | tests/core/test_playtest3_fixes.py、tests/core/test_rules.py |
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
| PO-32 | 生まれたマスに住む所がなければ島の空いた町へ移り、どこにも空きがなければ生まれない | 決定 | tests/core/test_balance_round4.py |
| PO-33 | 人口の増減は家計の層の人数に比例して割り振り、資産を持つ層は割合を保つ | 決定 | （未確認） |
| PO-34 | 幸福度は式 `happiness`（`population.happiness`）で層ごとに出し… | 決定 | tests/core/test_phase3_4.py、tests/core/test_playtest3_fixes.py |
| PO-35 | 前のフェーズから移し替えた島は、幸福度の重みをその時の重みから `population.blen`… | 決定 | tests/core/test_playtest3_fixes.py |
| PO-36 | 汚染は施設が稼働率に応じて `facilities.yaml` の pollution の量を … | 決定 | tests/core/test_phase3_2.py |
| PO-37 | 災害は 1 島 1 ターンに `disasters.rate` の確率で起き、施設 1 つが種類… | 決定 | tests/core/test_playtest3_fixes.py、tests/core/test_rules.py |
| PO-38 | 国をまたぐ移民は、暮らしの見込み V（実質賃金 × 仕事の見つかる割合 × 食べられた割合）の対… | 決定 | tests/core/test_migration.py |
| PO-39 | 動く組は仕事のある一般の家計・失業している家計・技術者の家計で、資産を持つ層は動かない | 決定 | tests/core/test_migration.py |
| PO-40 | 受け入れは 1 年に住民の上限（既定 `migration.default_cap`）と町の空き… | 決定 | tests/core/test_migration.py |
| PO-41 | 出国を止められた人のうち国境（`migration.border` × 警察の充足）をすり抜けた… | 決定 | tests/core/test_migration.py |
| PO-42 | 移った人は行き先の層と空いた町に入り、預金を元の島の通貨のまま持って行く（資本取引が禁止なら政府… | 決定 | tests/core/test_migration.py |
| PO-43 | 移った人のうち働いている人は賃金の `migration.remittance` を元の島の一般… | 決定 | tests/core/test_migration.py |
| PO-44 | 二国の間の移動の自由の協定と、難民を受け入れる約束は、条約の命令として結べるようにする | 決定（フェーズ4 で作る） |  |
| PO-45 | 長く操作しない島は自動で消さず休止のまま残し、管理者が管理画面で扱う | 決定 | （未確認） |

## [3 物と市場と値段](03-market.md)

決まり 36 個、うち仮の決まりの表にあるもの 2 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| MK-01 | 取引は「島×財」ごとに、ターンの市場の段で1回の板寄せで決める | 決定 | tests/core/test_economy.py |
| MK-02 | 値段は前のターンの約定価格から1ターンに値幅までしか動かない | 決定 | tests/core/test_economy.py |
| MK-03 | 約定価格ちょうどの注文と、値幅の端で止まったときの多い側は、優先順位の高い順、同じ順位の中は量に… | 決定 | tests/core/test_economy.py |
| MK-04 | 売りと買いが交わらなければ約定せず、値幅の中の気配を次のターンの基準の値段にする | 決定 | tests/core/test_economy.py |
| MK-05 | 注文は出すときに「量×指値」のお金を取り置き、取り置けない分は出せない | 決定 | tests/core/test_economy.py |
| MK-06 | 注文を出すのは、独立採算と国庫直轄の施設（在庫の売りと次のターンの投入財の買い）、工事中の施設と… | 決定 | tests/core/test_balance_round3.py |
| MK-07 | 希望価格は売れ行きで見直す（式 `price_update`） | 決定 | tests/core/test_economy.py |
| MK-08 | 独立採算の売り手は1単位の変動費（投入財・賃金・電気代・運賃）より安い指値を出さない | 決定 | （未確認） |
| MK-09 | 操業目標は、売れ行きの見込み（前のターンに売れた量へ少しずつ近づける）と在庫の目安との差で決め、… | 決定 | tests/core/test_rules.py |
| MK-10 | 持ち越せない売れ残りを翌ターンに指値なしで売った分は、値段が変動費を下回った幅が値幅に届くまで、… | 決定 | tests/core/test_economy.py |
| MK-11 | 市場の段の後に、持ち越せる期間（goods.yaml の `shelf_life`）を過ぎた在庫… | 決定 | tests/core/test_playtest4_fixes.py、tests/core/test_rules.py |
| MK-12 | 原料が足りずに稼働率が下がった施設は、前のターンに買うつもりだった分の原料がそろっていれば、止ま… | 決定 | tests/core/test_playtest3_fixes.py |
| MK-13 | 品切れの財（買いたい人がいるのに売りに出なかった財）は、島で作り始められる一番安い値段が今の値段… | 決定 | tests/core/test_balance_round4.py |
| MK-14 | 島に買い手がなく売りだけの財は、圏で輸出が引き合う値段まで値幅ずつ下がる | 決定 | tests/core/test_trade.py |
| MK-15 | 前のターンに食べきれなかった家計は、食費の限りで値幅の上端を超えて食料を買い上がる（形はタブ 4… | 決定 | tests/core/test_balance_round3.py |
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
| MK-29 | 初めの値段は、建国と移し替えのときに定義データのレシピから「原価＋上乗せ」で解く | 決定 | tests/core/test_rules.py |
| MK-30 | 前のフェーズから移し替えた島で値段のない新しい財には、島の初めの値段に今の物価指数の倍率を掛けた… | 決定 | tests/core/test_phase3_2.py |
| MK-31 | 財は 24 品目（23 品目とサービス） | 決定 | （未確認） |
| MK-32 | 量は島の大きさに合わせて縮め、重さの1トンは実物のおよそ 25 トン分とする | 仮（仮の決まりの表あり） | （未確認） |
| MK-33 | 市場の記録に財ごとの値段・売買の量・買いたかった量・売りたかった量と、施設ごとに買えなかったわけ… | 決定 | tests/core/test_phase3_2.py、tests/core/test_rules.py |
| MK-34 | 計画経済の島は公定価格（値幅なし）で、混合経済の島は財ごとの値段の規制で、同じ板寄せを使う（決め… | 決定 | tests/core/test_phase3_4.py |
| MK-35 | 消費財の原価が家計の注文の一番上の段を超えると売れない件は「難しさとして残す」（注文の段はタブ … | 決定 | tests/core/test_economy.py |
| MK-36 | 消費財の指値に物価の見通しを足す | 決定（回5 で作る） | （未確認） |

## [4 仕事と家計](04-labor.md)

決まり 37 個、うち仮の決まりの表にあるもの 3 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| LB-01 | 労働者は一般と技術者の2種類 | 決定 | tests/core/test_rules.py |
| LB-02 | 雇用の段は、前のターンの人を雇い続け（多ければ遠いマスから減らす）、賃金の高い施設から通勤圏の失… | 決定 | tests/core/test_rules.py |
| LB-03 | 雇いたい人数は操業目標（工事中は資材で進められる分）で決め、払える賃金の分までに抑える | 決定 | tests/core/test_playtest_fixes.py |
| LB-04 | 技術者の席は定義の技術者数に雇いたい人数の割合を掛けた分 | 決定 | tests/core/test_playtest3_fixes.py |
| LB-05 | 賃金は施設ごと・労働者の種類ごとに毎ターン見直す（式 `wage_update`） | 決定（仮の決まりの表あり） | （未確認） |
| LB-06 | 独立採算の施設は、今の値段で変動費の利益が 0 になる賃金より上げない | 決定 | tests/core/test_balance_round4.py |
| LB-07 | 技術者の賃金は初め一般の決めた倍率で、同じ式で動く | 決定 | （未確認） |
| LB-08 | 工事の作業員・公務員・教員・警察官は前のターンの島の平均賃金で雇う | 決定 | tests/core/test_rules.py |
| LB-09 | 賃金の段で賃金を払い、所得税を差し引いて政府に払う | 決定 | tests/core/test_rules.py |
| LB-10 | 失業手当は「失業者1人に平均賃金の決めた割合」を国庫から失業している家計の層に払う（失業した技術… | 決定 | tests/server/test_economy.py |
| LB-11 | 最低賃金は命令 `set_labor_policy` で決める（決めなければなし） | 決定 | tests/core/test_phase3_4.py |
| LB-12 | 払えなかった賃金は「未払い賃金」として払い手（政府・独立採算の施設）の負債に残し、お金が入れば新… | 決定 | tests/core/test_balance_round3.py |
| LB-13 | 倒産と撤退のとき、未払い賃金は未払いの税と貸し手より先に払われる（分け方の全体はタブ 5） | 決定 | tests/core/test_balance_round3.py |
| LB-14 | 家計の層は、一般の労働者（家族と村の住民を含む）・失業している家計・技術者（家族を含む）・資産を… | 決定 | tests/core/test_balance_round4.py |
| LB-15 | 失業している家計の人数は賃金の段の初めに雇用の結果から数え直し（村で自給する人は除く）、仕事を失… | 決定 | tests/core/test_balance_round4.py |
| LB-16 | 層を移る人（育った技術者・引退・失業と再就職・人口の増減の割り振り）は、元の層の1人あたりの預金… | 決定 | tests/core/test_balance_round4.py |
| LB-17 | 収入は、働く層が賃金・失業手当・預金の利子、資産を持つ層が配当と預金の利子 | 決定 | （未確認） |
| LB-18 | 層が使う額（式 `consumption`）は、直近の可処分所得の平均に層ごとの割合を掛けた分と… | 決定 | （未確認） |
| LB-19 | 使う順は、電気代（生産の段で払った分を差し引く）→ 必需品（食料・衣料 | 決定 | tests/core/test_phase3_2.py |
| LB-20 | 食料は必要な量を、質を足した栄養あたりの値段の安い順に財ごとに割り振り、前のターンに売りに出てい… | 決定 | tests/core/test_economy.py |
| LB-21 | 前のターンに食べきれなかったか飢えの蓄えが残る家計は、預金の一部を足して食費の限りにし、要る量の… | 決定 | tests/core/test_balance_round3.py |
| LB-22 | 食料が足りないときは、食費のうち足りない割合の分をサービスにもぜいたく品にも使わず手元に残す | 決定 | tests/core/test_balance_round4.py |
| LB-23 | 消費財の注文は「いつもの値段」（家計が買ったターンだけの直近の平均）の段に分けた指値で出し、今の… | 仮（仮の決まりの表あり） | tests/core/test_economy.py、tests/core/test_playtest5_fixes.py |
| LB-24 | 消費税は家計が市場で買う物（食料・衣料・ぜいたく品・サービス）と電気に、代金に上乗せして政府に払… | 決定 | （未確認） |
| LB-25 | 町の資材は、都市の発展の段が「育てる」と決めた分を一般の層が預金の決めた割合まで市場で買う（住宅… | 決定 | （未確認） |
| LB-26 | 村の仕事のない住民は自給の農業で自分の食べる分をまかない、市場で買わない | 決定 | tests/core/test_balance_round4.py |
| LB-27 | 幸福度は家計の層ごとに出して人数で平均する | 決定 | （未確認） |
| LB-28 | 建国のとき、働く層と資産を持つ層に1人あたりの預金を置き、一般の労働者の決めた割合を技術者にする | 決定 | tests/server/test_economy.py |
| LB-29 | 財「サービス」はその場で作ってその場で使い、持ち越せず島の外へ運べない | 作り直し予定（回7） | tests/core/test_balance_round4.py |
| LB-30 | 家計は必需品を買った残りのうち所得に応じた割合をサービスに使う | 決定（回7 でも残す） | tests/core/test_balance_round4.py |
| LB-31 | 店の場所は通勤圏でいちばん人の多い集落で、雇う人数は場所ごとに買いに来る住民で割り振る | 仮（仮の決まりの表あり） | tests/core/test_balance_round4.py |
| LB-32 | 店の雇う人数の目安は直近の売上（買えなかった分も足す）× 人件費の割合 ÷ 賃金で、前のターンか… | 作り直し予定（回7） | tests/core/test_balance_round4.py |
| LB-33 | 店の運転資金は銀行から借り（審査は目安の人数での売上 − 賃金）、借りられなければ持ち主が出資す… | 作り直し予定（回7） | （未確認） |
| LB-34 | 店は企業の施設として作り直す: 賃金で人を取り合い、電力・食料・消費財を少し使い、建設費と減価償… | 決定（回7 で作る） | （未確認） |
| LB-35 | 家族や村の支え合い（働いている家族の稼ぎの一部が、仕事も手当もない家族の食べ物に回る）を家計の作… | 決定（回7 で作る） | （未確認） |
| LB-36 | 家計が預金から使う割合を物価上昇率と実質金利で動かし、消費財の指値に物価の見通しを足し、失業が多… | 決定（回5 で作る） | （未確認） |
| LB-37 | 統計に、層ごとの人数・可処分所得・預金・幸福度、平均賃金（一般）と技術者の賃金、実質賃金、失業率… | 決定 | tests/server/test_reports.py |

## [5 施設と企業](05-firms.md)

決まり 37 個、うち仮の決まりの表にあるもの 1 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| FM-01 | 施設の種類・建てる場所・働き手・作り方（レシピ）・産業の分類は定義データ（`packs/base`… | 決定 | （未確認） |
| FM-02 | プレイヤーは命令で施設を建て、取り壊す | 決定 | tests/core/test_rules.py |
| FM-03 | 稼働率は人手・投入財・電力の充足率の一番低いものと操業目標で決まる | 決定 | tests/core/test_infrastructure.py |
| FM-04 | 施設の会計は、民営（独立採算）・国営の独立採算・国営の国庫直轄の3つ | 決定 | （未確認） |
| FM-05 | 施設1つがお金の主体1つで、民営の持ち主は資産を持つ層1つ（企業という単位がない） | 仮（仮の決まりの表あり） | （未確認） |
| FM-06 | 独立採算の施設は自分の預金と借入を持ち、税引き後の利益の決めた割合を配当（民営）か国庫への納付（… | 決定 | tests/core/test_rules.py |
| FM-07 | 国庫直轄の施設は口座を持たず、売上は国庫へ、費用は国庫から予算の範囲で払う | 決定 | tests/core/test_shocks.py |
| FM-08 | 国営の独立採算の施設は民営と同じく操業目標を決めるが、自分で閉鎖せず倒産しない（返済が足りなけれ… | 決定 | （未確認） |
| FM-09 | 計画経済の島では倒産を起こさず、返済が遅れた国営施設には国営銀行が借り換えで穴を埋める | 決定 | tests/core/test_shocks.py |
| FM-10 | 設備は実際に払った建設費を決めたターン数で減価償却し、損益の費用に入れる | 作り直し予定（回6） | （未確認） |
| FM-11 | 施設は建てたときの資材を設備として持ち、毎ターン傷み、傷んだ分だけ作れる量が減る | 決定（回6 で作る） | （未確認） |
| FM-12 | 国営の施設は国が予算から直す | 決定（回6 で作る） | （未確認） |
| FM-13 | 民営工場の参入は参入と撤退の段で決める | 決定 | tests/core/test_finance.py |
| FM-14 | 参入の合図は、その財が品切れ続き（足りない量が新しい工場の全力の決めた割合以上のターンが、直近の… | 決定 | tests/core/test_finance.py |
| FM-15 | 原料のどれかが島で作られていない作り方、工事の資材が島で作られず売りにも出ていない、原料が品切れ… | 決定 | tests/core/test_finance.py |
| FM-16 | 民営工場が建つのは空いた工業団地のマスだけ | 決定 | tests/core/test_finance.py |
| FM-17 | 建設費（1割増しと動き出しの支払い分を足す）の決めた割合を資産を持つ層が出資し、残りを銀行の建設… | 決定 | tests/core/test_playtest3_fixes.py |
| FM-18 | 設備投資補助のある参入では、補助を参入のときに補助金の予算から先に出す | 決定 | tests/core/test_playtest_fixes.py |
| FM-19 | 民営工場と民間銀行が建たないわけ（工業団地がない・働き手がいない・出資が足りない・融資が断られた… | 決定 | tests/core/test_playtest_fixes.py |
| FM-20 | 撤退: 動かないターンが決めた数続くか、変動費の利益が決めたターン数続けて決めた幅を超えてマイナ… | 作り直し予定（回7） | tests/core/test_finance.py |
| FM-21 | 倒産: 返済が決めたターン数続けて遅れると倒産し、施設は止まり働き手は失業する | 決定 | tests/core/test_finance.py |
| FM-22 | 倒産と撤退の後、設備と在庫を売った代金は、未払い賃金 → 未払いの税 → 貸し手（貸した額の割合… | 決定 | tests/core/test_balance_round3.py |
| FM-23 | 民営の工事が決めたターン数進まなければ工事をやめる | 決定 | tests/core/test_finance.py |
| FM-24 | 民営の施設はプレイヤーが取り壊せない（国有化すれば取り壊せる） | 決定 | （未確認） |
| FM-25 | 国有化（命令）: 民営の施設を純資産（0未満なら0）で政府が買い取り、代金を国庫から資産を持つ層… | 決定 | tests/core/test_finance.py |
| FM-26 | 民営化（命令）: 国営の施設を純資産で資産を持つ層に売る | 決定 | tests/core/test_finance.py |
| FM-27 | 補助金（命令 | 決定 | tests/core/test_finance.py |
| FM-28 | 命令 `set_zone` で陸のマスを工業団地かオフィス街に指定・解除する（首都と町のマスは指… | 決定 | tests/core/test_finance.py、tests/server/test_finance.py |
| FM-29 | 鉱山は鉱業資源ごとの作り方を持ち、島に決めた数まで（工事中も数え、民営も数える | 決定 | tests/core/test_phase3_2.py |
| FM-30 | 施設の定義に産業の分類 `industry`（農業・鉱業・鉄鋼・非鉄・機械・自動車・繊維・石油・… | 決定 | （未確認） |
| FM-31 | 経験: 島×産業ごとに施設が出した産出の倍率（稼働率×生産性）を足して数え、係数を累積の対数で上… | 決定 | tests/core/test_phase3_3.py |
| FM-32 | 集積: 工場（分類が industry の施設）だけに、半径の中の同じ産業のほかの施設1つごとに… | 決定 | tests/core/test_phase3_3.py |
| FM-33 | 各産業の累積が世界1位の国に「鉄鋼の国」などの称号を付け、統計の画面に得意産業と世界の順位を出す… | 決定 | （未確認） |
| FM-34 | 暴動で止まるのは都市のマスの近くの施設で、農林水産業（農場・漁港・牧場・製材所）は止まらない（暴… | 決定 | tests/core/test_phase3_4.py |
| FM-35 | 商業・サービス業（島に1つの店）は倒産と撤退をせず、持ち主は経済体制の既定（細部はタブ 4 LB… | 作り直し予定（回7） | tests/core/test_balance_round4.py |
| FM-36 | 施設の束としての企業を主体にする | 決定（回7 で作る） | （未確認） |
| FM-37 | 店を企業の施設として作り直す | 決定（回7 で作る） | （未確認） |

## [6 国の財政と政策](06-finance.md)

決まり 23 個、うち仮の決まりの表にあるもの 0 個。

| 番号 | 決まり（頭） | 状態 | 試験 |
| --- | --- | --- | --- |
| FS-01 | 政府のお金は中央銀行にある国庫の口座だけ | 決定 | tests/core/test_economy.py |
| FS-02 | 収入は、所得税・法人税・消費税・関税、国庫直轄の施設の売上、国営の独立採算の施設と国営銀行の納付… | 決定 | （未確認） |
| FS-03 | 所得税は賃金の段で賃金から差し引き、消費税は家計の買い物と電気の代金に上乗せし（タブ 4）、法人… | 決定 | tests/core/test_bond_gains.py |
| FS-04 | 税率は命令で変え、次の段から効く | 決定 | tests/core/test_phase3_4.py |
| FS-05 | 支出は、公務員・教員・警察官の賃金、失業手当、国庫直轄の施設の費用、公共事業、教育、治安、補助金… | 決定 | （未確認） |
| FS-06 | 予算の項目（公共事業費・教育費・治安費・補助金・国の倉庫の買い入れ）は命令で年の額を決め、毎ター… | 決定 | （未確認） |
| FS-07 | 建国のときの予算は、住民1人あたりの決めた額（教育費は学校の運営の決めた年数分） | 決定 | （未確認） |
| FS-08 | ターンの初めに、前のターンの実績から各項目の支払いを多めに見積もり、国庫の残高を支払いの順番に取… | 決定 | （未確認） |
| FS-09 | 支払いの順番は、国債 → 未払い賃金 → 公務員 → 失業手当 → 国庫直轄の施設 → 教育 →… | 作り直し予定（回5） | （未確認） |
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
| FS-22 | 国庫の払う順番を見直し、払えないときに国債の支払いを止めるかを、借金の重さと一緒に決める（中央銀… | 決定（回5 で作る） | （未確認） |
| FS-23 | 国営の施設の設備は国が予算から直す（回6 | 決定（回6 で作る） | （未確認） |

