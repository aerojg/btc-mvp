<#
.SYNOPSIS
    run_daily.ps1을 Windows 작업 스케줄러에 등록한다. (관리자 권한 불필요, 몇 번 실행해도 안전)

.DESCRIPTION
    두 개의 트리거를 건다.
      1) 매일 06:00  - PC가 켜져 있으면 그 시각에 실행
      2) 로그온 시(3분 지연) - PC가 06:00에 꺼져 있었다면 켜는 시점에 실행

    "같은 날 두 번 도는 것" 은 run_daily.ps1 안의 날짜 스탬프(logs\.last_success)가 막는다.
    추가로 -StartWhenAvailable 을 켜 두어, 작업 스케줄러 자체도 놓친 일정을 따라잡는다.

    자격증명(git push) 때문에 '로그온한 사용자로만 실행'(Interactive)으로 등록한다.
    SYSTEM 계정으로 돌리면 Git Credential Manager에 접근하지 못해 push가 실패한다.

.PARAMETER At
    실행 시각. 기본 06:00.

.PARAMETER Unregister
    등록된 작업을 삭제한다.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\btc_mvp\setup_scheduled_task.ps1
    powershell -ExecutionPolicy Bypass -File C:\btc_mvp\setup_scheduled_task.ps1 -At 07:30
    powershell -ExecutionPolicy Bypass -File C:\btc_mvp\setup_scheduled_task.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$At = '06:00',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

$TaskName = 'BTC-MVP Daily Collect'
$TaskPath = '\'
$Root     = $PSScriptRoot
$Script   = Join-Path $Root 'run_daily.ps1'
$UserId   = "$env:USERDOMAIN\$env:USERNAME"

if ($Unregister) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction Stop
        Write-Host "삭제 완료: $TaskName"
    } catch {
        Write-Host "등록된 작업이 없습니다: $TaskName"
    }
    return
}

if (-not (Test-Path $Script)) { throw "run_daily.ps1을 찾을 수 없습니다: $Script" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script) `
    -WorkingDirectory $Root

$trigDaily = New-ScheduledTaskTrigger -Daily -At $At

# PC가 예약 시각에 꺼져 있었던 경우를 위한 보조 트리거.
# 부팅 직후에는 네트워크/자격증명이 아직 준비되지 않을 수 있어 3분 늦춘다.
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$trigLogon.Delay = 'PT3M'

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger @($trigDaily, $trigLogon) `
    -Settings $settings `
    -Principal $principal `
    -Description 'btc_mvp 온체인 데이터 일일 수집/계산/푸시. 매일 06:00, PC가 꺼져 있었으면 로그온 후 실행.' `
    -Force | Out-Null

Write-Host "등록 완료: $TaskName"
Write-Host "  스크립트 : $Script"
Write-Host "  실행 계정: $UserId (로그온 상태에서만 실행)"
Write-Host "  트리거   : 매일 $At + 로그온 3분 후 (같은 날 중복 실행은 스크립트가 차단)"
Write-Host ""
Write-Host "즉시 테스트: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "상태 확인  : Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "로그 확인  : Get-Content '$Root\logs\daily_$(Get-Date -Format yyyy-MM).log' -Tail 30"
