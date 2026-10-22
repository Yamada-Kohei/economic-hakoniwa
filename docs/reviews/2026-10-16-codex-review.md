# 経済版箱庭諸国 レビュー・改善検討報告書

作成日：2026年10月16日（日本時間）  
宛先：本ゲームの開発を担当するClaude  
対象：[Yamada-Kohei/economic-hakoniwa](https://github.com/Yamada-Kohei/economic-hakoniwa)  
対象コミット：`b4611b7f7276e069039a6f81b032bdbb604f796a`（フェーズ3-4）

## 1. この報告書の目的と読み方

ゲームデザイン、実装、アーキテクチャ、経済バランス、UI、現実経済との違いについて、この会話で挙げた内容を重複整理し、追加確認した論点も収録した。開発者が実装と照合し、修正・検証・仕様決定へ進むための引き継ぎ資料である。

**全項目を実装すべきという要求ではない。** 現実の制度を増やすほど面白くなるとは限らない。とくに経済モデルの省略は、再現したい現象、プレイヤーに与えたい判断、計算時間、説明の難しさを踏まえて採否を決めたい。一方、意図せず情報が公開されることや復旧後に世界が進まなくなることは、モデルの簡略化とは別に扱う。

確度は次のとおり区別する。

| 表記 | 意味 |
|---|---|
| ローカル再現済み | 独立した確認用DB・環境で挙動を確認した |
| 実画面で観察 | 会話中に公開画面を読み取りで確認した。その時点の観察であり、最新の状態ではない |
| コードで確認 | 対象コミットの処理・定義・テストから確認した |
| 静的な懸念 | 起こりうる処理経路を確認したが、並行実行・障害等を再現していない |
| 影響の仮説 | その実装が経済や遊びに与えると考えられる影響。因果は未計測 |
| 追加案 | 検討候補。実装を前提にしない |

優先度は、P1＝公開・復旧運用に先立ち対処したい、P2＝近い段階で修正または検証したい、P3＝目指すゲーム像に応じて検討、を意味する。経済モデルのP2は原則として**まず検証・仕様決定する優先度**であり、大規模な機能追加の即時要求ではない。

調査はソース、定義、テスト、ローカル確認、標準AIによる長期シミュレーション、公開画面の観察に基づく。ゲームコードの変更・修正PR作成、実サーバーへの命令登録・ターン進行・巻き戻しは行っていない。今回の掲載PRは報告書のみを追加する。作業用DBや報告書はレビュー用の別領域に置いた。接続先の私的アドレス、資格情報、他者への連絡先は本書に含めない。

報告書作成時にGitHubのmain先端も対象コミットと同一であることを確認した。ただし、稼働サーバーがこの版かは未照合。公開画面の観察とローカルコードの不具合を結び付けて、稼働サーバーにも同じ問題があるとは断定しない。原理的な全欠陥・全省略を列挙する完全監査ではなく、今回の確認範囲で報告できる論点をまとめている。

## 2. 総評と維持したい長所

**生産・所得・消費・金融・貿易がつながる骨格は強い。今の重点は、長期に回る経済と、プレイヤーが原因を理解して立て直せる体験である。**

- 鉄鉱石→鋼鉄→機械→設備という連鎖に、電力、物流、賃金、家計需要、融資、為替が関わる。単なる施設数の増加にとどまらず、政策の波及を遊びにできる。
- 資源選択、気候、産業経験に差があり、国の得意産業と貿易の動機が生まれる。
- 市場、財政、銀行、倒産、教育、インフラ、国際貿易、政治、計画経済まで実装されている。外見だけの試作品ではない。
- 「産業の鎖」、施設の停止理由、市場・施設詳細など、原因を調べる道具が既にある。案内を改善する際はこれを活かしたい。
- コアがDjango・DBから分離され、数値を定義データで調整できる。乱数再現性、島の処理順、保存・再読込後の一致、会計照合を検査している。

根拠：[README](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/README.md#L1)、[産業の鎖](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/chain.html#L9)、[依存境界のテスト](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/tests/core/test_boundaries.py#L14)、[再現性のテスト](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/tests/core/test_gate.py#L122)。

## 3. 実施した検証と結果の限界

### 3.1 通常の検証

この会話中、Python 3.12・SQLite環境で通常の自動テストは**803件成功・1件省略**。省略は長期関門の設定によるもの。コードの検査、定義データの検査、DB移行ファイルの整合性確認も通過した。ゲームコードは変更していないため、報告書作成だけのために同じ全テストを再実行してはいない。

リポジトリのCIにはPython 3.12/3.13×SQLite/PostgreSQLの検査が定義されている。ただし、今回のローカル結果をその全組合せの実行結果とは扱わない。CIの通常シミュレーションは50島×30ターンである。[CI定義](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/.github/workflows/ci.yml#L1)

### 3.2 50島・300ターンの長期関門

同じ対象コミットで、標準AI・seed 1・50島・300ターン・関門判定付きのシミュレーションを別途実行した。計算は完走し、内蔵の経済安定性判定は不合格だった。

| 指標 | 結果 |
|---|---:|
| 関門の逸脱メッセージ | 266件 |
| 判定期間のゲーム内失業率平均 | 25.7% |
| 同最大 | 59.1% |
| 同年率物価上昇率 | −40.3%～＋42.2% |
| 全300ターンの銀行破綻 | 0件 |
| 1ターンの平均計算時間 | 約1.54秒 |
| 同最大 | 約2.38秒 |

判定期間はターン120～300。失業率平均は人口加重ではなく、島×ターンを同じ重みで集計したもの。計算時間は今回のローカル条件の参考値で、実サーバー全体の応答時間や性能保証ではない。

**266件は266個の独立した不具合でも、266回の更新失敗でもない。** 島・商品・指標ごとの設計目安逸脱が混在している。標準AIの方針、配置、モデル、閾値のどこに原因があるかを切り分ける必要がある。また後述B02の判定窓の問題があるため、価格に関する内訳は検査を修正して再評価したい。銀行破綻0件も、あらゆる危機で金融が安全という意味ではない。

ゲーム内失業率は固定的な労働参加割合、自給生産の就業扱い、技術者の別集計を含む。求職活動・就業可能性等で定義される現実の完全失業率との単純比較はしない。輸送船等の表示が`0.0`でも、丸めで少量が隠れる場合があり、「一切生産・取引されていない」とは断定しない。

根拠：[関門の基準](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L36)、[集計](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L190)、[長期テストの実行条件](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/tests/core/test_gate.py#L156)、[一般層・自給の雇用集計](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/records/__init__.py#L58)、[技術者の別集計](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/records/__init__.py#L245)、[労働参加割合](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/facilities.py#L164)。

## 4. 技術・運用・公開範囲

### A01：巻き戻し後に世界の会計が崩れ、次の更新が停止する

**P1／ローカル再現済み。** 後発島を建国し、既存島から援助を送ってから、その建国前まで巻き戻す。後発島を残して既存島だけ過去へ戻すため、外貨保有と発行国側の対応勘定が一致しなくなる。独立した確認用DBで2回再現した。

最小手順：既存島でターン1→後発島を建国→既存島から額100の`send_aid`を実行してターン2→`rollback(0)`→次ターン実行。復旧直後に外貨照合の不一致2件があり、次ターンは`LedgerCheckFailed`で停止し、ターン0から進まなかった。

**改善方向：** 復旧時点で存在しない島の扱いを先に決め、世界全体を同じ時点へ整合させる。外貨だけでなく外国国債、中央銀行間融通、輸送中の荷、契約を含めて検査する。残高だけをつじつま合わせで補う修正は避ける。復旧トランザクション確定前の全世界照合と、後発島を含む回帰試験が必要。

根拠：[巻き戻し](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/turns/services.py#L163)、[外貨照合](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/money.py#L151)。

### A02：所有者限定の帳簿・銀行情報が、公開マス詳細から返る

**P1／ローカル再現済み。** 専用の施設帳簿・金融APIでは閲覧者を区別する一方、マス詳細は所有者確認なしで施設情報を組み立て、帳簿、借入、銀行個別情報を含める。匿名ユーザーでマス詳細が200を返し、帳簿と銀行情報のデータキーを含むことを確認した。同じ匿名ユーザーの専用金融APIは銀行・政策の非公開部分を返さなかった。

これはゲーム内情報の公開範囲の不一致であり、パスワード等の漏洩を確認したという報告ではない。稼働サーバーでの同じ問題は未確認。

**改善方向：** 公開用と所有者用の表示データを分けるか、権限を明示して組み立てる。画面で隠すだけでなくAPIから非公開項目を除く。匿名、他国の所有者、当該国の所有者で全取得経路を比較する。

根拠：[マス詳細API](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L253)、[施設情報組立](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/display.py#L152)、[施設金融情報](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/display.py#L315)、[専用APIの所有者検査](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/tests/server/test_reports.py#L149)。

### A03：ターン更新と命令列の全置換が競合しうる

**P2／静的な懸念、並行再現なし。** 更新側は命令を読み込んでから計算・保存する。API側はpending命令を削除して新しい行を作るが、両側で同じロックや列の版番号を共有していない。旧命令を更新側が読んだ後に列を保存すると、画面から消した命令が実行対象に残り、新しい同内容の命令も翌ターンへ残る可能性がある。

**改善方向：** 確定した当ターンの実行バッチと、編集可能な翌ターンの列を分ける。サーバー側で一貫する排他・版番号検査を設け、PostgreSQLで意図的に処理を交差させて再現する。締切後の命令を翌ターンに送る正当な仕様とは区別する。

根拠：[更新処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/turns/services.py#L80)、[命令読込](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/world/repository.py#L128)、[命令全置換](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L443)、[結果保存](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/world/bulk.py#L17)。

### A04：強制終了後、更新中の印が残り通常更新が止まり続けうる

**P2／静的な懸念。** `updating_since`は先に別トランザクションで保存される。通常例外なら解除するが、停電やプロセス強制終了では解除処理を通らず、復旧後の通常更新が「更新中」として見送られ続ける可能性がある。

**改善方向：** 実行所有者・生存確認・期限付きの実行権を含む復旧設計と、最終成功／失敗／遅延の表示を用意する。経過時間だけで生きた処理の印を解除しない。強制実行は時刻・一時停止・更新中判定を迂回するため、復旧手順で条件を明示する。world行のロックは既にあり、「同じターンが無制限に同時更新される」とまでは確認していない。

`mail_admins()`呼出しはあるが、同梱設定だけでは実際の通知先・配送設定まで確認できない。運用環境で別設定の可能性があるため、通知を一度実送信で検証するのは運営側の確認事項。

根拠：[更新中判定と印](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/turns/services.py#L63)、[例外処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/turns/services.py#L94)、[同梱設定](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/settings.py#L1)。

### A05：有効なJSONでも外形・型が違うと500になりうる【追加確認】

**P2／静的な懸念。** 命令列の要素へ型確認前に`.get()`、確認APIのトップレベルへ`.get()`、命令kindへ型確認前に辞書のmembership判定を行う。例として`{"orders":[null]}`、確認APIの配列・null、list型のkind等は通常の400へ変換されない例外経路がある。建国入力にも同種の外形検査を確認したい。異常入力は実サーバーへ送っていない。

**改善方向：** JSON構文、トップレベルの型、配列要素、kindの文字列、payloadのスキーマを順に検査し、形式エラーは400へ統一する。少数の境界入力で回帰検査する。

根拠：[命令入力](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L420)、[確認API](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L456)、[kind検証](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/orders.py#L24)、[建国入力](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/founding.py#L125)。

### A06：同一IP登録数の判定と作成が原子的でない【追加確認】

**P2／静的な懸念、並行再現なし。** 登録数のcount判定が登録トランザクションより前にあり、同一IPハッシュについて同時要求を直列化する仕組みが見当たらない。複数要求が同じcountを読めば、両方が上限未満として登録する可能性がある。

**改善方向：** 上限判定と作成を同じ排他区間へ置く。IPごとのカウンタ／ロック対象を検討し、上限が複数の場合も検査する。NAT利用者の扱いとプロキシのIP取得設定は運営仕様として別途決める。

根拠：[登録上限と登録](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/accounts/views.py#L34)、[登録記録モデル](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/accounts/models.py#L11)。

### A07：未実行命令・政策情報の公開範囲を決めたい【追加確認】

**P2／コードで確認、仕様決定。** 命令APIのGETは所有者確認より先に命令列を返し、payload、政策、最近の実行結果を含む。PUTの変更権限は検査している。命令を公開するゲーム仕様なら不具合とはいえないが、他国の画面で命令欄を隠すこととAPIの公開方針を合わせたい。

**改善方向：** 「誰でも読める」「所有者のみ」の項目表を定め、APIの経路を横断して適用する。A02と一緒に確認するとよい。匿名での追加取得実験は行っていない。

根拠：[命令APIのGET](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L412)、[返却項目](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/api.py#L479)。

### A08：GDP差分の診断がゼロ・負の生産額で不適切になる【追加確認】

**P2／コードで確認、実世界データでの発生は未確認。** `gdp.gap = abs(production - spending) / production if production else 0.0`なので、生産額0・支出額非0でも差を0とし、生産額が負なら指標が負になる。上限との比較で不一致を見逃しうる。

**改善方向：** 分母の絶対値・最小基準、ゼロ時の絶対差等を明示し、0と負の境界を検査する。また生産側・支出側は同じflowsから集計されるため、一致は有用な内部検査だが、独立した実物勘定や現実妥当性の検証の代わりにはならない。

根拠：[GDP差分](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/records/__init__.py#L113)、[GDPの両側集計](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/books.py#L110)、[差分の判定](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L316)。

## 5. UI・説明・ゲームの導線

以下の実画面観察は会話中のターン250時点。数値はその時点の表示であり、現在の各国の状態ではない。

| ID・優先度 | 確認・問題 | 改善方向 |
|---|---|---|
| U01・P2 | 序盤ガイドは操作説明が中心で、建国から12ターンで表示を終える。豊富な画面をどういう順で調べるか、初見では判断しにくいという見立て | 食料の安定、不足原因の診断等の短い目標と達成判定を付け、既存の産業の鎖・市場・マス詳細へ案内。必要時にガイドを再表示できるようにする |
| U02・P2 | 実画面で失業率41%、電力不足100%、年率物価上昇率−26.7%でも、通常の指標カードに埋もれていた。施設一覧も全施設・多列表示が中心 | 重要な変化を数件に集約し、「発電量ゼロ」「発電所停止」等の事実から原因画面へリンク。施設の状態別絞り込みも検討。数値だけで危機の原因を断定しない |
| U03・P2 | 実画面のログに`facility_stopped_riot`がそのまま表示された | 翻訳文・必要引数を追加し、内部キーを生文言として露出させない。全イベントキーと翻訳の照合を検査 |
| U04・P2 | 建国画面に「建国後は体制を変更できません」とあるが、憲法改正・体制変更は実装済み | 現在の条件・制限に合う説明へ更新。建国時にも体制の利点と負担を示す |
| U05・P2 | 順位の初期通貨では9か国中8か国を換算できず、別通貨に替えると6か国を比較できた | 比較可能国の多い通貨を初期値にし、順位の母数・換算不能国・評価時点を明示。比較対象1か国での「世界1位」に注意 |
| U06・P2 | ログは10ターン単位のページ送り。施設・原因・ターンを指定して追う導線が弱い | グラフの異変→そのターンのログ→該当施設をつなぐ。施設別・イベント種別の絞り込み |
| U07・P2 | 当日の公開トップで次回更新が9日先を示した。手動試験運用か遅延かは未確認。命令欄や統計画面から予定時刻も追いにくい | 手動更新、一時停止、定期更新、遅延・失敗を区別し、命令列の近くにも予定を表示。更新障害があったと断定しない |
| U08・P2 | ゲーム内失業率・年率・GDP・気配値を、現実の統計や実取引値と同じと受け取りやすい | 定義・対象期間・集計範囲を短く表示し、気配と約定、名目と実質、未観測とゼロを区別する |
| U09・P2・追加 | 建設候補で国内生産不能の原料を「原料なし」「建てても操業できない見込み」と表示する。貿易による調達可能性をこの判定に含めない | 「国内では作れず、輸入確保が必要」と、資源のない鉱山等の真正な立地不可を分ける。輸入があれば必ず操業できるという保証にも置き換えない |
| U10・P2・追加 | 幸福度に多くの寄与要因があるが、合計・層別値から寄与点の内訳へ下りる表示は確認範囲で見当たらない | 食料、衣料、電力、実質賃金下落等の寄与を示し、改善対象へリンク。政情の内訳表示を参考にする |
| U11・P3・追加 | Canvas地図はポインター等が中心で、キーボードのマス選択は見当たらない。サーバーテストだけでは実ブラウザの選択・ピンチ・狭い画面の品質を確認できない | 座標入力・一覧等の代替経路とキーボード操作を検討。対象端末での実操作を検証する。現在すべての端末で操作不能という報告ではない |
| U12・P2・追加 | 手引きの雇用説明に優先度順と賃金順が混在。二国間契約の説明も現行の市場／倉庫選択・既定値へ追従しきれていない。政治・計画経済の体系的な手引きも薄い | 既存雇用維持→新規採用の賃金順・同額なら優先度、契約ごとの受取先選択を正確に説明。各機能画面にある説明と手引きを合わせる |

根拠：[序盤ガイド](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/island.html#L19)、[建国説明](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/found.html#L6)、[体制変更画面](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/politics.html#L132)、[次回更新表示](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/top.html#L4)、[順位の計算](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/rankings.py#L1)、[ログ画面](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/log.html#L1)。U02・U03・U05・U06・U07は当時の画面観察を含む。実画面の現象と、全てが同じ原因で起きたという推測を混同しない。

追加の根拠：[ガイド期間](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/views.py#L17)、[施設一覧](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/facilities.html#L11)、[国内生産不能の警告](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/static/js/island/panel.js#L376)、[生産可否の元データ](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/views.py#L55)、[幸福度の計算](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/population/__init__.py#L139)、[地図操作](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/static/js/map/controls.js#L26)、[雇用の説明](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/help.html#L20)、[契約の説明](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/templates/web/help.html#L89)、[現在の契約受取先](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/contracts.py#L5)。

## 6. 長期バランスの検証課題

### B01：高失業・物価の大きな振れを、原因別に分解する

**P2／長期実行の観測。** 需要不足→減産→失業→消費減の循環は経済としてあり得る。しかし回復手段が成立し、プレイヤーが原因と対処を理解できる必要がある。今回の1本の走行だけで、設備更新不足やサービス業不足を原因と確定してはいけない。

最低限、求人／失業の地域分布、原料・電力・物流・販路・資金の制約、家計の実質支出、公共支出、輸出入、施設の参入拒否理由、技術者不足を同じ時系列で観察したい。AI方針とモデルの問題を分け、閾値を緩めるだけで解消扱いにしない。

### B02：関門の価格判定で、商品ごとの記録と評価窓がずれうる【追加整理】

**P2／コードで確認。** 価格比率は約定量が正のときだけ財ごとの列へ追加し、対応するターンを保存していない。`values[-len(window):]`による末尾抽出は「最後のN約定ターン分」であり、ターン120以降という意味と一致しない場合がある。後から登場した財や欠測のある財では対象期間外を混ぜる可能性がある。

価格にターンを対応付け、共通の期間条件で抽出する。修正後に266件の価格内訳を再評価する。ただし、この問題だけで失業率・インフレの観測結果が消えると考えない。

根拠：[記録](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L81)、[期間窓](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L264)、[価格の末尾抽出](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/gate.py#L309)。

### B03：海外商人終了後、少人数・放置国でも交易が続くか

**P2／追加検証案。** 海外商人は初期120ターンの供給役を担う。標準AIは貿易圏等を整えるため、その結果を少人数・無操作・新規参入国へ一般化できない。資源の組合せ、港・船・燃料、相手国の稼働、通貨市場を変えて確認したい。

「貿易しないと苦しい」ことは設計として成立するが、必要な相手が存在しないため何もできない状態は別に評価する。終了前の案内、段階的縮小、限定的な代替調達のどれを採るかは仕様判断。

標準AIは失業手当、公共事業、資材確保、工事数、貿易等の仕組みを知っている。初見プレイヤー、無操作国、工事の過剰発注、復帰国の回復可能性は別のシナリオとして確認する。

根拠：[海外商人の期間](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/README.md#L16)、[標準AIの交易準備](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/autoplan.py#L991)、[海外商人設定](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/game.yaml#L210)。

### B04：成長を止めた成熟国と、300ターン超を別に確認する【追加整理】

**P2／追加検証案。** 人口と設備数がほぼ一定でも、消費・維持更新・公的サービスで需要と雇用が続くかを見る。さらに船の寿命をまたぐ350～500ターン程度で、買い替えと貿易継続を確認したい。現在の300ターン関門が、長寿命設備の最初の更新直前で終わる場合に注意する。

単一seed・単一AIだけでなく、複数seedと少数の対照条件を使う。経済体制の優劣を比較する際は、AIの向き不向き、初期条件、固定ボーナス、公共部門の集計方法を分離する。

## 7. 現実経済との違いと、ゲームとしての採否

ここからは主に現代の工業国を比較対象とする。歴史上の全経済に普遍的な要件ではない。各項目の「影響」は仮説であり、E01～E35をまとめて追加することは勧めない。

### 7.1 成熟経済・家計・人口

#### E01：一般工場の設備更新需要が薄い〔P2〕

**現状：** 帳簿上の償却はあるが、通常の加齢で一般工場の設備を交換する処理は見当たらない。船・トラック・インフラの劣化、暴動後の修理は別途ある。機械は造船等にも継続投入される。

**影響・案：** 建設が一巡すると機械・鋼鉄需要が細る可能性。人口・設備が増えない世界で計測し、必要なら維持修繕と更新投資を設ける。「機械需要が必ずゼロになる」とは言わない。[償却](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/accounts/__init__.py#L229)、[生産・修理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/production/__init__.py#L211)。現実比較：[Eurostatの固定資本の説明](https://ec.europa.eu/eurostat/cache/metadata/EN/nama_10_nfa_st_esms.htm)。

#### E02：豊かな家計の支出が物品に偏る〔P2〕

**現状：** 金融・物流・教育等は既存。一方、外食・医療・娯楽などを家計が購入し、その売上が地域の賃金になる市場は薄い。

**影響・案：** 成熟後の所得・需要・雇用の循環が物品供給に寄る。生活サービスを一部門で試す場合も、雇用人数だけ追加せず、家計支出・企業売上・賃金・資金制約を接続する。[家計注文](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L1027)、[財の定義](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/goods.yaml#L1)。

#### E03：同じ階層の家計が共同財布に近い〔P2〕

**現状：** 一般・資産保有者・技術者ごとに所得と預金を集約して消費を決める。階層差は既存で、全員同一ではない。

**影響・案：** 同じ一般層の失業世帯と安定就業世帯の貯蓄差、給付の対象差、貧困が表れにくい。個人全員の計算前に、就業／失業や資産帯で少数の家計群へ分割する案がある。[階層と状態](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/households.py#L1)、[消費関数](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/households.py#L158)。

#### E04：人口増加と労働力の時間差が薄い〔P3〕

**現状：** 人口増が固定的な労働参加割合で働き手へ反映され、年齢コーホートを持たない。技術者の育成・引退はある。

**影響・案：** 出生と解釈するなら就労までの期間、扶養負担、高齢化が省略される。移住と解釈するなら出身地や受入条件が必要。「人口増」の内訳をまず仕様・表示で定め、必要な場合にだけ年齢層を加える。[人口増減](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/population/__init__.py#L283)、[労働参加割合](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/workers.yaml#L1)。

#### E05：住宅が収容力に寄り、土地・住宅資産の働きが薄い〔P3〕

**現状：** 住宅形成・段階的発展・資材需要は既存。地価・家賃・住宅ローンは見当たらない。

**影響・案：** 便利な立地の価格上昇、家賃負担、住宅借入と消費、不動産下落と銀行損失が表現されにくい。まず住居費だけを扱うのか、土地・住宅を金融資産にするのか、遊びの範囲を決めたい。[住宅処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/population/__init__.py#L417)。現実比較：[英中銀の金融政策報告](https://www.bankofengland.co.uk/monetary-policy-report/2025/august-2025)。

#### E06：国外流出が受入国や送金につながらない〔P3〕

**現状：** 島内移動、不満による人口減はある。流出者の渡航先への加算、移民の職業、家族への送金は見当たらない。国家間援助とは別の仕組みである。

**影響・案：** 移民による人手不足解消、頭脳流出、送金で支えられる家計が出にくい。導入するなら送り出し国・受入国の人口と資産を整合させる。[国外流出](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/population/__init__.py#L296)、[島内移動](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/population/__init__.py#L339)。現実比較：[世界銀行WDR2023](https://www.worldbank.org/en/publication/wdr2023)。

### 7.2 雇用・都市・企業の成長

#### E07：道路・鉄道で通勤可能範囲が広がらない〔P2〕

**現状：** 一般労働者の通勤は主に固定半径で、交通網の接続・所要時間を使わない。近ければ海を挟んでも圏内になりうる。技術者は島全体で配属される。道路・鉄道そのものの経済効果が皆無という意味ではない。

**影響・案：** 住宅地と工業地帯を交通で結び、失業と求人を同時に改善する判断が薄い。到達可能性だけの簡易接続から始め、必要なら移動時間と交通容量へ広げる。[通勤判定](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/facilities.py#L72)、[技術者の範囲](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/workers.yaml#L7)。

#### E08：小さく起業して増設する経路が薄い〔P2〕

**現状：** 設備は標準規模で定義される。民間参入は周辺の失業者が必要人数の半分以上という条件を持ち、標準製鉄所は総員2,000人なので1,000人が目安となる。操業率調整自体はある。

**影響・案：** 島全体で失業者がいても分散すると参入できない。小規模設備、初期規模の選択、段階的増設を比較し、単に参入閾値を下げて同時乱立させない。[立地条件](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L681)、[製鉄所規模](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/facilities.yaml#L126)。

#### E09：雇用人数と労働時間を分けない〔P2〕

**現状：** 操業目標・原料に応じて希望雇用人数を変える。既存雇用を優先するが、残業、休業、時短、多交代勤務を独立状態として扱わない。

**影響・案：** 一時的需要変動が人数調整へ寄る。人員と時間を分け、短期の残業・時短と長期の採用・解雇を区別する案。人数をフルタイム換算と解釈するなら、画面の「人数」「失業率」との対応を説明する。[雇用希望人数](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/facilities.py#L229)、[既存雇用維持](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/employment/__init__.py#L138)。現実比較：[ILOの労働時間調整](https://www.ilo.org/resource/news/flexible-working-hours-can-benefit-work-life-balance-businesses-and)。

#### E10：再就職の探索・職種転換・習熟が薄い〔P2〕

**現状：** 雇用継続、引き抜きの賃金差・人数制限、一般／技術者、大学育成は既存。ただし失業者は席・資金・通勤条件を満たせば同じ処理で採用され、同ターンの生産へ参加できる。技術者も産業別専門を持たない。

**影響・案：** 失業者がいても教師や特定技術者を確保できない状況、採用時間、職場固有の習熟が弱い。少数の技能区分か採用後の習熟期間を試す。新規採用を全て即戦力扱いしないことと、大学の一般教育を重複実装しないことがポイント。[新規採用](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/employment/__init__.py#L156)、[技術者配属](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/employment/__init__.py#L333)。現実比較：[ILOの技能ミスマッチ](https://www.ilo.org/resource/why-skills-mismatch-challenge-matters)。

#### E11：企業の情報・見通しが共通式に寄る〔P3〕

**現状：** 共通の価格・需要記録と参入式を使い、同じ商品の新工場直後は次の参入を抑える。計画経済にも実際の需要・不足を記録する仕組みがある。

**影響・案：** 判断のばらつき、他社と競う投資、誤認・学習、過剰投資が表れにくい。情報の遅れや予測差を導入する場合は市場経済・計画経済の両側へ対称的に検討し、乱数を足すだけにしない。[参入・退出の方針](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L1)、[計画目標](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/plan.py#L390)、[需要の記録](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/plan.py#L426)。

#### E12：複数施設を持つ企業・企業間再編が薄い〔P3〕

**現状：** 出資、配当、出資損失、国有化・民営化、差押施設の再売却は既存。企業間持株、親子会社、連結、通常の買収や海外直接投資は見当たらない。

**影響・案：** 製鉄会社が鉱山を持つ垂直統合、同業再編、海外工場所有による供給網が出にくい。企業の所有と施設の操業を分けると複雑になるため、まず再編で何を遊べるようにするかを決める。[所有変更](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L1102)、[所有者の会計主体](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/__init__.py#L129)。

### 7.3 技術・資源・商品・環境

#### E13：技術進歩が生産経験・教育・集積に寄る〔P3〕

**現状：** 経験、教育、集積、産業政策による生産性差は既存。資金を使う研究開発、技術購入、一般工場の改造投資は薄い。トラックには設備費と節約賃金を比べる導入判断がある。

**影響・案：** 後発国が技術導入で追い付く選択が弱い。経験蓄積に加え、対象を限定した技術導入・改造投資を比較する。[産業経験](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/experience.py#L1)、[トラック導入](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/trucks.py#L1)。比較の整理：[OECD Oslo Manual](https://www.oecd.org/en/publications/oslo-manual-2018_9789264304604-en/full-report/component-10.html)。

#### E14：鉱床の残量・品位・深さ・探鉱を持たない〔P3〕

**現状：** 地下資源の有無と定義済み生産方式で採掘する。継続採掘による鉱床条件や採掘費の変化は見当たらない。

**影響・案：** 資源があれば一定条件で供給し続け、追加供給の高コスト化や探鉱投資、資源国の転換動機が弱い。単純な枯渇だけでは新規国が詰むので、再探鉱・再資源化・代替調達と合わせて検討。[採掘を含む設備定義](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/facilities.yaml#L1)、[自然条件](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/nature.py#L1)。現実比較：[IEAの鉱物供給分析](https://www.iea.org/reports/global-critical-minerals-outlook-2025/overview-of-outlook-for-key-minerals)。

#### E15：原料高に対する技術選択は少数の既定方式に限られる〔P3〕

**現状：** 火力の石炭／燃料、消費財の複数方式など代替はあり、民間も採算を比較して切り替える。製鉄は鉄鉱石・石炭・電力の一方式。固定投入比率は短期工程としては妥当な近似になりうる。

**影響・案：** 設備・技術への投資で資源依存を変える余地が限られる。たとえばスクラップと電力を使う別方式を別設備・改造投資と結び付ける。石炭を追加電力だけで自由に置き換えるような物理的に不自然な修正はしない。[製鉄](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/facilities.yaml#L126)、[発電と消費財の複数方式](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/facilities.yaml#L185)、[方式の自動見直し](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L228)。

#### E16：同じ商品内の企業別品質・差別化が薄い〔P3〕

**現状：** 食品の品目別栄養・品質は既存。ただし同じ商品内の生産者・等級・ブランド・原産地による違いは薄く、在庫や市場は主に商品単位で扱う。

**影響・案：** 高賃金を高品質で補う国、低価格量産、特定顧客向け商品などの分化が弱い。自動車や衣料の少数等級から試し、市場と在庫の分割で計算量が増える点を測る。[品目別属性](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/goods.yaml#L1)、[在庫](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/stock.py#L29)、[商品ごとの市場](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L535)。

#### E17：農業の作付けから収穫までの期間が薄い〔P3〕

**現状：** 気候、肥沃度、肥料、食品の保管と劣化は既存。作付け・成長・収穫期を分けず、通常生産として毎ターン産出する。

**影響・案：** 収穫までの資金繰り、季節的供給集中、端境期の備蓄、収穫を見込んだ契約の意味が弱い。穀物だけの段階生産でも検討できる。毎ターン一定供給という平均化を残す設計も正当な選択。[生産](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/production/__init__.py#L167)、[気候係数](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/nature.py#L55)、[在庫劣化](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/stock.py#L103)。現実比較：[FAOの季節変動と貯蔵](https://www.fao.org/4/a0185e/a0185e04.htm)。

#### E18：汚染の蓄積・回復・対策費用の結び付きが薄い〔P3〕

**現状：** 現在の排出から汚染を毎ターン再計算する。土壌劣化は残るが、汚染が正なら濃度によらず同じ速度で進む。一般的な除染・排出削減設備への投資は見当たらない。

**影響・案：** 操業停止後にも残る被害や、排出を少しずつ減らす効果が弱い。流量の汚染と蓄積する汚染を分け、自然回復・対策費を加える場合も対象を限定する。[肥沃度低下](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/nature.py#L125)、[汚染の再計算](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/production/__init__.py#L562)。

### 7.4 取引・金融・倒産

#### E19：価格設定が毎ターンの共通市場に寄る〔P3〕

**現状：** 値幅、指値、過去価格の参照、公定価格、継続する二国間契約等はある。「価格硬直性ゼロ」「契約なし」ではない。一方、多くの財を毎ターンの板寄せで扱い、企業・顧客別の価格改定周期や取引関係は薄い。

**影響・案：** 原料価格と最終価格の時間差、据置価格、長期顧客の条件が均される。財の種類ごとの改定周期等を試す前に、現在の値幅と平均参照が既に生む遅れを計測する。[価格決定](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/market/auction.py#L1)、[家計指値](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L1027)。現実比較：[ECBの価格設定調査](https://www.ecb.europa.eu/press/economic-bulletin/focus/2019/html/ecb.ebbox201907_05~99afe2b4fe.en.html)。

#### E20：企業融資は全件、毎ターン金利を改定する〔P3〕

**現状：** 建設と運転資金の期間、据置は区別されるが、既存融資も毎ターン金利を再計算する。国債には固定利払い、複数満期、時価評価がある。

**影響・案：** 利上げが借換時まで効かない企業と、即時に効く企業の違いが弱い。固定／変動と改定周期を融資契約に持たせると、金利政策の時間差を表せる。[既存融資の金利](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/banking/__init__.py#L143)、[国債](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/debt.py#L1)。現実比較：[ECBの融資条件研究](https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3078~69f1be5d2f.en.pdf)。

#### E21：企業間の掛売り・売掛金が薄い〔P2〕

**現状：** 市場では買い手が資金を取り置き、約定時に代金を支払う。銀行の運転資金融資はあるが、仕入先が後払いを許す企業間信用は見当たらない。

**影響・案：** 商売は黒字でも入金が間に合わない、顧客の倒産で仕入先の売掛金が焦げ付く、という連鎖が薄い。限定的な支払期日・売掛／買掛を導入するなら、債権債務、延滞、倒産配分まで一組で設計する。[資金取り置き](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L776)、[即時決済](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L1621)。現実比較：[ECBの企業間信用研究](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp753.pdf)。

#### E22：企業・政府の外貨建て債務による危機が薄い〔P3〕

**現状：** 外貨保有、外国国債の保有、中央銀行間融通、為替・金利のリスクはある。ただし外国人が持つ国債も発行国通貨建てで、企業の外貨借入や外貨建て国債は中心的な対象ではない。

**影響・案：** 通貨安による輸出効果に加え、借入負担増で投資が落ちる経路が弱い。外貨債務を追加するなら輸出外貨収入との対応、破綻処理、国際会計を一緒に検証する。[外国国債の前提](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/abroad.py#L1)、[中央銀行間融通](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/swap.py#L1)。現実比較：[BISの企業投資と為替研究](https://www.bis.org/publications/working-paper-839-corporate-investment-and-exchange-rate-financial-channel)。

#### E23：休業等のリスクを商業保険で分担しない〔P3〕

**現状：** 預金保護・銀行救済はある。火災・事業中断・貨物等の商業保険、保険会社・再保険は見当たらない。現在の自然災害は原則として単施設の一時停止であり、大規模な資産全損ではない。

**影響・案：** 保険料と予備資金の選択、災害損失の分散、保険会社へ集中するリスクがない。災害を重くする際に検討し、今すぐ保険産業を一式追加する必要はない。[自然災害](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/events/__init__.py#L14)、[預金保護](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/banks.py#L210)。現実比較：[英中銀の保険と金融安定](https://www.bankofengland.co.uk/quarterly-bulletin/2015/q3/insurance-and-financial-stability)。

#### E24：倒産後の設備売却代金が最大貸付銀行へ集中する〔P2〕

**現状：** 現金を融資へ按分返済し、残債を貸倒処理してから、最大の貸し手を差押先にする。後日の設備売却代金はその1行だけへ入り、その銀行も破綻していれば国庫へ入る。差押・再売却・再稼働自体はある。

**影響・案：** 貸付額の小差で回収に大差が生じ、銀行間の損失配分が変わる。旧債権者の回収プールを残すなど、担保・順位・配分を簡潔なゲーム仕様として明示したい。特定国の倒産法を丸ごと模写する必要はない。[倒産処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/failures/__init__.py#L141)、[売却代金](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L1024)。比較：[UNCITRAL倒産制度ガイド](https://uncitral.un.org/en/texts/insolvency/legislativeguides/insolvency_law)。

#### E25：私企業の撤去後、残存在庫が無償で国の倉庫へ移る〔P2〕

**現状：** 撤退前に在庫売却を試すが、最終的な残存在庫は国の倉庫へ移す。差押施設も一定期間売れなければ同様。残存在庫への対価を所有者・旧債権者へ払う処理は見当たらない。

**影響・案：** 不況時に民間資源が政府へ集まる経路となる。意図した仕様かを確認し、残存資源の所有権・処分・代金の受取先を決める。同種施設の再売却はあるため、「退出資産が全部消える」わけではない。[通常撤退](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L388)、[差押後の撤去](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L409)、[残存在庫移管](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/firms/__init__.py#L1014)。

### 7.5 貿易・制度・行政

#### E26：海運の国際分業が薄い〔P3〕

**現状：** 通常の輸入は買い手国の船が空荷で売り手へ行き、積んで帰る。第三国の輸送業者、複数寄港、帰り荷は見当たらない。船員・燃料・容量・港・運賃は既存。

**影響・案：** 自国船を持たず製造業に集中する国や、他国間の海運で稼ぐ国が成立しにくい。まず輸送サービスの委託を導入するかを決める。往復時間等を単に削除すると物流費の意味を失う。[輸送の前提](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/ships.py#L1)。実際のサービスの例：[Maersk Spot](https://www.maersk.com/transportation-services/maersk-spot)。

#### E27：供給契約の履行義務が軽い〔P2〕

**現状：** 継続する二国間契約はある。可能な数量を履行し、不足を報告するが、未納の累積・追加履行義務・違約金・保証金は薄い。外交・禁輸・人間の報復があり得るため「不履行に一切の影響がない」とはしない。

**影響・案：** 信頼できる相手に高い価格を払う、契約用に在庫・生産余力を持つ動機が弱い。最低履行率、保証金、免責条件を任意の契約条件として検討する。供給事故へ一律罰金を課すだけでは、危機を増幅する。[契約の前提](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/contracts.py#L10)、[履行処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/trade/__init__.py#L660)。

#### E28：税率に加えて徴税能力を育てる仕組みが薄い〔P3〕

**現状：** 所得税は賃金支払時、消費税は取引時に徴収する。法人税は手元資金等の制約を受けるため「税は必ず全額取れる」わけではない。公務員の費用と行政力はあるが、納税者把握、非公式部門、脱税・徴収能力の独立モデルは薄い。

**影響・案：** 行政基盤を育てて実際の税収捕捉率を高める政策が弱い。税率と徴収可能性を分ける場合、恣意的な脱税乱数より、把握率・執行費用・制度への信頼をどう遊びにするかから決める。[所得税](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/labor/__init__.py#L178)、[消費税](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/market/__init__.py#L1645)、[法人税](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/accounts/__init__.py#L237)。現実比較：[IMFの徴税能力](https://www.imf.org/en/publications/staff-discussion-notes/issues/2023/09/15/building-tax-capacity-in-developing-countries-535449)。

#### E29：政治体制の名前が結果へ直接効く〔P2〕

**現状：** 体制ごとに工期・安定・消費の種類等へ固定の特色が効く。例として君主制の安定と共和制の条件付き効果は対称でない。選挙・議会・不満・暴動自体は既存。

**影響・案：** 制度名だけで優劣が決まる印象になり、国家能力・説明責任・承認手続・住民との調整を通じた効果が薄い。特色を残すなら建国時に明示し、体制比較では固定ボーナスを分けて計測する。「君主制が常に最強」は未検証。[体制定義](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/regimes.yaml#L1)、[特色の適用](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/politics.py#L1)。

#### E30：中央銀行の独立性と使える政策手段が束ねられる〔P2〕

**現状：** 独立フラグは金利の自律化だけでなく、政府による国債政策売買、直接引受、手動為替介入、中央銀行の外国国債購入等にも関わる。

**影響・案：** 政府が中銀に命令できないことと、中銀が独自判断で市場操作を行えることの区別が薄い。政府の権限、中銀の目的・反応関数、許容する政策手段、直接財政支援を分けて定義する。[独立性の切替](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/central_bank/__init__.py#L187)。現実比較：[ECBの独立性と資産購入の説明](https://www.ecb.europa.eu/press/key/date/2018/html/ecb.sp181026.en.html)。

### 7.6 今回追加したモデル上の論点

#### E31：船の寿命接近を見越した更新需要が薄い〔P2〕

**現状：** 船は使用開始時点別に保持し、寿命到達で解体する。買い増し希望は直近の船不足履歴から決め、寿命接近に基づく先回りの買い替え条件は見当たらない。初期配備船の寿命は300ターン。

**影響・案：** 同時に配備した船がまとめて寿命に達し、船不足が観測されてから注文するなら、調達・建造・航海の間に輸送が落ちる可能性がある。全ての世界で停止することは未再現。初期配備の時点も踏まえ、300ターンを超えて観察し、老朽船の予告需要と納期を接続する。[購入希望](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/ships.py#L76)、[寿命解体](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/ships.py#L111)、[初期船と寿命](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/game.yaml#L174)。

#### E32：GDPの集計範囲と名目・実質を明示したい〔P2〕

**現状：** 生産・支出の両側をflowsから集計する。コードの前提では銀行の利ざやをGDPへ入れず、銀行職員の賃金を移転として扱う。画面のGDPは現在価格の金額が中心で、CPIが別にあること自体は産業全体の実質GDPを計測したことにはならない。

**影響・案：** 金融部門の規模、物価、為替が違う国を「豊かさ」や生産量で比較すると読み違えやすい。集計対象を説明し、必要なら一定価格の産出・付加価値、実質消費、物量・不足率も併記する。SNAの金融サービス推計を全部実装する要求ではない。A08の内部照合とも別の論点。[GDPの範囲](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/books.py#L1)、[順位の指標](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/server/web/rankings.py#L26)。比較：[国連の金融サービス計測の整理](https://unstats.un.org/unsd/nationalaccount/issue16.asp)、[世界銀行の実質国民経済計算の説明](https://datahelpdesk.worldbank.org/knowledgebase/articles/114968-how-do-you-derive-your-constant-price-series-for-t)。

#### E33：「スワップ」という名称と、外貨貸付の近似を説明したい〔P3〕

**現状：** 中央銀行間の融通は、同じ外貨を貸し、その外貨で返す貸借として実装されている。二通貨を交換して満期に逆交換する二つの脚は持たない。借入枠・残高の制約はある。

**影響・案：** 典型的な中銀通貨スワップの仕組みを想定すると、資金供給源や担保・リスクを誤解する。外貨信用枠という用語に寄せるか、通貨交換まで実装するか決める。現行の国際融通が機能しないという指摘ではない。[融通の実行](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/swap.py#L192)。比較：[FRBの流動性スワップFAQ](https://www.federalreserve.gov/monetarypolicy/bst_swapfaqs.htm)。

#### E34：災害の地理的広がりと同時被害が薄い〔P3〕

**現状：** 自然災害は島ごとの発生抽選から非住宅の建設済み施設を一つ選び、数ターン停止させる方式。洪水・台風等の種類はあるが、その処理では河川・沿岸・施設集中を使って複数施設の同時被害を決めない。暴動の範囲被害は別に存在する。

**影響・案：** 工業を集中して集積効果を取るか、分散して同時停止を避けるかという立地判断が弱い。重い災害を追加する場合に、地域単位の相関、防災投資、保険、復旧能力と一緒に検討する。現行の穏やかな災害は意図した仕様と読めるため、修正必須ではない。[自然災害の意図と処理](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/modules/events/__init__.py#L1)、[災害定義](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/game.yaml#L460)。

#### E35：廃棄・解体から資源を回収する循環が薄い〔P3〕

**現状：** 食品の腐敗、車の摩耗、船の寿命解体はあるが、回収材・スクラップの財や、それらを原料へ戻す市場は見当たらない。物量を系外へ出すこと自体は簡略化として成立し、直ちに帳簿不整合ではない。

**影響・案：** 回収業の雇用、資源価格によるリサイクル採算、廃棄処理費用が薄い。E15の別製鉄方式やE01の保守を追加する場合に、廃車・解体船等の一部を回収する循環と組み合わせる。廃棄物全種類を今すぐ追加する要求ではない。[腐敗](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/stock.py#L103)、[車の減耗](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/economy/needs.py#L106)、[船の解体](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/hakoniwa_core/trade/ships.py#L111)、[財一覧](https://github.com/Yamada-Kohei/economic-hakoniwa/blob/b4611b7f7276e069039a6f81b032bdbb604f796a/packs/base/data/goods.yaml#L1)。

## 8. 誤解を避けるための補足・訂正

- 価格は指値・値幅・履歴・公定価格等を持つ。価格の調整や硬直性が一切ないわけではない。
- 食品の品目別品質、食品の劣化、車の保有台数・燃料・劣化・買い替えは実装済み。E16・E17はそれらを否定しない。
- 火力・消費財等の複数方式と民間の切替判断はある。投入代替がゼロではない。
- サービス業全体が欠落しているわけではない。E02は主に家計が買う生活サービスの話。
- 銀行は信用創造・返済時の預金消滅を扱う。単なる預金の又貸しモデルという批判は不正確。
- 国債の固定利払い・満期・時価評価、外国国債、外貨、中央銀行間融通はある。金融・国際資本が一切ないわけではない。
- 自国通貨国債の不履行は、発行能力が存在しないから必然的に起きるのではなく、入札・借換・引受方針等の制度と定数で起きる。救済・債務削減の政策値はゲーム仕様として評価する。
- 既存雇用の継続・引き抜き制限がある。「毎ターン全員を自由に再配分」は誤り。
- 一般工場の更新需要が薄くても、機械需要が必ず消えるとは限らない。造船、修理、他の設備更新等がある。
- 企業退出では現金の返還、差押、施設再売却がある。「倒産・資産処理がない」「資産が全部消える」は誤り。
- 自然災害は穏やかな一時停止に抑えられている。火災・地震で大量の設備が恒常的に失われる現状とは扱わない。
- 町・都市等の居住上限と現住人口は別で、実人口の表示もある。スプライトの建物密度や1マスの人口を、面積の定義なしに現実の人/km²と比較しない。絵の印象と統計の問題を分ける。
- 803件のテスト成功は経済の面白さや長期安定の保証ではなく、266件の関門逸脱も266個のバグではない。会計一致はモデルの現実妥当性とは別である。

## 9. 開発を進める順序と、確認したい結果

| 段階 | 主な対象 | 確認したい結果 |
|---|---|---|
| 1. 公開・復旧の整合性 | A01・A02、A07の公開方針 | 復旧後に更新を継続できる。本人以外に返すデータが全経路で意図どおり |
| 2. 運用と検査の信頼性 | A03～A06・A08、B02 | 並行保存・中断・異常入力で状態が食い違わず、関門が正しい期間・境界を判定 |
| 3. 説明と診断 | U01～U08 | 初見で不足原因を辿り、政策の結果を振り返れる。内部キーや古い説明がない |
| 4. 既存経済の長期検証 | B01・B03・B04、E24・E25・E31 | 少人数・成熟・船更新後でも継続可能。退出資産の配分が意図どおり |
| 5. 少数のモデル実験 | E01・E02・E03・E07～E10・E21・E27等から選ぶ | 想定した因果と、新しいプレイヤーの判断が実際に生まれる |
| 6. 世界観に合わせた拡張 | その他E項目、軍事等 | 計算量と説明負担に見合う遊びが増える |

個別の追加案を採る際は、「再現したい現象」「最小の仕組み」「見たい指標」「従来との差」「プレイヤーが理解する表示」を先に一組で定めたい。例として、設備更新なら成熟国の機械需要・投資・雇用、掛売りなら支払期日別債権・延滞・倒産連鎖を見る。

## 10. Claudeへの引き継ぎ事項

1. まず対象コミットと現在の実装を照合し、既に修正・仕様変更された項目は除外する。
2. P1の2件を再現し、情報公開方針と巻き戻し仕様を明文化する。再現は使い捨ての検証環境で行う。
3. 静的な懸念は再現試験を先に作り、未確認の可能性を確定不具合として扱わない。
4. 経済項目は「採用」「現状維持・説明追加」「後回し」「不採用」に分け、採否の理由を示す。現実にあるという理由だけで全部を追加しない。
5. 関門の測り方を整えてから、AI・初期条件・モデルを分けて長期検証する。閾値変更だけで合格を作らない。
6. 変更は小さなまとまりで行い、会計照合・再現性・保存互換性・必要なUIを一緒に確かめる。

この報告書はレビューと検討材料の引き継ぎであり、本番への変更や全項目の一括実装を指示・承認するものではない。
