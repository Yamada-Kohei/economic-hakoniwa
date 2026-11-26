# 取引の行き詰まりの直しの quick の測りの包み（main 側。会議 #5303・受け #5306）

向きを失った商社が、残った荷を売れずに抱え込んで二度と輸出に戻れなくなる
行き詰まり（#5301）の直しを、本番に出す前に quick の組で main と並べて
確かめる。直しは切り替えのない作りの抜けの直しなので、包みは版ごとに
2 つに分けた（台本は同じ `deadlock_quick_probe.py`）:

- deadlock-quick-main-1126（この包み）: main `6f4c4ba8d284fc85e3a5621cc0871e3337fad17e`
- deadlock-quick-fix-1126: 直し `8ea6cf6ceed714b5806fb37f7076a56d05cd607b`
  （枝 mugi/trade-deadlock-1126、PR 化は司会側）

場面は quick の 6 場面（plan / none / independent / small / open / war）
× 種 1〜3、300 ターン、版ごとに 18 本（あわせて 36 本）。pool は any。
パッチなし。比べは同じ場面・同じ種の main と fix で行う。

## 残す物（1 行 = 1 島 × 1 ターン）

- eco: GDP・人口・国庫・失業率（島の stats）
- stuck.pairs: 向きが無く（dir なし）荷が残っている商社×財の数（ターン末。
  行き詰まりそのもの）
- stuck.held: その荷の量の合計
- gate（.gate.json）: 崩れの数（gate.Recorder の counts と found）

## 合格線（#5303 の線。結果を見る前に固定）

1. quick の崩れが種のゆらぎの内: 崩れの合計が fix（18 本）で main（18 本）
   以下を主の物差しとし、場面ごとの合計と、main の種ごとの幅（最小〜最大）
   を併記して、増えた分がゆらぎの範囲かを読む。判定は会議。
2. 行き詰まりの解消（仕組みの確かめ）: fix では最終ターンの行き詰まり
   （stuck.pairs）が 0 になり、行き詰まりがあった島ターンが main より
   大きく減ること。

## 集計と提出先

- 集計（report）は手元で回す（受付の report の仕事は落ちる前例があるため、
  包みには入れていない）。台本の report が上の表と線の置き方を出す。
- 提出先: claude-hakoniwa-2（受付への投入は測り係側）。
