PO-63（PR #502）の quick 比べの見張り側、枝側（会議 #4774 の 2・#4777）。commit は枝 mugi/po63-farm-workers-fraction の tip（daa9f30b）。
対は w272-po63-watch-main-1125（main、b4781526）。種・初期状態は両側で同じ（small・plan、種 1、500 ターン、--on 1 = 今の既定）。
枝側は2点変更込み: settle_farm_capacity の端数保持（PO-63 本体、a706f78d）＋ _land_room の揃え（daa9f30b）（#4781 の明記どおり）。帰農（population.return_to_land）は既定が切のままなので、_land_room の分はこの比べにはほぼ出ない。
po63_watch.py は PO-63 の直しに揃えた版（island_row のかけ直しで int を外した物）。
比べる物: 届くマス数（short の延べ）・自給の人のいるマス数・必要量に対する余白（作れる量 ÷ 食べる量、比の最小と下 1 割）。集計は po63_watch_sum.py *.jsonl.gz と split_sum.py *.jsonl.gz（読むだけ）。
