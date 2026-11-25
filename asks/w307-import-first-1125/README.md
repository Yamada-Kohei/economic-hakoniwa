# W-307 import_first の長い測り（種 1・4・5、500 ターン）

本体 commit 8de61fa8（枝 claude/project-thread-x0hn7r。`trade.offtake.import_first` の作り）での
比べ。ask.json の 6 本を回す:

- `if-probe-off-s{1,4,5}`: 引き取り契約は入（--on）、import_first は切（今の「入」）
- `if-probe-on-s{1,4,5}`: 引き取り契約も import_first も入（--on --first）

記録は cfB の offtake_probe_v2.py と同じ列（offtake_probe_first.py が --first を足したもの）。

集計は w307_first_tally.py で、種ごとに off と on を並べる:

    py -3.12 -X utf8 w307_first_tally.py w307if-probe-off-s1.jsonl.gz w307if-probe-on-s1.jsonl.gz

止まりの計に加え、輸入に頼る島（そのターンに石炭を作る建った施設のない島）の止まりと、
作る施設のある島の止まりを分けて出す。見る所は、輸入に頼る島の止まりが減る一方で、先に回した分を
作る施設のある島の側が負っていないか（種 4 の cfB では、輸入に頼る島 4.50 → 3.46、計 14.10 → 14.70）。
