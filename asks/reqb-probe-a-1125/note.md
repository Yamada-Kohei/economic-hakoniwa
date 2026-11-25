# 要望 B probe（掲示板 #4840・#4842 の頼み）

quick（reqb-off-1125 / reqb-on-1125）で足りなかった記録を毎ターン残す台本。
版は #500 の c0dd979c（market.expected_demand の実装はこの版にだけある）。
切/入は probe の --off/--on で config をその場で切り替える（game.yaml は触らない）。

## 残す物（1 行 = 1 島 × 1 ターン、財ごと）

- 値段 price、買いたい量 demand、売りに出た量 supply、売れた量 sold、品切れ shortage
- 見込み需要 expected と、足した量 added（= expected − demand。本来ほしかった量の分）
- expected_price（prices.expected_price、ahead=1。品切れのあった財だけ）
- 作る量の計画: 計画経済の島は plan_goal（次のターンの生産目標）、市場の島は
  view_demand / view_shortage（参入・作り方の見直し・建て増しが読む _demand_view の最後の値）
- 実際の産出 production
- 家計の第一希望 first、実際の注文 placed、売りに出ていなくて注文しなかった量 wanted_off
- 参入 entries・作り方の変更 recipe_changes・撤退 exits の件数
- 崩れは .gate.json（gate.Recorder の counts と found。会社の連鎖 = firm_cascade）

## 代わりの買いの数え方（注意）

代わりの買いは献立の組み直しで起きるので、「どの財の代わりにどの財を」と 1 対 1 には
決まらない。そこで島 × ターンごとに、第一希望から減った量 unmet_first（max(0, first − placed)）と、
第一希望を超えて注文した量 subst（max(0, placed − first)）の両側を残す。
集計（report）では両側の合計で見る。

## 集計（report の run が report.md を出す）

- 崩れ: 種類ごとの合計と種 3 本の最小〜最大（会社の連鎖が種 3 本で出るかを含む）
- 財ごと: 品切れのあった島 × ターンの組で、入の exp_price と計画（view_demand / plan_goal）が
  切より上がっているか（中央値と上がった割合）、それで品切れ量が減ったか

## 確かめたこと

- small・種 1・12 ターンで切/入とも回し、行が出ること、入で expected / added /
  subst / unmet_first に値が入ること、report が表を出すことを確認した（むぎの手元）。
- ruff check / format 済み。
（11-25: 案 A の版 bd2191bc で回す写し。中身は reqb-probe-1125 と同じ、#5006 の 3）
