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
    settlements.yaml   住宅地の段階（村・町・市・大都市）
    workers.yaml       働き手の種類
    regimes.yaml       体制の選択肢
    start_island.yaml  建国直後の島の中身
    naming.yaml        施設に自動で付く名前の材料
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
    labor: 80                        # 工事に1ターンあたり要る人手
    materials: {building_materials: 30, machinery: 5}   # 工事全体で要る資材
  workers: 300                       # 全力で動くのに要る働き手
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
| `goods.yaml` | `tier`（1 原料、2 中間財、3 最終財）、`shelf_life`（持ち越せるターン数。0 は持ち越せない。書かなければ無期限）、`food`（食べられる財の `nutrition` と `quality`）、`mining`（建国時に選べる鉱業資源） | `schemas/goods.schema.json` |
| `terrain.yaml` | `land`（陸地か）、`reclaimable`（埋め立てられるか）、`height`（描画用の高さ） | `schemas/terrain.schema.json` |
| `facilities.yaml` | 上の例のとおり。ほかに下の表の項目 | `schemas/facilities.schema.json` |
| `orders.yaml` | `admin_cost`（要る行政力）、`payload`（命令の中身の形を JSON Schema で） | `schemas/orders.schema.json` |
| `settlements.yaml` | `min_population`（この段階になる人口）、`capacity`（住める人数）、`height`（描画用の高さ）、`subsistence`（働き口がなくても自給の農業で暮らせるか） | `schemas/settlements.schema.json` |
| `workers.yaml` | `share`（住民のうち、この種類の働き手になる割合） | `schemas/workers.schema.json` |
| `regimes.yaml` | `axis`（政治の仕組み・国家元首の形・経済体制のどれか）、`capital_building`（首都に建つ建物）、経済体制では `owner`・`state_owned`・`state_accounting`・`start` | `schemas/regimes.schema.json` |
| `start_island.yaml` | 首都の施設と周りの町、村の数と人口、最初の施設（`if_resource` で資源があるときだけ）、送電線、倉庫の中身 | `schemas/start_island.schema.json` |
| `naming.yaml` | 言語ごとの地名の材料（前半・後半）、数字、施設の種類ごとの短い名前と長い名前 | `schemas/naming.schema.json` |

施設の項目:

| 項目 | 意味 |
|---|---|
| `category` | 分類（primary・industry・power・admin・housing） |
| `terrain` | 建てられる地形 |
| `coastal` | `true` なら海に面したマスだけ |
| `near_capital` | 首都からこのマス数以内だけ |
| `buildable` | `false` なら命令では建てられない（住宅地は人口に合わせて自然に広がる） |
| `construction` | 工事のターン数（`turns`）、1ターンに要る人手（`labor`）、工事全体で要る資材（`materials`） |
| `workers` | 全力で動くのに要る働き手 |
| `priority` | 雇用・原料の配分・電力の配分の優先順位（0〜9、大きいほど先）。命令で施設ごとに変えられる |
| `recipes` | 作り方。`inputs`・`outputs` は1ターンあたり。`requires_resource` はその地下資源を選んだ島だけで使える |
| `pollution` | まわりのマスへの汚染（`amount` と `radius`） |
| `admin_power` | 働く人1人あたりに増える行政力（官庁） |

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
| `orders` | 建国直後の行政力（`base_admin_power`）、並べられる命令の数（`max_queue`） |
| `founding` | 選べる資源の数、描き直せる地形のマス数、自動で配る地形の割合、名前の長さの上限 |
| `labor` | 住宅地から通える距離（`commute_radius`）、優先度の既定 |
| `power` | 電気の財、住民1人あたりの電力、住宅地への配電の優先度 |
| `population` | 1人あたりの食料、増え方と減り方、幸福度の計算の重み |
| `housing` | 住宅地が1ターンに広がる・育つ数 |
| `disasters` | 災害の起きる割合と、施設が止まるターン数 |
| `accounts` | 同じ接続元から登録できる数（`signups_per_ip`、0 で無制限）と、覚えておく日数 |

数値はフェーズ1の試し遊びで調整する仮の値です。
