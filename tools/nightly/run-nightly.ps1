# 夜の点検（docs/nightly.md）。タスク スケジューラから毎晩呼ばれます。
# main の最新を <置き場所>\src に出し、python -m hakoniwa_core.nightly run を回して、
# 結果を <置き場所>\<日付> に置きます。置き場所は、既定ではこのファイルの3つ上（runs\nightly）です。
# -Until の時刻（既定は朝 6 時 30 分）になったら残りの組を止め、そこまでの分でまとめを書きます
# （朝 7 時前の確かめに間に合わせるため）。
param(
    [int]$Jobs = 14,
    [string]$Root = "",
    [string]$Until = "06:30"
)

# 外の命令（git・python）の標準エラーで止まらないように。終わりの番号で確かめる
$ErrorActionPreference = "Continue"
if (-not $Root) { $Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path }
$Src = Join-Path $Root "src"
$Python = Join-Path $Root "venv\Scripts\python.exe"
$Date = Get-Date -Format "yyyyMMdd"
$Out = Join-Path $Root $Date
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Log = Join-Path $Out "nightly.log"
$env:PYTHONUTF8 = "1"

function Write-Log([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $Log -Value "$stamp $Message" -Encoding UTF8
}

function Finish([string]$Message, [int]$Code) {
    Write-Log $Message
    Set-Content -Path (Join-Path $Root "latest.txt") -Value $Date -Encoding ASCII
    exit $Code
}

Write-Log "始めます（jobs $Jobs、$Until まで）"

# ほかの測りが走っている夜は休む（X870 で重い処理は1度に1つ）
$busy = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match "hakoniwa_core\.(measure|simulate|nightly)" })
if ($busy.Count -gt 0) { Finish "ほかの測りが走っているので休みます（$($busy.Count) 個）" 0 }

git -C $Src fetch --quiet origin main
if ($LASTEXITCODE -ne 0) { Finish "git fetch に失敗しました" 1 }
git -C $Src checkout --quiet --detach origin/main
if ($LASTEXITCODE -ne 0) { Finish "git checkout に失敗しました" 1 }
$Commit = (git -C $Src rev-parse --short HEAD | Out-String).Trim()
Write-Log "main $Commit"

# 依存（pyproject.toml）が変わったときだけ入れ直す
if (-not (Test-Path $Python)) {
    py -3.12 -m venv (Join-Path $Root "venv")
    if ($LASTEXITCODE -ne 0) { Finish "venv を作れませんでした" 1 }
}
$Stamp = Join-Path $Root "pyproject.sha"
$Hash = (Get-FileHash (Join-Path $Src "pyproject.toml")).Hash
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne $Hash) {
    & $Python -m pip install --quiet -e $Src *> (Join-Path $Out "pip.log")
    if ($LASTEXITCODE -ne 0) { Finish "pip install に失敗しました（pip.log）" 1 }
    Set-Content -Path $Stamp -Value $Hash -Encoding ASCII
}

$Known = Join-Path $Root "known.json"
$Arguments = @(
    "-m", "hakoniwa_core.nightly", "run",
    "--out-dir", "`"$Out`"", "--jobs", $Jobs, "--seed-base", $Date,
    "--known", "`"$Known`"", "--label", "`"main $Commit`"",
    "--until", $Until
)
$run = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Src `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput (Join-Path $Out "run.out") -RedirectStandardError (Join-Path $Out "run.err")
if ($run.ExitCode -ne 0) { Finish "点検の道具が $($run.ExitCode) で終わりました（run.err）" 1 }

# 30 日より前の結果は消す
Get-ChildItem $Root -Directory |
    Where-Object { $_.Name -match "^\d{8}$" -and $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Recurse -Force
Finish "終わりました（summary.md）" 0
