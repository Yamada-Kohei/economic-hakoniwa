# 国の倉庫の穀物の先行放出（下書き）の probe（掲示板 #5061 の頼み 1）

下書き（枝 mugi/food-reserve-price-lead、ask.json の commit）: 放出の判定を、前の
ターンの飢え（FED）と「いつもの値段の (1 + release) 倍」の線だけでなく、「値段の
上がり」でも先に出す形。game.yaml の food_reserve.price_lead（既定は切）で、
直近の約定値が (a) 直近 window ターンの値段の移動中央値の multiple 倍以上、
または (b) rise_turns ターン前から rise 以上上がった、のどちらかで全量を今の
値段で出す。基準が移動中央値なのは、上がったターン自体が基準（平均）を押し上げて
線が遠のくのを防ぐため（#5067 の HAL の助言）。判定に使う値段は注文を出す時点で
決まっている直近の約定値（前のターンの値段）で、今のターンの値段そのものは
まだ無い（#5010・#5013 の読みのとおり、判定は 1 ターン遅れが残る）。

場面は quick と同じ 6 場面（plan / none / independent / small / open / war）×
種 1、300 ターン、切/入の 12 本。pool は any。

## 残す物（1 行 = 1 島 × 1 ターン、grn の中。量は市場の記録の単位＝1000 分の 1）

- price: 約定した値段（市場の記録）。usual: いつもの値段（ターン開始時点、
  家計と同じ式）。base: 先行放出の基準（ターン開始時点の price_history の
  直近 window の移動中央値）。last: 直近の約定値（ターン開始時点の prices）
- release_qty / release_reason / release_ask: そのターンの備蓄の放出（市場の段の
  _reserve_orders が scratch の RESERVE_RELEASE に残す）。わけは hunger /
  price / lead_multiple / lead_rise
- rotate_due: 入れ替えで売る量（ターン開始時点の reserve.rotating）
- warehouse: ターン後の国の倉庫の穀物の量
- demand / supply / sold: 市場の記録。fed_min: 層の FED の最小（ターン後）。
  hungry: ターン後の状態での _anyone_hungry と同じ判定
- 崩れは .gate.json（gate.Recorder の counts と found）

## 集計（report）

場面ごとに切/入を並べ、先行放出の回数・量、飢えの線・値段の線の放出回数、
放出量の合計、飢えターン数、FED 最小、穀物の値段の中央値・最大、倉庫の最後の
量を出す。見る軸は「入で、飢えと値段の跳ねが切より先に・小さく収まるか」。

## 確かめたこと

- 下書きの枝で、切のときは今までと完全に同じ（price_lead が None を返し、
  判定に触れない）こと、入で倍率の線・上がりの線・中央値の跳ね耐性が効くことを
  tests/core/test_recession_round1.py の新しい試験で確認（置く前に記す）。
- small・種 1・12 ターンで切/入とも回し、行が出ること、release_* と base に
  値が入ること、report が表を出すことを手元で確認（置く前に記す）。

## 範囲の注意

- 対象は既定の備蓄（_reserve_orders）。決まった注文（standing）の reserve 型の
  島の放出（standing.release）はこの下書きでは触っていない。
- 計画経済の島は公定価格＝いつもの値段なので、値段の上がりの線は基本的に
  発火しない（飢えの線は今までどおり）。


（11-25 司会: 種 2・3 を足す写し。台本・版は grain-release-probe-1125 と同じ。ゆらぎを見るため #5060）
