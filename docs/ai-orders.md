# AI 用の命令例と確認の手順（W-37）

命令の形は `GET /api/defs/orders` の `orders.<kind>.payload` が正本です。
`GET /api/defs` には命令の表示名・行政力と、施設・財・生産方式の名前があります。
この文書は 2026-10-25 の定義と API の単位変換に合わせています。
例の島番号・座標・部隊番号・契約番号・金額は、今の世界の値に替えてください。
JSON としてそのまま読み込めますが、どの国でも実行できるという意味ではありません。

## 列を確認してから置き換える

1. `me`・`summary`・`orders <島>` で、操作する国、行政力、現在の列を確認します。
2. `get api/defs/orders` と `get api/defs` で命令と施設の定義を確認します。
3. 残したい既存命令も含めた **列全体** を UTF-8 の JSON ファイルに書きます。UTF-8 BOM も読めます。
4. `check <島> @orders.json` で全件を確認し、終了コードが 0 の場合だけ置き換えます。
5. `orders <島>` で保存結果を読み、更新後は `changes` と命令の結果を確認します。

PowerShell（`python` がない環境では、その環境の Python の実行ファイルを指定）:

```powershell
python tools/playtest/hk.py me
python tools/playtest/hk.py orders 4
python tools/playtest/hk.py get api/defs/orders
python tools/playtest/hk.py get api/defs
python tools/playtest/hk.py check 4 '@orders.json'
if ($LASTEXITCODE -eq 0) {
    python tools/playtest/hk.py orders 4 '@orders.json'
}
python tools/playtest/hk.py orders 4
```

POSIX シェル:

```sh
python tools/playtest/hk.py check 4 @orders.json &&
  python tools/playtest/hk.py orders 4 @orders.json
```

`check` は、1件の `{kind, payload}`、その配列、`{"orders": [...]}` を受け取ります。
列の場合は配列の順に、既存の `POST /api/islands/<島>/orders/check` を1件ずつ呼び、
`results` に1から始まる `index` と `ok`・理由・注意・解釈した命令を返します。
HTTP が成功しても、1件でも `ok: false` なら終了コードは 1 です。通信・権限の失敗時も置き換えません。
空配列は確認する命令がないので成功します。`orders` に空配列を送ると待ち命令がすべて消えます。

確認は **同じ現在の状態に対する個別確認** です。前の命令の効果を反映した予行演習ではありません。
同じマスへの二重建設、同じ資材や資金の取り合い、前の命令で新しく作る契約への承認などは保証しません。
行政力の合計、列の順序、更新までの状態の変化と実行時の議会の採決も別に確認します。
確認中にターンが進んだら、新しい状態で確認し直してください。
列は自動で分割・保存しません。API の呼び出し数は命令の数だけ増えるため、429 のときは
`Retry-After` の期間を待って読み直します。

JSON API からは同じ単体確認の後、`PUT /api/islands/<島>/orders` に `{"orders": [...]}` を送ります。
MCP では `defs(section="orders")` で定義を読み、`check_order` で各命令を確認してから `set_orders` で列全体を送ります。
最新の道具名と引数は `tools/list` を参照してください。

## よく使う命令

以下の JSON はそれぞれ **別のファイル** として保存し、`check 4 '@build.json'` のように確認できます。
命令の列にするときは、選んだオブジェクトを `[` と `]` で囲み、カンマで区切ります。
座標は0から始まります。財の量・値段は、画面と同じ表示単位で指定し、内部の1000分の1単位や
表示倍率を掛け直しません。税率・割合は %、予算は年額（1ターンはその12分の1）です。

### 建設・撤去・作り方・送電線

農場の穀物の基本方式は `default`。候補のマスが平地で空いているか、建設の人手と資材があるかを確かめます。

```json
{"kind":"build","payload":{"facility":"farm","recipe":"default","x":14,"y":12}}
```

撤去は首都などの保護された施設ではできません。

```json
{"kind":"demolish","payload":{"x":14,"y":12}}
```

国営の農場を綿に替える例です。民営施設の方式は企業が決めます。作物は蒔き直して育つまで待ちます。

```json
{"kind":"change_recipe","payload":{"x":14,"y":12,"recipe":"cotton"}}
```

```json
{"kind":"build_power_line","payload":{"tiles":[[13,12],[14,12],[15,12]]}}
```

### 税・予算・失業手当

書いた税だけを変更します。大きな変更には議会の採決があります。

```json
{"kind":"set_taxes","payload":{"income_tax":15,"corporate_tax":20,"consumption_tax":5}}
```

```json
{"kind":"set_budget","payload":{"construction":120000,"education":60000,"warehouse":24000}}
```

```json
{"kind":"set_labor_policy","payload":{"unemployment_benefit":40}}
```

### 公定価格・公共料金・倉庫の売買

公定価格は計画経済だけです。`price: 0` で上書きを解除します。穀物1トンの値段を指定する例です。

```json
{"kind":"set_official_price","payload":{"good":"grain","price":100}}
```

電力1 MWh の料金の上限を指定する例です。`price: 0` で上限をなくします。

```json
{"kind":"set_price_cap","payload":{"good":"electricity","price":100}}
```

倉庫の穀物10トンを指値なしで売ります。国の工事などに先に使う分があるため、売りの成立量は更新後に確認します。

```json
{"kind":"warehouse_trade","payload":{"good":"grain","side":"sell","quantity":10}}
```

買う場合は指値と倉庫の買い入れ予算が必要です。

```json
{"kind":"warehouse_trade","payload":{"good":"grain","side":"buy","quantity":10,"limit":100}}
```

### 貿易・契約

島5から毎ターン穀物10トンを12ターン買い、届いた荷を国内の市場に出します。
通貨の番号は島番号です。`currency: 5` は相手の通貨建てで、外貨が必要です。
港・船・相手の供給と承諾も確認します。市場へ出すことは家計が無料で受け取ることではありません。

```json
{"kind":"propose_contract","payload":{"partner":5,"side":"buy","good":"grain","quantity":10,"price":100,"currency":5,"turns":12,"delivery":"market"}}
```

相手の提案を買い手として受ける例です。契約番号は `get api/islands/4/trade` で確認します。
提案した命令と同時に、まだ存在しない契約番号を指定してはいけません。

```json
{"kind":"accept_contract","payload":{"contract":7,"delivery":"market"}}
```

商社の貿易を始める入口の一つは、開放された貿易圏への加入です。番号は貿易の API で読みます。

```json
{"kind":"join_bloc","payload":{"bloc":2}}
```

### 条約

島5と不可侵・中央銀行の融通を約束する例です。戦争中の相手には使えません。
融通（スワップ）は外貨の貸し借りで、通貨の交換ではありません。返済と利払いが必要です。
相手の署名がそろってから効きます。議会のある国は提案・署名にも採決が必要です。

```json
{"kind":"propose_treaty","payload":{"partner":5,"title":"友好と決済の協力","clauses":[{"type":"non_aggression"},{"type":"swap"}]}}
```

```json
{"kind":"sign_treaty","payload":{"treaty":3}}
```

条約番号は `diplomacy` または `get api/islands/4/diplomacy` で読みます。

### 部隊の編成・移動

自国の兵舎の座標に歩兵を編成します。兵・装備・予算が必要です。

```json
{"kind":"form_unit","payload":{"army":"infantry","x":14,"y":12}}
```

`military` で番号と編成完了を確かめた後、部隊2の行き先を指定します。

```json
{"kind":"move_unit","payload":{"unit":2,"x":15,"y":12}}
```

部隊への命令は実行後も持ち主だけに見えます。移動は到着まで続き、燃料・地形・戦争や停戦の状態に左右されます。

## アカウントの切り替えと短い一覧

`hk.py --profile <名前>` は、その呼び出しだけ接続先・鍵のファイル・cookie・差分の記録を切り替えます。
設定はホームの `.hk-profiles.json`（`--profiles <絶対パス>` で変更）に置きます。
例の接続先はローカル開発用です。実際の LAN の接続先や保存ファイルはリポジトリへ写しません。

```json
{
  "player1": {"base":"http://127.0.0.1:8000"},
  "player2": {"base":"http://127.0.0.1:8000"}
}
```

指定を省いた保存先はホームの `.hk/<名前>/key.txt`・`cookies.txt`・`state.json` です。
既存のファイルを使う場合だけ `key_file`・`cookies`・`state` にパスを書きます。
相対パスは設定ファイルのある場所から解決します。`base` に資格情報は含めません。
名前は英数字・`_`・`-` だけです。設定へ鍵やログイン情報そのものを書ける項目はありません。
既存ファイルの移動・コピーは行いません。

```sh
python tools/playtest/hk.py profiles
python tools/playtest/hk.py --profile player1 key
python tools/playtest/hk.py --profile player1 me
python tools/playtest/hk.py --profile player1 facilities
python tools/playtest/hk.py --profile player1 facilities --status 電力不足
python tools/playtest/hk.py --profile player2 summary
python tools/playtest/hk.py --profile player2 check 5 @orders.json
```

プロファイル選択時は環境変数の `HK_KEY`・`HK_KEY_FILE`・`HK_COOKIES`・`HK_STATE` と
ログイン用の `HK_USER`・`HK_PASSWORD` を引き継ぎません。別の国の鍵と混ざるのを避けるためです。
選択していない呼び出しは従来の環境変数と保存先を使います。`--base` の明示指定はプロファイルより優先します。
MCP の stdio の引数にも `--profile player1 mcp` と書けます。

`facilities [島]` は施設番号・座標・表示名・状態・稼働率・従業者・損益・預金を1施設1行で出します。
`--status` は状態の表示名との完全一致です。`--json` はフィルター前の API の JSON を返します。
他国の施設の非公開情報を補いません。`map` の文字の地図も引き続き使えます。
標準出力・標準エラーは UTF-8 です。Windows の端末・読み取るプログラムも UTF-8 に合わせてください。
