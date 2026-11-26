# 仕様の辞書（案内係フェーズ1）

生成物です。手で直さないでください（作り直しは tools/build_guide_dict.py）。
数値は定義データから引いた「定義上の値」です。

## 財の辞書

### 穀物（grain）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越せるのは24ターンまで（shelf_life）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 住民が食べられる。1単位で1人前、食事の質は0.2（0〜1）
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 注記: 乾かして倉庫に置けば年単位でもつ主食。国の備蓄の入れ替えの目安は2年（goods.yaml 冒頭のコメント、W-199）。
- 出典: packs/base/data/goods.yaml の grain／表示名は packs/base/locale/ja/goods.yaml

### 魚（fish）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越せるのは2ターンまで（shelf_life）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 住民が食べられる。1単位で1人前、食事の質は0.4（0〜1）
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 出典: packs/base/data/goods.yaml の fish／表示名は packs/base/locale/ja/goods.yaml

### 木材（wood）

- 段: 1（原料）
- 単位: m³
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.7トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の wood／表示名は packs/base/locale/ja/goods.yaml

### 石材（stone）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の stone／表示名は packs/base/locale/ja/goods.yaml

### 鉄鉱石（iron_ore）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の iron_ore／表示名は packs/base/locale/ja/goods.yaml

### 石炭（coal）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 出典: packs/base/data/goods.yaml の coal／表示名は packs/base/locale/ja/goods.yaml

### 鋼鉄（steel）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は2（1が先）
- 出典: packs/base/data/goods.yaml の steel／表示名は packs/base/locale/ja/goods.yaml

### 建材（building_materials）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は2（1が先）
- 出典: packs/base/data/goods.yaml の building_materials／表示名は packs/base/locale/ja/goods.yaml

### 食品（food）

- 段: 3（最終財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越せるのは6ターンまで（shelf_life）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 住民が食べられる。1単位で1.5人前、食事の質は1（0〜1）
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 注記: 加工した食品。穀物より1単位で養える人数が多く（nutrition 1.5）、食事の質も高い（quality 1）。
- 出典: packs/base/data/goods.yaml の food／表示名は packs/base/locale/ja/goods.yaml

### 機械（machinery）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を5トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は2（1が先）
- 出典: packs/base/data/goods.yaml の machinery／表示名は packs/base/locale/ja/goods.yaml

### 消費財（consumer_goods）

- 段: 3（最終財）
- 単位: 個
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.05トンとして積載量を数える
  - 家計にとっての種類はぜいたく品（need: luxury）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は4（1が先）
- 出典: packs/base/data/goods.yaml の consumer_goods／表示名は packs/base/locale/ja/goods.yaml

### 電力（electricity）

- 段: 2（中間財）
- 単位: MWh
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - そのターンに使わなかった分は消える。貯めておけない（shelf_life 0）
  - 船で運べない。島の外へ送れない（freight_weight が無い）
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
- 注記: 発電と消費は島ごと・ターンごとに釣り合わせるしかない（運べず、貯められないため）。足りない島では発電所を増やすか、電力を多く使う施設を減らすのが手になる。
- 出典: packs/base/data/goods.yaml の electricity／表示名は packs/base/locale/ja/goods.yaml

### 輸送船（transport_ship）

- 段: 3（最終財）
- 単位: 隻
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 自分で航海して届く物。運ぶ重さとしては数えない（freight_weight 0）
  - 計画経済の国営商社が少ない外貨を使う順は2（1が先）
- 注記: 輸送船そのもの。海運業が設備として使う。freight_weight 0 は「自分で航海して届く物」の印。
- 出典: packs/base/data/goods.yaml の transport_ship／表示名は packs/base/locale/ja/goods.yaml

### 原油（crude_oil）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 出典: packs/base/data/goods.yaml の crude_oil／表示名は packs/base/locale/ja/goods.yaml

### ボーキサイト（bauxite）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の bauxite／表示名は packs/base/locale/ja/goods.yaml

### 綿（cotton）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の cotton／表示名は packs/base/locale/ja/goods.yaml

### 嗜好品（luxury_crop）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 家計にとっての種類はぜいたく品（need: luxury）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は4（1が先）
- 出典: packs/base/data/goods.yaml の luxury_crop／表示名は packs/base/locale/ja/goods.yaml

### 石油燃料（fuel）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 出典: packs/base/data/goods.yaml の fuel／表示名は packs/base/locale/ja/goods.yaml

### 化学品（chemicals）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の chemicals／表示名は packs/base/locale/ja/goods.yaml

### アルミ（aluminium）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の aluminium／表示名は packs/base/locale/ja/goods.yaml

### 肉（meat）

- 段: 3（最終財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越せるのは3ターンまで（shelf_life）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 住民が食べられる。1単位で1.2人前、食事の質は0.8（0〜1）
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は1（1が先）
- 出典: packs/base/data/goods.yaml の meat／表示名は packs/base/locale/ja/goods.yaml

### 衣料（clothing）

- 段: 3（最終財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 家計にとっての種類は必需品（need: necessity）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は4（1が先）
- 出典: packs/base/data/goods.yaml の clothing／表示名は packs/base/locale/ja/goods.yaml

### 自動車（automobile）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1.5トンとして積載量を数える
  - 家計にとっての種類は耐久消費財（ぜいたく品の種類にも数える）（need: durable）
  - 家計が買う財で、物価指数に入る（consumer）
  - 計画経済の国営商社が少ない外貨を使う順は4（1が先）
- 注記: 1台ずつ数える財なので、display_scale（25倍の表示）は付かない。
- 出典: packs/base/data/goods.yaml の automobile／表示名は packs/base/locale/ja/goods.yaml

### 自動車部品（auto_parts）

- 段: 2（中間財）
- 単位: 組
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の auto_parts／表示名は packs/base/locale/ja/goods.yaml

### サービス（services）

- 段: 3（最終財）
- 単位: 人月
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - そのターンに使わなかった分は消える。貯めておけない（shelf_life 0）
  - 船で運べない。島の外へ送れない（freight_weight が無い）
  - 家計にとっての種類はサービス（ぜいたく品の種類にも数える）（need: service）
  - 家計が買う財で、物価指数に入る（consumer）
- 注記: 商店・飲食・理美容・娯楽・修理などの働き。その場で使われ、量は働く人1人の 1ターンの働き（人月）で数える。
- 出典: packs/base/data/goods.yaml の services／表示名は packs/base/locale/ja/goods.yaml

### 小売り（retail）

- 段: 3（最終財）
- 単位: 人月
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - そのターンに使わなかった分は消える。貯めておけない（shelf_life 0）
  - 船で運べない。島の外へ送れない（freight_weight が無い）
  - 家計にとっての種類は小売り（店の売り場の働き）（need: retail）
  - 家計が買う財で、物価指数に入る（consumer）
- 注記: 店の売り場の働き。家計が品物（電力と町の資材を除く）を買うと、代金に見合う量の小売りも商店・百貨店から一緒に買う（goods.yaml の retail のコメント）。
- 出典: packs/base/data/goods.yaml の retail／表示名は packs/base/locale/ja/goods.yaml

### 銅鉱石（copper_ore）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の copper_ore／表示名は packs/base/locale/ja/goods.yaml

### 銅（copper）

- 段: 2（中間財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は3（1が先）
- 出典: packs/base/data/goods.yaml の copper／表示名は packs/base/locale/ja/goods.yaml

### 電子機器（electronics）

- 段: 3（最終財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 計画経済の国営商社が少ない外貨を使う順は2（1が先）
- 出典: packs/base/data/goods.yaml の electronics／表示名は packs/base/locale/ja/goods.yaml

### 武器（weapons）

- 段: 3（最終財）
- 単位: 組
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.002トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 注記: 武器は1組が兵1人分（goods.yaml のフェーズ4-2のコメント）。
- 出典: packs/base/data/goods.yaml の weapons／表示名は packs/base/locale/ja/goods.yaml

### 弾薬（ammunition）

- 段: 3（最終財）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の ammunition／表示名は packs/base/locale/ja/goods.yaml

### 軍用車両（military_vehicle）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1.6トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の military_vehicle／表示名は packs/base/locale/ja/goods.yaml

### ミサイル（missile）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.1トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の missile／表示名は packs/base/locale/ja/goods.yaml

### 迎撃ミサイル（interceptor）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.05トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の interceptor／表示名は packs/base/locale/ja/goods.yaml

### ウラン鉱石（uranium_ore）

- 段: 1（原料）
- 単位: トン
- 誤解しやすい点:
  - 画面の量は内部の量の25倍で表示される。1単位あたりの値段は25分の1になり、お金の額は変わらない
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を1トンとして積載量を数える
  - 建国時に選べる地下資源（mining）
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
  - 限られた島の鉱脈でだけ採れる（vein_only）
- 注記: 鉱脈のある限られた島でだけ採れる（vein_only）。国どうしの契約でだけ動く。
- 出典: packs/base/data/goods.yaml の uranium_ore／表示名は packs/base/locale/ja/goods.yaml

### 核弾頭（nuclear_warhead）

- 段: 3（最終財）
- 単位: 台
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 船で運ぶとき、1単位を0.2トンとして積載量を数える
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 注記: 核施設で作り、ミサイル基地に置いてミサイルに積む装備。
- 出典: packs/base/data/goods.yaml の nuclear_warhead／表示名は packs/base/locale/ja/goods.yaml

### 軍艦（warship）

- 段: 3（最終財）
- 単位: 隻
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 自分で航海して届く物。運ぶ重さとしては数えない（freight_weight 0）
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の warship／表示名は packs/base/locale/ja/goods.yaml

### フリゲート（frigate）

- 段: 3（最終財）
- 単位: 隻
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 自分で航海して届く物。運ぶ重さとしては数えない（freight_weight 0）
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の frigate／表示名は packs/base/locale/ja/goods.yaml

### 潜水艦（submarine）

- 段: 3（最終財）
- 単位: 隻
- 誤解しやすい点:
  - 画面の量は内部の量そのまま（倍率の表示は付かない）
  - 持ち越しの期限は無い（shelf_life を書かない＝無期限）
  - 自分で航海して届く物。運ぶ重さとしては数えない（freight_weight 0）
  - 軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。買うのは国だけ（arms）
- 出典: packs/base/data/goods.yaml の submarine／表示名は packs/base/locale/ja/goods.yaml

## 施設の辞書（よく聞く施設）

### 農場（farm）

- 分類: primary／産業: 農業
- 建てられる地形: 平地
- 建設: 2ターン、労働 200人、資材 木材 5
- 働き手（全力のとき）: 500人（一般の労働者）
- 1マスに建て増せる規模の上限: 1
- 生産方式「穀物」: 入り なし → 出 穀物 20、蒔いてから収穫まで5ターン
- 生産方式「穀物（肥料あり）」: 入り 化学品 1 → 出 穀物 26、蒔いてから収穫まで5ターン
- 生産方式「綿」: 入り なし → 出 綿 12、蒔いてから収穫まで6ターン
- 生産方式「綿（肥料あり）」: 入り 化学品 1 → 出 綿 15.6、蒔いてから収穫まで6ターン
- 生産方式「嗜好品」: 入り なし → 出 嗜好品 8、蒔いてから収穫まで12ターン
- 生産方式「嗜好品（肥料あり）」: 入り 化学品 1 → 出 嗜好品 10.4、蒔いてから収穫まで12ターン
- 段階: mechanized・modern（読み込み時に作り方が足される）
- 注記: 作物を替えると、1ターンに畑の 1/grow_turns ずつ蒔き直し、蒔いた畑はその作物の grow_turns のあいだ何も出さない。化学品を肥料に入れると産出が3割増える（facilities.yaml の farm のコメント）。段階（機械化・近代化）の作り方は定義の grades から読み込み時に足されるので、この辞書の生産方式の一覧には出ない。
- 出典: packs/base/data/facilities.yaml の farm／表示名は packs/base/locale/ja/facilities.yaml

### 牧場（ranch）

- 分類: primary／産業: 農業
- 建てられる地形: 平地
- 建設: 2ターン、労働 200人、資材 木材 8
- 働き手（全力のとき）: 250人（一般の労働者）
- 1マスに建て増せる規模の上限: 1
- 生産方式「default」: 入り 穀物 4 → 出 肉 5
- 出典: packs/base/data/facilities.yaml の ranch／表示名は packs/base/locale/ja/facilities.yaml

### 火力発電所（thermal_power_plant）

- 分類: power／産業: エネルギー
- 建てられる地形: 平地
- 建設: 5ターン、労働 600人、資材 建材 25・鋼鉄 10・機械 5
- 働き手（全力のとき）: 一般労働者 40人・技術者 20人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「石炭で発電」: 入り 石炭 4 → 出 電力 30
- 生産方式「石油燃料で発電」: 入り 石油燃料 3 → 出 電力 30
- 汚染: 周り2マスに6
- 注記: 燃料は石炭と石油燃料の2通り選べ、どちらでも発電量は同じ30。周りに汚染を出す（pollution amount 6・radius 2）。
- 出典: packs/base/data/facilities.yaml の thermal_power_plant／表示名は packs/base/locale/ja/facilities.yaml

### 水力発電所（hydro_power_plant）

- 分類: power／産業: エネルギー
- 建てられる地形: 山
- 建設: 8ターン、労働 1,000人、資材 建材 60・鋼鉄 10・機械 5
- 働き手（全力のとき）: 一般労働者 18人・技術者 12人
- 1マスに建て増せる規模の上限: 1
- 生産方式「default」: 入り なし → 出 電力 20
- 水: 周り4マスの地形から集める（満水 26、産出の下限の割合 0.3）
- 注記: 周りの地形から集まる水の量で産出が決まる（facilities.yaml の water: 集める範囲 radius 4、満水 full 26、産出の下限の割合 min 0.3、地形ごとの流れ込み runoff）。建てられるのは山のマスだけ。
- 出典: packs/base/data/facilities.yaml の hydro_power_plant／表示名は packs/base/locale/ja/facilities.yaml

### 製鉄所（steel_mill）

- 分類: industry／産業: 鉄鋼業
- 建てられる地形: 平地
- 建設: 6ターン、労働 800人、資材 建材 30・機械 5
- 働き手（全力のとき）: 一般労働者 1,900人・技術者 100人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 鉄鉱石 10・石炭 5・電力 5 → 出 鋼鉄 5
- 汚染: 周り2マスに8
- 出典: packs/base/data/facilities.yaml の steel_mill／表示名は packs/base/locale/ja/facilities.yaml

### 建材工場（building_materials_plant）

- 分類: industry／産業: 鉱業
- 建てられる地形: 平地
- 建設: 4ターン、労働 500人、資材 木材 25
- 働き手（全力のとき）: 1,000人（一般の労働者）
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「石材から製造」: 入り 石材 10・木材 2・電力 2 → 出 建材 8
- 生産方式「木材から製造」: 入り 木材 12・電力 2 → 出 建材 5
- 汚染: 周り1マスに3
- 出典: packs/base/data/facilities.yaml の building_materials_plant／表示名は packs/base/locale/ja/facilities.yaml

### 機械工場（machinery_plant）

- 分類: industry／産業: 機械
- 建てられる地形: 平地
- 建設: 6ターン、労働 800人、資材 建材 30・鋼鉄 10
- 働き手（全力のとき）: 一般労働者 1,350人・技術者 150人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 鋼鉄 4・電力 4 → 出 機械 2
- 汚染: 周り1マスに3
- 出典: packs/base/data/facilities.yaml の machinery_plant／表示名は packs/base/locale/ja/facilities.yaml

### 食品工場（food_factory）

- 分類: industry／産業: 食品
- 建てられる地形: 平地
- 建設: 3ターン、労働 400人、資材 建材 10
- 働き手（全力のとき）: 800人（一般の労働者）
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「穀物から製造」: 入り 穀物 12・電力 1 → 出 食品 10
- 生産方式「魚から製造」: 入り 魚 15・電力 1 → 出 食品 10
- 出典: packs/base/data/facilities.yaml の food_factory／表示名は packs/base/locale/ja/facilities.yaml

### 消費財工場（consumer_goods_factory）

- 分類: industry／産業: 消費財
- 建てられる地形: 平地
- 建設: 4ターン、労働 500人、資材 建材 20・機械 5
- 働き手（全力のとき）: 一般労働者 950人・技術者 50人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「鋼鉄と木材から製造」: 入り 木材 4・鋼鉄 1・電力 2 → 出 消費財 6
- 生産方式「木材から製造」: 入り 木材 8・電力 2 → 出 消費財 4
- 生産方式「化学品から製造」: 入り 化学品 4・電力 2 → 出 消費財 6
- 汚染: 周り1マスに2
- 出典: packs/base/data/facilities.yaml の consumer_goods_factory／表示名は packs/base/locale/ja/facilities.yaml

### 製油所（refinery）

- 分類: industry／産業: 石油
- 建てられる地形: 平地
- 建設: 6ターン、労働 700人、資材 建材 30・鋼鉄 10・機械 3
- 働き手（全力のとき）: 一般労働者 550人・技術者 70人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 原油 10・電力 3 → 出 石油燃料 6・化学品 2
- 汚染: 周り2マスに6
- 出典: packs/base/data/facilities.yaml の refinery／表示名は packs/base/locale/ja/facilities.yaml

### 繊維工場（textile_mill）

- 分類: industry／産業: 繊維
- 建てられる地形: 平地
- 建設: 3ターン、労働 400人、資材 建材 15・機械 1
- 働き手（全力のとき）: 1,500人（一般の労働者）
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 綿 8・電力 1 → 出 衣料 6
- 汚染: 周り1マスに1
- 出典: packs/base/data/facilities.yaml の textile_mill／表示名は packs/base/locale/ja/facilities.yaml

### アルミ精錬所（aluminium_smelter）

- 分類: industry／産業: 非鉄
- 建てられる地形: 平地
- 建設: 6ターン、労働 600人、資材 建材 30・鋼鉄 10・機械 3
- 働き手（全力のとき）: 一般労働者 450人・技術者 50人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り ボーキサイト 8・電力 20 → 出 アルミ 3
- 汚染: 周り1マスに4
- 出典: packs/base/data/facilities.yaml の aluminium_smelter／表示名は packs/base/locale/ja/facilities.yaml

### 銅の精錬所（copper_smelter）

- 分類: industry／産業: 非鉄
- 建てられる地形: 平地
- 建設: 5ターン、労働 600人、資材 建材 25・鋼鉄 10・機械 3
- 働き手（全力のとき）: 一般労働者 270人・技術者 30人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 銅鉱石 12・電力 8 → 出 銅 4
- 汚染: 周り1マスに4
- 出典: packs/base/data/facilities.yaml の copper_smelter／表示名は packs/base/locale/ja/facilities.yaml

### 電子機器工場（electronics_plant）

- 分類: industry／産業: 電子機器
- 建てられる地形: 平地
- 建設: 6ターン、労働 800人、資材 建材 30・鋼鉄 10・機械 5
- 働き手（全力のとき）: 一般労働者 320人・技術者 80人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「default」: 入り 銅 1・化学品 0.6・機械 0.4・電力 3 → 出 電子機器 2
- 生産方式「銅の代わりにアルミで製造」: 入り アルミ 0.5・化学品 0.6・機械 0.5・電力 3.5 → 出 電子機器 2
- 汚染: 周り1マスに2
- 出典: packs/base/data/facilities.yaml の electronics_plant／表示名は packs/base/locale/ja/facilities.yaml

### 自動車部品工場（auto_parts_plant）

- 分類: industry／産業: 自動車
- 建てられる地形: 平地
- 建設: 5ターン、労働 600人、資材 建材 25・鋼鉄 10・機械 6
- 働き手（全力のとき）: 一般労働者 1,250人・技術者 130人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「鋼鉄から製造」: 入り 鋼鉄 3.2・化学品 0.8・衣料 0.12・機械 0.2・電力 1.5 → 出 自動車部品 200
- 生産方式「鋼鉄とアルミから製造」: 入り 鋼鉄 1.8・アルミ 0.7・化学品 0.8・衣料 0.12・機械 0.2・電力 1.5 → 出 自動車部品 200
- 汚染: 周り1マスに2
- 出典: packs/base/data/facilities.yaml の auto_parts_plant／表示名は packs/base/locale/ja/facilities.yaml

### 自動車工場（automobile_plant）

- 分類: industry／産業: 自動車
- 建てられる地形: 平地
- 建設: 8ターン、労働 1,000人、資材 建材 40・鋼鉄 20・機械 10
- 働き手（全力のとき）: 一般労働者 550人・技術者 70人
- 1マスに建て増せる規模の上限: 10（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）
- 生産方式「部品を組み立て」: 入り 自動車部品 200・鋼鉄 2.2・化学品 0.3・電力 1.5 → 出 自動車 200
- 生産方式「軍用車両を製造」: 入り 自動車部品 20・鋼鉄 2.4・機械 0.2・電子機器 0.1・電力 1.5 → 出 軍用車両 2
- 汚染: 周り1マスに3
- 出典: packs/base/data/facilities.yaml の automobile_plant／表示名は packs/base/locale/ja/facilities.yaml
