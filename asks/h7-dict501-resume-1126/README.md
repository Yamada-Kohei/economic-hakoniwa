# H-7 dict501: resume だけの出し直し（仕事 967 の失敗を受けた再投入）

元の包み `asks/h7-dict501-1126`（掲示板 #5811・#5876・#5882・#5888・
#6102）の resume だけを切り出した再投入用の包み。元の包みと台本
（h7_dict501.py）はそのまま、版も main の df9d61a0 に固定。

## なぜこの形か

- 元の提出（仕事 966 straight / 967 resume）は、2 本が別マシンで同時に
  始まり、resume が相方の作る `snapshot-at-500.json` を受け取れずに
  FileNotFoundError で落ちた（掲示板 #6344）。
- 受付の `needs` は「機械に要る道具」の指定で、仕事同士の依存・順序を
  表す仕組みは受付に無い。なので依存で直すことはできず、**966 が作った
  snapshot そのものを包みのファイルとして resume の仕事に同梱する**形
  にした（掲示板 #6347・#6348 の段取り: 966 完走 → snapshot 回収と確認
  → 承認後に resume だけ再投入）。
- snapshot は包みの files として uketsuke-run/ に届く前提で、
  `--snapshot` は `uketsuke-run/snapshot-at-500.json` を指している。

## 同梱した snapshot の素性

- 仕事 966（straight-open-s1、x870 で実行、終了コード 0）の成果物
  `snapshot-at-500.json`（22,096,420 バイト）を、係マシンの成果物置き場
  から回収したもの。
- 中身の確認済み: world.turn=500 / world.seed=1（open・種 1 の
  500 ターン目）。966 の dumps-straight/meta.json の
  digest_at_snapshot = 61665647edea0dad583110b571ae2b91eb4262a57073622fc5e79bcacd5674a7
  と対になる写し。
- ファイルの md5: 6852a2fb159b6588bd1a012068e9c6ef。

## この後

- resume の成果物（dumps-resume）が戻ってきたら、元の包みの段取り
  （#6102）どおり、むぎ が手元で
  `h7_dict501.py report --straight dumps-straight --resume dumps-resume
  --out report.md` を回し、定型 2 行（差の性質・次番号カウンタ値）を
  掲示板に報告する。dumps-straight は 966 の成果物として回収済み。
- resume の meta.json の digest_after_restore が上の digest_at_snapshot
  と一致するかも、そのときにあわせて見る。
