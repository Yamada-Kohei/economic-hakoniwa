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
  locale/ja/
    goods.yaml         財の表示名
    terrain.yaml       地形の表示名
    facilities.yaml    施設の表示名
    orders.yaml        命令の表示名
    messages.yaml      ターンログなどの文面
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
| `goods.yaml` | `tier`（1 原料、2 中間財、3 最終財）、`shelf_life`（持ち越せるターン数。0 は持ち越せない。書かなければ無期限） | `schemas/goods.schema.json` |
| `terrain.yaml` | `land`（陸地か）、`reclaimable`（埋め立てられるか）、`height`（描画用の高さ） | `schemas/terrain.schema.json` |
| `facilities.yaml` | 上の例のとおり | `schemas/facilities.schema.json` |
| `orders.yaml` | `admin_cost`（要る行政力）、`payload`（命令の中身の形を JSON Schema で） | `schemas/orders.schema.json` |

建てられる地形は施設の側（`terrain:`）にだけ書きます。地形の側には書きません。

## 検査

次の3か所で同じ検査が走ります。誤りは「ファイル名:行: 内容」で示します。

- `python manage.py check_defs`（または `python -m hakoniwa_core.defs`）
- サーバーの起動時（`runserver` や `manage.py check`）
- 自動テストと CI

形の検査（スキーマ）に加えて、「レシピに書いた財が定義されているか」「施設の地形が定義されているか」
「表示名がそろっているか」のような、ファイルをまたぐ確認も行います。

数値はフェーズ1で調整する仮の値です。
