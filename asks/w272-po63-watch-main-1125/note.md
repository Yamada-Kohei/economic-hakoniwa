PO-63（PR #502）の quick 比べの見張り側、main 側（会議 #4774 の 2・#4777）。commit は main の tip（b4781526）。
対は w272-po63-watch-fix-1125（枝 mugi/po63-farm-workers-fraction、daa9f30b）。種・初期状態は両側で同じ（small・plan、種 1、500 ターン、--on 1 = 今の既定）。
po63_watch.py は PO-63 の直しに揃えた版（island_row のかけ直しで int を外した物）。
比べる物: 届くマス数（short の延べ）・自給の人のいるマス数・必要量に対する余白（作れる量 ÷ 食べる量、比の最小と下 1 割）。集計は po63_watch_sum.py *.jsonl.gz と split_sum.py *.jsonl.gz（読むだけ）。
main 側では、本体の書く farm_capacity（short_ext）と fractional なかけ直し（short）がずれる分もそのまま出る。
