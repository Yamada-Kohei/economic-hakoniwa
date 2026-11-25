# 要望 B probe2（掲示板 #4975・#4977・#4981・#4989 の頼み）

probe1（reqb-probe-1125、#4974）で「入にしても expected_price と作る量の見込みが
ほとんど動かない」ことが分かり、コード読み（#4994）で切断点が 2 つに絞れた。
この probe2 は、その 2 点をターンの流れで確かめる台本。

- (1) prices.expected_price は見込みの系列を受け取るが、需要水準の推定で品切れ
  ターン（売りに出た量 == 売れた量）を平均から外すため、見込みの差分がほぼ捨てられる。
- (2) clothing など食料以外の家計財は、記録の側で第一希望が作られず
  expected_demand ≡ demand になる。

版は #500 の c0dd979c。計測パッチ reqb2-bundle.diff を当てた版で回す
（家計束ごとの記録を scratch に貯めるだけ。挙動は変えない。当てた版と当てない版で
同じ種・同じターンの記録が一致することを、むぎの手元で確かめた）。
切/入は probe の --off/--on で config をその場で切り替える（game.yaml は触らない）。
種は 1 本ずつ（#4975。36 本でゆらぎの幅は分かっている）。

## 残す物

probe1 と同じ島 × ターン × 財の行（price / demand / supply / sold / shortage /
expected / added / first / placed / wanted_off / subst / unmet_first / view_demand /
view_shortage / plan_goal / production / entries・recipe_changes・exits、崩れは
.gate.json）に加えて:

### 3 か所の term と反事実（sites。品切れのあった財と clothing に付く）

- plan_term: 作る量の計画（計画経済の島の _plan_record）が使う需要の値
  （入では記録の expected_demand、切では demand）。plan_term_noexp は demand。
- exp_price: prices.expected_price（ahead=1）をターン後に計算したもの。
- exp_price_noexp: 同じ状態で切り替えを切にして計算したもの。
- exp_price_zero: 同じ状態で見込みの系列を 0 にして計算したもの
  （expected_demand=0 の差分を見るため。入のときだけ）。
- view_avg: firms._demand_view の需要系列の平均（参入の _fill が使う term）。
  view_avg_zero は見込みの系列を 0 にして計算し直した平均。
- short_avg / short_avg_zero: 品切れ系列の平均も同じに 2 通り。

### 家計束の追跡（#4989 / #4990。全体平均ではなく束を選んで追う）

家計束は島の家計の層。hash は (島, 層) から作る同一性の印で、切/入の同じ束を
突き合わせるためのもの。

- bundles_cloth: 計測パッチが層ごとに貯めた衣料の記録（want > 0 の束 × ターン）。
  want = 真ん中の値段で買いたい量（needs.clothes_wanted の qty）。
  clothing には第一希望の仕組みが無いので、first に当たる量として want を使う。
  want_capped = 売りに出ていないときは衣料の予算で買える量まで細った値、
  売りに出ているときは want と同じ。ordered = 実際に出した注文の合計
  （売りに出ていなければ 0）。bought = household_goods の層ごとの買えた量。
  unmet_first = max(0, want − ordered)、subst = max(0, ordered − want)、
  unmet_bought = max(0, want − bought)。
- bundles_food: 入のときだけ、層ごとの第一希望 first と注文 placed
  （unmet_first > 0 か subst > 0 の束 × 財 × ターンだけ）。
- 集計（report）は、入の clothing の家計束から unmet_first の合計が大きい束を
  最大 3 件（あれば subst > 0 の束を最大 2 件追加）選び、ターンごとの流れ
  （束の want / ordered / bought と、同じターンの島の clothing の demand /
  expected / exp_price 3 通り / view_avg 2 通り）を並べる。

### view_demand の列の定義（#4981 の併記）

view_demand は firms._demand_view が返す需要系列の最後の値で、そのターンの市場の
記録の demand（入では expected_demand に置き換わる）。元の量は「そのターンに
買い手が注文した量の合計」（約定した量ではない）。第一希望の未充足分は、食料に
ついてのみ expected_demand 経由で含まれる。clothing など非食料の家計財では
expected_demand ≡ demand なので、買いたかったのに注文できなかった分は含まれない。
report の先頭にも同じ定義を出す。

## 集計（report の run が report.md を出す）

- 崩れ: 種類ごとの合計（切/入）。
- 財ごと: probe1 と同じ比べ方（品切れのあった島 × ターンの組で、exp_price と
  計画が入で上がったか、品切れ量が減ったか）。
- 3 か所の反事実: 入の同じ状態での exp_price（real − noexp、real − zero）と
  view_avg（real − zero）の中央値。real と noexp が同じなら、その状態では
  expected_price は見込みを読んでいない（または読んでも効いていない）。
- 家計束の追跡表（上記）と、食料の家計束の出事の件数。

## 確かめたこと

- small・種 1・12 ターンで切/入とも回し、行・sites・bundles_cloth・bundles_food が
  出ること、report が表と追跡を出すことを確認した（むぎの手元）。
- 計測パッチの有無で、切・同じ種・同じターンの goods と出入り件数と gate が
  一致することを確認した（パッチは挙動を変えない）。
- ruff は手元の VM に無いため未確認（書式は probe1 の台本に合わせた）。
