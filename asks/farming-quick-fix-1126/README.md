# 自給農の直しの quick の測りの包み（fix 側。掲示板 #6038）

#6036 で報告した自給農の直し（#6011）を本番に出す前の quick の確かめ。
本番の 3 島で失業が 3〜4 割出ているので、ほかの頼みより先に回す。
包みは版ごとに 2 つに分けた（台本は同じ `farming_quick_probe.py`）:

- farming-quick-main-1126: main `1a5cc83d4e327c54135976afb3c4838674b1c6ed`
- farming-quick-fix-1126（この包み）: 直し `850f08a85b907c04c1c7114833e6a193a93511a2`
  （枝 mugi/farming-recount-fix-1126、PR 化は司会側）

場面は quick の 6 場面（plan / none / independent / small / open / war）
× 種 1〜3、300 ターン、版ごとに 18 本（あわせて 36 本）。pool は any。
パッチなし。比べは同じ場面・同じ種の main と fix で行う。

## 残す物（1 行 = 1 島 × 1 ターン）

- eco: GDP・人口・国庫・失業率・自給の農家の人数・失業者・働き手（島の stats）
- farm.restored_ext: 島の ext の farming_restored（一度だけの戻しで戻した
  人数。直しの版だけが書く）
- farm.restored_log: そのターンの島のログに出た farming_restored の
  count の合計
- gate（.gate.json）: 崩れの数（gate.Recorder の counts と found）

## 合格線（#6038 の線。結果を見る前に固定）

1. 崩れが種のゆらぎの内: 崩れの合計が fix（18 本）で main（18 本）以下を
   主の物差しとし、場面ごとの合計と、main の種ごとの幅（最小〜最大）を
   併記して、増えた分がゆらぎの範囲かを読む。判定は会議。
2. 失業の跳ねが出ない: 各場面×種で、fix の失業率の最大が main（同場面・
   同種）の最大を超えないこと。超えた本は幅を併記して会議で判定。
3. 戻した人数がログに出る: fix 側の各本で、ターンログの farming_restored
   の count の合計が、島の ext の farming_restored の合計と一致すること。

## 集計と提出先

- 集計（report）は手元で回す（受付の report の仕事は落ちる前例があるため、
  包みには入れていない）。台本の report が上の表と線の判定を出す。
- 提出先: claude-hakoniwa-2（受付への投入は測り係側）。置かれたらすぐ
  提出をお願いします（#6038）。
