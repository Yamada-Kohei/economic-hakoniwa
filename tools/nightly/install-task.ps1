# 夜の点検（docs/nightly.md）を、タスク スケジューラに毎晩の予定として入れます。一度だけ手で実行します。
#   powershell -NoProfile -ExecutionPolicy Bypass -File runs\nightly\src\tools\nightly\install-task.ps1
# 時刻を変えるときは -At "03:00" を付けて実行し直します。取り消すときは
#   Unregister-ScheduledTask -TaskName HakoniwaNightly -Confirm:$false
param(
    [string]$At = "02:00",
    [int]$Jobs = 14
)

$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "run-nightly.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Jobs $Jobs"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
# 優先度 7 は「通常より低い」。PC を使っている人の作業を先にする（子の python も同じ優先度になる）
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -MultipleInstances IgnoreNew -Priority 7 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "HakoniwaNightly" -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "economic-hakoniwa: nightly check (docs/nightly.md)" -Force | Out-Null
Get-ScheduledTask -TaskName "HakoniwaNightly" | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime
