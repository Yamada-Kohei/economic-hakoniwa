# W-272 ④ 既定入の判定の測り ①③（会議 #4215・#4220）

- 7 本（selftest と、none 種 1・2 × now・stage・trigger、300 ターン）。走りの台本は記録（.jsonl.gz、1 本 10MB ほど）を書くだけです。
- 集計は、集めた記録 3 本ずつに `python w272_trigger_sum.py tr-s1-now.jsonl.gz tr-s1-stage.jsonl.gz tr-s1-trigger.jsonl.gz` をかけます。ゲームは読み込みません。担当のスレッドでもかけられます。
- 同じ判定の full（②）は w272-full-off-1124 と w272-full-on-1124 です。on は farming_by_housing と upgrade_without_subsistence を入にし、PO-63 は既定の入のままです。
