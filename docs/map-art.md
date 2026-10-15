# マップの絵の仕様

島の俯瞰図に使う絵（スプライト）の決まりです。絵は人でも AI（Codex など）でも描けるように、
ここに書いたことだけで作れて、ここに書いたことで見直せるようにしています。
絵がないものは、これまでどおり色付きのひし形と箱で描くので、少しずつ足していけます。

## しくみ

- 絵は `server/static/themes/<テーマ>/sprites/` の下に置き、同じテーマの `theme.yaml` の `sprites` に
  「名前 → ファイル」で並べます。いまのテーマは `default` だけです（`game.yaml` の `theme`）。
- 描くのは `server/static/js/map/renderer.js` で、絵の読み込みは `sprites.js` です。
  `sprites` に載っていない名前、読み込めなかった絵は、色の箱で描きます。
- 小さな地図（右下の全体図）は今後も `theme.yaml` の色で描きます。絵の色合いをその色に近づけてください。

```yaml
# server/static/themes/default/theme.yaml
sprite_scale: 1
sprites:
  terrain/plain: sprites/terrain/plain.svg
  facility/steel_mill: sprites/facility/steel_mill.svg
  housing/city: {file: sprites/housing/city.svg, anchor: [32, 96]}   # 基準点を変えるときだけ
  facility/bank: {file: sprites/facility/bank.png, scale: 0.5}       # 2 倍で描いた PNG
```

## 名前の一覧

`terrain/` は地形、`facility/` は施設（定義データ `packs/base/data/facilities.yaml` の `sprite`）、
`housing/` は住宅地の段階（`settlements.yaml`）です。「段」はいまの色の箱の高さで、絵の高さの目安です。

| 名前 | 何か | 目安 |
|---|---|---|
| `terrain/sea` | 海 | 地面の高さ 0 段 |
| `terrain/shallows` | 浅瀬 | 地面の高さ 0 段 |
| `terrain/plain` | 平地 | 地面の高さ 1 段 |
| `terrain/forest` | 森 | 地面の高さ 1 段 |
| `terrain/mountain` | 山 | 地面の高さ 2 段 |
| `facility/farm` | 農場 | 0.3 段・平地 |
| `facility/lumber_camp` | 林業場 | 1 段・森 |
| `facility/quarry` | 採石場 | 1 段・山 |
| `facility/mine` | 鉱山 | 1.5 段・山 |
| `facility/fishing_port` | 漁港 | 1 段・平地・海沿い |
| `facility/steel_mill` | 製鉄所 | 3 段・平地 |
| `facility/building_materials_plant` | 建材工場 | 2 段・平地 |
| `facility/machinery_plant` | 機械工場 | 2.5 段・平地 |
| `facility/food_factory` | 食品工場 | 2 段・平地 |
| `facility/thermal_power_plant` | 火力発電所 | 3.5 段・平地 |
| `facility/hydro_power_plant` | 水力発電所 | 2.5 段・山 |
| `facility/consumer_goods_factory` | 消費財工場 | 2 段・平地 |
| `facility/government_office` | 中央省庁 | 3 段・平地 |
| `facility/bank` | 銀行 | 2.5 段・平地 |
| `facility/school` | 学校 | 2 段・平地 |
| `facility/university` | 大学 | 3 段・平地 |
| `facility/port` | 港（フェーズ3-1） | 2.5 段・平地・海沿い（クレーンの先は 5 段ほど） |
| `facility/shipyard` | 造船所（フェーズ3-1） | 3 段・平地・海沿い（クレーンの先は 5 段ほど） |
| `facility/trading` | 商社（フェーズ3-1） | 3 段・平地・オフィス街（屋上の地球儀まで 5 段ほど） |
| `facility/oil_well` | 油井（フェーズ3-2） | 2 段・平地か山（やぐらの先は 5 段ほど） |
| `facility/refinery` | 製油所（フェーズ3-2） | 3 段・平地（蒸留塔とフレアスタックは 5 段ほど） |
| `facility/aluminium_smelter` | アルミ精錬所（フェーズ3-2） | 3 段・平地 |
| `facility/textile_mill` | 繊維工場（フェーズ3-2） | 2 段・平地（煙突は 4 段ほど） |
| `facility/ranch` | 牧場（フェーズ3-2） | 0.5 段・平地 |
| `facility/automobile_plant` | 自動車工場（フェーズ3-2） | 2.5 段・平地 |
| `housing/village` | 村 | 1 段 |
| `housing/town` | 町 | 2 段 |
| `housing/city` | 都市 | 4 段 |
| `housing/metropolis` | 大都市 | 6 段 |
| `capital/<施設の sprite>` | 首都のマスにあるときの施設（任意） | 例 `capital/government_office` |
| `construction` | 工事中（任意。どの施設にも共通） | なければ白い点線の枠 |

施設はこれからも増えます（フェーズ2-2 以降）。新しい施設の絵は、その施設が定義データに入ってから足します。
いまある名前は、見本ページ（下）の上に「絵がないもの」として出ます。

## 大きさと位置

| 項目 | 決まり |
|---|---|
| マス | ひし形。幅 64・高さ 32 ピクセル（`theme.yaml` の `tile_width`・`tile_height`）。縦横 2:1 |
| 地面の段 | 1 段 8 ピクセル（`step_height`）。地形の側面（崖）はコードが色で描く |
| 施設の段 | 1 段 10 ピクセル（`facility_unit`）。壁の高さの目安 |
| 基準点 | 絵の **下の真ん中** を、マスの上面のひし形の **下の頂点** に合わせて描く。変えるときは `anchor: [x, y]`（絵の左上からのピクセル） |
| 幅 | 地形は必ず 64（マスの幅）。施設と住宅は 64 以下にし、建物の足元はひし形の内側（8 割ほど）に収める |
| 高さ | 自由。上に伸びたぶんは奥のマスに重なって描かれる（奥から手前へ描くので、それで正しい） |
| 光 | 左上から。左の側面を明るく、右の側面を暗く（いまの箱は上面の色の 0.78 倍と 0.62 倍） |

地形の絵は、そのマスの上面（ひし形）と、そこから上に出る木や岩だけを描きます。隣のマスとつながって
見えるよう、ひし形の縁ぎりぎりまで塗り、縁取りの線は付けないか、ごく薄くします。
海と浅瀬は平らなひし形です。

施設の絵の大きさの例（製鉄所、3 段）: 幅 64、高さ = 足元のひし形 32 + 壁 30 + 煙突などの飛び出し。
基準点を変えないなら、絵の一番下の行がマスの下の頂点に来ます。

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="80" viewBox="0 0 64 80">
  <!-- 左の側面・右の側面・屋根・煙突。足元のひし形の下の頂点が (32, 80) -->
  <polygon points="6,51 32,64 32,80 6,67" fill="#6b3a2e"/>
  <polygon points="58,51 32,64 32,80 58,67" fill="#552e25"/>
  <polygon points="32,38 58,51 32,64 6,51" fill="#8a4b3c"/>
  <rect x="40" y="8" width="6" height="40" fill="#444"/>
</svg>
```

## 絵の描き方

- **形式は SVG を基本にします。** 拡大しても粗くならず、差分で中身が読めて、直しやすいからです。
  PNG（または WebP）も使えます。そのときは 2 倍の大きさで描き、その絵の項目に `scale: 0.5` を付けます
  （例 `facility/bank: {file: sprites/facility/bank.png, scale: 0.5}`。`anchor` は元の画像のピクセルで書きます）。
- SVG には数値の `width` と `height` を書きます（`viewBox` だけでは大きさが決まりません）。
  `<script>`、`on...` 属性、外のファイルや URL の参照、`<foreignObject>` は使えません。1 ファイル 100KB まで。
- 絵柄: 平らな塗りと少しの陰影の、すっきりした見下ろしの絵。縮小（0.25 倍）でも何の施設か形で分かるように、
  施設ごとに目立つ特徴（製鉄所の高炉と煙突、発電所の冷却塔、港の桟橋など）を 1 つ入れます。
  拡大（4 倍）しても見苦しくないこと。
- 住宅は村 → 町 → 都市 → 大都市で、建物が高く密になっていくのが分かるようにします（目安の段の高さを守る）。
- 絵の中に文字を書きません（画面は日本語以外にも訳すため）。
- 選んだマスの枠、止まっている施設の赤い印、重ね表示の色、送電線、開発禁止の斜線はコードが描くので、絵に入れません。
- 絵を生成するスクリプトを書いたときは `tools/sprites/` に置き、標準ライブラリだけで動くようにします。

## ライセンスと出典

このプロジェクトは MIT ライセンスです。絵もすべて MIT と両立するものにします
（[CONTRIBUTING.md](../CONTRIBUTING.md) の「外部のものを足すとき」）。

- 自分で（または AI に）新しく描いた絵だけを使います。他のゲームや素材集の絵を写す・なぞる・似せることはしません。
- 外部の素材を使うなら CC0・MIT など MIT と両立するものだけで、出典の URL とライセンスを書きます。
  出典やライセンスが分からないものは使いません。
- 足した絵は同じ PR で [THIRD_PARTY.md](../THIRD_PARTY.md) の「絵・音などの素材」に、ファイルかフォルダの
  パス・作った人（AI で作ったときはその道具の名前と、依頼した人）・ライセンス・出典を書きます。
  この行がないと自動テストが落ちます。

## 確かめ方

1. `python manage.py setup_dev` と `python manage.py runserver` で動かします。
2. **見本ページ** http://127.0.0.1:8000/static/map-preview.html を開きます。地形・施設・住宅の各段階・
   工事中・止まっている施設・首都・送電線・開発禁止を 1 つの島に並べ、島の画面と同じコードで描きます。
   `?sprites=0` で絵なし（色の箱）、`?scale=2` で拡大、`?overlay=population` で重ね表示と見比べられます。
   ドラッグとホイールで動かせます。
3. 実際の島 http://127.0.0.1:8000/islands/1/ でも、拡大・縮小して見ます。
4. 自動テスト: `pytest tests/server/test_theme_sprites.py`（名前・ファイル・大きさ・危ない SVG・出典を調べる）と、
   `ruff check . && ruff format --check .`、`pytest`。

## 見直しの観点

絵を足す PR は、次の観点で見直します（Claude が見直すときもこの順に見ます）。

1. 自動テストと CI が通っている。
2. 名前・大きさ・基準点がこの仕様どおりで、見本ページで地面から浮いたり沈んだりしていない。
3. 隣のマスとつなげたとき、地形の継ぎ目が目立たない。
4. 縮小しても施設が見分けられ、住宅の段階の違いが分かる。拡大しても粗くない。
5. 光の向き・色合い・線の太さが絵どうしでそろっていて、小さな地図の色と大きく違わない。
6. 文字や、コードが描くもの（印・枠・線）を絵に入れていない。
7. THIRD_PARTY.md に出典とライセンスがあり、他の作品に似せた絵がない。
8. 絵と、それを使うための最小限のコード以外を変えていない。
