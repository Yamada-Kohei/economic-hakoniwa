# 夜の点検

変えた後のゲームが、遊ぶ人より先に壊れていないかを確かめるため、空いている夜に大きめの一括シミュレーションを
回します。道具は `hakoniwa_core/nightly.py`、Windows の PC で毎晩回すための台本は `tools/nightly/` にあります。
ゲームの状態は読むだけで、数字を寄せることはしません（見つけたものは、仕組みから直します）。

## 何を見るか

| 種類 | 世界 | 見つけるもの |
|---|---|---|
| 長い年月（long） | 標準の計画・命令なし・少ない島の世界を 1,200 ターン（100 年）。場面ごとに種 2 つ | 関門の目安（`hakoniwa_core/gate.py`）から外れた島の数を、バランスの測りが見る範囲（120〜500 ターン）とその後（2 つに分ける）で数え、**後の期間にだけ出た種類**を「40 年より後に出た崩れ」として並べる |
| でたらめな国（random） | 50 島のうち 10 島が、毎ターン決まりの中ででたらめな命令を 0〜3 個出す（残りは標準の計画）。300 ターン×種 30 | **ターンの処理が例外で止まる**（サーバーならターンが進まなくなる）、**命令の受け付けで例外が出る**（画面なら 500） |

でたらめな命令は、命令の形（`packs/base/data/orders.yaml` の payload）からでたらめに作り、画面と同じ確かめ
（`hakoniwa_core/orders.check_order`）を通ったものだけを並べます。マスはなるべく施設のあるマス、相手はほかの島、
財・施設・作り方・体制は定義の名前から選び、数はときどき下限・上限・とても大きい値にします。
帳簿や島をまたぐお金の突き合わせが合わないときも、記録の段と貿易の確かめの段が例外で止めるので「止まった」に入ります。

## 手元で回す

```sh
python -m hakoniwa_core.nightly run --out-dir runs/nightly/test --jobs 4 \
    --long-turns 600 --random-seeds 4            # 小さめに一晩分を回す
python -m hakoniwa_core.nightly one --kind random --scenario plan --seed 202610170 --turns 300 --out r.json
python -m hakoniwa_core.nightly summary --out-dir runs/nightly/test   # まとめを書き直す
```

`run` は組を `--jobs` 本ずつ別の処理で回し、`summary.md`（人が読む）と `summary.json` を書きます。
`--until` の時刻（既定は朝 6 時 30 分、PC の時計）になったら、走っている組を止め、残りは始めずにまとめを書きます
（`--hours 2` のように時間の長さでも決められます）。組は 100 ターンごとに途中の結果を書くので、止めた組も
そこまでの分を見て、「時間内に終わらなかった」として届いたターンを書きます。
`--known known.json` を渡すと、前の夜までに見つけたものと同じものには「新」を付けず、一覧に足します。
種は `--seed-base`（既定はその日の日付）から作るので、夜ごとに違う世界を回します。
止まった組は `summary.md` の「同じことを起こす命令」を同じ版で回せば、同じターンで同じように止まります。

1 組（50 島）のメモリは 0.1〜0.3GB です。後のターンほど 1 ターンが遅くなります（国債の銘柄が年とともに増える
ため。銘柄ごとに島の勘定を全部見ていた分は PR #59 で直しました）。最初の夜（2026-10-17、main 2dd3a84、X870 で
14 組ずつ）は 36 組が 59 分で終わり、いちばん長かったのは標準の計画の 50 島を 1,200 ターン回す組の 59 分でした。
朝 6 時 30 分に間に合わない夜は止めた所までを見ます。「いちばん長くかかった組」は `summary.md` の初めに書きます。

## X870 で毎晩回す

セビーリャさんの PC（X870）で、毎晩 2 時（日本時間）に回します。置き場所は作業用のフォルダ
（`C:\claude\経済版箱庭諸国`）の `runs\nightly\` で、ほかのスレッドが使う `runs\` の下の場所には触りません。

```
runs\nightly\
  src\           リポジトリの clone（毎晩 origin/main に合わせる）
  venv\          python の環境（pyproject.toml が変わったときだけ入れ直す）
  20261017\      その夜の結果（summary.md・summary.json・組ごとの JSON と記録・nightly.log）
  known.json     これまでに見つけたもの（初めて見た日）
  latest.txt     いちばん新しい夜の名前
```

入れ方（一度だけ。PowerShell で、作業用のフォルダから）:

```powershell
git clone https://github.com/Yamada-Kohei/economic-hakoniwa.git runs\nightly\src
powershell -NoProfile -ExecutionPolicy Bypass -File runs\nightly\src\tools\nightly\install-task.ps1
```

`install-task.ps1` はタスク スケジューラに `HakoniwaNightly`（毎日 2 時、優先度は「通常より低い」、8 時間で打ち切り）を
入れます。時刻は `-At "03:00"`、同時に回す数は `-Jobs 12` で変えられます。取り消すときは
`Unregister-ScheduledTask -TaskName HakoniwaNightly -Confirm:$false` です。
タスクは PC にサインインしている間だけ動きます（画面のロックはかまいません。サインアウトした夜やスリープの夜は
回りません）。
手で 1 回回すときは `powershell -NoProfile -ExecutionPolicy Bypass -File runs\nightly\src\tools\nightly\run-nightly.ps1` です。

決まり:

- X870 で重い処理は 1 度に 1 つです。始めるときにバランスの測り（`hakoniwa_core.measure` や `simulate`）が走っていれば、
  その夜は休みます（`nightly.log` に書きます）。
- 同時に回すのは 14 本までです（本物の頭 16 のうち 2 つは PC を使う人のため）。
- 結果は 30 日で消します。

## 朝の確かめ

朝 6 時 30 分にまとめが書かれ、7 時前に Claude が `latest.txt` の夜の `summary.md` を読み、「新」の付いたものが
あったときだけ、プロジェクトのスレッド「X870 の計算資源の生かし方」で知らせます。何もなければ知らせません。
「40 年より後に出た崩れ」は、不具合ではなく直すべき仕組みの抜けのこともあります。直すか残すかはバランス調整の回で決めます。
