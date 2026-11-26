# 家計の電気 LB-47 の測り直し・出し直し包み（掲示板 #5249・#5250・#5260・#5356、件 21 = #6302）

`lb47-household-remeasure-1126` の出し直し。測りの中身（版・場面・種・
ターン数・合格線・台本）は元の包みと完全に同じで、ask.json の引数の
並びだけを直した物。合格線や残す物の定義は元の包みの README
（`../lb47-household-remeasure-1126/README.md`）が正。

## なぜ出し直すか

- 元の包みの 36 本（受付の仕事 1008〜1043）は、全本が hal で同じ引数
  エラーにより即落ちした（終了コード 2）:
  `lb47_household_probe.py: error: argument cmd: invalid choice:
  'lb47_household_probe.py' (choose from 'probe', 'report')`
- 原因: 元の ask.json は各 run の `args` の先頭に台本のファイル名が
  入っていた。受付は `sys.argv` を `[uketsuke-run/<台本名>] + args` の
  形に組み立てるので台本名が argv[1] にも入り、argparse がそれを
  サブコマンド（probe/report）と誤認した。手元の試走は
  `python3 lb47_household_probe.py probe ...` の形（台本名は argv[0]
  の 1 つだけ）だったため出なかった。通っている包み
  （deadlock-quick・h7-dict501・grain-rotate 等）は全部 args の先頭が
  サブコマンドで、台本名を入れていない
- 直し: 全 36 本の args から先頭の台本名を取り除いた（元の包みの
  ask.json も同じ直しを入れてある）。受付と同じ組み立て方
  （manage.py shell + runpy、sys.argv の形も同一）を固定版の手元で
  再現し、small・種 1・30 ターン切/入とも通り、行と gate が出る
  ことを確認済み
- 元の包みの ask.json を直しただけでは受付に新しい仕事が作られ
  なかった（push 後しばらく見て仕事の最大番号が 1043 のまま）ため、
  H-7 の出し直し（h7-dict501-resume-1126）の前例に倣い、別の包み
  として置く

## 測りの要点（詳細は元の包みの README）

- 版: main `6f4c4ba8d284fc85e3a5621cc0871e3337fad17e` に固定
- 6 場面（plan / none / independent / small / open / war）× 種 1〜3、
  300 ターン、`power.household_curve` の切/入で 36 本。pool は any
- 合格線（結果を見る前に固定済み、#5250・#5260 のまま）:
  1. 取引の止まり（gate の no_trade）の増えが種のゆらぎの内
  2. 貿易の途絶え（trade_stopped）が入で出ない
  3. 失業率の差が種のゆらぎの内
  全部満たせば既定入。あわせて電気の不足率・家計の買えなかった割合・
  本当の電力不足の割合を並べる。集計（report）は手元で回す
