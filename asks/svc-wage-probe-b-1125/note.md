# サービス業の値段が賃金に負ける件の probe（掲示板 #4980 の頼み）

診断（11-24）の裏付けを取る台本。家計がサービスを買うときの指値は、いつもの値段
（直近 12 ターンの値段の平均）の倍率の段（household.consumer_goods_tiers =
[0.95, 1.0, 1.05]）でしか出ないので、買い手の上限は 1.05 × 直近 12 ターン平均に
なり、値段の上がりが 1 ターン約 0.75% に抑えられる。一方で店の賃金は上がるので
賃金 ÷ 値段が開く、という読み。

版は今の main（9335f95a）。場面は quick と同じ 6 場面（plan / none / independent /
small / open / war）× 種 1、300 ターン。pool は any。

## 残す物（1 行 = 1 島 × 1 ターン、svc の中。量は市場の記録の単位＝1000 分の 1）

サービスの財は 1 島に 1 つ（services.good = "services"、店は島に 1 つの
商業・サービス業）なので、財ごとの内訳は svc 1 つだけ。

- price: 約定した値段（市場の記録 MarketRecord.price）
- usual: いつもの値段。ターン開始時点の状態から、市場の段で家計が計算するのと
  同じ式（economy/prices.py の usual_price、usual_price_turns=12 と reprice_turns）
  で計算したもの。値段の履歴は記録の段で足されるので、開始時点で計算すれば
  そのターンの市場で効いた値と一致する（計画経済の公定価格だけは前ターンの値での
  近似になる）
- cap: 買い手の上限 = 1.05 × usual（modules/market の _service_buys が top として
  使う値そのもの。scale_price(usual, max(consumer_goods_tiers))）
- cut: 上限で切られた量。_service_buys が island.scratch["unplaced"] に残す量。
  家計のサービスの予算が「売りに出ている量の全部を上限の値段で買う額」を超えた
  とき、超えた分が上限の値段で量に直されて記録される（注文に出なかった量）。
  cut > 0 のターンは、上限の値段でも買いたい量が売りに出た量を超えていて、
  値段が上がって捌くことが上限で止められたターンと読む
- demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量。
  shortage は max(0, demand − supply)
- wage: 商業・サービス業の賃金（1 人あたり。施設の economy["wage"]。無ければ
  島の平均賃金で代用し、wage_fallback を true にする）。avg_wage は島の平均賃金
- wage_price: wage ÷ price
- workers: 店で働いている人数。seats: 次のターンに雇う人数の目安
  （市場の後に services.plan が決めた後の値）
- labor_short: 人手の足りなさ。中心ごとの「雇う目安 − 雇えた人数」の合計
  （economy/services.py の vacancies）
- ask: 店の次のターンの希望価格（値上げが買い手の一番高い指値で止まったかを
  見る対照用）
- 崩れは .gate.json（gate.Recorder の counts と found）

## 集計（report の run が report.md を出す）

島ごとに、賃金の上がり gw = wage(t) / wage(t−1) − 1 と値段の上がり
gp = price(t) / price(t−1) − 1 の差 gap = gw − gp をターンごとに出す。
見る軸は、gap（特に正の gap の合計）が「上限で切られたターン」（cut > 0）に
集まっているかどうか:

- 切られたターンとそうでないターンの gap の中央値の比べ
- 正の gap の合計のうち、切られたターンに載った割合
- 補助の区切りとして、値段が上限に張り付いたターン（price >= cap）の
  ターン数と gap の中央値も出す

## 確かめたこと

- main（9335f95a）のコードで、cap の式と unplaced の置き場所が _service_buys の
  実装どおりであること、usual_price の呼び方が家計と同じであることを読んで確認。
- small・種 1・12 ターンで回し、行が出ること、price / usual / cap / cut / wage /
  wage_price / sold / labor_short に値が入ること、report が表を出すことを
  むぎの手元で確認した（置く前に記す）。


（11-25 司会: 種 2・3 を足す写し。台本・版は svc-wage-probe-1125 と同じ。ゆらぎを見るため #5060）
