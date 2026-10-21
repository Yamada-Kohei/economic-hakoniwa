# 定義データの書き方

ゲームの財・地形・施設・命令は、すべて YAML の定義データです。本体の定義も
`packs/base/` という「本体パック」に入っていて、追加パックと同じ仕組みで読み込まれます。

```
packs/base/
  pack.yaml            パックの名前と版
  data/
    goods.yaml         財
    terrain.yaml       地形
    facilities.yaml    施設
    orders.yaml        命令
    army.yaml          部隊の種類（陸の歩兵・機甲。フェーズ4-2）
   settlements.yaml   町の段階（村・町・都市・大都市）と、育つのに使う資源
    workers.yaml       労働者の種類
    regimes.yaml       体制の選択肢
    start_island.yaml  建国直後の島の中身
    naming.yaml        地名の様式と、施設に自動で付く名前の材料
    finance.yaml       金融の部品（仕訳の種類と国債の期間）
  locale/ja/
    goods.yaml など    data/ と同じ名前のファイルに表示名
    recipes.yaml       作り方の表示名
    messages.yaml      ターンログ・命令の失敗の理由・画面の文面
```

## 基本の決まり

- 1つのファイルには「名前: 中身」を並べます。名前は英小文字・数字・`_` だけ（例: `steel_mill`）。
- 表示名（日本語）は定義ファイルに書かず、`locale/ja/` の同じ名前のファイルに書きます。
  表示名がない定義は誤りになります。
- 各ファイルの形は `schemas/` の JSON Schema で決まっています。項目の意味もスキーマの `description` にあります。
- 財の量は `2.5` のように小数で書けます。内部では 1000 分の 1 単位の整数（2500）になります。

## 例: 製鉄所

```yaml
# data/facilities.yaml
steel_mill:
  category: industry                 # 分類
  terrain: [plain]                   # 建てられる地形
  construction:
    turns: 6                         # 資材と人手がそろったときの工事のターン数
    labor: 800                       # 工事に1ターンあたり要る人手
    materials: {building_materials: 30, machinery: 5}   # 工事全体で要る資材
  workers: 2000                      # 全力で動くのに要る労働者
  recipes:                           # 作り方（複数あれば建てるときに選ぶ）
    default:
      inputs: {iron_ore: 10, coal: 5, electricity: 5}   # 1ターンの投入
      outputs: {steel: 5}                               # 1ターンの産出
  sprite: steel_mill                 # 絵の名前
```

```yaml
# locale/ja/facilities.yaml
steel_mill: 製鉄所
```

## それぞれのファイル

| ファイル | 1項目の中身 | スキーマ |
|---|---|---|
| `goods.yaml` | `tier`（1 原料、2 中間財、3 最終財）、`unit`（量の単位。表示名は `locale/<言語>/units.yaml`）、`shelf_life`（持ち越せるターン数。0 は持ち越せない。書かなければ無期限）、`food`（食べられる財の `nutrition` と `quality`）、`mining`（建国時に選べる鉱業資源）、`arms`（軍事品。貿易圏の市場に出さず、国どうしの契約と物の援助でだけ動く。フェーズ4-2）、`consumer`（家計が買う財。物価指数に入れる） | `schemas/goods.schema.json` |
| `terrain.yaml` | `land`（陸地か）、`reclaimable`（埋め立てられるか）、`height`（描画用の高さ） | `schemas/terrain.schema.json` |
| `facilities.yaml` | 上の例のとおり。ほかに下の表の項目 | `schemas/facilities.schema.json` |
| `orders.yaml` | `admin_cost`（要る行政力。ふつう 1、名前の変更は 0）、`payload`（命令の中身の形を JSON Schema で） | `schemas/orders.schema.json` |
| `army.yaml` | `arm`（land・sea）、`soldiers`（部隊 1 つの兵の数）、`equipment`（持つ装備。財 → 数）、`weight`（船で運ぶ重さ）、`speed`（1 ターンに進める量）、`vehicles`（動くと燃料を使うか）、`strength`（強さ） | `schemas/army.schema.json` |
| `settlements.yaml` | `min_population`（この段階になる人口）、`capacity`（住める人数）、`height`（描画用の高さ）、`subsistence`（働き口がなくても自給の農業で暮らせるか）、`materials`（この段階に育つ・いちばん下の段階なら新しくできるときに倉庫から使う資源） | `schemas/settlements.schema.json` |
| `workers.yaml` | `share`（住民のうち、この種類の労働者になる割合） | `schemas/workers.schema.json` |
| `regimes.yaml` | `axis`（政治の仕組み・国家元首の形・経済体制のどれか）、`capital_building`（首都に建つ建物）、経済体制では `owner`・`state_owned`・`state_accounting`・`start`・`bank_owner`（建国のときに置く銀行の持ち主）・`capital_controls`（資本取引の既定。フェーズ3-3） | `schemas/regimes.schema.json` |
| `start_island.yaml` | 首都の施設と周りの町、村の数と人口、最初の施設（`if_resource` で資源があるときだけ）、送電線、倉庫の中身 | `schemas/start_island.schema.json` |
| `finance.yaml` | `type: journal` は仕訳の種類（お金の動きの名前。帳簿の確かめは、ここにない種類の仕訳を誤りにする）、`type: bond_term` は国債の期間（`turns` と期間の上乗せの利回り `premium`） | `schemas/finance.schema.json` |
| `naming.yaml` | 言語ごとの、地名の様式（`places`。様式ごとに表示名 `label`・前半 `first`・後半 `second`・つなぎ `joiner`）、数字、施設の種類ごとの短い名前と長い名前、名前の形 | `schemas/naming.schema.json` |

施設の項目:

| 項目 | 意味 |
|---|---|
| `category` | 分類（primary・industry・power・admin・housing） |
| `industry` | 産業の分類（農業・鉱業・鉄鋼など。表示名は `locale/<言語>/industries.yaml`）。経験・集積・称号はこの分類ごと（フェーズ3-3） |
| `terrain` | 建てられる地形 |
| `coastal` | `true` なら海に面したマスだけ |
| `near_capital` | 首都からこのマス数以内だけ |
| `max_per_island` | 1つの国が持てる数（工事中も数える）。鉱山は 2 |
| `buildable` | `false` なら命令では建てられない（町は人口に合わせて自然に広がる） |
| `construction` | 工事のターン数（`turns`）、1ターンに要る人手（`labor`）、工事全体で要る資材（`materials`） |
| `workers` | 全力で動くのに要る労働者の数 |
| `priority` | 雇用・原料の配分・電力の配分の優先順位（0〜9、大きいほど先）。命令で施設ごとに変えられる |
| `recipes` | 作り方。`inputs`・`outputs` は1ターンあたり。`requires_resource` はその地下資源を選んだ島だけで使える |
| `pollution` | まわりのマスへの汚染（`amount` と `radius`） |
| `admin_power` | 働く人1人あたりに増える行政力（中央省庁。0.001 なら 1,000 人で 1） |
| `public_service` | 公務員の施設（中央省庁など）。賃金を国庫が払う |
| `zone` | その指定（`office` のオフィス街など）のあるマスにだけ建つ（銀行） |
| `tileless` | マスを持たない施設（運送業）。島に1つ自動で置かれる |

建てられる地形は施設の側（`terrain:`）にだけ書きます。地形の側には書きません。

## 検査

次の3か所で同じ検査が走ります。誤りは「ファイル名:行: 内容」で示します。

- `python manage.py check_defs`（または `python -m hakoniwa_core.defs`）
- サーバーの起動時（`runserver` や `manage.py check`）
- 自動テストと CI

形の検査（スキーマ）に加えて、「レシピに書いた財が定義されているか」「施設の地形が定義されているか」
「表示名がそろっているか」のような、ファイルをまたぐ確認も行います。

## game.yaml のゲームの数値

施設や財に結びつかない数値は `game.yaml` に置きます。

| 節 | 中身 |
|---|---|
| `calendar` | 世界の暦の名前（`name`、既定「箱庭暦」）と、ターン1 の年（`first_year`、既定 1）。1ターンが1か月で、1年のターン数（12）は `hakoniwa_core/dates.py` の `TURNS_PER_YEAR` だけに書く |
| `orders` | 行政力（1ターンに実行できる命令の数）の最低（`base_admin_power`）と最大（`max_admin_power`）、並べられる命令の数（`max_queue`） |
| `founding` | 選べる資源の数、描き直せる地形のマス数、自動で配る地形の割合、名前の長さの上限 |
| `labor` | 町から通える距離（`commute_radius`）、優先度の既定、初めの賃金と賃金の動き方（フェーズ2） |
| `market` | 値幅（`band`）、希望価格の動きやすさ、操業目標と在庫の目安、初めの値段の解き方（フェーズ2） |
| `freight` | 運賃の率（道のり1あたり・上限）と運送業（フェーズ2） |
| `household` | 家計の層、使う額の式の係数、食費と消費財の買い方（フェーズ2） |
| `firms` | 減価償却のターン数、運転資金など施設のお金、民営工場の参入と撤退・倒産と差し押さえ（フェーズ2） |
| `banking`・`loans`・`central_bank`・`bonds` | 銀行の置き方と金利・規制・破綻処理、融資の審査、中央銀行、国債の入札と売買（フェーズ2）。市中銀行の外国の資産の上限と買い方（`banking.foreign_*`。フェーズ3-3） |
| `trade`・`fx`・`foreign_merchant` | 港と船・商社・契約、為替の値幅と市場の厚み、固定相場の幅（`fx.peg_band`。フェーズ3-3）、海外の商人（フェーズ3-1） |
| `experience` | 産業の経験の係数（伸び方・上限・組み立て産業の上限）、動かさなかった年の減り方、集積の半径と係数（フェーズ3-3） |
| `fiscal` | 税率と予算の既定、予算を決められる項目（フェーズ2） |
| `records` | 値段の履歴・物価指数の重み・仕訳・約定・施設の帳簿を残すターン数（フェーズ2） |
| `power` | 電気の財、住民1人あたりの電力、町への配電の優先度 |
| `population` | 1人あたりの食料、増え方と減り方、幸福度の計算の重み |
| `housing` | 町が1ターンに新しくできる・育つ数 |
| `naming` | 地名の区画の大きさ（`place_block`）、地名の様式の既定（`place_style`） |
| `disasters` | 災害の起きる割合と、施設が止まるターン数 |
| `accounts` | 同じ接続元から登録できる数（`signups_per_ip`、0 で無制限）と、覚えておく日数 |

数値は試し遊びと一括シミュレーションで調整する仮の値です。
