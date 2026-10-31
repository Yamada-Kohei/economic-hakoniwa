# 後回しの台帳の経済の現象（W 番号）の測り直し。X870 で 1 回だけ回す（重い処理はこれ 1 つ、並列 14）。
# main b43db2a の full（14 組）を時系列つきで回し、wcheck.py で W ごとに数える。読むだけ。push しない。
$ErrorActionPreference = "Stop"
$rev = "b43db2a"
$branch = "claude/project-thread-i8d8am"
$root = "C:\claude\経済版箱庭諸国\runs\wcheck-20261101"
New-Item -ItemType Directory -Force $root | Out-Null
Set-Location "C:\claude\経済版箱庭諸国\runs\x870-src"
git fetch origin main $branch
$wt = "$root\wt-$rev"
if (-not (Test-Path $wt)) { git worktree add --detach $wt $rev }
git show "origin/${branch}:tools/wcheck/wcheck.py" | Out-File -Encoding utf8 "$root\wcheck.py"
Push-Location $wt
py -3.12 -m hakoniwa_core.measure batch --set full --jobs 14 --series --out-dir "$root\out" *> "$root\log.txt"
Pop-Location
py -3.12 -X utf8 "$root\wcheck.py" "$root\out" "$root\wcheck.txt"
"done" | Out-File "$root\done.txt"
