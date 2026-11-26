# 相談メモ（案内係フェーズ1）

生成物です。手で直さないでください（原稿は tools/guide_memos.yaml）。
答えは、コードと定義で確かめた事だけを書きます。

## 電力が余っている島があるのに、別の島では電力が足りないのはなぜ？

電力は船で運べず（freight_weight が無い）、貯めてもおけない（shelf_life 0）ので、島ごとに、そのターンの中で発電と消費を釣り合わせるしかありません。足りない島では発電所を増やすか、電力を多く使う施設を減らすのが手です。

根拠:
- packs/base/data/goods.yaml の electricity（freight_weight なし・shelf_life 0）
- packs/base/data/facilities.yaml の thermal_power_plant（作り方 default: 石炭 4 → 電力 30、fuel: 燃料 3 → 電力 30）

## 穀物は長く貯められるのに、魚はすぐだめになるのはなぜ？

財ごとに「持ち越せるターン数」（shelf_life）が決まっているためです。穀物は24ターン、食品は6ターン、肉は3ターン、魚は2ターンです。期限を過ぎた分は消えるので、傷みやすい食べ物は作りすぎに注意が要ります。

根拠:
- packs/base/data/goods.yaml の grain（shelf_life 24）・food（6）・meat（3）・fish（2）

## 画面に出る量が、思っていたより25倍に大きく見えるのはなぜ？

重さ・体積の財などは、量を島の大きさに合わせて縮めて計算し、画面では display_scale（25倍）で見せているためです。1単位あたりの値段は逆に25分の1になるので、お金の額は変わりません。打ち込んだ量と値段は、中で元に戻されます。

根拠:
- packs/base/data/goods.yaml 冒頭のコメント（display_scale の説明）
- hakoniwa_core/defs/defs.py の display_scale()（書かなければ 1）

## サービスや小売りは、貯めておいたり別の島へ送ったりできないの？

できません。サービスと小売りは「その場で使う働き」で、shelf_life 0 （そのターン限り）かつ freight_weight が無い（船で運べない）ためです。量は、働く人1人の1ターンの働き（人月）で数えます。

根拠:
- packs/base/data/goods.yaml の services・retail（shelf_life 0・freight_weight なし）と冒頭のコメント

## 武器や軍艦は、市場で買えないの？

買えません。軍事品（arms: true）は貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動きます。買うのは国だけです。

根拠:
- packs/base/data/goods.yaml 冒頭のコメント（フェーズ4-2: arms の説明）
- packs/base/data/goods.yaml の weapons・warship など（arms: true）

## 農場を作ったのに、しばらく何も収穫できないのはなぜ？

農場には「蒔いてから収穫までのターン数」（grow_turns）があるためです。穀物は5ターン、綿は6ターン、嗜好品は12ターンかかります。作物を替えると蒔き直しになり、そのあいだは収穫がありません。

根拠:
- packs/base/data/facilities.yaml の farm（作り方ごとの grow_turns と冒頭の説明）

## 火力発電所の燃料は、石炭と石油燃料のどちらがいいの？

発電量はどちらでも同じ（電力 30）です。石炭は4、石油燃料は3使います。島で余っている方、安く手に入る方を使うのが目安になります。

根拠:
- packs/base/data/facilities.yaml の thermal_power_plant（作り方 default と fuel）

## 自動車の台数は、ほかの財みたいに25倍で表示されないの？

されません。display_scale は重さ・体積の財や電力・消費財・機械などに付く倍率で、自動車・部品の組・輸送船のように1つずつ数える財と、人月で数えるサービス・小売りには付きません（倍率は1のまま）。

根拠:
- packs/base/data/goods.yaml 冒頭のコメント（display_scale を書かない財の説明）
- packs/base/data/goods.yaml の automobile（display_scale なし）
