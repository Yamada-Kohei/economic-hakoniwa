# W-308 海運再参入の quick の測りの包み（線 5 の崩れ）

会議の結論 #6020 の残り 1 件。再参入（切り替え `trade.shipping_reentry`）は
効いている（線 (1)(4) 通過）ので、既定を入にする提案の前に、線 (5)
「quick の崩れが種のゆらぎの内」を main の切と入で確かめる。受けは #6022。

## 版の固定

- 対象コード: commit `1a5cc83d4e327c54135976afb3c4838674b1c6ed`（main の枝先。
  再参入の実装 266cf73d と、仕訳の種類 ship_purchase の定義漏れ直し
  9ca5044b はどちらも main に入り済み。game.yaml の既定は切）
- 切/入は同じ版・同じ台本で、`--off` / `--on` が config の
  `trade.shipping_reentry` をその場で切り替えるだけ（game.yaml は
  書き換えない。grain-rotate-probe と同じ作法）。パッチなし。
- 場面は quick の 6 場面（plan / none / independent / small / open / war）
  × 種 1〜3、300 ターン、切/入の 36 本。pool は any。
- 比べは常に同じ版の中の入/切で行う（HAL #6023: main と同 seed・同版・
  同じ帳簿の確かめ＝gate の Recorder で回す）。

## 残す物（1 行 = 1 島 × 1 ターン、ship の中。量は 1000 分の 1）

- firm / fid: 地元の海運の会社があるか / 会社の施設の番号
- fleet: 船団（1000 分の 1 隻）
- carried: そのターンに地元の海運が運んだ量
- freight: 他国・借りの船に払った運賃（stats の import.freight）
- short: 船がなくて運べなかった荷（入のときだけ残る。切では null）
- yard / port: 造船所があるか / 港があるか
- transfers: 移管の台帳（shipping_transfers）の累積件数
- gate（.gate.json）: 崩れの数（gate.Recorder の counts と found）

## 合格線（#5475 の線 5。結果を見る前に固定）

5. quick の崩れが種のゆらぎの内にあること。

集計（report）での置き方: 崩れの合計が入（18 本）で切（18 本）以下を
主の物差しとし、場面ごとの合計と、切の種ごとの幅（最小〜最大）を併記して、
増えた分がゆらぎの範囲かを読む。判定は会議。

## 集計と提出先

- 集計（report）は手元で回す（受付の report の仕事は落ちる前例があるため、
  包みには入れていない）。台本の report が上の表と線 5 の判定を出す。
- 提出先: claude-hakoniwa-2（受付への投入は測り係側）。
